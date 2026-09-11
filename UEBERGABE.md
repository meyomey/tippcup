# Übergabe-Notiz für neuen Chat

Stand: siehe CHANGELOG.md für Details. Diese Datei ist eine Kurzübersicht
für den Einstieg in einen neuen Chat.

## Wichtigste Punkte

- **Backup-Bereich überarbeitet** (September 2026): automatisches
  Backup ist jetzt konfigurierbar (welche Komponenten), Restore ist
  granular (DB/Medien/Config einzeln wählbar, sowohl beim Datei-Upload
  als auch direkt aus einem gespeicherten Auto-Backup). Ein echter,
  vorbestehender Bug wurde dabei gefunden und gefixt (`io`-Modul war
  nie importiert – ZIP-Restore wäre gecrasht). Helper-Funktionen für
  Config-Werte vereinheitlicht: `get_setting()`/`set_setting()` statt
  mehrerer Dopplungen. Details siehe CHANGELOG.md.

- **⚠️ GitHub und der Live-Server (tippcup.com) sind komplett getrennt!**
  `git push` lädt NICHTS automatisch auf den Server. Jede Änderung muss
  zusätzlich manuell und VOLLSTÄNDIG per FTP hochgeladen werden – siehe
  „Deployment"-Abschnitt weiter unten. Ist bereits einmal schiefgegangen
  (September 2026): nur `app.py` wurde hochgeladen, `templates/` blieb
  alt → CSRF-Feld fehlte im Login-Formular → alle Logins mit 403
  abgelehnt. Beim nächsten Deploy IMMER den kompletten geänderten
  Dateibaum hochladen, nicht nur einzelne Dateien.

- **Automatisches tägliches Backup** (September 2026): läuft entweder
  passiv über Seitenaufrufe mit (kein Cron nötig) oder optional exakt
  getimed über `/cron/backup/<token>` (Token in Admin → Backup-Seite).
  Landet in `backups/` (nicht im Git-Repo), Aufbewahrung konfigurierbar.
  Siehe CHANGELOG.md.

- **Projekt liegt jetzt auf GitHub** (siehe README.md für Repo-Link und
  Setup-Anleitung). Lokales Arbeitsverzeichnis in einer neuen Chat-Session
  jetzt per `git clone` statt ZIP-Upload möglich (ZIP-Upload funktioniert
  weiterhin als Fallback).

- **Sicherheits-Audit (September 2026) durchgeführt und Fixes eingebaut** —
  siehe CHANGELOG.md Abschnitt "September 2026". Kurzfassung: Scoring-Bug
  bei fehlenden Liga-Tipps behoben (Zufalls-Tabelle via
  `autofill_missing_predictions()`), Traceback-Leaks entfernt,
  CSRF-Schutz projektweit eingebaut (eigene Implementierung, kein
  pip-Paket nötig), Secret-Key-Fallback entfernt, Security-Header +
  Cookie-Secure ergänzt, Admin-Impersonation-Randfall abgesichert,
  Rate-Limiting auf Profil-Passwort ausgeweitet, Tie-Break-Determinismus
  in allen Ranglisten-Queries. **WICHTIG:** `SECRET_KEY` MUSS in
  `passenger_wsgi.py` gesetzt sein (sonst nur temporärer Zufalls-Key
  pro Prozessstart — Sessions/Logins werden bei jedem Neustart
  ungültig)!

- **In-App-Chat & Umfragen sind komplett entfernt** (Feature existierte
  im Frontend schon länger nicht mehr, aber Datenbank-Tabellen und
  toter Code waren noch da). Falls auf dem Server noch alte Daten in
  `chat_messages`/`chat_reactions`/`polls`-Tabellen liegen: wurden NICHT
  automatisch gelöscht, ggf. manuell aufräumen.

- **Domain-Migration** liga.tippcup.com → tippcup.com läuft/ist erfolgt.
  Admin → Domain-Migration Seite (`/admin/migration`) enthält Checkliste,
  Push-Cleanup-Button, Telegram-Webhook-Updater.

