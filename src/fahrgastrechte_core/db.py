"""SQLite-Storage für das Verspätungskonto (ANFORDERUNGEN §4).

Speicherort: ``~/.local/share/fahrgastrechte/konto.db`` (per Env ``FAHRGASTRECHTE_DB``
überschreibbar). Schema-Version wird in ``meta(schema_version)`` gespeichert
und startet bei **1**.

Schema-Migration
----------------

Eine v0-DB ist eine Datei ohne Tabellen. Die einzige sinnvolle Migration
``v0 → v1`` ist daher das Anlegen des vollständigen Schemas. ``init_db``
ist **idempotent**: existiert bereits eine v1-DB (an ``meta.schema_version``
erkennbar), wird der Aufruf zur No-Op (kein Crash).

Exit-Code-Semantik
------------------

Diese Bibliothek wirft ``NotInitializedError``, wenn ein Lese-/Schreib-
Aufruf eine nicht initialisierte Datei antrifft. Die CLI übersetzt das in
**Exit 2** (ANFORDERUNGEN §3). Ungültige Eingaben lösen ``ValueError`` aus,
was Exit 3 ergibt.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Sequence

from .paths import default_db_path, ensure_private_dir, ensure_private_file


SCHEMA_VERSION = 1

# Sensible Konfigurationsschlüssel (für Keyring-Option in config.py).
# Die Werte landen immer auch in der ``config``-Tabelle; das Keyring ist
# ein optionales Sicherheits-Add-On (ANFORDERUNGEN §9 N1).
# Nicht abschließend — die endgültige Liste steht in config.py.


class NotInitializedError(RuntimeError):
    """Die DB-Datei existiert nicht oder hat kein gültiges Schema.

    Wird vom CLI in Exit-Code 2 übersetzt (ANFORDERUNGEN §3).
    """


class StorageError(RuntimeError):
    """Allgemeiner Storage-Fehler (z. B. kaputte Datei, falsche Berechtigung)."""


# ---------------------------------------------------------------- Schema
# ANFORDERUNGEN §4, Wortlaut und Feldnamen 1:1 übernommen.
_SCHEMA_SQL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS meta (
        schema_version INTEGER PRIMARY KEY
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS config (
        key   TEXT PRIMARY KEY,
        value TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stations (
        eva_no   INTEGER PRIMARY KEY,
        name     TEXT NOT NULL,
        ds100    TEXT,
        lat      REAL, lon REAL,
        source   TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS trips (
        id              INTEGER PRIMARY KEY,
        service_date    TEXT NOT NULL,
        planned_dep     TEXT, planned_arr TEXT,
        actual_dep      TEXT, actual_arr TEXT,
        delay_min_arr   INTEGER,
        start_eva       INTEGER REFERENCES stations(eva_no),
        dest_eva        INTEGER REFERENCES stations(eva_no),
        category        TEXT,
        line            TEXT,
        train_number    TEXT,
        evu             TEXT,
        trip_ref        TEXT,
        data_source     TEXT NOT NULL,
        data_quality    TEXT NOT NULL,
        reconstructed_at TEXT,
        UNIQUE(service_date, train_number, start_eva, dest_eva)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cases (
        id              INTEGER PRIMARY KEY,
        trip_id         INTEGER REFERENCES trips(id) ON DELETE CASCADE,
        ticket_type     TEXT NOT NULL,
        ticket_class    INTEGER,
        reason_text     TEXT,
        evidence_path   TEXT,
        expected_cents  INTEGER NOT NULL,
        cap_applied     INTEGER,
        notes           TEXT,
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS case_status (
        case_id    INTEGER REFERENCES cases(id) ON DELETE CASCADE,
        status     TEXT NOT NULL,
        at         TEXT NOT NULL,
        note       TEXT,
        PRIMARY KEY (case_id, status, at)
    )
    """,
)

# Indizes laut §4: trips(service_date), trips(train_number, service_date),
# cases(trip_id), case_status(case_id, at).
_INDEX_SQL: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_trips_service_date ON trips(service_date)",
    "CREATE INDEX IF NOT EXISTS idx_trips_train_date ON trips(train_number, service_date)",
    "CREATE INDEX IF NOT EXISTS idx_cases_trip_id ON cases(trip_id)",
    "CREATE INDEX IF NOT EXISTS idx_case_status_case_at ON case_status(case_id, at)",
)

# (Stations haben schon einen PK-Index über eva_no; explizite Indizes
# sind in §4 nicht gefordert.)


