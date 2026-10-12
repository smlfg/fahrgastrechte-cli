# fahrgastrechte-cli

CLI für das **Verspätungskonto** — DB-Verspätungsfälle erfassen, sammeln, prüfen
und Anträge vorbereiten. Den Antrag schickt der Mensch selbst ab.

**Source of truth für die Anforderungen:** [`docs/ANFORDERUNGEN.md`](docs/ANFORDERUNGEN.md).

## Status

MVP-Schritt 1: **Skelett** (Commit `chore: bootstrap fahrgastrechte-cli skeleton`).
Die Typer-App steht, ``--version``/``--json`` funktionieren, ein ``ping``-Befehl
liefert Status + Version. Logik (Modelle, Storage, Connectoren, Regeln) kommt
ab Schritt 2.

## Setup

```bash
cd fahrgastrechte-cli
uv sync
uv run fahrgastrechte --version   # 0.1.0
uv run fahrgastrechte ping        # Tabelle
uv run fahrgastrechte ping --json # JSON
uv run pytest                     # Tests
```

## Architektur

- Sprache: Python 3.11+ (gleicher Stack wie `smlfg/ilias-cli`).
- Layout: `src/fahrgastrechte_core/` (Logik, Modelle, Connectoren, Regeln)
  und `src/fahrgastrechte_cli/` (Typer-App, dünne Hülle).
- Build: `uv` + `hatchling`.
- Lizenz: MIT.

## Phase-2-Pendant: `fahrgastrechte-mcp`

MCP-Server (FastMCP, stdio) auf demselben Core. Eigenes Repo unter
`/workspace/fahrgastrechte/fahrgastrechte-mcp` mit gleicher Struktur
(`src/fahrgastrechte_mcp_core/` als Fork des CLI-Cores).
