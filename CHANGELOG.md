# Changelog

Alle wesentlichen Änderungen am Tippcup-Projekt werden hier dokumentiert.

---

## [Aktuell] – September 2026 – Backup-Bereich aufgeräumt & granulares Restore

Vollständige Überprüfung des Backup/Restore-Bereichs auf Wunsch (Dopplungen,
Übersichtlichkeit, Granularität). Ergebnisse und Fixes:

### Gefunden & behoben
- **Config-Helper-Dopplung:** `_get_config_value`/`_set_config_value`
  (aus dem Backup-Feature) waren eine unnötige Kopie der bereits
  vorhandenen `db_config()`-Logik. Zusammengeführt zu einer einzigen
  Quelle: `get_setting()`/`set_setting()`, von Python-Code und
  Templates gleichermaßen genutzt.
- **Echter, vorbestehender Bug gefunden:** `io` war im gesamten Projekt
  nie als Modul importiert (nur lokal einmal als `_io`-Alias), der
  alte ZIP-Restore-Code nutzte aber `io.BytesIO(...)` direkt – wäre
  bei jedem ZIP-Restore mit `NameError` abgestürzt. War schlicht nie
  getestet worden. Jetzt als globaler Import gefixt.
- **Automatisches Backup war nicht konfigurierbar** (immer DB+Medien+
  Config hart kodiert). Jetzt per Checkbox einstellbar, was automatisch
  gesichert wird (`backup_auto_include_db/uploads/config`).
- **Restore war nicht granular:** DB und Medien wurden bisher immer
  mitgenommen, wenn im ZIP vorhanden – nur Konfigdateien waren per
  Checkbox optional. Jetzt sind alle drei Komponenten unabhängig
  wählbar, sowohl beim Hochladen einer Datei (`/admin/restore`) als
  auch beim neuen Direkt-Restore aus einem gespeicherten Backup.
- **Neu: Direkt-Restore ohne Download-Umweg** – jedes automatische
  Backup in der Liste hat jetzt einen "Wiederherstellen"-Knopf
  (`/admin/backup/restore-from-auto/<datei>`), der dieselbe granulare
  Auswahl (DB/Medien/Config) bietet wie der Datei-Upload-Weg.
- Gemeinsame Restore-Logik in `_apply_restore_from_zip_bytes()`
  extrahiert – vermeidet Code-Dopplung zwischen Upload-Restore und
  Direkt-Restore.
- Veraltete "Backup-Strategie"-Karte (empfahl noch manuelle
  wöchentliche Backups, widersprach dem automatischen täglichen
  Backup) entfernt, durch klare Erklärung der jetzt drei Wege ersetzt
  (automatisch / Sofort-Download / Wiederherstellen).
- 8 neue Tests (`TestGranularAutoBackupContent`, `TestGranularRestore`)
  – insgesamt jetzt 50 Tests, alle grün.
- Kleines Test-Aufräumen: `tests/conftest.py` entfernt jetzt auch
  `.before_restore`/`.restore_tmp`-Artefakte, die Restore-Tests
  erzeugen können (eine solche Datei war versehentlich fast committet
  worden – `.gitignore` entsprechend erweitert).

### Bewusst nicht angefasst
Es gibt im Projekt zusätzlich eine zweite, komplett ungenutzte
Konfigurationstabelle (`app_config` mit eigenem `get_config()`/
`set_config()`) neben der tatsächlich verwendeten `config`-Tabelle –
totes Gerüst aus früherer Entwicklung. Nicht angefasst, da außerhalb
des Backup-Themas und mit Risiko für andere Funktionen.

---

## [Vorfall] – September 2026 – Login-Ausfall nach unvollständigem Deploy

Nach dem CSRF-Feature (siehe Audit-Eintrag unten) wurde beim FTP-Deploy
nur `app.py` hochgeladen, `templates/login.html` blieb auf dem alten
Stand ohne CSRF-Hidden-Field. Ergebnis: jeder Login-Versuch scheiterte
mit 403 "Kein Zugriff" (Browser-Konsole: `POST /login → 403`).

**Kein Code-Fehler** – reines Deployment-Problem (GitHub ≠ Live-Server,
keine automatische Synchronisierung). Behoben durch vollständigen
Re-Upload aller geänderten Dateien. Siehe UEBERGABE.md, neuer Abschnitt
„Deployment" mit Checkliste, um das künftig zu vermeiden.

---

## [Aktuell] – September 2026 – Automatisches tägliches Backup

Neue Funktion: automatische, tägliche Backups (DB + Medien + Config)
landen als ZIP im neuen `backups/`-Verzeichnis (nicht im Git-Repo,
siehe `.gitignore`).

