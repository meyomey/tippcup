# Tippcup – Technische Dokumentation

> Stand: April 2026 | Version 2.0 | Python/Flask · SQLite · Bootstrap 5

---

## Inhaltsverzeichnis

1. [Architektur-Überblick](#1-architektur-überblick)
2. [Ordnerstruktur](#2-ordnerstruktur)
3. [Modul-Beschreibung](#3-modul-beschreibung)
4. [Datenbankschema](#4-datenbankschema)
5. [API- & Funktions-Referenz](#5-api--funktions-referenz)
6. [Setup-Anleitung](#6-setup-anleitung)
7. [Deployment (Netcup/Plesk)](#7-deployment-netcupplesk)
8. [Konfiguration & Umgebungsvariablen](#8-konfiguration--umgebungsvariablen)

---

## 1. Architektur-Überblick

### Tech-Stack

| Schicht | Technologie |
|---------|-------------|
| Backend | Python 3.9+ · Flask 3.x |
| Datenbank | SQLite 3 (eine Datei) |
| Frontend | Bootstrap 5.3 · Vanilla JS |
| API-Daten | football-data.org (BL1) · OpenligaDB (BL1/BL2) |
| Push | Web Push API · VAPID · pywebpush |
| Telegram | Telegram Bot API (HTTP) |
| Hosting | Netcup Shared Hosting · Plesk · Phusion Passenger |
| PWA | Service Worker · Web App Manifest |

### Datenfluss

```
Browser ──► Passenger/WSGI ──► Flask (app.py)
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
               SQLite DB      openliga.py     telegram_bot.py
                                    │
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
           football-data.org                  OpenligaDB API
           (BL1, kostenpflichtig)             (BL1+BL2, kostenlos)
```

### Dual-API-Strategie

- **BL1**: football-data.org (wenn Key konfiguriert) mit 15-Minuten-TTL, Fallback auf OpenligaDB
- **BL2**: immer OpenligaDB mit 60-Minuten-TTL
- **Änderungserkennung**: football-data.org → Content-Hash, OpenligaDB → `getlastchangedate`-Timestamp
- **Cache**: In-Process-Dict (`_process_cache`) + DB-Tabelle `api_cache`

---

## 2. Ordnerstruktur

```
tippspiel/
├── app.py                    # Hauptanwendung (~4600 Zeilen)
├── openliga.py               # API-Client (football-data.org + OpenligaDB)
├── telegram_bot.py           # Telegram-Bot-Integration
├── passenger_wsgi.py         # Phusion-Passenger-Einstiegspunkt
├── requirements.txt          # Python-Abhängigkeiten
├── tippspiel.db              # SQLite-Datenbank (nicht im Repo!)
│
├── static/
│   ├── css/style.css         # Haupt-Stylesheet (inkl. Dark Mode, CSS-Tooltips)
│   ├── sw.js                 # Service Worker (PWA/Push)
│   ├── manifest.json         # PWA-Manifest
│   └── favicon.svg / icon-*.png/svg
│
├── templates/
│   ├── base.html             # Basis-Layout (Nav, Footer, Bootstrap, Dark Mode)
│   ├── _macros.html          # Jinja2-Makros (player_tooltip)
│   ├── dashboard.html        # Startseite / Live-Tabelle
│   ├── predict.html          # Tipp-Abgabe
│   ├── leaderboard.html      # Bestenliste (Karten + Tabelle)
│   ├── tips.html             # Alle Tipps Übersicht
│   ├── tips_user.html        # Einzelansicht Tipp
│   ├── verlauf.html          # Platzierungsverlauf (Chart + Tabelle)
│   ├── profile.html          # Benutzerprofil
│   ├── angsthasen.html       # Angsthasen-Analyse
│   ├── rueckblick.html       # Saisonrückblick
│   ├── highlights.html       # Spieltag-Highlights
│   ├── badges.html           # Abzeichen
│   ├── head2head.html        # Direktvergleich
│   ├── teamstatistik.html    # Team-Statistiken
│   ├── spieltage.html        # Live-Spieltage
│   ├── ewige_tabelle.html    # Ewige Tabelle (saisonübergreifend)
│   ├── medien.html           # Medien-Galerie
│   ├── login.html
│   ├── errors/               # 403, 404, 500
│   └── admin/
│       ├── base.html         # Admin-Layout (Sidebar)
│       ├── dashboard.html    # Admin-Startseite
│       ├── season.html       # Saison-Verwaltung
│       ├── teams.html        # Team-Import und -Verwaltung
│       ├── users.html        # Benutzerliste
│       ├── user_form.html    # Benutzer anlegen/bearbeiten
│       ├── api_config.html   # API-Key + Push-Status
│       ├── email.html        # SMTP + Rundbrief
│       ├── telegram.html     # Telegram-Bot-Konfiguration
│       ├── backup.html       # Backup & Restore
│       ├── security.html     # Login-Versuche / Sicherheit
│       ├── medien.html       # Medienverwaltung
│       ├── links.html        # Externe Links
│       └── manual_standings.html
│
└── docs/
    ├── TECHNISCHE_DOKUMENTATION.md  (diese Datei)
    └── BENUTZERHANDBUCH.md
```

---

## 3. Modul-Beschreibung

### 3.1 `app.py` – Hauptanwendung

Die monolithische Flask-Anwendung enthält alle Routen, Business-Logik und Datenbankzugriffe.

**Interne Abschnitte (durch `# ──` markiert):**

| Abschnitt | Inhalt |
|-----------|--------|
| Imports & Konstanten | Alle Standard-Imports, `MAX_SCORE`, `MAX_DEVIATION`, VAPID-Konfiguration, `BADGE_DEFS`, `LEAGUES` |
| Datenbankzugriff | `get_db()`, `init_db()`, `close_db()` |
| Dekoratoren | `@login_required`, `@admin_required` |
| Hilfsfunktionen | Caching, Zeitzone, Score-Berechnung, Push-Notifications |
| Öffentliche Routen | Dashboard, Login, Tipps, Bestenliste, Verlauf, Profil |
| Admin-Routen | Saison, Teams, Benutzer, Backup, E-Mail, Telegram |
| Push-Routen | VAPID-Key, Subscribe, Unsubscribe, Test |
| Telegram-Routen | Konfiguration, Webhook |
| Medien & Links | Upload, Galerie, externe Links |

### 3.2 `openliga.py` – API-Client

Kapselt alle externen API-Aufrufe. Hat keinen direkten Datenbankzugriff.

**Klassen/Abschnitte:**
- **football-data.org-Funktionen** (`fd_*`): Tabelle, Spieltag, aktuelle Spieltagnummer
- **OpenligaDB-Funktionen** (`ol_*`): Tabelle, Spieltage, Normalisierung
- **Einheitliche Schnittstelle** (`get_table`, `get_matchday`, `get_current_matchday_nr`): Wählt automatisch den besten Provider
- **Caching**: `_process_cache` (In-Process-Dict) für kurzfristige Daten

**Wichtige Konstanten:**
```python
INTERVAL_TABLE    = 3600   # OpenligaDB TTL: 60 Min.
INTERVAL_TABLE_FD = 900    # football-data.org TTL: 15 Min.
FD_LEAGUE = {'bl1': 2002}  # Nur BL1 im Free Tier
```

### 3.3 `telegram_bot.py` – Telegram-Integration

Kapselt die gesamte Telegram-Bot-Logik.

**Funktionsgruppen:**
- **Nachrichten senden**: `send_message()`, `test_connection()`
- **Bot-Info**: `get_bot_info()`, `set_webhook()`, `delete_webhook()`
- **Automatische Notifications**: `notify_standings_updated()`, `notify_deadline_reminder()`, `notify_matchday_highlight()`
- **Webhook-Handler**: `handle_bot_command()` – verarbeitet `/rangliste`, `/spieltag`, `/help`
- **Einstellungen**: `is_enabled()`, `is_matchday_complete()`, `get_settings()`, `save_settings()`

### 3.4 Spieltag-Vollständigkeitsprüfung (`is_matchday_complete`)

Bevor automatische Nachrichten gesendet werden, prüft `is_matchday_complete()` ob wirklich alle Spiele beider Ligen abgeschlossen sind (`is_finished = True`). Verhindert Nachrichten während laufender Spieltage.

---

## 4. Datenbankschema

### Kern-Tabellen

```sql
users
  id, username (UNIQUE), password_hash, display_name, full_name,
  favorite_club, email, mobile, is_admin, is_active, last_login, created_at

seasons
  id, year, name, is_active, season_started, tips_locked,
  deadline_bl1, deadline_bl2, last_updated, created_at

teams
  id, name, short_name, openliga_id, fd_id, season_id, league, logo_url
  UNIQUE(season_id, openliga_id, league)

predictions
  id, user_id, season_id, team_id, predicted_rank, created_at, updated_at
  UNIQUE(user_id, season_id, team_id)

standings
  id, season_id, team_id, current_rank, points, goals_for, goals_against,
  matches_played, wins, draws, losses, updated_at
  UNIQUE(season_id, team_id)

scores
  id, user_id, season_id, score, score_bl1, score_bl2,
  deviation_bl1, deviation_bl2, total_deviation, std_deviation,
  max_deviation, volltreffer, updated_at
  UNIQUE(user_id, season_id)
```

### Hilfs-Tabellen

```sql
api_cache          -- Caching von API-Antworten (TTL + Hash)
config             -- Telegram-Einstellungen (key/value)
app_config         -- Allgemeine App-Konfiguration (key/value)
smtp_config        -- SMTP-Zugangsdaten
ranking_snapshots  -- Historische Platzierungen je Spieltag
matchday_highlights -- Bester Tipper je Spieltag
user_badges        -- Abzeichen (UNIQUE: user_id, season_id, badge_type)
push_subscriptions -- Web-Push-Endpoints
login_attempts     -- Brute-Force-Schutz
media_folders      -- Medien-Ordner
media_files        -- Hochgeladene Bilder/Dateien
external_links     -- Externe Links-Seite
prev_standings     -- Vorjahrestabelle (für Angsthasen-Berechnung)
```

### Wichtige Indices

```sql
idx_predictions_season, idx_predictions_user, idx_predictions_team,
idx_standings_season, idx_standings_team, idx_users_active,
idx_scores_season, idx_teams_season, idx_highlights_season,
idx_poll_votes_poll, idx_reactions_msg, idx_snapshots_season
```

---

## 5. API- & Funktions-Referenz

### 5.1 Öffentliche HTTP-Endpunkte

| Route | Methode | Beschreibung |
|-------|---------|--------------|
| `/` | GET | Dashboard mit Live-Tabelle |
| `/login` | GET, POST | Login-Formular |
| `/logout` | GET | Abmelden |
| `/predict` | GET, POST | Tipp abgeben / bearbeiten |
| `/leaderboard` | GET | Bestenliste der aktuellen Saison |
| `/leaderboard/scores` | GET | JSON: Score-Daten für Charts |
| `/tips` | GET | Alle abgegebenen Tipps (nach Spieltag sortierbar) |
| `/tips/user/<uid>` | GET | Einzelansicht: Tipp eines Spielers |
| `/verlauf` | GET | Platzierungsverlauf (Chart + sortierbare Tabelle) |
| `/angsthasen` | GET | Angsthasen-Analyse (Risikobereitschaft) |
| `/vergleich/<uid1>/<uid2>` | GET | Head-to-Head Direktvergleich |
| `/rueckblick` | GET | Saisonrückblick (Statistiken, Highlights, Ewige Tabelle) |
| `/highlights` | GET | Spieltag-Highlights |
| `/spieltage` | GET | Aktuelle Spieltagsergebnisse |
| `/spieltage/<league>/<matchday>` | GET | Bestimmter Spieltag |
| `/spieltage/api/<league>/<year>/<matchday>` | GET | JSON: Spieltag-Daten |
| `/profile` | GET, POST | Benutzerprofil anzeigen / bearbeiten |
| `/badges` | GET | Abzeichen-Übersicht |
| `/teamstatistik` | GET | Team-Tipp-Statistiken |
| `/ewige-tabelle` | GET | Saisonübergreifende Rangliste |
| `/medien` | GET | Medien-Galerie |

### 5.2 Admin-Endpunkte (Authentifizierung erforderlich)

| Route | Beschreibung |
|-------|--------------|
| `/admin` | Admin-Dashboard |
| `/admin/season` | Saison verwalten (starten, sperren, Deadlines) |
| `/admin/teams` | Teams importieren, Tipps reparieren |
| `/admin/teams/import_both` POST | Beide Ligen von API laden |
| `/admin/teams/import` POST | Eine Liga laden |
| `/admin/teams/repair` POST | Prediction-Backup wiederherstellen |
| `/admin/users` | Benutzerliste |
| `/admin/users/add` | Benutzer anlegen |
| `/admin/users/<uid>/edit` | Benutzer bearbeiten |
| `/admin/api-config` | football-data.org Key + Push-Status |
| `/admin/telegram` | Telegram-Bot konfigurieren |
| `/admin/email` | SMTP + Rundbrief |
| `/admin/backup` | Datenbank-Download |
| `/admin/restore` POST | Datenbank-Restore |
| `/admin/security` | Fehlgeschlagene Login-Versuche |
| `/admin/diagnose` | Datenbank-Integritätscheck |
| `/admin/badges/assign` POST | Abzeichen neu berechnen |
| `/admin/medien` | Medienverwaltung |
| `/admin/links` | Externe Links verwalten |

### 5.3 Push-Endpunkte

| Route | Beschreibung |
|-------|--------------|
| `/push/vapid-public-key` | VAPID Public Key für Browser |
| `/push/subscribe` POST | Subscription registrieren |
| `/push/unsubscribe` POST | Subscription entfernen |
| `/push/renew` POST | Subscription erneuern |
| `/push/test` POST | Test-Push an aktuellen Nutzer |
| `/push/diagnose` | Test-Push an alle Geräte (Admin) |
| `/telegram/webhook/<token>` POST | Telegram-Updates empfangen |

### 5.4 Wichtige Python-Funktionen

#### Scoring (`app.py`)

```python
calculate_scores_for_season(season_id: int) -> None
```
Berechnet Scores für alle Tipper. Formel:
- **Score pro Liga** = `MAX_DEVIATION - Σ|tipp_rank - actual_rank|`
- **Std-Abweichung** (σ): Maß für Konsistenz (niedriger = besser)
- **Volltreffer**: Anzahl exakter Platzierungstreffer

Nach Berechnung: Ranking-Snapshot speichern, Spieltag-Highlight, Telegram-Notification, Badges, Push.

```python
calculate_angsthasen(season: dict) -> tuple[list, bool]
```
Berechnet Angsthasen-Score. Vergleicht aktuellen Tipp mit Vorjahrestabelle. Kleinerer Wert = mehr Angsthase.

#### Team-Import (`app.py`)

```python
_upsert_teams(db, table: list, season_id: int, league: str) -> tuple[int, int]
```
Importiert Teams prediction-sicher. 4-stufiges Matching:
1. TLA (3-Buchstaben-Kürzel: FCB, BVB, BMG)
2. fd_id / openliga_id
3. Bester Keyword-Score (mit Stopwords für häufige Wörter wie "Borussia")

Gibt `(updated, inserted)` zurück.

```python
_repair_predictions_from_backup(db, season_id: int) -> tuple[int, int]
```
Stellt Predictions nach Team-Löschen+Reimport wieder her. Nutzt `api_cache`-Backup.

#### Badges (`app.py`)

```python
calculate_and_assign_badges(season_id: int) -> None
```
Löscht alle alten Badges und vergibt neu:

| Badge | Bedingung |
|-------|-----------|
| 🏆 Saisonsieger | Höchster Score |
| 🎯 Volltreffer-König | Meiste exakte Treffer |
| 🔩 Eiserner Tipper | Niedrigste Std-Abweichung (σ) |
| 🎲 Wildcard | Höchste Einzelabweichung |
| 🐇 Größter Angsthase | Niedrigster Angsthasen-Wert |
| 🦁 Mutigster Tipper | Höchster Angsthasen-Wert |

#### Cache (`app.py`)

```python
_cache_get(key: str) -> dict | None
_cache_set(key: str, last_ts: str, data: str = '') -> None
_seconds_since_fetch(cache_row) -> float
```
DB-seitiges Caching mit Timestamp. Verhindert zu häufige API-Aufrufe.

#### OpenligaDB (`openliga.py`)

```python
get_table(league: str, year: int) -> list[dict]
```
Einheitliche Schnittstelle: wählt football-data.org (wenn Key) oder OpenligaDB.

Rückgabe-Keys pro Team: `TeamName, ShortName, TeamInfoId, TeamRank, Points, Goals, OpponentGoals, _source`

```python
get_matchday(league: str, year: int, matchday: int) -> list[dict]
```
Rückgabe-Keys pro Spiel: `match_id, datetime, is_finished, is_live, team1_name, team1_short, team1_icon, team2_name, team2_short, team2_icon, goals_ft_1, goals_ft_2, goals_ht_1, goals_ht_2, _source`

Wichtig: `datetime` bei football-data.org wird automatisch UTC→CET/CEST konvertiert.

```python
get_current_matchday_nr(league: str) -> int
is_matchday_complete(db, season: dict) -> bool  # telegram_bot.py
```

---

## 6. Setup-Anleitung

### 6.1 Voraussetzungen

- Python 3.9 oder neuer
- pip
- (Optional) Git

### 6.2 Lokale Installation

```bash
# 1. Repository klonen oder ZIP entpacken
cd tippspiel/

# 2. Virtuelle Umgebung erstellen (empfohlen)
python -m venv venv
source venv/bin/activate        # Linux/macOS
venv\Scripts\activate           # Windows

# 3. Abhängigkeiten installieren
pip install -r requirements.txt

# 4. Umgebungsvariablen setzen
export SECRET_KEY="dein-geheimer-schluessel"
export DB_PATH="tippspiel.db"

# Optional: Push-Notifications
# export VAPID_PRIVATE_KEY="..."
# export VAPID_PUBLIC_KEY="..."

# 5. App starten
python app.py
# → http://localhost:5000
```

### 6.3 Standard-Login

Nach dem ersten Start wird automatisch angelegt:
- **Benutzername**: `admin`
- **Passwort**: `admin123`

⚠️ Passwort nach dem ersten Login unter Profil ändern!

### 6.4 Abhängigkeiten

```
flask>=3.0.0       # Web-Framework
werkzeug>=3.0.0    # WSGI-Utilities (Passwort-Hashing, Security)
requests>=2.31.0   # HTTP-Client für API-Calls
pywebpush>=1.14.0  # Web-Push-Notifications (VAPID)
```

Alle Pakete sind Pure-Python oder haben minimal native Abhängigkeiten.

### 6.5 football-data.org API-Key (optional)

1. Kostenlos registrieren: https://www.football-data.org/client/register
2. Im Admin-Bereich unter **API-Einstellungen** eintragen
3. Wird in der DB gespeichert — kein Neustart nötig

---

## 7. Deployment (Netcup/Plesk)

### 7.1 `passenger_wsgi.py` konfigurieren

```python
import sys, os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

vendor_dir = os.path.join(BASE_DIR, 'vendor')
if os.path.isdir(vendor_dir):
    sys.path.insert(0, vendor_dir)

# Pflicht: Session-Schlüssel (sicher, zufällig, min. 32 Zeichen)
os.environ['SECRET_KEY'] = 'dein-geheimer-schluessel-hier'
os.environ['DB_PATH']    = os.path.join(BASE_DIR, 'tippspiel.db')

# Optional: VAPID-Keys für Push (wenn nicht in app.py als Fallback)
# os.environ['VAPID_PRIVATE_KEY'] = '...'
# os.environ['VAPID_PUBLIC_KEY']  = '...'
# os.environ['VAPID_EMAIL']       = 'mailto:admin@example.com'

from app import app, init_db
init_db()
application = app
```

### 7.2 Python-Version in Plesk

In Plesk unter **Python-Einstellungen** mindestens Python 3.9 wählen.

### 7.3 pywebpush im Vendor-Verzeichnis

Falls pywebpush nicht global installierbar:

```bash
pip install pywebpush --target vendor/
```

### 7.4 Passenger neu starten

Nach Dateiänderungen in Plesk: **Neu starten** oder `touch tmp/restart.txt`.

### 7.5 Telegram-Webhook (für Bot-Befehle)

HTTPS ist Pflicht. Im Admin unter **Telegram** → **Bot-Befehle (Webhook)** → **Aktivieren**.

---

## 8. Konfiguration & Umgebungsvariablen

### Umgebungsvariablen

| Variable | Pflicht | Beschreibung |
|----------|---------|--------------|
| `SECRET_KEY` | ✓ | Flask-Session-Schlüssel (min. 32 Zeichen, zufällig) |
| `DB_PATH` | ✓ | Absoluter Pfad zur SQLite-Datei |
| `VAPID_PRIVATE_KEY` | – | Web-Push (Fallback: Wert in app.py) |
| `VAPID_PUBLIC_KEY` | – | Web-Push (Fallback: Wert in app.py) |
| `VAPID_EMAIL` | – | Kontakt-E-Mail für VAPID |
| `VAPID_KEY_VERSION` | – | Key-Version (Standard: v2) |
| `FLASK_DEBUG` | – | `1` für Debug-Modus (nie in Produktion!) |

### DB-Konfiguration (Admin-Bereich)

| Schlüssel | Gespeichert in | Beschreibung |
|-----------|---------------|--------------|
| `football_data_api_key` | `app_config` | football-data.org API-Key |
| `telegram_token` | `config` | Bot-Token von @BotFather |
| `telegram_chat_id` | `config` | Negative Gruppen-ID |
| `telegram_group_link` | `config` | Einladungslink (für Navbar) |
| `tg_notify_rangliste` | `config` | Ranglisten-Notification (0/1) |
| `tg_notify_highlight` | `config` | Highlight-Notification (0/1) |
| `tg_notify_deadline` | `config` | Deadline-Erinnerung (0/1) |
| `tg_last_rangliste_md` | `config` | Letzter gesendeter Spieltag (Rangliste, Deduplizierung) |
| `tg_last_highlight_md` | `config` | Letzter gesendeter Spieltag (Highlight, Deduplizierung) |
| `tg_notification_log` | `api_cache` | JSON-Array der letzten 30 Versendungsversuche |

**Telegram-Benachrichtigungs-Ablauf:**

```
Seitenaufruf /  →  maybe_auto_update()
  → calculate_scores_for_season()
    → tg_last_*_md prüfen (Deduplizierung)
    → is_matchday_complete() prüfen (alle Spiele finished?)
    → notify_*() senden
    → tg_last_*_md aktualisieren
    → log_attempt() schreiben
```

Status-Codes im Log: `sent` | `skipped` | `blocked` | `error`

### SMTP (Admin → E-Mail)

In der Tabelle `smtp_config` gespeichert: Host, Port, Benutzername, Passwort, Absender-Name, Absender-E-Mail, TLS.

### Backup-Format

ZIP-Archiv mit folgender Struktur (je nach gewählten Checkboxen):

```
tippcup_backup_YYYYMMDD_HHMMSS_db_uploads.zip
  tippspiel.db          ← DB (via VACUUM INTO, konsistenter Snapshot)
  config/
    app.py
    passenger_wsgi.py
    telegram_bot.py
    openliga.py
    requirements.txt
  uploads/
    <alle Dateien aus static/uploads/>
```

Restore akzeptiert ZIP oder einzelne `.db`-Datei. DB wird vor Übernahme via `SELECT COUNT(*) FROM users` validiert.

---

## Scoring-Formel

```
MAX_DEVIATION = n²/2    (n = Teamanzahl, BL = 18 → 162)
MAX_SCORE     = 2 × MAX_DEVIATION = 324 (beide Ligen)

Score_Liga = MAX_DEVIATION - Σ|tipp_rank(i) - actual_rank(i)|
Gesamtscore = Score_BL1 + Score_BL2

σ (Std-Abweichung) = sqrt( Σ d² / m )   (m = Teamanzahl pro Liga)
```

Höherer Score = besser. Niedrigeres σ = konsistenter. Bei Punktgleichstand gewinnt der mit der niedrigeren Std-Abweichung, danach mehr Volltreffer.

**Gleichstand-Reihenfolge:** `score DESC, std_deviation ASC, volltreffer DESC`

**Wildcard-Markierung:** Der Tipper mit der größten Einzel-Abweichung je Liga (BL1/BL2) wird mit 🎲 markiert (`wildcard_bl1_id`, `wildcard_bl2_id`, berechnet via `predictions × standings`).

---

## Spielminuten-Berechnung (Live-Spieltage)

**football-data.org Free Tier** liefert kein `minute`-Feld. Berechnung server-seitig in `openliga.py`:

```python
elapsed = (now_local - kickoff_local).total_seconds() / 60
if elapsed > 60:
    elapsed -= 15   # ~15 Min Halbzeitpause
live_minute = max(1, min(90, int(elapsed)))
```

Zusätzlich clientseitige Aktualisierung alle 30 Sekunden via JS (`calcLiveMinutes()`). Bei `live_status == 'HALF_TIME'` oder `'PAUSED'` (aus FD.org) wird Text statt Minute angezeigt.

---

## Medien-System

### Upload-Typen

```python
ALLOWED_IMAGES = {'jpg','jpeg','png','gif','webp'}
ALLOWED_DOCS   = {'pdf','doc','docx','xls','xlsx','ppt','pptx','txt','zip'}
ALLOWED_VIDEOS = {'mp4','mov','avi','mkv','webm','m4v'}
ALLOWED_AUDIO  = {'mp3','ogg','wav','m4a','aac','flac','opus'}
MAX_FILE_MB    = 50     # Dokumente/Bilder
MAX_VIDEO_MB   = 200    # Videos/Audio
```

### ZIP-Extraktion

Beim Upload einer `.zip`-Datei wird `_extract_zip_images()` aufgerufen:
- Extrahiert nur `ALLOWED_IMAGES`-Dateitypen
- Sicherheits-Check: `os.path.basename()` gegen Path-Traversal
- Größenprüfung per extrahierter Datei (`member.file_size`)
- ZIP selbst wird nicht gespeichert

### Typ-Filter (Frontend)

Jedes Dateielement hat `data-filetype="image|video|audio|pdf|word|excel|other_doc"`.
Badge-Klick filtert DOM-Elemente clientseitig, leere Ordner werden ausgeblendet.
