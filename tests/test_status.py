"""Unit-Tests für den Status-Workflow (F7)."""

from __future__ import annotations

import sqlite3

import pytest

from fahrgastrechte_core import cases as fc_cases
from fahrgastrechte_core import db
from fahrgastrechte_core import status as fc_status
from fahrgastrechte_core.status import InvalidStatusTransitionError


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    path = db.init_db(tmp_path / "konto.db")
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def _add(conn: sqlite3.Connection) -> int:
    result = fc_cases.add_case(
        conn,
        fc_cases.AddCaseInput(
            service_date="2026-10-12",
            train_number="1",
            start_eva=8000096,
            dest_eva=8000105,
            ticket_type="d_ticket",
            reason_text="test",
            expected_cents=150,
        ),
    )
    return result.case_id


def test_update_status_offen_to_eingereicht(conn: sqlite3.Connection) -> None:
    case_id = _add(conn)
    payload = fc_status.update_status(conn, case_id, "eingereicht")
    assert payload["current_status"] == "eingereicht"


def test_update_status_rejects_offen_to_abgelehnt(conn: sqlite3.Connection) -> None:
    case_id = _add(conn)
    with pytest.raises(InvalidStatusTransitionError):
        fc_status.update_status(conn, case_id, "abgelehnt", note="nein")