### Funktionsweise
- **Zwei Trigger-Wege, beide nutzbar:**
  1. **Ohne jede Konfiguration:** Bei jedem Dashboard-Aufruf eines
     beliebigen Users wird geprüft, ob das letzte automatische Backup
     ≥23h her ist – falls ja, läuft es im Hintergrund (eigener Thread,
     blockiert den Seitenaufruf nicht). Funktioniert, solange täglich
     mindestens einmal irgendjemand die Seite besucht – kein Cron nötig.
  2. **Optional, für einen exakten Zeitpunkt:** Ein neuer Endpoint
     `/cron/backup/<token>` (Token in der Admin-Backup-Seite sichtbar,
     regenerierbar) kann von Plesk „Geplante Aufgaben" (curl) oder einem
     externen Dienst wie cron-job.org einmal täglich aufgerufen werden.
     Sessionlos, daher CSRF-Schutz-Ausnahme (wie Telegram-Webhook).
- **Aufbewahrung konfigurierbar** (Standard: 14 Tage), ältere
  automatische Backups werden automatisch gelöscht.
- **Admin-UI** (`/admin/backup/page`): Ein/Aus-Schalter, Aufbewahrungsdauer,
  Zeitpunkt des letzten Versuchs UND des letzten *erfolgreichen* Laufs
  (getrennt sichtbar, falls ein Lauf fehlschlägt), Liste vorhandener
  Auto-Backups mit Download/Löschen, „Jetzt sofort erstellen"-Button.
- Der bestehende manuelle Backup-Download (`/admin/backup`) funktioniert
  unverändert weiter – beide Wege nutzen jetzt dieselbe ZIP-Bau-Funktion
  (`build_backup_zip_bytes`), um Doppelcode zu vermeiden.
- 12 neue Tests in `tests/test_backup.py` (Trigger-Zeitfenster, Force,
  Deaktivierung, Pruning, Cron-Token, Path-Traversal-Schutz beim
  Download, Admin-Berechtigung).
- Kleiner Testsuite-Fix: Hintergrund-Backup-Trigger wird bei
  `app.config['TESTING']=True` übersprungen (sonst Race Condition mit
  der schnell wechselnden Test-DB zwischen einzelnen Tests).

---

## [September 2026] – Sicherheits- & Fairness-Audit

Vollständiger Code-Audit durchgeführt (Funktionalität, Tippspiel-Logik,
Punkteberechnung, Datenintegrität, Sicherheit) – siehe Ergebnisse und
Fixes unten. Alle Fixes wurden gegen eine Test-DB verifiziert
(Login-/CSRF-/Autofill-/Impersonation-Tests).

### Kritisch behoben
- **Scoring-Bug:** Ein User, der für eine bereits gesperrte Liga keinen
  Tipp abgegeben hat, bekam bisher automatisch die HÖCHSTMÖGLICHE
  Punktzahl für diese Liga (da 0 Abweichung = perfekter Tipp gerechnet
  wurde). Neue Funktion `autofill_missing_predictions()`: läuft
  automatisch vor jeder Punkteberechnung. Wenn die Deadline einer Liga
  erreicht ist, der User keine aktive Nachfrist hat und der Tipp fehlt
  (ganz oder teilweise), wird eine ZUFÄLLIGE Tabelle für die fehlenden
  Plätze erzeugt und in `predictions.is_auto=1` markiert (neue Spalte).
  In der Tipp-Einzelansicht (`/tips/user/<id>`) wird das per Icon
  kenntlich gemacht.
- **Information Disclosure:** 11 Stellen im Code gaben bei
  unerwarteten Fehlern den vollständigen Python-Traceback direkt als
  HTML an den Browser aus (Dateipfade, SQL, interne Variablen –
  auch auf normalen User-Routen, nicht nur Admin). Ersetzt durch
  `app.logger.exception(...)` (nur noch im Server-Log) + generische
  Fehlerseite bzw. Flash-Meldung.

### Sicherheit – hohe Priorität
- **CSRF-Schutz neu eingebaut** (eigene, schlanke Implementierung ohne
  zusätzliches pip-Paket, da FTP-only-Hosting ohne SSH): Session-Token,
  geprüft bei jedem POST/PUT/PATCH/DELETE. Alle 60 Formulare im Projekt
  automatisch mit Hidden-Field versehen; alle `fetch()`-Aufrufe werden
  über einen globalen Wrapper in `base.html` automatisch mit
  `X-CSRFToken`-Header versehen. Telegram-Webhook und `/push/renew`
  sind bewusst ausgenommen (eigene, sessionlose Authentifizierung).
