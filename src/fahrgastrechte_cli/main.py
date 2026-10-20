"""Kommandozeilen-Einstieg des Verspätungskontos.

Die CLI enthält nur Typer-Befehle, Prompts, Ausgabe und Exit-Code-Abbildung.
Sämtliche Logik (Speicherung, Connectoren, Regeln) liegt in
:mod:`fahrgastrechte_core`; ab MVP-Schritt 2 kommen dort Modelle, Storage und
``init``/``config`` hinzu.

Schritt 1: Skelett mit ``--version``, ``--json``-Flag und ``ping``-Befehl.
Schritt 2 (dieser Commit): ``init`` und ``config set/get/list``-Subbefehle.
"""

from __future__ import annotations

import json
import platform
import sqlite3
import sys
from pathlib import Path
from typing import Any, NoReturn

import typer
from rich.console import Console
from rich.table import Table

from fahrgastrechte_core import __version__, cases as fc_cases, config as fc_config, db
from fahrgastrechte_core import rules as fc_rules
from fahrgastrechte_core import stats as fc_stats
from fahrgastrechte_core.models import DataQuality, DataSource, TicketType
from fahrgastrechte_core.timetables import (
    RealtimeKind,
    TimetablesClient,
    TimetablesError,
)


app = typer.Typer(
    help=(
        "Verspätungskonto: DB-Verspätungsfälle erfassen, sammeln, prüfen, "
        "Anträge vorbereiten. Den Antrag schickt der Mensch selbst ab."
    ),
    no_args_is_help=True,
    add_completion=False,
)
config_app = typer.Typer(
    name="config",
    help=(
        "Stammdaten + Ticket-Konfiguration (ANFORDERUNGEN §4, F10). "
        "Schlüssel siehe `fahrgastrechte config list` oder ANFORDERUNGEN §4."
    ),
    no_args_is_help=True,
)
app.add_typer(config_app)

console = Console(stderr=True)

# ---------------------------------------------------------------- Optionen
def _json_callback(value: bool) -> bool:
    """Stellt sicher, dass ``--json`` nur als Flag (kein Wert) verwendet wird."""

    return value


JSON_OPTION = typer.Option(
    False,
    "--json",
    callback=_json_callback,
    is_flag=True,
    help="Maschinenlesbare JSON-Ausgabe statt Tabelle.",
)


# ---------------------------------------------------------------- Exit-Codes
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NOT_INITIALIZED = 2  # ANFORDERUNGEN §3
EXIT_INVALID_INPUT = 3   # ANFORDERUNGEN §3
EXIT_NETWORK = 4         # ANFORDERUNGEN §3
EXIT_PARSER = 5          # ANFORDERUNGEN §3


# ---------------------------------------------------------------- Hilfen
def _emit(
    payload: dict[str, Any],
    json_output: bool,
    *,
    title: str | None = None,
) -> None:
    """JSON auf stdout, hübsche Tabelle auf stderr.

    ``title``: Tabellen-Überschrift. Default = aus ``payload["title"]``
    (wird dann aus den Zeilen entfernt, damit es nicht doppelt erscheint).
    """

    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    body = dict(payload)
    if title is None and "title" in body:
        title = str(body.pop("title"))
    table = Table(title=title or "", show_header=True, header_style="bold")
    table.add_column("Feld", style="cyan")
    table.add_column("Wert", style="white")
    for key, value in body.items():
        if key == "__title__":
            continue
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False, sort_keys=True)
        table.add_row(str(key), str(value))
    console.print(table)


def _version_callback(value: bool) -> None:
    """Wertet ``--version`` aus und beendet das Programm."""

    if value:
        typer.echo(f"fahrgastrechte {__version__}")
        raise typer.Exit(code=EXIT_OK)


def _exit_db_not_ready(_exc: db.NotInitializedError) -> NoReturn:
    """Übersetzt eine ``NotInitializedError`` in Exit 2."""

    console.print(f"[red]DB nicht initialisiert:[/red] {_exc}")
    raise typer.Exit(code=EXIT_NOT_INITIALIZED)


def _open_db_or_exit() -> Path:
    """Wie :func:`db.ensure_initialized`, übersetzt den Fehler in Exit 2."""

    try:
        return db.ensure_initialized()
    except db.NotInitializedError as exc:
        _exit_db_not_ready(exc)
        # Hilft dem Type-Checker: dieser Pfad ist unerreichbar.
        raise RuntimeError("unreachable") from exc


