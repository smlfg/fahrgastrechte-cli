# Pflicht-QA: fahrgastrechte

- Datum: 2026-11-09
- Version: 0.1.0
- Commit: `c3d026b`
- Installation: `pipx install git+https://github.com/smlfg/fahrgastrechte-cli` in `./scratch/pipx`
  (Protokoll: `scratch/qa/pipx-install.log`, Python 3.14.7, Konsolen-Skript `fahrgastrechte`)
- Aufruf: ausschließlich über `PATH=$PWD/scratch/pipx/bin:$PATH` (nicht aus `scratch/venv`)
- Testsuite: `scratch/venv/bin/pytest -q` → **207 passed** (`scratch/qa/s5b-pytest.txt`)
- Vorgehen: vorhandene Logs aus drei früheren QA-Läufen (`scratch/qa/s5-*`, `scratch/qa/qa2-logs/`,
  `scratch/qa/0*`–`5*`) ausgewertet; fehlende Prüfungen mit der frischen `pipx`-Installation ergänzt.
  Live-Aufrufe waren ausschließlich lesende GET-Requests auf `station`/`trip`; es gab keine echten
  Buchungen, Einreichungen, Logins auf echten Konten oder Schreibaktionen gegen die DB.
- Fixtures/Zugangsdaten in den Läufen waren synthetisch (`test-client-id`, `test-api-key`).

## Ergebnis

**62 von 68 Prüfungen OK, 6 FAIL.** Die offenen FAILs stehen unten in der Tabelle und gesammelt in
`scratch/QA-FAILS.md`.

## Installation, Hilfe und Tests

| Prüfung | Befehl | Erwartet | Ergebnis | Beleg (gekürzt) |
|---|---|---|---|---|
| Frische pipx-Installation | `pipx install git+https://github.com/smlfg/fahrgastrechte-cli` | Paket 0.1.0 installiert, `fahrgastrechte` im PATH | OK | `pipx-install.log`: „installed package fahrgastrechte-cli 0.1.0“ |
| Version | `fahrgastrechte --version` | `fahrgastrechte 0.1.0`, Exit 0 | OK | `s5b-00-version.txt` |
| Haupt-Hilfe | `fahrgastrechte --help` | alle Unterkommandos, Exit 0 | OK | `s5b-00-help.txt`; 15 Unterkommandos |
| Unterkommando-Hilfe | `fahrgastrechte {station,add,trip,list,export,config} --help` | Exit 0, Argumente/Flags lesbar | OK | `scratch/g_check.md` (UX03), `s5b-00-help.txt` |
| Testsuite | `scratch/venv/bin/pytest -q` | grün | OK | `s5b-pytest.txt`: 207 passed |

## Login, Session und Sicherheit

| Prüfung | Befehl | Erwartet | Ergebnis | Beleg (gekürzt) |
|---|---|---|---|---|
| Login im TTY | `fahrgastrechte login` mit synthetischen Werten | Prompt für Client-ID/API-Key, speichert, Exit 0 | OK | `s5b-01-login.log`: „Zugangsdaten gespeichert.“ |
| Session-Rechte | `find <home> -path '*session.json' -printf '%m %p'` | `600` | OK | `s5b-01-login.log`, Modus `600` |
| Login ohne TTY/EOF | `fahrgastrechte login < /dev/null` | Exit ≠ 0, nächster Schritt, kein Traceback | OK | `s5-26-login-eof.log`: Exit 3, Hinweis auf `login`/Env |
| Login mit Strg+D | `fahrgastrechte login` + EOF am Prompt | sauberer Abbruch, Exit ≠ 0, kein Traceback | OK | `s5-28-login-ctrld.log`: Exit 3 |
| Login mit Strg+C | `fahrgastrechte login` + `^C` | sauberer Abbruch, kein Traceback | OK | `s5-27-login-sigint.log` (Exit 3), `42-login-sigint.log` (Exit 130) |
| Login mit leeren Feldern | `fahrgastrechte login` + leere Eingaben | Fehler + nächster Schritt, Exit 3 | OK | `s5-29-login-empty.log` |
| `whoami` mit Credentials | `fahrgastrechte whoami` | vorhanden, Quelle, Secrets maskiert | OK | `s5-30-whoami.log`: `te…id`, `te…ey` |
| `whoami` ohne Credentials | `fahrgastrechte whoami` in frischem HOME | „fehlen“ + nächster Schritt | OK | `s5-38-whoami-add.log` |

## README-Schnellstart (Fake-Server mit UX-Routen, pipx, Pseudo-TTY)

