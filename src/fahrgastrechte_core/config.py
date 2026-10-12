"""Konfigurations-Schicht für das Verspätungskonto (ANFORDERUNGEN §4, F10).

Schlüssel laut §4-Spezifikation:

* Stammdaten: ``name``, ``iban``, ``address``
* Ticket: ``ticket_type``, ``ticket_price_cents``, ``ticket_issuing_evu``,
  ``dticket_id``, ``bahncard_no``, ``evu_for_claims``
* API-Credentials: ``db_client_id``, ``db_api_key`` (besonders sensibel)

Speicherort
-----------

Standardmäßig liegen alle Werte in der ``config``-Tabelle der SQLite-DB
(plain text, Datei ``chmod 0600``). Für **besonders sensible** Schlüssel
kann zusätzlich das OS-Keyring genutzt werden — gesteuert über die
Umgebungsvariable ``FAHRGASTRECHTE_KEYRING=1``.

In Tests ohne interaktiven Keyring funktioniert der Round-Trip über die
DB-Tabelle. Der Keyring-Pfad wird nur dann angesprochen, wenn die Env
gesetzt UND das ``keyring``-Modul erfolgreich importierbar ist; in einem
Headless-Container ohne Backend wird der Aufruf stillschweigend
übersprungen (Best-Effort, es wird geloggt aber kein Crash).
"""

from __future__ import annotations

import logging
import os
import sqlite3
from typing import Any

_log = logging.getLogger(__name__)

# --------------------------------------------------------------- Konstanten
ENV_KEYRING = "FAHRGASTRECHTE_KEYRING"

# Schlüssel laut ANFORDERUNGEN §4.
CONFIG_KEYS: tuple[str, ...] = (
    # Stammdaten
    "name",
    "iban",
    "address",
    # Ticket
    "ticket_type",
    "ticket_price_cents",
    "ticket_issuing_evu",
    "dticket_id",
    "bahncard_no",
    "evu_for_claims",
    # API-Credentials
    "db_client_id",
    "db_api_key",
)

# Besonders sensible Schlüssel (zusätzliches Keyring-Backup, opt-in).
SENSITIVE_KEYS: frozenset[str] = frozenset(
    {"iban", "address", "db_client_id", "db_api_key"}
)

# Stammdaten-Defaults (Ticket-Typ + Monatspreis). Kommen aus ANFORDERUNGEN
# §8/§9 N7: D-Ticket = 49 € (regulär) bzw. 58 € (Solidarmodell).
DEFAULT_TICKET_TYPE = "d_ticket"
DEFAULT_TICKET_PRICE_CENTS = 4900  # 49,00 €; Solidarmodell wird via config überschrieben.


# --------------------------------------------------------------- Keyring
def _keyring_enabled() -> bool:
    return os.environ.get(ENV_KEYRING) == "1"


def _keyring_service() -> str:
    return "fahrgastrechte"


def _keyring_set(key: str, value: str) -> bool:
    """Schreibt ``key`` ins OS-Keyring. ``True`` bei Erfolg, ``False`` sonst.

    Best-Effort: scheitert der Import oder das Backend (z. B. headless
    Container), wird geloggt und ``False`` zurückgegeben. Die DB-Zeile
    bleibt die Quelle der Wahrheit, der Keyring-Eintrag ist nur ein
    optionales Plus.
    """

    if not _keyring_enabled() or key not in SENSITIVE_KEYS:
        return False
    try:
        import keyring  # type: ignore[import-not-found]  # noqa: PLC0415
        keyring.set_password(_keyring_service(), key, value)
        return True
    except Exception as exc:  # pragma: no cover - defensive
        _log.debug("Keyring-Schreiben für %s fehlgeschlagen: %s", key, exc)
        return False


