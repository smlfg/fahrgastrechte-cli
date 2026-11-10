"""Kommandozeilen-Einstieg des Verspätungskontos.

Die CLI enthält nur Typer-Befehle, Prompts, Ausgabe und Exit-Code-Abbildung.
Sämtliche Logik (Speicherung, Connectoren, Regeln) liegt in
:mod:`fahrgastrechte_core`.

R3 ergänzt die menschenbedienbare Oberfläche: positionale Namen,
``login``/``logout``/``whoami``, ``station``, Bestätigung mit ``[j/N]``,
``--confirm`` für Agenten, automatisches JSON bei Pipe-Ausgabe und einen
zentralen Fehler-/Signalpfad ohne Tracebacks.
"""

from __future__ import annotations

import getpass
import json
import os
import platform
import sqlite3
import sys
from datetime import date as _date
from pathlib import Path
from typing import Any, NoReturn, Sequence

import typer
from rich.console import Console
from rich.table import Table

from fahrgastrechte_core import __version__, cases as fc_cases, config as fc_config, db
from fahrgastrechte_core import export as fc_export
from fahrgastrechte_core import rules as fc_rules
from fahrgastrechte_core import session as fc_session
from fahrgastrechte_core import stats as fc_stats
from fahrgastrechte_core.models import CaseStatusName, DataQuality, DataSource, TicketType
from fahrgastrechte_core.timetables import (
    AuthenticationError,
    ParserError,
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
    rich_markup_mode=None,  # plain Click-Hilfe (lesbar + maschinell auswertbar)
)
config_app = typer.Typer(
    name="config",
    help=(
        "Stammdaten + Ticket-Konfiguration (ANFORDERUNGEN §4, F10). "
        "Schlüssel siehe `fahrgastrechte config list` oder ANFORDERUNGEN §4."
    ),
    no_args_is_help=True,
    rich_markup_mode=None,
)
app.add_typer(config_app)

console = Console(stderr=True)
out = Console()


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
CONFIRM_OPTION = typer.Option(
    False,
    "--confirm",
    "--yes",
    is_flag=True,
    help="Bestätigung überspringen (nur für Agenten/nicht-interaktive Läufe).",
)


# ---------------------------------------------------------------- Exit-Codes
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NOT_INITIALIZED = 2  # ANFORDERUNGEN §3
EXIT_INVALID_INPUT = 3   # ANFORDERUNGEN §3
EXIT_NETWORK = 4         # ANFORDERUNGEN §3
EXIT_PARSER = 5          # ANFORDERUNGEN §3


# ---------------------------------------------------------------- Ausgabe
def _wants_json(explicit: bool) -> bool:
    """``--json`` oder Pipe/Agent; Test-/Text-Override via Env."""

    if explicit:
        return True
    override = os.environ.get("FAHRGASTRECHTE_OUTPUT", "").strip().lower()
    if override in {"text", "human", "table"}:
        return False
    if override in {"json", "machine"}:
        return True
    try:
        return not sys.stdout.isatty()
    except (AttributeError, ValueError, OSError):
        return False


def _emit(
    payload: dict[str, Any],
    json_output: bool,
    *,
    title: str | None = None,
) -> None:
    """JSON auf stdout, hübsche Tabelle auf stdout (Menschen)."""

    if _wants_json(json_output):
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
    out.print(table)


def _emit_json(payload: Any) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _fail(
    message: str,
    code: int,
    *,
    json_output: bool = False,
    hint: str | None = None,
) -> NoReturn:
    """Fehler ohne Traceback; ``--json`` liefert genau ein JSON-Objekt."""

    if _wants_json(json_output):
        _emit_json({"error": message, "hint": hint, "exit_code": code})
    else:
        console.print(f"[red]Fehler:[/red] {message}")
        if hint:
            console.print(f"[yellow]Nächster Schritt:[/yellow] {hint}")
    raise typer.Exit(code=code)


def _version_callback(value: bool) -> None:
    """Wertet ``--version`` aus und beendet das Programm."""

    if value:
        typer.echo(f"fahrgastrechte {__version__}")
        raise typer.Exit(code=EXIT_OK)


