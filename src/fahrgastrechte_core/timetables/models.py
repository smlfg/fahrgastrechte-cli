"""Geparste Datenmodelle für die DB-Timetables-Antworten (ANFORDERUNGEN §7).

Die Pydantic-Modelle hier spiegeln die *vom Connector aufbereiteten*
Daten, nicht die rohe IRIS-XML-Struktur. Aus dem IRIS-Format mit
Einzelbuchstaben-Attributen (``pt``, ``ct``, ``pp``, ``cp``, ``ps`` …)
werden sprechende, typsichere Felder — inklusive

* ISO-8601-Zeitstempel (``Europe/Berlin`` unter ANFORDERUNGEN §9 N4),
* berechneter ``delay_min`` (PT → CT in Minuten, sonst 0/None),
* Erkennung von Stornierungen (``status == 'c'`` → ``None`` für die Zeit).

**Wichtig (N3):** Ist ``ct`` nicht vorhanden und kein Status-``c``
gesetzt, gilt der Zug als pünktlich (``delay_min = 0``) und
``actual_time = planned_time``. Sind *beide* Attribute fehlend, wird
**keine** Zeit erfunden: ``planned_time`` und ``actual_time`` bleiben
``None``, ``delay_min`` wird ``None``. Die Rekonstruktion in einem
späteren Schritt (Schritt 4) muss daraus ``data_quality = best_effort``
ableiten, nicht ``verified``.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
from enum import StrEnum
from typing import Any, Iterable
from xml.etree import ElementTree as ET

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .errors import ParserError

# IRIS liefert lokale Berliner Wandzeit ohne Offset; wir taggen sie direkt mit
# Europe/Berlin (ANFORDERUNGEN §9 N4).
BERLIN_TZ = ZoneInfo("Europe/Berlin")


class RealtimeKind(StrEnum):
    """Welche Realtime-Quelle der Eintrag kommt aus (``rchg`` oder ``fchg``)."""

    RCHG = "rchg"
    FCHG = "fchg"


# ---------------------------------------------------------- Trip-Label
class TripLabel(BaseModel):
    """IRIS ``tl``-Element: ``f``, ``t``, ``o``, ``n``, ``c`` Attribute."""

    model_config = ConfigDict(from_attributes=True)

    filter: str | None = Field(default=None, description="IRIS tl f (Filter-Flags)")
    trip_type: str | None = Field(default=None, description="IRIS tl t")
    owner: str | None = Field(default=None, description="IRIS tl o (EVU-Kürzel)")
    number: str | None = Field(default=None, description="IRIS tl n (Zugnummer)")
    category: str | None = Field(default=None, description="IRIS tl c (RE/RB/ICE …)")


# ---------------------------------------------------------- Stop-Event
class StopEvent(BaseModel):
    """Ein ``<ar>`` oder ``<dp>``-Element eines Stops.

    * ``planned_time`` / ``actual_time`` sind ``datetime`` mit Zeitzone.
    * ``delay_min`` ist die berechnete Differenz (``actual - planned``),
      ``None`` wenn eine der Zeitangaben fehlt, ``0`` wenn der Zug
      pünktlich ist (``ct`` nicht gesetzt, kein ``cs='c'``).
    """

    model_config = ConfigDict(from_attributes=True)

    event_type: str = Field(description="'ar' oder 'dp'")
    planned_time: datetime | None = None
    actual_time: datetime | None = None
    delay_min: int | None = None
    planned_platform: str | None = None
    changed_platform: str | None = None
    line: str | None = None
    cancelled: bool = False
    status: str | None = None  # 'p' (planned), 'a' (added), 'c' (cancelled)


# ---------------------------------------------------------- Parsed-Stop
class ParsedStop(BaseModel):
    """Ein ``<s>`` aus einem IRIS-Timetable-Dokument."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    eva: int = Field(ge=1_000_000, le=9_999_999)
    arrival: StopEvent | None = None
    departure: StopEvent | None = None
    trip_label: TripLabel | None = None
    messages: list[str] = Field(default_factory=list)


