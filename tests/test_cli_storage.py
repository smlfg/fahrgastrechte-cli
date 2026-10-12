"""CLI-Tests für MVP-Schritt 2: ``init`` und ``config set/get/list``.

Exit-Codes laut ANFORDERUNGEN §3:

* 0 — OK
* 2 — nicht initialisiert (DB-Datei fehlt)
* 3 — ungültige Eingabe

Jeder Test läuft gegen die per autouse-Fixture gesetzte ``tmp_path``-DB,
sodass die globale User-DB unberührt bleibt.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from fahrgastrechte_cli.main import app, EXIT_NOT_INITIALIZED, EXIT_INVALID_INPUT

runner = CliRunner()


# ------------------------------------------------------- init
def test_init_creates_db_and_returns_zero(_isolated_db: Path) -> None:
    """Erster ``init``-Aufruf: legt Datei an, Exit 0."""

    assert not _isolated_db.exists()
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    assert _isolated_db.exists(), "DB-Datei sollte angelegt sein"
    assert "DB initialisiert" in result.output


def test_init_json_output_is_stable(_isolated_db: Path) -> None:
    """``init --json`` liefert ein valides JSON mit schema_version=1."""

    result = runner.invoke(app, ["init", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["schema_version"] == 1
    assert payload["path"] == str(_isolated_db)


def test_init_is_idempotent(_isolated_db: Path) -> None:
    """Mehrfacher ``init``-Aufruf: Exit 0, kein zweiter Crash."""

    first = runner.invoke(app, ["init"])
    assert first.exit_code == 0
    second = runner.invoke(app, ["init"])
    assert second.exit_code == 0, second.output
    # Schema-Version bleibt 1.
    import sqlite3

    with sqlite3.connect(_isolated_db) as conn:
        v = conn.execute("SELECT schema_version FROM meta").fetchone()[0]
    assert v == 1


# ------------------------------------------------------- config set
def test_config_set_after_init_returns_zero(_isolated_db: Path) -> None:
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["config", "set", "name", "Samuel"])
    assert result.exit_code == 0, result.output
    assert result.output.strip().lower().startswith("ok") or "ok" in result.output


def test_config_set_before_init_returns_exit_two(_isolated_db: Path) -> None:
    """Ohne ``init`` → Exit 2 (nicht initialisiert)."""

    assert not _isolated_db.exists()
    result = runner.invoke(app, ["config", "set", "name", "Samuel"])
    assert result.exit_code == EXIT_NOT_INITIALIZED
    assert not _isolated_db.exists(), "set darf die DB nicht erst anlegen"


def test_config_set_unknown_key_returns_exit_three(_isolated_db: Path) -> None:
    """Unbekannter Key → Exit 3 (ungültige Eingabe)."""

    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["config", "set", "nonsense", "x"])
    assert result.exit_code == EXIT_INVALID_INPUT


# ------------------------------------------------------- config get
def test_config_get_round_trip(_isolated_db: Path) -> None:
    runner.invoke(app, ["init"])
    runner.invoke(app, ["config", "set", "name", "Samuel Fleig"])
    result = runner.invoke(app, ["config", "get", "name"])
    assert result.exit_code == 0, result.output
    assert "Samuel Fleig" in result.output


def test_config_get_returns_json(_isolated_db: Path) -> None:
    runner.invoke(app, ["init"])
    runner.invoke(app, ["config", "set", "iban", "DE-test"])
    result = runner.invoke(app, ["config", "get", "iban", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload == {"key": "iban", "value": "DE-test"}


def test_config_get_unset_is_exit_zero(_isolated_db: Path) -> None:
    """Nicht gesetzter Key ist KEIN Fehler (Exit 0, Hinweis)."""

    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["config", "get", "name"])
    assert result.exit_code == 0
    assert "nicht gesetzt" in result.output


def test_config_get_before_init_returns_exit_two(_isolated_db: Path) -> None:
    result = runner.invoke(app, ["config", "get", "name"])
    assert result.exit_code == EXIT_NOT_INITIALIZED


def test_config_get_unknown_key_returns_exit_three(_isolated_db: Path) -> None:
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["config", "get", "nonsense"])
    assert result.exit_code == EXIT_INVALID_INPUT


# ------------------------------------------------------- config list
def test_config_list_shows_table(_isolated_db: Path) -> None:
    runner.invoke(app, ["init"])
    runner.invoke(app, ["config", "set", "name", "Samuel"])
    runner.invoke(app, ["config", "set", "iban", "DE-1"])
    result = runner.invoke(app, ["config", "list"])
    assert result.exit_code == 0, result.output
    assert "name" in result.output
    assert "iban" in result.output


def test_config_list_json_keys_sorted(_isolated_db: Path) -> None:
    runner.invoke(app, ["init"])
    runner.invoke(app, ["config", "set", "z_last", "Z"])
    runner.invoke(app, ["config", "set", "a_first", "A"])
    runner.invoke(app, ["config", "set", "ticket_price_cents", "4900"])
    result = runner.invoke(app, ["config", "list", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    keys = list(payload.keys())
    assert keys == sorted(keys)
    # ticket_price_cents → int (Cent-Form).
    assert payload["ticket_price_cents"] == 4900
    assert isinstance(payload["ticket_price_cents"], int)


def test_config_list_empty_after_init_hint(_isolated_db: Path) -> None:
    """Frische DB → leere Liste, Exit 0, kleiner Beispiel-Hinweis."""

    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["config", "list"])
    assert result.exit_code == 0
    assert "Beispiel" in result.output or "set" in result.output


def test_config_list_before_init_returns_exit_two(_isolated_db: Path) -> None:
    result = runner.invoke(app, ["config", "list"])
    assert result.exit_code == EXIT_NOT_INITIALIZED