# ---------------------------------------------------------------- Interaktion
def _is_interactive() -> bool:
    """TTY-Prüfung: ohne Terminal wird vor Requests abgebrochen."""

    try:
        return bool(sys.stdin.isatty() and sys.stderr.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def _ask_yes_no(action: str) -> bool:
    """Stellt ``<Aktion> verbindlich? [j/N]`` auf stderr; Default Nein."""

    if not _is_interactive():
        return False
    try:
        sys.stderr.write(f"{action} verbindlich? [j/N] ")
        sys.stderr.flush()
        answer = sys.stdin.readline()
    except (EOFError, KeyboardInterrupt):
        sys.stderr.write("\n")
        return False
    if answer == "":
        sys.stderr.write("\n")
        return False
    return answer.strip().lower() in {"j", "ja", "y", "yes"}


def _ask_line(prompt: str) -> str:
    """Liest eine Zeile; EOF/Strg+C werden als leere Eingabe behandelt."""

    try:
        sys.stderr.write(prompt)
        sys.stderr.flush()
        line = sys.stdin.readline()
    except (EOFError, KeyboardInterrupt):
        return ""
    if line == "":
        return ""
    return line.rstrip("\n")


def _require_confirmation_path(action: str, confirm: bool, json_output: bool) -> None:
    """Bricht ohne TTY und ohne ``--confirm`` vor jedem Request ab."""

    if confirm or _is_interactive():
        return
    _fail(
        f"{action} braucht ein Terminal oder --confirm.",
        EXIT_INVALID_INPUT,
        json_output=json_output,
        hint="Im Terminal interaktiv bestätigen oder --confirm/--yes setzen.",
    )


def _select_index(count: int, label: str) -> int | None:
    """Nummerierte Auswahl im TTY; ``None`` bei Abbruch/EOF."""

    choice = _ask_line(f"{label} wählen [1-{count}]: ")
    if not choice.strip():
        return None
    try:
        index = int(choice.strip())
    except ValueError:
        return None
    if 1 <= index <= count:
        return index - 1
    return None


# ---------------------------------------------------------------- Hilfen
def _today_iso() -> str:
    """Heutiges Datum; ``FAHRGASTRECHTE_HEUTE`` nur für deterministische Tests."""

    override = os.environ.get("FAHRGASTRECHTE_HEUTE")
    if override:
        try:
            return fc_cases._normalise_date(override)
        except ValueError:
            pass
    return _date.today().isoformat()


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
        raise RuntimeError("unreachable") from exc


def _validate_date(value: str, flag: str) -> str:
    """Gibt ISO-Datum zurück oder wirft ``ValueError`` → Exit 3."""

    try:
        return fc_cases._normalise_date(value)
    except ValueError as exc:
        raise ValueError(f"{flag}: {exc}") from exc


def _build_timetables_client() -> TimetablesClient:
    """Factory für den Timetables-Client (Env/Session-Credentials)."""

    credentials = fc_session.resolve_credentials()
    return TimetablesClient(
        client_id=credentials.client_id,
        api_key=credentials.api_key,
        rate_per_minute=30,  # ANFORDERUNGEN §9 N2: selbstauferlegt
    )


def _map_timetables_error(
    exc: TimetablesError, *, json_output: bool = False
) -> NoReturn:
    """Übersetzt Connector-Fehler in Exit 3/4/5 mit nächstem Schritt."""

    if isinstance(exc, AuthenticationError):
        _fail(
            "DB-Zugangsdaten fehlen oder wurden abgelehnt.",
            EXIT_INVALID_INPUT,
            json_output=json_output,
            hint="`fahrgastrechte login` ausführen oder die Credential-Env setzen.",
        )
    if isinstance(exc, ParserError):
        _fail(
            f"Antwort der DB-Timetables-API war nicht lesbar: {exc}",
            EXIT_PARSER,
            json_output=json_output,
            hint="Später erneut versuchen oder den Fall mit `fahrgastrechte add` manuell anlegen.",
        )
    _fail(
        f"Datenquelle nicht erreichbar: {exc}",
        EXIT_NETWORK,
        json_output=json_output,
        hint="Internetverbindung prüfen und danach erneut versuchen.",
    )


def _resolve_ticket_type(value: str | None, conn: sqlite3.Connection | None = None) -> str:
    """Liest Ticket-Typ aus Flag, dann DB-Config, dann Default."""

    candidate = value
    if candidate is None and conn is not None:
        candidate = fc_config.get_config(conn, "ticket_type")
    candidate = candidate or fc_config.DEFAULT_TICKET_TYPE
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
    """Berechnet ``expected_cents`` per Regel, wenn nicht explizit gesetzt."""

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


def _format_delay(delay: int | None) -> str:
    if delay is None:
        return "keine Daten"
    if delay >= 0:
        return f"+{delay} min"
    return f"{delay} min"


def _format_hhmm(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return value.strftime("%H:%M")
    except AttributeError:
        return str(value)


def _normalise_train(value: str) -> str:
    return " ".join((value or "").upper().split())


def _train_matches(label: Any, query: str) -> bool:
    """Vergleicht ``RE 5``/``ICE 123``/``123`` mit einem IRIS-TripLabel."""

    wanted = _normalise_train(query)
    if not wanted:
        return True
    if label is None:
        return False
    number = _normalise_train(getattr(label, "number", "") or "")
    category = _normalise_train(getattr(label, "category", "") or "")
    variants = {number, f"{category} {number}".strip(), f"{category}{number}".strip()}
    if wanted in variants:
        return True
    compact_wanted = wanted.replace(" ", "")
    return compact_wanted in {variant.replace(" ", "") for variant in variants if variant}


def _match_stop(rt: Any, train: str) -> Any:
    wanted = _normalise_train(train)
    if not wanted:
        return rt.stops[0] if rt.stops else None
    for stop in rt.stops:
        if _train_matches(stop.trip_label, train):
            return stop
    return None


def _station_line(name: str, eva: int, ds100: str | None = None) -> str:
    suffix = f" · {ds100}" if ds100 else ""
    return f"{name} (EVA {eva}){suffix}"


def _resolve_station(
    client: TimetablesClient,
    pattern: str,
    *,
    json_output: bool = False,
) -> Any:
    """Löst einen Bahnhofsnamen auf; mehrere Treffer → TTY-Auswahl/Liste."""

    matches = client.search_station(pattern)
    if not matches:
        _fail(
            f"Keine Station zu {pattern!r} gefunden.",
            EXIT_ERROR,
            json_output=json_output,
            hint="Schreibweise prüfen, z. B. `fahrgastrechte station \"Stuttgart Hbf\"`.",
        )
    exact = [m for m in matches if m.name.lower() == pattern.strip().lower()]
    if len(exact) == 1:
        return exact[0]
    if len(matches) == 1:
        return matches[0]

    if _is_interactive():
        for index, station in enumerate(matches, start=1):
            sys.stderr.write(f"{index}) {_station_line(station.name, station.eva_no, station.ds100)}\n")
        sys.stderr.flush()
        selected = _select_index(len(matches), "Station")
        if selected is not None:
            return matches[selected]
        _fail(
            "Keine Auswahl getroffen.",
            EXIT_INVALID_INPUT,
            json_output=json_output,
            hint="Befehl erneut ausführen und eine Nummer wählen.",
        )

    for station in matches:
        out.print(_station_line(station.name, station.eva_no, station.ds100))
    _fail(
        f"{len(matches)} Treffer für {pattern!r}; ohne Terminal ist keine Auswahl möglich.",
        EXIT_NOT_INITIALIZED,
        json_output=json_output,
        hint="Suchbegriff genauer eingeben, z. B. den vollständigen Bahnhofsnamen.",
    )


def _resolve_case_id(
    conn: sqlite3.Connection, identifier: str, *, json_output: bool = False
) -> int:
    """Zahl = Fall-ID, sonst Suche gegen Zugnummer/Linie."""

    value = (identifier or "").strip()
    if value.isdigit():
        return int(value)
    matches = fc_cases.find_cases_by_train(conn, value)
    if not matches:
        _fail(
            f"Kein Fall zu {identifier!r} gefunden.",
            EXIT_ERROR,
            json_output=json_output,
            hint="`fahrgastrechte list` zeigt alle Fälle.",
        )
    if len(matches) == 1:
        return int(matches[0]["id"])
    if _is_interactive():
        for index, case in enumerate(matches, start=1):
            sys.stderr.write(
                f"{index}) Fall #{case['id']} {case['service_date']} "
                f"{case['train_number'] or case['line']} ({case['current_status']})\n"
            )
        sys.stderr.flush()
        selected = _select_index(len(matches), "Fall")
        if selected is not None:
            return int(matches[selected]["id"])
        _fail(
            "Keine Auswahl getroffen.",
            EXIT_INVALID_INPUT,
            json_output=json_output,
            hint="Befehl erneut ausführen und eine Nummer wählen.",
        )
    for case in matches:
        out.print(
            f"#{case['id']} {case['service_date']} "
            f"{case['train_number'] or case['line'] or '—'}"
        )
    _fail(
        f"{len(matches)} Fälle zu {identifier!r}; ohne Terminal ist keine Auswahl möglich.",
        EXIT_INVALID_INPUT,
        json_output=json_output,
        hint="Stattdessen die Fall-ID angeben (siehe `fahrgastrechte list`).",
    )


def _profile_from_config(conn: sqlite3.Connection) -> dict[str, str]:
    profile: dict[str, str] = {}
    for key in ("name", "address", "iban", "ticket_type", "dticket_id"):
        value = fc_config.get_config(conn, key)
        if value:
            profile[key] = value
    return profile


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
    confirm: bool = CONFIRM_OPTION,
    json_output: bool = JSON_OPTION,
) -> None:
    """Initialisiert die lokale Datenbank (idempotent).

    Im Terminal werden Stammdaten abgefragt; leere Eingaben überspringen den
    jeweiligen Wert. Ohne TTY ist nur der Schreibpfad mit ``--confirm``
    erlaubt, damit ein versehentlicher Agenten-Lauf nichts anlegt.
    """

    _require_confirmation_path("Init", confirm, json_output)
    profile: dict[str, str] = {}
    if _is_interactive() and not confirm:
        console.print("[bold]Stammdaten[/bold] (leer = überspringen)")
        name = _ask_line("Name: ").strip()
        address = _ask_line("Adresse: ").strip()
        iban = _ask_line("IBAN: ").strip()
        ticket_type = _ask_line(
            f"Ticket-Typ [{fc_config.DEFAULT_TICKET_TYPE}]: "
        ).strip()
        price = _ask_line(
            f"Ticket-Preis in Cent [{fc_config.DEFAULT_TICKET_PRICE_CENTS}]: "
        ).strip()
        if not _ask_yes_no("Init"):
            console.print("Abgebrochen, nichts geändert.")
            raise typer.Exit(code=EXIT_OK)
        for key, value in (
            ("name", name),
            ("address", address),
            ("iban", iban),
            ("ticket_type", ticket_type),
            ("ticket_price_cents", price),
        ):
            if value:
                profile[key] = value

    target = db.init_db()
    if profile:
        with db.connect(target) as conn:
            for key, value in profile.items():
                fc_config.set_config(conn, key, value)
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
    confirm: bool = CONFIRM_OPTION,
    json_output: bool = JSON_OPTION,
) -> None:
    """Setzt einen Konfigurations-Wert in der DB (Exit 0/3)."""

    _require_confirmation_path("Config set", confirm, json_output)
    db_path = _open_db_or_exit()
    try:
        with db.connect(db_path) as conn:
            if not fc_config.is_known_key(key):
                raise ValueError(
                    f"Unbekannter config-key {key!r}. "
                    f"Erlaubt: {', '.join(fc_config.CONFIG_KEYS)}"
                )
            if _is_interactive() and not confirm:
                console.print(f"config set {key} = {value}")
                if not _ask_yes_no("Config set"):
                    console.print("Abgebrochen, nichts geändert.")
                    raise typer.Exit(code=EXIT_OK)
            fc_config.set_config(conn, key, value)
        payload = {"key": key, "status": "ok", "value_set": True}
    except ValueError as exc:
        _fail(str(exc), EXIT_INVALID_INPUT, json_output=json_output)
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
        _fail(
            f"Unbekannter config-key {key!r}. Erlaubt: {', '.join(fc_config.CONFIG_KEYS)}",
            EXIT_INVALID_INPUT,
            json_output=json_output,
        )
    with db.connect(db_path) as conn:
        value = fc_config.get_config(conn, key)
    if value is None:
        if _wants_json(json_output):
            _emit_json({"key": key, "value": None})
        else:
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
    if not values and not _wants_json(json_output):
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


