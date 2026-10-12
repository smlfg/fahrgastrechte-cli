"""Pytest-Konfiguration für fahrgastrechte-cli.

Stellt sicher, dass ``src/`` auf dem Pfad liegt, ohne dass das Paket via
``uv sync`` installiert sein muss (hilft beim Edit-and-Run-Durchlauf).

Schritt 2: Jeder Test bekommt eine eigene tmp-DB, damit die globale
``~/.local/share/fahrgastrechte/konto.db`` des Entwicklers nicht berührt
wird. Außerdem wird ``FAHRGASTRECHTE_KEYRING`` deaktiviert, sodass die
DB-Pfad-Tests keinen interaktiven OS-Keyring brauchen.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stellt ``FAHRGASTRECHTE_DB`` per Test auf eine eigene Datei.

    Setzt ``FAHRGASTRECHTE_KEYRING`` zurück, damit keine Testläufe
    versehentlich in das OS-Keyring des Host-Systems schreiben.
    """

    db_path = tmp_path / "konto.db"
    monkeypatch.setenv("FAHRGASTRECHTE_DB", str(db_path))
    # Default: Keyring aus. Tests, die es brauchen, setzen es selbst.
    monkeypatch.delenv("FAHRGASTRECHTE_KEYRING", raising=False)
    return db_path