# ---------------------------------------------------------------- Befehle
@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Version anzeigen und beenden.",
    ),
) -> None:
    """Globale Optionen (--version)."""


@app.command()
def ping(
    json_output: bool = JSON_OPTION,
) -> None:
    """Sanity-Check: Tool reagiert, Version + Python-Info ausgeben."""

    payload: dict[str, Any] = {
        "title": "fahrgastrechte ping",
        "status": "ok",
        "version": __version__,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }
    _emit(payload, json_output)


@app.command()
def init(
    json_output: bool = JSON_OPTION,
) -> None:
    """Initialisiert die lokale Datenbank (idempotent).

    Legt das konfigurierte Verzeichnis + ``konto.db`` (chmod 0700/0600)
    und das v1-Schema an. Mehrfacher Aufruf ist erlaubt: ist die DB
    bereits initialisiert, meldet das Tool das und liefert Exit 0.
    """

    target = db.init_db()
    schema = db.get_schema_version(target)
    payload: dict[str, Any] = {
        "title": "fahrgastrechte init",
        "status": "ok",
        "message": "DB initialisiert" if schema == db.SCHEMA_VERSION else "DB unbekannt",
        "path": str(target),
        "schema_version": schema,
    }
    _emit(payload, json_output)


# ----------------------------------------------------------- config set/get/list
@config_app.command("set")
def config_set(
    key: str = typer.Argument(..., help="Schlüssel laut ANFORDERUNGEN §4."),
    value: str = typer.Argument(..., help="Wert als String."),
    json_output: bool = JSON_OPTION,
) -> None:
    """Setzt einen Konfigurations-Wert in der DB (Exit 0/3)."""

    db_path = _open_db_or_exit()
    try:
        with db.connect(db_path) as conn:
            fc_config.set_config(conn, key, value)
        payload = {"key": key, "status": "ok", "value_set": True}
    except ValueError as exc:
        console.print(f"[red]Ungültige Eingabe:[/red] {exc}")
        raise typer.Exit(code=EXIT_INVALID_INPUT) from exc
    _emit(payload, json_output)


@config_app.command("get")
def config_get(
    key: str = typer.Argument(..., help="Schlüssel laut ANFORDERUNGEN §4."),
    json_output: bool = JSON_OPTION,
) -> None:
    """Liest einen Konfigurations-Wert aus der DB (Exit 0/2/3).

    Exit 0 auch, wenn der Key nicht gesetzt ist — das ist kein Fehler,
    sondern normaler Anfangszustand.
    """

    db_path = _open_db_or_exit()
    if not fc_config.is_known_key(key):
        console.print(
            f"[red]Ungültige Eingabe:[/red] unbekannter config-key {key!r}. "
            f"Erlaubt: {', '.join(fc_config.CONFIG_KEYS)}"
        )
        raise typer.Exit(code=EXIT_INVALID_INPUT)
    with db.connect(db_path) as conn:
        value = fc_config.get_config(conn, key)
    if value is None:
        console.print(f"[yellow]Key {key!r} ist nicht gesetzt.[/yellow]")
        raise typer.Exit(code=EXIT_OK)
    _emit({"key": key, "value": value}, json_output)


@config_app.command("list")
def config_list(
    json_output: bool = JSON_OPTION,
) -> None:
    """Listet alle gesetzten Konfigurations-Werte (Exit 0/2)."""

    db_path = _open_db_or_exit()
    with db.connect(db_path) as conn:
        values = fc_config.list_config(conn)
    if not values and not json_output:
        console.print(
            "[yellow]Keine Konfiguration gesetzt. Beispiel:[/yellow] "
            "`fahrgastrechte config set name 'Samuel Fleig'`"
        )
        raise typer.Exit(code=EXIT_OK)
    _emit(
        fc_config.to_dict_payload(values),
        json_output,
        title="fahrgastrechte config list",
    )


# ----------------------------------------------------------- add / list / show / trip
def _validate_date(value: str, flag: str) -> str:
    """Gibt ISO-Datum zurück oder wirft ``ValueError`` → Exit 3."""

    try:
        return fc_cases._normalise_date(value)
    except ValueError as exc:
        raise ValueError(f"{flag}: {exc}") from exc


