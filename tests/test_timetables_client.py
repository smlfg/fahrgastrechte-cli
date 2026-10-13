"""HTTP-Client-Tests für den DB-Timetables-Connector (ANFORDERUNGEN §7, §13/3).

Alle Tests laufen gegen ``respx``-gemockte Endpunkte — kein Live-Netzwerk,
kein echtes ``time.sleep``. Die Retry-Backoff wird in den Tests per
``backoff_base_s=0.0`` neutralisiert; das Rate-Limit pro Minute wird
hochgesetzt, damit serielle Test-Sequenzen keine ungewollten Wartezeiten
auslösen.
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest
import respx

from fahrgastrechte_core.timetables import (
    AuthenticationError,
    NotFoundError,
    ParserError,
    RateLimitError,
    RealtimeKind,
    ServerError,
    TimetablesClient,
    TimetablesError,
    USER_AGENT,
)
from fahrgastrechte_core.timetables.client import (
    DEFAULT_BASE_URL,
    ENV_API_KEY,
    ENV_CLIENT_ID,
)

# ----------------------------------------------------------- XML-Fixtures
STATIONS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<stationList>
  <station eva="8000096" ds100="TS" name="Stuttgart Hbf" lat="48.783586" lon="9.181370"/>
  <station eva="8000103" ds100="TMO" name="Mannheim Hbf"/>
</stationList>
"""

PLAN_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<Timetable station="Stuttgart Hbf" eva="8000096">
  <s id="-1-2610131400-1" eva="8000096">
    <tl o="DBR" n="1" c="ICE"/>
    <ar pt="2610131400" pp="3"/>
    <dp pt="2610131405" pp="3"/>
  </s>
</Timetable>
"""

RCHG_DELAYED_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<Timetable station="Stuttgart Hbf" eva="8000096">
  <s id="-2-2610131500-1" eva="8000096">
    <tl o="DBR" n="2" c="RE"/>
    <ar pt="2610131500" ct="2610131605" pp="2" cp="2"/>
    <dp pt="2610131505" ct="2610131610"/>
  </s>
</Timetable>
"""

CLIENT_ID = "test-client-abc"
API_KEY = "test-api-key-xyz"
EVA = 8000096


# --------------------------------------------------------------- fixtures
@pytest.fixture
def mock_router():
    """respx-Router mit DB-Base-URL; ``assert_all_called=False``, damit
    unbenutzte Routen aus früheren Test-Sessions keinen Fehler werfen."""
    with respx.mock(
        base_url=DEFAULT_BASE_URL, assert_all_called=False
    ) as router:
        yield router


@pytest.fixture
def client(mock_router):
    """TimetablesClient mit Mock-Transport: schnelle Retries, hohes
    Rate-Limit, Base-URL = DB Marketplace."""
    return TimetablesClient(
        client_id=CLIENT_ID,
        api_key=API_KEY,
        base_url=DEFAULT_BASE_URL,
        rate_per_minute=10000,   # Rate-Limit neutralisieren
        backoff_base_s=0.0,      # kein echter Backoff-Sleep
        max_attempts=3,
    )


def _make_client(**overrides) -> TimetablesClient:
    """Freistehende Factory für Tests, die eigene Credentials/Optionen brauchen."""
    defaults: dict = {
        "base_url": DEFAULT_BASE_URL,
        "rate_per_minute": 10000,
        "backoff_base_s": 0.0,
        "max_attempts": 3,
    }
    defaults.update(overrides)
    return TimetablesClient(**defaults)


# =========================================================== search_station
def test_search_station_returns_parsed_list(client, mock_router) -> None:
    route = mock_router.get("/station/Mannheim").respond(
        200, content=STATIONS_XML
    )
    stations = client.search_station("Mannheim")
    assert len(stations) == 2
    assert stations[0].name == "Stuttgart Hbf"
    assert stations[0].eva_no == 8000096
    assert stations[1].name == "Mannheim Hbf"
    assert route.called
    assert route.call_count == 1


def test_search_station_sends_db_auth_headers(client, mock_router) -> None:
    route = mock_router.get("/station/Mannheim").respond(
        200, content=STATIONS_XML
    )
    client.search_station("Mannheim")
    sent = route.calls.last.request
    assert sent.headers["DB-Client-Id"] == CLIENT_ID
    assert sent.headers["DB-Api-Key"] == API_KEY


def test_search_station_sends_user_agent(client, mock_router) -> None:
    route = mock_router.get("/station/Mannheim").respond(
        200, content=STATIONS_XML
    )
    client.search_station("Mannheim")
    sent = route.calls.last.request
    assert sent.headers["User-Agent"] == USER_AGENT


