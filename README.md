# fahrgastrechte-cli

Kommandozeilen-Tool für ein persönliches **Verspätungskonto**: DB-Verspätungsfälle erfassen,
sammeln, die Entschädigung nach den Fahrgastrechte-Regeln berechnen und Anträge vorbereiten.
Den Antrag schickt der Mensch selbst ab.

## Status

Version 0.1.0, in Entwicklung. Umgesetzt sind Erfassen, Listen, Anzeigen, Trip-Rekonstruktion,
Berechnung, Statistik und Stammdaten (F1 bis F6, F10, F11 aus
[docs/ANFORDERUNGEN.md](docs/ANFORDERUNGEN.md)). Status-Workflow (F7) und Export als PDF/CSV
(F8, F9) fehlen noch.

## Schnellstart

Voraussetzungen: Python 3.11 oder neuer und [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/smlfg/fahrgastrechte-cli.git
cd fahrgastrechte-cli
uv sync
uv run fahrgastrechte init
uv run fahrgastrechte add --datum 2026-10-01 --zug 12345 --start-eva 8000105 --dest-eva 8000191 --begruendung "Anschluss verpasst" --delay-min 75
uv run fahrgastrechte calc 1
```

`calc` zeigt Betrag, angewendete Regel mit Quelle, den 25-%-Deckel und ob der Fall unter der
4-€-Bagatellgrenze liegt.

## Befehle

| Befehl | Zweck |
|---|---|
| `fahrgastrechte ping` | Sanity-Check: Version und Python-Info |
| `fahrgastrechte init` | Lokale Datenbank anlegen (idempotent) |
| `fahrgastrechte add` | Fall manuell erfassen (Pflicht: Datum, Zug, Start-/Ziel-EVA, Begründung); Duplikatschutz, `--force` zum Überschreiben |
| `fahrgastrechte trip` | Fall aus der DB-Timetables-API rekonstruieren und anlegen |
| `fahrgastrechte list` | Fälle listen, Filter `--status`, `--since`, `--until`, `--line`, mit Summenzeile |
| `fahrgastrechte show <id>` | Einzelnen Fall anzeigen |
| `fahrgastrechte calc <id>` | Entschädigung für einen Fall berechnen |
| `fahrgastrechte stats [--month YYYY-MM]` | Statistik pro Monat: Fälle, Summe, Bagatellgrenze, Deckel |
| `fahrgastrechte config set/get/list` | Stammdaten und Ticket-Konfiguration |

Die meisten Befehle geben mit `--json` maschinenlesbare Ausgabe statt einer Tabelle aus.

## Konfiguration

| Variable | Wirkung |
|---|---|
| `FAHRGASTRECHTE_DB` | Pfad der SQLite-Datenbank (Default `~/.local/share/fahrgastrechte/konto.db`) |
| `FAHRGASTRECHTE_KEYRING=1` | Besonders sensible Schlüssel zusätzlich im OS-Schlüsselbund ablegen |
| `FAHRGASTRECHTE_DB_CLIENT_ID`, `FAHRGASTRECHTE_DB_API_KEY` | Zugang zur DB-Timetables-API für `trip` |
| `FAHRGASTRECHTE_TIMETABLES_BASE_URL` | Andere Basis-URL für die Timetables-API |

Stammdaten (`name`, `iban`, `address`, Ticket-Angaben) liegen standardmäßig im Klartext in der
`config`-Tabelle der Datenbank; die Datei wird mit Rechten `0600` angelegt.

## Aufbau

- `src/fahrgastrechte_core/`: Logik ohne CLI (Modelle, SQLite-Storage, Konfiguration, Regeln,
  Statistik, Timetables-Connector mit Ratelimit).
- `src/fahrgastrechte_cli/`: dünne Typer-Oberfläche, Einstiegspunkt `fahrgastrechte`.
- Das Gegenstück als MCP-Server ist [fahrgastrechte-mcp](https://github.com/smlfg/fahrgastrechte-mcp).

## Entwicklung und Tests

```bash
uv sync
uv run pytest
```

Die Tests mocken die Timetables-API mit `respx` und brauchen kein Netz.

## Grenzen

- Status-Workflow (`eingereicht`, `bewilligt` …) und Export als PDF oder CSV sind noch nicht umgesetzt.
- Die Tests prüfen `trip` nur gegen eine gemockte Timetables-API.
- Kein CI-Workflow im Repository.

## Lizenz

Noch keine Lizenzdatei im Repository; ohne Lizenz gelten standardmäßig alle Rechte als
vorbehalten. `pyproject.toml` nennt MIT, das ist noch nicht durch eine `LICENSE`-Datei bestätigt.
