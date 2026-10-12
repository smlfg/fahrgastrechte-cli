"""Pytest-Konfiguration für fahrgastrechte-cli.

Stellt sicher, dass ``src/`` auf dem Pfad liegt, ohne dass das Paket via
``uv sync`` installiert sein muss (hilft beim Edit-and-Run-Durchlauf).
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