def _keyring_get(key: str) -> str | None:
    if not _keyring_enabled() or key not in SENSITIVE_KEYS:
        return None
    try:
        import keyring  # type: ignore[import-not-found]  # noqa: PLC0415
        return keyring.get_password(_keyring_service(), key)
    except Exception as exc:  # pragma: no cover
        _log.debug("Keyring-Lesen für %s fehlgeschlagen: %s", key, exc)
        return None


# --------------------------------------------------------------- Public API
def get_config(
    conn: sqlite3.Connection, key: str, default: str | None = None
) -> str | None:
    """Liest einen Konfigurations-Wert. Reihenfolge: DB → Keyring → Default.

    ``default`` ist der Rückgabewert, wenn weder DB noch Keyring einen
    Eintrag liefern.
    """

    if key not in CONFIG_KEYS:
        # Unbekannte Keys werden ignoriert, kein Crash — verhindert stilles
        # Mitschleppen von Tippfehlern in Skripten.
        return default
    row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
    if row is not None:
        return row["value"]
    kr_value = _keyring_get(key)
    if kr_value is not None:
        return kr_value
    return default


def set_config(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Schreibt (oder überschreibt) ``key`` in DB + optional Keyring.

    Wirft ``ValueError`` bei unbekanntem Schlüssel — dies ist eine
    Programmier-/Eingabe-Fehlerklasse (Exit 3 in der CLI).
    """

    if key not in CONFIG_KEYS:
        raise ValueError(
            f"Unbekannter config-key: {key!r}. Erlaubt: {', '.join(CONFIG_KEYS)}"
        )
    conn.execute(
        "INSERT INTO config(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
    _keyring_set(key, value)


def delete_config(conn: sqlite3.Connection, key: str) -> bool:
    """Entfernt ``key`` aus DB und Keyring. ``True`` falls etwas entfernt wurde."""

    if key not in CONFIG_KEYS:
        return False
    cur = conn.execute("DELETE FROM config WHERE key = ?", (key,))
    conn.commit()
    removed = cur.rowcount > 0
    if _keyring_enabled():
        try:
            import keyring  # type: ignore[import-not-found]  # noqa: PLC0415
            keyring.delete_password(_keyring_service(), key)
        except Exception:  # pragma: no cover - defensive
            pass
    return removed


def list_config(conn: sqlite3.Connection) -> dict[str, str]:
    """Liefert alle DB-seitig gespeicherten Werte als Dict (sortiert)."""

    rows = conn.execute("SELECT key, value FROM config ORDER BY key").fetchall()
    return {row["key"]: row["value"] for row in rows}


# ----------------------------------------------------- Stammdaten-Defaults
def is_known_key(key: str) -> bool:
    return key in CONFIG_KEYS


def ticket_defaults() -> dict[str, str]:
    """Vorgeschlagene Defaults für die Ticket-Konfig."""

    return {
        "ticket_type": DEFAULT_TICKET_TYPE,
        "ticket_price_cents": str(DEFAULT_TICKET_PRICE_CENTS),
    }


def to_dict_payload(values: dict[str, str]) -> dict[str, Any]:
    """Stabile JSON-Form: ``ticket_price_cents`` als int, Rest als str.

    n. v. in dieser Stufe — vorbereitet für ``--json`` in Schritt ≥ 3
    (``fahrgastrechte config list --json``). Hier zentral, damit alle
    Ausgabe-Pfade einheitlich sind.
    """

    out: dict[str, Any] = {}
    for key, value in sorted(values.items()):
        if key == "ticket_price_cents":
            try:
                out[key] = int(value)
            except (TypeError, ValueError):
                out[key] = value
        else:
            out[key] = value
    return out


__all__ = [
    "ENV_KEYRING",
    "CONFIG_KEYS",
    "SENSITIVE_KEYS",
    "DEFAULT_TICKET_TYPE",
    "DEFAULT_TICKET_PRICE_CENTS",
    "get_config",
    "set_config",
    "delete_config",
    "list_config",
    "is_known_key",
    "ticket_defaults",
    "to_dict_payload",
]
