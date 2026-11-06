# plainbook – Nutzererlebnis

plainbook ist ein kleiner Kurskatalog ohne Login für die fiktive Volkshochschule.

```ux-meta
{"tool": "plainbook",
 "install": "pipx install git+https://github.com/smlfg/plainbook",
 "base_url_env": "PLAINBOOK_BASE_URL",
 "credential_env": []}
```

## Installieren

```ux-session
$ pipx install git+https://github.com/smlfg/plainbook
! exit 0
```

## Kurs suchen

```ux-session
$ plainbook search volleyball
< Volleyball Mittwoch
! exit 0
```

## Kurs am Freitag suchen

```ux-session
$ plainbook search freitag
< Volleyball Freitag
! exit 0
```

## Kurs buchen

```ux-session
$ plainbook book volleyball
< Auswahl:
> 1
< [j/N]
> j
< Gebucht.
! exit 0
```

```ux-routes
[{"method": "GET", "path": "/items", "response": "tests/fixtures/ux/items.json", "status": 200, "content_type": "application/json; charset=utf-8"}]
```

```ux-formsteps
[{"id": "formular", "method": "POST", "path": "/form", "sends": ["item"], "response": "tests/fixtures/ux/step1_formular.html", "before_confirm": true, "live": "synthetisch"},
 {"id": "daten", "method": "POST", "path": "/form", "sends": ["vorname", "name", "status"], "response": "tests/fixtures/ux/step2_uebersicht.html", "live": "synthetisch"},
 {"id": "bestaetigen", "method": "POST", "path": "/form", "sends": ["fid"], "response": "tests/fixtures/ux/step3_bestaetigt.html", "final": true, "live": "synthetisch"}]
```