# ---------------------------------------------------------- Station
class ParsedStation(BaseModel):
    """Ein ``<station>`` aus der Station-Suche (multipleStationData)."""

    model_config = ConfigDict(from_attributes=True, str_strip_whitespace=True)

    eva_no: int = Field(ge=1_000_000, le=9_999_999)
    name: str
    ds100: str | None = None
    lat: float | None = None
    lon: float | None = None

    @field_validator("name")
    @classmethod
    def _name_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name darf nicht leer sein")
        return value


# ---------------------------------------------------------- Plan / Realtime
class PlanEntry(BaseModel):
    """Ein Stop aus ``/plan/{evaNo}/{date}/{hour}`` — Soll-Fahrplan.

    In ``/plan`` ist immer nur ``pt``/``pp``/``ps``/``ppth`` gesetzt
    (nie ``ct`` etc.); ``actual_time`` ist daher identisch mit
    ``planned_time``. Diese Klasse ist semantisch identisch mit
    :class:`RealtimeChange`, nur mit fixer ``kind = PLAN``.
    """

    model_config = ConfigDict(from_attributes=True)

    station_eva: int | None = None
    station_name: str | None = None
    hour: int | None = None
    stops: list[ParsedStop] = Field(default_factory=list)


class RealtimeChange(BaseModel):
    """Ein Stop aus ``/rchg/{evaNo}`` oder ``/fchg/{evaNo}``."""

    model_config = ConfigDict(from_attributes=True)

    kind: RealtimeKind
    station_eva: int | None = None
    station_name: str | None = None
    stops: list[ParsedStop] = Field(default_factory=list)


# ============================================================== Helpers
def _strip_ns(tag: str) -> str:
    """Entfernt XML-Namespace-Prefix (``{ns}foo`` → ``foo``)."""
    if "}" in tag and tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


def _attr(elem: ET.Element, name: str) -> str | None:
    """Holt ein Attribut, ignoriert Namespace auf dem Namen."""
    return elem.attrib.get(name) or elem.attrib.get(_strip_ns(name))


def _children_by_localname(elem: ET.Element, name: str) -> list[ET.Element]:
    """Sammelt direkte Kinder mit lokalem Elementname ``name``."""
    target = _strip_ns(name)
    out: list[ET.Element] = []
    for child in list(elem):
        if _strip_ns(child.tag) == target:
            out.append(child)
    return out


def parse_yymmddhhmm(value: str) -> datetime:
    """IRIS-Zeitformat ``YYMMDDHHmm`` (z. B. ``2610131430``).

    Die Timetables-API gibt lokale Berliner Wandzeit ohne Offset aus;
    wir setzen ``tzinfo=Europe/Berlin`` (ANFORDERUNGEN §9 N4).
    """

    if not value or len(value) != 10 or not value.isdigit():
        raise ParserError(
            f"Ungültiges IRIS-Zeitformat: {value!r} (erwartet YYMMDDHHmm)"
        )
    yy, mm, dd, hh, mn = (
        int(value[0:2]),
        int(value[2:4]),
        int(value[4:6]),
        int(value[6:8]),
        int(value[8:10]),
    )
    # 2-stellige Jahreszahl: DB-Konvention ist 20YY.
    year = 2000 + yy
    try:
        return datetime(year, mm, dd, hh, mn, tzinfo=BERLIN_TZ)
    except ValueError as exc:
        raise ParserError(f"Ungültiges IRIS-Datum/Zeit: {value!r} ({exc})") from exc


def _compute_delay(planned: datetime | None, actual: datetime | None) -> int | None:
    """Delay in Minuten zwischen Soll und Ist.

    Gibt ``None`` zurück, wenn eine der Zeiten fehlt — N3 ("keine erfundenen Zeiten").
    """

    if planned is None or actual is None:
        return None
    delta = (actual - planned).total_seconds() / 60
    return int(round(delta))