| Prüfung | Befehl | Erwartet | Ergebnis | Beleg (gekürzt) |
|---|---|---|---|---|
| Stationssuche | `fahrgastrechte station "Stuttgart Hbf"` | `Stuttgart Hbf (EVA 8000096)`, Exit 0 | OK | `s5b-02-station.log` |
| Fahrt prüfen | `fahrgastrechte trip "ICE 123" "Stuttgart Hbf" "Mannheim Hbf"` | Von/Nach/Ankunft, „Nicht gespeichert“, Exit 0 | OK | `s5b-03-trip.log`: `17:54 (Soll 15:25, +149 min)` |
| Anlegen mit N | `fahrgastrechte add "RE 5" "Stuttgart Hbf" "Ludwigsburg"` + `N` | Zusammenfassung + `[j/N]`, „Abgebrochen“, kein Schreibvorgang | OK | `s5b-04-add-N.log` |
| Anlegen mit j | gleicher Befehl + `j` | Echtzeit + `Fall #1 angelegt (offen)` | OK | `s5b-05-add-j.log`: `Echtzeit: +33 min` |
| `trip --save` mit N | `fahrgastrechte trip … --save` + `N` | `[j/N]`, kein Fall | OK | `46-tripsave-N.log` |
| `trip --save` mit j | gleicher Befehl + `j` | Fall wird angelegt | OK | `47-tripsave-j.log` |
| `show` per Zugname | `fahrgastrechte show "RE 5"` (mehrere Treffer) | nummerierte Auswahl, danach Fallanzeige | OK | `43-show-auswahl.log` |
| `list` im TTY | `fahrgastrechte list offen` | lesbare Tabelle, Summenzeile | OK | `45-list-tty.log` |
| `init` im TTY, N | `fahrgastrechte init` + Stammdaten + `N` | Stammdaten-Prompts, `[j/N]`, Abbruch | OK | `33-init-tty.log` |
| `init` im TTY, j | gleicher Befehl + `j` | DB angelegt, Tabelle | OK | `34-init-tty-j.log` |
| `config set` N/j | `fahrgastrechte config set ticket_type eurorail` | `[j/N]`; N ändert nichts, j schreibt | OK | `31-config-N.log`, `32-config-j.log` |
| `export csv` N/j | `fahrgastrechte export csv` | `[j/N]`; N schreibt nicht, j schreibt Datei | OK | `35-export-N.log`, `36-export-j.log` |
| `export csv --status offen` | `fahrgastrechte export csv --status offen` | nur offene Fälle | OK | `38-export-offen.log`: 2 Fälle; `scratch/qa/export/faelle-2026-11-09.csv` enthält nur `offen` |
| `export pdf` j | `fahrgastrechte export pdf` | PDF wird geschrieben | OK | `37-export-pdf.log`; `scratch/qa/export/antrag-2026-11-09.pdf` beginnt mit `%PDF-1.4` |
| `status` N/j | `fahrgastrechte status 1 eingereicht` | `[j/N]`; N nichts, j Statuswechsel | OK | `s5-39-status-N.log`, `s5-40-status-j.log` |
| Unerlaubter Statuswechsel | `fahrgastrechte status 1 offen` nach `eingereicht` | Fehler + erlaubte Ziele + Exit 3 | OK | `s5-41-status-invalid.log` |
| Begründungspflicht | `fahrgastrechte status 1 abgelehnt` ohne `--note` | Fehler + Exit 3 | OK | `s5-42-status-note.log` |
| `show`/`calc` per Zugname | `fahrgastrechte show "RE 5"`, `calc "RE 5"` | Fall wird gefunden, Tabelle/JSON | OK | `s5-35-show-name.log`, `s5-36-calc-name.log`, `s5b-10-show.json`, `s5b-11-calc.json` |
| JSON für `list`/`show` | `fahrgastrechte list --json`, `show "RE 5" --json` | genau ein valides JSON-Objekt | OK | `s5b-09-list.json`, `s5b-10-show.json`; `qa2-logs/filter-*.json` |

## JSON, Fehler und Exit-Codes