def _build_timetables_client(db_path: Path) -> TimetablesClient:
    """Factory für den Timetables-Client.

    Wird beim ``trip``-Befehl benutzt. Liest DB-Timetables-Credentials aus
    der Konfiguration (``config set`` / Keyring, ANFORDERUNGEN §7); fehlende
    Werte fallen im Client auf Env-Variablen zurück. Indem wir die
    Konstruktion in eine eigene Funktion kapseln, können Tests per
    ``monkeypatch`` einen respx-gemockten Client einsetzen.
    """

    with db.connect(db_path) as conn:
        client_id = fc_config.get_config(conn, "db_client_id")
        api_key = fc_config.get_config(conn, "db_api_key")
    return TimetablesClient(
        client_id=client_id,
        api_key=api_key,
        rate_per_minute=30,  # ANFORDERUNGEN §9 N2: selbstauferlegt
    )


def _resolve_ticket_type(value: str | None) -> str:
    """Liest Ticket-Typ aus CLI-Argument oder DB-Config."""

    candidate = value or fc_config.DEFAULT_TICKET_TYPE
    try:
        return TicketType(candidate).value
    except ValueError:
        raise ValueError(
            f"Unbekannter ticket_type {candidate!r}; "
            f"erlaubt: {', '.join(t.value for t in TicketType)}"
        )


def _ticket_price_cents(conn: sqlite3.Connection) -> int:
    """Liest den konfigurierten Ticket-Preis (Default 4900 Cent)."""

    raw = fc_config.get_config(conn, "ticket_price_cents")
    if raw is None or raw == "":
        return int(fc_config.DEFAULT_TICKET_PRICE_CENTS)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return int(fc_config.DEFAULT_TICKET_PRICE_CENTS)


def _apply_rule_if_empty(
    conn: sqlite3.Connection,
    *,
    ticket_type: str,
    delay_min: int | None,
    ticket_class: int | None,
    explicit_expected_cents: int,
) -> int:
    """Berechnet ``expected_cents`` per Regel, wenn nicht explizit gesetzt.

    ``explicit_expected_cents != 0`` → vom Menschen vorgegeben, wird
    unverändert übernommen (Test-Kompatibilität und Override-Möglichkeit).
    Sonst: ``compute_compensation`` mit ``ticket_price_cents`` aus der
    Config (oder Default). Bei unbekanntem Ticket-Typ / Regel-Fehler
    fällt der Betrag auf ``0`` zurück — keine erfundenen Cent-Werte.
    """

    if explicit_expected_cents != 0:
        return int(explicit_expected_cents)
    if delay_min is None:
        return 0
    try:
        delay_int = int(delay_min)
    except (TypeError, ValueError):
        return 0
    if delay_int < 60:
        return 0
    klass = int(ticket_class) if ticket_class in (1, 2) else 2
    price = _ticket_price_cents(conn)
    try:
        result = fc_rules.compute_compensation(
            ticket_type,
            delay_int,
            ticket_price_cents=price,
            travel_class=klass,
        )
        return int(result.cents)
    except ValueError:
        return 0


