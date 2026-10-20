"""Monats-Statistik für das Verspätungskonto (ANFORDERUNGEN F11).

Aggregiert Fälle pro Monat, wendet den 25-%-Monatsdeckel pro Ticket-Typ
an und liefert das Ergebnis als stabile Dict-Struktur für ``--json`` und
den Phase-2-MCP-Tool ``stats(month?) -> {...}``.

Designprinzipien
================

* **Keine erfundenen Beträge.** Aggregation folgt exakt der Regel aus
  :mod:`rules`: für jeden Fall wird ``compute_compensation`` mit dem
  laufenden Monats-Saldo aufgerufen — so greift der Deckel an exakt der
  richtigen Stelle.
* **Caps pro Ticket-Typ.** D-Ticket 58 € hat einen eigenen Deckel
  (1450 Cent), BC100 10 € einen eigenen (2500 Cent). Wir aggregieren
  daher **pro Ticket-Typ** und summieren am Ende.
* **Monats-Filter optional.** ``month=None`` bedeutet "alle Fälle
  gruppiert nach Kalendermonat"; ein spezifisches dict gibt nur den
  Monat zurück.
* **Strikter Output.** Alle Geldwerte in Cent (``int``), niemals ``float``.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime
from typing import Any, Iterable

from .models import TicketType
from .rules import (
    DECKEL_PROZENT,
    BAGATELLGRENZE_CENTS,
    CompensationResult,
    ReasonCode,
    compute_compensation,
)


def _normalise_month(month: str) -> str:
    """Akzeptiert 'YYYY-MM' oder 'YYYYMM'."""

    s = (month or "").strip()
    ym: str | None = None
    if len(s) == 7 and s[4] == "-":
        ym = s
    elif len(s) == 6 and s.isdigit():
        ym = f"{s[0:4]}-{s[4:6]}"
    else:
        raise ValueError(
            f"Ungültiger month-pattern -- {month!r} (erwartet YYYY-MM oder YYYYMM)"
        )
    try:
        parsed = datetime.strptime(f"{ym}-01", "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(
            f"Ungültiger month-pattern -- {month!r} (erwartet YYYY-MM oder YYYYMM)"
        ) from exc
    return parsed.strftime("%Y-%m")


def _service_month(service_date: str) -> str | None:
    """Extrahiert 'YYYY-MM' aus 'YYYY-MM-DD' (None bei Müll)."""

    s = (service_date or "").strip()
    if len(s) >= 7 and s[4] == "-" and s[5:7].isdigit():
        return f"{s[0:4]}-{s[5:7]}"
    return None


def _case_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Holt alle Fälle inklusive Ticket-Preis aus der DB.

    ``ticket_price_cents`` kommt aus der ``config``-Tabelle (Default
    :data:`config.DEFAULT_TICKET_PRICE_CENTS`). Wir lesen den Preis hier
    zentral, damit Tests ohne gesetzte Config funktionieren.
    """

    rows = conn.execute(
        "SELECT c.id AS case_id, c.ticket_type, c.ticket_class, "
        "c.expected_cents, c.cap_applied, "
        "t.service_date, t.delay_min_arr "
        "FROM cases c JOIN trips t ON c.trip_id = t.id "
        "ORDER BY t.service_date ASC, c.id ASC"
    ).fetchall()
    return [dict(r) for r in rows]


def _ticket_price_cents(conn: sqlite3.Connection) -> int:
    """Liest den konfigurierten Ticket-Preis (oder Default 4900 Cent)."""

    from .config import DEFAULT_TICKET_PRICE_CENTS, get_config  # lazy import

    raw = get_config(conn, "ticket_price_cents")
    if raw is None:
        return int(DEFAULT_TICKET_PRICE_CENTS)
    try:
        return int(raw)
    except ValueError:
        return int(DEFAULT_TICKET_PRICE_CENTS)


# --------------------------------------------------------------- Aggregation
def _month_key_for(row: dict[str, Any], month_filter: str | None) -> str | None:
    """Bestimmt, in welchen Monats-Bucket der Fall gehört (oder ``None``)."""

    bucket = _service_month(str(row.get("service_date") or ""))
    if bucket is None:
        return None
    if month_filter is not None and bucket != month_filter:
        return None
    return bucket


