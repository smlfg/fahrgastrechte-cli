"""Kommandozeilen-Einstieg des Verspätungskontos.

Die CLI enthält nur Typer-Befehle, Prompts, Ausgabe und Exit-Code-Abbildung.
Sämtliche Logik (Speicherung, Connectoren, Regeln) liegt in
:mod:`fahrgastrechte_core`; ab MVP-Schritt 2 kommen dort Modelle, Storage und
``init``/``config`` hinzu.

Schritt 1 (dieser Commit): Skelett mit ``--version``, ``--json``-Flag und
``ping``-Befehl als Sanity-Check. Keine Geschäftslogik.
"""

from __future__ import annotations

import json
import platform
import sys
from typing import Any

import typer
from rich.console import Console

from fahrgastrechte_core import __version__

app = typer.Typer(
    help=(
        "Verspätungskonto: DB-Verspätungsfälle erfassen, sammeln, prüfen, "
        "Anträge vorbereiten. Den Antrag schickt der Mensch selbst ab."
    ),
    no_args_is_help=True,
    add_completion=False,
)

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


# ---------------------------------------------------------------- Hilfen
def _emit(payload: dict[str, Any], json_output: bool) -> None:
    """JSON auf stdout, hübsche Tabelle auf stderr."""

    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        from rich.table import Table

        table = Table(title=payload.get("title", ""), show_header=True, header_style="bold")
        table.add_column("Feld", style="cyan")
        table.add_column("Wert", style="white")
        for key, value in payload.items():
            if key == "title":
                continue
            table.add_row(str(key), str(value))
        console.print(table)


def _version_callback(value: bool) -> None:
    """Wertet ``--version`` aus und beendet das Programm."""

    if value:
        typer.echo(f"fahrgastrechte {__version__}")
        raise typer.Exit(code=0)


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


def main() -> None:
    """Entry-Point für ``fahrgastrechte``-Konsolen-Skript."""

    app()


if __name__ == "__main__":  # pragma: no cover
    main()