def _cmd_add(
    *,
    service_date: str,
    train: str,
    start_eva: int,
    dest_eva: int,
    reason: str,
    ticket_type: str | None,
    expected_cents: int,
    planned_dep: str | None,
    actual_dep: str | None,
    planned_arr: str | None,
    actual_arr: str | None,
    delay_min: int | None,
    line: str | None,
    evu: str | None,
    category: str | None,
    ticket_class: int | None,
    evidence: str | None,
    cap_applied: int | None,
    notes: str | None,
    force: bool,
    json_output: bool,
) -> None:
    """F1: Verspätungs-Fall manuell erfassen.

    Pflichtfelder laut §3 F1 + Begründungspflicht (Schema). Duplikate
    werden via SHA-256 über ``(Datum, Zugnummer, Start-/Ziel-EVA,
    planned_dep)`` erkannt (UNIQUE auf ``cases.dup_hash``).
    """

    db_path = _open_db_or_exit()
    try:
        ticket_value = _resolve_ticket_type(ticket_type)
    except ValueError as ve:
        console.print(f"[red]Ungültige Eingabe:[/red] {ve}")
        raise typer.Exit(code=EXIT_INVALID_INPUT) from ve

    # Schritt 5: expected_cents per Regel berechnen, wenn nicht explizit
    # gesetzt (Test-Kompatibilität: explizite Werte bleiben unangetastet).
    with db.connect(db_path) as conn:
        expected_cents = _apply_rule_if_empty(
            conn,
            ticket_type=ticket_value,
            delay_min=delay_min,
            ticket_class=ticket_class,
            explicit_expected_cents=expected_cents,
        )

    payload = fc_cases.AddCaseInput(
        service_date=service_date,
        train_number=train,
        start_eva=start_eva,
        dest_eva=dest_eva,
        ticket_type=ticket_value,
        reason_text=reason,
        expected_cents=expected_cents,
        planned_dep=planned_dep,
        actual_dep=actual_dep,
        planned_arr=planned_arr,
        actual_arr=actual_arr,
        delay_min_arr=delay_min,
        line=line,
        evu=evu,
        category=category,
        ticket_class=ticket_class,
        evidence_path=evidence,
        cap_applied=cap_applied,
        notes=notes,
        data_source=DataSource.MANUAL,
        data_quality=DataQuality.MANUAL_ONLY,
        force=force,
    )
    try:
        with db.connect(db_path) as conn:
            result = fc_cases.add_case(conn, payload)
    except fc_cases.DuplicateCaseError as exc:
        console.print(
            f"[red]Duplikat:[/red] Fall #{exc.original_id} hat bereits "
            f"denselben Hash ({exc.dup_hash[:12]}…). `add --force` zum "
            "Überschreiben."
        )
        raise typer.Exit(code=EXIT_ERROR) from exc
    except ValueError as exc:
        console.print(f"[red]Ungültige Eingabe:[/red] {exc}")
        raise typer.Exit(code=EXIT_INVALID_INPUT) from exc

    out = {
        "case_id": result.case_id,
        "trip_id": result.trip_id,
        "dup_hash": result.dup_hash,
        "created_at": result.created_at,
        "ticket_type": payload.ticket_type,
        "expected_cents": payload.expected_cents,
    }
    _emit(out, json_output, title=f"Fall #{result.case_id} angelegt")


def _cmd_list(
    *,
    status: str | None,
    since: str | None,
    until: str | None,
    line: str | None,
    json_output: bool,
) -> None:
    """F3: Fälle auflisten, mit Filter und Summenzeile."""

    db_path = _open_db_or_exit()
    try:
        with db.connect(db_path) as conn:
            cases_list = fc_cases.list_cases(
                conn,
                status=status,
                since=since,
                until=until,
                line=line,
            )
            totals = fc_cases.sum_cases(cases_list)
    except ValueError as exc:
        console.print(f"[red]Ungültige Eingabe:[/red] {exc}")
        raise typer.Exit(code=EXIT_INVALID_INPUT) from exc

    if json_output:
        payload = {
            "cases": cases_list,
            "summary": {
                "count": totals["count"],
                "total_cents": totals["total_cents"],
            },
        }
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return

    if not cases_list:
        console.print(
            "[yellow]Keine Fälle gefunden.[/yellow] "
            "Filter lockern oder `fahrgastrechte add …` aufrufen."
        )
        raise typer.Exit(code=EXIT_OK)

    table = Table(title="fahrgastrechte list", show_header=True, header_style="bold")
    for col in ("ID", "Datum", "Zug", "Linie", "Delay", "Status", "Betrag (€)"):
        table.add_column(col)
    for c in cases_list:
        delay = c.get("delay_min_arr")
        delay_str = "—" if delay is None else f"{int(delay)} min"
        eur = int(c["expected_cents"]) / 100.0
        table.add_row(
            str(c["id"]),
            str(c["service_date"]),
            str(c["train_number"]),
            str(c["line"] or "—"),
            delay_str,
            str(c["current_status"]),
            f"{eur:.2f}",
        )
    # Summenzeile (F3).
    eur_total = totals["total_eur"]
    table.add_row(
        "Σ",
        "—",
        "—",
        "—",
        "—",
        f"{totals['count']} Fälle",
        f"{eur_total:.2f}",
        end_section=True,
    )
    console.print(table)


def _cmd_show(*, case_id: int, json_output: bool) -> None:
    """F4: einzelnen Fall anzeigen."""

    db_path = _open_db_or_exit()
    try:
        with db.connect(db_path) as conn:
            payload = fc_cases.show_case(conn, case_id)
    except fc_cases.CaseNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=EXIT_ERROR) from exc
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return

    table = Table(
        title=f"Fahrgastrechte-Fall #{case_id}",
        show_header=True,
        header_style="bold",
    )
    table.add_column("Feld", style="cyan")
    table.add_column("Wert", style="white")
    for key, value in payload.items():
        if key == "status_history":
            value = json.dumps(value, ensure_ascii=False, sort_keys=True)
        elif isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False, sort_keys=True)
        elif value is None:
            value = "—"
        table.add_row(str(key), str(value))
    console.print(table)


