# UX-Spec: fahrgastrechte

`fahrgastrechte` ist das lokale Verspätungskonto für eigene DB-Fahrten. Das Tool
liest Soll-/Ist-Daten über die DB-Timetables-API, legt Fälle lokal in SQLite ab
und bereitet Anträge vor. Den Antrag selbst schickt der Mensch über das
DB-Kundenkonto oder per Post ab; das Tool sendet nichts an die DB.

Die drei wichtigsten Aufgaben sind: Zugangsdaten hinterlegen und einen Bahnhof
per Namen finden, eine Fahrt aus den Timetables prüfen und einen Verspätungsfall
mit Bestätigung lokal anlegen.

## Meta

```ux-meta
{"tool": "fahrgastrechte",
 "install": "pipx install git+https://github.com/smlfg/fahrgastrechte-cli",
 "base_url_env": "FAHRGASTRECHTE_TIMETABLES_BASE_URL",
 "env": {"FAHRGASTRECHTE_HEUTE": "2026-11-04"},
 "credential_env": ["FAHRGASTRECHTE_DB_CLIENT_ID", "FAHRGASTRECHTE_DB_API_KEY"]}
```

`FAHRGASTRECHTE_TIMETABLES_BASE_URL` überschreibt die produktive Basis-URL
`https://apis.deutschebahn.com/db-api-marketplace/apis/timetables/v1` und wird
von der Harness auf den lokalen Fake-Server gesetzt. `FAHRGASTRECHTE_HEUTE`
macht die datumsabhängigen Sitzungen deterministisch; im Alltag gilt das
heutige Datum.

## Sitzungen

### Installation, Login und Stationssuche

Der Login fragt die DB-API-Zugangsdaten interaktiv ab. Der API-Key wird nicht
angezeigt und nicht in der Shell-Historie gespeichert; die Datei mit den
Zugangsdaten liegt unter dem XDG-Konfigurationspfad und hat Rechte 0600. Die
Werte in dieser Sitzung sind synthetisch und keine echten Zugangsdaten.

```ux-session
$ pipx install git+https://github.com/smlfg/fahrgastrechte-cli
$ fahrgastrechte login
< DB-Client-ID:
> test-client-id
< DB-API-Key:
> test-api-key
< Zugangsdaten gespeichert.
! exit 0
$ fahrgastrechte station "Stuttgart Hbf"
< Stuttgart Hbf (EVA 8000096)
! exit 0
```

### Fahrt aus den Timetables prüfen

`trip` liest die aktuellen Änderungen am Zielbahnhof und zeigt die
rekonstruierte Ankunft. Der Befehl speichert noch nichts; das Anlegen erfolgt
bewusst erst mit `add` und der Bestätigungsfrage.

```ux-session
$ fahrgastrechte trip "ICE 123" "Stuttgart Hbf" "Mannheim Hbf"
< Zug: ICE 123
< Von: Stuttgart Hbf (EVA 8000096)
< Nach: Mannheim Hbf (EVA 8000244)
< Ankunft: 17:54 (Soll 15:25, +149 min)
< Nicht gespeichert. Anlegen mit `fahrgastrechte add ...`
! exit 0
```

### Verspätungsfall verbindlich anlegen

`add` zeigt zuerst Zug, Start, Ziel und Datum. Danach fragt es mit Default Nein.
Erst nach `j` werden die Bahnhöfe aufgelöst, die Echtzeitdaten am Ziel gelesen
und der Fall lokal in SQLite angelegt. Bei `N` endet der Befehl ohne den
Echtzeit-Request und ohne lokalen Schreibvorgang.

```ux-session
$ fahrgastrechte add "RE 5" "Stuttgart Hbf" "Ludwigsburg"
< Zug: RE 5
< Von: Stuttgart Hbf (EVA 8000096)
< Nach: Ludwigsburg (EVA 8000235)
< Datum: 2026-11-04
< Fall anlegen verbindlich? [j/N]
> j
< Echtzeit: +33 min
< Fall #1 angelegt (offen)
! exit 0
```

## Lesende Routen

Hier stehen die lesenden Requests der Sitzungen, die nicht zugleich als
Formschritt der schreibenden Aktion dienen. Die Stationssuche nach
`Stuttgart Hbf` ist auch der erste Formschritt von `add`; sie steht deshalb nur
unter `ux-formsteps`, damit der Fake-Server sie eindeutig dem Formschritt
zuordnet.

```ux-routes
[{"method":"GET","path":"/station/Mannheim%20Hbf","response":"tests/fixtures/ux/stations_mannheim_hbf.xml","status":200,"content_type":"application/xml; charset=utf-8"},
 {"method":"GET","path":"/rchg/8000244","response":"tests/fixtures/ux/rchg_mannheim_ice123.xml","status":200,"content_type":"application/xml; charset=utf-8"}]
```

## Formularschritte der schreibenden Aktion

`add` löst vor der Bestätigung beide Bahnhofsnamen über die Stationssuche auf
und zeigt die EVA-Nummern in der Zusammenfassung. Nach `j` liest es die
Echtzeitänderungen am Ziel und legt danach den Fall lokal an. Der lokale
SQLite-Schreibvorgang ist kein HTTP-Request; `final` markiert den letzten
Request davor.

```ux-formsteps
[{"id":"start_station","method":"GET","path":"/station/Stuttgart%20Hbf","sends":[],"response":"tests/fixtures/ux/stations_stuttgart_hbf.xml","before_confirm":true,"live":"erkundet"},
 {"id":"ziel_station","method":"GET","path":"/station/Ludwigsburg","sends":[],"response":"tests/fixtures/ux/stations_ludwigsburg.xml","before_confirm":true,"live":"erkundet"},
 {"id":"echtzeit_ziel","method":"GET","path":"/rchg/8000235","sends":[],"response":"tests/fixtures/ux/rchg_ludwigsburg.xml","final":true,"live":"synthetisch"}]
```

Die Bahnhofsantworten sind echte Live-Antworten des öffentlichen IRIS-Spiegels
vom 2026-11-04. Die Echtzeitantwort für Ludwigsburg ist aus einer Live-Antwort
gekürzt; die Zugnummer `RE 5` wurde für die deterministische Sitzung eingesetzt,
weil zum Aufnahmezeitpunkt kein RE 5 in der Antwort stand.
