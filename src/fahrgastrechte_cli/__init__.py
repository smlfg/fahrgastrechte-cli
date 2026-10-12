"""CLI-Hülle für das Verspätungskonto.

Die Typer-App liegt in :mod:`fahrgastrechte_cli.main`. Sämtliche Logik
(Speicherung, Connectoren, Regeln) steckt in :mod:`fahrgastrechte_core`;
dieses Paket enthält nur Prompts, Ausgabe und Exit-Code-Abbildung.
"""

from fahrgastrechte_core import __version__

__all__ = ["__version__"]