def test_search_station_reads_credentials_from_env(
    monkeypatch, mock_router
) -> None:
    """Werden weder ``client_id``/``api_key`` explizit übergeben, fallen
    sie auf die Env-Variablen ``FAHRGASTRECHTE_DB_CLIENT_ID`` /
    ``FAHRGASTRECHTE_DB_API_KEY`` zurück."""
    monkeypatch.setenv(ENV_CLIENT_ID, "env-client-123")
    monkeypatch.setenv(ENV_API_KEY, "env-key-secret")
    c = _make_client()  # ohne client_id / api_key → Env greift

    route = mock_router.get("/station/Mannheim").respond(
        200, content=STATIONS_XML
    )
    c.search_station("Mannheim")
    sent = route.calls.last.request
    assert sent.headers["DB-Client-Id"] == "env-client-123"
    assert sent.headers["DB-Api-Key"] == "env-key-secret"


def test_search_station_empty_pattern_raises_value_error() -> None:
    c = _make_client(client_id="x", api_key="y")
    with pytest.raises(ValueError):
        c.search_station("")


def test_search_station_missing_credentials_raises_authentication_error(
    monkeypatch, mock_router
) -> None:
    """Ohne Credentials (weder Argumente noch Env) wirft der Client
    :class:`AuthenticationError`, bevor er das Netzwerk berührt."""
    monkeypatch.delenv(ENV_CLIENT_ID, raising=False)
    monkeypatch.delenv(ENV_API_KEY, raising=False)
    c = _make_client()
    assert c.credentials_configured is False

    # Kein respx-Route registriert: würde, falls doch angefragt, einen
    # ``RequestError`` auslösen. Wir erwarten den Auth-Fehler vorher.
    with pytest.raises(AuthenticationError):
        c.search_station("X")


# =================================================================== get_plan
def test_get_plan_returns_parsed_entry(client, mock_router) -> None:
    route = mock_router.get(f"/plan/{EVA}/261013/14").respond(
        200, content=PLAN_XML
    )
    plan = client.get_plan(EVA, date=date(2026, 10, 13), hour=14)
    assert plan.station_eva == EVA
    assert plan.station_name == "Stuttgart Hbf"
    assert plan.hour == 14
    assert len(plan.stops) == 1
    assert plan.stops[0].trip_label is not None
    assert plan.stops[0].trip_label.category == "ICE"
    assert route.called


def test_get_plan_accepts_yyyy_mm_dd_string(client, mock_router) -> None:
    route = mock_router.get(f"/plan/{EVA}/261013/14").respond(
        200, content=PLAN_XML
    )
    plan = client.get_plan(EVA, date="2026-10-13", hour=14)
    assert len(plan.stops) == 1
    assert route.calls.last.request.url.path.endswith("/plan/8000096/261013/14")


def test_get_plan_accepts_yymmdd_string(client, mock_router) -> None:
    route = mock_router.get(f"/plan/{EVA}/261013/14").respond(
        200, content=PLAN_XML
    )
    plan = client.get_plan(EVA, date="261013", hour=14)
    assert len(plan.stops) == 1
    assert route.called


def test_get_plan_invalid_eva_raises_value_error() -> None:
    c = _make_client(client_id="x", api_key="y")
    with pytest.raises(ValueError):
        c.get_plan(123, date="2026-10-13", hour=14)


def test_get_plan_invalid_hour_raises_value_error() -> None:
    c = _make_client(client_id="x", api_key="y")
    with pytest.raises(ValueError):
        c.get_plan(EVA, date="2026-10-13", hour=24)


# ====================================================== get_realtime_changes
def test_get_realtime_changes_returns_rchg(client, mock_router) -> None:
    route = mock_router.get(f"/rchg/{EVA}").respond(
        200, content=RCHG_DELAYED_XML
    )
    rt = client.get_realtime_changes(EVA)
    assert rt.kind == RealtimeKind.RCHG
    assert rt.station_eva == EVA
    assert len(rt.stops) == 1
    arr = rt.stops[0].arrival
    assert arr is not None
    # 15:00 → 16:05 = 65 Minuten
    assert arr.delay_min == 65
    assert arr.cancelled is False
    assert route.called


def test_get_full_changes_uses_fchg_endpoint(client, mock_router) -> None:
    route = mock_router.get(f"/fchg/{EVA}").respond(
        200, content=RCHG_DELAYED_XML
    )
    rt = client.get_full_changes(EVA)
    assert rt.kind == RealtimeKind.FCHG
    assert route.called


def test_get_realtime_changes_explicit_fchg_kind(client, mock_router) -> None:
    route = mock_router.get(f"/fchg/{EVA}").respond(
        200, content=RCHG_DELAYED_XML
    )
    rt = client.get_realtime_changes(EVA, kind=RealtimeKind.FCHG)
    assert rt.kind == RealtimeKind.FCHG
    assert route.called