| Prüfung | Befehl | Erwartet | Ergebnis | Beleg (gekürzt) |
|---|---|---|---|---|
| Auto-JSON bei Pipe | `fahrgastrechte station "Stuttgart Hbf"` mit stdout=Pipe | JSON ohne `--json` | OK | `s5b-06-auto-json.txt`, `s5-31-auto-json.txt` |
| `--json` im TTY | `fahrgastrechte station "Stuttgart Hbf" --json` | JSON statt Tabelle | OK | `s5b-08-json-tty.log`, `s5-32-json-tty.log` |
| Netzfehler (Text) | `FAHRGASTRECHTE_TIMETABLES_BASE_URL=http://127.0.0.1:9 fahrgastrechte station …` | Exit 4, „Nächster Schritt“, kein Traceback | OK | `s5b-07-net-error.log`, `s5-24-net-error.log` |
| Netzfehler (JSON) | gleicher Aufruf mit `--json` | valides JSON-Objekt, Exit 4 | OK | `s5-25-net-error-json.log` |
| Usage-Fehler | `fahrgastrechte station` ohne Suchbegriff | Exit 2, kein Traceback | OK | `s5-33-usage.err` |
| Usage-Fehler mit `--json` | `fahrgastrechte station --json` ohne Suchbegriff | genau ein JSON-Objekt | **FAIL** | `s5b-19-usage-json.out` ist leer, `s5b-19-usage-json.err` enthält Klartext-Usage, Exit 2 |
| Unbekanntes Kommando | `fahrgastrechte gibtsnicht` | Exit 2, kein Traceback | OK | `s5-34-unknown.err` |
| Schreiben ohne TTY: `add` | `fahrgastrechte add "RE 5" …` ohne `--confirm` | Abbruch vor Request, Exit 3 | OK | `s5b-20-add-notty.out`, `s5-07-notty.out` |
| Schreiben mit Agenten-Flag: `add` | `fahrgastrechte add … --confirm` gegen Fake-Server | legt Fall an | OK | `s5-08-confirm.json` |
| Schreiben ohne TTY: `trip --save` | `fahrgastrechte trip … --save` ohne `--confirm` | Abbruch, Exit 3 | OK | `scratch/qa/s5b-*`-Lauf, `s5b-20-add-notty`-Muster |
| Schreiben ohne TTY: `status` | `fahrgastrechte status 1 bewilligt` ohne `--confirm` | Abbruch, Exit 3 | OK | Lauf 2026-11-09: Exit 3, Hinweis auf `--confirm` |
| Schreiben ohne TTY: `export` | `fahrgastrechte export csv …` ohne `--confirm` | Abbruch, keine Datei, Exit 3 | OK | Lauf 2026-11-09: Exit 3, Zieldatei nicht angelegt |
| Schreiben ohne TTY: `init` | `fahrgastrechte init --json` ohne `--confirm` (stdout=Pipe) | Abbruch vor lokalem Schreibvorgang | **FAIL** | `s5b-17-init-noconfirm.out`: Exit 0, „DB initialisiert“; DB-Datei wurde angelegt |
| Schreiben ohne TTY: `config set` | `fahrgastrechte config set ticket_type eurorail --json` ohne `--confirm` | Abbruch vor lokalem Schreibvorgang | **FAIL** | `s5b-18-config-noconfirm.out`: Exit 0, `value_set: true`; `config get` liefert `eurorail` |

## Live-Prüfungen (nur lesende GET-Requests)

| Prüfung | Befehl | Erwartet | Ergebnis | Beleg (gekürzt) |
|---|---|---|---|---|
| Live: Stuttgart Hbf | `fahrgastrechte station "Stuttgart Hbf"` | richtiger Bahnhof/EVA | OK | `s5-10-live-stuttgart.log`, `qa2-logs/live-01-stuttgart.log` |
| Live: München Hbf | `fahrgastrechte station "München Hbf"` | richtiger Bahnhof/EVA | OK | `s5-11-live-muenchen-hbf.log`: EVA 8000261 |
| Live: Köln Hbf | `fahrgastrechte station "Köln Hbf"` | richtiger Bahnhof/EVA | OK | `s5-12-live-koeln-hbf.log`: EVA 8000207 |
| Live: Ludwigsburg | `fahrgastrechte station "Ludwigsburg"` | richtiger Bahnhof/EVA | OK | `s5-13-live-ludwigsburg.log`: EVA 8000235 |
| Live: Frankfurt(Main)Hbf | `fahrgastrechte station "Frankfurt(Main)Hbf"` | richtiger Bahnhof/EVA | OK | `s5-14-live-frankfurt-klammer.log`: EVA 8000105 |
| Live: Berlin Hbf | `fahrgastrechte station "Berlin Hbf"` | richtiger Bahnhof/EVA | OK | `s5-20-live-berlin.log`: EVA 8011160 |
| Live: Hamburg Hbf | `fahrgastrechte station "Hamburg Hbf"` | richtiger Bahnhof/EVA | OK | `20-live-hamburg.log`: EVA 8002549 |
| Live: Teiltreffer mit Umlaut | `fahrgastrechte station "München"` | plausible Treffer oder sauberes „nicht gefunden“ | **FAIL** | `s5-15-live-muenchen.log`: ParserError (EVA < 1 000 000), Exit 5, irreführender Hinweis |
| Live: Tippvarianten | `station Muenchen`, `station "Koeln Hbf"`, `station "Frankfurt (Main) Hbf"` | gebräuchliche Schreibvarianten werden aufgelöst | **FAIL** | `s5-16`, `s5-17`, `s5-18`, `qa2-logs/live-07/09/10`: jeweils „Keine Station … gefunden“, Exit 1 |
| Live: kein Treffer | `fahrgastrechte station "XyzNope"` | Fehler + nächster Schritt, Exit 1 | OK | `s5-19-live-empty.log` |
| Live: trip ohne Echtzeitdaten | `fahrgastrechte trip "ICE 123" …` bzw. `"ICE 9999"` | plausibler Hinweis, Exit 0 | OK | `qa2-logs/live-12`, `live-14`, `s5-21`, `s5-22` |
| Live: trip mit Echtzeitdaten | `fahrgastrechte trip "IC 2040" …` | Ankunft/Soll/Delay plausibel | OK | `s5-23-live-trip-current.log`: `11:50 (Soll 11:21, +29 min)` |
| Live: trip weitere Verbindung | `fahrgastrechte trip "ICE 2873" …` | Ankunft/Soll/Delay plausibel | OK | `qa2-logs/live-15-trip-ICE-2873.log`: `10:40 (Soll 10:26, +14 min)` |
| Live nur lesend | `station`/`trip` gegen DB-Quelle | nur GET, keine Buchung/Einreichung/Login | OK | Befehlsprotokolle `s5-10`–`s5-25`; keine Schreibziele im Quellcode dieser Pfade |

