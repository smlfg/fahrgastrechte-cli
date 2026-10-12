"""Tests für die Pydantic-Modelle (ANFORDERUNGEN §4)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from fahrgastrechte_core.models import (
    Case,
    CaseStatusEntry,
    CaseStatusName,
    DataQuality,
    DataSource,
    Station,
    StationSource,
    TicketType,
    Trip,
    TripCategory,
)


# ---------------------------------------------------------- Station
def test_station_minimal_construction() -> None:
    """Stations-Modell akzeptiert Pflichtfelder, alles andere None."""

    s = Station(eva_no=8000096, name="Stuttgart Hbf")
    assert s.eva_no == 8000096
    assert s.ds100 is None
    assert s.lat is None
    assert s.source is StationSource.MANUAL


def test_station_rejects_out_of_range_eva() -> None:
    """7-stellige EVA-Nummer wird erzwungen."""

    with pytest.raises(ValidationError):
        Station(eva_no=123, name="Mini")
    with pytest.raises(ValidationError):
        Station(eva_no=10000000, name="Größer")


def test_station_rejects_empty_name() -> None:
    """Validator: Name darf nicht leer sein."""

    with pytest.raises(ValidationError):
        Station(eva_no=8000096, name="")


# ----------------------------------------------------------------- Trip
def test_trip_minimal_construction() -> None:
    """Trip kommt mit service_date + data_source/-quality durch."""

    t = Trip(service_date="2026-10-12", data_source="manual", data_quality="manual_only")
    assert t.id is None
    assert t.category is None
    assert t.delay_min_arr is None
    assert t.data_source is DataSource.MANUAL
    assert t.data_quality is DataQuality.MANUAL_ONLY
    # Round-Trip über to_dict ist json-tauglich.
    d = t.to_dict()
    assert d["service_date"] == "2026-10-12"
    assert d["delay_min_arr"] is None


def test_trip_category_enum() -> None:
    """TripCategory liest String-Werte aus §4 als Enum."""

    t = Trip(service_date="2026-10-12", category="RE")
    assert t.category is TripCategory.RE


# ------------------------------------------------------------------ Case
def test_case_required_fields() -> None:
    """Case: ticket_type + expected_cents + created/updated_at sind Pflicht."""

    c = Case(
        ticket_type=TicketType.D_TICKET,
        expected_cents=150,
        created_at="2026-10-12T19:05:00+02:00",
        updated_at="2026-10-12T19:05:00+02:00",
    )
    assert c.expected_cents == 150
    assert c.cap_applied is None
    assert c.ticket_type is TicketType.D_TICKET


def test_case_expected_cents_must_be_nonnegative() -> None:
    """Expected-Cents darf nicht negativ sein (sonst ist es kein gültiger Fall)."""

    with pytest.raises(ValidationError):
        Case(
            ticket_type=TicketType.D_TICKET,
            expected_cents=-1,
            created_at="2026-10-12T19:05:00+02:00",
            updated_at="2026-10-12T19:05:00+02:00",
        )


def test_case_ticket_class_must_be_1_or_2() -> None:
    """ticket_class ist 1 oder 2 (Bahn-Klassen)."""

    with pytest.raises(ValidationError):
        Case(
            ticket_type=TicketType.D_TICKET,
            expected_cents=150,
            ticket_class=3,
            created_at="2026-10-12T19:05:00+02:00",
            updated_at="2026-10-12T19:05:00+02:00",
        )


# -------------------------------------------------------- CaseStatusEntry
def test_case_status_entry_enum_status() -> None:
    """Status-Strings aus §4 mappen auf CaseStatusName."""

    e = CaseStatusEntry(case_id=1, status="offen", at="2026-10-12T19:00:00+02:00")
    assert e.status is CaseStatusName.OFFEN
    assert e.to_dict()["status"] == "offen"