- **Liga-getrenntes Tippsperren**: BL1 und BL2 haben jeweils eigene
  Deadline + Lock-Flag (`deadline_bl1/2`, `locked_bl1/2` in `seasons`).
  Wichtig: NICHT mehr die früheste Deadline für beide Ligen nutzen.

- **Nachfrist-System**: Admin kann einzelnen Usern eine Ausnahme für
  verspätetes Tippen gewähren (Tabelle `nachfrist`). Beim Entziehen wird
  NICHT gelöscht, sondern `revoked_at` gesetzt — Historie bleibt für den
  Saisonrückblick erhalten. Strafgeld wird später auf der Hauptversammlung
  festgelegt und kann nachträglich im Nachfrist-Modal eingetragen werden.
  Nachfrist gilt für BEIDE Ligen gleichzeitig (nicht liga-getrennt) —
  sie hebt `locked_leagues` beim Tippen komplett auf.

- **Admin-Impersonation**: Button in Benutzerverwaltung, um die App als
  ein bestimmter User zu sehen. Roter Banner oben zum Zurückwechseln.

- **Tipp-Status-Seite** (`/tipp-status`): öffentliche Übersicht wer wann
  getippt hat, inkl. Rechtzeitigkeit pro Liga, sortierbar.

- **Saisonrückblick** wurde stark erweitert: Zu spät/Nachfrist,
  Volltreffer-König, Aufsteiger/Absteiger, Ewige Tabelle jetzt ganz unten.

## Bekannte Stolperfallen (bitte beim nächsten Mal vermeiden)

1. `@app.route(...)` Decorators gehen bei größeren str_replace-Edits
   gelegentlich verloren → nach jeder Änderung `python3 -c "import app"`
   zum Syntax-Check.
2. `sqlite3.Row` hat KEIN `.get()` — vor `.get()`-Zugriffen immer erst
   `dict(row)` wandeln. Betrifft auch Jinja2-Templates (`season.get(...)`
   funktioniert dort NICHT, nur `season['key']` oder `season.key`).
3. Nach Refactoring/Strukturierung von app.py: prüfen ob `send_file`,
   `make_response`, `render_template_string` noch im globalen
   `from flask import (...)`-Block stehen.
4. Jinja2 kennt kein `not in`-Test in `selectattr()` — stattdessen mit
   einer normalen `{% for %}`-Schleife arbeiten.
5. Modals gehören NIEMALS in eine `<table>` — sonst reagiert
   `data-bs-toggle="modal"` nicht (ungültiges HTML, Bootstrap findet
   das Element nicht).
6. **Neue Formulare (POST) brauchen das CSRF-Hidden-Field**:
   `<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">`
   direkt nach dem `<form method="POST">`-Tag — sonst 403. Bei neuen
   `fetch()`-POST-Aufrufen ist das NICHT nötig, die werden automatisch
   vom globalen Wrapper in `base.html` abgedeckt (außer bei Skripten
   außerhalb des Seitenkontexts wie `static/sw.js` — dort ggf. den
   Endpunkt in `CSRF_EXEMPT_ENDPOINTS` eintragen, siehe `app.py`).
7. Reine Python-f-string-HTML-Blöcke (z. B. Diagnose-Seite,
   `admin_repair_tips()`) nutzen `{variable}` (Python-Interpolation),
   KEIN Jinja — `{{ csrf_token() }}` funktioniert dort nicht, sondern
   `{get_csrf_token()}`. Nur bei `render_template_string(...)` mit
   NICHT-f-präfigiertem String ist `{{ csrf_token() }}` (Jinja) richtig.
8. `tippspiel.db`, `*.env`, echte Konfigurationsdateien mit Zugangsdaten
   NIE committen — siehe `.gitignore` im Projekt-Root. Vor jedem
   `git add`/Commit kurz `git status` prüfen.
9. **Es gibt jetzt eine Testsuite** (`tests/`, pytest, 30 Tests) —
   nach größeren Änderungen an `calculate_scores_for_season`,
   `autofill_missing_predictions`, `/predict`, Login/CSRF oder
   Admin-Berechtigungen IMMER `pytest` laufen lassen, bevor der Code
   als fertig gilt. Bei neuen kritischen Funktionen einen passenden
   Test in der jeweiligen `tests/test_*.py`-Datei ergänzen (siehe
   `tests/README.md`).

