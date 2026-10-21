"""Tests für fahrgastrechte_core.cases (ANFORDERUNGEN §3 F1/F3/F4/F6, Schritt 4).

Jeder Test läuft gegen ``tmp_path`` + die per Fixture gesetzte Env
``FAHRGASTRECHTE_DB`` (siehe ``conftest.py``).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from fahrgastrechte_core import cases as fc_cases, db
from fahrgastrechte_core.models import (
    DataQuality,
    DataSource,
    TicketType,
)


# ----------------------------------------------------------- Hilfs-Fixture
@pytest.fixture
def fresh_db(tmp_path: Path) -> Path:
    target = tmp_path / "konto.db"
    db.init_db(target)
    return target


@pytest.fixture
def opened(fresh_db: Path):
    with db.connect(fresh_db) as conn:
        yield conn


def _basic_input(**overrides) -> fc_cases.AddCaseInput:
    """Standard-Inputs für ein neues ``AddCaseInput``."""

    defaults: dict = {
        "service_date": "2026-10-12",
        "train_number": "12345",
        "start_eva": 8000096,
        "dest_eva": 8000105,
        "ticket_type": TicketType.D_TICKET,
        "reason_text": "RE 65 min verspätet",
        "expected_cents": 150,
    }
    defaults.update(overrides)
    return fc_cases.AddCaseInput(**defaults)


# ----------------------------------------------------------- Duplikat-Hash
def test_dup_hash_is_deterministic_for_same_inputs() -> None:
    h1 = fc_cases.compute_dup_hash(
        service_date="2026-10-12",
        train_number="12345",
        start_eva=8000096,
        dest_eva=8000105,
        planned_dep="14:00",
    )
    h2 = fc_cases.compute_dup_hash(
        service_date="2026-10-12",
        train_number="12345",
        start_eva=8000096,
        dest_eva=8000105,
        planned_dep="14:00",
    )
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex


def test_dup_hash_differs_on_train_change() -> None:
    base = fc_cases.compute_dup_hash(
        service_date="2026-10-12",
        train_number="12345",
        start_eva=8000096,
        dest_eva=8000105,
    )
    other = fc_cases.compute_dup_hash(
        service_date="2026-10-12",
        train_number="99999",
        start_eva=8000096,
        dest_eva=8000105,
    )
    assert base != other


def test_dup_hash_differs_on_dest_change() -> None:
    a = fc_cases.compute_dup_hash(
        service_date="2026-10-12",
        train_number="12345",
        start_eva=8000096,
        dest_eva=8000105,
    )
    b = fc_cases.compute_dup_hash(
        service_date="2026-10-12",
        train_number="12345",
        start_eva=8000096,
        dest_eva=8000103,
    )
    assert a != b


def test_dup_hash_separator_prevents_concat_collision() -> None:
    """``12|345`` vs ``1|2345`` mit gültigen EVAs dürfen nicht kollidieren.

    Wir testen hier die Trennzeichen-Logik (Pipe ``|`` als Separator),
    nicht die EVA-Validierung — beide Aufrufe verwenden absichtlich
    gültige 7-stellige Nummern.
    """

    a = fc_cases.compute_dup_hash(
        service_date="2026-10-12",
        train_number="12345",
        start_eva=1000001,
        dest_eva=1000002,
    )
    b = fc_cases.compute_dup_hash(
        service_date="2026-10-12",
        train_number="12345",
        start_eva=1000012,
        dest_eva=1000345,
    )
    assert a != b


def test_dup_hash_train_number_normalisation_strips_whitespace() -> None:
    h1 = fc_cases.compute_dup_hash(
        service_date="2026-10-12",
        train_number="12345",
        start_eva=8000096,
        dest_eva=8000105,
    )
    h2 = fc_cases.compute_dup_hash(
        service_date="2026-10-12",
        train_number=" 12345 ",
        start_eva=8000096,
        dest_eva=8000105,
    )
    assert h1 == h2


def test_dup_hash_accepts_yyyymmdd_date() -> None:
    h = fc_cases.compute_dup_hash(
        service_date="20261012",
        train_number="12345",
        start_eva=8000096,
        dest_eva=8000105,
    )
    h_iso = fc_cases.compute_dup_hash(
        service_date="2026-10-12",
        train_number="12345",
        start_eva=8000096,
        dest_eva=8000105,
    )
    assert h == h_iso


def test_dup_hash_rejects_invalid_eva() -> None:
    with pytest.raises(ValueError):
        fc_cases.compute_dup_hash(
            service_date="2026-10-12",
            train_number="12345",
            start_eva=123,
            dest_eva=8000105,
        )


def test_dup_hash_rejects_garbage_date() -> None:
    with pytest.raises(ValueError):
        fc_cases.compute_dup_hash(
            service_date="not-a-date",
            train_number="12345",
            start_eva=8000096,
            dest_eva=8000105,
        )


def test_dup_hash_none_planned_dep_is_stable() -> None:
    """Ohne ``planned_dep`` muss derselbe Hash herauskommen."""

    h1 = fc_cases.compute_dup_hash(
        service_date="2026-10-12",
        train_number="12345",
        start_eva=8000096,
        dest_eva=8000105,
        planned_dep=None,
    )
    h2 = fc_cases.compute_dup_hash(
        service_date="2026-10-12",
        train_number="12345",
        start_eva=8000096,
        dest_eva=8000105,
        planned_dep=None,
    )
    assert h1 == h2


# ----------------------------------------------------------- add_case
def test_add_case_creates_trip_and_case(opened: sqlite3.Connection) -> None:
    result = fc_cases.add_case(opened, _basic_input())
    assert result.case_id >= 1
    assert result.trip_id >= 1
    assert len(result.dup_hash) == 64

    # Trip-Zeile existiert mit den richtigen Feldern.
    row = opened.execute(
        "SELECT service_date, train_number, start_eva, dest_eva, "
        "data_source, data_quality FROM trips WHERE id = ?",
        (result.trip_id,),
    ).fetchone()
    assert row["service_date"] == "2026-10-12"
    assert row["train_number"] == "12345"
    assert row["start_eva"] == 8000096
    assert row["dest_eva"] == 8000105
    assert row["data_source"] == "manual"
    assert row["data_quality"] == "manual_only"


def test_add_case_initial_status_is_offen(
    opened: sqlite3.Connection,
) -> None:
    result = fc_cases.add_case(opened, _basic_input())
    row = opened.execute(
        "SELECT status, note FROM case_status WHERE case_id = ? "
        "ORDER BY at DESC LIMIT 1",
        (result.case_id,),
    ).fetchone()
    assert row["status"] == "offen"


def test_add_case_lazily_creates_stations(
    opened: sqlite3.Connection,
) -> None:
    """FK-Constraint: ``trips.start_eva`` referenziert ``stations`` —
    die müssen beim ersten Anlegen implizit da sein."""

    fc_cases.add_case(opened, _basic_input())
    rows = opened.execute(
        "SELECT eva_no, source FROM stations ORDER BY eva_no"
    ).fetchall()
    assert {r["eva_no"] for r in rows} == {8000096, 8000105}


def test_add_case_duplicate_raises_with_original_id(
    opened: sqlite3.Connection,
) -> None:
    first = fc_cases.add_case(opened, _basic_input())
    with pytest.raises(fc_cases.DuplicateCaseError) as exc_info:
        fc_cases.add_case(opened, _basic_input())
    assert exc_info.value.original_id == first.case_id


def test_add_case_duplicate_force_overwrites(opened: sqlite3.Connection) -> None:
    """``force=True`` überschreibt den Original-Fall (gleiche ID, neue Felder)."""

    first = fc_cases.add_case(
        opened, _basic_input(expected_cents=150, reason_text="alt")
    )
    second = fc_cases.add_case(
        opened,
        _basic_input(expected_cents=350, reason_text="neu", force=True),
    )
    assert second.was_duplicate is True
    assert second.original_id == first.case_id
    assert second.case_id == first.case_id  # gleiche ID, Werte überschrieben
    assert second.trip_id == first.trip_id
    # Inhalt wurde wirklich überschrieben.
    payload = fc_cases.show_case(opened, first.case_id)
    assert payload["reason_text"] == "neu"
    assert payload["expected_cents"] == 350


def test_add_case_duplicate_force_does_not_raise(
    opened: sqlite3.Connection,
) -> None:
    fc_cases.add_case(opened, _basic_input())
    # Kein DuplicateCaseError trotz identischer Eingabe.
    fc_cases.add_case(opened, _basic_input(force=True))


def test_add_case_rejects_unknown_ticket_type(
    opened: sqlite3.Connection,
) -> None:
    with pytest.raises(ValueError):
        fc_cases.add_case(opened, _basic_input(ticket_type="nope"))


def test_add_case_rejects_invalid_ticket_class(
    opened: sqlite3.Connection,
) -> None:
    with pytest.raises(ValueError):
        fc_cases.add_case(opened, _basic_input(ticket_class=3))


def test_add_case_rejects_negative_expected_cents(
    opened: sqlite3.Connection,
) -> None:
    with pytest.raises(ValueError):
        fc_cases.add_case(opened, _basic_input(expected_cents=-1))


def test_add_case_rejects_missing_reason(
    opened: sqlite3.Connection,
) -> None:
    with pytest.raises(ValueError):
        fc_cases.add_case(opened, _basic_input(reason_text=""))


def test_add_case_rejects_invalid_eva(opened: sqlite3.Connection) -> None:
    with pytest.raises(ValueError):
        fc_cases.add_case(opened, _basic_input(start_eva=123))


def test_add_case_reuses_trip_for_same_unique_key(
    opened: sqlite3.Connection,
) -> None:
    """Zwei Cases zum selben Trip (ohne Duplikat-Hash) müssen zwei IDs,
    aber eine Trip-ID haben."""

    a = fc_cases.add_case(
        opened,
        _basic_input(reason_text="A", planned_dep="14:00"),
    )
    b = fc_cases.add_case(
        opened,
        _basic_input(
            reason_text="B", planned_dep="15:00", force=True
        ),  # anderer planned_dep → anderer Hash
    )
    assert a.trip_id == b.trip_id
    assert a.case_id != b.case_id


def test_add_case_updates_existing_trip_on_reconstruction(
    opened: sqlite3.Connection,
) -> None:
    """Rekonstruktion muss eine vorhandene manuelle Trip-Zeile überschreiben (#8)."""

    manual = fc_cases.add_case(
        opened,
        _basic_input(service_date="2026-10-15", reason_text="Manuell erfasst"),
    )
    trip_case = fc_cases.add_case(
        opened,
        _basic_input(
            service_date="2026-10-15",
            reason_text="Rekonstruktion",
            planned_dep="2026-10-15T14:00+02:00",
            planned_arr="2026-10-13T16:55+02:00",
            actual_arr="2026-10-13T17:30+02:00",
            delay_min_arr=35,
            data_source=DataSource.TIMETABLES_API,
            data_quality=DataQuality.VERIFIED,
            reconstructed_at="2026-10-21T08:00:00+02:00",
        ),
    )
    assert trip_case.trip_id == manual.trip_id

    shown = fc_cases.show_case(opened, trip_case.case_id)
    assert shown["delay_min_arr"] == 35
    assert shown["data_source"] == "timetables_api"
    assert shown["data_quality"] == "verified"
    assert shown["planned_arr"] == "2026-10-13T16:55+02:00"

    manual_shown = fc_cases.show_case(opened, manual.case_id)
    assert manual_shown["delay_min_arr"] == 35
    assert manual_shown["data_quality"] == "verified"


def test_add_case_manual_does_not_downgrade_verified_trip(
    opened: sqlite3.Connection,
) -> None:
    verified = fc_cases.add_case(
        opened,
        _basic_input(
            planned_dep="2026-10-12T14:00+02:00",
            delay_min_arr=35,
            data_source=DataSource.TIMETABLES_API,
            data_quality=DataQuality.VERIFIED,
        ),
    )
    fc_cases.add_case(
        opened,
        _basic_input(
            reason_text="später manuell",
            planned_dep="2026-10-12T15:00+02:00",
        ),
    )
    row = opened.execute(
        "SELECT delay_min_arr, data_quality FROM trips WHERE id = ?",
        (verified.trip_id,),
    ).fetchone()
    assert row["delay_min_arr"] == 35
    assert row["data_quality"] == "verified"


# ----------------------------------------------------------- list_cases
def test_list_cases_returns_all_when_no_filter(
    opened: sqlite3.Connection,
) -> None:
    fc_cases.add_case(opened, _basic_input(service_date="2026-10-11"))
    fc_cases.add_case(opened, _basic_input(service_date="2026-10-12"))
    out = fc_cases.list_cases(opened)
    assert len(out) == 2
    # Neueste zuerst.
    assert out[0]["service_date"] == "2026-10-12"
    assert out[1]["service_date"] == "2026-10-11"


def test_list_cases_filter_by_status(opened: sqlite3.Connection) -> None:
    fc_cases.add_case(opened, _basic_input())
    # Status auf eingereicht setzen.
    opened.execute(
        "INSERT INTO case_status(case_id, status, at, note) "
        "VALUES ((SELECT id FROM cases LIMIT 1), 'eingereicht', ?, '')",
        ("2099-12-31T23:59:59+01:00",),
    )
    opened.commit()

    fc_cases.add_case(opened, _basic_input(train_number="54321"))
    offen = fc_cases.list_cases(opened, status="offen")
    eingereicht = fc_cases.list_cases(opened, status="eingereicht")
    assert len(eingereicht) == 1
    assert len(offen) == 1


def test_list_cases_filter_by_since(opened: sqlite3.Connection) -> None:
    fc_cases.add_case(opened, _basic_input(service_date="2026-10-01"))
    fc_cases.add_case(opened, _basic_input(service_date="2026-10-12"))
    out = fc_cases.list_cases(opened, since="2026-10-10")
    assert {c["service_date"] for c in out} == {"2026-10-12"}


def test_list_cases_sum_aggregates_expected_cents(
    opened: sqlite3.Connection,
) -> None:
    fc_cases.add_case(opened, _basic_input(expected_cents=150))
    fc_cases.add_case(
        opened, _basic_input(train_number="54321", expected_cents=350)
    )
    cases = fc_cases.list_cases(opened)
    s = fc_cases.sum_cases(cases)
    assert s["count"] == 2
    assert s["total_cents"] == 500
    assert s["total_eur"] == 5.0


# ----------------------------------------------------------- show_case
def test_show_case_returns_full_detail(
    opened: sqlite3.Connection,
) -> None:
    result = fc_cases.add_case(
        opened,
        _basic_input(
            line="RE 5",
            evu="DB Regio",
            category="RE",
            planned_dep="14:00",
        ),
    )
    payload = fc_cases.show_case(opened, result.case_id)
    assert payload["id"] == result.case_id
    assert payload["service_date"] == "2026-10-12"
    assert payload["train_number"] == "12345"
    assert payload["line"] == "RE 5"
    assert payload["evu"] == "DB Regio"
    assert payload["current_status"] == "offen"
    assert isinstance(payload["status_history"], list)
    assert payload["status_history"][-1]["status"] == "offen"
    assert "expected_reason" in payload


def test_show_case_unknown_id_raises(opened: sqlite3.Connection) -> None:
    with pytest.raises(fc_cases.CaseNotFoundError) as exc_info:
        fc_cases.show_case(opened, 999)
    assert exc_info.value.case_id == 999


# ----------------------------------------------------------- UNIQUE-Constraint
def test_db_unique_index_blocks_duplicate_at_sql_level(
    opened: sqlite3.Connection,
) -> None:
    """Selbst ohne den Core-Code darf die DB keinen zweiten Fall mit
    demselben Hash aufnehmen."""

    result = fc_cases.add_case(opened, _basic_input())
    with pytest.raises(sqlite3.IntegrityError):
        # Direkt INSERT umgehen → der UNIQUE-Index muss zuschlagen.
        opened.execute(
            "INSERT INTO cases(trip_id, ticket_type, expected_cents, "
            "dup_hash, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                result.trip_id,
                "d_ticket",
                100,
                result.dup_hash,
                "2026-10-13T00:00:00+02:00",
                "2026-10-13T00:00:00+02:00",
            ),
        )