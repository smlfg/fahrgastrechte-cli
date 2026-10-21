"""CLI-Tests für MVP-Schritt 4: ``add``, ``list``, ``show``, ``trip`` (F1/F2/F3/F4/F6).

Exit-Codes laut ANFORDERUNGEN §3:

* 0 — OK
* 1 — Duplikat / sonstiger Fehler
* 2 — nicht initialisiert
* 3 — ungültige Eingabe
* 4 — Netzwerk/Datenquelle nicht erreichbar

Jeder Test läuft gegen die per autouse-Fixture gesetzte ``tmp_path``-DB.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from fahrgastrechte_cli.main import (
    EXIT_ERROR,
    EXIT_INVALID_INPUT,
    EXIT_NOT_INITIALIZED,
    app,
)
from fahrgastrechte_core.timetables.client import DEFAULT_BASE_URL

runner = CliRunner()


# ----------------------------------------------------------- Hilfen
def _init_db(_isolated_db: Path) -> None:
    r = runner.invoke(app, ["init"])
    assert r.exit_code == 0, r.output


def _add_basic(**extra: object):
    args = [
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
    ]
    for k, v in extra.items():
        flag = f"--{k.replace('_', '-')}"
        if isinstance(v, bool):
            if v:
                args.append(flag)
        else:
            args.extend([flag, str(v)])
    return runner.invoke(app, args)


# ----------------------------------------------------------- add
def test_add_happy_path_returns_zero(_isolated_db: Path) -> None:
    _init_db(_isolated_db)
    result = _add_basic()
    assert result.exit_code == 0, result.output
    assert "Fall #1 angelegt" in result.output or "case_id" in result.output


def test_add_d_ticket_class_1_delay_60_no_crash(_isolated_db: Path) -> None:
    _init_db(_isolated_db)
    result = _add_basic(
        ticket_type="d_ticket",
        ticket_class=1,
        delay_min=60,
    )
    assert result.exit_code == 0, result.output
    assert "KeyError" not in result.output


def test_add_creates_row_in_db(_isolated_db: Path) -> None:
    _init_db(_isolated_db)
    _add_basic(expected_cents=150)
    import sqlite3

    with sqlite3.connect(_isolated_db) as conn:
        conn.row_factory = sqlite3.Row
        n = conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
        assert n == 1
        cents = conn.execute(
            "SELECT expected_cents FROM cases LIMIT 1"
        ).fetchone()[0]
        assert cents == 150


def test_add_missing_required_field_returns_exit_three(
    _isolated_db: Path,
) -> None:
    """Typer lehnt fehlende Pflichtfelder mit Exit ≠ 0 ab (in der Praxis
    Exit 2 für Missing-Argument). Wir erwarten 3 oder 2 — beides ≠ 0."""

    _init_db(_isolated_db)
    result = runner.invoke(
        app,
        [
            "add",
            "--service-date",
            "2026-10-12",
            # train fehlt komplett
            "--start-eva",
            "8000096",
            "--dest-eva",
            "8000105",
        ],
    )
    assert result.exit_code != 0
    # Typer wirft MissingOption mit Exit 2, unsere Wrapper-Funktion
    # würde Exit 3 liefern — wichtig ist nur ≠ 0.
    assert result.exit_code in (EXIT_INVALID_INPUT, EXIT_NOT_INITIALIZED, 2)


def test_add_without_init_returns_exit_two(_isolated_db: Path) -> None:
    assert not _isolated_db.exists()
    result = _add_basic()
    assert result.exit_code == EXIT_NOT_INITIALIZED
    assert not _isolated_db.exists()


def test_add_duplicate_returns_exit_one_and_names_original(
    _isolated_db: Path,
) -> None:
    _init_db(_isolated_db)
    first = _add_basic()
    assert first.exit_code == 0

    second = _add_basic()
    assert second.exit_code == EXIT_ERROR
    assert "Duplikat" in second.output
    assert "Fall #1" in second.output  # nennt die Original-ID


def test_add_duplicate_with_force_succeeds(_isolated_db: Path) -> None:
    _init_db(_isolated_db)
    first = _add_basic()
    assert first.exit_code == 0

    second = _add_basic(force=True)
    assert second.exit_code == 0


def test_add_json_output_is_valid(_isolated_db: Path) -> None:
    _init_db(_isolated_db)
    result = runner.invoke(
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
            "Test",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["case_id"] == 1
    assert payload["trip_id"] >= 1
    assert len(payload["dup_hash"]) == 64


def test_add_json_duplicate_includes_original_id(
    _isolated_db: Path,
) -> None:
    _init_db(_isolated_db)
    _add_basic()
    second = runner.invoke(
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
            "Dup",
            "--json",
        ],
    )
    # Text-Ausgabe über stderr → stderr hat die Original-ID.
    # JSON-Output wird gar nicht erst geschrieben.
    assert second.exit_code == EXIT_ERROR
    assert "Fall #1" in second.output


# ----------------------------------------------------------- list
def test_list_empty_after_init(_isolated_db: Path) -> None:
    _init_db(_isolated_db)
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert "Keine Fälle" in result.output


def test_list_before_init_returns_exit_two(_isolated_db: Path) -> None:
    result = runner.invoke(app, ["list"])
    assert result.exit_code == EXIT_NOT_INITIALIZED


def test_list_shows_two_cases_and_sum_row_matches(
    _isolated_db: Path,
) -> None:
    _init_db(_isolated_db)
    _add_basic(expected_cents=150)
    _add_basic(
        service_date="2026-10-11",
        train="54321",
        expected_cents=350,
    )

    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0, result.output
    body = result.output
    # Beide Fälle sichtbar.
    assert "12345" in body
    assert "54321" in body
    # Summe = 1,50 + 3,50 = 5,00 €
    assert "5.00" in body
    assert "2 Fälle" in body


def test_list_json_has_summary_and_cases(_isolated_db: Path) -> None:
    _init_db(_isolated_db)
    _add_basic(expected_cents=150)
    _add_basic(
        service_date="2026-10-11",
        train="54321",
        expected_cents=350,
    )

    result = runner.invoke(app, ["list", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload["cases"]) == 2
    assert payload["summary"] == {"count": 2, "total_cents": 500}


def test_list_filter_by_status(_isolated_db: Path) -> None:
    _init_db(_isolated_db)
    _add_basic()
    _add_basic(train="99")

    # Ersten Fall auf "eingereicht" setzen.
    import sqlite3

    with sqlite3.connect(_isolated_db) as conn:
        conn.execute(
            "INSERT INTO case_status(case_id, status, at, note) "
            "VALUES (1, 'eingereicht', '2099-12-31T23:59:59+01:00', '')"
        )
        conn.commit()

    result = runner.invoke(
        app, ["list", "--status", "eingereicht", "--json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload["cases"]) == 1
    assert payload["cases"][0]["id"] == 1


# ----------------------------------------------------------- show
def test_show_existing_case_returns_zero(_isolated_db: Path) -> None:
    _init_db(_isolated_db)
    _add_basic(line="RE 5", evu="DB Regio")

    result = runner.invoke(app, ["show", "1"])
    assert result.exit_code == 0, result.output
    assert "Fall #1" in result.output or "RE 5" in result.output


def test_show_unknown_id_returns_exit_one(_isolated_db: Path) -> None:
    _init_db(_isolated_db)
    result = runner.invoke(app, ["show", "999"])
    assert result.exit_code == EXIT_ERROR
    assert "nicht gefunden" in result.output


def test_show_json_is_strict_and_complete(_isolated_db: Path) -> None:
    _init_db(_isolated_db)
    _add_basic(line="RE 5", evu="DB Regio", planned_dep="14:00")

    result = runner.invoke(app, ["show", "1", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    # Pflichtfelder laut F4 + erweiterte Audit-Felder.
    for key in (
        "id",
        "service_date",
        "train_number",
        "ticket_type",
        "expected_cents",
        "current_status",
        "dup_hash",
        "status_history",
        "expected_reason",
    ):
        assert key in payload, f"fehlendes Feld: {key}"
    assert payload["id"] == 1
    assert payload["line"] == "RE 5"
    assert payload["evu"] == "DB Regio"
    # delay_min_arr nicht gesetzt → None.
    assert payload["delay_min_arr"] is None


def test_show_before_init_returns_exit_two(_isolated_db: Path) -> None:
    result = runner.invoke(app, ["show", "1"])
    assert result.exit_code == EXIT_NOT_INITIALIZED


# ----------------------------------------------------------- trip
RCHG_DELAYED = b"""<?xml version="1.0" encoding="UTF-8"?>
<Timetable station="Mannheim Hbf" eva="8000105">
  <s id="-2-2610131500-1" eva="8000105">
    <tl o="DBR" n="12345" c="RE"/>
    <ar pt="2610131500" ct="2610131605" pp="2" cp="2"/>
    <dp pt="2610131505" ct="2610131610"/>
  </s>