def _cmd_trip(
    *,
    date: str,
    train: str,
    start_eva: int,
    dest_eva: int,
    reason: str | None,
    ticket_type: str | None,
    ticket_class: int | None,
    notes: str | None,
    force: bool,
    json_output: bool,
) -> None:
    """F2: Trip aus dem DB-Timetables-Connector rekonstruieren und als Fall speichern.

    API-Fehler → ``data_quality=best_effort`` und ``delay_min_arr=NULL``
    (N3, keine erfundenen Verspätungen). Der Fall wird in jedem Fall
    angelegt — auch ohne Echtzeit-Daten — damit der Mensch ihn später
    manuell ergänzen kann.
    """

    db_path = _open_db_or_exit()
    try:
        iso_date = _validate_date(date, "--date")
        ticket_value = _resolve_ticket_type(ticket_type)
    except ValueError as ve:
        console.print(f"[red]Ungültige Eingabe:[/red] {ve}")
        raise typer.Exit(code=EXIT_INVALID_INPUT) from ve

    # 1) Connector-Aufruf (gemockt in Tests).
    delay_min_arr: int | None = None
    planned_arr: str | None = None
    actual_arr: str | None = None
    planned_dep: str | None = None
    actual_dep: str | None = None
    line: str | None = None
    evu: str | None = None
    category: str | None = None
    trip_ref: str | None = None
    data_source = DataSource.MANUAL
    data_quality = DataQuality.MANUAL_ONLY
    error_message: str | None = None

    try:
        client = _build_timetables_client(db_path)
        with client:
            rt = client.get_realtime_changes(dest_eva, kind=RealtimeKind.RCHG)
            stop = _match_train(rt, train)
            if stop is not None and stop.arrival is not None:
                arr = stop.arrival
                if arr.planned_time is not None:
                    planned_arr = arr.planned_time.astimezone(fc_cases.BERLIN).isoformat(
                        timespec="minutes"
                    )
                if arr.actual_time is not None:
                    actual_arr = arr.actual_time.astimezone(fc_cases.BERLIN).isoformat(
                        timespec="minutes"
                    )
                delay_min_arr = arr.delay_min
            if stop is not None and stop.departure is not None:
                dep = stop.departure
                if dep.planned_time is not None:
                    planned_dep = dep.planned_time.astimezone(fc_cases.BERLIN).isoformat(
                        timespec="minutes"
                    )
                if dep.actual_time is not None:
                    actual_dep = dep.actual_time.astimezone(fc_cases.BERLIN).isoformat(
                        timespec="minutes"
                    )
            if stop is not None and stop.trip_label is not None:
                tl = stop.trip_label
                if tl.number:
                    train = tl.number or train
                if tl.category:
                    category = tl.category
                if tl.owner:
                    evu = tl.owner
                trip_ref = stop.id
            data_source = DataSource.TIMETABLES_API
            if delay_min_arr is None or planned_arr is None or actual_arr is None:
                data_quality = DataQuality.BEST_EFFORT
            else:
                data_quality = DataQuality.VERIFIED
            line = line  # bleibt None — IRIS liefert kein Linien-Feld im /rchg
    except TimetablesError as exc:
        error_message = f"{type(exc).__name__}: {exc}"
        data_quality = DataQuality.BEST_EFFORT
    except Exception as exc:  # pragma: no cover - defensive
        error_message = f"Unerwarteter Fehler: {exc}"
        data_quality = DataQuality.BEST_EFFORT

    # 1.x) Schritt 5: expected_cents per Regel berechnen, wenn die
    # Echtzeit-Daten eine Verspätung liefern. Bei ``best_effort`` (keine
    # Verspätung bekannt) bleibt der Betrag 0.
    with db.connect(db_path) as conn:
        expected_cents = _apply_rule_if_empty(
            conn,
            ticket_type=ticket_value,
            delay_min=delay_min_arr,
            ticket_class=ticket_class,
            explicit_expected_cents=0,
        )

    # 2) Fall anlegen (mit Trip-Rekonstruktion).
    payload = fc_cases.AddCaseInput(
        service_date=iso_date,
        train_number=train,
        start_eva=start_eva,
        dest_eva=dest_eva,
        ticket_type=ticket_value,
        reason_text=reason or "Rekonstruktion aus DB-Timetables-API",
        expected_cents=expected_cents,
        planned_dep=planned_dep,
        actual_dep=actual_dep,
        planned_arr=planned_arr,
        actual_arr=actual_arr,
        delay_min_arr=delay_min_arr,
        line=line,
        evu=evu,
        category=category,
        ticket_class=ticket_class,
        notes=notes,
        data_source=data_source,
        data_quality=data_quality,
        trip_ref=trip_ref,
        reconstructed_at=(
            fc_cases._now_iso() if data_source != DataSource.MANUAL else None
        ),
        force=force,
    )
    try:
        with db.connect(db_path) as conn:
            result = fc_cases.add_case(conn, payload)
    except fc_cases.DuplicateCaseError as exc:
        console.print(
            f"[red]Duplikat:[/red] Fall #{exc.original_id} hat bereits "
            f"denselben Hash ({exc.dup_hash[:12]}…). `trip … --force` zum "
            "Überschreiben."
        )
        raise typer.Exit(code=EXIT_ERROR) from exc
    except ValueError as exc:
        console.print(f"[red]Ungültige Eingabe:[/red] {exc}")
        raise typer.Exit(code=EXIT_INVALID_INPUT) from exc

    out: dict[str, Any] = {
        "case_id": result.case_id,
        "trip_id": result.trip_id,
        "dup_hash": result.dup_hash,
        "delay_min_arr": delay_min_arr,
        "planned_arr": planned_arr,
        "actual_arr": actual_arr,
        "data_source": data_source.value,
        "data_quality": data_quality.value,
    }
    if error_message is not None:
        out["warning"] = error_message

    if json_output:
        typer.echo(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True))
        return

    title = (
        f"Rekonstruierter Fall #{result.case_id}"
        if data_quality is DataQuality.VERIFIED
        else f"Rekonstruktions-Versuch #{result.case_id} (best_effort)"
    )
    table = Table(title=title, show_header=True, header_style="bold")
    table.add_column("Feld", style="cyan")
    table.add_column("Wert", style="white")
    for key, value in out.items():
        table.add_row(str(key), "—" if value is None else str(value))
    if error_message is not None:
        console.print(f"[yellow]Warnung:[/yellow] {error_message}")
    console.print(table)


