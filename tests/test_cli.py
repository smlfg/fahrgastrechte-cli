"""Smoke-Tests für das CLI-Skelett (MVP-Schritt 1).

Diese Tests prüfen nur, dass die Typer-App antwortet, ``--version`` die
Version ausgibt, ``--json`` valides JSON liefert und das Tool ohne
Geschäftslogik-Hülle installierbar ist. Logik-Tests kommen ab Schritt 2.
"""

from __future__ import annotations

import json
import re

from typer.testing import CliRunner

from fahrgastrechte_core import __version__
from fahrgastrechte_cli.main import app

runner = CliRunner()


def test_help_exits_zero() -> None:
    """``--help`` beendet sauber (Exit 0)."""

    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    assert "Verspätungskonto" in result.output


def test_version_flag_prints_version() -> None:
    """``--version`` gibt genau ``fahrgastrechte 0.1.0`` aus."""

    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == f"fahrgastrechte {__version__}"


def test_ping_text_output() -> None:
    """``fahrgastrechte ping`` ohne ``--json`` zeigt eine Tabelle."""

    result = runner.invoke(app, ["ping"])
    assert result.exit_code == 0, result.output
    assert "ok" in result.output
    assert __version__ in result.output


def test_ping_json_output_is_valid_and_stable() -> None:
    """``fahrgastrechte ping --json`` liefert striktes, sortiertes JSON."""

    result = runner.invoke(app, ["ping", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["version"] == __version__
    # Schlüssel-Reihenfolge ist durch ``sort_keys=True`` fixiert.
    keys = list(payload.keys())
    assert keys == sorted(keys)
    # Semver-Form
    assert re.fullmatch(r"\d+\.\d+\.\d+", payload["version"])


def test_unknown_command_exits_nonzero() -> None:
    """Typer lehnt unbekannte Befehle mit Exit != 0 ab."""

    result = runner.invoke(app, ["does-not-exist"])
    assert result.exit_code != 0