# ----------------------------------------------------------- station / login
@app.command()
def station(
    suchbegriff: str = typer.Argument(..., help="Bahnhofsname oder Suchbegriff."),
    json_output: bool = JSON_OPTION,
) -> None:
    """Sucht Bahnhöfe über die DB-Timetables-API (nur lesend)."""

    client = _build_timetables_client()
    try:
        with client:
            matches = client.search_station(suchbegriff)
    except TimetablesError as exc:
        _map_timetables_error(exc, json_output=json_output)
    if not matches:
        _fail(
            f"Keine Station zu {suchbegriff!r} gefunden.",
            EXIT_ERROR,
            json_output=json_output,
            hint="Schreibweise prüfen oder den vollständigen Bahnhofsnamen versuchen.",
        )
    if _wants_json(json_output):
        _emit_json(
            {
                "query": suchbegriff,
                "stations": [
                    {
                        "name": item.name,
                        "eva_no": item.eva_no,
                        "ds100": item.ds100,
                    }
                    for item in matches
                ],
            }
        )
        return
    for item in matches:
        out.print(_station_line(item.name, item.eva_no, item.ds100))


@app.command()
def login() -> None:
    """Speichert DB-API-Zugangsdaten lokal (API-Key verdeckt, Session 0600)."""

    if not _is_interactive():
        _fail(
            "Login braucht ein interaktives Terminal.",
            EXIT_INVALID_INPUT,
            hint=(
                "Im Terminal `fahrgastrechte login` ausführen oder "
                "FAHRGASTRECHTE_DB_CLIENT_ID / FAHRGASTRECHTE_DB_API_KEY setzen."
            ),
        )
    try:
        console.print("[bold]DB-API-Zugangsdaten[/bold] (nur lokal gespeichert)")
        client_id = input("DB-Client-ID: ").strip()
        api_key = getpass.getpass("DB-API-Key: ").strip()
    except (EOFError, KeyboardInterrupt):
        console.print("Abgebrochen, keine Zugangsdaten gespeichert.")
        raise typer.Exit(code=EXIT_INVALID_INPUT)
    if not client_id or not api_key:
        _fail(
            "DB-Client-ID und DB-API-Key dürfen nicht leer sein.",
            EXIT_INVALID_INPUT,
            hint="`fahrgastrechte login` erneut ausführen.",
        )
    path = fc_session.save_credentials(client_id, api_key)
    console.print(f"Zugangsdaten gespeichert. ({path})")


@app.command()
def logout() -> None:
    """Löscht Session-Datei und die alten DB-Config-Credentials (idempotent)."""

    removed = fc_session.delete_session()
    db_path = db.default_db_path()
    if db.get_schema_version(db_path) == db.SCHEMA_VERSION:
        with db.connect(db_path) as conn:
            for key in ("db_client_id", "db_api_key"):
                fc_config.delete_config(conn, key)
    console.print("Abgemeldet." if removed else "Nicht angemeldet, nichts zu tun.")


@app.command()
def whoami(
    json_output: bool = JSON_OPTION,
) -> None:
    """Zeigt, ob DB-API-Zugangsdaten vorhanden sind (niemals Secrets)."""

    credentials = fc_session.resolve_credentials()
    payload = {
        "configured": credentials.configured,
        "source": credentials.source,
        "client_id": fc_session.mask_secret(credentials.client_id),
        "api_key": fc_session.mask_secret(credentials.api_key),
    }
    if _wants_json(json_output):
        _emit_json(payload)
        return
    if credentials.configured:
        out.print("DB-API-Zugangsdaten: vorhanden")
        out.print(f"Quelle: {credentials.source or 'unbekannt'}")
        out.print(f"Client-ID: {payload['client_id']}")
        out.print(f"API-Key: {payload['api_key']}")
    else:
        out.print("DB-API-Zugangsdaten: fehlen")
        console.print(
            "[yellow]Nächster Schritt:[/yellow] `fahrgastrechte login` "
            "oder FAHRGASTRECHTE_DB_CLIENT_ID / FAHRGASTRECHTE_DB_API_KEY setzen."
        )


# ----------------------------------------------------------- add / list / show / trip
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
    """F1 (Altpfad): Verspätungs-Fall mit expliziten Optionen erfassen."""

    db_path = _open_db_or_exit()

    with db.connect(db_path) as conn:
        try:
            ticket_value = _resolve_ticket_type(ticket_type, conn)
        except ValueError as ve:
            _fail(str(ve), EXIT_INVALID_INPUT, json_output=json_output)
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
        _fail(str(exc), EXIT_INVALID_INPUT, json_output=json_output)

    result_payload = {
        "case_id": result.case_id,
        "trip_id": result.trip_id,
        "dup_hash": result.dup_hash,
        "created_at": result.created_at,
        "ticket_type": payload.ticket_type,
        "expected_cents": payload.expected_cents,
    }
    _emit(result_payload, json_output, title=f"Fall #{result.case_id} angelegt")


