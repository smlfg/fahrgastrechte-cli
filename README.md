# fahrgastrechte-cli

CLI für das **Verspätungskonto** — DB-Verspätungsfälle erfassen, sammeln, prüfen
und Anträge vorbereiten. Den Antrag schickt der Mensch selbst ab.

**Source of truth für die Anforderungen:** [`docs/ANFORDERUNGEN.md`](docs/ANFORDERUNGEN.md).

## Status

MVP-Schritte 1–6 fertig:

* **Schritt 1:** Skelett (`chore: bootstrap fahrgastrechte-cli skeleton`)
* **Schritt 2:** Storage + Pydantic-Modelle + Config + `init` (`feat: storage + pydantic models + config`)
* **Schritt 3:** DB-Timetables-Connector (gemockt via `respx`) (`feat: db timetables api connector (mocked)`)
* **Schritt 4:** `add`/`list`/`show`/`trip` inkl. Duplikat-Hash (`feat: case add/list/show + duplicate guard`)
* **Schritt 5:** Entschädigungsregeln + `calc`/`stats` (`feat: compensation rules + calc/stats`)
* **Schritt 6:** `status`/`export pdf`/`export csv`/`station` (F7/F8/F9)

Logik (Modelle, Storage, Connectoren, Regeln) ist im `fahrgastrechte_core`-Paket,
die CLI ist eine dünne Typer-Hülle.

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
