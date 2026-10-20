"""Geschäftslogik für Verspätungs-Fälle (ANFORDERUNGEN §3 F1/F3/F4/F6).

Dieses Modul kapselt alles, was zwischen der reinen DB-Schicht und der CLI / UI
passiert:

* Duplikat-Hash (sha256 über ``service_date`` + Zugnummer + Start-/Ziel-EVA
  + ``planned_dep`` — siehe §3 F6).
* Anlegen eines Falles: Trip finden/erzeugen, Case einfügen, Status-Log
  initialisieren.
* Listen / Anzeigen mit Join über Trip und aktuellen Status.

Designregeln
------------

* **Keine Annahmen über Berechtigung:** das Tool speichert nur, was Samuel
  eintippt — keine erfundenen Verspätungen, keine Schätzungen
  (``expected_cents`` ist deshalb standardmäßig ``0`` und wird in
  Schritt 5 überschrieben).
* **Strikte Trennung:** die Funktionen hier kennen keine Typer-Optionen,
  keine Rich-Tabellen, keine Exit-Codes. Die CLI übersetzt
  ``ValueError`` in Exit 3 und :class:`DuplicateCaseError` in Exit 1.
* **Idempotente Trip-Erzeugung:** zwei identische ``add_case``-Aufrufe
  erzeugen denselben Trip (UNIQUE auf ``trips``), aber keinen zweiten
  Case (UNIQUE auf ``cases.dup_hash``).
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from .models import (
    Case,
    CaseStatusEntry,
    CaseStatusName,
    DataQuality,
    DataSource,
    TicketType,
)

_log = logging.getLogger(__name__)

# Feste Zeitzone für alle Zeitstempel (ANFORDERUNGEN §9 N4).
BERLIN = ZoneInfo("Europe/Berlin")


# ----------------------------------------------------------- Fehler
class CaseNotFoundError(LookupError):
    """Ein ``case_id`` existiert nicht."""

    def __init__(self, case_id: int) -> None:
        super().__init__(f"Fall #{case_id} nicht gefunden")
        self.case_id = case_id


class DuplicateCaseError(ValueError):
    """Es existiert bereits ein Fall mit demselben :func:`compute_dup_hash`.

    Die ursprüngliche Fall-ID wird in ``original_id`` mitgegeben — die CLI
    zeigt sie im Exit-1-Fehlertext an (ANFORDERUNGEN §3 F6).
    """

    def __init__(self, original_id: int, dup_hash: str) -> None:
        super().__init__(
            f"Duplikat: Fall #{original_id} hat bereits denselben Hash "
            f"({dup_hash[:12]}…). `add --force` zum Überschreiben."
        )
        self.original_id = original_id
        self.dup_hash = dup_hash


# ----------------------------------------------------------- Hilfen
def _now_iso() -> str:
    """ISO-8601-Zeitstempel in ``Europe/Berlin`` (ANFORDERUNGEN §9 N4)."""

    return datetime.now(BERLIN).isoformat(timespec="seconds")


def _normalise_date(service_date: str) -> str:
    """Akzeptiert ISO-Datum ("2026-10-12") und YYYYMMDD ("20261012").

    Wirft ``ValueError`` (→ Exit 3 in der CLI) bei offensichtlichem Müll.
    """

    s = (service_date or "").strip()
    iso_candidate: str | None = None
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        iso_candidate = s
    elif len(s) == 8 and s.isdigit():
        iso_candidate = f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    if iso_candidate is not None:
        try:
            return datetime.strptime(iso_candidate, "%Y-%m-%d").date().isoformat()
        except ValueError as exc:
            raise ValueError(
                f"Ungültiges service_date {service_date!r} "
                "(erwartet YYYY-MM-DD oder YYYYMMDD)"
            ) from exc
    try:
        return datetime.strptime(s, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise ValueError(
            f"Ungültiges service_date {service_date!r} "
            "(erwartet YYYY-MM-DD oder YYYYMMDD)"
        ) from exc


def _validate_eva(eva: int, *, name: str = "eva") -> None:
    if not (1_000_000 <= int(eva) <= 9_999_999):
        raise ValueError(
            f"{name} muss eine 7-stellige EVA-Nummer sein, "
            f"bekommen: {eva!r}"
        )


# ----------------------------------------------------------- Duplikat-Hash
def compute_dup_hash(
    *,
    service_date: str,
    train_number: str,
    start_eva: int,
    dest_eva: int,
    planned_dep: str | None = None,
) -> str:
    """Berechnet den Duplikat-Hash laut §3 F6.

    Felder: ``service_date`` + ``train_number`` + ``start_eva`` +
    ``dest_eva`` + ``planned_dep`` (leerer String, wenn None — sonst
    würden zwei manuelle Fälle ohne Abfahrtszeit als verschieden
    gehasht). Trennzeichen ist ``|``, damit z. B. "12" + "345" vs.
    "1" + "2345" sicher kollidieren.

    Wirft ``ValueError`` bei offensichtlich ungültigen Pflichtfeldern.
    """

    if not service_date or not train_number:
        raise ValueError(
            "compute_dup_hash braucht mindestens service_date und train_number"
        )
    _validate_eva(start_eva, name="start_eva")
    _validate_eva(dest_eva, name="dest_eva")
    payload = "|".join(
        [
            _normalise_date(service_date),
            train_number.strip(),
            f"{int(start_eva):07d}",
            f"{int(dest_eva):07d}",
            (planned_dep or "").strip(),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _lastrowid(cur: sqlite3.Cursor) -> int:
    """Holt ``cur.lastrowid`` als ``int`` (Stub ist ``int | None``)."""

    rid = cur.lastrowid
    if rid is None:
        raise RuntimeError("Cursor.lastrowid war None — INSERT fehlgeschlagen?")
    return int(rid)


def find_case_by_hash(
    conn: sqlite3.Connection, dup_hash: str
) -> tuple[int, int] | None:
    """Liefert ``(case_id, trip_id)`` für einen existierenden Hash oder ``None``."""

    row = conn.execute(
        "SELECT id, trip_id FROM cases WHERE dup_hash = ? LIMIT 1",
        (dup_hash,),
    ).fetchone()
    if row is None:
        return None
    return int(row["id"]), int(row["trip_id"])


# ----------------------------------------------------------- Trip
def _ensure_station(
    conn: sqlite3.Connection, eva: int | None, *, source: str = "manual"
) -> None:
    """Legt einen Stations-Eintrag an, falls noch keiner existiert.

    Die ``stations``-Tabelle hat einen FK-Constraint auf ``trips``. Wir
    füllen sie implizit beim ersten Trip — Station-Name wird nicht
    erfunden, der Datensatz kann später über ``stations`` aufgeräumt
    werden (oder durch eine echte Timetables-API-Suche in
    Schritt ≥ 6 ergänzt).
    """

    if eva is None:
        return
    if not (1_000_000 <= int(eva) <= 9_999_999):
        return
    conn.execute(
        "INSERT OR IGNORE INTO stations(eva_no, name, source) VALUES (?, ?, ?)",
        (int(eva), f"eva:{int(eva)}", source),
    )


def _find_trip(
    conn: sqlite3.Connection,
    *,
    service_date: str,
    train_number: str,
    start_eva: int | None,
    dest_eva: int | None,
) -> int | None:
    """Sucht einen Trip per UNIQUE-Key laut §4 (``trips``)."""

    row = conn.execute(
        "SELECT id FROM trips WHERE service_date = ? AND train_number = ? "
        "AND (start_eva IS ? OR start_eva = ?) "
        "AND (dest_eva IS ? OR dest_eva = ?) LIMIT 1",
        (
            service_date,
            train_number,
            start_eva,
            start_eva,
            dest_eva,
            dest_eva,
        ),
    ).fetchone()
    return int(row["id"]) if row is not None else None


def _insert_trip(
    conn: sqlite3.Connection,
    *,
    service_date: str,
    train_number: str,
    start_eva: int | None,
    dest_eva: int | None,
    planned_dep: str | None,
    actual_dep: str | None,
    planned_arr: str | None,
    actual_arr: str | None,
    delay_min_arr: int | None,
    category: str | None,
    line: str | None,
    evu: str | None,
    trip_ref: str | None,
    data_source: DataSource,
    data_quality: DataQuality,
    reconstructed_at: str | None,
) -> int:
    cur = conn.execute(
        "INSERT INTO trips("
        "service_date, planned_dep, planned_arr, actual_dep, actual_arr, "
        "delay_min_arr, start_eva, dest_eva, category, line, train_number, "
        "evu, trip_ref, data_source, data_quality, reconstructed_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            service_date,
            planned_dep,
            planned_arr,
            actual_dep,
            actual_arr,
            delay_min_arr,
            start_eva,
            dest_eva,
            category,
            line,
            train_number,
            evu,
            trip_ref,
            data_source.value,
            data_quality.value,
            reconstructed_at,
        ),
    )
    return _lastrowid(cur)


# ----------------------------------------------------------- Anlegen
@dataclass
class AddCaseInput:
    """Eingabe-Werte für :func:`add_case`.

    Wird sowohl von der manuellen ``add``-CLI als auch vom
    Connector-gestützten ``trip``-Pfad gefüllt — die Validierung passiert
    zentral in :func:`add_case`.
    """

    service_date: str
    train_number: str
    start_eva: int
    dest_eva: int
    ticket_type: str | TicketType
    reason_text: str
    expected_cents: int = 0
    # Optional / mit Defaults
    ticket_class: int | None = None
    planned_dep: str | None = None
    actual_dep: str | None = None
    planned_arr: str | None = None
    actual_arr: str | None = None
    delay_min_arr: int | None = None
    line: str | None = None
    evu: str | None = None
    category: str | None = None
    evidence_path: str | None = None
    cap_applied: int | None = None
    notes: str | None = None
    data_source: DataSource = DataSource.MANUAL
    data_quality: DataQuality = DataQuality.MANUAL_ONLY
    trip_ref: str | None = None
    reconstructed_at: str | None = None
    force: bool = False  # Duplikat trotzdem erlauben (überschreibt Original nicht)


@dataclass
class AddCaseResult:
    """Rückgabe-Werte von :func:`add_case`."""

    case_id: int
    trip_id: int
    dup_hash: str
    created_at: str
    updated_at: str
    was_duplicate: bool = False  # True bei ``force``-Override
    original_id: int | None = None  # gesetzt, wenn ``was_duplicate``


def add_case(conn: sqlite3.Connection, payload: AddCaseInput) -> AddCaseResult:
    """Legt einen neuen Fall an.

    Reihenfolge:
      1. Eingaben validieren (Pflichtfelder, EVA-Nummern).
      2. Dup-Hash berechnen. Bereits vorhanden → ``DuplicateCaseError``
         (außer ``payload.force``).
      3. Trip finden oder anlegen (UNIQUE auf trips).
      4. Case einfügen + initialen Status ``offen`` setzen.

    Wirft:
      * ``ValueError`` bei ungültiger Eingabe (→ Exit 3).
      * ``DuplicateCaseError`` bei Duplikat (→ Exit 1).
    """

    # 1) Validierung — minimal, der Rest fällt Pydantic in der CLI zu.
    if not payload.service_date or not payload.train_number:
        raise ValueError(
            "service_date und train_number sind Pflichtfelder"
        )
    _validate_eva(payload.start_eva, name="start_eva")
    _validate_eva(payload.dest_eva, name="dest_eva")
    if not payload.reason_text:
        raise ValueError("reason_text ist ein Pflichtfeld (F1)")
    if payload.expected_cents < 0:
        raise ValueError("expected_cents darf nicht negativ sein")
    try:
        ticket_type = TicketType(payload.ticket_type)
    except ValueError as exc:
        raise ValueError(
            f"Unbekannter ticket_type {payload.ticket_type!r}; "
            f"erlaubt: {', '.join(t.value for t in TicketType)}"
        ) from exc
    if payload.ticket_class is not None and payload.ticket_class not in (1, 2):
        raise ValueError(
            f"ticket_class muss 1 oder 2 sein, nicht {payload.ticket_class!r}"
        )

    # 2) Duplikat-Hash.
    norm_date = _normalise_date(payload.service_date)
    dup_hash = compute_dup_hash(
        service_date=norm_date,
        train_number=payload.train_number,
        start_eva=payload.start_eva,
        dest_eva=payload.dest_eva,
        planned_dep=payload.planned_dep,
    )
    existing = find_case_by_hash(conn, dup_hash)
    was_duplicate = False
    original_id: int | None = None
    if existing is not None and not payload.force:
        raise DuplicateCaseError(original_id=existing[0], dup_hash=dup_hash)

    # 3) Trip.
    now_iso = _now_iso()
    # Stations implizit anlegen — FK-Constraint in §4 erzwingt sonst einen Crash.
    _ensure_station(conn, payload.start_eva)
    _ensure_station(conn, payload.dest_eva)
    trip_id = _find_trip(
        conn,
        service_date=norm_date,
        train_number=payload.train_number,
        start_eva=payload.start_eva,
        dest_eva=payload.dest_eva,
    )
    if trip_id is None:
        trip_id = _insert_trip(
            conn,
            service_date=norm_date,
            train_number=payload.train_number,
            start_eva=payload.start_eva,
            dest_eva=payload.dest_eva,
            planned_dep=payload.planned_dep,
            actual_dep=payload.actual_dep,
            planned_arr=payload.planned_arr,
            actual_arr=payload.actual_arr,
            delay_min_arr=payload.delay_min_arr,
            category=payload.category,
            line=payload.line,
            evu=payload.evu,
            trip_ref=payload.trip_ref,
            data_source=payload.data_source,
            data_quality=payload.data_quality,
            reconstructed_at=(
                payload.reconstructed_at
                if payload.data_source != DataSource.MANUAL
                else None
            ),
        )

    # 4) Case einfügen ODER (force + Duplikat) überschreiben.
    if existing is not None and payload.force:
        existing_id = existing[0]
        conn.execute(
            "UPDATE cases SET "
            "trip_id = ?, ticket_type = ?, ticket_class = ?, "
            "reason_text = ?, evidence_path = ?, expected_cents = ?, "
            "cap_applied = ?, notes = ?, dup_hash = ?, updated_at = ? "
            "WHERE id = ?",
            (
                trip_id,
                ticket_type.value,
                payload.ticket_class,
                payload.reason_text,
                payload.evidence_path,
                payload.expected_cents,
                payload.cap_applied if payload.cap_applied is not None else 0,
                payload.notes,
                dup_hash,
                now_iso,
                existing_id,
            ),
        )
        case_id = existing_id
        was_duplicate = True
        original_id = existing_id
    else:
        cur = conn.execute(
            "INSERT INTO cases("
            "trip_id, ticket_type, ticket_class, reason_text, evidence_path, "
            "expected_cents, cap_applied, notes, dup_hash, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                trip_id,
                ticket_type.value,
                payload.ticket_class,
                payload.reason_text,
                payload.evidence_path,
                payload.expected_cents,
                payload.cap_applied if payload.cap_applied is not None else 0,
                payload.notes,
                dup_hash,
                now_iso,
                now_iso,
            ),
        )
        case_id = _lastrowid(cur)

    # Status-Eintrag nur bei echtem INSERT (nicht beim Überschreiben).
    if not was_duplicate:
        conn.execute(
            "INSERT INTO case_status(case_id, status, at, note) "
            "VALUES (?, ?, ?, ?)",
            (
                case_id,
                CaseStatusName.OFFEN.value,
                now_iso,
                "angelegt",
            ),
        )

    conn.commit()
    return AddCaseResult(
        case_id=case_id,
        trip_id=trip_id,
        dup_hash=dup_hash,
        created_at=now_iso,
        updated_at=now_iso,
        was_duplicate=was_duplicate,
        original_id=original_id,
    )


# ----------------------------------------------------------- Liste
def _case_to_dict(
    row: sqlite3.Row,
    *,
    current_status: str,
    days_late: int | None = None,
) -> dict[str, Any]:
    """DB-Row → stabiles Dict für ``--json`` und Tests."""

    out: dict[str, Any] = {
        "id": int(row["case_id"]),
        "trip_id": int(row["trip_id"]),
        "service_date": row["service_date"],
        "train_number": row["train_number"],
        "line": row["line"],
        "category": row["category"],
        "evu": row["evu"],
        "start_eva": row["start_eva"],
        "dest_eva": row["dest_eva"],
        "delay_min_arr": row["delay_min_arr"],
        "ticket_type": row["ticket_type"],
        "ticket_class": row["ticket_class"],
        "reason_text": row["reason_text"],
        "expected_cents": int(row["expected_cents"]),
        "cap_applied": int(row["cap_applied"] or 0),
        "current_status": current_status,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if days_late is not None:
        out["days_late"] = days_late
    return out


def list_cases(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    since: str | None = None,
    until: str | None = None,
    line: str | None = None,
) -> list[dict[str, Any]]:
    """Liefert eine Liste von Fällen, sortiert nach service_date DESC.

    Join mit ``trips`` und dem jeweils aktuellsten Status aus
    ``case_status``. Optionale Filter: ``status`` (z. B. "offen"),
    ``since``/``until`` (ISO-Datum, inklusive), ``line`` (exakter Match).
    """

    clauses: list[str] = []
    params: list[Any] = []
    joins = (
        "FROM cases c "
        "JOIN trips t ON c.trip_id = t.id "
        "LEFT JOIN case_status cs ON cs.case_id = c.id "
        "AND cs.at = (SELECT MAX(at) FROM case_status WHERE case_id = c.id)"
    )

    if status:
        clauses.append("cs.status = ?")
        params.append(status)
    if since:
        clauses.append("t.service_date >= ?")
        params.append(_normalise_date(since))
    if until:
        clauses.append("t.service_date <= ?")
        params.append(_normalise_date(until))
    if line:
        clauses.append("t.line = ?")
        params.append(line)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        "SELECT c.id AS case_id, c.trip_id, c.ticket_type, c.ticket_class, "
        "c.reason_text, c.expected_cents, c.cap_applied, c.created_at, "
        "c.updated_at, "
        "t.service_date, t.train_number, t.line, t.category, t.evu, "
        "t.start_eva, t.dest_eva, t.delay_min_arr, "
        "cs.status AS current_status "
        f"{joins} {where} "
        "ORDER BY t.service_date DESC, c.id DESC",
        params,
    ).fetchall()

    return [
        _case_to_dict(
            row,
            current_status=row["current_status"] or CaseStatusName.OFFEN.value,
        )
        for row in rows
    ]


def sum_cases(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Berechnet die Summenzeile für die ``list``-Ausgabe.

    Cent-Beträge werden in Euro umgerechnet, damit die Tabelle lesbar
    bleibt (``--json`` behält die Cents als int).
    """

    total_cents = sum(int(c["expected_cents"]) for c in cases)
    return {
        "count": len(cases),
        "total_cents": total_cents,
        "total_eur": total_cents / 100.0,
    }


