"""Tests für den RateLimiter (ANFORDERUNGEN §9 N2)."""

from __future__ import annotations

from typing import Iterable

import pytest

from fahrgastrechte_core.timetables import (
    DEFAULT_RATE_PER_MINUTE,
    RateLimiter,
)


class FakeClock:
    """Ein deterministisch steuerbarer Zeit-Mock für den RateLimiter."""

    def __init__(self) -> None:
        self.now = 0.0
        self.calls: list[float] = []

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def sleeper(self, sink: list[float]):
        """wait_fn, das die Wartezeit protokolliert UND die Uhr vorrückt
        (sonst Endlosschleife in RateLimiter.acquire)."""

        def _wait(seconds: float) -> None:
            sink.append(seconds)
            self.advance(seconds)

        return _wait


def test_interval_matches_configured_rate() -> None:
    rl = RateLimiter(rate_per_minute=60)
    assert rl.interval_seconds == pytest.approx(1.0)


def test_default_rate_is_30_per_minute() -> None:
    assert DEFAULT_RATE_PER_MINUTE == 30
    assert RateLimiter().interval_seconds == pytest.approx(2.0)


def test_first_acquire_does_not_wait() -> None:
    clock = FakeClock()
    sleeps: list[float] = []
    rl = RateLimiter(
        rate_per_minute=30, now_fn=clock, wait_fn=clock.sleeper(sleeps)
    )
    assert rl.time_to_next_slot() == 0.0
    rl.acquire()
    assert sleeps == []


def test_second_acquire_waits_until_interval_elapsed() -> None:
    clock = FakeClock()
    sleeps: list[float] = []
    rl = RateLimiter(
        rate_per_minute=30, now_fn=clock, wait_fn=clock.sleeper(sleeps)
    )
    rl.acquire()
    clock.advance(0.7)
    rl.acquire()
    # Es fehlen 2.0 - 0.7 = 1.3s.
    assert sleeps == [pytest.approx(1.3, abs=1e-9)]


def test_acquire_loops_until_enough_time_elapsed() -> None:
    """Semantik: Mindestabstand ``interval`` zwischen zwei Slots
    (ANFORDERUNGEN §9 N2).

    Auch wenn die Uhr zwischen den ``acquire()``-Aufrufen nur
    teilweise vorrückt, wartet jeder Aufruf exakt die Differenz zum
    nächsten erlaubten Slot. Sobald das volle Intervall seit dem
    letzten Slot verstrichen ist, entfällt das Warten — der Limiter
    kommt ohne weiteren ``wait``-Zyklus aus.
    """
    clock = FakeClock()
    sleeps: list[float] = []
    rl = RateLimiter(
        rate_per_minute=30, now_fn=clock, wait_fn=clock.sleeper(sleeps)
    )

    rl.acquire()  # t=0, last_call=0, kein Wait
    assert sleeps == []

    # Sofort danach: 2.0s fehlen → wait 2.0s, t→2.0, last_call=2.0
    rl.acquire()
    assert sleeps == [pytest.approx(2.0, abs=1e-9)]

    # Teilfortschritt 0.5s: 1.5s fehlen → wait 1.5s, t→4.0, last_call=4.0
    clock.advance(0.5)
    rl.acquire()
    assert sleeps == [
        pytest.approx(2.0, abs=1e-9),
        pytest.approx(1.5, abs=1e-9),
    ]

    # Genau 2.0s seit letztem Slot: kein Wait mehr.
    clock.advance(2.0)
    rl.acquire()
    assert sleeps == [
        pytest.approx(2.0, abs=1e-9),
        pytest.approx(1.5, abs=1e-9),
    ]


def test_invalid_rate_raises() -> None:
    with pytest.raises(ValueError):
        RateLimiter(rate_per_minute=0)
    with pytest.raises(ValueError):
        RateLimiter(rate_per_minute=-5)
