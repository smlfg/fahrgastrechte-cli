"""Schonender API-Zugriff (ANFORDERUNGEN §9 N2).

Diese Bibliothek ruft höchstens 30 Anfragen pro Minute an die
DB-Timetables-API ab. Der Timetables-Plan erlaubt nominal 60 req/min
— wir halten uns selbst an die Hälfte als Sicherheits-Reserve.

Realisiert als minimaler Token-Bucket ohne externe Abhängigkeiten:

* ``acquire()`` blockiert (per ``time.sleep`` oder injizierter
  ``wait_fn``) bis ein neues Token verfügbar ist;
* ``peek()`` ist eine Hilfsfunktion für Tests/Caller (z. B. um zu
  sehen, wie lange der nächste Aufruf warten müsste).

Der Limiter ist **zustandsbehaftet pro Instanz** — pro ``TimetablesClient``
gibt es genau einen Bucket. Bei mehreren Clients dürfen sich diese
gegenseitig ausmessen, das ist bewusst (separates Rate-Limit-Per-Konto).
"""

from __future__ import annotations

import threading
import time
from typing import Callable

DEFAULT_RATE_PER_MINUTE = 30  # ANFORDERUNGEN §9 N2


class RateLimiter:
    """Minimaler Token-Bucket mit fester Rate ``rate_per_minute``."""

    def __init__(
        self,
        rate_per_minute: int = DEFAULT_RATE_PER_MINUTE,
        *,
        now_fn: Callable[[], float] = time.monotonic,
        wait_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate_per_minute <= 0:
            raise ValueError("rate_per_minute muss > 0 sein")
        self._interval = 60.0 / rate_per_minute
        self._now = now_fn
        self._wait = wait_fn
        self._last_call: float | None = None
        self._lock = threading.Lock()

    @property
    def interval_seconds(self) -> float:
        return self._interval

    def time_to_next_slot(self) -> float:
        """Sekunden bis der nächste Aufruf erlaubt ist (0 wenn sofort)."""

        with self._lock:
            if self._last_call is None:
                return 0.0
            elapsed = self._now() - self._last_call
            remaining = self._interval - elapsed
            return max(0.0, remaining)

    def acquire(self) -> None:
        """Blockiert falls nötig und markiert dann einen neuen Slot.

        Tests können ``wait_fn=lambda s: None`` injizieren, um das
        Warten zu neutralisieren — der Limiter funktioniert dann rein
        als Buchhalter.
        """

        while True:
            with self._lock:
                if self._last_call is None:
                    self._last_call = self._now()
                    return
                elapsed = self._now() - self._last_call
                remaining = self._interval - elapsed
                if remaining <= 0:
                    self._last_call = self._now()
                    return
                wait_for = remaining

            # Außerhalb des Locks warten — sonst serialisieren wir
            # parallel laufende Threads künstlich.
            self._wait(wait_for)


__all__ = ["DEFAULT_RATE_PER_MINUTE", "RateLimiter"]