- **Secret-Key-Fallback entfernt:** Der fest im Code hinterlegte
  Fallback-Schlüssel wurde entfernt. Fehlt `SECRET_KEY` in der Umgebung
  (siehe `passenger_wsgi.example.py`), wird bei jedem Prozessstart ein
  neuer Zufalls-Key erzeugt + eine Warnung geloggt, statt eines im
  Repository sichtbaren, statischen Schlüssels.
- **Admin-Impersonation:** Beim Zurückwechseln (`/admin/stop-impersonate`)
  wird jetzt erneut live geprüft, ob der Ursprungs-Admin-Account noch
  Admin-Rechte hat und aktiv ist. Falls er zwischenzeitlich degradiert
  wurde, wird die Session geleert statt fälschlich Admin-Rechte
  wiederherzustellen.
- **Security-Header** ergänzt: `X-Frame-Options`, `X-Content-Type-Options`,
  `Referrer-Policy`, `Strict-Transport-Security` (außerhalb Debug-Modus).
  `SESSION_COOKIE_SECURE` aktiviert (außerhalb Debug-Modus).

### Sicherheit – mittlere Priorität
- **Rate-Limiting Profil-Passwort:** Die Passwortänderung im Profil
  nutzt jetzt denselben IP-basierten Sperrmechanismus wie der Login
  (5 Fehlversuche → 15 Minuten Sperre).
- **Tie-Break-Determinismus:** Alle Ranglisten-Sortierungen (12 Stellen)
  haben jetzt `user_id ASC` als viertes, stabiles Sortierkriterium –
  bei exaktem Gleichstand ist die Reihenfolge nicht mehr von SQLites
  interner (nicht garantierter) Sortierstabilität abhängig.

### Aufräumarbeiten
- **In-App-Chat & Umfragen entfernt:** Die Funktion existiert schon
  länger nicht mehr im Frontend (keine Route mehr vorhanden), aber die
  Datenbank-Tabellen (`chat_messages`, `chat_reactions` – dabei sogar
  **doppelt** und widersprüchlich definiert –, `polls`, `poll_options`,
  `poll_votes`) und toter Code (Cleanup-Logik beim User-Löschen,
  Statistik-Kachel im Admin-Backup) wurden vollständig aus dem Code
  entfernt. Achtung: Falls auf dem Produktivserver noch alte Daten in
  diesen Tabellen liegen, wurden diese NICHT automatisch gelöscht
  (kein `DROP TABLE` im Code) – bei Bedarf manuell/gezielt aufräumen.

### Repository
- Projekt erstmals als Git-Repository initialisiert und nach GitHub
  übertragen (siehe README.md / .gitignore).
- `vendor/`-Verzeichnis mit pywebpush 1.14.1 + Abhängigkeiten ergänzt
  (gezielt für Python 3.9 / manylinux2014_x86_64 gebaut, siehe
  `vendor/VENDOR_INFO.md`).
- **Automatisierte Testsuite (pytest) eingeführt** – 30 Tests in
  `tests/`, decken die im Audit als kritisch identifizierten Bereiche ab:
  Punkteberechnung (inkl. Regressionstest für den Scoring-Bug oben),
  Tipp-Validierung, Login/CSRF, Admin-Berechtigungen,
  Ranglisten-Determinismus, Admin-Impersonation-Randfall. Siehe
  `tests/README.md` für Details zum Ausführen.

---

## [Mai 2026]

### Bestenliste & Rückblick
- Größte Einzel-Abweichung pro Liga (BL1/BL2) mit Vereinsname in Bestenliste und Saisonrückblick
- Desktop-Ansicht: zwei separate Spalten BL1/BL2 (vorher nur ein Gesamtwert)
- Wildcard-Markierung 🎲 + gelber Rahmen + „höchste!"-Label für den Tipper mit der absolut größten Abweichung je Liga
- Funktioniert in Bestenliste (Mobile + Desktop) und Saisonrückblick

### Telegram
- `/rangliste`-Befehl zeigt jetzt alle Teilnehmer (vorher Top 10)
- Sortierung wie App-Bestenliste: `score DESC, std_deviation ASC, volltreffer DESC`
- Admin-Diagnose: Reset-Buttons für Deduplizierung (Rangliste, Highlight, Beide)

### Saison-Abschluss
- Admin → Saison: CSV-Export-Button (⬇ CSV) für jede Saison
- Export im `legacy_results`-Format – direkt importierbar über Admin → Tipp-Archiv
- Per-Liga-Werte werden aus `predictions × standings` berechnet (Abw, Stdabw, MaxAbw, Treffer)
- Spieltage aus `standings.matches_played`, Fallback 34