def _match_train(rt, train_number: str):  # type: ignore[no-untyped-def]
    """Findet den ersten Stop aus ``rt``, dessen Zugnummer mit ``train_number`` matcht."""

    wanted = (train_number or "").strip()
    if not wanted:
        return rt.stops[0] if rt.stops else None
    for stop in rt.stops:
        tl = stop.trip_label
        if tl is not None and tl.number and tl.number.strip() == wanted:
            return stop
    # Fallback: nichts gefunden → None; Caller behandelt das als best_effort.
    return None


@app.command()
def add(
    service_date: str = typer.Option(
        ...,
        "--service-date",
        "--datum",
        help="Service-Datum der Fahrt (YYYY-MM-DD).",
    ),
    train: str = typer.Option(
        ..., "--train", "--zug", help="Zugnummer, z. B. 12345."
    ),
    start_eva: int = typer.Option(
        ...,
        "--start-eva",
        help="7-stellige EVA-Nummer des Start-Bahnhofs.",
    ),
    dest_eva: int = typer.Option(
        ...,
        "--dest-eva",
        help="7-stellige EVA-Nummer des Ziel-Bahnhofs.",
    ),
    reason: str = typer.Option(
        ...,
        "--reason",
        "--begruendung",
        help="Freitext-Begründung (Pflicht laut F1).",
    ),
    ticket_type: str = typer.Option(
        None,
        "--ticket-type",
        help="Ticket-Typ (Default: aus config oder d_ticket).",
    ),
    expected_cents: int = typer.Option(
        0,
        "--expected-cents",
        help="Erwartete Entschädigung in Cent (Schritt 5 überschreibt das).",
    ),
    planned_dep: str | None = typer.Option(
        None, "--planned-dep", help="Soll-Abfahrtszeit (ISO)."
    ),
    actual_dep: str | None = typer.Option(
        None, "--actual-dep", help="Ist-Abfahrtszeit (ISO)."
    ),
    planned_arr: str | None = typer.Option(
        None, "--planned-arr", help="Soll-Ankunftszeit (ISO)."
    ),
    actual_arr: str | None = typer.Option(
        None, "--actual-arr", help="Ist-Ankunftszeit (ISO)."
    ),
    delay_min: int | None = typer.Option(
        None, "--delay-min", help="Verspätung am Ziel in Minuten (überschreibt plan-recurs)."
    ),
    line: str | None = typer.Option(
        None, "--line", "--linie", help="Linie, z. B. 'RE 5'."
    ),
    evu: str | None = typer.Option(
        None, "--evu", help="Eisenbahn-Verkehrsunternehmen."
    ),
    category: str | None = typer.Option(
        None, "--category", help="Produktkategorie (RE/RB/S/IC/ICE)."
    ),
    ticket_class: int | None = typer.Option(
        None, "--ticket-class", help="1 oder 2 (Bahn-Klasse)."
    ),
    evidence: str | None = typer.Option(
        None, "--evidence", help="Pfad zu einem Beleg (Ticketfoto)."
    ),
    cap_applied: int | None = typer.Option(
        None, "--cap-applied", help="25-%-Deckel in Cent (0 wenn irrelevant)."
    ),
    notes: str | None = typer.Option(
        None, "--notes", help="Interne Notizen zum Fall."
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Duplikat trotzdem anlegen (Exit 1 wird überschrieben).",
    ),
    json_output: bool = JSON_OPTION,
) -> None:
    """F1: Verspätungs-Fall manuell erfassen."""

    _cmd_add(
        service_date=service_date,
        train=train,
        start_eva=start_eva,
        dest_eva=dest_eva,
        reason=reason,
        ticket_type=ticket_type,
        expected_cents=expected_cents,
        planned_dep=planned_dep,
        actual_dep=actual_dep,
        planned_arr=planned_arr,
        actual_arr=actual_arr,
        delay_min=delay_min,
        line=line,
        evu=evu,
        category=category,
        ticket_class=ticket_class,
        evidence=evidence,
        cap_applied=cap_applied,
        notes=notes,
        force=force,
        json_output=json_output,
    )