def _parse_event(elem: ET.Element, event_type: str) -> StopEvent:
    """Parst ein ``<ar>`` / ``<dp>`` Element."""

    pt_raw = _attr(elem, "pt")
    ct_raw = _attr(elem, "ct")
    ps = _attr(elem, "ps")
    cs = _attr(elem, "cs")
    pp = _attr(elem, "pp")
    cp = _attr(elem, "cp")
    line = _attr(elem, "l")

    planned_time: datetime | None = parse_yymmddhhmm(pt_raw) if pt_raw else None
    raw_changed: datetime | None = parse_yymmddhhmm(ct_raw) if ct_raw else None

    cancelled = (cs == "c") or (ps == "c")
    status: str | None = cs or ps

    # IRIS-Konvention: ist ``ct`` nicht gesetzt und nicht storniert,
    # ist der Zug pünktlich → actual == planned, delay == 0.
    if not cancelled:
        actual_time: datetime | None = raw_changed or planned_time
    else:
        # Stornierung: kein verlässlicher Ist-Wert → N3.
        actual_time = None

    delay = _compute_delay(planned_time, actual_time)

    return StopEvent(
        event_type=event_type,
        planned_time=planned_time,
        actual_time=actual_time,
        delay_min=delay,
        planned_platform=pp,
        changed_platform=cp,
        line=line,
        cancelled=cancelled,
        status=status,
    )


def _parse_trip_label(elem: ET.Element | None) -> TripLabel | None:
    if elem is None:
        return None
    return TripLabel(
        filter=_attr(elem, "f"),
        trip_type=_attr(elem, "t"),
        owner=_attr(elem, "o"),
        number=_attr(elem, "n"),
        category=_attr(elem, "c"),
    )


def _parse_message_texts(elem: ET.Element) -> list[str]:
    """Sammelt externe Message-Texte (``<m ext="…">``) aus einem Event/Stop."""

    texts: list[str] = []
    for msg in _children_by_localname(elem, "m"):
        # IRIS: ``m`` ist ein Element *oder* eine Liste von ``m``-Kindern
        # unter ``m`` (OpenAPI listet beide Optionen).
        if list(msg):  # Container mit eigenem ``m``-Kind
            for child in _children_by_localname(msg, "m"):
                text = _attr(child, "ext") or _attr(child, "int")
                if text:
                    texts.append(text)
        else:
            text = _attr(msg, "ext") or _attr(msg, "int")
            if text:
                texts.append(text)
    return texts


def _parse_stop(elem: ET.Element) -> ParsedStop:
    """Parst ein ``<s>``-Stopelement."""

    sid = _attr(elem, "id")
    eva_raw = _attr(elem, "eva")
    if not sid or not eva_raw:
        raise ParserError("TimetableStop ohne id/eva")
    eva = int(eva_raw)

    arrival_elem = _children_by_localname(elem, "ar")
    departure_elem = _children_by_localname(elem, "dp")

    arrival = _parse_event(arrival_elem[0], "ar") if arrival_elem else None
    departure = _parse_event(departure_elem[0], "dp") if departure_elem else None

    tl_elems = _children_by_localname(elem, "tl")
    trip_label = _parse_trip_label(tl_elems[0]) if tl_elems else None

    messages: list[str] = []
    if arrival is not None:
        messages.extend(_parse_message_texts(arrival_elem[0]))
    if departure is not None:
        messages.extend(_parse_message_texts(departure_elem[0]))
    messages.extend(_parse_message_texts(elem))

    return ParsedStop(
        id=sid,
        eva=eva,
        arrival=arrival,
        departure=departure,
        trip_label=trip_label,
        messages=messages,
    )


