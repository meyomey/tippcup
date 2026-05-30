# ⚽ Tippcup

**Bundesliga-Tippspiel für geschlossene Gruppen** | [liga.tippcup.com](https://liga.tippcup.com)

Tippcup ist ein privates Saisonprognose-Spiel für die 1. und 2. Bundesliga. Jeder Teilnehmer tippt vor dem ersten Spieltag die komplette Abschlusstabelle beider Ligen. Während der Saison wird automatisch berechnet, wer am nächsten dran liegt.

---

## Features

### Tipp-Spiel
- 🏆 Bestenliste mit Live-Score nach jedem Spieltag
- 📊 Verlaufsdiagramm (Platzierung & Score), Head-to-Head, Rückblick
- 🐇 Angsthasen-Analyse (wer tippt nah an der Vorjahrestabelle)
- ⭐ Spieltag-Highlights, Team-Statistiken, Abzeichen-System
- 🌐 Ewige Tabelle (App-Saisons + historischer Archiv-Vergleich)
- ⚽ Live-Spieltage mit Anpfiffzeit und Spielminute

### Archiv (1993–heute)
- 🕰️ Historische Abschlusstabellen seit 1993/94 per CSV-Import
- 📈 Ewige Tabelle (normierter Rang-Durchschnitt)
- 😱 Historische Angsthasen-Tabellen
- 👤 Karriere-Ansicht pro Tipper mit Rang-Verlauf-Chart

### Kommunikation
- 🤖 Telegram-Bot (Gruppen-Nachrichten, Bot-Befehle, Logging, Deduplizierung)
- 🔔 Web-Push-Benachrichtigungen (Chrome, Firefox, Edge, Safari)
- 📧 E-Mail-Rundbriefe via SMTP

### Medien
- 🖼️ Medien-Galerie mit Ordnern, Lightbox, Video-Autoplay
- 📁 ZIP-Upload mit automatischer Bild-Extraktion
- 🔍 Typ-Filter (Bilder, PDF, Word, Excel, Video, Audio)

### Technik
- 📱 Progressive Web App (installierbar, Landscape-Support)
- 🌙 Dark Mode
- 🔒 Brute-Force-Schutz, Session-Management
- 💾 Selektives Backup (DB / Medien / Konfigdateien als ZIP)

---

## Dokumentation

| Dokument | Zielgruppe |
|----------|------------|
| [📖 Benutzerhandbuch](docs/BENUTZERHANDBUCH.md) | Alle Teilnehmer |
| [🔧 Technische Dokumentation](docs/TECHNISCHE_DOKUMENTATION.md) | Entwickler & Administratoren |
| [📋 Changelog](CHANGELOG.md) | Alle |

---

## Quick-Start (lokal)

```bash
git clone https://github.com/dein-user/tippcup.git
cd tippcup
pip install -r requirements.txt
export SECRET_KEY="dein-geheimer-schluessel"
export DB_PATH="tippspiel.db"
python app.py
# → http://localhost:5000  (Login: admin / admin123)
```

---

## Tech-Stack

| Komponente | Technologie |
|---|---|
| Backend | Python 3.9+, Flask 3.x |
| Datenbank | SQLite (via Python stdlib) |
| Frontend | Bootstrap 5, Chart.js, Leaflet |
| Bundesliga-Daten | football-data.org (BL1) + OpenligaDB (BL2) |
| Push | pywebpush 1.14.1 (vendored) |
| Messaging | Telegram Bot API |
| Hosting | Netcup / Plesk / Phusion Passenger |

---

## Umgebungsvariablen

| Variable | Pflicht | Beschreibung |
|---|---|---|
| `SECRET_KEY` | ✅ | Flask-Session-Schlüssel (min. 32 Zeichen) |
| `DB_PATH` | ✅ | Pfad zur SQLite-Datenbank |
| `FD_API_KEY` | optional | football-data.org API-Key (ohne = kein BL1) |
| `VAPID_PRIVATE_KEY` | optional | Web-Push VAPID Private Key |
| `VAPID_PUBLIC_KEY` | optional | Web-Push VAPID Public Key |
| `VAPID_EMAIL` | optional | Web-Push Kontakt-E-Mail |

---

## Hinweise

- **pywebpush 1.14.1** ist im `vendor/`-Verzeichnis gevendored und darf nicht aktualisiert werden (API-Inkompatibilität)
- Die App läuft auf **Python 3.9+** (getestet auf 3.9.2 auf Netcup Shared Hosting)
- BL2-Daten kommen immer von OpenligaDB (kostenlos, kein Key nötig)
- BL1 nutzt football-data.org Free Tier (10 Requests/Minute, 1 Minute Cache)

---

## Lizenz

Privates Projekt – alle Rechte vorbehalten.