@app.command(name="list")
def list_(  # noqa: A001 -- CLI-Konvention kollidiert mit builtin
    status: str | None = typer.Option(
        None, "--status", help="Status-Filter (offen, eingereicht, …)."
    ),
    since: str | None = typer.Option(
        None, "--since", help="Nur Fälle ab diesem Datum (YYYY-MM-DD)."
    ),
    until: str | None = typer.Option(
        None, "--until", help="Nur Fälle bis zu diesem Datum (YYYY-MM-DD)."
    ),
    line: str | None = typer.Option(
        None, "--line", help="Nur Fälle mit dieser Linie."
    ),
    json_output: bool = JSON_OPTION,
) -> None:
    """F3: Eigene Fälle listen, mit Filter und Summenzeile."""

    _cmd_list(
        status=status,
        since=since,
        until=until,
        line=line,
        json_output=json_output,
    )


@app.command()
def show(
    case_id: int = typer.Argument(..., help="ID des Falls."),
    json_output: bool = JSON_OPTION,
) -> None:
    """F4: einzelnen Fall anzeigen."""

    _cmd_show(case_id=case_id, json_output=json_output)


@app.command()
def trip(
    date: str = typer.Option(
        ...,
        "--date",
        "--datum",
        help="Service-Datum (YYYY-MM-DD).",
    ),
    train: str = typer.Option(
        ..., "--train", "--zug", help="Zugnummer, z. B. 12345."
    ),
    start_eva: int = typer.Option(
        ...,
        "--start-eva",
        help="7-stellige EVA-Nummer des Start-Bahnhofs.",
    ),
    dest_eva: int = typer.Option(
        ...,
        "--dest-eva",
        help="7-stellige EVA-Nummer des Ziel-Bahnhofs.",
    ),
    reason: str | None = typer.Option(
        None,
        "--reason",
        "--begruendung",
        help="Optionaler Freitext (Default: 'Rekonstruktion aus DB-Timetables-API').",
    ),
    ticket_type: str | None = typer.Option(
        None, "--ticket-type", help="Ticket-Typ (Default: d_ticket)."
    ),
    ticket_class: int | None = typer.Option(
        None, "--ticket-class", help="1 oder 2 (Bahn-Klasse)."
    ),
    notes: str | None = typer.Option(
        None, "--notes", help="Interne Notizen."
    ),
    force: bool = typer.Option(
        False, "--force", help="Duplikat überschreiben."
    ),
    json_output: bool = JSON_OPTION,
) -> None:
    """F2: Trip rekonstruieren und als Fall anlegen.

    Holt Soll/Ist über den DB-Timetables-Connector (``/rchg``); mappt
    in einen Fall. API-Fehler → ``data_quality=best_effort`` und
    ``delay_min_arr=NULL`` (ANFORDERUNGEN §9 N3).
    """

    _cmd_trip(
        date=date,
        train=train,
        start_eva=start_eva,
        dest_eva=dest_eva,
        reason=reason,
        ticket_type=ticket_type,
        ticket_class=ticket_class,
        notes=notes,
        force=force,
        json_output=json_output,
    )


