# Verspätungskonto (fahrgastrechte-cli + fahrgastrechte-mcp): Anforderungen, Entwurf v0.1

Stand: 12.10.2026. Für: Samuel Fleig. Erstnutzer und einziger Nutzer in Phase 1.
Quellen-Kennzeichnung: **[V]** = selbst geprüft (öffentlich, mit Quellen-URL, 12.10.2026) · **[A]** = Annahme, muss noch bestätigt werden · **[S]** = Aussage von Samuel im Chat.

---

## 1. Ziel & Scope

- **Ziel:** Samuel erfasst, sammelt und bereitet eigene DB-Verspätungsfälle für Fahrgastrechte-Anträge vor. Das Tool schlägt Fälle vor, prüft sie gegen echte Verspätungsdaten, markiert Duplikate, berechnet den voraussichtlichen Entschädigungsbetrag nach den offiziellen Regeln und erzeugt druckfertige / importierbare Antragsunterlagen. **Den Antrag selbst schickt Samuel weiterhin selbst ab** (DB-Kundenkonto, Formular per Post, Servicecenter). Das Tool berät und vorbereitet, es vertritt nicht.
- **Zwei Repos, ein Core:**
  - `smlfg/fahrgastrechte-cli` — Kommandozeilen-Tool `fahrgastrechte` für Terminal und Skripte.
  - `smlfg/fahrgastrechte-mcp` — MCP-Server (stdio, FastMCP), damit der gleiche Core aus Claude Desktop, Cursor, Hermes, Codex, OpenCode etc. nutzbar ist.
  - Beide Repos referenzieren denselben Kern; `fahrgastrechte-mcp` vendored den Core (gleiche Konvention wie `ilias-mcp` vendored `ilias-cli`/`ilias_core`, siehe https://github.com/smlfg/ilias-mcp).
- **Erstnutzer:** Samuel, Pendler im Nah- und Regionalverkehr mit Deutschlandticket (Schwerpunkt). Spätere Erweiterung: Fernverkehrs-Einzeltickets, BahnCard 100, andere Verkehrsunternehmen (nicht in Phase 1).
- **Nicht im Scope:** automatisierter Versand des Antrags, Scraping des DB-Kundenkontos, Anwalts-/Inkasso-Funktionen, Mehrnutzer-/Mandantenfähigkeit.

## 2. Nutzer & Use Cases

| #   | Use Case                                                                                                  | Warum                                                                                                                                |
| --- | --------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| UC1 | „Heute war mein RE 30 min zu spät, 65 min am Ziel — Fall aufnehmen."                                     | Dauer-Schmerz: nach 4–5 Fällen lohnt sich der Aufwand für 7,50 € (D-Ticket 1,50 € × 5) erst ab 4-€-Bagatellgrenze, also sammeln [V]    |
| UC2 | „Zeig mir alle offenen Fälle, was wäre die Gesamtsumme, ab wann lohnt sich das Einreichen?"               | Übersicht über das „Konto", Planung des Antragszeitpunkts                                                                            |
| UC3 | „Prüfe den Fall gegen echte Daten — war der Zug wirklich 65 min später?"                                 | Selbstschutz: Antrag nur bei belegbaren Fällen, keine Erinnerungslücken                                                              |
| UC4 | „Doppelten Fall verhindern — ich habe letzte Woche schon mal RE nach Stuttgart eingegeben."              | Mehrere Tickets für die gleiche Fahrt oder schlicht vergessen → Duplikatschutz                                                       |
| UC5 | „Bereite mir den Antrag vor — als druckfertiges Formular und als Import fürs DB-Kundenkonto."            | 90 % des Aufwands ist Datentippen; Samuel will die Zeit nicht investieren                                                            |
| UC6 | „Wo ist mein letzter Antrag? Status: eingereicht, Rückfrage, bewilligt, ausgezahlt."                      | Rückfragen kommen bei der DB regelmäßig vor; der Status muss mit                                                                     |
| UC7 | „Sag mir per Sprache in 5 Sekunden, was ich jetzt tun soll." (Später, Phase 2)                            | Erfassung im fahrenden Zug ohne Tastatur                                                                                            |

## 3. Funktionale Anforderungen

Jeder Befehl: `--json` liefert stabile Maschinendaten, ohne Flag eine Tabelle für Menschen. Exit-Codes: 0 OK, 1 sonstiger Fehler, 2 nicht initialisiert (kein DB-Setup), 3 ungültige Eingabe, 4 Netzwerk/Datenquelle nicht erreichbar, 5 Parser-/Datenfehler.

| ID  | Prio     | Funktion                                                                                                                                                                                                                                                                              | Vorgeschlagener Befehl / MCP-Tool                                            |
| --- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| F1  | **Must** | Fall manuell erfassen: Datum, Start-Bhf., Ziel-Bhf., Soll-Ankunft, Ist-Ankunft, Zugnummer, Linie, EVU, Begründung (Text), Beleg (Dateipfad optional). Pflichtfelder prüfen.                                                                                                          | `fahrgastrechte add` · `add_delay_case`                                      |
| F2  | **Must** | Fall automatisch aus realer Fahrt rekonstruieren: übergibt Datum + Zugnummer (oder Journey-Ref), Tool ruft Timetables-API (`/plan` + `/rchg`), berechnet Ist-Ankunft und Verspätung am Ziel. Wenn Quelle nichts liefert: Fall mit `Quelle: manuell` markieren, nicht abstürzen.       | `fahrgastrechte add --from-trip "2026-10-12 RE 12345 Stuttgart-Mannheim"`    |
| F3  | **Must** | Eigene Fälle listen, mit Filter (offen/eingereicht/bewilligt/ausgezahlt, Datumsbereich, Linie) und Summenzeile                                                                                                                                                                       | `fahrgastrechte list [--status offen] [--since 2026-09-01] [--sum]`          |
| F4  | **Must** | Einzelfall anzeigen, mit berechnetem Erwartungsbetrag und Begründung der Regel (z. B. „1,50 € × 1 Fall = 1,50 €, unter 4-€-Bagatellgrenze, sammeln")                                                                                                                                  | `fahrgastrechte show <id>`                                                   |
| F5  | **Must** | Entschädigung berechnen: Ticket-Typ (D-Ticket/Nahverkehrszeitkarte/Fernverkehrszeitkarte/Einzelticket/BahnCard 100) + Verspätung am Ziel → Betrag, Hinweis auf Bagatellgrenze (4 €) und Deckel (25 % des Ticketwerts). Regeln aus bahn.de (siehe § 8).                              | `fahrgastrechte calc <id>` · `compute_compensation`                          |
| F6  | **Must** | Duplikatschutz: Hash aus (Datum, Zugnummer, Start-Bhf.-ID, Ziel-Bhf.-ID). Beim `add` eines Hash, der schon existiert: Warnung mit ID des Originals, Exit 1; `--force` zum Überschreiben.                                                                                                                                                              | in `add` integriert · `check_duplicate`                                     |
| F7  | **Must** | Status-Workflow: `offen` → `eingereicht` → (`rückfrage` ↔ `nachgereicht`) → `bewilligt` → `ausgezahlt` \| `abgelehnt`. Nur erlaubte Übergänge, Begründungspflicht bei `abgelehnt` / `rückfrage`.                                                                                       | `fahrgastrechte status <id> <neu> [--note …]`                                |
| F8  | **Should** | Antrags-Vorbereitung als PDF: druckfertiges DB-Fahrgastrechte-Formular, vorbefüllt mit Stammdaten (Name, Adresse, IBAN, Ticket-Nr., D-Ticket-Sichtausweis-ID) und allen ausgewählten offenen Fällen. PDF wird im Arbeitsverzeichnis abgelegt, nicht automatisch versendet.            | `fahrgastrechte export pdf --ids 1,2,3 -o antrag-2026-10.pdf`               |
| F9  | **Should** | Antrags-Vorbereitung als CSV: Format angelehnt an die DB-Kundenkonto-Auftragssuche, damit Samuel die Fälle schnell im Webformular referenzieren kann.                                                                                                                                | `fahrgastrechte export csv --status offen -o faelle.csv`                    |
| F10 | **Should** | Stammdaten verwalten: Name, Adresse, IBAN, D-Ticket-Sichtausweis-ID, Ticket-Typ, Ticket-Preis/Monat, BahnCard-Nummer. Verschlüsselt im OS-Keyring bzw. `chmod 600`-Datei.                                                                                                            | `fahrgastrechte config set/get`                                              |
| F11 | **Should** | Statistik: pro Monat X Fälle, Y €, Erwartungs-Entschädigung unter/über 4-€-Grenze, was wäre maximaler Deckel.                                                                                                                                                                          | `fahrgastrechte stats [--month 2026-10]`                                     |
| F12 | **Could** | Sprach-Erfassung (Phase 2): Mikrofon-Input, Transkription, Extraktion in F1-Felder. Erst sinnvoll, wenn F1/F3 stabil laufen.                                                                                                                                                            | (Phase 2)                                                                    |
| F13 | **Could** | Erinnerung, wenn das „Konto" die 4 € erreicht oder ein Antrag > 30 Tage offen ist.                                                                                                                                                                                                     | `fahrgastrechte watch` (Daemon / Cron)                                       |

MCP-Mapping (Phase 2, jeder Must- und Should-Befehl wird 1:1 ein Tool): `add_delay_case`, `list_delay_cases`, `show_delay_case`, `compute_compensation`, `update_status`, `export_pdf`, `export_csv`, `get_config`, `set_config`.

## 4. Datenmodell (SQLite, lokal)

Eine Datei `~/.local/share/fahrgastrechte/konto.db`. Schema-Version in `meta.schema_version` (Start: 1). Pydantic-Modelle im Core.

```text
config (
  key   TEXT PRIMARY KEY,   -- "name", "iban", "address", "ticket_type", "ticket_price_cents",
                            -- "ticket_issuing_evu", "dticket_id", "bahncard_no", "evu_for_claims"
  value TEXT
)

stations (
  eva_no   INTEGER PRIMARY KEY,   -- 7-stellige EVA-Nummer (z. B. 8000096 Stuttgart Hbf)
  name     TEXT NOT NULL,
  ds100    TEXT,                  -- DS100-Kürzel, optional
  lat      REAL, lon REAL,
  source   TEXT                   -- "timetables_api" | "manual"
)

trips (
  id              INTEGER PRIMARY KEY,
  service_date    TEXT NOT NULL,         -- ISO 8601, "2026-10-12"
  planned_dep     TEXT, planned_arr TEXT, -- ISO 8601 mit Zeitzone Europe/Berlin
  actual_dep      TEXT, actual_arr TEXT,
  delay_min_arr   INTEGER,               -- berechnet: actual_arr - planned_arr in Minuten
  start_eva       INTEGER REFERENCES stations(eva_no),
  dest_eva        INTEGER REFERENCES stations(eva_no),
  category        TEXT,                  -- "RE" | "RB" | "S" | "IC" | "ICE" | "Bus" | "Strab" …
  line            TEXT,                  -- "RE 5", "S3"
  train_number    TEXT,                  -- betriebliche Zugnummer (z. B. "12345")
  evu             TEXT,                  -- "DB Regio BW" etc.
  trip_ref        TEXT,                  -- API-Referenz auf die Rekonstruktion (z. B. HAFAS-Journey-ID)
  data_source     TEXT NOT NULL,         -- "timetables_api" | "db_vendo" | "manual"
  data_quality    TEXT NOT NULL,         -- "verified" | "best_effort" | "manual_only"
  reconstructed_at TEXT,
  UNIQUE(service_date, train_number, start_eva, dest_eva)   -- Dedup-Key
)

cases (
  id              INTEGER PRIMARY KEY,
  trip_id         INTEGER REFERENCES trips(id) ON DELETE CASCADE,
  ticket_type     TEXT NOT NULL,    -- "d_ticket" | "nv_zeitkarte" | "fv_zeitkarte" | "nv_einzel" | "bc100"
  ticket_class    INTEGER,          -- 1 oder 2
  reason_text     TEXT,             -- Freitext: "RE ausgefallen, SEV"
  evidence_path   TEXT,             -- optionaler Pfad auf Beleg (Ticketfoto)
  expected_cents  INTEGER NOT NULL, -- nach F5 berechnet
  cap_applied     INTEGER,          -- 25-%-Deckel in Cent (0 wenn nicht relevant)
  notes           TEXT,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL
)

case_status (
  case_id    INTEGER REFERENCES cases(id) ON DELETE CASCADE,
  status     TEXT NOT NULL,         -- "offen" | "eingereicht" | "rueckfrage" | "nachgereicht"
                                    -- | "bewilligt" | "ausgezahlt" | "abgelehnt"
  at         TEXT NOT NULL,
  note       TEXT,
  PRIMARY KEY (case_id, status, at)
)
```

Indizes: `trips(service_date)`, `trips(train_number, service_date)`, `cases(trip_id)`, `case_status(case_id, at)`.

## 5. CLI-Kommandos (Übersicht)

```text
fahrgastrechte init                                  # legt konto.db an, fragt Stammdaten ab
fahrgastrechte station <suchbegriff>                  # Stationssuche (Timetables-API /station/{pattern})
fahrgastrechte trip --date 2026-10-12 --train 12345   # Rekonstruktion aus Echtzeitdaten
fahrgastrechte add [...]                             # F1: manuell
fahrgastrechte add --from-trip <trip-ref>             # F2: aus Rekonstruktion
fahrgastrechte list [--status ...] [--since ...]     # F3
fahrgastrechte show <id>                             # F4
fahrgastrechte calc <id>                             # F5, auch ohne Speichern
fahrgastrechte status <id> <neu> [--note ...]         # F7
fahrgastrechte export pdf --ids 1,2,3                # F8
fahrgastrechte export csv [--status offen]           # F9
fahrgastrechte config set|get|list                   # F10
fahrgastrechte stats [--month 2026-10]               # F11
```

Alle Befehle: `--json` für Maschinen, Standardausgabe Tabelle via `rich` (wie `ilias-cli`). Aufruf-Dokumentation in `--help` mit Beispielen.

## 6. MCP-Tools (Phase 2, `fahrgastrechte-mcp`)

Jeder Must- und Should-Befehl wird 1:1 zu einem FastMCP-Tool, json-Schema strikt typisiert. Schreibende Tools (`add_delay_case`, `update_status`, `set_config`) erfordern eine explizite Bestätigung im UI (passt zum Owner-Gate aus `ilias-mcp`). Read-only-Tools (list, show, calc, stats) sind frei.

```text
add_delay_case(...)
list_delay_cases(status?, since?, until?) -> [...]
show_delay_case(id) -> {...}
compute_compensation(ticket_type, delay_min, ticket_price_cents) -> {cents, reason, cap_applied}
reconstruct_trip(date, train_number, start_eva, dest_eva) -> {...}    # Timetables-API
search_station(pattern) -> [...]
check_duplicate(date, train_number, start_eva, dest_eva) -> {id?, exists}
update_status(id, new_status, note?) -> {...}
export_pdf(ids, out_path) -> {path, size}
export_csv(status, out_path) -> {path, rows}
get_config(key) / set_config(key, value) / list_config()
stats(month?) -> {case_count, expected_cents, below_threshold, cap}
```

## 7. Datenquellen (verifiziert 12.10.2026)

**Empfehlung für Phase 1: DB API Marketplace — Timetables (IRIS).** Begründung: offiziell, kostenlos, mit klaren Quoten, lizenziert (CC BY 4.0), und genau das, was wir für die Rekonstruktion brauchen (Soll/Ist-Verspätung eines konkreten Zuges an einem konkreten Tag an einem konkreten Bahnhof).

| Quelle                                                              | Status     | Lizenz                | Empfehlung                                                                                                                       |
| ------------------------------------------------------------------- | ---------- | --------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| DB API Marketplace — **Timetables** (IRIS) [V, developers.deutschebahn.com/db-api-marketplace/apis/product/timetables] | aktiv      | CC BY 4.0             | **Primär.** Endpoints: `GET /fchg/{evaNo}` (Gleis-/Zugänderungen), `GET /plan/{evaNo}/{date}/{hour}` (Sollfahrplan), `GET /rchg/{evaNo}` (Echtzeit-Abweichungen), `GET /station/{pattern}`. Free-Tier: 60 Aufrufe/Minute, Client-ID + API-Key. **Nur Züge, keine Busse/Straßenbahnen** — für D-Ticket im Nahverkehr reicht das, weil 95 % der Betroffenheit Schiene ist. |
| v6.db.transport.rest (`db-vendo-client`) [V, v6.db.transport.rest]  | aktiv      | Drittanbieter, kein Key nötig | **Sekundär.** Nutzbar ohne Anmeldung, 100 req/min (Burst 200). ETag/Cache-freundlich. Achtung: `journeys()` enthält **keine Echtzeit** — Verspätungen kommen nur in `departures()`. Wird heute von `db-vendo-client` (Movas) statt der alten HAFAS-API gespeist. |
| DB HAFAS (offiziell) [V, github.com/public-transport/hafas-client/issues/331] | **dauerhaft abgeschaltet** | n. a. | **Nicht nutzbar.** Die alte HAFAS-API ist seit einiger Zeit tot; darauf basierende Tools müssen migrieren. |
| `hafas-client` mit OEBB-Profil [V, github.com/public-transport/hafas-client] | aktiv      | ISC                   | Mögliche Alternative für Verspätungsdaten von Zügen in Deutschland, falls Timetables-API nicht ausreicht (z. B. wenn Busse gebraucht werden). |
| bahn.expert [V, bahn.expert]                                        | aktiv      | Web-Frontend, keine offene API | **Nicht nutzbar** als Datenquelle. Nützlich nur, um die Rekonstruktion manuell zu prüfen. |
| GTFS/GTFS-RT (DELFI, Transitous, gtfs.de) [V, transitous.org]       | aktiv      | je nach Feed          | **Mittelfristig.** Für bundesweite Verspätungsstatistik und Validierung, aber Overhead für Phase 1 zu groß.                       |
| LameTrain [V, lametrain.de]                                         | seit **15.11.2020** eingestellt | n. a. | Historische Referenz. Kein aktiver Konkurrent mehr.                                                                              |
| sha2nkt/delay_bahn [V, github.com/sha2nkt/delay_bahn]                | Open Source, MIT  | MIT              | Aktivste direkte Referenzimplementierung (Python, rekonstruiert Fahrten, klickt sich in den DB-Antragsflow). Inspiration, nicht Codebasis. |
| bahn.de Antragsformular (manuell)                                   | aktiv      | n. a.                 | **Ziel des Exports.** Tool bereitet vor, Mensch schickt ab.                                                                       |

**Auth-Modell DB Timetables:** `DB-Client-Id` und `DB-Api-Key` als Header. Beide werden über `fahrgastrechte config set` (Keyring) verwaltet, nie in der DB oder im Repo. Free-Plan reicht für 60 req/min, mehr als genug für manuelle Erfassung.

**Falls die Timetables-API für den konkreten Fall nichts liefert** (z. B. Linie nicht in IRIS, Busse/Straßenbahnen, lückenhafte Historie > 7 Tage): Fall als `data_quality: best_effort` oder `manual_only` markieren. Keine erfundenen Ist-Zeiten.

## 8. Regeln (verifiziert 12.10.2026, mit Quelle)

Alle Werte nach offiziellen Quellen, Stand 12.10.2026. **Kein erfundener Betrag.**

| Ticket-Typ                  | Verspätung am Ziel | Entschädigung                                                                  | Quelle                                                                                                                                                            |
| --------------------------- | ------------------ | ------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Deutschlandticket**       | ≥ 60 min           | **1,50 € pro Fall** (2. Kl.)                                                  | bahn.de/faq/deutschlandticket-verspaetung-erstattung [V] · § 3 EVO (erheblich ermäßigtes Beförderungsentgelt) [V]                                                  |
| Nahverkehrs-Zeitkarte (2. Kl.) | ≥ 60 min         | **1,50 € pro Fall** (2. Kl.), 2,25 € (1. Kl.)                                 | bahn.de/service/informationen-buchung/fahrgastrechte/rechtliche-regelungen [V]                                                                                    |
| Fernverkehrs-Zeitkarte (2. Kl.) | ≥ 60 min       | **5,00 € pro Fall** (2. Kl.), 7,50 € (1. Kl.)                                 | bahn.de/service/.../rechtliche-regelungen [V]                                                                                                                     |
| BahnCard 100                | ≥ 60 min           | **10,00 € pro Fall** (2. Kl.), 15,00 € (1. Kl.)                               | bahn.de/service/.../rechtliche-regelungen [V]                                                                                                                     |
| Einzelticket Nahverkehr     | ≥ 60 min           | **25 % des Fahrpreises (einfache Fahrt)**                                      | bahn.de/service/.../rechtliche-regelungen [V]                                                                                                                     |
| Einzelticket Nahverkehr     | ≥ 120 min          | **50 % des Fahrpreises**                                                      | bahn.de/service/.../rechtliche-regelungen [V]                                                                                                                     |
| Einzelticket Fernverkehr    | ≥ 60 min           | **25 % des Fahrpreises**                                                       | bahn.de/service/.../rechtliche-regelungen [V]                                                                                                                     |
| Einzelticket Fernverkehr    | ≥ 120 min          | **50 % des Fahrpreises**                                                       | bahn.de/service/.../rechtliche-regelungen [V]                                                                                                                     |
| **Bagatellgrenze**          | beliebig           | Beträge **< 4,00 €** werden nicht ausgezahlt; Fälle sammeln                    | bahn.de/service/.../rechtliche-regelungen [V]                                                                                                                     |
| **Deckel pro Zeitraum**     | beliebig           | **max. 25 % des Ticketwerts** (D-Ticket 49 € → 12,25 €; 58 € → 14,50 €)       | bahn.de/faq/deutschlandticket-verspaetung-erstattung [V]                                                                                                          |
| Letzte Verbindung / 0–5 Uhr Ankunft | ≥ 60 min   | Nutzung ICE/IC erlaubt (separates Ticket, bis 120 € erstattungsfähig)          | bahn.de/faq/.../deutschland-ticket/fahrgastrechte [V]                                                                                                              |
| Reisekette                  | eine Kette = ein Fall bei Anschlussverlust                                    | bahn.de/service/.../rechtliche-regelungen [V]                                                                                                                     |

**Wichtige Ausschlüsse für das Tool:**
- Kein durchgängiger Anspruch zwischen Nahverkehr (D-Ticket) und Fernverkehr (separates Ticket). Zwei getrennte Buchungen, zwei getrennte Anträge.
- Kein Anspruch auf Erstattung von Fernverkehrsfahrkarten, wenn mit D-Ticket im ICE gefahren (Fernverkehr ist beim D-Ticket ohnehin nicht enthalten).
- Antrag immer beim **ersten EVU der Reisekette**; bei D-Ticket ist das meist das EVU der genutzten Linie. Tool schlägt vor, der Mensch entscheidet.

## 9. Nicht-funktionale Anforderungen

- **N1 Sicherheit & DSGVO:**
  - Stammdaten (Name, Adresse, IBAN) nur lokal, im OS-Keyring oder in `chmod 0600`-Datei unter `~/.config/fahrgastrechte/`. Nichts in der SQLite-DB im Klartext für sensible Felder, sondern Referenz auf Keyring-Key [A].
  - Keine Telemetrie, keine Cloud-Sync, keine Logs nach extern.
  - DB-API-Key wird über `keyring` oder Env-Variable aufgenommen, nicht im Repo.
- **N2 Schonender API-Zugriff:** Max. 30 req/min selbstauferlegt (Timetables erlaubt 60), Retry mit Exponential-Backoff, ETag-Caching, eigener User-Agent `fahrgastrechte/0.1 (+contact: samuel@example.invalid)`.
- **N3 Robustheit:** Rekonstruktion darf nie aus einem API-Fehler ein erfundenes `delay_min_arr` machen. Bei Fehler: `data_quality = best_effort` und `delay_min_arr = NULL`; UI zeigt klare Lücke.
- **N4 Ausgabe:** Text-Default für Menschen, `--json` strikt und versioniert, ISO 8601 mit Zeitzone Europe/Berlin, `cents` als Ganzzahl, `delay_min` als Ganzahl.
- **N5 Cross-Plattform:** Linux (primär), macOS, Windows. Python 3.11+, Verwaltung mit `uv`.
- **N6 Testbarkeit:** Wie `ilias-cli`: alle Tests gegen gemockte HTTP-Server (`respx`) und gegen `tmp_path` für SQLite. Keine Tests, die Live-Server berühren; ein optionaler Smoke-Test gegen Timetables-API mit `--network`-Flag.
- **N7 Konfigurierbarkeit:** Ticket-Preis, Ticket-Typ, EVU konfigurierbar; Standardannahme: `d_ticket` zum aktuellen Monatspreis, abfragbar im Setup.
- **N8 Datumsgrenzen:** D-Ticket-Frist für Antragstellung: laut EVO § 14 in der Regel 3 Monate, danach verfällt der Anspruch. Tool markiert Fälle älter als 90 Tage als „Frist riskiert".

## 10. Nicht-Ziele (Phase 1)

- Automatisches Einreichen des Antrags (Post, E-Mail, Webformular) — bewusst nicht, RDG-Risiko.
- Anwaltsfunktion, Inkasso, Musterverfahren.
- Mehrnutzer-Konten, Mandantenfähigkeit.
- Cover weiterer Verkehrsunternehmen über DB hinaus (VRS-, HVV-, BVG-Schnittstellen etc.) — in Phase 2.
- Mobile App.
- Web-UI.

## 11. Risiken

| Risiko                                                                 | Wahrscheinlichkeit | Impact | Mitigation                                                                                                                                                       |
| ---------------------------------------------------------------------- | ------------------ | ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **RGD/Recht (Rechtsdienstleistungsgesetz):** Tool wirkt wie anwaltliche Beratung. | mittel             | hoch   | Disclaimer im Tool-Output („keine Rechtsberatung"), Tool bereitet nur vor, Mensch schickt ab. README/Lizenz weist darauf hin. KEIN Versand, KEINE Erfolgsversprechen. |
| **Falsche Entschädigungs-Berechnung:** Rechenfehler im Tool führt zu falschem Antrag. | mittel             | mittel | Regeln zentral in `rules.py` mit Unit-Tests gegen die Quell-Tabelle; jede Berechnung gibt `reason` (z. B. „1,50 € × 1 Fall, unter 4 €") für Nachvollziehbarkeit.    |
| **API-Key-Leak:** DB-Client-Id/Key landen im Repo oder in Logs.        | mittel             | mittel | `keyring` als Default, `.env` mit `.gitignore`, Pre-Commit-Hook `gitleaks`-ähnlich (siehe `ilias-cli/.gitleaksignore`).                                          |
| **Datenpanne (Stammdaten):** Klarname/IBAN auf verlorenem Laptop.       | gering              | hoch   | Verschlüsselte Stammdaten-Datei, Key im Keyring, klare Doku zur Datenhaltung.                                                                                    |
| **Erinnerungs-Irrtum:** Bagatellgrenze ändert sich politisch (Stand 2026: 4 €). | gering              | mittel | `rules.py` mit `RULES_LAST_VERIFIED` und Hinweis beim Tool-Start, wenn > 90 Tage alt.                                                                            |
| **Timetables-API unvollständig für Busse/Trams:** D-Ticket deckt auch Bus ab. | hoch               | gering | Fall manuell ergänzbar; `data_quality = manual_only`; später db-vendo oder GTFS-RT.                                                                              |
| **Rekonstruktion sagt etwas anderes als Erinnerung:** Streit mit EVU.    | mittel             | mittel | Original-Daten (`trips.data_source`, `planned_arr`, `actual_arr`) bleiben auditierbar; Export enthält Quelle + Zeitstempel.                                      |

## 12. Architektur & Konventionen

- **Sprache:** Python 3.11+ (passt zu `hai-mcp` und `ilias-cli`/`ilias-mcp`, keine Misch-Stacks).
- **Stack:** `typer` + `rich` (CLI), `httpx` + `respx`, `pydantic`, `sqlite3` (stdlib), `keyring`, `reportlab` (PDF-Export), `pytest`. MCP-Server: `mcp` (FastMCP), `>=1.9`.
- **Layout (CLI-Repo):** `src/fahrgastrechte_core/` (Logik, Modelle, Connectoren, Regeln) + `src/fahrgastrechte_cli/` (Typer-App, dünne Hülle).
- **Layout (MCP-Repo):** `src/fahrgastrechte_mcp_core/` (gevendored aus CLI-Core, manuell synchronisiert wie `ilias-mcp`) + `src/fahrgastrechte_mcp/` (FastMCP-Server).
- **Build:** `uv` + `hatchling`, `pyproject.toml` mit `[project.scripts] fahrgastrechte = "fahrgastrechte_cli.main:main"`.
- **Lizenz:** MIT (wie `ilias-cli`).
- **Git:** Zwei Repos lokal unter `smlfg/fahrgastrechte-cli` und `smlfg/fahrgastrechte-mcp`. Push erst nach Samuels Freigabe; `GH_TOKEN` aus der Umgebung.
- **Tests:** `pytest` + `respx`; lokaler Fake für Timetables-API; `tmp_path` für SQLite.

## 13. MVP — 6 testbare Schritte, je ein Commit

| #   | Schritt                                                                                                          | Test                                                                                                                                                | Commit-Subject (Vorschlag)                          |
| --- | ---------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------- |
| 1   | Repo-Skelett: `pyproject.toml`, `src/fahrgastrechte_core/__init__.py`, `src/fahrgastrechte_cli/main.py` mit `fahrgastrechte --version` und `fahrgastrechte --json`, Tests grün | `pytest -q` zeigt 1 Smoke-Test „cli help exits 0"; `uv run fahrgastrechte --version` gibt `0.1.0` aus                                              | `chore: bootstrap fahrgastrechte-cli skeleton`       |
| 2   | Datenmodell + Storage: `models.py` (Pydantic), `db.py` (SQLite-Schema aus § 4), `konto.db` anlegen, `init`-Befehl, `config get/set` mit Keyring      | Tests mit `tmp_path`: Schema-Migration v0→v0 ok, `init` legt Datei an, `config get/set` round-trip                                               | `feat: storage + pydantic models + config`          |
| 3   | Connector `timetables`: `client.py` mit `search_station`, `get_plan`, `get_realtime_changes`, gemockt via `respx`                                                       | Tests gegen `respx`-Fixtures: Stationssuche, Plan-Parse, rchg-Parse mit Soll/Ist; Rate-Limit und Retry getestet                                | `feat: db timetables api connector (mocked)`        |
| 4   | CLI-Kommandos F1/F3/F4: `add` (manuell, mit Validierung), `list` (Tabelle + `--json`), `show <id>`, Duplikatschutz-Hash; `trip` als Wrapper um Connector     | Tests: `add` mit fehlenden Pflichtfeldern → Exit 3; `add` mit Duplikat-Hash → Exit 1 mit Original-ID; `list` zählt 2 Fälle, Summenzeile stimmt | `feat: case add/list/show + duplicate guard`        |
| 5   | Regeln + `calc`: `rules.py` mit Tabelle aus § 8 (dicts, mit `RULES_LAST_VERIFIED`), `compute_compensation(...)`; `calc <id>`-Befehl; `stats`           | Tests gegen die Quell-Tabelle: 1 × 60 min D-Ticket → 1,50 €, 1 Fall unter 4 € → Hinweis, 5 Fälle → 7,50 €; D-Ticket 58 € × 10 Fälle → 14,50 € (Cap) | `feat: compensation rules + calc + stats`           |
| 6   | Export F8/F9: `export pdf` mit `reportlab` (Stammdaten + Tabelle aller ausgewählten Fälle), `export csv`; `status <id> <neu>` mit erlaubten Übergängen   | Tests: PDF wird geschrieben (> 1 KB, enthält Namen + Betrag), CSV ist RFC-4180-konform, Status-Übergang `offen→abgelehnt` ohne `--force` → Exit 3 | `feat: export pdf/csv + status workflow`            |

Danach: Phase 2 = MCP-Server, Sprach-Erfassung, `watch`, weitere EVUs.

## 14. Offene Fragen an Samuel

1. **Ticket-Typ & -Preis pro Monat:** Ist der Default `d_ticket` richtig? Welcher Monatspreis soll gesetzt werden (49 €, 58 €, Solidarmodell, JobTicket, Semesterticket)? Bei Wechsel des Tickets muss `config` das anpassen.
2. **D-Ticket-Sichtausweis-ID:** Steht auf der App oder dem PDF. Willst du die im Tool hinterlegen oder jedes Mal neu eingeben (für die Antragsstellung nötig)?
3. **Mehrere EVUs:** Reicht für Phase 1 „erstes EVU der Reisekette" und du tippst das beim Antrag selbst nach? Oder soll das Tool die Reisekette aus `plan` zusammensetzen und das zuständige EVU vorschlagen?
4. **Erinnerungen:** Soll das Tool dich aktiv erinnern (Cron, Desktop-Notification), wenn das Konto die 4-€-Grenze reißt oder ein Fall älter als 90 Tage wird? Wenn ja, auf welchem Gerät/Kanal?
5. **Push zu GitHub:** `smlfg/fahrgastrechte-cli` und `smlfg/fahrgastrechte-mcp` unter deinem `smlfg`-Org mit `GH_TOKEN` pushen — okay, oder erstmal nur lokal und du pushst selbst?
6. **Sprach-Erfassung (Phase 2):** Mikrofon + lokales Whisper, oder lieber in der Hermes-/HAI-Pipeline mit Voice-Memo + LLM-Extraktion?

---

*Quellen (alle am 12.10.2026 abgerufen):*
- bahn.de: https://www.bahn.de/faq/deutschlandticket-verspaetung-erstattung
- bahn.de: https://www.bahn.de/service/informationen-buchung/fahrgastrechte/rechtliche-regelungen
- bahn.de: https://www.bahn.de/faq/gk/angebot/zeitkarten/deutschland-ticket/fahrgastrechte
- bahn.de: https://www.bahn.de/service/informationen-buchung/fahrgastrechte
- DB API Marketplace — Timetables: https://developers.deutschebahn.com/db-api-marketplace/apis/product/timetables (Lizenz: CC BY 4.0)
- v6.db.transport.rest: https://v6.db.transport.rest/ · Doku: https://v6.db.transport.rest/api.html
- hafas-client (DB-Profil, Status): https://github.com/public-transport/hafas-client/issues/331
- Transitous / GTFS-RT: https://transitous.org
- Referenzimplementierung: https://github.com/sha2nkt/delay_bahn
- Eigene Projekte (Konventionen): https://github.com/smlfg/ilias-cli · https://github.com/smlfg/ilias-mcp