---

## [April/Mai 2025] – April/Mai 2025

### Archiv (1993–heute)
- Neue DB-Tabellen: `legacy_results`, `legacy_angsthasen`, `legacy_name_map`
- CSV-Import für historische Abschlusstabellen und Angsthasen-Tabellen (1993/94–2024/25)
- `/archiv` – Abschlusstabelle pro Saison mit MaxAbw-Markierung (🔥)
- `/archiv/ewige-tabelle` – normierter Rang-Durchschnitt (Rang×100÷Teilnehmer), farbcodiert
- `/archiv/angsthasen` – historische Angsthasen-Tabellen mit 😎/😱 für Bester/Letzter
- `/archiv/karriere/<name>` – Karriere-Ansicht mit Chart.js Rang-Verlauf
- Name-Mapping: historische CSV-Namen → App-Accounts
- Ewige Tabelle zeigt Archiv-Stats (Rang, Gesamt, Siege) als Zusatzspalten
- Profilseite zeigt historische Karrieredaten (Siege, Top-3, Bester Rang)
- Admin-Import-Seite: CSV-Upload, Name-Mapping, Saison-Löschen

### Medien & Links
- ZIP-Upload mit automatischer Bild-Extraktion (ALLOWED_IMAGES)
- Typ-Filter per Badge-Klick (Bilder, PDF, Word, Excel, Video, Audio)
- Ordner-Sortierung (Name auf-/absteigend), "Alles zuklappen"-Button
- Ordner-Header zeigt aufgeschlüsselte Dateianzahl per Typ mit Icons
- Statistik-Leiste oben mit Gesamtanzahl pro Typ
- Clipboard-Paste (Strg+V) für Datei-Upload
- Neueste Ordner standardmäßig oben

### Saison-Management
- Admin: Inaktive Saisons löschen (mit FK-Cleanup, PRAGMA foreign_keys OFF)
- Admin: Archiv-Saisons löschen per Dropdown

### Telegram
- Deduplizierung: `tg_last_highlight_md` / `tg_last_rangliste_md` in config
- Logging: alle Versuche in `api_cache` (key: `tg_notification_log`), max. 30 Einträge
- Diagnose-Seite: Telegram-Sektion mit Live-Status, Log-Tabelle, Reset-Buttons
- Rangliste-Query: sortiert jetzt korrekt nach `score DESC, std_deviation ASC, volltreffer DESC`

### Backup & Restore
- Selektives ZIP-Backup: DB, Medien, Konfigdateien per Checkbox wählbar
- Dateiname enthält Timestamp und gewählte Komponenten
- Restore akzeptiert ZIP und .db-Datei
- DB wird vor Restore validiert und als `.before_restore` gesichert

### Spieltage
- Live-Spielminute: server-seitig aus Anpfiffzeit berechnet, clientseitig alle 30s aktualisiert
- Anpfiffzeit in der Status-Leiste bei laufenden und beendeten Spielen
- Halbzeit/Pause korrekt erkannt

### Sonstiges
- Benutzer löschen: `poll_votes`, `polls`, `legacy_name_map`, `legacy_results` korrekt bereinigt
- Admin-Diagnose: erweitert um Telegram-Status, Log, Reset-Buttons

---

## [Mai 2025] – Session-Änderungen aus Vorgänger-Chat

### Medien
- Video-Lightbox mit Autoplay
- Audio-Support (mp3, wav, m4a, ogg, aac, flac), 200 MB Limit
- Media-Route mit korrektem MIME-Type
- Admin: Drag-and-Drop Datei-Umordnung

### Chart & Verlauf
- Verlauf-Chart: Start mit eigenem Spieler, andere durchgestrichen, anklickbar
- Landscape-Anpassung via matchMedia, PWA manifest.json orientation: any

### Spieltag-Snapshot
- `get_last_finished_matchday`: max. 3 API-Calls rückwärts, gecacht
- Verlauf-Filter auf `<=` korrigiert
- `has_changed_since` nutzt ebenfalls `get_last_finished_matchday`

### UI
- Hover-Tooltip auf Spielernamen (CSS position:fixed)
- Sticky-Spalten in "Alle Tipps" und Verlaufstabelle
- Admin-UI: Diagnose-Seite `/admin/diagnose`
- Doku-Seiten im Admin: `/admin/handbuch`, `/admin/doku`
- Hilfe-Seite für User: `/hilfe`