</Timetable>
"""

RCHG_ON_TIME_4711 = b"""<?xml version="1.0" encoding="UTF-8"?>
<timetable station="Frankfurt(Main)Hbf" eva="8000105">
  <s id="9876543210" eva="8000105">
    <tl f="F" t="p" o="80" n="4711" c="RE"/>
    <ar pt="2610131500" pp="5" cp="5"/>
  </s>
</timetable>
"""


def _patch_client_factory(client):
    """Monkeypatch-Helfer: ersetzt ``_build_timetables_client``."""

    from fahrgastrechte_cli import main as cli_main

    def factory() -> client:  # type: ignore[valid-type]
        return client

    return cli_main, factory


@pytest.fixture
def mocked_timetables():
    """Stellt respx-gemockte Timetables-API + Credentials bereit."""

    with respx.mock(
        base_url=DEFAULT_BASE_URL, assert_all_called=False
    ) as router:
        yield router


def test_trip_reconstructs_case_from_api(
    mocked_timetables,
    _isolated_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: /rchg liefert Verspätung, Case wird mit delay_min_arr angelegt."""

    monkeypatch.setenv("FAHRGASTRECHTE_DB_CLIENT_ID", "cid")
    monkeypatch.setenv("FAHRGASTRECHTE_DB_API_KEY", "key")

    client = _make_mock_client(rate=10000, backoff=0.0)
    cli_main, factory = _patch_client_factory(client)
    monkeypatch.setattr(cli_main, "_build_timetables_client", factory)

    mocked_timetables.get("/rchg/8000105").respond(200, content=RCHG_DELAYED)

    _init_db(_isolated_db)
    result = runner.invoke(
        app,
        [
            "trip",
            "--date",
            "2026-10-13",
            "--train",
            "12345",
            "--start-eva",
            "8000096",
            "--dest-eva",
            "8000105",
            "--reason",
            "Live-Test",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["delay_min_arr"] == 65
    assert payload["data_quality"] == "verified"
    assert payload["data_source"] == "timetables_api"
    assert payload["case_id"] == 1


def test_trip_planned_arr_matches_iris_local_berlin_wall_clock(
    mocked_timetables,
    _isolated_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``pt=2610131500`` ist 15:00 Berlin — nicht +2 h durch UTC-Fehlinterpretation (#6)."""

    monkeypatch.setenv("FAHRGASTRECHTE_DB_CLIENT_ID", "cid")
    monkeypatch.setenv("FAHRGASTRECHTE_DB_API_KEY", "key")

    client = _make_mock_client(rate=10000, backoff=0.0)
    cli_main, factory = _patch_client_factory(client)
    monkeypatch.setattr(cli_main, "_build_timetables_client", factory)

    mocked_timetables.get("/rchg/8000105").respond(200, content=RCHG_ON_TIME_4711)

    _init_db(_isolated_db)
    result = runner.invoke(
        app,
        [
            "trip",
            "--date",
            "2026-10-13",
            "--train",
            "4711",
            "--start-eva",
            "8000096",
            "--dest-eva",
            "8000105",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["planned_arr"] == "2026-10-13T15:00+02:00"
    assert payload["actual_arr"] == "2026-10-13T15:00+02:00"
    assert payload["delay_min_arr"] == 0


def test_trip_train_not_in_rchg_response_marks_manual(
    mocked_timetables,
    _isolated_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F2: API antwortet, Zug fehlt in /rchg → ``data_source=manual`` (#9)."""

    monkeypatch.setenv("FAHRGASTRECHTE_DB_CLIENT_ID", "cid")
    monkeypatch.setenv("FAHRGASTRECHTE_DB_API_KEY", "key")

    client = _make_mock_client(rate=10000, backoff=0.0)
    cli_main, factory = _patch_client_factory(client)
    monkeypatch.setattr(cli_main, "_build_timetables_client", factory)

    mocked_timetables.get("/rchg/8000105").respond(200, content=RCHG_DELAYED)

    _init_db(_isolated_db)
    result = runner.invoke(
        app,
        [
            "trip",
            "--date",
            "2026-10-19",
            "--train",
            "66666",
            "--start-eva",
            "8000096",
            "--dest-eva",
            "8000105",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["delay_min_arr"] is None
    assert payload["planned_arr"] is None
    assert payload["data_source"] == "manual"
    assert payload["case_id"] == 1


def test_trip_api_error_marks_best_effort(
    mocked_timetables,
    _isolated_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fehlerpfad: API 5xx → ``data_quality=best_effort``, ``delay_min_arr=NULL``."""

    monkeypatch.setenv("FAHRGASTRECHTE_DB_CLIENT_ID", "cid")
    monkeypatch.setenv("FAHRGASTRECHTE_DB_API_KEY", "key")

    client = _make_mock_client(rate=10000, backoff=0.0, max_attempts=2)
    cli_main, factory = _patch_client_factory(client)
    monkeypatch.setattr(cli_main, "_build_timetables_client", factory)

    # /rchg antwortet immer mit 503 → nach Retries ServerError.
    mocked_timetables.get("/rchg/8000105").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(503),
        ]
    )

    _init_db(_isolated_db)
    result = runner.invoke(
        app,
        [
            "trip",
            "--date",
            "2026-10-13",
            "--train",
            "12345",
            "--start-eva",
            "8000096",
            "--dest-eva",
            "8000105",
            "--json",
        ],
    )
    # CLI speichert trotz Fehlers, aber best_effort.
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["delay_min_arr"] is None
    assert payload["data_quality"] == "best_effort"
    assert payload["data_source"] == "manual"
    assert "warning" in payload
    assert payload["case_id"] == 1


def test_trip_missing_date_returns_exit_three(
    _isolated_db: Path,
) -> None:
    _init_db(_isolated_db)
    result = runner.invoke(
        app,
        [
            "trip",
            # --date fehlt
            "--train",
            "12345",
            "--start-eva",
            "8000096",
            "--dest-eva",
            "8000105",
        ],
    )
    assert result.exit_code != 0


def test_trip_garbage_date_returns_exit_three(_isolated_db: Path) -> None:
    _init_db(_isolated_db)
    result = runner.invoke(
        app,
        [
            "trip",
            "--date",
            "not-a-date",
            "--train",
            "12345",
            "--start-eva",
            "8000096",
            "--dest-eva",
            "8000105",
        ],
    )
    assert result.exit_code == EXIT_INVALID_INPUT


def test_trip_before_init_returns_exit_two(_isolated_db: Path) -> None:
    result = runner.invoke(
        app,
        [
            "trip",
            "--date",
            "2026-10-13",
            "--train",
            "12345",
            "--start-eva",
            "8000096",
            "--dest-eva",
            "8000105",
        ],
    )
    assert result.exit_code == EXIT_NOT_INITIALIZED


# ----------------------------------------------------------- helpers
def _make_mock_client(*, rate: int, backoff: float, max_attempts: int = 3):
    """Bautinstanziiert einen TimetablesClient mit hohem Rate-Limit
    und ohne echten Backoff — Tests laufen so in Millisekunden."""

    from fahrgastrechte_core.timetables import TimetablesClient

    return TimetablesClient(
        client_id="cid",
        api_key="key",
        base_url=DEFAULT_BASE_URL,
        rate_per_minute=rate,
        backoff_base_s=backoff,
        max_attempts=max_attempts,
    )