# ----------------------------------------------------------- Connection
def open_db(path: Path | None = None) -> sqlite3.Connection:
    """Öffnet die SQLite-DB an ``path`` (Default: :func:`paths.default_db_path`).

    * Verzeichnis + Datei werden bei Bedarf angelegt (mit restriktiven Modi).
    * ``row_factory = sqlite3.Row`` für bequemen Dict-artigen Zugriff.
    * ``PRAGMA foreign_keys = ON`` für die ``REFERENCES``-Constraints.
    * ``PRAGMA journal_mode = WAL`` für parallel-lesende Tools / Phase-2-MCP.

    Wenn ``ensure_private_file`` eine vorhandene Datei nicht antastet,
    wird sie hier dennoch geöffnet — das ist gewollt (kein Mod-Change
    stillschweigend auf genutzten Dateien).
    """

    resolved = (path or default_db_path()).expanduser()
    if resolved.is_dir() or str(resolved).endswith(("/", "\\")):
        resolved = resolved / "konto.db"
    ensure_private_dir(resolved.parent)
    ensure_private_file(resolved)
    conn = sqlite3.connect(str(resolved))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def connect(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """Context-Manager: öffnet + commit/rollback automatisch."""

    conn = open_db(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# -------------------------------------------------- Schema-Version / Init
def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'index')"
    ).fetchall()
    return {row["name"] for row in rows}


def _applied_schema_version(conn: sqlite3.Connection) -> int | None:
    """Gibt die Schema-Version aus ``meta.schema_version`` zurück, oder ``None``."""

    if "meta" not in _table_names(conn):
        return None
    row = conn.execute("SELECT schema_version FROM meta LIMIT 1").fetchone()
    if row is None:
        return None
    return int(row["schema_version"])


def _apply_schema(conn: sqlite3.Connection) -> None:
    """Legt alle Tabellen, Indizes und den ``meta``-Versionseintrag an."""

    for stmt in _SCHEMA_SQL:
        conn.execute(stmt)
    for stmt in _INDEX_SQL:
        conn.execute(stmt)
    # Version sicherstellen — idempotent durch INSERT OR IGNORE.
    conn.execute(
        "INSERT OR IGNORE INTO meta(schema_version) VALUES (?)", (SCHEMA_VERSION,)
    )
    conn.commit()


def init_db(path: Path | None = None) -> Path:
    """Initialisiert die DB an ``path`` (Default-Pfad).

    * Existiert die Datei nicht, wird sie samt Schema angelegt → frische v1-DB.
    * Existiert sie, aber ``meta`` zeigt eine andere Version (oder fehlt),
      wird :func:`migrate` aufgerufen.
    * Existiert sie und ist bereits v1 → No-Op (idempotent).

    Rückgabewert ist der tatsächlich verwendete DB-Pfad.
    """

    resolved = (path or default_db_path()).expanduser()
    ensure_private_dir(resolved.parent)
    ensure_private_file(resolved)
    with sqlite3.connect(str(resolved)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        existing = _applied_schema_version(conn)
        if existing == SCHEMA_VERSION:
            return resolved  # idempotent
        migrate(conn, existing)
    return resolved


def migrate(conn: sqlite3.Connection, from_version: int | None) -> None:
    """Wendet ausstehende Migrationen an.

    Der einzige Fall in Phase 1: ``None`` (leere/neue Datei) oder ``0``
    → v1. Höhere ``from_version``-Werte werden als Fehler gemeldet (kein
    silent downgrade).
    """

    if from_version == SCHEMA_VERSION:
        return  # bereits aktuell
    if from_version not in (None, 0):
        raise StorageError(
            f"DB-Schema-Version {from_version} ist neuer als unterstützte "
            f"Version {SCHEMA_VERSION}. Bitte Tool aktualisieren."
        )
    # v0 → v1: nichts zu übertragen, nur Schema anlegen.
    _apply_schema(conn)
    return None


def get_schema_version(path: Path | None = None) -> int | None:
    """Liest die Schema-Version ohne Schreiboperationen.

    Gibt ``None`` zurück, wenn die Datei / Tabelle nicht existiert
    (wird vom CLI als „nicht initialisiert" interpretiert, Exit 2).
    """

    resolved = (path or default_db_path()).expanduser()
    if not resolved.exists() or resolved.stat().st_size == 0:
        return None
    with sqlite3.connect(str(resolved)) as conn:
        conn.row_factory = sqlite3.Row
        return _applied_schema_version(conn)


def ensure_initialized(path: Path | None = None) -> Path:
    """Stellt sicher, dass die DB initialisiert ist, oder wirft
    :class:`NotInitializedError`."""

    resolved = (path or default_db_path()).expanduser()
    if get_schema_version(resolved) != SCHEMA_VERSION:
        raise NotInitializedError(
            f"DB nicht initialisiert unter {resolved}. "
            f"Lösung: `fahrgastrechte init` ausführen."
        )
    return resolved


# ------------------------------------------------- Sonstige Helfer (intern)
def _check_columns(table: str, expected: Sequence[str], conn: sqlite3.Connection) -> None:
    """Debug-Helfer: verifiziert die erwarteten Spalten — wird in Tests genutzt."""

    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    actual = {row["name"] for row in rows}
    missing = set(expected) - actual
    if missing:
        raise StorageError(
            f"Tabelle {table}: fehlende Spalten {sorted(missing)} (erwartet "
            f"{sorted(expected)})"
        )


__all__ = [
    "SCHEMA_VERSION",
    "NotInitializedError",
    "StorageError",
    "open_db",
    "connect",
    "init_db",
    "migrate",
    "get_schema_version",
    "ensure_initialized",
]