def aggregate_cases(
    rows: Iterable[dict[str, Any]],
    *,
    ticket_price_cents: int,
    month: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Aggregiert eine gegebene Falliste zu Monats-Stats.

    Ohne ``month``-Filter: ein Eintrag pro Kalendermonat. Mit Filter:
    genau ein Eintrag (für ``month``) oder ein leeres Dict (wenn kein
    Fall in dem Monat). Pro Ticket-Typ wird ein eigener Saldo geführt,
    damit die Caps unabhängig wirken.
    """

    normalised_filter: str | None = None
    if month is not None:
        normalised_filter = _normalise_month(month)

    # ticket_type -> cents_in_month
    type_saldo: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    # Auch der pro-Fall-Begründungs-Code (für Audit / JSON).
    reasons: dict[str, list[str]] = defaultdict(list)
    counts: dict[str, int] = defaultdict(int)

    for row in rows:
        bucket = _month_key_for(row, normalised_filter)
        if bucket is None:
            continue
        tt_raw = str(row.get("ticket_type") or "")
        try:
            tt = TicketType(tt_raw)
        except ValueError:
            tt = None  # type: ignore[assignment]
        delay = row.get("delay_min_arr")
        try:
            delay_int = int(delay) if delay is not None else 0
        except (TypeError, ValueError):
            delay_int = 0
        klass = row.get("ticket_class")
        try:
            klass_int = int(klass) if klass is not None else 2
        except (TypeError, ValueError):
            klass_int = 2

        # compute_compensation wirft ValueError bei unbekanntem Typ —
        # wir geben in dem Fall 0 Cent zurück und dokumentieren es.
        try:
            result: CompensationResult = compute_compensation(
                tt if tt is not None else "unknown",
                delay_int,
                ticket_price_cents=ticket_price_cents,
                travel_class=klass_int,
                monthly_total_cents=int(type_saldo[bucket][tt_raw]),
            )
        except ValueError:
            result = CompensationResult(
                cents=0,
                reason="Unbekannter ticket_type in der DB; Betrag 0.",
                reason_code=ReasonCode.UNKNOWN_TICKET,
                cap_applied=0,
                below_threshold=True,
            )

        type_saldo[bucket][tt_raw] += int(result.cents)
        reasons[bucket].append(result.reason_code.value)
        counts[bucket] += 1

    out: dict[str, dict[str, Any]] = {}
    for bucket in sorted(set(counts) | set(type_saldo)):
        # Gesamtsumme über alle Ticket-Typen in diesem Monat.
        total_cents = sum(int(c) for c in type_saldo[bucket].values())
        # Summe der 25-%-Caps pro Ticket-Typ = maximal möglicher Betrag
        # in diesem Monat (über alle beteiligten Typen hinweg).
        cap_total = sum(
            (int(ticket_price_cents) * DECKEL_PROZENT) // 100
            for _ in type_saldo[bucket]
        )
        # Wenn im Monat nur ein Ticket-Typ auftaucht, ist cap_total
        # = einzelner Deckel. Für die Ausgabe runden wir auf Cent (int).
        out[bucket] = {
            "case_count": int(counts[bucket]),
            "expected_cents": int(total_cents),
            "below_threshold": int(total_cents) < BAGATELLGRENZE_CENTS,
            "cap": int(cap_total),
        }
    return out


# --------------------------------------------------------------- Public API
def compute_monthly_stats(
    conn: sqlite3.Connection,
    *,
    month: str | None = None,
    ticket_price_cents: int | None = None,
) -> dict[str, Any]:
    """Liefert Statistik-Daten für den MCP-Tool ``stats`` (F11).

    Parameter
    ---------

    * ``conn`` — offene SQLite-Connection.
    * ``month`` — optional "YYYY-MM". ``None`` → ein Eintrag pro Monat.
    * ``ticket_price_cents`` — Monatspreis für die Deckel-Berechnung.
      ``None`` → aus :func:`config.get_config` (Default 4900 Cent).

    Rückgabe
    --------

    Bei spezifischem Monat::

        {
            "month": "2026-10",
            "case_count": <int>,
            "expected_cents": <int>,     # nach Cap, Ganzzahl
            "below_threshold": <bool>,
            "cap": <int>,
        }

    Ohne Filter::

        {
            "months": [
                {"month": "2026-09", "case_count": ..., ...},
                {"month": "2026-10", ...},
            ],
            "totals": {
                "case_count": <int>,
                "expected_cents": <int>,
                "below_threshold": <bool>,
                "cap": <int>,
            },
        }
    """

    if ticket_price_cents is None:
        ticket_price_cents = _ticket_price_cents(conn)

    rows = _case_rows(conn)
    buckets = aggregate_cases(
        rows, ticket_price_cents=int(ticket_price_cents), month=month
    )

    if month is not None:
        # Spezifischer Monat — wenn keine Fälle, leere Struktur zurück.
        normalised = _normalise_month(month)
        entry = buckets.get(normalised)
        if entry is None:
            return {
                "month": normalised,
                "case_count": 0,
                "expected_cents": 0,
                "below_threshold": True,
                "cap": 0,
            }
        return {"month": normalised, **entry}

    # Alle Monate.
    months = [
        {"month": m, **buckets[m]} for m in sorted(buckets)
    ]
    totals_count = sum(b["case_count"] for b in months)
    totals_cents = sum(b["expected_cents"] for b in months)
    totals_cap = sum(b["cap"] for b in months)
    return {
        "months": months,
        "totals": {
            "case_count": int(totals_count),
            "expected_cents": int(totals_cents),
            "below_threshold": int(totals_cents) < BAGATELLGRENZE_CENTS,
            "cap": int(totals_cap),
        },
    }


# --------------------------------------------------------------- Re-export
__all__ = [
    "aggregate_cases",
    "compute_monthly_stats",
]