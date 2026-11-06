# UX-Gate-Bericht

- Datum: 2026-11-06
- Tool: `fahrgastrechte` (Repo smlfg/fahrgastrechte-cli)
- Gate: `tests/harness/ux_check.py` und `tests/harness/ux_session.py` (unverändert)

## Exit-Codes

| Gate | Exit-Code |
|---|---|
| `python3 tests/harness/ux_check.py . --report scratch/ux_check.md` | 0 |
| `python3 tests/harness/ux_session.py . --report scratch/ux_session.md` | 0 |

## ux_check.md

- Repo: `/home/box/cli-daily/fahrgastrechte-cli`
- Tool: `fahrgastrechte`
- Modus: voll
- Ergebnis: PASS

| ID | Ergebnis | Befund |
|---|---|---|
| UX00 | PASS | 16 Request(s) am Fake-Server, keine Fremdverbindung |
| UX01 | PASS | docs/UX.md: 3 Sitzungen, 3 formsteps |
| UX02 | PASS | Credential-Env FAHRGASTRECHTE_DB_API_KEY, FAHRGASTRECHTE_DB_CLIENT_ID, USER_AGENT über `login` |
| UX03 | PASS | --help, --version und 15 Unterkommandos mit Exit 0 |
| UX04 | PASS | 15 Unterkommandos ohne Pflicht-Option statt positional |
| UX05 | PASS | 3 Sitzungen ohne JSON/Traceback im TTY |
| UX06 | PASS | Schnellstart: 4 Tool-Befehle, Installation passt |
| UX07 | PASS | 1 schreibende Sitzung(en) mit [j/N] |
| UX08 | PASS | alle formsteps in Reihenfolge und mit allen Feldern gesendet |
| UX09 | PASS | 1 schreibende Sitzung(en): Voraussetzungen vor [j/N] und Login-Felder vollständig |

## ux_session.md

- Tool: `fahrgastrechte`
- Repo: `/home/box/cli-daily/fahrgastrechte-cli`
- Ergebnis: PASS

| Sitzung | Lauf | Ergebnis | Befund |
|---|---|---|---|
| Installation, Login und Stationssuche | 1 | PASS | OK |
| Fahrt aus den Timetables prüfen | 1 | PASS | OK |
| Verspätungsfall verbindlich anlegen | a (N) | PASS | OK |
| Verspätungsfall verbindlich anlegen | b (j) | PASS | OK |

## Tests

- `scratch/venv/bin/pytest -q`: 207 passed, 13 warnings (keine Favorites/Secrets beteiligt)