def _cmd_add_named(
    *,
    train: str,
    start: str,
    ziel: str,
    date: str,
    delay_min: int | None,
    reason: str | None,
    ticket_type: str | None,
    ticket_class: int | None,
    force: bool,
    confirm: bool,
    json_output: bool,
) -> None:
    """Menschenpfad: ``add <zug> <start> <ziel>`` mit ``[j/N]``."""

    _require_confirmation_path("Fall anlegen", confirm, json_output)
    try:
        iso_date = _validate_date(date, "--datum")
    except ValueError as exc:
        _fail(str(exc), EXIT_INVALID_INPUT, json_output=json_output)

    client = _build_timetables_client()
    try:
        with client:
            start_station = _resolve_station(client, start, json_output=json_output)
            dest_station = _resolve_station(client, ziel, json_output=json_output)

            if not _wants_json(json_output):
                out.print(f"Zug: {train}")
                out.print(f"Von: {start_station.name} (EVA {start_station.eva_no})")
                out.print(f"Nach: {dest_station.name} (EVA {dest_station.eva_no})")
                out.print(f"Datum: {iso_date}")

            if not confirm:
                if not _ask_yes_no("Fall anlegen"):
                    console.print("Abgebrochen, nichts geändert.")
                    raise typer.Exit(code=EXIT_OK)

            rt_delay: int | None = delay_min
            planned_arr: str | None = None
            actual_arr: str | None = None
            line = train
            train_number = train
            category: str | None = None
            evu: str | None = None
            trip_ref: str | None = None
            data_source = DataSource.MANUAL
            data_quality = DataQuality.MANUAL_ONLY
            warning: str | None = None
            try:
                realtime = client.get_realtime_changes(
                    dest_station.eva_no, kind=RealtimeKind.RCHG
                )
                stop = _match_stop(realtime, train)
                if stop is not None:
                    data_source = DataSource.TIMETABLES_API
                    data_quality = DataQuality.BEST_EFFORT
                    trip_ref = stop.id
                    if stop.trip_label is not None:
                        label = stop.trip_label
                        category = label.category
                        evu = label.owner
                        if label.number:
                            train_number = label.number
                        if label.category and label.number:
                            line = f"{label.category} {label.number}"
                    arrival = stop.arrival
                    if arrival is not None:
                        if arrival.planned_time is not None:
                            planned_arr = arrival.planned_time.astimezone(
                                fc_cases.BERLIN
                            ).isoformat(timespec="minutes")
                        if arrival.actual_time is not None:
                            actual_arr = arrival.actual_time.astimezone(
                                fc_cases.BERLIN
                            ).isoformat(timespec="minutes")
                        rt_delay = arrival.delay_min if arrival.delay_min is not None else delay_min
                        if (
                            arrival.planned_time is not None
                            and arrival.actual_time is not None
                            and arrival.delay_min is not None
                        ):
                            data_quality = DataQuality.VERIFIED
                    if not _wants_json(json_output):
                        out.print(f"Echtzeit: {_format_delay(rt_delay)}")
                else:
                    warning = f"Zug {train!r} nicht in den Echtzeitdaten am Zielbahnhof."
            except TimetablesError as exc:
                warning = f"{type(exc).__name__}: {exc}"
    except TimetablesError as exc:
        _map_timetables_error(exc, json_output=json_output)

    db_path = db.init_db()
    with db.connect(db_path) as conn:
        try:
            ticket_value = _resolve_ticket_type(ticket_type, conn)
        except ValueError as exc:
            _fail(str(exc), EXIT_INVALID_INPUT, json_output=json_output)
        expected_cents = _apply_rule_if_empty(
            conn,
            ticket_type=ticket_value,
            delay_min=rt_delay,
            ticket_class=ticket_class,
            explicit_expected_cents=0,
        )
        reason_text = reason or "Rekonstruktion aus DB-Timetables-API"
        payload = fc_cases.AddCaseInput(
            service_date=iso_date,
            train_number=train_number,
            start_eva=start_station.eva_no,
            dest_eva=dest_station.eva_no,
            ticket_type=ticket_value,
            reason_text=reason_text,
            expected_cents=expected_cents,
            planned_arr=planned_arr,
            actual_arr=actual_arr,
            delay_min_arr=rt_delay,
            line=line,
            evu=evu,
            category=category,
            ticket_class=ticket_class,
            data_source=data_source,
            data_quality=data_quality,
            trip_ref=trip_ref,
            reconstructed_at=(
                fc_cases._now_iso()
                if data_source != DataSource.MANUAL
                else None
            ),
            force=force,
        )
        try:
            result = fc_cases.add_case(conn, payload)
        except fc_cases.DuplicateCaseError as exc:
            # identischer Hash ⇒ exakt dieselben Daten; erneutes `j` ist dann
            # idempotent (No-op) statt ein Fehler — der Bestand bleibt #1.
            existing = fc_cases.show_case(conn, exc.original_id)
            result = fc_cases.AddCaseResult(
                case_id=exc.original_id,
                trip_id=int(existing["trip_id"]),
                dup_hash=exc.dup_hash,
                created_at=str(existing["created_at"]),
                updated_at=str(existing["updated_at"]),
                was_duplicate=True,
                original_id=exc.original_id,
            )
        except ValueError as exc:
            _fail(str(exc), EXIT_INVALID_INPUT, json_output=json_output)

    if _wants_json(json_output):
        result_payload: dict[str, Any] = {
            "case_id": result.case_id,
            "trip_id": result.trip_id,
            "train": train,
            "start_eva": start_station.eva_no,
            "dest_eva": dest_station.eva_no,
            "service_date": iso_date,
            "delay_min_arr": rt_delay,
            "planned_arr": planned_arr,
            "actual_arr": actual_arr,
            "data_source": data_source.value,
            "data_quality": data_quality.value,
            "expected_cents": expected_cents,
            "current_status": CaseStatusName.OFFEN.value,
        }
        if warning:
            result_payload["warning"] = warning
        _emit_json(result_payload)
    else:
        if warning:
            console.print(f"[yellow]Hinweis:[/yellow] {warning}")
        out.print(f"Fall #{result.case_id} angelegt (offen)")


