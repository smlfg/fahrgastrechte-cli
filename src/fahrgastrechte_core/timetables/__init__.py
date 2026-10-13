"""DB Timetables (IRIS / DB API Marketplace) — Connector (ANFORDERUNGEN §7).

Phase 1 nutzt nur die offizielle DB-API: ``/station/{pattern}``,
``/plan/{evaNo}/{date}/{hour}``, ``/rchg/{evaNo}`` (sowie optional
``/fchg/{evaNo}``). Authentifizierung läuft über die Header
``DB-Client-Id`` und ``DB-Api-Key``.

Diese Bibliothek

* kapselt HTTP-Transport, Auth-Header, User-Agent und eigenes
  Rate-Limiting (ANFORDERUNGEN §9 N2),
* parst XML-Antworten in stabile Pydantic-Modelle und berechnet
  ``delay_min`` ohne erfundene Zeiten (N3),
* unterscheidet Netzwerk-/HTTP-Fehler (``TimetablesError``-Subklassen)
  und Parser-Fehler (``ParserError``).
"""

from .client import (
    DEFAULT_BASE_URL,
    USER_AGENT,
    TimetablesClient,
)
from .errors import (
    AuthenticationError,
    NetworkError,
    NotFoundError,
    ParserError,
    RateLimitError,
    ServerError,
    TimetablesError,
)
from .models import (
    ParsedStation,
    ParsedStop,
    PlanEntry,
    RealtimeChange,
    RealtimeKind,
    StopEvent,
    TripLabel,
    parse_plan,
    parse_realtime,
    parse_stations,
    parse_yymmddhhmm,
)
from .ratelimit import (
    DEFAULT_RATE_PER_MINUTE,
    RateLimiter,
)

__all__ = [
    # client
    "DEFAULT_BASE_URL",
    "USER_AGENT",
    "TimetablesClient",
    # errors
    "TimetablesError",
    "AuthenticationError",
    "NotFoundError",
    "RateLimitError",
    "ServerError",
    "NetworkError",
    "ParserError",
    # models
    "RealtimeKind",
    "TripLabel",
    "StopEvent",
    "ParsedStop",
    "ParsedStation",
    "PlanEntry",
    "RealtimeChange",
    "parse_stations",
    "parse_plan",
    "parse_realtime",
    "parse_yymmddhhmm",
    # rate limit
    "DEFAULT_RATE_PER_MINUTE",
    "RateLimiter",
]