# =============================================================== 4xx/5xx
def test_401_raises_authentication_error(client, mock_router) -> None:
    mock_router.get("/station/Mannheim").respond(401)
    with pytest.raises(AuthenticationError):
        client.search_station("Mannheim")


def test_403_raises_authentication_error(client, mock_router) -> None:
    mock_router.get(f"/rchg/{EVA}").respond(403)
    with pytest.raises(AuthenticationError):
        client.get_realtime_changes(EVA)


def test_404_raises_not_found_error(client, mock_router) -> None:
    mock_router.get("/station/NoSuchPlace").respond(404)
    with pytest.raises(NotFoundError):
        client.search_station("NoSuchPlace")


def test_400_is_not_retryable_and_raises_timetables_error(
    client, mock_router
) -> None:
    """400 Bad Request ist nicht-retryable: sofort ``TimetablesError``."""
    route = mock_router.get(f"/plan/{EVA}/261013/14").respond(400)
    with pytest.raises(TimetablesError):
        client.get_plan(EVA, date="2026-10-13", hour=14)
    assert route.call_count == 1


# =================================================================== Retries
def test_429_retries_until_exhausted_then_raises_rate_limit_error(
    client, mock_router
) -> None:
    """3× HTTP 429 → nach ``max_attempts`` Versuchen ``RateLimitError``."""
    route = mock_router.get("/station/Mannheim").mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(429),
            httpx.Response(429),
        ]
    )
    with pytest.raises(RateLimitError):
        client.search_station("Mannheim")
    assert route.call_count == 3


def test_429_then_200_eventually_succeeds(client, mock_router) -> None:
    """HTTP 429 mit ``Retry-After: 0`` → Retry liefert 200, Antwort parsen."""
    route = mock_router.get("/station/Mannheim").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(200, content=STATIONS_XML),
        ]
    )
    stations = client.search_station("Mannheim")
    assert len(stations) == 2
    assert route.call_count == 2


def test_5xx_retries_until_exhausted_then_raises_server_error(
    client, mock_router
) -> None:
    """3× HTTP 5xx → ``ServerError`` mit ``status_code`` aus letzter Antwort."""
    route = mock_router.get(f"/rchg/{EVA}").mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(502),
            httpx.Response(503),
        ]
    )
    with pytest.raises(ServerError) as exc_info:
        client.get_realtime_changes(EVA)
    assert exc_info.value.status_code == 503
    assert route.call_count == 3


def test_5xx_then_200_eventually_succeeds(client, mock_router) -> None:
    """Ein einzelner 503, dann 200: Connector parsed die erfolgreiche Antwort."""
    route = mock_router.get(f"/plan/{EVA}/261013/14").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, content=PLAN_XML),
        ]
    )
    plan = client.get_plan(EVA, date="2026-10-13", hour=14)
    assert plan.station_eva == EVA
    assert route.call_count == 2


# ====================================================== response parsing
def test_200_with_invalid_xml_raises_parser_error(
    client, mock_router
) -> None:
    """HTTP 200, aber kaputtes XML: ``ParserError`` (nicht 5xx)."""
    mock_router.get(f"/plan/{EVA}/261013/14").respond(
        200, content=b"<not-valid-xml"
    )
    with pytest.raises(ParserError):
        client.get_plan(EVA, date="2026-10-13", hour=14)


# ====================================================== base url + extras
def test_default_base_url_is_db_marketplace() -> None:
    """``DEFAULT_BASE_URL`` zeigt auf den offiziellen DB-API-Marketplace."""
    assert DEFAULT_BASE_URL.startswith("https://apis.deutschebahn.com/")
    assert "timetables" in DEFAULT_BASE_URL


def test_user_agent_constant_is_stable() -> None:
    """Der User-Agent ist audit-pflichtig (ANFORDERUNGEN N2) und darf
    nicht versehentlich geändert werden."""
    assert USER_AGENT.startswith("fahrgastrechte/")
    assert "+contact:" in USER_AGENT


def test_invalid_max_attempts_raises_value_error() -> None:
    with pytest.raises(ValueError):
        TimetablesClient(
            client_id="x", api_key="y",
            base_url=DEFAULT_BASE_URL,
            max_attempts=0,
        )


def test_context_manager_closes_underlying_client() -> None:
    """``with TimetablesClient(...) as c`` schließt den HTTP-Client sauber."""
    c = _make_client(client_id="x", api_key="y")
    with c as inner:
        assert inner is c
    # Nach Verlassen des Contexts: ``_client`` ist geschlossen.
    assert c._client.is_closed
