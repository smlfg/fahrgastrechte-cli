# INTERFACE – fahrgastrechte

Verbindliche Oberfläche des Befehls `fahrgastrechte` (R2, Stand 2026-11-04).
Die UX-Sitzungen in [`docs/UX.md`](docs/UX.md) und die Anforderungen in
[`docs/ANFORDERUNGEN.md`](docs/ANFORDERUNGEN.md) gelten zusätzlich. Bei
Widerspruch gilt diese Datei für Flags, Exit-Codes und Ausgabeformat.

## Grundsätze

- Alle Befehle nehmen **Namen oder Suchbegriffe positional** entgegen, keine
  IDs oder EVA-Nummern als Pflicht. `<zug|id>` ist eine ID, wenn der Wert nur
  aus Ziffern besteht; sonst wird gegen Zugnummer/Linie gesucht.
- Lesende Befehle senden nur GET-Requests an die Timetables-API. Das Tool
  reicht keine Anträge ein, bucht nichts und schreibt nichts auf DB-Webseiten.
- Menschen sehen lesbare Tabellen/Texte auf stdout. Prompts, Warnungen und
  Fehler gehen nach stderr.
- `--json` liefert **genau ein** JSON-Objekt auf stdout. Ist stdout kein TTY
  (Pipe/Agent) und `--json` nicht gesetzt, wird automatisch JSON ausgegeben.
- Datumsabhängige Befehle nutzen das heutige Datum; `--datum YYYY-MM-DD`
  überschreibt es. `FAHRGASTRECHTE_HEUTE` ist nur für deterministische Tests.
- Keine Tracebacks bei Strg+C, EOF, TTY-Problemen oder fehlenden Credentials.
  Fehlermeldungen nennen den nächsten Schritt.

## Befehle

```text
fahrgastrechte station <suchbegriff> [--json]
fahrgastrechte trip <zug> <start> <ziel> [--datum D] [--save] [--confirm|--yes] [--json]
fahrgastrechte add <zug> <start> <ziel> [--datum D] [--delay-min N]
                    [--reason TEXT] [--ticket-type T] [--ticket-class 1|2]
                    [--force] [--confirm|--yes] [--json]
fahrgastrechte list [status] [--since D] [--until D] [--line L] [--sum] [--json]
fahrgastrechte show <zug|id> [--json]
fahrgastrechte calc <zug|id> [--json]
fahrgastrechte status <zug|id> <neu> [--note TEXT] [--confirm|--yes] [--json]
fahrgastrechte export pdf|csv [name] [--status S] [--ids 1,2]
                    [--confirm|--yes] [--json]
fahrgastrechte init [--confirm|--yes] [--json]
fahrgastrechte config set <key> <value> [--confirm|--yes] [--json]
fahrgastrechte config get <key> [--json]
fahrgastrechte config list [--json]
fahrgastrechte login
fahrgastrechte logout
fahrgastrechte whoami [--json]
```

Bestehende Befehle bleiben erhalten: `ping`, `stats [--month M]`,
`show <id>`, `calc <id>`, `list --status S`. `--status` bleibt als
Kompatibilitäts-Alias zum positional `list [status]` erhalten.

### Lesende Befehle

| Befehl | Verhalten |
|---|---|
| `station <suchbegriff>` | `GET /station/{suchbegriff}`; Tabelle mit Name, EVA-Nummer, DS100. Mehrere Treffer werden vollständig gelistet. |
| `trip <zug> <start> <ziel>` | Löst beide Bahnhofsnamen auf, liest `GET /rchg/{ziel-eva}` und zeigt Zug, Bahnhöfe, Soll-/Ist-Ankunft und Verspätung. **Speichert nichts.** |
| `trip ... --save` | Wie oben, danach schreibend: Zusammenfassung + `[j/N]`, erst nach `j` lokal anlegen. |
| `list [status]` | Lokale Fälle, optional nach Status/Datum/Linie gefiltert, mit Summenzeile. |
| `show <zug|id>` | Einzelfall inkl. Statusverlauf. Mehrdeutige Zugsuche: nummerierte Auswahl im TTY, sonst Liste + Exit 3. |
| `calc <zug|id>` | Entschädigung nach den Regeln aus ANFORDERUNGEN §8. |
| `whoami` | Zeigt, ob DB-API-Zugangsdaten vorhanden sind und aus welcher Quelle (Env/Session). Niemals Secrets. |

### Schreibende Befehle

Schreibend sind `init`, `add`, `trip --save`, `status`, `export` und
`config set`. Jeder schreibende Befehl zeigt zuerst eine Zusammenfassung und
fragt auf stderr:

```text
<Aktion> verbindlich? [j/N]
```

Details:

- Default ist **Nein**. Nur `j`, `ja`, `y` oder `yes` (case-insensitiv) führen
  die Aktion aus. `N`, EOF oder eine andere Eingabe brechen ohne Schreibvorgang
  und ohne Request nach der Frage ab (Exit 0, „Abgebrochen, nichts geändert.“).
- Lesende Vorbereitungsschritte (Bahnhofsauflösung, Vorschau) dürfen **vor**
  der Frage laufen. Nach der Frage werden alle verbleibenden Schritte
  ausgeführt.
- `--confirm` bzw. `--yes` überspringen die Frage und sind **nur für Agenten**
  gedacht. Ohne TTY und ohne dieses Flag bricht der Befehl **vor jedem
  Request** ab (Exit 3) und nennt `--confirm` bzw. das interaktive Terminal.
