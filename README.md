# fahrgastrechte-cli

## Schnellstart

Empfohlen: `pipx install git+https://github.com/smlfg/fahrgastrechte-cli` (landet im PATH).
Alternative: `pip install --user git+https://github.com/smlfg/fahrgastrechte-cli`.
Wenn der Befehl danach nicht gefunden wird: pipx nutzen oder das bin-Verzeichnis des venv in den PATH aufnehmen.

```bash
pipx install git+https://github.com/smlfg/fahrgastrechte-cli
fahrgastrechte login
fahrgastrechte station "Stuttgart Hbf"
fahrgastrechte trip "ICE 123" "Stuttgart Hbf" "Mannheim Hbf"
fahrgastrechte add "RE 5" "Stuttgart Hbf" "Ludwigsburg"
```

CLI für das **Verspätungskonto** — DB-Verspätungsfälle erfassen, sammeln, prüfen
und Anträge vorbereiten. Den Antrag schickt der Mensch selbst ab.

**Source of truth für die Anforderungen:** [`docs/ANFORDERUNGEN.md`](docs/ANFORDERUNGEN.md),
die verbindliche Oberfläche steht in [`INTERFACE.md`](INTERFACE.md), die
Nutzer-Sitzungen in [`docs/UX.md`](docs/UX.md).

## Bedienung

Alle Befehle nehmen Namen und Suchbegriffe positional, nicht EVA-Nummern oder
Pflicht-IDs. Schreibende Aktionen (`init`, `add`, `trip --save`, `status`,
`export`, `config set`) zeigen eine Zusammenfassung und fragen im Terminal:

```text
Fall anlegen verbindlich? [j/N]
```

Default ist Nein; erst nach `j` wird geschrieben oder ein Request nach der
Frage gesendet. `N` bricht ohne Änderung ab (Exit 0). In einer Pipe oder bei
Agenten ohne TTY bricht das Tool vor jedem Request ab, außer `--confirm` bzw.
`--yes` ist gesetzt; diese Flags sind nur für nicht-interaktive Läufe gedacht.
Mit `--json` oder wenn stdout kein TTY ist, kommt genau ein JSON-Objekt.

## Status

MVP-Schritte 1–4 fertig:

* **Schritt 1:** Skelett (`chore: bootstrap fahrgastrechte-cli skeleton`)
* **Schritt 2:** Storage + Pydantic-Modelle + Config + `init` (`feat: storage + pydantic models + config`)
* **Schritt 3:** DB-Timetables-Connector (gemockt via `respx`) (`feat: db timetables api connector (mocked)`)
* **Schritt 4:** `add`/`list`/`show`/`trip` inkl. Duplikat-Hash (`feat: case add/list/show + duplicate guard`)

Logik (Modelle, Storage, Connectoren, Regeln) ist im `fahrgastrechte_core`-Paket,
die CLI ist eine dünne Typer-Hülle.

## Setup

```bash
cd fahrgastrechte-cli
uv sync
uv run fahrgastrechte --version      # 0.1.0
uv run fahrgastrechte login          # DB-API-Credentials lokal speichern
uv run fahrgastrechte station Stuttgart
uv run fahrgastrechte ping --json    # JSON
uv run pytest                        # Tests
```

Credentials lassen sich für Agenten auch über
`FAHRGASTRECHTE_DB_CLIENT_ID` und `FAHRGASTRECHTE_DB_API_KEY` setzen; die
Basis-URL überschreibt `FAHRGASTRECHTE_TIMETABLES_BASE_URL` (z. B. für den
Fake-Server in Tests).

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
