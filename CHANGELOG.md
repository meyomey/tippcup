# Changelog

Alle wesentlichen Änderungen am Tippcup-Projekt werden hier dokumentiert.

---

## [Aktuell] – Mai 2026

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
