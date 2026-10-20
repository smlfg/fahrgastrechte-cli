"""Timetables-API HTTP-Client (ANFORDERUNGEN §7, §9 N2, §13/3).

Drei Methoden:

* ``search_station(pattern)``         → ``GET /station/{pattern}``
* ``get_plan(eva_no, date, hour)``    → ``GET /plan/{evaNo}/{date}/{hour}``
* ``get_realtime_changes(eva_no)``    → ``GET /rchg/{evaNo}``
* ``get_full_changes(eva_no)``        → ``GET /fchg/{evaNo}``

Authentifizierung:

* Header ``DB-Client-Id`` und ``DB-Api-Key`` (DB API Marketplace);
* wahlweise via Konstruktor-Argumente, Umgebungsvariablen
  ``FAHRGASTRECHTE_DB_CLIENT_ID`` / ``FAHRGASTRECHTE_DB_API_KEY``,
  oder explizite Übergabe pro Aufruf.

User-Agent: ``fahrgastrechte/0.1 (+contact: samuel@example.invalid)``
(ANFORDERUNGEN N2). Niemals überschreiben — der Wert ist audit-pflichtig.

Retry-Strategie:

* 429 mit Retry-After: ``min(server_value, expo_backoff)``;
* 5xx: Exponential-Backoff 0,5s → 1s → 2s (3 Versuche standardmäßig);
* Netzwerk-Fehler (ConnectError, ReadTimeout) werden ebenfalls retried.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date as _date
from typing import Any, Iterable, Mapping

import httpx

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
    PlanEntry,
    RealtimeChange,
    RealtimeKind,
    parse_plan,
    parse_realtime,
    parse_stations,
)
from .ratelimit import RateLimiter

_log = logging.getLogger(__name__)

# Offizielle DB-API-Marketplace-URL für das Timetables-Produkt (Phase 1).
# Die Sandbox-Variante ``iris.noncd.db.de`` ist legacy und nicht zur
# freien Nutzung dokumentiert.
DEFAULT_BASE_URL = (
    "https://apis.deutschebahn.com/db-api-marketplace/apis/timetables/v1"
)
USER_AGENT = "fahrgastrechte/0.1 (+contact: samuel@example.invalid)"

ENV_CLIENT_ID = "FAHRGASTRECHTE_DB_CLIENT_ID"
ENV_API_KEY = "FAHRGASTRECHTE_DB_API_KEY"
ENV_BASE_URL = "FAHRGASTRECHTE_TIMETABLES_BASE_URL"

DEFAULT_TIMEOUT_S = 15.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_BASE_S = 0.5

# Endpunkte, die wir für /rchg + /fchg erlauben.
_REALTIME_ENDPOINTS: Mapping[RealtimeKind, str] = {
    RealtimeKind.RCHG: "/rchg/{eva}",
    RealtimeKind.FCHG: "/fchg/{eva}",
}


class TimetablesClient:
    """HTTP-Client für das DB Timetables API (IRIS, DB API Marketplace)."""

    def __init__(
        self,
        client_id: str | None = None,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        rate_per_minute: int = 30,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff_base_s: float = DEFAULT_BACKOFF_BASE_S,
        transport: httpx.BaseTransport | None = None,
        limiter: RateLimiter | None = None,
        sleep_fn: Any = time.sleep,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts muss >= 1 sein")
        self.client_id = client_id or os.environ.get(ENV_CLIENT_ID)
        self.api_key = api_key or os.environ.get(ENV_API_KEY)
        self.base_url = (
            base_url
            or os.environ.get(ENV_BASE_URL)
            or DEFAULT_BASE_URL
        ).rstrip("/")
        self.timeout_s = timeout_s
        self.max_attempts = max_attempts
        self.backoff_base_s = backoff_base_s
        self._limiter = limiter or RateLimiter(
            rate_per_minute=rate_per_minute,
            wait_fn=sleep_fn,
        )
        # ``transport`` ermöglicht Tests mit respx (httpx.MockTransport).
        # In Produktion lassen wir httpx den Default-Transport wählen.
        self._owns_client = transport is None
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout_s,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/xml, text/xml;q=0.9, */*;q=0.5",
            },
            transport=transport,
        )

    # --------------------------------------------------------- properties
    @property
    def credentials_configured(self) -> bool:
        return bool(self.client_id and self.api_key)

    # ----------------------------------------------------------- helpers
    def _headers(self) -> dict[str, str]:
        if not self.credentials_configured:
            raise AuthenticationError(
                "DB Timetables Credentials fehlen: "
                f"`fahrgastrechte config set db_client_id` / "
                f"`db_api_key`, {ENV_CLIENT_ID} / {ENV_API_KEY} "
                "oder explizit übergeben."
            )
        return {
            "DB-Client-Id": self.client_id or "",
            "DB-Api-Key": self.api_key or "",
        }

    def _request_with_retry(
        self, method: str, path: str, **params: Any
    ) -> httpx.Response:
        """HTTP-Aufruf mit Rate-Limit-Vorlauf und Retry-Loop.

        Wird sowohl vom production-Code als auch von den Tests
        durchgereicht — daher ist die Funktion bewusst frei von
        *Magic*: alles über die Konstruktor-Argumente bzw. ``self``
        steuerbar.
        """

        self._limiter.acquire()
        headers = self._headers()
        last_exc: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self._client.request(
                    method, path, params=params, headers=headers
                )
            except httpx.ConnectError as exc:
                last_exc = exc
                if attempt >= self.max_attempts:
                    raise NetworkError(
                        f"Verbindung zu DB Timetables fehlgeschlagen: {exc}",
                        original=exc,
                    ) from exc
                self._sleep_backoff(attempt)
                continue
            except httpx.ReadTimeout as exc:
                last_exc = exc
                if attempt >= self.max_attempts:
                    raise NetworkError(
                        f"Timeout bei DB Timetables: {exc}",
                        original=exc,
                    ) from exc
                self._sleep_backoff(attempt)
                continue
            except httpx.HTTPError as exc:
                # Nicht-retryable: kein ConnectError/ReadTimeout etc.
                raise NetworkError(
                    f"Unerwarteter HTTP-Transportfehler: {exc}",
                    original=exc,
                ) from exc

            status = response.status_code
            if status < 400:
                return response

            # === 4xx / 5xx ===
            if status in (401, 403):
                raise AuthenticationError(
                    "DB Timetables hat die Credentials abgelehnt "
                    f"(HTTP {status})",
                    response=response,
                )
            if status == 404:
                raise NotFoundError(
                    f"Nicht gefunden: {path!r} (HTTP 404)",
                    response=response,
                )
            if status == 429:
                if attempt >= self.max_attempts:
                    raise RateLimitError(
                        "DB Timetables Rate-Limit erschöpft (HTTP 429)",
                        response=response,
                    )
                retry_after = _parse_retry_after(response)
                self._sleep_backoff(attempt, retry_after=retry_after)
                continue
            if 500 <= status < 600:
                if attempt >= self.max_attempts:
                    raise ServerError(
                        f"DB Timetables Serverfehler HTTP {status}",
                        status_code=status,
                        response=response,
                    )
                self._sleep_backoff(attempt)
                continue
            # Andere 4xx: nicht-retryable (z. B. 400 Bad Request)
            raise TimetablesError(
                f"Unerwarteter HTTP-Status {status} von DB Timetables"
            )

        # Sollte nicht erreicht werden.
        raise NetworkError(
            "DB Timetables nach Retries nicht erreichbar",
            original=last_exc,
        )

    def _sleep_backoff(
        self, attempt: int, *, retry_after: float | None = None
    ) -> None:
        """Wartet mit Exponential-Backoff (oder auf ``Retry-After``)."""

        delay = self.backoff_base_s * (2 ** (attempt - 1))
        if retry_after is not None:
            delay = min(delay, retry_after)
        _log.debug(
            "TimetablesClient retry attempt=%d delay=%.3fs", attempt, delay
        )
        if delay > 0:
            time.sleep(delay)

    # ================================================================ API
    def search_station(self, pattern: str) -> list:
        """``GET /station/{pattern}`` → Liste von :class:`ParsedStation`."""

        if not pattern:
            raise ValueError("pattern darf nicht leer sein")
        response = self._request_with_retry(
            "GET", f"/station/{pattern}"
        )
        try:
            return parse_stations(response.content)
        except ParserError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            raise ParserError(
                f"Unerwarteter Parser-Fehler bei /station: {exc}",
                payload=response.content,
                original=exc,
            ) from exc

    def get_plan(
        self,
        eva_no: int,
        date: _date | str,
        hour: int,
    ) -> PlanEntry:
        """``GET /plan/{evaNo}/{date}/{hour}`` → :class:`PlanEntry`.

        ``date`` akzeptiert ``datetime.date`` und ISO-Strings ("2026-10-12" /
        "261013") — letztere werden unverändert an die API durchgereicht.
        """

        if not (1_000_000 <= eva_no <= 9_999_999):
            raise ValueError("eva_no muss eine 7-stellige EVA-Nummer sein")
        if not (0 <= hour <= 23):
            raise ValueError("hour muss im Bereich 0..23 sein")
        date_str = _format_plan_date(date)
        hour_str = f"{hour:02d}"
        response = self._request_with_retry(
            "GET", f"/plan/{eva_no}/{date_str}/{hour_str}"
        )
        try:
            return parse_plan(response.content, hour=hour)
        except ParserError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            raise ParserError(
                f"Unerwarteter Parser-Fehler bei /plan: {exc}",
                payload=response.content,
                original=exc,
            ) from exc

    def get_realtime_changes(
        self, eva_no: int, *, kind: RealtimeKind = RealtimeKind.RCHG
    ) -> RealtimeChange:
        """``GET /rchg/{evaNo}`` (Default) oder ``/fchg/{evaNo}``.

        ``kind=RCHG`` ist der politische Default — nur Änderungen der
        letzten 2 Minuten, ausreichend für die manuelle Rekonstruktion
        eines Falls. ``FCHG`` ist umfangreicher und für die ersten
        Initial-Ladungen gedacht (Empfehlung DB: erst ``fchg``, dann
        periodisch ``rchg``).
        """

        if not (1_000_000 <= eva_no <= 9_999_999):
            raise ValueError("eva_no muss eine 7-stellige EVA-Nummer sein")
        try:
            endpoint = _REALTIME_ENDPOINTS[kind]
        except KeyError as exc:
            raise ValueError(f"Unbekannte Realtime-Kind: {kind!r}") from exc
        path = endpoint.format(eva=eva_no)
        response = self._request_with_retry("GET", path)
        try:
            return parse_realtime(response.content, kind=kind)
        except ParserError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            raise ParserError(
                f"Unerwarteter Parser-Fehler bei {kind}: {exc}",
                payload=response.content,
                original=exc,
            ) from exc

    # Komfort-Aliase für klaren Code an der Caller-Seite.
    def get_full_changes(self, eva_no: int) -> RealtimeChange:
        """Kurzform für ``kind=FCHG``."""

        return self.get_realtime_changes(eva_no, kind=RealtimeKind.FCHG)

    # --------------------------------------------------------- context mgr
    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "TimetablesClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


# ------------------------------------------------------------ helpers
def _format_plan_date(date: _date | str) -> str:
    """Normalisiert ``date`` zu IRIS-``YYMMDD``-Format."""

    if isinstance(date, str):
        # Bereits YYMMDD?
        if len(date) == 6 and date.isdigit():
            return date
        # "YYYY-MM-DD" → YYMMDD
        if len(date) == 10 and date[4] == "-" and date[7] == "-":
            return f"{date[2:4]}{date[5:7]}{date[8:10]}"
        # Versuch als ISO-Datum zu parsen.
        try:
            d = _date.fromisoformat(date)
            return f"{d.year % 100:02d}{d.month:02d}{d.day:02d}"
        except ValueError as exc:
            raise ValueError(
                f"Ungültiges Datum für /plan: {date!r} (erwartet YYYY-MM-DD oder YYMMDD)"
            ) from exc
    if hasattr(date, "year") and hasattr(date, "month"):
        return f"{date.year % 100:02d}{date.month:02d}{date.day:02d}"
    raise TypeError(f"date muss date | str sein, nicht {type(date).__name__}")


def _parse_retry_after(response: httpx.Response) -> float | None:
    """Parst den ``Retry-After``-Header (Sekunden). Gibt ``None`` falls
    nicht parsebar."""

    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def stations_to_dicts(
    stations: Iterable,
) -> list[dict[str, Any]]:
    """Komfort-Helper für ``--json``: list[dict] statt list[Model]."""

    return [s.model_dump(mode="json") for s in stations]


__all__ = [
    "DEFAULT_BASE_URL",
    "USER_AGENT",
    "ENV_CLIENT_ID",
    "ENV_API_KEY",
    "ENV_BASE_URL",
    "DEFAULT_TIMEOUT_S",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_BACKOFF_BASE_S",
    "TimetablesClient",
    "stations_to_dicts",
]