@app.command()
def add(
    train_arg: str | None = typer.Argument(None, metavar="ZUG", help="Zug oder Linie, z. B. \"RE 5\"."),
    start_arg: str | None = typer.Argument(None, metavar="START", help="Startbahnhof als Name."),
    ziel_arg: str | None = typer.Argument(None, metavar="ZIEL", help="Zielbahnhof als Name."),
    service_date: str | None = typer.Option(
        None, "--service-date", "--datum", help="Service-Datum (YYYY-MM-DD)."
    ),
    train: str | None = typer.Option(None, "--train", "--zug", help="Zugnummer (Altform)."),
    start_eva: int | None = typer.Option(None, "--start-eva", help="Start-EVA (Altform)."),
    dest_eva: int | None = typer.Option(None, "--dest-eva", help="Ziel-EVA (Altform)."),
    reason: str | None = typer.Option(None, "--reason", "--begruendung", help="Freitext-Begründung."),
    ticket_type: str | None = typer.Option(None, "--ticket-type", help="Ticket-Typ (Default: config/d_ticket)."),
    expected_cents: int = typer.Option(0, "--expected-cents", help="Erwartete Entschädigung in Cent."),
    planned_dep: str | None = typer.Option(None, "--planned-dep", help="Soll-Abfahrtszeit (ISO)."),
    actual_dep: str | None = typer.Option(None, "--actual-dep", help="Ist-Abfahrtszeit (ISO)."),
    planned_arr: str | None = typer.Option(None, "--planned-arr", help="Soll-Ankunftszeit (ISO)."),
    actual_arr: str | None = typer.Option(None, "--actual-arr", help="Ist-Ankunftszeit (ISO)."),
    delay_min: int | None = typer.Option(None, "--delay-min", help="Verspätung am Ziel in Minuten."),
    line: str | None = typer.Option(None, "--line", "--linie", help="Linie, z. B. 'RE 5'."),
    evu: str | None = typer.Option(None, "--evu", help="Eisenbahn-Verkehrsunternehmen."),
    category: str | None = typer.Option(None, "--category", help="Produktkategorie (RE/RB/S/IC/ICE)."),
    ticket_class: int | None = typer.Option(None, "--ticket-class", help="1 oder 2 (Bahn-Klasse)."),
    evidence: str | None = typer.Option(None, "--evidence", help="Pfad zu einem Beleg."),
    cap_applied: int | None = typer.Option(None, "--cap-applied", help="25-%-Deckel in Cent."),
    notes: str | None = typer.Option(None, "--notes", help="Interne Notizen."),
    force: bool = typer.Option(False, "--force", help="Duplikat trotzdem anlegen."),
    confirm: bool = CONFIRM_OPTION,
    json_output: bool = JSON_OPTION,
) -> None:
    """F1: Verspätungsfall anlegen (positional Name oder Altform mit Optionen)."""

    positional = [train_arg, start_arg, ziel_arg]
    has_positional = any(item is not None for item in positional)
    has_legacy = any(
        item is not None for item in (service_date, train, start_eva, dest_eva)
    )
    if has_positional and has_legacy:
        _fail(
            "Bitte entweder positional (ZUG START ZIEL) oder die Alt-Optionen nutzen.",
            EXIT_INVALID_INPUT,
            json_output=json_output,
        )
    if has_positional:
        if any(item is None for item in positional):
            _fail(
                "Für `add` werden Zug, Start und Ziel benötigt.",
                EXIT_INVALID_INPUT,
                json_output=json_output,
                hint='Beispiel: `fahrgastrechte add "RE 5" "Stuttgart Hbf" "Ludwigsburg"`.',
            )
        _cmd_add_named(
            train=str(train_arg),
            start=str(start_arg),
            ziel=str(ziel_arg),
            date=service_date or _today_iso(),
            delay_min=delay_min,
            reason=reason,
            ticket_type=ticket_type,
            ticket_class=ticket_class,
            force=force,
            confirm=confirm,
            json_output=json_output,
        )
        return

    missing = [
        flag
        for flag, value in (
            ("--service-date", service_date),
            ("--train", train),
            ("--start-eva", start_eva),
            ("--dest-eva", dest_eva),
            ("--reason", reason),
        )
        if value in (None, "")
    ]
    if missing:
        _fail(
            "Altform von `add` braucht " + ", ".join(missing) + ".",
            EXIT_INVALID_INPUT,
            json_output=json_output,
            hint='Menschen nutzen `fahrgastrechte add "RE 5" "Stuttgart Hbf" "Ludwigsburg"`.',
        )
    _cmd_add(
        service_date=str(service_date),
        train=str(train),
        start_eva=int(start_eva),
        dest_eva=int(dest_eva),
        reason=str(reason),
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
        _fail(str(exc), EXIT_INVALID_INPUT, json_output=json_output)

    if _wants_json(json_output):
        payload = {
            "cases": cases_list,
            "summary": {
                "count": totals["count"],
                "total_cents": totals["total_cents"],
            },
        }
        _emit_json(payload)
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
    out.print(table)


@app.command(name="list")
def list_(  # noqa: A001 -- CLI-Konvention kollidiert mit builtin
    status_arg: str | None = typer.Argument(None, metavar="STATUS", help="Status-Filter (z. B. offen)."),
    status: str | None = typer.Option(None, "--status", help="Status-Filter (Alias)."),
    since: str | None = typer.Option(None, "--since", help="Nur Fälle ab diesem Datum (YYYY-MM-DD)."),
    until: str | None = typer.Option(None, "--until", help="Nur Fälle bis zu diesem Datum (YYYY-MM-DD)."),
    line: str | None = typer.Option(None, "--line", help="Nur Fälle mit dieser Linie."),
    json_output: bool = JSON_OPTION,
) -> None:
    """F3: Eigene Fälle listen, mit Filter und Summenzeile."""

    _cmd_list(
        status=status_arg or status,
        since=since,
        until=until,
        line=line,
        json_output=json_output,
    )


def _cmd_show(*, case_id: int, json_output: bool) -> None:
    """F4: einzelnen Fall anzeigen."""

    db_path = _open_db_or_exit()
    try:
        with db.connect(db_path) as conn:
            payload = fc_cases.show_case(conn, case_id)
    except fc_cases.CaseNotFoundError as exc:
        _fail(str(exc), EXIT_ERROR, json_output=json_output, hint="`fahrgastrechte list` zeigt alle Fälle.")
    if _wants_json(json_output):
        _emit_json(payload)
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
    out.print(table)


@app.command()
def show(
    identifier: str = typer.Argument(..., metavar="ZUG|ID", help="Fall-ID oder Zugnummer/Linie."),
    json_output: bool = JSON_OPTION,
) -> None:
    """F4: einzelnen Fall per ID oder Zugnummer anzeigen."""

    db_path = _open_db_or_exit()
    with db.connect(db_path) as conn:
        case_id = _resolve_case_id(conn, identifier, json_output=json_output)
    _cmd_show(case_id=case_id, json_output=json_output)


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
    """F2 (Altpfad): Trip rekonstruieren und als Fall speichern."""

    db_path = _open_db_or_exit()
    try:
        iso_date = _validate_date(date, "--date")
    except ValueError as ve:
        _fail(str(ve), EXIT_INVALID_INPUT, json_output=json_output)

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
        client = _build_timetables_client()
        with client:
            rt = client.get_realtime_changes(dest_eva, kind=RealtimeKind.RCHG)
            stop = _match_stop(rt, train)
            if stop is not None and stop.arrival is not None:
                arr = stop.arrival
                if arr.planned_time is not None:
                    planned_arr = arr.planned_time.astimezone(
                        fc_cases.BERLIN
                    ).isoformat(timespec="minutes")
                if arr.actual_time is not None:
                    actual_arr = arr.actual_time.astimezone(
                        fc_cases.BERLIN
                    ).isoformat(timespec="minutes")
                delay_min_arr = arr.delay_min
            if stop is not None and stop.departure is not None:
                dep = stop.departure
                if dep.planned_time is not None:
                    planned_dep = dep.planned_time.astimezone(
                        fc_cases.BERLIN
                    ).isoformat(timespec="minutes")
                if dep.actual_time is not None:
                    actual_dep = dep.actual_time.astimezone(
                        fc_cases.BERLIN
                    ).isoformat(timespec="minutes")
            if stop is not None and stop.trip_label is not None:
                tl = stop.trip_label
                if tl.number:
                    train = tl.number or train
                if tl.category:
                    category = tl.category
                if tl.owner:
                    evu = tl.owner
                trip_ref = stop.id
            if stop is not None:
                data_source = DataSource.TIMETABLES_API
                if delay_min_arr is None or planned_arr is None or actual_arr is None:
                    data_quality = DataQuality.BEST_EFFORT
                else:
                    data_quality = DataQuality.VERIFIED
    except TimetablesError as exc:
        error_message = f"{type(exc).__name__}: {exc}"
        data_quality = DataQuality.BEST_EFFORT
    except Exception as exc:  # pragma: no cover - defensive
        error_message = f"Unerwarteter Fehler: {exc}"
        data_quality = DataQuality.BEST_EFFORT

    with db.connect(db_path) as conn:
        try:
            ticket_value = _resolve_ticket_type(ticket_type, conn)
        except ValueError as exc:
            _fail(str(exc), EXIT_INVALID_INPUT, json_output=json_output)
        expected_cents = _apply_rule_if_empty(
            conn,
            ticket_type=ticket_value,
            delay_min=delay_min_arr,
            ticket_class=ticket_class,
            explicit_expected_cents=0,
        )

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
        _fail(str(exc), EXIT_INVALID_INPUT, json_output=json_output)

    result_payload: dict[str, Any] = {
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
        result_payload["warning"] = error_message

    if _wants_json(json_output):
        _emit_json(result_payload)
        return

    title = (
        f"Rekonstruierter Fall #{result.case_id}"
        if data_quality is DataQuality.VERIFIED
        else f"Rekonstruktions-Versuch #{result.case_id} (best_effort)"
    )
    table = Table(title=title, show_header=True, header_style="bold")
    table.add_column("Feld", style="cyan")
    table.add_column("Wert", style="white")
    for key, value in result_payload.items():
        table.add_row(str(key), "—" if value is None else str(value))
    if error_message is not None:
        console.print(f"[yellow]Warnung:[/yellow] {error_message}")
    out.print(table)


def _cmd_trip_named(
    *,
    train: str,
    start: str,
    ziel: str,
    date: str,
    save: bool,
    reason: str | None,
    ticket_type: str | None,
    ticket_class: int | None,
    confirm: bool,
    json_output: bool,
) -> None:
    """Menschenpfad: lesende `trip`-Vorschau, optional `--save`."""

    if save:
        _require_confirmation_path("Fall anlegen", confirm, json_output)
    try:
        iso_date = _validate_date(date, "--datum")
    except ValueError as exc:
        _fail(str(exc), EXIT_INVALID_INPUT, json_output=json_output)

    client = _build_timetables_client()
    try:
        with client:
            start_station = _resolve_station(client, start, json_output=json_output)
            dest_station = _resolve_station(client, ziel, json_output=json_output)
            realtime = client.get_realtime_changes(
                dest_station.eva_no, kind=RealtimeKind.RCHG
            )
            stop = _match_stop(realtime, train)

            delay: int | None = None
            planned_arr: str | None = None
            actual_arr: str | None = None
            planned_time = None
            actual_time = None
            train_number = train
            line = train
            category: str | None = None
            evu: str | None = None
            trip_ref: str | None = None
            if stop is not None:
                trip_ref = stop.id
                if stop.trip_label is not None:
                    label = stop.trip_label
                    category = label.category
                    evu = label.owner
                    if label.number:
                        train_number = label.number
                    if label.category and label.number:
                        line = f"{label.category} {label.number}"
                if stop.arrival is not None:
                    planned_time = stop.arrival.planned_time
                    actual_time = stop.arrival.actual_time
                    delay = stop.arrival.delay_min
                    if planned_time is not None:
                        planned_arr = planned_time.astimezone(
                            fc_cases.BERLIN
                        ).isoformat(timespec="minutes")
                    if actual_time is not None:
                        actual_arr = actual_time.astimezone(
                            fc_cases.BERLIN
                        ).isoformat(timespec="minutes")
    except TimetablesError as exc:
        _map_timetables_error(exc, json_output=json_output)

    delay_text = _format_delay(delay)
    arrival_text = _format_hhmm(actual_time)
    planned_text = _format_hhmm(planned_time)
    read_payload = {
        "train": line,
        "start": {"name": start_station.name, "eva_no": start_station.eva_no},
        "dest": {"name": dest_station.name, "eva_no": dest_station.eva_no},
        "service_date": iso_date,
        "planned_arr": planned_arr,
        "actual_arr": actual_arr,
        "delay_min_arr": delay,
        "found": stop is not None,
    }

    if not save:
        if _wants_json(json_output):
            read_payload["saved"] = False
            _emit_json(read_payload)
            return
        out.print(f"Zug: {line}")
        out.print(f"Von: {start_station.name} (EVA {start_station.eva_no})")
        out.print(f"Nach: {dest_station.name} (EVA {dest_station.eva_no})")
        if stop is None:
            out.print("Ankunft: keine Echtzeitdaten zum Zug gefunden")
        else:
            out.print(
                f"Ankunft: {arrival_text} (Soll {planned_text}, {delay_text})"
            )
        out.print("Nicht gespeichert. Anlegen mit `fahrgastrechte add ...`")
        return

    if not _wants_json(json_output):
        out.print(f"Zug: {line}")
        out.print(f"Von: {start_station.name} (EVA {start_station.eva_no})")
        out.print(f"Nach: {dest_station.name} (EVA {dest_station.eva_no})")
        out.print(f"Datum: {iso_date}")
        if stop is not None:
            out.print(f"Ankunft: {arrival_text} (Soll {planned_text}, {delay_text})")
    if not confirm:
        if not _ask_yes_no("Fall anlegen"):
            console.print("Abgebrochen, nichts geändert.")
            raise typer.Exit(code=EXIT_OK)

    db_path = db.init_db()
    with db.connect(db_path) as conn:
        try:
            ticket_value = _resolve_ticket_type(ticket_type, conn)
        except ValueError as exc:
            _fail(str(exc), EXIT_INVALID_INPUT, json_output=json_output)
        expected_cents = _apply_rule_if_empty(
            conn,
            ticket_type=ticket_value,
            delay_min=delay,
            ticket_class=ticket_class,
            explicit_expected_cents=0,
        )
        payload = fc_cases.AddCaseInput(
            service_date=iso_date,
            train_number=train_number,
            start_eva=start_station.eva_no,
            dest_eva=dest_station.eva_no,
            ticket_type=ticket_value,
            reason_text=reason or "Rekonstruktion aus DB-Timetables-API",
            expected_cents=expected_cents,
            planned_arr=planned_arr,
            actual_arr=actual_arr,
            delay_min_arr=delay,
            line=line,
            evu=evu,
            category=category,
            ticket_class=ticket_class,
            data_source=(
                DataSource.TIMETABLES_API if stop is not None else DataSource.MANUAL
            ),
            data_quality=(
                DataQuality.VERIFIED
                if stop is not None and delay is not None
                else DataQuality.BEST_EFFORT
            ),
            trip_ref=trip_ref,
            reconstructed_at=(
                fc_cases._now_iso() if stop is not None else None
            ),
        )
        try:
            result = fc_cases.add_case(conn, payload)
        except fc_cases.DuplicateCaseError as exc:
            _fail(
                f"Duplikat: Fall #{exc.original_id} hat bereits denselben Hash. "
                "Mit --force trotzdem anlegen.",
                EXIT_ERROR,
                json_output=json_output,
            )
        except ValueError as exc:
            _fail(str(exc), EXIT_INVALID_INPUT, json_output=json_output)

    if _wants_json(json_output):
        read_payload.update(
            {
                "saved": True,
                "case_id": result.case_id,
                "expected_cents": expected_cents,
            }
        )
        _emit_json(read_payload)
        return
    out.print(f"Fall #{result.case_id} angelegt (offen)")


@app.command()
def trip(
    train_arg: str | None = typer.Argument(None, metavar="ZUG", help="Zug oder Linie, z. B. \"ICE 123\"."),
    start_arg: str | None = typer.Argument(None, metavar="START", help="Startbahnhof als Name."),
    ziel_arg: str | None = typer.Argument(None, metavar="ZIEL", help="Zielbahnhof als Name."),
    date: str | None = typer.Option(None, "--date", "--datum", help="Service-Datum (YYYY-MM-DD)."),
    train: str | None = typer.Option(None, "--train", "--zug", help="Zugnummer (Altform)."),
    start_eva: int | None = typer.Option(None, "--start-eva", help="Start-EVA (Altform)."),
    dest_eva: int | None = typer.Option(None, "--dest-eva", help="Ziel-EVA (Altform)."),
    reason: str | None = typer.Option(None, "--reason", "--begruendung", help="Optionaler Freitext."),
    ticket_type: str | None = typer.Option(None, "--ticket-type", help="Ticket-Typ (Default: config/d_ticket)."),
    ticket_class: int | None = typer.Option(None, "--ticket-class", help="1 oder 2 (Bahn-Klasse)."),
    notes: str | None = typer.Option(None, "--notes", help="Interne Notizen."),
    force: bool = typer.Option(False, "--force", help="Duplikat überschreiben."),
    save: bool = typer.Option(False, "--save", help="Ergebnis lokal als Fall anlegen."),
    confirm: bool = CONFIRM_OPTION,
    json_output: bool = JSON_OPTION,
) -> None:
    """F2: Fahrt prüfen (lesend) oder mit ``--save`` lokal anlegen."""

    positional = [train_arg, start_arg, ziel_arg]
    has_positional = any(item is not None for item in positional)
    has_legacy = any(
        item is not None for item in (date, train, start_eva, dest_eva)
    )
    if has_positional and has_legacy:
        _fail(
            "Bitte entweder positional (ZUG START ZIEL) oder die Alt-Optionen nutzen.",
            EXIT_INVALID_INPUT,
            json_output=json_output,
        )
    if has_positional:
        if any(item is None for item in positional):
            _fail(
                "Für `trip` werden Zug, Start und Ziel benötigt.",
                EXIT_INVALID_INPUT,
                json_output=json_output,
                hint='Beispiel: `fahrgastrechte trip "ICE 123" "Stuttgart Hbf" "Mannheim Hbf"`.',
            )
        _cmd_trip_named(
            train=str(train_arg),
            start=str(start_arg),
            ziel=str(ziel_arg),
            date=date or _today_iso(),
            save=save,
            reason=reason,
            ticket_type=ticket_type,
            ticket_class=ticket_class,
            confirm=confirm,
            json_output=json_output,
        )
        return

    missing = [
        flag
        for flag, value in (
            ("--date", date),
            ("--train", train),
            ("--start-eva", start_eva),
            ("--dest-eva", dest_eva),
        )
        if value in (None, "")
    ]
    if missing:
        _fail(
            "Altform von `trip` braucht " + ", ".join(missing) + ".",
            EXIT_INVALID_INPUT,
            json_output=json_output,
            hint='Menschen nutzen `fahrgastrechte trip "ICE 123" "Stuttgart Hbf" "Mannheim Hbf"`.',
        )
    _cmd_trip(
        date=str(date),
        train=str(train),
        start_eva=int(start_eva),
        dest_eva=int(dest_eva),
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
        _fail(str(exc), EXIT_ERROR, json_output=json_output, hint="`fahrgastrechte list` zeigt alle Fälle.")

    payload: dict[str, Any] = {"case_id": case_id, **result.to_dict()}
    if _wants_json(json_output):
        _emit_json(payload)
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
    out.print(table)


@app.command()
def calc(
    identifier: str = typer.Argument(..., metavar="ZUG|ID", help="Fall-ID oder Zugnummer/Linie."),
    json_output: bool = JSON_OPTION,
) -> None:
    """F5: Entschädigung per ID oder Zugnummer berechnen."""

    db_path = _open_db_or_exit()
    with db.connect(db_path) as conn:
        case_id = _resolve_case_id(conn, identifier, json_output=json_output)
    _cmd_calc(case_id=case_id, json_output=json_output)


@app.command()
def status(
    identifier: str = typer.Argument(..., metavar="ZUG|ID", help="Fall-ID oder Zugnummer/Linie."),
    neu: str = typer.Argument(..., metavar="NEU", help="Neuer Status (z. B. eingereicht)."),
    note: str | None = typer.Option(None, "--note", help="Begründung (Pflicht bei rueckfrage/abgelehnt)."),
    force: bool = typer.Option(False, "--force", help="Nicht erlaubten Übergang erzwingen."),
    confirm: bool = CONFIRM_OPTION,
    json_output: bool = JSON_OPTION,
) -> None:
    """F7: Status eines Falls ändern (mit Bestätigung)."""

    db_path = _open_db_or_exit()
    with db.connect(db_path) as conn:
        case_id = _resolve_case_id(conn, identifier, json_output=json_output)
        try:
            previous = fc_cases.get_current_status(conn, case_id)
            target = CaseStatusName(neu).value
        except ValueError:
            _fail(
                f"Unbekannter Status {neu!r}. Erlaubt: "
                + ", ".join(item.value for item in CaseStatusName),
                EXIT_INVALID_INPUT,
                json_output=json_output,
            )
        allowed = fc_cases.ALLOWED_TRANSITIONS.get(previous, set())
        if target != previous and target not in allowed and not force:
            _fail(
                f"Statuswechsel {previous!r} → {target!r} ist nicht erlaubt. "
                f"Erlaubt: {', '.join(sorted(allowed)) or 'keine'}. Mit --force erzwingen.",
                EXIT_INVALID_INPUT,
                json_output=json_output,
            )
        if target in fc_cases.NOTE_REQUIRED_STATUSES and not (note or "").strip():
            _fail(
                f"Status {target!r} braucht eine Begründung (--note).",
                EXIT_INVALID_INPUT,
                json_output=json_output,
            )
        if not confirm:
            if not _is_interactive():
                _fail(
                    "Status ändern braucht ein Terminal oder --confirm.",
                    EXIT_INVALID_INPUT,
                    json_output=json_output,
                    hint="Im Terminal bestätigen oder --confirm/--yes setzen.",
                )
            out.print(f"Fall #{case_id}: {previous} → {target}")
            if not _ask_yes_no("Status ändern"):
                console.print("Abgebrochen, nichts geändert.")
                raise typer.Exit(code=EXIT_OK)
        try:
            result = fc_cases.update_case_status(
                conn, case_id, target, note=note, force=force
            )
        except (fc_cases.InvalidStatusTransitionError, ValueError) as exc:
            _fail(str(exc), EXIT_INVALID_INPUT, json_output=json_output)
        except fc_cases.CaseNotFoundError as exc:
            _fail(str(exc), EXIT_ERROR, json_output=json_output)

    if _wants_json(json_output):
        _emit_json(result)
        return
    out.print(f"Status von Fall #{case_id}: {result['previous_status']} → {result['status']}")


@app.command()
def export(
    format_: str = typer.Argument(..., metavar="PDF|CSV", help="Exportformat: pdf oder csv."),
    name: str | None = typer.Argument(None, metavar="NAME", help="Zieldatei (Default: antrag-/faelle-Datum)."),
    status_filter: str | None = typer.Option(None, "--status", help="Nur Fälle mit diesem Status."),
    ids: str | None = typer.Option(None, "--ids", help="Kommagetrennte Fall-IDs."),
    confirm: bool = CONFIRM_OPTION,
    json_output: bool = JSON_OPTION,
) -> None:
    """F8/F9: Antrag als PDF oder CSV vorbereiten (schreibt nur lokal)."""

    fmt = format_.strip().lower()
    if fmt not in {"pdf", "csv"}:
        _fail(
            f"Unbekanntes Format {format_!r}; erlaubt: pdf, csv.",
            EXIT_INVALID_INPUT,
            json_output=json_output,
            hint="Beispiel: `fahrgastrechte export pdf`.",
        )

    db_path = _open_db_or_exit()
    with db.connect(db_path) as conn:
        all_cases = fc_cases.list_cases(conn, status=status_filter)
        profile = _profile_from_config(conn)

    if ids:
        try:
            wanted_ids = {int(part.strip()) for part in ids.split(",") if part.strip()}
        except ValueError:
            _fail(
                f"--ids erwartet Zahlen wie 1,2,3, bekommen: {ids!r}.",
                EXIT_INVALID_INPUT,
                json_output=json_output,
            )
        selected = [case for case in all_cases if int(case["id"]) in wanted_ids]
        missing_ids = wanted_ids - {int(case["id"]) for case in selected}
        if missing_ids:
            _fail(
                "Nicht gefundene Fall-IDs: " + ", ".join(str(i) for i in sorted(missing_ids)),
                EXIT_ERROR,
                json_output=json_output,
                hint="`fahrgastrechte list` zeigt alle vorhandenen Fälle.",
            )
    else:
        selected = all_cases

    if not selected:
        _fail(
            "Keine Fälle für den Export ausgewählt.",
            EXIT_ERROR,
            json_output=json_output,
            hint="`fahrgastrechte list` prüfen oder Filter lockern.",
        )

    default_name = (
        f"antrag-{_today_iso()}.pdf" if fmt == "pdf" else f"faelle-{_today_iso()}.csv"
    )
    target = Path(name or default_name).expanduser()

    if _is_interactive() and not confirm:
        out.print(f"Export: {fmt.upper()} mit {len(selected)} Fällen → {target}")
        if not _ask_yes_no("Export"):
            console.print("Abgebrochen, nichts geändert.")
            raise typer.Exit(code=EXIT_OK)
    elif not confirm:
        _fail(
            "Export braucht ein Terminal oder --confirm.",
            EXIT_INVALID_INPUT,
            json_output=json_output,
            hint="Im Terminal bestätigen oder --confirm/--yes setzen.",
        )

    try:
        if fmt == "pdf":
            written = fc_export.export_pdf(selected, target, profile=profile)
        else:
            written = fc_export.export_csv(selected, target)
    except OSError as exc:
        _fail(
            f"Export konnte nicht geschrieben werden: {exc}",
            EXIT_ERROR,
            json_output=json_output,
            hint="Schreibrechte und Zielpfad prüfen.",
        )

    result = {
        "format": fmt,
        "path": str(written),
        "rows": len(selected),
        "size_bytes": written.stat().st_size,
    }
    if _wants_json(json_output):
        _emit_json(result)
        return
    out.print(f"Geschrieben: {written} ({len(selected)} Fälle)")


def _cmd_stats(*, month: str | None, json_output: bool) -> None:
    """F11: Monats-Statistik (Fälle, Summe, Bagatellgrenze, Deckel)."""

    db_path = _open_db_or_exit()
    try:
        with db.connect(db_path) as conn:
            payload = fc_stats.compute_monthly_stats(conn, month=month)
    except ValueError as exc:
        _fail(str(exc), EXIT_INVALID_INPUT, json_output=json_output)

    if _wants_json(json_output):
        _emit_json(payload)
        return

    if month is not None:
        title = f"Statistik {payload.get('month', month)}"
        rows = {k: v for k, v in payload.items() if k != "month"}
        table = Table(title=title, show_header=True, header_style="bold")
        table.add_column("Kennzahl", style="cyan")
        table.add_column("Wert", style="white")
        for key, value in rows.items():
            table.add_row(str(key), str(value))
        out.print(table)
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
    out.print(table)


@app.command()
def stats(
    month: str | None = typer.Option(None, "--month", help="Kalendermonat (YYYY-MM oder YYYYMM)."),
    json_output: bool = JSON_OPTION,
) -> None:
    """F11: Statistik pro Monat (Fälle, Summe, Bagatellgrenze, Deckel)."""

    _cmd_stats(month=month, json_output=json_output)


def main() -> None:
    """Entry-Point für ``fahrgastrechte``-Konsolen-Skript."""

    try:
        # ``standalone_mode=False`` gibt uns die Chance, auch Usage-Fehler
        # (fehlende Argumente) mit ``--json`` als genau ein JSON-Objekt
        # auszugeben, statt Klartext auf stderr zu schreiben.
        result = app(standalone_mode=False)
    except typer.TyperException as exc:
        if _wants_json(False) or "--json" in sys.argv[1:]:
            _emit_json(
                {
                    "error": exc.format_message(),
                    "hint": "`fahrgastrechte --help` zeigt die erwarteten Argumente.",
                    "exit_code": int(getattr(exc, "exit_code", EXIT_INVALID_INPUT)),
                }
            )
        else:
            exc.show()
        raise SystemExit(int(getattr(exc, "exit_code", EXIT_INVALID_INPUT)))
    except typer.Abort:
        sys.stderr.write("\nAbgebrochen (Eingabe beendet).\n")
        raise SystemExit(EXIT_INVALID_INPUT)
    except KeyboardInterrupt:
        sys.stderr.write("\nAbgebrochen (Strg+C).\n")
        raise SystemExit(130)
    except EOFError:
        sys.stderr.write("\nAbgebrochen (Eingabe beendet).\n")
        raise SystemExit(EXIT_INVALID_INPUT)
    except BrokenPipeError:
        raise SystemExit(EXIT_OK)
    if isinstance(result, int):
        raise SystemExit(result)


if __name__ == "__main__":  # pragma: no cover
    main()