## Arbeitsweise

Lokales Sandbox-Verzeichnis (`/home/claude/tippspiel`) wird zwischen
Chat-Sessions NICHT automatisch beibehalten. Zu Beginn einer neuen
Session: entweder das GitHub-Repo klonen ODER `tippcup_uebergabe.zip`
entpacken (bzw. `app.py` einzeln hochladen) BEVOR mit Edits begonnen
wird.

## Server-Pfad (Netcup/Plesk)

```
/var/www/vhosts/hosting139268.a2e64.netcup.net/tippcup.com/tippspiel/
```

Domain liga.tippcup.com läuft ggf. noch parallel — siehe
Domain-Migration-Notizen oben.

## Deployment (WICHTIG — bitte genau befolgen)

GitHub (`github.com/meyomey/tippcup`) ist nur die Code-Ablage/Versionierung.
**Es gibt keine automatische Verbindung zum Live-Server.** Nach jeder
Änderung müssen die betroffenen Dateien manuell per FTP auf den
Server-Pfad oben hochgeladen werden.

**Seit September 2026 liefert Claude nach jeder inhaltlichen Änderung
automatisch zwei komplette ZIPs statt einzelner Dateien** (behebt genau
das Problem, das zum Login-Ausfall geführt hat — siehe Vorfall unten):
- `tippcup_deploy.zip` — kompletter Ersatz für den FTP-Upload (app.py,
  templates/, static/, vendor/, openliga.py, telegram_bot.py,
  requirements.txt, docs/, passenger_wsgi.example.py). OHNE
  tippspiel.db, OHNE echte passenger_wsgi.py, OHNE backups/-Inhalt.
- `tippcup_github.zip` — 1:1 Abbild des committeten Repo-Stands
  (per `git archive`), inkl. tests/, pytest.ini, .gitignore.

Falls eine neue Chat-Session das noch nicht automatisch tut: **explizit
danach fragen** ("kannst du mir wieder ein Deploy-ZIP und ein
GitHub-ZIP bauen").

**Checkliste vor jedem Upload:**
1. Lokal `python3 -c "import app"` und `pytest` laufen lassen — beides
   muss fehlerfrei durchlaufen.
2. **Alle** geänderten Dateien identifizieren (z.B. `git status` bzw.
   `git diff --name-only <alter-commit> <neuer-commit>`) — nicht nur
   `app.py` hochladen, wenn sich auch `templates/` geändert hat!
   Gerade bei CSRF-/Formular-Änderungen betrifft das fast immer sowohl
   `app.py` als auch mehrere `templates/*.html`-Dateien gleichzeitig.
3. Per FTP hochladen — **NIEMALS mit hochladen:**
   `tippspiel.db` (echte Nutzerdaten!), die echte `passenger_wsgi.py`
   auf dem Server (nur bei tatsächlichen Config-Änderungen anfassen,
   z.B. neuer `SECRET_KEY` oder `DB_PATH`).
4. Nach dem Upload: Passenger-Prozess neu starten (leere Datei
   `tmp/restart.txt` im Projektordner anlegen, oder über Plesk →
   Node.js/Python-Apps → Neu starten).
5. Kurz die Seite testen (Login, eine Kernfunktion) — bei
   Formular-/CSRF-Änderungen unbedingt mit Hard-Refresh
   (Strg+Shift+R), da sonst der Browser-Cache eine alte Seite ohne
   aktuelles CSRF-Feld anzeigen kann.

**Vorfall September 2026 (zur Erinnerung):** Nach dem CSRF-Feature
wurde nur `app.py` hochgeladen, `templates/login.html` blieb auf dem
alten Stand ohne CSRF-Hidden-Field → jeder Login-Versuch endete mit
403 "Kein Zugriff". Ursache war schnell gefunden (Browser-Konsole zeigte
`POST /login → 403`, Seitenquelltext zeigte das fehlende Feld), aber
komplett vermeidbar gewesen durch einen vollständigen Upload.