# ----------------------------------------------------------- Show
def _status_history(conn: sqlite3.Connection, case_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT status, at, note FROM case_status "
        "WHERE case_id = ? ORDER BY at ASC",
        (case_id,),
    ).fetchall()
    return [
        {
            "status": row["status"],
            "at": row["at"],
            "note": row["note"],
        }
        for row in rows
    ]


def calc_compensation_for_case(
    conn: sqlite3.Connection,
    case_id: int,
    *,
    ticket_price_cents: int | None = None,
):
    """Berechnet Entschädigung für einen Fall inkl. Monats-Deckel (F5).

    Berücksichtigt alle **früheren** Fälle im selben Kalendermonat und
    Ticket-Typ (Reihenfolge: ``service_date``, ``case_id``).
    """

    from .config import DEFAULT_TICKET_PRICE_CENTS, get_config
    from .rules import CompensationResult, ReasonCode, compute_compensation

    if ticket_price_cents is None:
        raw = get_config(conn, "ticket_price_cents")
        if raw is None:
            ticket_price_cents = int(DEFAULT_TICKET_PRICE_CENTS)
        else:
            try:
                ticket_price_cents = int(raw)
            except ValueError:
                ticket_price_cents = int(DEFAULT_TICKET_PRICE_CENTS)

    rows = conn.execute(
        "SELECT c.id AS case_id, c.ticket_type, c.ticket_class, "
        "t.service_date, t.delay_min_arr "
        "FROM cases c JOIN trips t ON c.trip_id = t.id "
        "ORDER BY t.service_date ASC, c.id ASC"
    ).fetchall()

    saldo_by_type: dict[str, int] = {}
    target: sqlite3.Row | None = None
    for row in rows:
        cid = int(row["case_id"])
        if cid == case_id:
            target = row
            break
        tt = str(row["ticket_type"])
        delay = row["delay_min_arr"]
        try:
            delay_int = int(delay) if delay is not None else 0
        except (TypeError, ValueError):
            delay_int = 0
        klass = row["ticket_class"]
        try:
            klass_int = int(klass) if klass is not None else 2
        except (TypeError, ValueError):
            klass_int = 2
        prior = int(saldo_by_type.get(tt, 0))
        try:
            step = compute_compensation(
                tt,
                delay_int,
                ticket_price_cents=int(ticket_price_cents),
                travel_class=klass_int,
                monthly_total_cents=prior,
            )
        except ValueError:
            step = CompensationResult(
                cents=0,
                reason="Unbekannter ticket_type",
                reason_code=ReasonCode.UNKNOWN_TICKET,
                cap_applied=0,
                below_threshold=True,
            )
        saldo_by_type[tt] = prior + int(step.cents)

    if target is None:
        raise CaseNotFoundError(case_id)

    tt = str(target["ticket_type"])
    delay = target["delay_min_arr"]
    try:
        delay_int = int(delay) if delay is not None else 0
    except (TypeError, ValueError):
        delay_int = 0
    klass = target["ticket_class"]
    try:
        klass_int = int(klass) if klass is not None else 2
    except (TypeError, ValueError):
        klass_int = 2
    prior = int(saldo_by_type.get(tt, 0))
    return compute_compensation(
        tt,
        delay_int,
        ticket_price_cents=int(ticket_price_cents),
        travel_class=klass_int,
        monthly_total_cents=prior,
    )