def _cmd_calc(*, case_id: int, json_output: bool) -> None:
    """F5: Entschädigung für einen Fall nach §8 berechnen."""

    db_path = _open_db_or_exit()
    try:
        with db.connect(db_path) as conn:
            result = fc_cases.calc_compensation_for_case(conn, case_id)
    except fc_cases.CaseNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=EXIT_ERROR) from exc

    payload: dict[str, Any] = {"case_id": case_id, **result.to_dict()}
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return

    table = Table(
        title=f"Entschädigung Fall #{case_id}",
        show_header=True,
        header_style="bold",
    )
    table.add_column("Feld", style="cyan")
    table.add_column("Wert", style="white")
    for key, value in payload.items():
        table.add_row(str(key), str(value))
    console.print(table)


def _cmd_stats(*, month: str | None, json_output: bool) -> None:
    """F11: Monats-Statistik (Fälle, Summe, Bagatellgrenze, Deckel)."""

    db_path = _open_db_or_exit()
    try:
        with db.connect(db_path) as conn:
            payload = fc_stats.compute_monthly_stats(conn, month=month)
    except ValueError as exc:
        console.print(f"[red]Ungültige Eingabe:[/red] {exc}")
        raise typer.Exit(code=EXIT_INVALID_INPUT) from exc

    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return

    if month is not None:
        title = f"Statistik {payload.get('month', month)}"
        rows = {k: v for k, v in payload.items() if k != "month"}
        table = Table(title=title, show_header=True, header_style="bold")
        table.add_column("Kennzahl", style="cyan")
        table.add_column("Wert", style="white")
        for key, value in rows.items():
            table.add_row(str(key), str(value))
        console.print(table)
        return

    table = Table(title="fahrgastrechte stats", show_header=True, header_style="bold")
    table.add_column("Monat", style="cyan")
    table.add_column("Fälle", justify="right")
    table.add_column("Erwartet (Cent)", justify="right")
    table.add_column("Unter 4 €", justify="center")
    table.add_column("Deckel (Cent)", justify="right")
    for entry in payload.get("months", []):
        table.add_row(
            str(entry["month"]),
            str(entry["case_count"]),
            str(entry["expected_cents"]),
            "ja" if entry["below_threshold"] else "nein",
            str(entry["cap"]),
        )
    totals = payload.get("totals", {})
    table.add_row(
        "Σ",
        str(totals.get("case_count", 0)),
        str(totals.get("expected_cents", 0)),
        "ja" if totals.get("below_threshold") else "nein",
        str(totals.get("cap", 0)),
        end_section=True,
    )
    console.print(table)


@app.command()
def calc(
    case_id: int = typer.Argument(..., help="ID des Falls."),
    json_output: bool = JSON_OPTION,
) -> None:
    """F5: Entschädigung für einen Fall berechnen (Regeln aus bahn.de §8)."""

    _cmd_calc(case_id=case_id, json_output=json_output)


@app.command()
def stats(
    month: str | None = typer.Option(
        None,
        "--month",
        help="Kalendermonat (YYYY-MM oder YYYYMM).",
    ),
    json_output: bool = JSON_OPTION,
) -> None:
    """F11: Statistik pro Monat (Fälle, Summe, Bagatellgrenze, Deckel)."""

    _cmd_stats(month=month, json_output=json_output)


def main() -> None:
    """Entry-Point für ``fahrgastrechte``-Konsolen-Skript."""

    app()


if __name__ == "__main__":  # pragma: no cover
    main()
