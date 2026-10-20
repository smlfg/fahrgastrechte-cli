"""Status-Workflow für Verspätungs-Fälle (ANFORDERUNGEN §3 F7)."""

from __future__ import annotations

import sqlite3
from typing import Any

from .cases import CaseNotFoundError, _now_iso, show_case
from .models import CaseStatusName

# Erlaubte Übergänge laut §3 F7.
_ALLOWED: dict[CaseStatusName, frozenset[CaseStatusName]] = {
    CaseStatusName.OFFEN: frozenset({CaseStatusName.EINGEREICHT}),
    CaseStatusName.EINGEREICHT: frozenset(
        {
            CaseStatusName.RUECKFRAGE,
            CaseStatusName.BEWILLIGT,
            CaseStatusName.ABGELEHNT,
        }
    ),
    CaseStatusName.RUECKFRAGE: frozenset({CaseStatusName.NACHGEREICHT}),
    CaseStatusName.NACHGEREICHT: frozenset(
        {
            CaseStatusName.RUECKFRAGE,
            CaseStatusName.BEWILLIGT,
            CaseStatusName.ABGELEHNT,
        }
    ),
    CaseStatusName.BEWILLIGT: frozenset({CaseStatusName.AUSGEZAHLT}),
    CaseStatusName.AUSGEZAHLT: frozenset(),
    CaseStatusName.ABGELEHNT: frozenset(),
}

_NOTE_REQUIRED = frozenset(
    {CaseStatusName.ABGELEHNT, CaseStatusName.RUECKFRAGE}
)

_STATUS_ALIASES: dict[str, str] = {
    "rückfrage": CaseStatusName.RUECKFRAGE.value,
    "rueckfrage": CaseStatusName.RUECKFRAGE.value,
}


class InvalidStatusTransitionError(ValueError):
    """Übergang ist im Workflow nicht erlaubt."""


class StatusNoteRequiredError(ValueError):
    """Begründung fehlt für ``abgelehnt`` / ``rueckfrage``."""


def normalise_status_name(raw: str) -> CaseStatusName:
    """Mappt CLI-Strings auf :class:`CaseStatusName`."""

    key = (raw or "").strip().lower()
    key = _STATUS_ALIASES.get(key, key)
    try:
        return CaseStatusName(key)
    except ValueError as exc:
        allowed = ", ".join(s.value for s in CaseStatusName)
        raise ValueError(
            f"Unbekannter Status {raw!r}; erlaubt: {allowed}"
        ) from exc


def _current_status(conn: sqlite3.Connection, case_id: int) -> CaseStatusName:
    row = conn.execute(
        "SELECT status FROM case_status WHERE case_id = ? "
        "ORDER BY at DESC, rowid DESC LIMIT 1",
        (case_id,),
    ).fetchone()
    if row is None:
        return CaseStatusName.OFFEN
    return CaseStatusName(row["status"])


def update_status(
    conn: sqlite3.Connection,
    case_id: int,
    new_status: str,
    *,
    note: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Setzt einen neuen Workflow-Status und liefert den aktualisierten Fall."""

    # Existenz prüfen (show_case wirft CaseNotFoundError).
    show_case(conn, case_id)

    target = normalise_status_name(new_status)
    current = _current_status(conn, case_id)

    if not force and target not in _ALLOWED.get(current, frozenset()):
        raise InvalidStatusTransitionError(
            f"Übergang {current.value} → {target.value} ist nicht erlaubt."
        )

    if target in _NOTE_REQUIRED and not (note or "").strip():
        raise StatusNoteRequiredError(
            f"Für Status {target.value!r} ist --note Pflicht."
        )

    at = _now_iso()
    conn.execute(
        "INSERT INTO case_status(case_id, status, at, note) VALUES (?, ?, ?, ?)",
        (case_id, target.value, at, note),
    )
    conn.execute(
        "UPDATE cases SET updated_at = ? WHERE id = ?",
        (at, case_id),
    )
    conn.commit()
    return show_case(conn, case_id)


__all__ = [
    "InvalidStatusTransitionError",
    "StatusNoteRequiredError",
    "normalise_status_name",
    "update_status",
]
