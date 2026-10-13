"""Kernbibliothek des Verspätungskontos.

Enthält Modelle, Storage (SQLite), Connectoren (Timetables-API, db-vendo),
Regeln (Entschädigungs-Berechnung) und Konfiguration. Die CLI ist eine dünne
Hülle über diesen Funktionen; der MCP-Server (``fahrgastrechte-mcp``) wird
dieselbe Bibliothek vendored.
"""

from . import cases, config, db, models, rules, stats
from .version import __version__

__all__ = [
    "__version__",
    "cases",
    "config",
    "db",
    "models",
    "rules",
    "stats",
]