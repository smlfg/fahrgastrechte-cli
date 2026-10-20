"""CLI-Tests für MVP-Schritt 6: ``status``, ``export``, ``station`` (F7/F8/F9)."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import respx
from typer.testing import CliRunner

from fahrgastrechte_cli.main import (
    EXIT_INVALID_INPUT,
    EXIT_NOT_INITIALIZED,
    app,
)
from fahrgastrechte_core.timetables.client import DEFAULT_BASE_URL

runner = CliRunner()

STATION_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<stationList>
  <station eva="8000096" ds100="TS" name="Stuttgart Hbf"/>
</stationList>
"""


def _init_db(_isolated_db: Path) -> None:
    r = runner.invoke(app, ["init"])
    assert r.exit_code == 0, r.output


def _add_case(_isolated_db: Path) -> None:
    _init_db(_isolated_db)
    r = runner.invoke(
        app,
        [
            "add",
            "--service-date",
            "2026-10-12",
            "--train",
            "12345",
            "--start-eva",
            "8000096",
            "--dest-eva",
            "8000105",
            "--reason",
            "RE verspätet",
            "--expected-cents",
            "150",
        ],
    )
    assert r.exit_code == 0, r.output


def test_status_command_exists_and_transitions(_isolated_db: Path) -> None:
    _add_case(_isolated_db)
    result = runner.invoke(app, ["status", "1", "eingereicht"])
    assert result.exit_code == 0, result.output

    show = runner.invoke(app, ["show", "1", "--json"])
    payload = json.loads(show.output)
    assert payload["current_status"] == "eingereicht"


def test_status_invalid_transition_exit_three(_isolated_db: Path) -> None:
    _add_case(_isolated_db)
    result = runner.invoke(app, ["status", "1", "abgelehnt", "--note", "x"])
    assert result.exit_code == EXIT_INVALID_INPUT


def test_status_note_required_for_abgelehnt(_isolated_db: Path) -> None:
    _add_case(_isolated_db)
    runner.invoke(app, ["status", "1", "eingereicht"])
    result = runner.invoke(app, ["status", "1", "abgelehnt"])
    assert result.exit_code == EXIT_INVALID_INPUT


def test_export_csv_writes_rfc4180(_isolated_db: Path, tmp_path: Path) -> None:
    _add_case(_isolated_db)
    out = tmp_path / "faelle.csv"
    result = runner.invoke(
        app,
        ["export", "csv", "--status", "offen", "-o", str(out)],
    )
    assert result.exit_code == 0, result.output
    assert out.stat().st_size > 0
    with out.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert len(rows) >= 2
    assert rows[0][0] == "case_id"


def test_export_pdf_contains_name_and_amount(
    _isolated_db: Path, tmp_path: Path
) -> None:
    _add_case(_isolated_db)
    runner.invoke(app, ["config", "set", "name", "Samuel Fleig"])
    out = tmp_path / "antrag.pdf"
    result = runner.invoke(
        app,
        ["export", "pdf", "--ids", "1", "-o", str(out)],
    )
    assert result.exit_code == 0, result.output
    data = out.read_bytes()
    assert len(data) > 1024
    assert b"Samuel Fleig" in data
    assert b"1,50" in data or b"150" in data


def test_station_search_mocked(
    _isolated_db: Path,
    monkeypatch,
) -> None:
    _init_db(_isolated_db)
    monkeypatch.setenv("FAHRGASTRECHTE_DB_CLIENT_ID", "cid")
    monkeypatch.setenv("FAHRGASTRECHTE_DB_API_KEY", "key")
    with respx.mock(base_url=DEFAULT_BASE_URL) as router:
        router.get("/station/Stuttgart").respond(200, content=STATION_XML)
        result = runner.invoke(app, ["station", "Stuttgart"])
    assert result.exit_code == 0, result.output
    assert "Stuttgart Hbf" in result.output


def test_status_before_init_returns_exit_two(_isolated_db: Path) -> None:
    result = runner.invoke(app, ["status", "1", "eingereicht"])
    assert result.exit_code == EXIT_NOT_INITIALIZED