def show_case(conn: sqlite3.Connection, case_id: int) -> dict[str, Any]:
    """Liefert den vollständigen Fall-Datensatz.

    Enthält Trip-Felder, aktuellen Status, komplette Status-Historie
    und den ``expected_cents``-Hinweis aus §3 F4 ("berechneter
    Erwartungsbetrag und Begründung der Regel"). Schritt 5 ersetzt die
    statische Begründung durch die echte Regel-Auswertung.

    Wirft :class:`CaseNotFoundError`, wenn die ID nicht existiert.
    """

    row = conn.execute(
        "SELECT c.id AS case_id, c.trip_id, c.ticket_type, c.ticket_class, "
        "c.reason_text, c.evidence_path, c.expected_cents, c.cap_applied, "
        "c.notes, c.dup_hash, c.created_at, c.updated_at, "
        "t.service_date, t.train_number, t.line, t.category, t.evu, "
        "t.planned_dep, t.actual_dep, t.planned_arr, t.actual_arr, "
        "t.delay_min_arr, t.start_eva, t.dest_eva, t.data_source, "
        "t.data_quality, t.trip_ref, t.reconstructed_at "
        "FROM cases c JOIN trips t ON c.trip_id = t.id "
        "WHERE c.id = ?",
        (case_id,),
    ).fetchone()
    if row is None:
        raise CaseNotFoundError(case_id)

    # Aktueller Status = jüngster case_status-Eintrag.
    cur = conn.execute(
        "SELECT status, at FROM case_status WHERE case_id = ? "
        "ORDER BY at DESC LIMIT 1",
        (case_id,),
    ).fetchone()
    current_status = cur["status"] if cur else CaseStatusName.OFFEN.value

    history = _status_history(conn, case_id)
    base = _case_to_dict(row, current_status=current_status)
    base.update(
        {
            "evidence_path": row["evidence_path"],
            "notes": row["notes"],
            "dup_hash": row["dup_hash"],
            "planned_dep": row["planned_dep"],
            "actual_dep": row["actual_dep"],
            "planned_arr": row["planned_arr"],
            "actual_arr": row["actual_arr"],
            "data_source": row["data_source"],
            "data_quality": row["data_quality"],
            "trip_ref": row["trip_ref"],
            "reconstructed_at": row["reconstructed_at"],
            "status_history": history,
        }
    )
    if row["delay_min_arr"] is not None:
        comp = calc_compensation_for_case(conn, case_id)
        base["expected_cents"] = int(comp.cents)
        base["expected_reason"] = comp.reason
        base["compensation"] = comp.to_dict()
    else:
        base["expected_reason"] = _default_expected_reason(
            int(row["expected_cents"]), row["ticket_type"]
        )
    return base


def _default_expected_reason(expected_cents: int, ticket_type: str) -> str:
    """Platzhalter-Begründung — Schritt 5 ersetzt sie durch die echte Regel."""

    if expected_cents <= 0:
        return "Betrag 0 — wird in Schritt 5 durch compute_compensation ersetzt."
    eur = expected_cents / 100.0
    return (
        f"Vorläufige Annahme: {eur:.2f} € ({ticket_type}); "
        "Schritt 5 berechnet den Betrag nach §8."
    )


# ----------------------------------------------------------- Re-export
__all__ = [
    "AddCaseInput",
    "AddCaseResult",
    "BERLIN",
    "CaseNotFoundError",
    "DuplicateCaseError",
    "compute_dup_hash",
    "find_case_by_hash",
    "add_case",
    "calc_compensation_for_case",
    "list_cases",
    "show_case",
    "sum_cases",
]