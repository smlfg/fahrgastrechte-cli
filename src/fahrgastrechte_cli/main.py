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
import sys
from pathlib import Path
from typing import Any, NoReturn

import typer
from rich.console import Console
from rich.table import Table

from fahrgastrechte_core import __version__, config as fc_config, db


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


def main() -> None:
    """Entry-Point für ``fahrgastrechte``-Konsolen-Skript."""

    app()


if __name__ == "__main__":  # pragma: no cover
    main()
