"""Pfade und Standard-Speicherorte für das Verspätungskonto.

Standardpfad:
    ~/.local/share/fahrgastrechte/konto.db

Überschreibbar in Tests und Skripten via Umgebungsvariable
``FAHRGASTRECHTE_DB``. Dort darf entweder ein vollständiger Pfad (wird
bei Bedarf angelegt) oder ein Verzeichnis (mit angehängtem
``konto.db``) stehen.

Beim Anlegen wird das Zielverzeichnis mit ``chmod 0o700`` und die
Datenbank mit ``chmod 0o600`` versehen — siehe ANFORDERUNGEN §9 N1
(DSGVO/Stammdaten).
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_DB = "FAHRGASTRECHTE_DB"
APP_DIR_NAME = "fahrgastrechte"
DB_FILE_NAME = "konto.db"
CONFIG_DIR_NAME = "fahrgastrechte"  # ~/.config/fahrgastrechte (für Stammdaten-Datei, Phase 2)

_DEFAULT_DIR = Path.home() / ".local" / "share" / APP_DIR_NAME
_DEFAULT_DB = _DEFAULT_DIR / DB_FILE_NAME


def default_db_path() -> Path:
    """Liefert den Default-DB-Pfad (Anlegen wird nicht erzwungen).

    Override-Reihenfolge:
        1. ``FAHRGASTRECHTE_DB`` Umgebungsvariable
        2. ``~/.local/share/fahrgastrechte/konto.db``
    """

    override = os.environ.get(ENV_DB)
    if override:
        path = Path(override).expanduser()
        if path.is_dir() or str(path).endswith(("/", os.sep)):
            return path / DB_FILE_NAME
        return path
    return _DEFAULT_DB


def ensure_private_dir(path: Path) -> Path:
    """Legt das DB-Verzeichnis an und setzt ``0o700`` (Linux/macOS).

    Existiert das Verzeichnis schon, wird der Modus nur dann nachgezogen,
    wenn die Datei unsicherer ist (z. B. Repos mit falscher Berechtigung).
    Auf Windows ohne POSIX-Modes wird der Schritt stillschweigend
    übersprungen.
    """

    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except (PermissionError, NotImplementedError, OSError):  # pragma: no cover
        pass
    return path


def ensure_private_file(path: Path) -> Path:
    """Erzeugt eine leere Datei mit ``0o600``-Modus (Linux/macOS).

    Existiert die Datei bereits, bleibt sie unangetastet; nur der Modus
    wird auf 0600 nachgezogen, wenn er weiter war.
    """

    path.touch(exist_ok=True)
    try:
        path.chmod(0o600)
    except (PermissionError, NotImplementedError, OSError):  # pragma: no cover
        pass
    return path


__all__ = [
    "ENV_DB",
    "DB_FILE_NAME",
    "default_db_path",
    "ensure_private_dir",
    "ensure_private_file",
]
