"""Fehler-Hierarchie für den DB-Timetables-Connector (ANFORDERUNGEN §3 + §13/3).

Die CLI übersetzt diese später in Exit-Codes:

* Netzwerk-/HTTP-Fehler  → Exit 4
* Parser-/Datenfehler     → Exit 5
* Auth-Fehler             → Exit 4 (Credentials fehlen/falsch)

``TimetablesError`` ist die gemeinsame Basis für alle Connector-Fehler
und lässt sich bequem mit ``except TimetablesError`` fangen. Die Subtypen
enthalten jeweils die ursprüngliche ``httpx``-Response (sofern vorhanden),
damit die CLI saubere Diagnose-Texte ausgeben kann.
"""

from __future__ import annotations

from typing import Any

import httpx


class TimetablesError(RuntimeError):
    """Basisklasse für alle Connector-Fehler."""


class NetworkError(TimetablesError):
    """Transport-Fehler (DNS, Timeout, Verbindungsabbruch) — CLI: Exit 4."""

    def __init__(self, message: str, *, original: Exception | None = None) -> None:
        super().__init__(message)
        self.original = original


class AuthenticationError(TimetablesError):
    """HTTP 401/403 — fehlende oder falsche Credentials — CLI: Exit 4."""

    def __init__(self, message: str, *, response: httpx.Response | None = None) -> None:
        super().__init__(message)
        self.response = response


class NotFoundError(TimetablesError):
    """HTTP 404 — Station unbekannt oder Pfad falsch — CLI: Exit 4."""

    def __init__(self, message: str, *, response: httpx.Response | None = None) -> None:
        super().__init__(message)
        self.response = response


class RateLimitError(TimetablesError):
    """HTTP 429 nach erschöpften Retries — CLI: Exit 4."""

    def __init__(self, message: str, *, response: httpx.Response | None = None) -> None:
        super().__init__(message)
        self.response = response


class ServerError(TimetablesError):
    """HTTP 5xx nach erschöpften Retries — CLI: Exit 4."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        response: httpx.Response | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response = response


class ParserError(TimetablesError):
    """Antwort war HTTP-200, aber XML/Modelle sind nicht interpretierbar — CLI: Exit 5."""

    def __init__(
        self,
        message: str,
        *,
        payload: Any = None,
        original: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.payload = payload
        self.original = original


__all__ = [
    "TimetablesError",
    "NetworkError",
    "AuthenticationError",
    "NotFoundError",
    "RateLimitError",
    "ServerError",
    "ParserError",
]