# -------------------------------------------------------- public parsers
def parse_stations(payload: bytes | str) -> list[ParsedStation]:
    """Parst Antwort von ``GET /station/{pattern}``.

    Die IRIS-OpenAPI kennt einen ``<stationList>``-Wrapper mit
    ``<station>``-Items. Wir akzeptieren zusätzlich ``<stations>``
    (Plural) und direkte ``<station>``-Kinder des Wurzelelements, um
    auch Marketplace-spezifische Wrapper-Varianten robust zu schlucken.
    """

    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ParserError(f"XML nicht parsbar: {exc}", payload=payload) from exc

    # Versuche mehrere mögliche Wrapper-Formen.
    candidate_lists: list[ET.Element] = [root]
    for tagname in ("stationList", "stations", "Stations", "StationsList"):
        found = _children_by_localname(root, tagname)
        if found:
            candidate_lists = list(found)
            break
    # Falls root selbst keine Liste ist: fallback = root.
    if candidate_lists == [root]:
        stations = _children_by_localname(root, "station")
    else:
        stations = _children_by_localname(candidate_lists[0], "station")

    out: list[ParsedStation] = []
    for s in stations:
        eva_raw = _attr(s, "eva")
        name = _attr(s, "name")
        if not eva_raw or not name:
            # Schlecht geformte Items werden übersprungen statt zu erfinden.
            continue
        ds100 = _attr(s, "ds100")
        lat_raw = _attr(s, "lat") or _attr(s, "latitude")
        lon_raw = _attr(s, "lon") or _attr(s, "lng") or _attr(s, "longitude")
        try:
            lat = float(lat_raw) if lat_raw else None
            lon = float(lon_raw) if lon_raw else None
        except ValueError:
            lat = lon = None
        out.append(
            ParsedStation(
                eva_no=int(eva_raw),
                name=name,
                ds100=ds100,
                lat=lat,
                lon=lon,
            )
        )
    return out


def _parse_timetable_root(payload: bytes | str) -> ET.Element:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ParserError(f"Timetable-XML nicht parsbar: {exc}", payload=payload) from exc
    local = _strip_ns(root.tag).lower()
    if local not in {"timetable", "timetables"}:
        # Manche Wrapper liefern ``<Timetable>`` als Wurzelelement
        # mit Namespace — wir versuchen zuerst ein ``<s>``-Kind.
        if not _children_by_localname(root, "s"):
            raise ParserError(
                f"Unerwarteter Wurzelknoten: {_strip_ns(root.tag)!r} "
                "(erwartet 'Timetable')",
                payload=payload,
            )
    return root


def _parse_timetable_stops(root: ET.Element) -> list[ParsedStop]:
    """Sammelt ``<s>``-Stops aus einem Timetable-Wurzelelement."""

    stops: list[ParsedStop] = []
    errors: list[Exception] = []
    for s in _children_by_localname(root, "s"):
        try:
            stops.append(_parse_stop(s))
        except ParserError as exc:
            # Einzelne kaputte Stops nicht die ganze Antwort kippen —
            # sondern am Ende gesammelt berichten, wenn gar nichts übrig bleibt.
            errors.append(exc)
    if not stops and errors:
        raise ParserError(
            "Keine verwertbaren Stops im Timetable",
            payload=None,
            original=errors[0],
        )
    return stops


def parse_plan(payload: bytes | str, *, hour: int | None = None) -> PlanEntry:
    """Parst Antwort von ``GET /plan/{evaNo}/{date}/{hour}``."""

    root = _parse_timetable_root(payload)
    station_eva = _attr(root, "eva")
    station_name = _attr(root, "station")
    stops = _parse_timetable_stops(root)
    return PlanEntry(
        station_eva=int(station_eva) if station_eva else None,
        station_name=station_name,
        hour=hour,
        stops=stops,
    )


def parse_realtime(
    payload: bytes | str, *, kind: RealtimeKind = RealtimeKind.RCHG
) -> RealtimeChange:
    """Parst Antwort von ``GET /rchg/{evaNo}`` (oder ``/fchg/{evaNo}``)."""

    root = _parse_timetable_root(payload)
    station_eva = _attr(root, "eva")
    station_name = _attr(root, "station")
    stops = _parse_timetable_stops(root)
    return RealtimeChange(
        kind=kind,
        station_eva=int(station_eva) if station_eva else None,
        station_name=station_name,
        stops=stops,
    )


def stops_with_arrival_delay(stops: Iterable[ParsedStop]) -> list[ParsedStop]:
    """Komfort-Helper: nur Stops, deren Ankunfts-Delay bekannt ist (``pt``+``ct``)."""

    return [s for s in stops if s.arrival is not None and s.arrival.delay_min is not None]


__all__ = [
    "BERLIN_TZ",
    "RealtimeKind",
    "TripLabel",
    "StopEvent",
    "ParsedStop",
    "ParsedStation",
    "PlanEntry",
    "RealtimeChange",
    "parse_yymmddhhmm",
    "parse_stations",
    "parse_plan",
    "parse_realtime",
    "stops_with_arrival_delay",
]
