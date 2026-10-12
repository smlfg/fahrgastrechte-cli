"""Tests für fahrgastrechte_core.db (ANFORDERUNGEN §4 + §13 Schritt 2).

Alle Tests laufen gegen ``tmp_path`` + die per Fixture gesetzte Env
``FAHRGASTRECHTE_DB``. Die globale User-DB wird nicht angefasst.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from fahrgastrechte_core import db


# Tabellen + Indizes, die laut §4 vorhanden sein müssen.
_EXPECTED_TABLES = (
    "meta",
    "config",
    "stations",
    "trips",
    "cases",
    "case_status",
)

_EXPECTED_INDEXES = (
    "idx_trips_service_date",
    "idx_trips_train_date",
    "idx_cases_trip_id",
    "idx_case_status_case_at",
)


# ----------------------------------------------------------- Schema-Inhalt
def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    return {r[0] if not hasattr(r, "keys") else r["name"] for r in rows}


def _indexes(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r[0] if not hasattr(r, "keys") else r["name"] for r in rows}


def test_init_creates_db_file_and_schema(tmp_path: Path) -> None:
    """``init_db`` legt die Datei an, befüllt ``meta(schema_version)=1``."""

    target = tmp_path / "fresh.db"
    assert not target.exists()
    result = db.init_db(target)
    assert result == target
    assert target.exists(), "init_db muss Datei erzeugen"

    schema = db.get_schema_version(target)
    assert schema == db.SCHEMA_VERSION == 1


def test_init_is_idempotent(tmp_path: Path) -> None:
    """Mehrfaches ``init_db`` schreibt kein zweites Schema, kein Crash."""

    target = tmp_path / "twice.db"
    db.init_db(target)
    with sqlite3.connect(target) as conn:
        rows_after_first = conn.execute("SELECT COUNT(*) FROM trips").fetchone()[0]
    db.init_db(target)
    with sqlite3.connect(target) as conn:
        rows_after_second = conn.execute("SELECT COUNT(*) FROM trips").fetchone()[0]
    assert rows_after_first == rows_after_second == 0


def test_init_creates_all_spec_tables(tmp_path: Path) -> None:
    """Tabellen laut ANFORDERUNGEN §4 sind alle da."""

    target = tmp_path / "schema.db"
    db.init_db(target)
    with sqlite3.connect(target) as conn:
        conn.row_factory = sqlite3.Row
        present = _tables(conn)
    missing = set(_EXPECTED_TABLES) - present
    assert not missing, f"fehlende Tabellen: {missing}"


def test_init_creates_all_spec_indexes(tmp_path: Path) -> None:
    """Indizes laut §4: trips(service_date), trips(train_number, service_date),
    cases(trip_id), case_status(case_id, at)."""

    target = tmp_path / "index.db"
    db.init_db(target)
    with sqlite3.connect(target) as conn:
        conn.row_factory = sqlite3.Row
        present = _indexes(conn)
    missing = set(_EXPECTED_INDEXES) - present
    assert not missing, f"fehlende Indizes: {missing}"


def test_schema_columns_match_spec(tmp_path: Path) -> None:
    """Spalten + Constraints exakt wie in §4."""

    target = tmp_path / "cols.db"
    db.init_db(target)
    with sqlite3.connect(target) as conn:
        conn.row_factory = sqlite3.Row

        # config(key PRIMARY KEY, value TEXT)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(config)")}
        assert {"key", "value"} <= cols
        # stations(eva_no PK, name NOT NULL, ds100, lat, lon, source)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(stations)")}
        assert {"eva_no", "name", "ds100", "lat", "lon", "source"} <= cols
        # trips + UNIQUE-Constraint
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(trips)")}
        assert {
            "id",
            "service_date",
            "planned_dep",
            "planned_arr",
            "actual_dep",
            "actual_arr",
            "delay_min_arr",
            "start_eva",
            "dest_eva",
            "category",
            "line",
            "train_number",
            "evu",
            "trip_ref",
            "data_source",
            "data_quality",
            "reconstructed_at",
        } <= cols
        # cases(id, trip_id FK CASCADE, expected_cents NOT NULL, ...)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(cases)")}
        assert {
            "id",
            "trip_id",
            "ticket_type",
            "ticket_class",
            "reason_text",
            "evidence_path",
            "expected_cents",
            "cap_applied",
            "notes",
            "created_at",
            "updated_at",
        } <= cols
        # case_status-PK (case_id, status, at)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(case_status)")}
        assert {"case_id", "status", "at", "note"} <= cols


def test_meta_has_schema_version_one(tmp_path: Path) -> None:
    """meta-Schema-Version == 1 nach init."""

    target = tmp_path / "meta.db"
    db.init_db(target)
    with sqlite3.connect(target) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT schema_version FROM meta").fetchone()
    assert row is not None
    assert row["schema_version"] == 1


# --------------------------------------------------------- Migration
def test_migrate_from_empty_db_to_v1(tmp_path: Path) -> None:
    """Migration: leere 0-Byte-Datei → v1 Schema ist OK (idempotent zu init_db)."""

    target = tmp_path / "v0.db"
    target.touch()  # leere Datei
    db.init_db(target)
    assert db.get_schema_version(target) == 1
    with sqlite3.connect(target) as conn:
        present = _tables(conn)
    assert set(_EXPECTED_TABLES) <= present


def test_ensure_initialized_raises_before_init(tmp_path: Path) -> None:
    """Vor ``init_db`` muss ``ensure_initialized`` Exit-2-Signal werfen."""

    target = tmp_path / "missing.db"
    assert not target.exists()
    with pytest.raises(db.NotInitializedError):
        db.ensure_initialized(target)


def test_ensure_initialized_ok_after_init(tmp_path: Path) -> None:
    """Nach ``init_db`` gibt ``ensure_initialized`` den Pfad zurück."""

    target = tmp_path / "ready.db"
    db.init_db(target)
    assert db.ensure_initialized(target) == target


def test_init_file_modes_are_private(tmp_path: Path) -> None:
    """DB-Datei und Verzeichnis werden mit restriktiven Modi angelegt."""

    target = tmp_path / "private" / "konto.db"
    db.init_db(target)
    # POSIX-Mode-Check — Plattform-spezifisch (Windows würde anders aussehen,
    # aber Tests laufen auf Linux).
    if target.parent.exists():
        mode_dir = target.parent.stat().st_mode & 0o777
        assert mode_dir & 0o077 == 0, f"Verzeichnis-Mode zu offen: {oct(mode_dir)}"
    mode_file = target.stat().st_mode & 0o777
    assert mode_file & 0o077 == 0, f"Datei-Mode zu offen: {oct(mode_file)}"


# --------------------------------------------------------- Connection
def test_open_db_sets_required_pragmas(tmp_path: Path) -> None:
    """PRAGMA foreign_keys + journal_mode werden gesetzt."""

    target = tmp_path / "pragma.db"
    db.init_db(target)
    conn = db.open_db(target)
    try:
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        jm = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert fk == 1, "Foreign-Keys sollten ON sein"
        # WAL ist Default, auf POSIX-Dateisystemen ist der Wert "wal"
        assert jm.lower() == "wal"
    finally:
        conn.close()


def test_connect_context_commits_and_rolls_back(tmp_path: Path) -> None:
    """``connect`` committet im Erfolgsfall, rollt im Fehlerfall zurück."""

    target = tmp_path / "tx.db"
    db.init_db(target)
    # Erfolg: Schreiben überlebt den with-Block.
    with db.connect(target) as c:
        c.execute(
            "INSERT INTO config(key, value) VALUES ('name', 'OK')"
        )
    with sqlite3.connect(target) as c:
        v = c.execute("SELECT value FROM config WHERE key='name'").fetchone()[0]
    assert v == "OK"

    # Fehler: Schleife wirft → kein Schreiben persistiert.
    with pytest.raises(RuntimeError):
        with db.connect(target) as c:
            c.execute(
                "INSERT INTO config(key, value) VALUES ('iban', 'X')"
            )
            raise RuntimeError("boom")
    with sqlite3.connect(target) as c:
        n = c.execute("SELECT COUNT(*) FROM config WHERE key='iban'").fetchone()[0]
    assert n == 0


def test_path_default_uses_env_override(tmp_path: Path) -> None:
    """``default_db_path`` greift auf ``FAHRGASTRECHTE_DB`` zurück
    (per autouse-Fixture gesetzt)."""

    from fahrgastrechte_core.paths import default_db_path, ENV_DB

    import os

    expected = os.environ[ENV_DB]
    assert default_db_path() == Path(expected)
