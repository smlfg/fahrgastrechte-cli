"""Tests für fahrgastrechte_core.config (ANFORDERUNGEN §4 + F10)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterator

import pytest

from fahrgastrechte_core import config as fc_config, db


# ------------------------------------------------------------ Hilfs-Fixture
@pytest.fixture
def fresh_db(tmp_path: Path) -> Iterator[Path]:
    """Initialisierte DB, bereit für Tests."""

    target = tmp_path / "konto.db"
    db.init_db(target)
    yield target


@pytest.fixture
def opened(fresh_db: Path) -> Iterator[sqlite3.Connection]:
    """Geöffnete Connection innerhalb des Context-Managers."""

    with db.connect(fresh_db) as conn:
        yield conn


# ----------------------------------------------------- Round-Trip
def test_set_then_get_returns_same_value(opened: sqlite3.Connection) -> None:
    fc_config.set_config(opened, "name", "Samuel")
    assert fc_config.get_config(opened, "name") == "Samuel"


def test_get_unknown_key_returns_default(opened: sqlite3.Connection) -> None:
    assert fc_config.get_config(opened, "iban") is None
    assert fc_config.get_config(opened, "iban", default="") == ""


def test_set_overwrites_existing_value(opened: sqlite3.Connection) -> None:
    """``INSERT ... ON CONFLICT DO UPDATE`` überschreibt sauber."""

    fc_config.set_config(opened, "name", "V1")
    fc_config.set_config(opened, "name", "V2")
    assert fc_config.get_config(opened, "name") == "V2"


def test_set_config_rejects_unknown_key(opened: sqlite3.Connection) -> None:
    """Falsche Schlüssel sind Eingabe-Fehler (Exit 3 in der CLI)."""

    with pytest.raises(ValueError):
        fc_config.set_config(opened, "not-a-key", "x")


def test_get_config_ignores_unknown_key(opened: sqlite3.Connection) -> None:
    """Unbekannter Key beim Lesen: Default, kein Crash."""

    assert fc_config.get_config(opened, "not-a-key") is None
    assert fc_config.get_config(opened, "not-a-key", default="42") == "42"


# ----------------------------------------------------- Listen + Löschen
def test_list_returns_sorted_dict(opened: sqlite3.Connection) -> None:
    """``list_config`` ist deterministisch sortiert."""

    fc_config.set_config(opened, "name", "Samuel")
    fc_config.set_config(opened, "iban", "DE49...")
    out = fc_config.list_config(opened)
    assert list(out.keys()) == sorted(out.keys())
    assert out["iban"] == "DE49..."
    assert out["name"] == "Samuel"


def test_list_empty_when_nothing_set(opened: sqlite3.Connection) -> None:
    assert fc_config.list_config(opened) == {}


def test_delete_removes_value(opened: sqlite3.Connection) -> None:
    fc_config.set_config(opened, "iban", "DE...")
    assert fc_config.delete_config(opened, "iban") is True
    assert fc_config.get_config(opened, "iban") is None
    # Idempotent: zweites Löschen → False, kein Crash.
    assert fc_config.delete_config(opened, "iban") is False


def test_delete_unknown_key_returns_false(opened: sqlite3.Connection) -> None:
    assert fc_config.delete_config(opened, "not-a-key") is False


# ----------------------------------------------------- Stammdaten
def test_all_spec_config_keys_are_known() -> None:
    """Die in §4 aufgelisteten Schlüssel sind alle in :data:`CONFIG_KEYS`."""

    expected = {
        "name",
        "iban",
        "address",
        "ticket_type",
        "ticket_price_cents",
        "ticket_issuing_evu",
        "dticket_id",
        "bahncard_no",
        "evu_for_claims",
        "db_client_id",
        "db_api_key",
    }
    assert expected <= set(fc_config.CONFIG_KEYS)


def test_sensitive_keys_are_subset() -> None:
    """SENSITIVE_KEYS ⊂ CONFIG_KEYS (Schlüssel-konsistent)."""

    assert fc_config.SENSITIVE_KEYS <= set(fc_config.CONFIG_KEYS)


def test_ticket_defaults_are_d_ticket() -> None:
    """Default-Ticket ist D-Ticket zum aktuellen Monatspreis (49 €)."""

    defaults = fc_config.ticket_defaults()
    assert defaults["ticket_type"] == "d_ticket"
    assert defaults["ticket_price_cents"] == "4900"


def test_to_dict_payload_coerces_cents() -> None:
    """``ticket_price_cents`` wird in int umgewandelt."""

    payload = fc_config.to_dict_payload({"ticket_price_cents": "4900", "name": "x"})
    assert payload["ticket_price_cents"] == 4900
    assert payload["name"] == "x"
    assert isinstance(payload["ticket_price_cents"], int)


def test_to_dict_payload_invalid_cents_stays_string() -> None:
    """Nicht-numerische Cent-Werte bleiben Strings (keine Exception)."""

    payload = fc_config.to_dict_payload({"ticket_price_cents": "n/a"})
    assert payload["ticket_price_cents"] == "n/a"


# ----------------------------------------------------- Keyring-Pfad
class FakeKeyring:
    """In-Memory-Keyring-Backend für Tests."""

    def __init__(self) -> None:
        self.storage: dict[str, dict[str, str]] = {}

    def set_password(self, service: str, key: str, value: str) -> None:
        self.storage.setdefault(service, {})[key] = value

    def get_password(self, service: str, key: str) -> str | None:
        return self.storage.get(service, {}).get(key)

    def delete_password(self, service: str, key: str) -> None:
        self.storage.get(service, {}).pop(key, None)


@pytest.fixture
def fake_keyring(monkeypatch: pytest.MonkeyPatch) -> FakeKeyring:
    """Tauscht das Keyring-Backend gegen ein In-Memory-Modell aus."""

    fake = FakeKeyring()
    import keyring as kr_module  # noqa: PLC0415

    monkeypatch.setattr(kr_module, "set_password", fake.set_password)
    monkeypatch.setattr(kr_module, "get_password", fake.get_password)
    monkeypatch.setattr(kr_module, "delete_password", fake.delete_password)
    return fake


def test_keyring_disabled_by_default_does_not_touch_store(
    opened: sqlite3.Connection,
) -> None:
    """Ohne ``FAHRGASTRECHTE_KEYRING=1`` läuft alles über die DB-Tabelle."""

    import keyring as kr_module  # noqa: PLC0415

    # Sicherheitshalber: Backend inkl. set_password als Attr prüfen …
    # Der eigentliche Punkt ist: das Tool ruft ``keyring.*`` gar nicht auf.
    calls: list[str] = []

    def spy(*_args: object, **_kwargs: object) -> None:
        calls.append("set")

    monkeypatch_patched = pytest.MonkeyPatch()
    monkeypatch_patched.setattr(kr_module, "set_password", spy)
    try:
        fc_config.set_config(opened, "iban", "DE123")
    finally:
        monkeypatch_patched.undo()
    assert calls == []  # kein Keyring-Zugriff
    assert fc_config.get_config(opened, "iban") == "DE123"


def test_keyring_enabled_writes_sensitive_keys(
    monkeypatch: pytest.MonkeyPatch,
    opened: sqlite3.Connection,
    fake_keyring: FakeKeyring,
) -> None:
    """Mit ``FAHRGASTRECHTE_KEYRING=1`` werden sensible Keys zusätzlich geschrieben."""

    monkeypatch.setenv("FAHRGASTRECHTE_KEYRING", "1")
    fc_config.set_config(opened, "iban", "DE-secret")
    fc_config.set_config(opened, "name", "Samuel")  # nicht sensitiv → nicht ins Keyring
    assert fake_keyring.storage.get("fahrgastrechte", {}).get("iban") == "DE-secret"
    assert fake_keyring.storage.get("fahrgastrechte", {}).get("name") is None


def test_keyring_fallback_for_missing_db_value(
    monkeypatch: pytest.MonkeyPatch,
    opened: sqlite3.Connection,
    fake_keyring: FakeKeyring,
) -> None:
    """Fehlt der Wert in der DB, fällt ``get_config`` auf das Keyring zurück."""

    monkeypatch.setenv("FAHRGASTRECHTE_KEYRING", "1")
    fake_keyring.set_password("fahrgastrechte", "db_api_key", "kr-only")
    # DB hat nichts.
    assert fc_config.get_config(opened, "db_api_key") == "kr-only"


def test_keyring_import_failure_is_swallowed(
    monkeypatch: pytest.MonkeyPatch,
    opened: sqlite3.Connection,
) -> None:
    """Wenn keyring-Modul crasht: DB-Wert bleibt lesbar, kein Hard-Fail."""

    # keyring.enabled + set_password werfen Exception → Best-Effort schluckt.
    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("simulated keyring failure")

    import keyring as kr_module  # noqa: PLC0415

    monkeypatch.setenv("FAHRGASTRECHTE_KEYRING", "1")
    monkeypatch.setattr(kr_module, "set_password", boom)
    monkeypatch.setattr(kr_module, "get_password", boom)
    # Schreiben in DB nicht blockiert, Lesen auch nicht:
    fc_config.set_config(opened, "iban", "x")
    assert fc_config.get_config(opened, "iban") == "x"


# ----------------------------------------------------- is_known_key
def test_is_known_key_distinguishes() -> None:
    assert fc_config.is_known_key("name") is True
    assert fc_config.is_known_key("garbage") is False
