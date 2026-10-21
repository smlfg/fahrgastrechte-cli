"""Tests für die IRIS-XML-Parser (kein Netzwerk-Zugriff)."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

BERLIN = ZoneInfo("Europe/Berlin")

from fahrgastrechte_core.timetables import (
    ParserError,
    parse_plan,
    parse_realtime,
    parse_stations,
    parse_yymmddhhmm,
    RealtimeKind,
)


# ---------------------------------------------------------- parse_yymmddhhmm
def test_yymmddhhmm_parses_well_formed_value() -> None:
    dt = parse_yymmddhhmm("2610131430")
    assert dt == datetime(2026, 10, 13, 14, 30, tzinfo=timezone.utc)


def test_yymmddhhmm_interprets_iris_wall_clock_as_europe_berlin() -> None:
    """IRIS ``pt``/``ct`` sind lokale Berliner Zeit ohne Offset (Issue #6)."""

    dt = parse_yymmddhhmm("2610131500")
    assert dt == datetime(2026, 10, 13, 15, 0, tzinfo=BERLIN)


@pytest.mark.parametrize("bad", ["", "1", "a" * 10, "261013143X", "26101"])
def test_yymmddhhmm_rejects_garbage(bad: str) -> None:
    with pytest.raises(ParserError):
        parse_yymmddhhmm(bad)


def test_yymmddhhmm_rejects_invalid_date_components() -> None:
    # 13. Monat existiert nicht — Fehler kommt aus datetime-Konstruktor,
    # Parser muss ihn als ParserError weiterreichen.
    with pytest.raises(ParserError):
        parse_yymmddhhmm("2613131430")  # month=13


# ---------------------------------------------------------- parse_stations
STATION_XML = """<?xml version="1.0" encoding="UTF-8"?>
<stationList>
  <station eva="8000096" ds100="TS" name="Stuttgart Hbf" lat="48.783586" lon="9.181370"/>
  <station eva="8000103" ds100="TMO" name="Mannheim Hbf" lat="49.479700" lon="8.470200"/>
  <station eva="8000001" name="Aachen Hbf"/>
</stationList>
"""


def test_parse_stations_basic() -> None:
    stations = parse_stations(STATION_XML)
    assert len(stations) == 3

    s0 = stations[0]
    assert s0.eva_no == 8000096
    assert s0.name == "Stuttgart Hbf"
    assert s0.ds100 == "TS"
    assert s0.lat == pytest.approx(48.783586)
    assert s0.lon == pytest.approx(9.181370)

    # Station ohne Koordinaten → lat/lon sind None (keine Erfindung, N3).
    s2 = stations[2]
    assert s2.eva_no == 8000001
    assert s2.name == "Aachen Hbf"
    assert s2.lat is None and s2.lon is None
    assert s2.ds100 is None


def test_parse_stations_handles_plural_wrapper() -> None:
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<stations>
  <station eva="8098105" name="Frankfurt(Main)Hbf" ds100="FF"/>
</stations>
"""
    sts = parse_stations(xml)
    assert len(sts) == 1
    assert sts[0].name == "Frankfurt(Main)Hbf"
    assert sts[0].ds100 == "FF"


def test_parse_stations_skips_malformed_entries() -> None:
    # Items ohne eva oder ohne name werden stillschweigend übersprungen;
    # ein Crash wäre hier grob fahrlässig (DB hat historisch „kaputte"
    # Station-Einträge für Betriebsstellen).
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<stationList>
  <station name="Eva fehlt"/>
  <station eva="8000096" name="Stuttgart Hbf"/>
</stationList>
"""
    sts = parse_stations(xml)
    assert len(sts) == 1
    assert sts[0].eva_no == 8000096


def test_parse_stations_raises_on_invalid_xml() -> None:
    with pytest.raises(ParserError):
        parse_stations("<stationList><station eva= oops /></stationList>")


# ---------------------------------------------------------- parse_plan
PLAN_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Timetable station="Stuttgart Hbf" eva="8000096">
  <s id="-1234567890-2610131430-1" eva="8000096">
    <tl o="DBR" n="123" c="RE"/>
    <ar pt="2610131430" pp="3" ppth="Heilbronn|Stuttgart Hbf"/>
    <dp pt="2610131435" pp="3" ppth="Stuttgart Hbf|Tuebingen"/>
  </s>
  <s id="-987-2610131500-2" eva="8000096">
    <tl o="DBR" n="987" c="RB"/>
    <ar pt="2610131500" pp="2"/>
  </s>
</Timetable>
"""


def test_parse_plan_root_metadata() -> None:
    plan = parse_plan(PLAN_XML, hour=14)
    assert plan.station_eva == 8000096
    assert plan.station_name == "Stuttgart Hbf"
    assert plan.hour == 14
    assert len(plan.stops) == 2


def test_parse_plan_stop_with_arrival_and_departure() -> None:
    plan = parse_plan(PLAN_XML, hour=14)
    s0 = plan.stops[0]
    assert s0.id == "-1234567890-2610131430-1"
    assert s0.eva == 8000096
    assert s0.trip_label is not None
    assert s0.trip_label.owner == "DBR"
    assert s0.trip_label.number == "123"
    assert s0.trip_label.category == "RE"

    assert s0.arrival is not None
    assert s0.arrival.event_type == "ar"
    assert s0.arrival.planned_time == datetime(
        2026, 10, 13, 14, 30, tzinfo=timezone.utc
    )
    # In /plan gibt es per Definition kein ``ct`` → actual_time == planned_time,
    # delay_min == 0.
    assert s0.arrival.actual_time == s0.arrival.planned_time
    assert s0.arrival.delay_min == 0
    assert s0.arrival.planned_platform == "3"
    assert s0.arrival.cancelled is False

    assert s0.departure is not None
    assert s0.departure.planned_time == datetime(
        2026, 10, 13, 14, 35, tzinfo=timezone.utc
    )


def test_parse_plan_stop_arrival_only() -> None:
    plan = parse_plan(PLAN_XML, hour=14)
    s1 = plan.stops[1]
    assert s1.arrival is not None
    assert s1.departure is None  # Endstation: kein dp-Event
    assert s1.trip_label is not None
    assert s1.trip_label.category == "RB"
    assert s1.trip_label.number == "987"


# ---------------------------------------------------------- parse_realtime
RCHG_ON_TIME = """<?xml version="1.0" encoding="UTF-8"?>
<Timetable station="Stuttgart Hbf" eva="8000096">
  <s id="-1-2610131430-1" eva="8000096">
    <tl o="DBR" n="1" c="ICE"/>
    <ar pt="2610131430" ct="2610131430" pp="5" cp="5"/>
    <dp pt="2610131435" ct="2610131435"/>
  </s>
</Timetable>
"""

RCHG_DELAYED = """<?xml version="1.0" encoding="UTF-8"?>
<Timetable station="Mannheim Hbf" eva="8000103">
  <s id="-2-2610131500-1" eva="8000103">
    <tl o="DBR" n="2" c="RE"/>
    <ar pt="2610131500" ct="2610131605" pp="2" cp="2"/>
    <dp pt="2610131505" ct="2610131610"/>
  </s>
</Timetable>
"""

RCHG_CANCELLED = """<?xml version="1.0" encoding="UTF-8"?>
<Timetable station="Stuttgart Hbf" eva="8000096">
  <s id="-3-2610131500-1" eva="8000096">
    <tl o="DBR" n="3" c="RE"/>
    <ar pt="2610131500" cs="c"/>
    <dp pt="2610131505" cs="c"/>
  </s>
</Timetable>
"""


def test_parse_realtime_on_time_yields_zero_delay() -> None:
    rt = parse_realtime(RCHG_ON_TIME, kind=RealtimeKind.RCHG)
    assert rt.kind == RealtimeKind.RCHG
    assert len(rt.stops) == 1
    arr = rt.stops[0].arrival
    assert arr is not None
    assert arr.planned_time == datetime(2026, 10, 13, 14, 30, tzinfo=timezone.utc)
    assert arr.actual_time == arr.planned_time
    assert arr.delay_min == 0
    assert arr.cancelled is False


def test_parse_realtime_delayed_computes_delay() -> None:
    rt = parse_realtime(RCHG_DELAYED)
    arr = rt.stops[0].arrival
    assert arr is not None
    # 15:00 → 16:05 = 65 Minuten
    assert arr.delay_min == 65
    assert arr.actual_time == datetime(2026, 10, 13, 16, 5, tzinfo=timezone.utc)
    assert arr.planned_time == datetime(2026, 10, 13, 15, 0, tzinfo=timezone.utc)
    assert arr.cancelled is False
    assert arr.changed_platform == "2"

    dep = rt.stops[0].departure
    assert dep is not None
    # 15:05 → 16:10 = 65 Minuten
    assert dep.delay_min == 65


def test_parse_realtime_cancelled_does_not_invent_time() -> None:
    """Bei ``cs='c'`` darf das Parser-Modul keine Ist-Zeit erfinden (N3)."""

    rt = parse_realtime(RCHG_CANCELLED)
    arr = rt.stops[0].arrival
    assert arr is not None
    assert arr.cancelled is True
    # actual_time und delay_min sind None — NICHT pt als Ist ausgeben.
    assert arr.actual_time is None
    assert arr.delay_min is None
    assert arr.planned_time == datetime(2026, 10, 13, 15, 0, tzinfo=timezone.utc)


def test_parse_realtime_fchg_kind_round_trip() -> None:
    rt = parse_realtime(RCHG_DELAYED, kind=RealtimeKind.FCHG)
    assert rt.kind == RealtimeKind.FCHG


def test_parse_realtime_missing_root_raises_parser_error() -> None:
    with pytest.raises(ParserError):
        parse_realtime("<other><foo/></other>")


def test_parse_plan_no_stops_raises_parser_error() -> None:
    """Ein leerer Timetable ist verdächtig genug, um Parser-Error zu sein."""

    with pytest.raises(ParserError):
        parse_plan(
            "<?xml version='1.0'?><Timable station='X' eva='8000096'></Timable>",
        )