## Filter-Semantik über alle Treffer

| Prüfung | Befehl | Erwartet | Ergebnis | Beleg (gekürzt) |
|---|---|---|---|---|
| Status `offen` | `fahrgastrechte list --status offen --json` | jeder Treffer `current_status=offen` | OK | `qa2-logs/filter-status-offen.json`: 1 Fall, nur `offen` |
| Status `eingereicht` | `fahrgastrechte list --status eingereicht --json` | jeder Treffer `eingereicht` | OK | `qa2-logs/filter-status-eingereicht.json`: 2 Fälle, beide `eingereicht` |
| Status `abgelehnt` | `fahrgastrechte list --status abgelehnt --json` | jeder Treffer `abgelehnt` | OK | `qa2-logs/filter-status-abgelehnt.json`: 1 Fall, nur `abgelehnt` |
| Status-Alias | `fahrgastrechte list offen --json` | positional = Filter, jeder Treffer `offen` | OK | `qa2-logs/filter-status-alias.json`: 1 Fall, nur `offen` |
| Linienfilter | `fahrgastrechte list --line "RE 5" --json` | jeder Treffer `line="RE 5"` | OK | `qa2-logs/filter-line.json`: alle Treffer `RE 5` |
| `--since` | `fahrgastrechte list --since 2026-11-02 --json` | alle `service_date >= 2026-11-02` | OK | `qa2-logs/filter-since.json`: 2026-11-03/11-02 |
| `--until` | `fahrgastrechte list --until 2026-11-02 --json` | alle `service_date <= 2026-11-02` | OK | `qa2-logs/filter-until.json`: 2026-11-02/11-01 |
| Alle Fälle / Export ohne Filter | `fahrgastrechte list --json`, `export csv --confirm` | jeder Fall genau einmal, Summe stimmt | **FAIL** | `s5b-15-duplicate-list.json`: Fall #3 dreimal (`[3,3,3,2,1]`, `count=5` statt 3); `s5b-16-duplicate-export.csv`: #3 dreimal, `rows=5` |

## Offene FAILs

1. **Teiltreffer mit Umlaut:** `station "München"` bricht mit `ParserError` (EVA < 1 000 000) und Exit 5 ab,
   statt unpassende Treffer zu ignorieren und z. B. `München Hbf` zu liefern.
2. **Tippvarianten:** `Muenchen`, `Koeln Hbf` und `Frankfurt (Main) Hbf` finden nichts, obwohl die
   Schreibweise mit Umlaut bzw. ohne Leerzeichen funktioniert.
3. **Doppelte/stale Fälle:** Zwei Statuswechsel in derselben Sekunde erzeugen denselben `at`-Wert;
   `list`/Filter/`export` joinen dann mehrere Statuszeilen und zählen einen Fall mehrfach
   (inkl. veralteter Statuswerte). Frisch reproduziert mit drei Statuswechseln in einer Sekunde:
   `[3,3,3,2,1]`, `count=5`, `total_cents=750` statt 3 Fällen / 450 Cent.
4. **`init` ohne TTY/`--confirm`:** schreibt die DB trotz fehlender Bestätigung (Exit 0).
5. **`config set` ohne TTY/`--confirm`:** schreibt den Wert trotz fehlender Bestätigung (Exit 0).
6. **Usage-Fehler mit `--json`:** liefert Klartext auf stderr und leerem stdout statt genau eines
   JSON-Objekts.

Keine dieser Stellen wurde im QA-Lauf geändert (find → file).
