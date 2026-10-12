# fahrgastrechte-cli

CLI für das **Verspätungskonto** — DB-Verspätungsfälle erfassen, sammeln, prüfen
und Anträge vorbereiten. Den Antrag schickt der Mensch selbst ab.

**Source of truth für die Anforderungen:**
[`/workspace/fahrgastrechte/ANFORDERUNGEN.md`](../ANFORDERUNGEN.md) (im übergeordneten
Arbeitsverzeichnis; diese Datei wird im Repo eingecheckt, sobald die
Implementierung startet).

## Status

Phase 0: **Anforderungsanalyse**. Es gibt noch keinen ausführbaren Code.
Die geplante Architektur ist im Master-Dokument beschrieben.

## Geplante Architektur (Kurzfassung)

- Sprache: Python 3.11+ (gleicher Stack wie `smlfg/ilias-cli` und `smlfg/ilias-mcp`).
- Layout: `src/fahrgastrechte_core/` (Logik, Modelle, Connectoren, Regeln)
  und `src/fahrgastrechte_cli/` (Typer-App, dünne Hülle).
- Build: `uv` + `hatchling`.
- Lizenz: MIT.

## Phase-2-Pendant: `fahrgastrechte-mcp`

MCP-Server (FastMCP, stdio) auf demselben Core. Eigenes Repo unter
`/workspace/fahrgastrechte/fahrgastrechte-mcp` mit gleicher Struktur
(`src/fahrgastrechte_mcp_core/` als Fork des CLI-Cores).