- `N` ist kein Fehler; `add` nach `j` endet mit „Fall #N angelegt (offen)“.
- `add` liest den Ticket-Typ in dieser Reihenfolge: `--ticket-type` →
  `config ticket_type` → Default `d_ticket`.
- `init` ist idempotent. Im TTY fragt es die Stammdaten ab (Name, Adresse,
  IBAN, Ticket-Typ, Ticket-Preis in Cent); leere Eingaben überspringen den
  jeweiligen Wert. Mit `--confirm` bleibt die DB-Anlage ohne Rückfrage.
- `status <zug|id> <neu>` prüft die erlaubten Übergänge aus ANFORDERUNGEN §3
  F7. Ungültige Übergänge: Exit 3.
- `export pdf|csv [name]` schreibt in das aktuelle Verzeichnis und sendet
  nichts. Ohne `name`: `antrag-YYYY-MM-DD.pdf` bzw. `faelle-YYYY-MM-DD.csv`.
- `config set` validiert den Schlüssel gegen ANFORDERUNGEN §4.

### Login und Session

Es gibt **keinen Web-Login**. `login` speichert nur die beiden DB-API-Header
lokal:

- `DB-Client-ID`: sichtbare Eingabe (`input`).
- `DB-API-Key`: verdeckte Eingabe (`getpass`), niemals als Flag, Argument oder
  in der Shell-Historie.

Session-Pfad (Reihenfolge):

1. `$FAHRGASTRECHTE_SESSION`, falls gesetzt,
2. `$XDG_CONFIG_HOME/fahrgastrechte/session.json`,
3. `~/.config/fahrgastrechte/session.json`.

Die Datei wird atomar mit Rechten `0600` geschrieben, das Verzeichnis mit
`0700`. Inhalt: `{"db_client_id": "...", "db_api_key": "..."}`.

`logout` löscht die Session-Datei und die Schlüssel `db_client_id` /
`db_api_key` aus der lokalen Config-DB; mehrfacher Aufruf ist erlaubt (Exit 0).
`whoami` nennt Quelle und Maskierung, nie den Schlüsselwert.

Priorität der Zugangsdaten: `FAHRGASTRECHTE_DB_CLIENT_ID` /
`FAHRGASTRECHTE_DB_API_KEY` (Env) **vor** Session-Datei. Fehlen beide, nennt
der Fehler `fahrgastrechte login` und endet mit Exit 3.

## Flags

| Flag | Bedeutung |
|---|---|
| `--json` | Genau ein JSON-Objekt auf stdout; Prompts bleiben auf stderr. |
| `--datum D` | Service-Datum `YYYY-MM-DD` (Default: heute). |
| `--confirm`, `--yes` | Bestätigung überspringen, nur für Agenten/nicht-interaktive Läufe. |
| `--save` | Nur `trip`: Ergebnis lokal als Fall anlegen (schreibend). |
| `--force` | Duplikat trotzdem anlegen bzw. Status-Übergang erzwingen (bestehend). |
| `--status S`, `--since D`, `--until D`, `--line L`, `--sum` | Filter/Summe für `list` (bestehend). |
| `--ids 1,2`, `--status S` | Auswahl für `export`. |
| `--month M` | Monat für `stats` (bestehend). |

## Umgebungsvariablen

| Variable | Bedeutung |
|---|---|
| `FAHRGASTRECHTE_TIMETABLES_BASE_URL` | Überschreibt `https://apis.deutschebahn.com/db-api-marketplace/apis/timetables/v1` (Fake-Server/Tests). |
| `FAHRGASTRECHTE_DB_CLIENT_ID` | DB-API-Client-ID; überschreibt die Session. |
| `FAHRGASTRECHTE_DB_API_KEY` | DB-API-Key; überschreibt die Session. |
| `FAHRGASTRECHTE_SESSION` | Alternativer Session-Dateipfad. |
| `FAHRGASTRECHTE_DB` | Alternativer Pfad zur lokalen SQLite-DB (`konto.db`). |
| `FAHRGASTRECHTE_KEYRING=1` | Optimiert sensible Config-Werte zusätzlich ins OS-Keyring (bestehend). |
| `FAHRGASTRECHTE_HEUTE` | Nur Tests: `YYYY-MM-DD` als „heute“. |
| `FAHRGASTRECHTE_OUTPUT` | Nur Tests: `text` erzwingt Tabellenausgabe, `json` erzwingt JSON (Standard: TTY-Erkennung). |
| `XDG_CONFIG_HOME`, `XDG_DATA_HOME` | Standardpfade für Session und DB. |

## Exit-Codes

| Code | Bedeutung |
|---|---|
| `0` | Erfolg, auch bei Abbruch mit `N`/EOF (nichts geändert). |
| `1` | Fachlicher Fehler: Duplikat ohne `--force`, Fall nicht gefunden, Export fehlgeschlagen. |
| `2` | Lokale DB nicht initialisiert und `init` nicht möglich/erforderlich. |
| `3` | Ungültige Eingabe/Usage, fehlende Zugangsdaten, mehrdeutige Auswahl, kein TTY ohne `--confirm`. |
| `4` | Netzwerk/Datenquelle nicht erreichbar. |
| `5` | Antwort nicht parsebar bzw. unerwartetes Datenformat. |
| `130` | Abbruch per Strg+C (SIGINT), ohne Traceback. |

Usage-Fehler von Typer/Click liefern ebenfalls Exit 3 und mit `--json` genau
ein JSON-Objekt auf stdout.
