#!/usr/bin/env python3
"""
Tippcup Bundesliga – 1. & 2. Bundesliga gleichzeitig
"""
from flask import (Flask, render_template, render_template_string, request, redirect, url_for,
                   session, flash, g, abort, jsonify, send_from_directory,
                   send_file, make_response)
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime, timedelta
import time as _time

# Zeitzone Europe/Berlin (automatische Sommer-/Winterzeit)
try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo('Europe/Berlin')
    def local_now():
        return datetime.now(_TZ).replace(tzinfo=None)
except ImportError:
    # Fallback für Python < 3.9: feste UTC+2 Korrektur
    try:
        import pytz
        _TZ = pytz.timezone('Europe/Berlin')
        def local_now():
            return datetime.now(pytz.utc).astimezone(_TZ).replace(tzinfo=None)
    except ImportError:
        # Letzter Fallback: UTC + 2h (Sommerzeit) – manuell anpassen falls nötig
        def local_now():
            return datetime.utcnow() + timedelta(hours=2)

def local_now_str():
    return local_now().strftime('%d.%m.%Y %H:%M')

def _utc_to_local_str(ts_str, fmt='%H:%M'):
    """Konvertiert einen UTC-Timestamp-String aus der DB in lokale Zeit."""
    if not ts_str:
        return ''
    try:
        # SQLite speichert als 'YYYY-MM-DD HH:MM:SS' oder 'YYYY-MM-DDTHH:MM:SS'
        ts_str = str(ts_str).replace('T', ' ')[:19]
        dt_utc = datetime.strptime(ts_str, '%Y-%m-%d %H:%M:%S')
        dt_local = dt_utc + timedelta(hours=2)  # UTC→CET/CEST (TODO: exakter mit zoneinfo)
        return dt_local.strftime(fmt)
    except Exception:
        return str(ts_str)[11:16]

import sqlite3, os
import io
import json
import math
import shutil
import tempfile
import smtplib
import random
import secrets
import hmac
from urllib.parse import urlparse, parse_qs
from email.mime.multipart import MIMEMultipart
from email.mime.text      import MIMEText
from email.utils          import formataddr
from datetime import timezone
import openliga as ol
import telegram_bot as tg

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__)

# Jinja-Filter: UTC-DB-Timestamps → lokale Anzeigezeit
app.jinja_env.filters['localtime'] = lambda ts, fmt='%H:%M': _utc_to_local_str(ts, fmt)
app.jinja_env.filters['localdt']   = lambda ts: _utc_to_local_str(ts, '%d.%m.%Y %H:%M')

_DEBUG_MODE = os.environ.get('FLASK_DEBUG', '0') == '1'

# SECRET_KEY MUSS über die Umgebung (passenger_wsgi.py) gesetzt werden – siehe
# passenger_wsgi.example.py. Kein fest im Code hinterlegter Fallback mehr:
# Ein im Repository sichtbarer Schlüssel würde bei fehlender Konfiguration
# Session-Fälschung ermöglichen. Fehlt SECRET_KEY, wird stattdessen bei
# jedem Prozessstart ein neuer Zufalls-Key erzeugt (Sessions/Logins bleiben
# dann nur bis zum nächsten Neustart gültig) – für den lokalen Testbetrieb
# ausreichend, für den Produktivbetrieb aber unbedingt SECRET_KEY setzen!
_secret_key = os.environ.get('SECRET_KEY')
if not _secret_key:
    _secret_key = secrets.token_hex(32)
    app.logger.warning(
        'SECRET_KEY ist NICHT gesetzt! Es wird ein temporärer Zufalls-Key verwendet '
        '(Sessions werden bei jedem Neustart ungültig). '
        'In passenger_wsgi.py setzen – siehe passenger_wsgi.example.py.'
    )
app.secret_key = _secret_key

# Session-Timeout: 8 Stunden Inaktivität → automatisch abmelden
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=8)
app.config['SESSION_COOKIE_HTTPONLY']    = True   # XSS-Schutz
app.config['SESSION_COOKIE_SECURE']      = not _DEBUG_MODE  # Cookie nur über HTTPS (außer im lokalen Debug-Betrieb)
app.config['SESSION_COOKIE_SAMESITE']    = 'Lax'  # CSRF-Grundschutz
app.config['MAX_CONTENT_LENGTH']         = 100 * 1024 * 1024  # max. 100 MB Upload

@app.after_request
def set_security_headers(resp):
    """Grundlegende Security-Header gegen Clickjacking, MIME-Sniffing etc."""
    resp.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
    resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
    resp.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    if not _DEBUG_MODE:
        resp.headers.setdefault('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
    return resp
DATABASE = os.environ.get('DB_PATH', 'tippspiel.db')

LEAGUES = {'bl1': '1. Bundesliga', 'bl2': '2. Bundesliga'}
MAX_DEVIATION = 162
MAX_SCORE     = MAX_DEVIATION * len(LEAGUES)  # 324

# ─────────────────────────────────────────────
SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    display_name TEXT,
    full_name TEXT,
    favorite_club TEXT,
    email TEXT,
    mobile TEXT,
    is_admin INTEGER DEFAULT 0,
    is_active INTEGER DEFAULT 1,
    last_login TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS seasons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year INTEGER NOT NULL,
    name TEXT NOT NULL,
    is_active INTEGER DEFAULT 1,
    season_started INTEGER DEFAULT 0,
    tips_locked INTEGER DEFAULT 0,
    locked_bl1  INTEGER DEFAULT 0,
    locked_bl2  INTEGER DEFAULT 0,
    deadline_bl1 TIMESTAMP,
    deadline_bl2 TIMESTAMP,
    last_updated TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS teams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    short_name TEXT,
    openliga_id INTEGER,
    fd_id INTEGER,
    season_id INTEGER NOT NULL,
    league TEXT NOT NULL DEFAULT 'bl1',
    logo_url TEXT,
    FOREIGN KEY (season_id) REFERENCES seasons(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    season_id INTEGER NOT NULL,
    team_id INTEGER NOT NULL,
    predicted_rank INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, season_id, team_id),
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (season_id) REFERENCES seasons(id),
    FOREIGN KEY (team_id) REFERENCES teams(id)
);
CREATE TABLE IF NOT EXISTS standings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id INTEGER NOT NULL,
    team_id INTEGER NOT NULL,
    current_rank INTEGER NOT NULL,
    points INTEGER DEFAULT 0,
    goals_for INTEGER DEFAULT 0,
    goals_against INTEGER DEFAULT 0,
    matches_played INTEGER DEFAULT 0,
    wins INTEGER DEFAULT 0,
    draws INTEGER DEFAULT 0,
    losses INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(season_id, team_id),
    FOREIGN KEY (season_id) REFERENCES seasons(id),
    FOREIGN KEY (team_id) REFERENCES teams(id)
);
CREATE TABLE IF NOT EXISTS scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    season_id INTEGER NOT NULL,
    score INTEGER DEFAULT 0,
    score_bl1 INTEGER DEFAULT 0,
    score_bl2 INTEGER DEFAULT 0,
    deviation_bl1 INTEGER DEFAULT 0,
    deviation_bl2 INTEGER DEFAULT 0,
    total_deviation INTEGER DEFAULT 0,
    std_deviation REAL DEFAULT 0,
    max_deviation INTEGER DEFAULT 0,
    volltreffer INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, season_id),
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (season_id) REFERENCES seasons(id)
);
CREATE TABLE IF NOT EXISTS app_config (
        key   TEXT PRIMARY KEY,
        value TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS ranking_snapshots (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id  INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    rank       INTEGER NOT NULL,
    score      INTEGER NOT NULL,
    matchday   INTEGER,
    label      TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (season_id) REFERENCES seasons(id),
    FOREIGN KEY (user_id)   REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS prev_standings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id INTEGER NOT NULL,
    openliga_id INTEGER NOT NULL,
    league TEXT NOT NULL,
    final_rank INTEGER NOT NULL,
    team_name TEXT NOT NULL,
    UNIQUE(season_id, openliga_id, league),
    FOREIGN KEY (season_id) REFERENCES seasons(id)
);
CREATE TABLE IF NOT EXISTS login_attempts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ip         TEXT NOT NULL,
    username   TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS media_folders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    description TEXT DEFAULT '',
    sort_order  INTEGER DEFAULT 0,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS media_files (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    folder_id   INTEGER,
    filename    TEXT NOT NULL,
    orig_name   TEXT NOT NULL,
    file_type   TEXT NOT NULL,
    file_size   INTEGER DEFAULT 0,
    title       TEXT DEFAULT '',
    description TEXT DEFAULT '',
    sort_order  INTEGER DEFAULT 0,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (folder_id) REFERENCES media_folders(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS external_links (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    url         TEXT NOT NULL,
    description TEXT DEFAULT '',
    icon        TEXT DEFAULT 'bi-link-45deg',
    sort_order  INTEGER DEFAULT 0,
    is_active   INTEGER DEFAULT 1,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS email_reminders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    season_id  INTEGER NOT NULL,
    sent_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, season_id),
    FOREIGN KEY (user_id)   REFERENCES users(id),
    FOREIGN KEY (season_id) REFERENCES seasons(id)
);
CREATE TABLE IF NOT EXISTS matchday_highlights (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id    INTEGER NOT NULL,
    matchday     INTEGER NOT NULL,
    user_id      INTEGER NOT NULL,
    rank_before  INTEGER,
    rank_after   INTEGER,
    score_before INTEGER,
    score_after  INTEGER,
    improvement  INTEGER DEFAULT 0,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(season_id, matchday, user_id),
    FOREIGN KEY (season_id) REFERENCES seasons(id),
    FOREIGN KEY (user_id)   REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS push_subscriptions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    endpoint   TEXT NOT NULL UNIQUE,
    p256dh     TEXT NOT NULL,
    auth       TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS user_badges (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    season_id  INTEGER NOT NULL,
    badge_type TEXT NOT NULL,
    UNIQUE(user_id, season_id, badge_type),
    FOREIGN KEY (user_id)   REFERENCES users(id),
    FOREIGN KEY (season_id) REFERENCES seasons(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS legacy_results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    season      TEXT NOT NULL,
    rang        INTEGER NOT NULL,
    tipper      TEXT NOT NULL,
    user_id     INTEGER,
    spieltag_1l INTEGER,
    spieltag_2l INTEGER,
    abw_1l      REAL, stdabw_1l REAL, maxabw_1l REAL, treffer_1l INTEGER,
    abw_2l      REAL, stdabw_2l REAL, maxabw_2l REAL, treffer_2l INTEGER,
    abw_ges     REAL, stdabw_ges REAL, maxabw_ges REAL, treffer_ges INTEGER,
    UNIQUE(season, tipper),
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS legacy_name_map (
    csv_name    TEXT PRIMARY KEY,
    user_id     INTEGER,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS nachfrist (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL UNIQUE,
    season_id    INTEGER NOT NULL,
    granted_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at   TIMESTAMP,
    granted_by   INTEGER,
    strafgeld    TEXT DEFAULT '',
    notiz        TEXT DEFAULT '',
    revoked_at   TIMESTAMP,
    FOREIGN KEY (user_id)    REFERENCES users(id),
    FOREIGN KEY (season_id)  REFERENCES seasons(id),
    FOREIGN KEY (granted_by) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS legacy_angsthasen (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    season      TEXT NOT NULL,
    rang        INTEGER NOT NULL,
    name        TEXT NOT NULL,
    user_id     INTEGER,
    abw_1l      INTEGER,
    abw_2l      INTEGER,
    abw_ges     INTEGER,
    abw_gew     REAL,
    n_1l        INTEGER,
    k_1l        INTEGER,
    n_2l        INTEGER,
    k_2l        INTEGER,
    UNIQUE(season, name),
    FOREIGN KEY (user_id) REFERENCES users(id)
);
"""

BADGE_DEFS = {
    'volltreffer_king': {'emoji': '🎯', 'label': 'Volltreffer-König',   'desc': 'Meiste exakte Treffer der Saison'},
    'mutiger_tipper':  {'emoji': '🦁', 'label': 'Mutigster Tipper',    'desc': 'Höchste Angsthasen-Wertung'},
    'angsthase':       {'emoji': '🐇', 'label': 'Größter Angsthase',   'desc': 'Niedrigste Angsthasen-Wertung'},
    'wildcard':        {'emoji': '🎲', 'label': 'Wildcard',             'desc': 'Höchste Einzelabweichung gesamt'},
    'wildcard_bl1':    {'emoji': '🎲', 'label': 'Wildcard BL1',         'desc': 'Höchste Einzelabweichung in der 1. Liga'},
    'wildcard_bl2':    {'emoji': '🎲', 'label': 'Wildcard BL2',         'desc': 'Höchste Einzelabweichung in der 2. Liga'},
    'champion':        {'emoji': '🏆', 'label': 'Saisonsieger',         'desc': 'Bester Gesamtscore der Saison'},
    'iron_tipper':     {'emoji': '🔩', 'label': 'Eiserner Tipper',      'desc': 'Niedrigste Standardabweichung'},
}

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db

@app.teardown_appcontext
def close_db(e):
    db = g.pop('db', None)
    if db: db.close()

def init_db():
    with app.app_context():
        db = get_db()
        db.executescript(SCHEMA)
        # Migration: Spalten ergänzen falls DB bereits existiert
        for col, typ in [('std_deviation', 'REAL DEFAULT 0'), ('max_deviation', 'INTEGER DEFAULT 0'), ('volltreffer', 'INTEGER DEFAULT 0')]:
            try:
                db.execute(f'ALTER TABLE scores ADD COLUMN {col} {typ}')
                db.commit()
            except: pass
        for col, typ in [('deadline_bl1', 'TIMESTAMP'), ('deadline_bl2', 'TIMESTAMP'),
                         ('locked_bl1', 'INTEGER DEFAULT 0'), ('locked_bl2', 'INTEGER DEFAULT 0')]:
            try:
                db.execute(f'ALTER TABLE seasons ADD COLUMN {col} {typ}')
                db.commit()
            except: pass
        try:
            db.execute('ALTER TABLE predictions ADD COLUMN is_auto INTEGER DEFAULT 0')
            db.commit()
        except: pass
        try:
            db.execute('ALTER TABLE nachfrist ADD COLUMN revoked_at TIMESTAMP')
            db.commit()
        except: pass
        try:
            db.execute('ALTER TABLE users ADD COLUMN email TEXT')
            db.commit()
        except: pass
        try:
            db.execute('ALTER TABLE users ADD COLUMN last_login TIMESTAMP')
            db.commit()
        except: pass
        try:
            db.execute('ALTER TABLE users ADD COLUMN mobile TEXT')
            db.commit()
        except: pass
        try:
            db.execute('ALTER TABLE users ADD COLUMN full_name TEXT')
            db.commit()
        except: pass
        try:
            db.execute('ALTER TABLE users ADD COLUMN favorite_club TEXT')
            db.commit()
        except: pass
        # Neue Tabellen anlegen
        db.execute("""CREATE TABLE IF NOT EXISTS login_attempts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            ip         TEXT NOT NULL,
            username   TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS ranking_snapshots (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            season_id  INTEGER NOT NULL,
            user_id    INTEGER NOT NULL,
            rank       INTEGER NOT NULL,
            score      INTEGER NOT NULL,
            matchday   INTEGER,
            label      TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (season_id) REFERENCES seasons(id),
            FOREIGN KEY (user_id)   REFERENCES users(id)
        )""")
        try:
            db.commit()
        except: pass
        # Migration: zusätzliche Tabellen (Push, Badges)
        for stmt in [
            'CREATE TABLE IF NOT EXISTS push_subscriptions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, endpoint TEXT NOT NULL UNIQUE, p256dh TEXT NOT NULL, auth TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)',
            'CREATE TABLE IF NOT EXISTS user_badges (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, season_id INTEGER NOT NULL, badge_type TEXT NOT NULL, UNIQUE(user_id, season_id, badge_type))',
        ]:
            try: db.execute(stmt); db.commit()
            except: pass
        # Alte Push-Subscriptions bei Key-Wechsel löschen
        try:
            kv = db.execute("SELECT data FROM api_cache WHERE key='vapid_key_version'").fetchone()
            if not kv or kv['data'] != VAPID_KEY_VERSION:
                db.execute('DELETE FROM push_subscriptions')
                db.execute("INSERT OR REPLACE INTO api_cache (key,last_ts,data) VALUES ('vapid_key_version',datetime('now'),?)",
                           (VAPID_KEY_VERSION,))
                db.commit()
        except Exception:
            pass
        try:
            db.execute('ALTER TABLE teams ADD COLUMN fd_id INTEGER')
            db.commit()
        except: pass
        # Datenbank-Indices für häufige WHERE-Abfragen
        indices = [
            'CREATE INDEX IF NOT EXISTS idx_predictions_season  ON predictions(season_id)',
            'CREATE INDEX IF NOT EXISTS idx_predictions_user    ON predictions(user_id)',
            'CREATE INDEX IF NOT EXISTS idx_predictions_team    ON predictions(team_id)',
            'CREATE INDEX IF NOT EXISTS idx_standings_season    ON standings(season_id)',
            'CREATE INDEX IF NOT EXISTS idx_standings_team      ON standings(team_id)',
            'CREATE INDEX IF NOT EXISTS idx_users_active        ON users(is_active)',
            'CREATE INDEX IF NOT EXISTS idx_scores_season       ON scores(season_id)',
            'CREATE INDEX IF NOT EXISTS idx_snapshots_season    ON ranking_snapshots(season_id)',
            'CREATE INDEX IF NOT EXISTS idx_teams_season        ON teams(season_id)',
            'CREATE INDEX IF NOT EXISTS idx_highlights_season   ON matchday_highlights(season_id)',
        ]
        for idx_sql in indices:
            try:
                db.execute(idx_sql)
            except Exception:
                pass
        db.commit()

        # Badge-Duplikate bereinigen (aus Zeit vor UNIQUE-Constraint)
        try:
            db.execute('''
                DELETE FROM user_badges WHERE id NOT IN (
                    SELECT MIN(id) FROM user_badges
                    GROUP BY user_id, season_id, badge_type
                )
            ''')
            db.commit()
        except: pass
        # Upload-Verzeichnis anlegen
        upload_dir = os.path.join(BASE_DIR, 'static', 'uploads')
        os.makedirs(upload_dir, exist_ok=True)
        # Archiv-Tabellen
        for stmt in [
            '''CREATE TABLE IF NOT EXISTS legacy_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                season TEXT NOT NULL, rang INTEGER NOT NULL, tipper TEXT NOT NULL,
                user_id INTEGER, spieltag_1l INTEGER, spieltag_2l INTEGER,
                abw_1l REAL, stdabw_1l REAL, maxabw_1l REAL, treffer_1l INTEGER,
                abw_2l REAL, stdabw_2l REAL, maxabw_2l REAL, treffer_2l INTEGER,
                abw_ges REAL, stdabw_ges REAL, maxabw_ges REAL, treffer_ges INTEGER,
                UNIQUE(season, tipper))''',
            '''CREATE TABLE IF NOT EXISTS legacy_name_map (
                csv_name TEXT PRIMARY KEY, user_id INTEGER)''',
            '''CREATE TABLE IF NOT EXISTS nachfrist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL UNIQUE, season_id INTEGER NOT NULL,
                granted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP, granted_by INTEGER,
                strafgeld TEXT DEFAULT '', notiz TEXT DEFAULT '')''',
            '''CREATE TABLE IF NOT EXISTS legacy_angsthasen (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                season TEXT NOT NULL, rang INTEGER NOT NULL, name TEXT NOT NULL,
                user_id INTEGER, abw_1l INTEGER, abw_2l INTEGER, abw_ges INTEGER,
                abw_gew REAL, n_1l INTEGER, k_1l INTEGER, n_2l INTEGER, k_2l INTEGER,
                UNIQUE(season, name))''',
        ]:
            try: db.execute(stmt); db.commit()
            except: pass
        db.execute("""CREATE TABLE IF NOT EXISTS config (
            key   TEXT PRIMARY KEY,
            value TEXT DEFAULT ''
        )""")

        db.execute("""CREATE TABLE IF NOT EXISTS api_cache (
            key        TEXT PRIMARY KEY,
            last_ts    TEXT DEFAULT '',
            last_fetch TIMESTAMP,
            data       TEXT DEFAULT ''
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS smtp_config (
            id           INTEGER PRIMARY KEY CHECK (id=1),
            host         TEXT DEFAULT '',
            port         INTEGER DEFAULT 587,
            username     TEXT DEFAULT '',
            password     TEXT DEFAULT '',
            sender_name  TEXT DEFAULT 'Tippcup Bundesliga',
            sender_email TEXT DEFAULT '',
            use_tls      INTEGER DEFAULT 1,
            updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        if not db.execute('SELECT id FROM smtp_config WHERE id=1').fetchone():
            db.execute('INSERT INTO smtp_config (id) VALUES (1)')
        db.commit()
        if not db.execute('SELECT id FROM users WHERE is_admin=1').fetchone():
            db.execute('INSERT OR IGNORE INTO users (username,password_hash,display_name,is_admin) VALUES (?,?,?,?)',
                ('admin', generate_password_hash('admin123'), 'Administrator', 1))
        if not db.execute('SELECT id FROM seasons WHERE is_active=1').fetchone():
            db.execute('INSERT INTO seasons (year,name,is_active) VALUES (?,?,?)',
                (2024, 'Bundesliga 2024/25', 1))
        db.commit()

def login_required(f):
    @wraps(f)
    def d(*a, **kw):
        if 'user_id' not in session:
            # Push-Endpunkte und AJAX bekommen JSON 401
            is_ajax  = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
            is_push  = request.path.startswith('/push/')
            is_json  = 'application/json' in (request.content_type or '')
            if is_ajax or is_push or is_json:
                return jsonify({'ok': False, 'error': 'Nicht eingeloggt'}), 401
            flash('Bitte zuerst einloggen.', 'warning')
            return redirect(url_for('login', next=request.path))
        return f(*a, **kw)
    return d

def admin_required(f):
    @wraps(f)
    def d(*a, **kw):
        if 'user_id' not in session: return redirect(url_for('login'))
        if not session.get('is_admin'):
            flash('Adminrechte erforderlich.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*a, **kw)
    return d


# ════════════════════════════════════════════════════════════
# HILFSFUNKTIONEN & SCORING
# ════════════════════════════════════════════════════════════


def get_setting(key, default=''):
    """
    Liest einen Wert aus der 'config'-Tabelle (Key-Value-Einstellungen,
    z.B. Telegram-Zugangsdaten, Backup-Einstellungen). Einzige Quelle für
    diese Tabelle – sowohl von Python-Code als auch von Templates
    (siehe db_config() unten) genutzt, um Dopplung zu vermeiden.
    """
    try:
        row = get_db().execute('SELECT value FROM config WHERE key=?', (key,)).fetchone()
        return row['value'] if row and row['value'] not in (None, '') else default
    except Exception as e:
        app.logger.debug(f"Ignorierter Fehler: {e}")
        return default

def set_setting(key, value):
    """Schreibt einen Wert in die 'config'-Tabelle."""
    get_db().execute('INSERT OR REPLACE INTO config (key,value) VALUES (?,?)', (key, value))
    get_db().commit()


@app.context_processor
def inject_db_config():
    """Stellt db_config() in allen Templates bereit (z.B. für Telegram-Link)."""
    return dict(db_config=get_setting)


# ════════════════════════════════════════════════════════════
# CSRF-SCHUTZ
# ════════════════════════════════════════════════════════════
# Eigene, schlanke Implementierung (kein zusätzliches pip-Paket nötig –
# relevant, da das Hosting nur FTP-Zugriff ohne SSH erlaubt).
# Ein Token pro Session, geprüft bei jedem state-changing Request
# (POST/PUT/PATCH/DELETE) gegen das Form-Feld 'csrf_token' oder den
# Header 'X-CSRFToken' (für AJAX/fetch – siehe base.html).

# Endpunkte, die absichtlich OHNE Session-Cookie aufgerufen werden und
# daher kein CSRF-Ziel sind (eigene Authentifizierung über URL-Token
# bzw. den alten Push-Endpoint):
CSRF_EXEMPT_ENDPOINTS = {'telegram_webhook', 'push_renew', 'cron_backup'}

def get_csrf_token():
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_hex(32)
    return session['csrf_token']

@app.context_processor
def inject_csrf_token():
    return dict(csrf_token=get_csrf_token)

@app.before_request
def csrf_protect():
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        if request.endpoint in CSRF_EXEMPT_ENDPOINTS:
            return
        token_session = session.get('csrf_token')
        token_sent    = request.form.get('csrf_token') or request.headers.get('X-CSRFToken')
        if not token_session or not token_sent or not hmac.compare_digest(token_session, token_sent):
            app.logger.warning(
                f'CSRF-Token ungültig/fehlend – Endpoint={request.endpoint}, IP={get_client_ip()}'
            )
            abort(403)


def get_config(key, default=''):
    """Liest einen Wert aus der app_config-Tabelle."""
    row = get_db().execute('SELECT value FROM app_config WHERE key=?', (key,)).fetchone()
    return row['value'] if row else default

def set_config(key, value):
    """Schreibt einen Wert in die app_config-Tabelle."""
    get_db().execute(
        """INSERT INTO app_config (key, value, updated_at)
           VALUES (?,?,CURRENT_TIMESTAMP)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP""",
        (key, value)
    )
    get_db().commit()

def get_active_season():
    return get_db().execute('SELECT * FROM seasons WHERE is_active=1 ORDER BY id DESC LIMIT 1').fetchone()

def get_season_teams(season_id, league=None):
    if league:
        return get_db().execute('SELECT * FROM teams WHERE season_id=? AND league=? ORDER BY name', (season_id, league)).fetchall()
    return get_db().execute('SELECT * FROM teams WHERE season_id=? ORDER BY league,name', (season_id,)).fetchall()

def get_standings(season_id, league=None):
    q = """SELECT s.current_rank,s.points,s.wins,s.draws,s.losses,
                  s.goals_for,s.goals_against,s.matches_played,
                  t.id as team_id,t.name,t.short_name,t.logo_url,t.league
           FROM standings s JOIN teams t ON s.team_id=t.id WHERE s.season_id=?"""
    p = [season_id]
    if league: q += ' AND t.league=?'; p.append(league)
    return get_db().execute(q + ' ORDER BY t.league,s.current_rank', p).fetchall()

def team_count_per_league(season_id):
    rows = get_db().execute('SELECT league,COUNT(*) as c FROM teams WHERE season_id=? GROUP BY league', (season_id,)).fetchall()
    return {r['league']: r['c'] for r in rows}


def rebuild_snapshots_from_history(season_id):
    """
    Rekonstruiert den kompletten Platzierungsverlauf.
    Lädt alle Saisonspiele einmalig von OpenligaDB und berechnet
    die Tabelle nach jedem Spieltag lokal.
    Gibt (ok_count, skip_count, error_list) zurück.
    """
    import time
    db = get_db()
    season = db.execute('SELECT * FROM seasons WHERE id=?', (season_id,)).fetchone()
    if not season:
        return 0, 0, ['Saison nicht gefunden']
    year = season['year']

    existing = {r['matchday'] for r in db.execute(
        'SELECT DISTINCT matchday FROM ranking_snapshots WHERE season_id=? AND matchday IS NOT NULL',
        (season_id,)
    ).fetchall()}

    users = db.execute(
        """SELECT DISTINCT u.id, u.display_name, u.username
           FROM predictions p JOIN users u ON p.user_id=u.id
           WHERE p.season_id=? AND u.is_active=1""",
        (season_id,)
    ).fetchall()
    if not users:
        return 0, 0, ['Keine Tipps gefunden']

    preds_raw = db.execute(
        'SELECT user_id, team_id, predicted_rank FROM predictions WHERE season_id=?',
        (season_id,)
    ).fetchall()
    preds_by_user = {}
    for p in preds_raw:
        preds_by_user.setdefault(p['user_id'], {})[p['team_id']] = p['predicted_rank']

    teams_by_olid = {}
    teams_by_name = {}
    for t in db.execute(
        'SELECT id, openliga_id, name, short_name, league FROM teams WHERE season_id=?',
        (season_id,)
    ).fetchall():
        if t['openliga_id']:
            teams_by_olid[int(t['openliga_id'])] = {'team_id': t['id'], 'league': t['league']}
        teams_by_name[t['name'].lower()] = {'team_id': t['id'], 'league': t['league']}
        if t['short_name']:
            teams_by_name[t['short_name'].lower()] = {'team_id': t['id'], 'league': t['league']}

    n_teams = {r['league']: r['c'] for r in db.execute(
        'SELECT league, COUNT(*) as c FROM teams WHERE season_id=? GROUP BY league',
        (season_id,)
    ).fetchall()}
    a_max = {lg: (n ** 2) // 2 for lg, n in n_teams.items()}

    try:
        current_md = ol.get_current_matchday_nr('bl1') or 999
    except Exception:
        current_md = 999

    errors = []
    all_matches = {}
    for league in ('bl1', 'bl2'):
        try:
            all_matches[league] = ol.ol_get_all_season_matches(league, year)
            time.sleep(0.5)
        except Exception as e:
            errors.append(f'{league}: {e}')
            all_matches[league] = []

    ok_count = 0
    skip_count = 0

    for matchday in range(1, current_md):
        if matchday in existing:
            skip_count += 1
            continue

        standings = {}
        for league in ('bl1', 'bl2'):
            table = ol.compute_table_after_matchday(all_matches.get(league, []), matchday)
            for ol_tid, entry in table.items():
                if ol_tid == '_ranked_ids':
                    continue
                rank = entry.get('_rank')
                name = entry.get('name', '')
                info = teams_by_olid.get(int(ol_tid) if ol_tid else 0) or                        teams_by_name.get(name.lower())
                if info and rank:
                    standings[info['team_id']] = {'rank': rank, 'league': info['league']}

        if not standings:
            continue

        user_scores = []
        for u in users:
            uid   = u['id']
            upred = preds_by_user.get(uid, {})
            dev   = {'bl1': 0, 'bl2': 0}
            for team_id, pred_rank in upred.items():
                if team_id not in standings:
                    continue
                d  = abs(pred_rank - standings[team_id]['rank'])
                lg = standings[team_id]['league']
                dev[lg] += d
            s1 = a_max.get('bl1', 162) - dev['bl1']
            s2 = a_max.get('bl2', 162) - dev['bl2']
            user_scores.append({'uid': uid, 'score': s1 + s2})

        user_scores.sort(key=lambda x: -x['score'])
        from datetime import datetime as _dt
        ts_str = _dt.now().strftime('%Y-%m-%d %H:%M:%S') + f'.{matchday:03d}'

        for rank, us in enumerate(user_scores, 1):
            db.execute(
                """INSERT INTO ranking_snapshots
                   (season_id, user_id, rank, score, matchday, label, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (season_id, us['uid'], rank, us['score'],
                 matchday, f'Spieltag {matchday}', ts_str)
            )
        db.commit()
        ok_count += 1

    return ok_count, skip_count, errors


def autofill_missing_predictions(season_id):
    """
    Erstellt automatisch eine ZUFÄLLIGE Tabelle für Nutzer, die für eine
    bereits gesperrte Liga keinen (oder nur einen unvollständigen) Tipp
    abgegeben haben – und die auch KEINE aktive Nachfrist mehr haben.

    Ablauf pro Liga (bl1/bl2):
      1. Liga noch nicht gesperrt (Deadline nicht erreicht)  → nichts tun,
         der User kann noch normal tippen.
      2. Liga gesperrt, User hat aktive Nachfrist             → nichts tun,
         er bekommt seine Nachfrist-Chance.
      3. Liga gesperrt, KEINE aktive Nachfrist, Tipp fehlt
         (ganz oder teilweise)                                → fehlende
         Plätze zufällig auffüllen und als is_auto=1 markieren.

    Wird bei jeder Score-Neuberechnung aufgerufen (siehe
    calculate_scores_for_season), läuft also automatisch mit jedem
    Tabellen-Update bzw. jeder manuellen Neuberechnung mit.
    """
    db = get_db()
    season = db.execute('SELECT * FROM seasons WHERE id=?', (season_id,)).fetchone()
    if not season:
        return
    season = dict(season)

    active_users = db.execute('SELECT id FROM users WHERE is_active=1').fetchall()
    changed = False

    for lg in ('bl1', 'bl2'):
        if not season.get(f'locked_{lg}'):
            continue  # Deadline dieser Liga noch nicht erreicht

        teams = get_season_teams(season_id, lg)
        if not teams:
            continue
        all_ranks = set(range(1, len(teams) + 1))

        for u in active_users:
            uid = u['id']
            if has_nachfrist(uid, season_id):
                continue  # bekommt noch seine Chance über die Nachfrist

            existing = {p['team_id']: p['predicted_rank'] for p in db.execute(
                'SELECT team_id, predicted_rank FROM predictions WHERE user_id=? AND season_id=? '
                'AND team_id IN (%s)' % ','.join('?' * len(teams)),
                [uid, season_id] + [t['id'] for t in teams]
            ).fetchall()}

            if len(existing) == len(teams):
                continue  # bereits vollständig getippt

            missing_teams = [t for t in teams if t['id'] not in existing]
            missing_ranks = list(all_ranks - set(existing.values()))
            random.shuffle(missing_ranks)

            for team, rank in zip(missing_teams, missing_ranks):
                db.execute(
                    """INSERT INTO predictions (user_id,season_id,team_id,predicted_rank,is_auto,updated_at)
                       VALUES (?,?,?,?,1,CURRENT_TIMESTAMP)
                       ON CONFLICT(user_id,season_id,team_id) DO UPDATE SET
                       predicted_rank=excluded.predicted_rank, is_auto=1, updated_at=CURRENT_TIMESTAMP""",
                    (uid, season_id, team['id'], rank)
                )
            changed = True
            app.logger.info(
                f'autofill_missing_predictions: Zufallstipp für user_id={uid}, '
                f'Liga={lg}, Saison={season_id} ({len(missing_teams)} Plätze ergänzt)'
            )

    if changed:
        db.commit()


def calculate_scores_for_season(season_id):
    """
    Berechnet Punkte und Kennwerte nach dem mathematischen Dokument:

    Abweichung pro Liga:  a_l = Σ|r_li - t_li|
    Standardabw. pro Liga: s_l = sqrt( Σ(r_li - t_li)² / m_l )  ← quadratisches Mittel
    Gesamt: a_ges = Σ a_l  (bei unterschiedl. Ligagröße: quadratisch gewichtet)
            s_ges = Σ s_l
    Score pro Liga: a_max_l - a_l  mit a_max_l = n_l² / 2
    """
    autofill_missing_predictions(season_id)

    db = get_db()

    # Aktuelle Tabelle: team_id → {current_rank, league}
    st = {r['team_id']: r for r in db.execute(
        'SELECT st.team_id,st.current_rank,t.league FROM standings st JOIN teams t ON st.team_id=t.id WHERE st.season_id=?',
        (season_id,)
    ).fetchall()}

    # Anzahl Teams pro Liga → a_max_l = n_l² / 2
    n_teams = {}
    for r in st.values():
        n_teams[r['league']] = n_teams.get(r['league'], 0) + 1
    a_max = {lg: (n ** 2) // 2 for lg, n in n_teams.items()}

    for u in db.execute('SELECT DISTINCT user_id FROM predictions WHERE season_id=?', (season_id,)).fetchall():
        uid   = u['user_id']
        preds = db.execute(
            'SELECT team_id,predicted_rank FROM predictions WHERE user_id=? AND season_id=?',
            (uid, season_id)
        ).fetchall()

        dev        = {'bl1': 0, 'bl2': 0}   # Σ|r_i - t_i|
        sq_dev     = {'bl1': 0.0, 'bl2': 0.0}  # Σ(r_i - t_i)²
        count      = {'bl1': 0, 'bl2': 0}   # m_l
        max_dev    = 0
        volltreffer = 0

        for p in preds:
            if p['team_id'] not in st:
                continue
            lg   = st[p['team_id']]['league']
            d    = abs(p['predicted_rank'] - st[p['team_id']]['current_rank'])
            dev[lg]    += d
            sq_dev[lg] += d * d
            count[lg]  += 1
            if d > max_dev: max_dev = d
            if d == 0: volltreffer += 1

        # s_l = sqrt( Σ d² / m_l ) pro Liga — dann summieren
        s_ges = 0.0
        for lg in ('bl1', 'bl2'):
            if count[lg] > 0:
                s_ges += math.sqrt(sq_dev[lg] / count[lg])
        std_dev = round(s_ges, 4)

        # Score pro Liga: a_max_l - a_l (quadratisch gewichtet bei versch. Ligagröße)
        s1 = a_max.get('bl1', MAX_DEVIATION) - dev['bl1']
        s2 = a_max.get('bl2', MAX_DEVIATION) - dev['bl2']

        # a_ges: bei unterschiedlicher Ligagröße quadratisch gewichten
        n1 = n_teams.get('bl1', 18)
        n2 = n_teams.get('bl2', 18)
        if n1 == n2:
            total_dev = dev['bl1'] + dev['bl2']
        else:
            # Normalisierung auf gemeinsame Basis (größere Liga)
            n_ref = max(n1, n2)
            total_dev = (dev['bl1'] * (n_ref / n1) ** 2 +
                         dev['bl2'] * (n_ref / n2) ** 2)
            total_dev = round(total_dev, 2)

        db.execute(
            """INSERT INTO scores (user_id,season_id,score,score_bl1,score_bl2,
               deviation_bl1,deviation_bl2,total_deviation,std_deviation,max_deviation,
               volltreffer,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(user_id,season_id) DO UPDATE SET
               score=excluded.score, score_bl1=excluded.score_bl1,
               score_bl2=excluded.score_bl2,
               deviation_bl1=excluded.deviation_bl1,
               deviation_bl2=excluded.deviation_bl2,
               total_deviation=excluded.total_deviation,
               std_deviation=excluded.std_deviation,
               max_deviation=excluded.max_deviation,
               volltreffer=excluded.volltreffer,
               updated_at=CURRENT_TIMESTAMP""",
            (uid, season_id, s1+s2, s1, s2,
             dev['bl1'], dev['bl2'], total_dev,
             std_dev, max_dev, volltreffer)
        )

    db.commit()
    # Rangfolge speichern
    _save_ranking_snapshot(season_id)
    # Spieltag-Highlight + Telegram-Notification
    try:
        season_tmp = get_db().execute('SELECT * FROM seasons WHERE id=?', (season_id,)).fetchone()
        year = season_tmp['year'] if season_tmp else datetime.now().year
        matchday = ol.get_last_finished_matchday('bl1', year)
        if matchday:
            save_matchday_highlight(season_id, matchday)
            # Telegram: Spieltag-Highlight
            db_tg = get_db()
            if tg.is_enabled(db_tg, 'notify_highlight'):
                season_row = db_tg.execute('SELECT * FROM seasons WHERE id=?', (season_id,)).fetchone()
                # Deduplizierung: bereits für diesen Spieltag gesendet?
                _last_hl = db_tg.execute(
                    "SELECT value FROM config WHERE key='tg_last_highlight_md'"
                ).fetchone()
                _last_hl_md = int(_last_hl['value']) if _last_hl and _last_hl['value'].isdigit() else 0
                if _last_hl_md >= matchday:
                    tg.log_attempt(db_tg, 'highlight', matchday, 'skipped',
                                   f'Bereits für Spieltag {matchday} gesendet')
                elif not season_row:
                    tg.log_attempt(db_tg, 'highlight', matchday, 'blocked', 'Saison nicht gefunden')
                elif not tg.is_matchday_complete(db_tg, season_row):
                    tg.log_attempt(db_tg, 'highlight', matchday, 'blocked',
                                   'Spieltag noch nicht vollständig (is_finished fehlt)')
                else:
                    highlight = db_tg.execute(
                        '''SELECT h.*, u.display_name, u.username
                           FROM matchday_highlights h JOIN users u ON h.user_id=u.id
                           WHERE h.season_id=? AND h.matchday=?
                           ORDER BY h.id DESC LIMIT 1''', (season_id, matchday)
                    ).fetchone()
                    if highlight:
                        name = highlight['display_name'] or highlight['username']
                        ok, err = tg.notify_matchday_highlight(db_tg, season_row['name'], matchday, name, '')
                        if ok:
                            db_tg.execute(
                                "INSERT OR REPLACE INTO config (key,value) VALUES ('tg_last_highlight_md',?)",
                                (str(matchday),)
                            )
                            db_tg.commit()
                            tg.log_attempt(db_tg, 'highlight', matchday, 'sent',
                                           f'Spieltagssieger: {name}')
                        else:
                            tg.log_attempt(db_tg, 'highlight', matchday, 'error', err)
                    else:
                        tg.log_attempt(db_tg, 'highlight', matchday, 'blocked',
                                       'Kein Highlight-Eintrag für diesen Spieltag')
            else:
                tg.log_attempt(get_db(), 'highlight', matchday, 'skipped', 'Benachrichtigung deaktiviert')
    except Exception as e:
        app.logger.debug(f"Ignorierter Fehler: {e}")
    # Telegram: Rangliste nach vollständigem Spieltag
    try:
        db_tg = get_db()
        if tg.is_enabled(db_tg, 'notify_rangliste'):
            season_row = db_tg.execute('SELECT * FROM seasons WHERE id=?', (season_id,)).fetchone()
            year2 = season_row['year'] if season_row else datetime.now().year
            matchday2 = ol.get_last_finished_matchday('bl1', year2)
            if not matchday2:
                matchday2 = 0
            # Deduplizierung
            _last_rl = db_tg.execute(
                "SELECT value FROM config WHERE key='tg_last_rangliste_md'"
            ).fetchone()
            _last_rl_md = int(_last_rl['value']) if _last_rl and _last_rl['value'].isdigit() else 0
            if _last_rl_md >= matchday2 and matchday2 > 0:
                tg.log_attempt(db_tg, 'rangliste', matchday2, 'skipped',
                               f'Bereits für Spieltag {matchday2} gesendet')
            elif not season_row:
                tg.log_attempt(db_tg, 'rangliste', matchday2, 'blocked', 'Saison nicht gefunden')
            elif not tg.is_matchday_complete(db_tg, season_row):
                tg.log_attempt(db_tg, 'rangliste', matchday2, 'blocked',
                               'Spieltag noch nicht vollständig (is_finished fehlt)')
            else:
                top3 = db_tg.execute(
                    '''SELECT u.display_name, u.username, sc.score,
                              sc.std_deviation, sc.volltreffer
                       FROM scores sc JOIN users u ON sc.user_id=u.id
                       WHERE sc.season_id=? AND u.is_active=1
                       ORDER BY sc.score DESC, sc.std_deviation ASC, sc.volltreffer DESC, sc.user_id ASC LIMIT 3''', (season_id,)
                ).fetchall()
                if top3:
                    ok, err = tg.notify_standings_updated(db_tg, season_row['name'], [dict(r) for r in top3])
                    if ok:
                        db_tg.execute(
                            "INSERT OR REPLACE INTO config (key,value) VALUES ('tg_last_rangliste_md',?)",
                            (str(matchday2),)
                        )
                        db_tg.commit()
                        tg.log_attempt(db_tg, 'rangliste', matchday2, 'sent',
                                       f'Top3: {", ".join(r["display_name"] or r["username"] for r in top3)}')
                    else:
                        tg.log_attempt(db_tg, 'rangliste', matchday2, 'error', err)
                else:
                    tg.log_attempt(db_tg, 'rangliste', matchday2, 'blocked', 'Keine Scores vorhanden')
        else:
            tg.log_attempt(get_db(), 'rangliste', 0, 'skipped', 'Benachrichtigung deaktiviert')
    except Exception as e:
        app.logger.debug(f"Ignorierter Fehler: {e}")
    # Badges neu vergeben
    try:
        calculate_and_assign_badges(season_id)
    except Exception as e:
        app.logger.debug(f"Ignorierter Fehler: {e}")
    # Push: Rangliste aktualisiert
    try:
        _push_leaderboard_update(season_id)
    except Exception as e:
        app.logger.debug(f"Ignorierter Fehler: {e}")

def _push_leaderboard_update(season_id):
    """Benachrichtigt Spieler wenn sie überholt wurden."""
    db = get_db()
    current = {r['user_id']: (i+1, r['score']) for i,r in enumerate(db.execute(
        '''SELECT sc.user_id, sc.score FROM scores sc JOIN users u ON sc.user_id=u.id
           WHERE sc.season_id=? AND u.is_active=1
           ORDER BY sc.score DESC, sc.std_deviation ASC, sc.volltreffer DESC, sc.user_id ASC''',
        (season_id,)
    ).fetchall())}
    prev = {r['user_id']: r['rank'] for r in db.execute(
        '''SELECT user_id, rank FROM ranking_snapshots
           WHERE season_id=? AND id IN (
               SELECT MAX(id) FROM ranking_snapshots
               WHERE season_id=? GROUP BY user_id)''',
        (season_id, season_id)
    ).fetchall()}
    for uid, (new_rank, score) in current.items():
        old_rank = prev.get(uid)
        if old_rank and new_rank < old_rank:
            send_push_notification(uid, 'Tippcup 🏆',
                f'Du bist auf Platz {new_rank} aufgestiegen! 🎉', '/leaderboard')

def _save_ranking_snapshot(season_id):
    """Speichert den Tabellenstand als Snapshot — nur wenn Spieltag abgeschlossen."""
    db = get_db()
    season = db.execute('SELECT * FROM seasons WHERE id=?', (season_id,)).fetchone()
    matchday = None
    label    = local_now().strftime('%d.%m.')
    if season:
        try:
            matchday = ol.get_last_finished_matchday('bl1', season['year'])
            if matchday:
                label = f'Spieltag {matchday}'
        except Exception as e:
            app.logger.debug(f"Ignorierter Fehler: {e}")

    all_scores = db.execute(
        """SELECT sc.user_id, sc.score FROM scores sc
           JOIN users u ON sc.user_id=u.id
           WHERE sc.season_id=? AND u.is_active=1
           ORDER BY sc.score DESC, sc.std_deviation ASC, sc.volltreffer DESC, sc.user_id ASC""",
        (season_id,)
    ).fetchall()
    if not all_scores:
        return

    # Nicht mehr als 1 Snapshot pro Spieltag (letzter überschreibt vorherigen)
    if matchday:
        # Bestehende Snapshots dieses Spieltags löschen → frischer Stand
        db.execute('DELETE FROM ranking_snapshots WHERE season_id=? AND matchday=?',
                   (season_id, matchday))
    else:
        # Kein Spieltag → max 1 Snapshot pro Stunde
        last = db.execute(
            'SELECT created_at FROM ranking_snapshots WHERE season_id=? ORDER BY id DESC LIMIT 1',
            (season_id,)
        ).fetchone()
        if last:
            try:
                diff = (local_now() - datetime.fromisoformat(str(last['created_at']))).total_seconds()
                if diff < 3600:
                    return
            except Exception as e:
                app.logger.debug(f"Ignorierter Fehler: {e}")

    for rank, s in enumerate(all_scores, 1):
        db.execute(
            """INSERT INTO ranking_snapshots (season_id,user_id,rank,score,matchday,label)
               VALUES (?,?,?,?,?,?)""",
            (season_id, s['user_id'], rank, s['score'], matchday, label)
        )
    db.commit()

def _cache_get(key):
    """API-Cache-Eintrag aus DB lesen."""
    row = get_db().execute('SELECT * FROM api_cache WHERE key=?', (key,)).fetchone()
    return row

def _cache_set(key, last_ts, data=''):
    """API-Cache-Eintrag speichern."""
    get_db().execute(
        """INSERT INTO api_cache (key, last_ts, last_fetch, data)
           VALUES (?,?,CURRENT_TIMESTAMP,?)
           ON CONFLICT(key) DO UPDATE SET
           last_ts=excluded.last_ts, last_fetch=CURRENT_TIMESTAMP, data=excluded.data""",
        (key, last_ts, data)
    )
    get_db().commit()

def _seconds_since_fetch(cache_row) -> float:
    """Sekunden seit letztem API-Abruf."""
    if not cache_row or not cache_row['last_fetch']:
        return float('inf')
    try:
        last = datetime.fromisoformat(str(cache_row['last_fetch']))
        return (local_now() - last).total_seconds()
    except:
        return float('inf')

def update_standings_from_api(season, force=False):
    """
    Aktualisiert die Tabelle intelligent:
    - OpenligaDB:        getlastchangedate als leichte Vorab-Prüfung
    - football-data.org: Content-Hash-Vergleich (kein change-endpoint verfügbar)
    - Beide:             TTL-basiertes Caching in api_cache (DB)
    """
    import requests as _req
    db = get_db(); errors = []; success = []

    for league in ('bl1', 'bl2'):
        cache_key = f'table_{league}_{season["year"]}'
        cache     = _cache_get(cache_key)
        secs      = _seconds_since_fetch(cache)

        # TTL nicht abgelaufen und kein force → überspringen
        # Bei football-data.org: 15 Min. TTL (Daten kommen schneller)
        # Bei OpenligaDB:        60 Min. TTL
        fd_key    = os.environ.get('FOOTBALL_DATA_API_KEY', '')
        ttl       = ol.INTERVAL_TABLE_FD if fd_key else ol.INTERVAL_TABLE
        if not force and secs < ttl:
            success.append(league)
            continue

        known_ts   = cache['last_ts'] if cache else ''
        fd_key     = os.environ.get('FOOTBALL_DATA_API_KEY', '')

        if fd_key:
            # ── football-data.org: Hash-basiert ──────────────────────
            # Kein getlastchangedate → direkt laden und Hash vergleichen
            try:
                table, new_hash = ol.get_table_with_hash(league, season['year'])
            except _req.exceptions.ConnectTimeout:
                errors.append(f'{league}: Timeout'); continue
            except Exception as e:
                errors.append(f'{league}: {e}'); continue

            if new_hash == known_ts and not force:
                # Inhalt unverändert → nur TTL erneuern, kein DB-Update nötig
                _cache_set(cache_key, known_ts)
                app.logger.info(f'update_standings {league}: Hash unverändert ({known_ts}) → kein Update')
                success.append(league)
                continue
            new_ts = new_hash

        else:
            # ── OpenligaDB: Timestamp-basiert ────────────────────────
            try:
                # Letzten abgeschlossenen Spieltag prüfen, nicht den aktuellen
                # (get_current_matchday_nr gibt nach Spieltagsende bereits den nächsten zurück)
                matchday = ol.get_last_finished_matchday(league, season['year'])
                changed, new_ts = ol.has_changed_since(league, season['year'], matchday, known_ts)
                if not changed and not force:
                    _cache_set(cache_key, new_ts)
                    success.append(league)
                    continue
            except Exception:
                new_ts = ''  # Bei Fehler trotzdem laden

            try:
                table, _ = ol.ol_get_table(league, season['year'])
            except _req.exceptions.ConnectTimeout:
                errors.append(f'{league}: Timeout'); continue
            except _req.exceptions.ConnectionError:
                errors.append(f'{league}: Verbindungsfehler'); continue
            except Exception as e:
                errors.append(f'{league}: {e}'); continue

        # ── Tabelle in DB schreiben ───────────────────────────────
        stopwords = {'fc', 'sc', 'sv', 'vfl', 'vfb', '04', '05', '1.', '1899', 'rb', 'tsg', 'bsc'}
        for rank, td in enumerate(table, 1):
            oid    = td.get('TeamInfoId')
            source = td.get('_source', '')
            tla    = (td.get('TLA') or td.get('ShortName', ''))[:3].upper()
            team   = None

            # 1. ID-Matching
            if source == 'football-data.org' and oid:
                team = db.execute('SELECT id FROM teams WHERE fd_id=? AND season_id=?',
                                  (oid, season['id'])).fetchone()
            elif oid:
                team = db.execute('SELECT id FROM teams WHERE openliga_id=? AND season_id=?',
                                  (oid, season['id'])).fetchone()

            # 2. TLA (FCB, BVB, B04…) — 100% API-übergreifend identisch
            if not team and tla:
                team = db.execute(
                    'SELECT id FROM teams WHERE short_name=? AND season_id=? AND league=?',
                    (tla, season['id'], league)
                ).fetchone()

            # 3. Exakter Name
            if not team:
                team = db.execute(
                    'SELECT id FROM teams WHERE name=? AND season_id=? AND league=?',
                    (td['TeamName'], season['id'], league)
                ).fetchone()

            # 4. Schlüsselwort-Matching
            if not team:
                td_words = set(td['TeamName'].lower().split()) - stopwords
                if td_words:
                    for row in db.execute(
                        'SELECT id, name FROM teams WHERE season_id=? AND league=?',
                        (season['id'], league)
                    ).fetchall():
                        if td_words & (set(row['name'].lower().split()) - stopwords):
                            team = row
                            break

            # fd_id hinterlegen für künftige ID-Matches
            if team and source == 'football-data.org' and oid:
                db.execute('UPDATE teams SET fd_id=? WHERE id=? AND (fd_id IS NULL OR fd_id=0)',
                           (oid, team['id']))

            if team:
                db.execute(
                    """INSERT INTO standings
                       (season_id,team_id,current_rank,points,goals_for,goals_against,
                        matches_played,wins,draws,losses,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                       ON CONFLICT(season_id,team_id) DO UPDATE SET
                       current_rank=excluded.current_rank, points=excluded.points,
                       goals_for=excluded.goals_for, goals_against=excluded.goals_against,
                       matches_played=excluded.matches_played, wins=excluded.wins,
                       draws=excluded.draws, losses=excluded.losses,
                       updated_at=CURRENT_TIMESTAMP""",
                    (season['id'], team['id'], rank,
                     td.get('Points',0), td.get('Goals',0), td.get('OpponentGoals',0),
                     td.get('Matches',0), td.get('Wins',0), td.get('Draw',0), td.get('Losses',0))
                )
        _cache_set(cache_key, new_ts)
        app.logger.info(f'update_standings {league}: {len(table)} Teams geschrieben (hash/ts={new_ts})')
        success.append(league)

    if success:
        db.execute('UPDATE seasons SET last_updated=CURRENT_TIMESTAMP WHERE id=?', (season['id'],))
        db.commit()
        calculate_scores_for_season(season['id'])

    if errors and not success:
        return False, ' | '.join(errors)
    if errors:
        return True, 'Teilweise OK – ' + ' | '.join(errors)
    return True, 'OK'

def fetch_deadlines(season):
    """
    Holt den Anpfiff des ersten Spiels beider Ligen.
    Primär: football-data.org (falls Key gesetzt), Fallback: OpenligaDB.
    """
    import requests as _req, os as _os
    db = get_db()
    results = {}
    fd_key = _os.environ.get('FOOTBALL_DATA_API_KEY', '')

    for league in ('bl1', 'bl2'):
        try:
            earliest = None
            if fd_key:
                # football-data.org: matches?season=X&matchday=1
                matches = ol.fd_get_matchday(league, season['year'], 1)[0]
                for m in matches:
                    dt_str = m.get('datetime', '')
                    if not dt_str:
                        continue
                    try:
                        dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
                        # UTC → lokal
                        try:
                            from zoneinfo import ZoneInfo
                            dt = dt.astimezone(ZoneInfo('Europe/Berlin')).replace(tzinfo=None)
                        except ImportError:
                                                    dt = dt.replace(tzinfo=None) + timedelta(hours=2)
                        if earliest is None or dt < earliest:
                            earliest = dt
                    except Exception:
                        continue
            else:
                # OpenligaDB: Rohformat (matchDateTime-Feld)
                matches = ol.get_matchday(league, season['year'], 1)
                for m in matches:
                    dt_str = m.get('matchDateTime') or m.get('MatchDateTime', '')
                    if not dt_str:
                        continue
                    try:
                        dt = datetime.fromisoformat(dt_str.replace('Z', '').rstrip('+00:00'))
                        if earliest is None or dt < earliest:
                            earliest = dt
                    except Exception:
                        continue

            if earliest:
                results[league] = earliest
                db.execute(f'UPDATE seasons SET deadline_{league}=? WHERE id=?',
                           (earliest.isoformat(), season['id']))
        except Exception as e:
            app.logger.debug(f"Ignorierter Fehler: {e}")

    if results:
        db.commit()
    return results

def check_and_autolock(season):
    """
    Sperrt die Tippabgabe automatisch wenn der erste Spieltag begonnen hat.
    Aktualisiert auch season_started. Gibt die (ggf. neu geladene) Season zurück.
    """
    if not season or season['tips_locked']:
        return season
    # sqlite3.Row hat kein .get() – in dict umwandeln
    season = dict(season)
    db  = get_db()
    now = local_now()
    dl_bl1 = season['deadline_bl1']
    dl_bl2 = season['deadline_bl2']
    if not dl_bl1 and not dl_bl2:
        fetched = fetch_deadlines(season)
        season  = get_active_season()
        dl_bl1  = season['deadline_bl1']
        dl_bl2  = season['deadline_bl2']

    changed = False
    # BL2 sperren sobald ihre Deadline erreicht ist
    if dl_bl2 and not season.get('locked_bl2', 0):
        try:
            if now >= datetime.fromisoformat(str(dl_bl2)):
                db.execute('UPDATE seasons SET locked_bl2=1 WHERE id=?', (season['id'],))
                changed = True
        except Exception as e:
            app.logger.debug(f"Ignorierter Fehler: {e}")
    # BL1 sperren sobald ihre Deadline erreicht ist
    if dl_bl1 and not season.get('locked_bl1', 0):
        try:
            if now >= datetime.fromisoformat(str(dl_bl1)):
                db.execute('UPDATE seasons SET locked_bl1=1 WHERE id=?', (season['id'],))
                changed = True
        except Exception as e:
            app.logger.debug(f"Ignorierter Fehler: {e}")
    # Beide Ligen gesperrt → Saison vollständig sperren und starten
    if changed:
        season_r = dict(get_active_season()) if changed else season
        if season_r and season_r.get('locked_bl1') and season_r.get('locked_bl2'):
            db.execute('UPDATE seasons SET tips_locked=1, season_started=1 WHERE id=?', (season['id'],))
        elif changed:
            db.execute('UPDATE seasons SET season_started=1 WHERE id=?', (season['id'],))
        db.commit()
        return dict(get_active_season())
    return season

def maybe_auto_update(season):
    if not season: return season
    season = check_and_autolock(season)
    if not season or not season['season_started']: return season
    # Adaptives TTL: football-data.org 15 Min., OpenligaDB 60 Min.
    fd_key = os.environ.get('FOOTBALL_DATA_API_KEY', '')
    ttl    = ol.INTERVAL_TABLE_FD if fd_key else ol.INTERVAL_TABLE
    if season['last_updated']:
        try:
            secs = (local_now() - datetime.fromisoformat(str(season['last_updated']))).total_seconds()
            if secs < ttl:
                return season
        except Exception as e:
            app.logger.debug(f"Ignorierter Fehler: {e}")
    try:
        update_standings_from_api(season)
    except Exception as e:
        app.logger.debug(f"Ignorierter Fehler: {e}")
    return get_active_season()

def get_pred_status(user_id, season_id):
    counts = team_count_per_league(season_id)
    preds  = {r['league']: r['c'] for r in get_db().execute(
        'SELECT t.league,COUNT(*) as c FROM predictions p JOIN teams t ON p.team_id=t.id WHERE p.user_id=? AND p.season_id=? GROUP BY t.league',
        (user_id, season_id)
    ).fetchall()}
    return {
        'bl1': preds.get('bl1',0) == counts.get('bl1',0) and counts.get('bl1',0) > 0,
        'bl2': preds.get('bl2',0) == counts.get('bl2',0) and counts.get('bl2',0) > 0,
        'complete': all(preds.get(lg,0) == counts.get(lg,0) for lg in counts if counts[lg] > 0),
        'preds': preds, 'counts': counts,
    }

def get_prev_year_table(season):
    """
    Holt die Vorjahres-Abschlusstabelle:
    1. Aus der Datenbank (vorherige Saison mit Standings)
    2. Sonst: OpenligaDB mit year-1
    Gibt dict zurück: {openliga_id: {'rank': X, 'league': 'bl1', 'name': '...'}}
    """
    db = get_db()
    prev_year = season['year'] - 1

    # Erst in DB suchen: vorherige Saison
    prev_season = db.execute(
        'SELECT id FROM seasons WHERE year=? AND is_active=0 ORDER BY id DESC LIMIT 1',
        (prev_year,)
    ).fetchone()

    if prev_season:
        rows = db.execute(
            """SELECT t.openliga_id, t.league, t.name, s.current_rank
               FROM standings s JOIN teams t ON s.team_id=t.id
               WHERE s.season_id=? AND t.openliga_id IS NOT NULL AND t.openliga_id > 0""",
            (prev_season['id'],)
        ).fetchall()
        if rows:
            return {r['openliga_id']: {'rank': r['current_rank'],
                                        'league': r['league'],
                                        'name': r['name']} for r in rows}

    # Sonst: Cache in prev_standings Tabelle prüfen
    cached = db.execute(
        'SELECT * FROM prev_standings WHERE season_id=?', (season['id'],)
    ).fetchall()
    if cached:
        return {r['openliga_id']: {'rank': r['final_rank'],
                                    'league': r['league'],
                                    'name': r['team_name']} for r in cached}

    # Sonst: von OpenligaDB laden und cachen
    import requests as _req
    result = {}
    for league in ('bl1', 'bl2'):
        try:
            table = ol.get_table(league, prev_year)
            for rank, td in enumerate(table, 1):
                oid  = td['TeamInfoId']
                name = td['TeamName']
                result[oid] = {'rank': rank, 'league': league, 'name': name}
                db.execute(
                    """INSERT OR REPLACE INTO prev_standings
                       (season_id, openliga_id, league, final_rank, team_name)
                       VALUES (?,?,?,?,?)""",
                    (season['id'], oid, league, rank, name)
                )
        except Exception as e:
            app.logger.debug(f"Ignorierter Fehler: {e}")
    if result:
        db.commit()
    return result

def calculate_angsthasen(season):
    """
    Berechnet den gewichteten Angsthasen-Wert für jeden Tipper.

    Formel: Abw(gew) = Abw(ges) / (n * k)
      n = Alle Teams in der Liga (z.B. 18)
      k = Verbleibende Teams (nicht auf-/abgestiegen, also in beiden Saisons vertreten)
    Je kleiner Abw(gew), desto mehr Angsthase (tippt nah an der Vorjahrestabelle).

    Auf- und Absteiger werden herausgefiltert — nur Teams die in beiden Saisons
    in derselben Liga spielten zählen für den Vergleich.
    """
    db       = get_db()
    prev_map = get_prev_year_table(season)   # {openliga_id: {rank, league, name}}
    if not prev_map:
        return [], False

    # Aktuelle Teams: team_id → openliga_id + liga
    curr_teams = db.execute(
        'SELECT id, openliga_id, league FROM teams WHERE season_id=? AND openliga_id > 0',
        (season['id'],)
    ).fetchall()

    # n pro Liga: Anzahl aller aktuellen Teams
    n = {'bl1': 0, 'bl2': 0}
    for t in curr_teams:
        if t['league'] in n:
            n[t['league']] += 1

    # Verbleibende Teams: in beiden Saisons in DERSELBEN Liga (keine Auf-/Absteiger)
    # Ein Team gilt als verblieben wenn openliga_id in prev_map und liga identisch
    team_to_prev = {}   # team_id → (prev_rank, league)
    k = {'bl1': 0, 'bl2': 0}
    for t in curr_teams:
        if t['openliga_id'] in prev_map:
            p = prev_map[t['openliga_id']]
            if p['league'] == t['league']:          # gleiche Liga → kein Auf-/Absteiger
                team_to_prev[t['id']] = (p['rank'], t['league'])
                k[t['league']] += 1

    if not team_to_prev:
        return [], True

    users = db.execute(
        """SELECT DISTINCT u.id, u.username, u.display_name, u.full_name, u.favorite_club
           FROM users u JOIN predictions p ON u.id=p.user_id
           WHERE p.season_id=? AND u.is_active=1""",
        (season['id'],)
    ).fetchall()

    results = []
    for user in users:
        preds = {p['team_id']: p['predicted_rank'] for p in db.execute(
            'SELECT team_id, predicted_rank FROM predictions WHERE user_id=? AND season_id=?',
            (user['id'], season['id'])
        ).fetchall()}

        dev = {'bl1': 0.0, 'bl2': 0.0}
        matched = {'bl1': 0, 'bl2': 0}
        for tid, (prev_rank, lg) in team_to_prev.items():
            if tid in preds:
                dev[lg]     += abs(preds[tid] - prev_rank)
                matched[lg] += 1

        if matched['bl1'] + matched['bl2'] == 0:
            continue

        # Gewichtete Abweichung pro Liga: Abw(ges) / (n * k)
        def weighted(lg):
            if k[lg] == 0 or n[lg] == 0 or matched[lg] == 0:
                return None
            return dev[lg] / (n[lg] * k[lg])

        w_bl1 = weighted('bl1')
        w_bl2 = weighted('bl2')

        # Gesamtwert: Mittel beider verfügbaren Ligas
        w_vals = [v for v in (w_bl1, w_bl2) if v is not None]
        w_total = sum(w_vals) / len(w_vals) if w_vals else 0.0

        results.append({
            'user_id':      user['id'],
            'display_name': user['display_name'] or user['username'],
            'username':     user['username'],
            'full_name':    user['full_name'] or '',
            'favorite_club': user['favorite_club'] or '',
            'dev_bl1':      dev['bl1'],
            'dev_bl2':      dev['bl2'],
            'total':        dev['bl1'] + dev['bl2'],
            'w_bl1':        w_bl1,
            'w_bl2':        w_bl2,
            'w_total':      w_total,
            'k_bl1':        k['bl1'],
            'k_bl2':        k['bl2'],
            'n_bl1':        n['bl1'],
            'n_bl2':        n['bl2'],
        })

    # Sortierung nach gewichtetem Gesamtwert (kleinster = größter Angsthase)
    results.sort(key=lambda x: x['w_total'])
    return results, True

def has_nachfrist(user_id, season_id):
    """Prüft ob ein User eine aktive (nicht entzogene) Nachfrist hat."""
    from datetime import datetime as _dt
    row = get_db().execute(
        'SELECT expires_at FROM nachfrist WHERE user_id=? AND season_id=? AND revoked_at IS NULL',
        (user_id, season_id)
    ).fetchone()
    if not row:
        return False
    if not row['expires_at']:
        return True   # keine Ablaufzeit = unbegrenzt
    try:
        return _dt.now() <= _dt.fromisoformat(str(row['expires_at']))
    except Exception:
        return False

# Rückwärtskompatibilität
def get_wildcard_ids(scores):
    """Gibt die user_ids ALLER Spieler mit der höchsten max_deviation zurück (Set)."""
    valid = [s for s in scores if s['max_deviation']]
    if not valid:
        return set()
    max_dev = max(s['max_deviation'] for s in valid)
    return {s['id'] for s in valid if s['max_deviation'] == max_dev}

def get_wildcard_id(scores):
    ids = get_wildcard_ids(scores)
    return next(iter(ids)) if ids else None


# ── VAPID-Keys für Web Push ───────────────────────────────────
# VAPID-Keys für Web Push — aus Umgebungsvariablen lesen (in passenger_wsgi.py setzen)
# Fallback auf eingebettete Keys für bestehende Installationen
VAPID_PRIVATE_KEY = os.environ.get(
    'VAPID_PRIVATE_KEY',
    'aBkFyKdyU7cATp_pJRq9Wxu34MvlwGXZqZp6Y02TcGY'
)
VAPID_PUBLIC_KEY  = os.environ.get(
    'VAPID_PUBLIC_KEY',
    'BMBMG_jooxyWUDghBy9s8X6AfjUfSR-oyvPFP0XPNy8DSsOcgzth2ZS69hrVMiTZHjlRCn0vPElCQjiNoWuKWmw'
)
VAPID_CLAIMS      = {"sub": os.environ.get('VAPID_EMAIL', 'mailto:admin@tippcup.de')}
VAPID_KEY_VERSION = os.environ.get('VAPID_KEY_VERSION', 'v2')

def send_push_notification(user_id, title, body, url='/'):
    """Sendet eine Web Push Notification an alle Geräte eines Users."""
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        app.logger.warning('pywebpush nicht verfügbar')
        return
    db = get_db()
    subs = db.execute('SELECT * FROM push_subscriptions WHERE user_id=?', (user_id,)).fetchall()
    for sub in subs:
        try:
            parsed = urlparse(sub['endpoint'])
            aud    = f"{parsed.scheme}://{parsed.netloc}"
            claims = {**VAPID_CLAIMS, 'aud': aud}
            resp = webpush(
                subscription_info={
                    "endpoint": sub['endpoint'],
                    "keys": {"p256dh": sub['p256dh'], "auth": sub['auth']}
                },
                data=json.dumps({"title": title, "body": body, "url": url}),
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims=claims,
                content_encoding="aes128gcm",
                ttl=86400,
            )
            status = resp.status_code if resp else '?'
            app.logger.info(f'Push OK → user_id={user_id} aud={aud} status={status}')
        except WebPushException as ex:
            status = ex.response.status_code if ex.response else '?'
            body_text = ex.response.text[:200] if ex.response else ''
            app.logger.error(f'Push FAIL user_id={user_id} status={status} body={body_text}')
            if status in (404, 410):
                db.execute('DELETE FROM push_subscriptions WHERE id=?', (sub['id'],))
                db.commit()
        except Exception as ex:
            app.logger.error(f'Push Exception user_id={user_id}: {type(ex).__name__}: {ex}')

def calculate_and_assign_badges(season_id):
    """
    Berechnet und vergibt Abzeichen für die Saison.
    Alle bestehenden Badges werden zuerst gelöscht und dann neu vergeben —
    so gibt es pro Badge-Typ immer genau einen Träger.
    """
    db = get_db()
    scores = db.execute(
        '''SELECT sc.user_id, sc.score, sc.std_deviation, sc.volltreffer, sc.max_deviation
           FROM scores sc JOIN users u ON sc.user_id=u.id
           WHERE sc.season_id=? AND u.is_active=1
           ORDER BY sc.score DESC, sc.std_deviation ASC, sc.volltreffer DESC, sc.user_id ASC''',
        (season_id,)
    ).fetchall()
    if not scores:
        return

    # Alle alten Badges dieser Saison löschen → saubere Neuvergabe
    db.execute('DELETE FROM user_badges WHERE season_id=?', (season_id,))

    def award(user_id, badge_type):
        db.execute(
            'INSERT INTO user_badges (user_id,season_id,badge_type) VALUES (?,?,?)',
            (user_id, season_id, badge_type)
        )

    # 🏆 Saisonsieger
    award(scores[0]['user_id'], 'champion')
    # 🎯 Volltreffer-König
    vk = max(scores, key=lambda s: s['volltreffer'] or 0)
    award(vk['user_id'], 'volltreffer_king')
    # 🔩 Eiserner Tipper (niedrigste sigma)
    iron = min(scores, key=lambda s: s['std_deviation'] or 999)
    award(iron['user_id'], 'iron_tipper')
    # 🎲 Wildcard gesamt (höchste Einzelabweichung, alle bei Gleichstand)
    if scores:
        max_dev = max((s['max_deviation'] or 0) for s in scores)
        if max_dev > 0:
            for s in scores:
                if (s['max_deviation'] or 0) == max_dev:
                    award(s['user_id'], 'wildcard')

    # 🎲 Wildcard BL1 / BL2 – höchste Abweichung je Liga (alle bei Gleichstand)
    for league, badge_type in (('bl1', 'wildcard_bl1'), ('bl2', 'wildcard_bl2')):
        rows = db.execute(
            """SELECT p.user_id, MAX(ABS(p.predicted_rank - st.current_rank)) AS max_dev
               FROM predictions p
               JOIN teams t     ON p.team_id = t.id
               JOIN standings st ON st.team_id = t.id AND st.season_id = p.season_id
               WHERE p.season_id=? AND t.league=?
               GROUP BY p.user_id""",
            (season_id, league)
        ).fetchall()
        if rows:
            top = max(r['max_dev'] or 0 for r in rows)
            if top > 0:
                for r in rows:
                    if (r['max_dev'] or 0) == top:
                        award(r['user_id'], badge_type)
    # 🐇 / 🦁 Angsthasen
    season = db.execute('SELECT * FROM seasons WHERE id=?', (season_id,)).fetchone()
    if season:
        angst, _ = calculate_angsthasen(season)
        if angst:
            award(angst[0]['user_id'],  'angsthase')
            award(angst[-1]['user_id'], 'mutiger_tipper')
    db.commit()

def get_user_badges(user_id, season_id=None):
    db = get_db()
    q = 'SELECT * FROM user_badges WHERE user_id=?'
    params = [user_id]
    if season_id:
        q += ' AND season_id=?'; params.append(season_id)
    rows = db.execute(q, params).fetchall()
    return [{**BADGE_DEFS.get(r['badge_type'], {}), 'type': r['badge_type'],
              'season_id': r['season_id']} for r in rows if r['badge_type'] in BADGE_DEFS]


def get_smtp_config():
    return get_db().execute('SELECT * FROM smtp_config WHERE id=1').fetchone()

def send_email(to_list, subject, body_html, body_text=None):
    """
    Sendet eine E-Mail über den konfigurierten SMTP-Server.
    to_list = Liste von (name, email) Tupeln oder einzelne Strings.
    Gibt (success_count, error_list) zurück.
    """

    cfg = get_smtp_config()
    if not cfg or not cfg['host'] or not cfg['sender_email']:
        return 0, ['SMTP nicht konfiguriert. Bitte unter Admin → E-Mail einrichten.']

    success, errors = 0, []
    for recipient in to_list:
        if isinstance(recipient, (list, tuple)):
            name, addr = recipient[0], recipient[1]
        else:
            name, addr = '', recipient
        if not addr:
            continue
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From']    = formataddr((cfg['sender_name'], cfg['sender_email']))
            msg['To']      = formataddr((name, addr)) if name else addr

            if body_text:
                msg.attach(MIMEText(body_text, 'plain', 'utf-8'))
            msg.attach(MIMEText(body_html, 'html', 'utf-8'))

            if cfg['use_tls']:
                server = smtplib.SMTP(cfg['host'], cfg['port'], timeout=15)
                server.starttls()
            else:
                server = smtplib.SMTP_SSL(cfg['host'], cfg['port'], timeout=15)

            if cfg['username']:
                server.login(cfg['username'], cfg['password'])
            server.sendmail(cfg['sender_email'], addr, msg.as_string())
            server.quit()
            success += 1
        except Exception as e:
            errors.append(f'{addr}: {e}')
    return success, errors


# ════════════════════════════════════════════════════════════
# AUTHENTIFIZIERUNG & SICHERHEIT
# ════════════════════════════════════════════════════════════
# ── Brute-Force-Schutz ───────────────────────
MAX_ATTEMPTS  = 5    # Fehlversuche bis Sperre
LOCKOUT_MIN   = 15   # Sperrzeit in Minuten
CLEANUP_HOURS = 24   # Alte Einträge löschen nach X Stunden

def get_client_ip():
    """IP-Adresse des Clients ermitteln (auch hinter Proxy/Nginx)."""
    return (request.headers.get('X-Forwarded-For', '').split(',')[0].strip()
            or request.headers.get('X-Real-IP', '')
            or request.remote_addr
            or 'unknown')

def _utc_cutoff(minutes=None, hours=None):
    """Cutoff-Zeitstempel in UTC mit SQLite-kompatiblem Format (Leerzeichen statt T)."""
    delta  = timedelta(minutes=minutes) if minutes else timedelta(hours=hours)
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - delta
    return cutoff.strftime('%Y-%m-%d %H:%M:%S')

def is_locked_out(ip: str) -> tuple[bool, int]:
    """Prüft ob eine IP gesperrt ist. Gibt (gesperrt, verbleibende_minuten) zurück."""
    db     = get_db()
    cutoff = _utc_cutoff(minutes=LOCKOUT_MIN)
    count  = db.execute(
        'SELECT COUNT(*) as c FROM login_attempts WHERE ip=? AND created_at > ?',
        (ip, cutoff)
    ).fetchone()['c']
    if count >= MAX_ATTEMPTS:
        oldest = db.execute(
            'SELECT created_at FROM login_attempts WHERE ip=? AND created_at > ? ORDER BY created_at ASC LIMIT 1',
            (ip, cutoff)
        ).fetchone()
        if oldest:
            try:
                now_utc     = datetime.now(timezone.utc).replace(tzinfo=None)
                unlock_time = datetime.fromisoformat(str(oldest['created_at']).replace(' ','T')) + timedelta(minutes=LOCKOUT_MIN)
                remaining   = max(1, int((unlock_time - now_utc).total_seconds() / 60))
                return True, remaining
            except Exception as e:
                app.logger.debug(f"Ignorierter Fehler: {e}")
        return True, LOCKOUT_MIN
    return False, 0

def record_failed_attempt(ip: str, username: str):
    db = get_db()
    db.execute('INSERT INTO login_attempts (ip, username) VALUES (?,?)', (ip, username))
    db.execute('DELETE FROM login_attempts WHERE created_at < ?', (_utc_cutoff(hours=CLEANUP_HOURS),))
    db.commit()

def clear_attempts(ip: str):
    get_db().execute('DELETE FROM login_attempts WHERE ip=?', (ip,))
    get_db().commit()

def get_attempt_count(ip: str) -> int:
    return get_db().execute(
        'SELECT COUNT(*) as c FROM login_attempts WHERE ip=? AND created_at > ?',
        (ip, _utc_cutoff(minutes=LOCKOUT_MIN))
    ).fetchone()['c']


# ════════════════════════════════════════════════════════════
# ROUTEN – BENUTZER
# ════════════════════════════════════════════════════════════

@app.route('/login', methods=['GET','POST'])
def login():
    if 'user_id' in session: return redirect(url_for('dashboard'))
    ip = get_client_ip()

    # Gesperrt?
    locked, remaining = is_locked_out(ip)
    if locked:
        flash(f'Zu viele Fehlversuche. Bitte {remaining} Minute{"n" if remaining != 1 else ""} warten.', 'danger')
        return render_template('login.html', locked=True, remaining=remaining)

    if request.method == 'POST':
        session.clear()  # Session-Fixation verhindern
        username = request.form.get('username','').strip()
        password = request.form.get('password','')
        user = get_db().execute(
            'SELECT * FROM users WHERE username=? AND is_active=1', (username,)
        ).fetchone()

        if user and check_password_hash(user['password_hash'], password):
            # Erfolg: Fehlversuche löschen, Session setzen
            clear_attempts(ip)
            remember = request.form.get('remember_me') == '1'
            session.permanent = True
            if remember:
                app.permanent_session_lifetime = timedelta(days=30)
            else:
                app.permanent_session_lifetime = timedelta(hours=8)
            session.update({'user_id':user['id'], 'username':user['username'],
                            'display_name':user['display_name'] or user['username'],
                            'is_admin':bool(user['is_admin']),
                            'remember_me': remember})
            get_db().execute('UPDATE users SET last_login=? WHERE id=?', (local_now().isoformat(), user['id']))
            get_db().commit()
            flash(f'Willkommen, {session["display_name"]}! ⚽', 'success')
            return redirect(request.args.get('next') or url_for('dashboard'))

        # Fehlgeschlagen: Versuch speichern
        record_failed_attempt(ip, username)
        attempts = get_attempt_count(ip)
        remaining_attempts = MAX_ATTEMPTS - attempts

        if remaining_attempts <= 0:
            flash(f'Zu viele Fehlversuche. Bitte {LOCKOUT_MIN} Minuten warten.', 'danger')
            return render_template('login.html', locked=True, remaining=LOCKOUT_MIN)
        elif remaining_attempts <= 2:
            flash(f'Ungültige Anmeldedaten. Noch {remaining_attempts} Versuch{"e" if remaining_attempts != 1 else ""} übrig.', 'warning')
        else:
            flash('Ungültiger Benutzername oder Passwort.', 'danger')

    attempts = get_attempt_count(ip)
    return render_template('login.html', locked=False,
                           attempts=attempts, max_attempts=MAX_ATTEMPTS)

@app.route('/logout')
def logout():
    session.clear(); flash('Erfolgreich abgemeldet.', 'info')
    return redirect(url_for('login'))

@app.route('/')
@login_required
def dashboard():
  try:
    maybe_trigger_backup_async()
    season = maybe_auto_update(get_active_season())
    pred_status, user_score, user_rank = None, None, None
    standings = {'bl1':[], 'bl2':[]}
    if season:
        pred_status = get_pred_status(session['user_id'], season['id'])
        user_score  = get_db().execute(
            'SELECT * FROM scores WHERE user_id=? AND season_id=?',
            (session['user_id'], season['id'])
        ).fetchone()
        # Platzierung in der Bestenliste ermitteln
        if user_score:
            all_scores = get_db().execute(
                """SELECT user_id, score FROM scores
                   JOIN users u ON scores.user_id=u.id
                   WHERE season_id=? AND u.is_active=1
                   ORDER BY score DESC, std_deviation ASC, volltreffer DESC, user_id ASC""",
                (season['id'],)
            ).fetchall()
            for i, s in enumerate(all_scores, 1):
                if s['user_id'] == session['user_id']:
                    user_rank = i
                    break
        for lg in ('bl1','bl2'):
            standings[lg] = get_standings(season['id'], lg)
    # Angsthasen-Platz ermitteln
    angst_rank  = None
    angst_total = None
    is_top_angsthase = False
    if season and season['season_started']:
        angst_results, _ = calculate_angsthasen(season)
        for i, r in enumerate(angst_results, 1):
            if r['user_id'] == session['user_id']:
                angst_rank  = i
                angst_total = len(angst_results)
                break
        is_top_angsthase = bool(angst_results and angst_results[0]['user_id'] == session['user_id'])
    return render_template('dashboard.html', season=season, standings=standings,
        pred_status=pred_status, user_score=user_score,
        user_rank=user_rank, angst_rank=angst_rank, angst_total=angst_total,
        is_top_angsthase=is_top_angsthase,
        leagues=LEAGUES, max_score=MAX_SCORE)
  except Exception:
    app.logger.exception('Dashboard Fehler')
    return render_template('errors/500.html'), 500

@app.route('/predict', methods=['GET','POST'])
@login_required
def predict():
  try:
    season = get_active_season()
    if not season: flash('Keine aktive Saison.','warning'); return redirect(url_for('dashboard'))
    # Auto-lock prüfen bevor wir die Seite anzeigen
    season = check_and_autolock(season)
    uid = session['user_id']
    nf  = has_nachfrist(uid, season['id'])
    if season['tips_locked'] and not nf:
        flash('Tippabgabe gesperrt – alle Ligen haben begonnen.', 'warning')
        return redirect(url_for('dashboard'))
    # Welche Ligen sind gesperrt? Bei Nachfrist alles offen
    locked_leagues = set()
    try:
        if not nf:
            if season.get('locked_bl1'): locked_leagues.add('bl1')
            if season.get('locked_bl2'): locked_leagues.add('bl2')
    except Exception:
        pass
    db    = get_db()
    # Deadlines aufbereiten
    deadlines = {}
    for lg in ('bl1','bl2'):
        dl = season[f'deadline_{lg}']
        if dl:
            try:
                deadlines[lg] = datetime.fromisoformat(str(dl))
            except Exception as e:
                app.logger.debug(f"Ignorierter Fehler: {e}")
    # Falls noch keine Deadlines: im Hintergrund holen (nicht blockierend)
    if not deadlines:
        try:
            fetch_deadlines(season)
            season = get_active_season()
            for lg in ('bl1','bl2'):
                dl = season[f'deadline_{lg}']
                if dl:
                    try: deadlines[lg] = datetime.fromisoformat(str(dl))
                    except Exception as e:
                        app.logger.debug(f"Ignorierter Fehler: {e}")
        except Exception as e:
            app.logger.debug(f"Ignorierter Fehler: {e}")
    teams = {lg: get_season_teams(season['id'], lg) for lg in ('bl1','bl2')}
    existing = {p['team_id']: p['predicted_rank'] for p in db.execute(
        'SELECT team_id,predicted_rank FROM predictions WHERE user_id=? AND season_id=?',
        (session['user_id'], season['id'])
    ).fetchall()}
    if request.method == 'POST':
        ranks, errors = {}, []
        for lg, lg_teams in teams.items():
            if not lg_teams: continue
            if lg in locked_leagues:
                continue   # gesperrte Liga überspringen
            used = set()
            for team in lg_teams:
                val = request.form.get(f'rank_{team["id"]}','')
                if not val or not val.isdigit():
                    errors.append(f'[{LEAGUES[lg]}] Platzierung für {team["name"]} fehlt.'); continue
                rank = int(val)
                if rank < 1 or rank > len(lg_teams): errors.append(f'[{LEAGUES[lg]}] Ungültige Platzierung.')
                elif rank in used: errors.append(f'[{LEAGUES[lg]}] Platz {rank} doppelt.')
                else: used.add(rank); ranks[team['id']] = rank
        if errors:
            for e in errors: flash(e,'danger')
        elif len(ranks) != sum(len(t) for lg, t in teams.items() if lg not in locked_leagues):
            flash('Bitte alle Teams beider Ligen platzieren.','danger')
        else:
            for tid, rank in ranks.items():
                db.execute(
                    """INSERT INTO predictions (user_id,season_id,team_id,predicted_rank,updated_at) VALUES (?,?,?,?,CURRENT_TIMESTAMP)
                       ON CONFLICT(user_id,season_id,team_id) DO UPDATE SET predicted_rank=excluded.predicted_rank,updated_at=CURRENT_TIMESTAMP""",
                    (session['user_id'], season['id'], tid, rank))
            db.commit()
            if db.execute('SELECT COUNT(*) as c FROM standings WHERE season_id=?',(season['id'],)).fetchone()['c'] > 0:
                calculate_scores_for_season(season['id'])
            flash('Tipp erfolgreich gespeichert! 🎯','success')
            return redirect(url_for('dashboard'))
    for lg in ('bl1','bl2'):
        teams[lg] = sorted(teams[lg], key=lambda t: existing.get(t['id'], 99) if existing else t['name'])
    # Tipp-Konfidenz: Durchschnittstipps aller anderen Spieler
    group_avg = {}
    if existing:
        others = db.execute(
            """SELECT p.team_id, AVG(p.predicted_rank) as avg_rank
               FROM predictions p
               JOIN users u ON p.user_id=u.id
               WHERE p.season_id=? AND p.user_id!=? AND u.is_active=1
               GROUP BY p.team_id""",
            (season['id'], session['user_id'])
        ).fetchall()
        group_avg = {r['team_id']: round(r['avg_rank'], 1) for r in others}
    return render_template('predict.html', season=season, teams=teams,
        existing=existing, leagues=LEAGUES, deadlines=deadlines,
        group_avg=group_avg, now_utc=local_now(),
        locked_leagues=locked_leagues)
  except Exception:
    app.logger.exception('Predict Fehler')
    return render_template('errors/500.html'), 500

@app.route('/leaderboard/scores')
@login_required
def leaderboard_scores_api():
    """JSON-Endpunkt: aktuelle Scores für Live-Refresh der Bestenliste."""
    season = get_active_season()
    if not season:
        return jsonify({'ok': False})
    scores = get_db().execute(
        """SELECT u.id, u.username, u.display_name,
                  sc.score, sc.score_bl1, sc.score_bl2,
                  sc.total_deviation, sc.volltreffer, sc.updated_at
           FROM scores sc JOIN users u ON sc.user_id=u.id
           WHERE sc.season_id=? AND u.is_active=1
           ORDER BY sc.score DESC, sc.std_deviation ASC, sc.volltreffer DESC, sc.user_id ASC""",
        (season['id'],)
    ).fetchall()
    return jsonify({
        'ok': True,
        'last_updated': season['last_updated'],
        'scores': [{
            'id':           s['id'],
            'name':         s['display_name'] or s['username'],
            'score':        s['score'],
            'score_bl1':    s['score_bl1'],
            'score_bl2':    s['score_bl2'],
            'deviation':    s['total_deviation'],
            'volltreffer':  s['volltreffer'],
            'updated_at':   s['updated_at'],
        } for s in scores]
    })

@app.route('/leaderboard')
@login_required
def leaderboard():
  try:
    season = get_active_season()
    scores, pending = [], []
    if season:
        scores = get_db().execute(
            """SELECT u.id,u.username,u.display_name,u.full_name,u.favorite_club,
                      sc.score,sc.score_bl1,sc.score_bl2,
                      sc.deviation_bl1,sc.deviation_bl2,sc.total_deviation,
                      sc.std_deviation,sc.max_deviation,sc.volltreffer,
                      sc.updated_at
               FROM scores sc JOIN users u ON sc.user_id=u.id
               WHERE sc.season_id=? AND u.is_active=1 ORDER BY sc.score DESC, sc.std_deviation ASC, sc.volltreffer DESC, sc.user_id ASC""",
            (season['id'],)
        ).fetchall()
        scored_ids = {s['id'] for s in scores}
        pending = [u for u in get_db().execute(
            'SELECT DISTINCT u.id,u.username,u.display_name FROM users u JOIN predictions p ON u.id=p.user_id WHERE p.season_id=? AND u.is_active=1',
            (season['id'],)
        ).fetchall() if u['id'] not in scored_ids]
    # Badges der Saison laden
    user_badges_map = {}
    if season:
        for row in get_db().execute(
            'SELECT user_id, badge_type FROM user_badges WHERE season_id=?', (season['id'],)
        ).fetchall():
            user_badges_map.setdefault(row['user_id'], []).append(row['badge_type'])
    # Größte Einzel-Abweichung pro User und Liga (mit Vereinsname)
    max_dev_map = {}   # user_id → {bl1: {dev, team}, bl2: {dev, team}}
    if season:
        db = get_db()
        rows = db.execute(
            """SELECT p.user_id, t.league, t.name AS team_name,
                      ABS(p.predicted_rank - s.current_rank) AS dev,
                      p.predicted_rank, s.current_rank
               FROM predictions p
               JOIN teams t      ON p.team_id = t.id
               JOIN standings s  ON s.team_id = t.id AND s.season_id = p.season_id
               WHERE p.season_id = ?
               ORDER BY p.user_id, t.league, dev DESC""",
            (season['id'],)
        ).fetchall()
        for r in rows:
            uid = r['user_id']
            lg  = r['league']
            if uid not in max_dev_map:
                max_dev_map[uid] = {}
            if lg not in max_dev_map[uid]:    # erste (= höchste) Zeile pro user+liga
                max_dev_map[uid][lg] = {
                    'dev':      r['dev'],
                    'team':     r['team_name'],
                    'tipp':     r['predicted_rank'],
                    'actual':   r['current_rank'],
                }
    # Wer hat die absolut größte Einzelabweichung in BL1/BL2? (alle bei Gleichstand)
    wildcard_bl1_ids = set()
    wildcard_bl2_ids = set()
    max_bl1 = max_bl2 = -1
    for uid, ligs in max_dev_map.items():
        if 'bl1' in ligs:
            if ligs['bl1']['dev'] > max_bl1:
                max_bl1 = ligs['bl1']['dev']; wildcard_bl1_ids = {uid}
            elif ligs['bl1']['dev'] == max_bl1:
                wildcard_bl1_ids.add(uid)
        if 'bl2' in ligs:
            if ligs['bl2']['dev'] > max_bl2:
                max_bl2 = ligs['bl2']['dev']; wildcard_bl2_ids = {uid}
            elif ligs['bl2']['dev'] == max_bl2:
                wildcard_bl2_ids.add(uid)
    return render_template('leaderboard.html', season=season, scores=scores,
        pending_users=pending, leagues=LEAGUES, max_score=MAX_SCORE,
        wildcard_id=get_wildcard_id(scores),
        wildcard_bl1_ids=wildcard_bl1_ids, wildcard_bl2_ids=wildcard_bl2_ids,
        user_badges_map=user_badges_map, badge_defs=BADGE_DEFS,
        max_dev_map=max_dev_map,
        now_local=local_now_str())
  except Exception:
    app.logger.exception('Leaderboard Fehler')
    return render_template('errors/500.html'), 500

@app.route('/tips')
@login_required
def tips():
  try:
    season = get_active_season()
    is_admin = session.get('is_admin', False)
    if not season:
        flash('Keine aktive Saison.', 'info')
        return redirect(url_for('dashboard'))
    season_d = dict(season)
    locked_bl1 = bool(season_d.get('tips_locked') or season_d.get('locked_bl1', 0))
    locked_bl2 = bool(season_d.get('tips_locked') or season_d.get('locked_bl2', 0))
    # Mindestens eine Liga muss gesperrt sein – sonst nichts sichtbar (außer Admin)
    if not is_admin and not locked_bl1 and not locked_bl2:
        flash('Die Tipps sind erst sichtbar wenn die Tippabgabe für die jeweilige Liga abgelaufen ist.', 'info')
        return redirect(url_for('dashboard'))
    # Sichtbare Ligen: nur gesperrte (oder alle für Admin)
    visible_leagues = {}
    for lg in ('bl1', 'bl2'):
        if is_admin or (lg == 'bl1' and locked_bl1) or (lg == 'bl2' and locked_bl2):
            visible_leagues[lg] = LEAGUES[lg]
    standings_dict = {s['team_id']: s['current_rank']
                      for lg in visible_leagues for s in get_standings(season['id'], lg)}
    teams = {}
    for lg in visible_leagues:
        lg_teams = get_season_teams(season['id'], lg)
        teams[lg] = sorted(lg_teams, key=lambda t: standings_dict.get(t['id'], 99))
    users = get_db().execute(
        """SELECT DISTINCT u.id,u.username,u.display_name,
                  (SELECT score FROM scores WHERE user_id=u.id AND season_id=:sid) as score
           FROM users u JOIN predictions p ON u.id=p.user_id
           WHERE p.season_id=:sid AND u.is_active=1 ORDER BY score DESC NULLS LAST,u.display_name""",
        {'sid': season['id']}
    ).fetchall()
    users_preds = [{'user': u, 'preds': {p['team_id']: p['predicted_rank'] for p in get_db().execute(
        'SELECT team_id,predicted_rank FROM predictions WHERE user_id=? AND season_id=?',
        (u['id'],season['id'])).fetchall()}} for u in users]
    scores_all = get_db().execute(
        'SELECT u.id,sc.max_deviation,sc.std_deviation FROM scores sc JOIN users u ON sc.user_id=u.id WHERE sc.season_id=? AND u.is_active=1',
        (season['id'],)
    ).fetchall()
    return render_template('tips.html', season=season, teams=teams,
        users_preds=users_preds, standings_dict=standings_dict,
        leagues=visible_leagues, all_leagues=LEAGUES,
        locked_bl1=locked_bl1, locked_bl2=locked_bl2,
        wildcard_id=get_wildcard_id(scores_all))
  except Exception:
    app.logger.exception('Tips Fehler')
    return render_template('errors/500.html'), 500

@app.route('/tips/user/<int:uid>')
@login_required
def tips_user(uid):
    """Einzelansicht: Tipp eines Benutzers als eigene Tabelle."""
    season = get_active_season()
    is_admin = session.get('is_admin', False)
    if not season:
        flash('Keine aktive Saison.', 'info')
        return redirect(url_for('dashboard'))
    season_d = dict(season)
    locked_bl1 = bool(season_d.get('tips_locked') or season_d.get('locked_bl1', 0))
    locked_bl2 = bool(season_d.get('tips_locked') or season_d.get('locked_bl2', 0))
    if not is_admin and not locked_bl1 and not locked_bl2:
        flash('Die Tipps sind erst sichtbar wenn die Tippabgabe für die jeweilige Liga abgelaufen ist.', 'info')
        return redirect(url_for('dashboard'))
    visible_leagues = {lg: LEAGUES[lg] for lg in ('bl1','bl2')
                       if is_admin or (lg=='bl1' and locked_bl1) or (lg=='bl2' and locked_bl2)}
    db      = get_db()
    tipuser = db.execute('SELECT id,username,display_name FROM users WHERE id=? AND is_active=1', (uid,)).fetchone()
    if not tipuser:
        flash('Benutzer nicht gefunden.', 'danger')
        return redirect(url_for('tips'))
    # Standings
    standings_dict = {s['team_id']: s['current_rank']
                      for lg in ('bl1','bl2') for s in get_standings(season['id'], lg)}
    # Score dieses Users
    score = db.execute('SELECT * FROM scores WHERE user_id=? AND season_id=?',
                       (uid, season['id'])).fetchone()
    # Platzierung
    user_rank = None
    if score:
        all_scores = db.execute(
            """SELECT user_id FROM scores JOIN users u ON scores.user_id=u.id
               WHERE season_id=? AND u.is_active=1
               ORDER BY score DESC, std_deviation ASC, volltreffer DESC, user_id ASC""",
            (season['id'],)
        ).fetchall()
        for i, s in enumerate(all_scores, 1):
            if s['user_id'] == uid:
                user_rank = i; break
    # Tipps pro Liga laden – nur sichtbare Ligen
    preds = {lg: [] for lg in ('bl1', 'bl2')}
    raw = db.execute(
        """SELECT p.predicted_rank, p.team_id, p.is_auto, t.name, t.short_name,
                  t.logo_url, t.league,
                  COALESCE(s.current_rank, NULL) as current_rank
           FROM predictions p
           JOIN teams t ON p.team_id=t.id
           LEFT JOIN standings s ON s.team_id=t.id AND s.season_id=p.season_id
           WHERE p.user_id=? AND p.season_id=?""",
        (uid, season['id'])
    ).fetchall()
    for p in raw:
        if p['league'] in visible_leagues:   # gesperrte Ligen filtern
            preds[p['league']].append(p)
    for lg in preds:
        preds[lg].sort(key=lambda p: standings_dict.get(p['team_id'], 99))
    return render_template('tips_user.html',
        season=season, tipuser=tipuser, score=score,
        user_rank=user_rank, preds=preds,
        standings_dict=standings_dict,
        leagues=visible_leagues, all_leagues=LEAGUES,
        max_score=MAX_SCORE)

@app.route('/angsthasen')
@login_required
def angsthasen():
    season = get_active_season()
    if not season:
        flash('Keine aktive Saison.', 'warning')
        return redirect(url_for('dashboard'))
    results, has_prev = calculate_angsthasen(season)
    prev_year = season['year'] - 1
    return render_template('angsthasen.html',
        season=season, results=results, has_prev=has_prev,
        prev_year=prev_year, leagues=LEAGUES)

@app.route('/verlauf')
@login_required
def verlauf():
    season = get_active_season()
    if not season or not season['season_started']:
        flash('Verlauf ist erst nach Saisonbeginn verfügbar.', 'info')
        return redirect(url_for('dashboard'))
    db = get_db()

    # Pro Spieltag den neuesten Batch holen:
    # MAX(created_at) pro matchday → Zeitstempel des letzten Speichervorgangs.
    # Dann alle Zeilen mit genau diesem created_at = alle Spieler dieses Batches.
    snapshot_meta = db.execute(
        """SELECT matchday, label, MAX(created_at) as last_ts
           FROM ranking_snapshots
           WHERE season_id=? AND matchday IS NOT NULL
           GROUP BY matchday
           ORDER BY matchday ASC""",
        (season['id'],)
    ).fetchall()

    # Letzten abgeschlossenen Spieltag ermitteln (nicht current — der kann schon voraus sein)
    try:
        season_year = season['year'] if season else datetime.now().year
        current_matchday = ol.get_last_finished_matchday('bl1', season_year) or 999
    except Exception:
        current_matchday = 999

    valid_meta = [row for row in snapshot_meta
                  if row['matchday'] is not None and row['matchday'] <= current_matchday]
    if not valid_meta:
        valid_meta = list(snapshot_meta)

    labels = []
    users_data = {}

    for meta in valid_meta:
        md      = meta['matchday']
        last_ts = meta['last_ts']
        label   = meta['label'] or f'Spieltag {md}'
        if label not in labels:
            labels.append(label)

        rows = db.execute(
            """SELECT rs.rank, rs.score, rs.user_id, u.display_name, u.username
               FROM ranking_snapshots rs
               JOIN users u ON rs.user_id=u.id
               WHERE rs.season_id=? AND rs.matchday=? AND rs.created_at=?
                 AND u.is_active=1
               ORDER BY rs.rank ASC""",
            (season['id'], md, last_ts)
        ).fetchall()

        for r in rows:
            uid  = r['user_id']
            name = r['display_name'] or r['username']
            if uid not in users_data:
                users_data[uid] = {'name': name, 'ranks': [], 'scores': []}
            users_data[uid]['ranks'].append(r['rank'])
            users_data[uid]['scores'].append(r['score'])

    # Aktuelle Scores für Sortierung
    current = db.execute(
        """SELECT sc.user_id, sc.score FROM scores sc
           JOIN users u ON sc.user_id=u.id
           WHERE sc.season_id=? AND u.is_active=1
           ORDER BY sc.score DESC""",
        (season['id'],)
    ).fetchall()
    total_players = len(current)
    return render_template('verlauf.html',
        season=season, labels=labels,
        users_data=users_data, current=current,
        total_players=total_players,
        my_uid=session.get('user_id', 0))


    season = get_active_season()
    if not season:
        flash('Keine aktive Saison.', 'warning')
        return redirect(url_for('dashboard'))
    results, has_prev = calculate_angsthasen(season)
    prev_year = season['year'] - 1
    return render_template('angsthasen.html',
        season=season, results=results, has_prev=has_prev,
        prev_year=prev_year, leagues=LEAGUES)

@app.route('/profile', methods=['GET','POST'])
@login_required
def profile():
    db = get_db()
    if request.method == 'POST':
        ip = get_client_ip()
        locked, remaining = is_locked_out(ip)
        if locked:
            flash(f'Zu viele Fehlversuche. Bitte {remaining} Minute{"n" if remaining != 1 else ""} warten.', 'danger')
            return redirect(url_for('profile'))
        user = db.execute('SELECT * FROM users WHERE id=?',(session['user_id'],)).fetchone()
        if not check_password_hash(user['password_hash'], request.form.get('current_password','')):
            record_failed_attempt(ip, f'profile:{user["username"]}')
            flash('Aktuelles Passwort falsch.','danger')
        else:
            clear_attempts(ip)
            updates, params = [], []
            dn = request.form.get('display_name','').strip()
            if dn: updates.append('display_name=?'); params.append(dn)
            fn = request.form.get('full_name','').strip()
            updates.append('full_name=?'); params.append(fn)
            fc = request.form.get('favorite_club','').strip()
            updates.append('favorite_club=?'); params.append(fc)
            em = request.form.get('email','').strip()
            updates.append('email=?'); params.append(em)
            mob = request.form.get('mobile','').strip()
            updates.append('mobile=?'); params.append(mob)
            np = request.form.get('new_password','').strip()
            if np:
                if np != request.form.get('confirm_password','').strip():
                    flash('Passwörter stimmen nicht überein.','danger'); return redirect(url_for('profile'))
                if len(np) < 6:
                    flash('Passwort mind. 6 Zeichen.','danger'); return redirect(url_for('profile'))
                updates.append('password_hash=?'); params.append(generate_password_hash(np))
            if updates:
                params.append(session['user_id'])
                db.execute(f'UPDATE users SET {",".join(updates)} WHERE id=?', params); db.commit()
                if dn: session['display_name'] = dn  # Spielername
                flash('Profil aktualisiert.','success')
    user   = db.execute('SELECT * FROM users WHERE id=?',(session['user_id'],)).fetchone()
    season = get_active_season()
    score  = None; preds = {'bl1':[], 'bl2':[]}; user_rank = None
    if season:
        score = db.execute('SELECT * FROM scores WHERE user_id=? AND season_id=?',
                           (session['user_id'],season['id'])).fetchone()
        # Platzierung ermitteln
        if score:
            all_scores = db.execute(
                """SELECT user_id FROM scores
                   JOIN users u ON scores.user_id=u.id
                   WHERE season_id=? AND u.is_active=1
                   ORDER BY score DESC, std_deviation ASC, volltreffer DESC, user_id ASC""",
                (season['id'],)
            ).fetchall()
            for i, s in enumerate(all_scores, 1):
                if s['user_id'] == session['user_id']:
                    user_rank = i; break
        # Standings für Sortierung laden
        standings_dict = {s['team_id']: s['current_rank']
                          for lg in ('bl1','bl2')
                          for s in get_standings(season['id'], lg)}
        # Tipps laden – nach aktuellem Tabellenplatz sortiert
        raw_preds = db.execute(
            """SELECT p.predicted_rank, p.team_id, p.is_auto, t.name, t.short_name,
                      t.logo_url, t.league,
                      COALESCE(s.current_rank, NULL) as current_rank
               FROM predictions p
               JOIN teams t ON p.team_id=t.id
               LEFT JOIN standings s ON s.team_id=t.id AND s.season_id=p.season_id
               WHERE p.user_id=? AND p.season_id=?""",
            (session['user_id'], season['id'])
        ).fetchall()
        for p in raw_preds:
            preds[p['league']].append(p)
        # Sortierung: nach aktuellem Rang (fallback: predicted_rank)
        for lg in preds:
            preds[lg].sort(key=lambda p: standings_dict.get(p['team_id'], 99))
    my_badges = get_user_badges(session['user_id'])
    # Historische Karriere-Daten
    legacy_rows = get_db().execute(
        'SELECT season, rang, abw_ges, treffer_ges, spieltag_1l, spieltag_2l '
        'FROM legacy_results WHERE user_id=? ORDER BY season ASC',
        (session['user_id'],)
    ).fetchall()
    legacy_wins = sum(1 for r in legacy_rows if r['rang'] == 1)
    legacy_top3 = sum(1 for r in legacy_rows if r['rang'] <= 3)
    legacy_best = min((r['rang'] for r in legacy_rows), default=None)
    return render_template('profile.html', user=user, season=season, score=score,
        preds=preds, user_rank=user_rank, leagues=LEAGUES, max_score=MAX_SCORE,
        my_badges=my_badges, vapid_public_key=VAPID_PUBLIC_KEY,
        legacy_rows=legacy_rows, legacy_wins=legacy_wins,
        legacy_top3=legacy_top3, legacy_best=legacy_best)


# ════════════════════════════════════════════════════════════
# ROUTEN – ADMIN
# ════════════════════════════════════════════════════════════

@app.route('/admin')
@admin_required
def admin_dashboard():
    db = get_db(); season = get_active_season(); sid = season['id'] if season else 0
    counts = team_count_per_league(sid)
    stats  = {'users': db.execute('SELECT COUNT(*) as c FROM users WHERE is_active=1').fetchone()['c'],
               'bl1': counts.get('bl1',0), 'bl2': counts.get('bl2',0),
               'tippers': db.execute('SELECT COUNT(DISTINCT user_id) as c FROM predictions WHERE season_id=?',(sid,)).fetchone()['c']}
    standings = {lg: get_standings(sid, lg) for lg in ('bl1','bl2')} if season else {}
    return render_template('admin/dashboard.html', season=season, stats=stats,
        standings=standings, leagues=LEAGUES)

@app.route('/admin/users')
@admin_required
def admin_users():
    season = get_active_season(); sid = season['id'] if season else 0
    users  = get_db().execute(
        """SELECT u.*,
           (SELECT COUNT(*) FROM predictions WHERE user_id=u.id AND season_id=:sid) as pred_count,
           (SELECT score FROM scores WHERE user_id=u.id AND season_id=:sid) as current_score
           FROM users u ORDER BY u.last_login DESC NULLS LAST, u.username""", {'sid':sid}
    ).fetchall()
    total = sum(team_count_per_league(sid).values())
    nachfrist_map = {r['user_id']: dict(r) for r in get_db().execute(
        'SELECT user_id, expires_at, strafgeld, notiz FROM nachfrist WHERE season_id=? AND revoked_at IS NULL', (sid,)
    ).fetchall()} if sid else {}
    return render_template('admin/users.html', users=users, season=season,
                           total_teams=total, nachfrist_map=nachfrist_map)

@app.route('/admin/users/add', methods=['GET','POST'])
@admin_required
def admin_add_user():
    if request.method == 'POST':
        session.clear()  # Session-Fixation verhindern
        username = request.form.get('username','').strip()
        password = request.form.get('password','').strip()
        if not username or len(password) < 6:
            flash('Benutzername und Passwort (mind. 6 Z.) erforderlich.','danger')
        else:
            try:
                get_db().execute('INSERT INTO users (username,display_name,email,password_hash,is_admin) VALUES (?,?,?,?,?)',
                    (username, request.form.get('display_name','').strip() or username,
                     request.form.get('email','').strip(),
                     generate_password_hash(password), 1 if request.form.get('is_admin') else 0))
                get_db().commit(); flash(f'"{username}" erstellt.','success')
                return redirect(url_for('admin_users'))
            except sqlite3.IntegrityError: flash(f'"{username}" bereits vergeben.','danger')
    return render_template('admin/user_form.html', edit_user=None)

@app.route('/admin/users/bulk', methods=['POST'])
@admin_required
def admin_bulk_users():
    raw = request.form.get('bulk_data','').strip()
    db  = get_db(); created, errors = 0, []
    for line in raw.splitlines():
        line = line.strip()
        if not line: continue
        parts = line.split(':',1)
        if len(parts) != 2: errors.append(f'Ignoriert: {line}'); continue
        u, p = parts[0].strip(), parts[1].strip()
        if len(p) < 6: errors.append(f'Passwort zu kurz: {u}'); continue
        try:
            db.execute('INSERT INTO users (username,display_name,password_hash) VALUES (?,?,?)',
                (u, u, generate_password_hash(p))); created += 1
        except sqlite3.IntegrityError: errors.append(f'"{u}" existiert bereits')
    db.commit()
    if created: flash(f'{created} Benutzer angelegt.','success')
    for e in errors: flash(e,'warning')
    return redirect(url_for('admin_users'))

@app.route('/admin/users/<int:uid>/edit', methods=['GET','POST'])
@admin_required
def admin_edit_user(uid):
    db = get_db(); eu = db.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
    if not eu: abort(404)
    if request.method == 'POST':
        display       = request.form.get('display_name','').strip()
        full_name     = request.form.get('full_name','').strip()
        favorite_club = request.form.get('favorite_club','').strip()
        email         = request.form.get('email','').strip()
        mobile        = request.form.get('mobile','').strip()
        password      = request.form.get('password','').strip()
        is_admin      = 1 if request.form.get('is_admin') else 0
        is_active     = 1 if request.form.get('is_active') else 0
        upd = ['display_name=?','full_name=?','favorite_club=?','email=?','mobile=?','is_admin=?','is_active=?']
        par = [display or eu['username'], full_name, favorite_club, email, mobile, is_admin, is_active]
        if password:
            if len(password) < 6: flash('Passwort mind. 6 Zeichen.','danger'); return render_template('admin/user_form.html',edit_user=eu)
            upd.append('password_hash=?'); par.append(generate_password_hash(password))
        par.append(uid); db.execute(f'UPDATE users SET {",".join(upd)} WHERE id=?', par); db.commit()
        flash(f'"{eu["username"]}" aktualisiert.','success'); return redirect(url_for('admin_users'))
    return render_template('admin/user_form.html', edit_user=eu)

@app.route('/admin/users/<int:uid>/nachfrist', methods=['POST'])
@admin_required
def admin_nachfrist(uid):
    """Nachfrist gewähren oder entziehen."""
    db     = get_db()
    season = get_active_season()
    if not season:
        flash('Keine aktive Saison.', 'danger')
        return redirect(url_for('admin_users'))
    user = db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
    if not user:
        flash('Benutzer nicht gefunden.', 'danger')
        return redirect(url_for('admin_users'))

    action    = request.form.get('action', 'grant')
    expires   = request.form.get('expires', '').strip() or None
    strafgeld = request.form.get('strafgeld', '').strip()
    notiz     = request.form.get('notiz', '').strip()
    name      = user['display_name'] or user['username']

    if action == 'revoke':
        db.execute('UPDATE nachfrist SET revoked_at=CURRENT_TIMESTAMP WHERE user_id=? AND season_id=?',
                   (uid, season['id']))
        db.commit()
        flash(f'Nachfrist für „{name}" beendet (Historie bleibt für den Rückblick erhalten).', 'success')
    else:
        db.execute('''INSERT INTO nachfrist (user_id, season_id, expires_at, granted_by, strafgeld, notiz, revoked_at)
                      VALUES (?,?,?,?,?,?,NULL)
                      ON CONFLICT(user_id) DO UPDATE SET
                        season_id=excluded.season_id, expires_at=excluded.expires_at,
                        granted_at=CURRENT_TIMESTAMP, granted_by=excluded.granted_by,
                        strafgeld=excluded.strafgeld, notiz=excluded.notiz, revoked_at=NULL''',
                   (uid, season['id'], expires, session['user_id'], strafgeld, notiz))
        db.commit()

        expires_str = f' bis {expires}' if expires else ' (unbegrenzt)'
        flash(f'✅ Nachfrist für „{name}" gewährt{expires_str}.', 'success')

        # Telegram-Benachrichtigung
        try:
            import telegram_bot as _tg
            db2 = get_db()
            msg = (f'⏰ <b>Nachfrist</b>\n'
                   f'{name} hat eine Nachfrist erhalten{expires_str}.\n'
                   + (f'Strafgeld: {strafgeld}\n' if strafgeld else '')
                   + (f'Notiz: {notiz}' if notiz else ''))
            _tg.send_message(db2, msg)
        except Exception as e:
            app.logger.debug(f'Telegram Nachfrist: {e}')

    return redirect(url_for('admin_users'))


@app.route('/admin/users/<int:uid>/impersonate', methods=['POST'])
@admin_required
def admin_impersonate(uid):
    """Einloggen als User – Admin kann App aus Nutzerperspektive sehen."""
    user = get_db().execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
    if not user:
        flash('Benutzer nicht gefunden.', 'danger')
        return redirect(url_for('admin_users'))
    # Admin-Session merken
    session['impersonator_id']   = session['user_id']
    session['impersonator_name'] = session.get('username', 'Admin')
    # Als User einloggen (ohne Admin-Rechte)
    session['user_id']  = user['id']
    session['username'] = user['username']
    session['is_admin'] = False
    from markupsafe import Markup
    flash(Markup(f'👁️ Du siehst die App jetzt als „{user["display_name"] or user["username"]}". '
          f'<a href="/admin/stop-impersonate" class="alert-link">← Zurück zum Admin</a>'), 'info')
    return redirect(url_for('dashboard'))


@app.route('/admin/stop-impersonate')
@login_required
def admin_stop_impersonate():
    """Zurück zum eigenen Admin-Account."""
    imp_id   = session.pop('impersonator_id', None)
    imp_name = session.pop('impersonator_name', 'Admin')
    if not imp_id:
        flash('Kein Impersonation aktiv.', 'warning')
        return redirect(url_for('dashboard'))
    admin = get_db().execute('SELECT * FROM users WHERE id=?', (imp_id,)).fetchone()
    if not admin or not admin['is_admin'] or not admin['is_active']:
        # Admin-Rechte wurden zwischenzeitlich entzogen (oder Account deaktiviert) –
        # NICHT als Admin zurückschalten, sondern normal ausloggen.
        session.clear()
        flash('Admin-Rechte des Ursprungskontos sind nicht mehr gültig. Bitte neu anmelden.', 'danger')
        return redirect(url_for('login'))
    session['user_id']  = admin['id']
    session['username'] = admin['username']
    session['is_admin'] = True
    flash(f'✅ Zurück als Admin „{imp_name}".', 'success')
    return redirect(url_for('admin_users'))


@app.route('/admin/users/<int:uid>/delete', methods=['POST'])
@admin_required
def admin_delete_user(uid):
    if uid == session['user_id']:
        flash('Sich selbst kann man nicht löschen.', 'danger')
        return redirect(url_for('admin_users'))
    db = get_db()
    try:
        u  = db.execute('SELECT username FROM users WHERE id=?', (uid,)).fetchone()
        if u:
            # Foreign Keys kurz deaktivieren für atomares Löschen
            db.execute('PRAGMA foreign_keys = OFF')
            for table in ('predictions', 'scores', 'ranking_snapshots',
                          'matchday_highlights', 'email_reminders',
                          'login_attempts', 'push_subscriptions', 'user_badges'):
                try:
                    db.execute(f'DELETE FROM {table} WHERE user_id=?', (uid,))
                except Exception as e:
                    app.logger.debug(f"Ignorierter Fehler: {e}")
            # Legacy-Archiv: user_id auf NULL setzen
            try:
                db.execute('UPDATE legacy_results SET user_id=NULL WHERE user_id=?', (uid,))
                db.execute('DELETE FROM legacy_name_map WHERE user_id=?', (uid,))
            except Exception as e:
                app.logger.debug(f"Ignorierter Fehler: {e}")
            db.execute('DELETE FROM users WHERE id=?', (uid,))
            db.commit()
            db.execute('PRAGMA foreign_keys = ON')
            flash(f'Benutzer „{u["username"]}" wurde gelöscht.', 'success')
        else:
            flash('Benutzer nicht gefunden.', 'danger')
    except Exception:
        app.logger.exception('Fehler beim Löschen eines Benutzers')
        flash('Fehler beim Löschen des Benutzers. Details siehe Server-Log.', 'danger')
    return redirect(url_for('admin_users'))

@app.route('/admin/teams')
@admin_required
def admin_teams():
    db     = get_db()
    season = get_active_season()
    teams  = {lg: get_season_teams(season['id'], lg) if season else [] for lg in ('bl1','bl2')}
    # Vorschläge: alle Teams aus früheren Saisons (für Autovervollständigung)
    suggestions = {lg: [] for lg in ('bl1','bl2')}
    if season:
        for lg in ('bl1','bl2'):
            rows = db.execute(
                """SELECT DISTINCT t.name, t.short_name, t.openliga_id, t.logo_url
                   FROM teams t JOIN seasons s ON t.season_id=s.id
                   WHERE t.league=? AND t.season_id != ?
                   ORDER BY s.year DESC, t.name""",
                (lg, season['id'])
            ).fetchall()
            seen = set()
            for r in rows:
                if r['name'] not in seen:
                    suggestions[lg].append(dict(r))
                    seen.add(r['name'])
    return render_template('admin/teams.html', season=season, teams=teams,
                           leagues=LEAGUES, suggestions=suggestions,
                           api_provider=ol.get_active_provider())

@app.route('/admin/teams/manual_bulk', methods=['POST'])
@admin_required
def admin_manual_bulk_teams():
    """Mehrere Teams auf einmal manuell eintragen (Name:Kürzel, einer pro Zeile)."""
    season = get_active_season()
    if not season: flash('Keine aktive Saison.','danger'); return redirect(url_for('admin_teams'))
    league = request.form.get('league','bl1')
    raw    = request.form.get('bulk_teams','').strip()
    if not raw: flash('Keine Eingabe.','warning'); return redirect(url_for('admin_teams'))
    db = get_db()
    created, errors = 0, []
    for line in raw.splitlines():
        line = line.strip()
        if not line: continue
        parts = line.split(':', 1)
        name  = parts[0].strip()
        short = parts[1].strip() if len(parts) > 1 else name[:3]
        if not name: continue
        # Vorhandene openliga_id aus früheren Saisons übernehmen
        prev = db.execute(
            'SELECT openliga_id, logo_url FROM teams WHERE name=? AND league=? AND openliga_id > 0 ORDER BY season_id DESC LIMIT 1',
            (name, league)
        ).fetchone()
        oid      = prev['openliga_id'] if prev else None
        fd_id    = prev['fd_id']       if prev and 'fd_id' in prev.keys() else None
        logo_url = prev['logo_url']    if prev else ''
        try:
            db.execute(
                'INSERT INTO teams (name,short_name,openliga_id,fd_id,season_id,league,logo_url) VALUES (?,?,?,?,?,?,?)',
                (name, short, oid, fd_id, season['id'], league, logo_url)
            )
            created += 1
        except sqlite3.IntegrityError:
            errors.append(f'"{name}" bereits vorhanden')
    db.commit()
    if created: flash(f'{created} Teams ({LEAGUES[league]}) eingetragen.','success')
    for e in errors: flash(e,'warning')
    return redirect(url_for('admin_teams'))


# ── Predictions nach Team-Reimport reparieren ──────────────────────
def _repair_predictions_from_backup(db, season_id):
    """
    Nach Löschen+Reimport die Predictions aus dem Backup neu anlegen.
    Backup-Format: [{user_id, season_id, predicted_rank, team_name, team_short, league}]
    Gibt (repaired, unmatched) zurück.
    """
    backup_row = db.execute("SELECT data FROM api_cache WHERE key='pred_team_backup'").fetchone()
    if not backup_row:
        return 0, 0
    backup = json.loads(backup_row['data'])
    if not backup:
        return 0, 0

    stopwords = {
        'fc','sc','sv','vfl','vfb','rb','tsg','bsc','fsv','ssc',
        '04','05','06','07','08','09','1.','2.',
        '1899','1900','1902','1903','1905','1907','1909',
        'borussia','eintracht','sportclub','sport','club',
    }

    # Neue Teams der Saison indexieren
    new_teams = db.execute(
        'SELECT id, name, short_name, league FROM teams WHERE season_id=?',
        (season_id,)
    ).fetchall()
    name_idx  = {t['name'].lower(): t for t in new_teams}
    short_idx = {(t['short_name'] or '').upper(): t for t in new_teams}

    repaired = unmatched = 0
    for entry in backup:
        # Nur wenn diese Prediction noch nicht existiert
        exists = db.execute(
            'SELECT 1 FROM predictions WHERE user_id=? AND season_id=? AND '
            'team_id IN (SELECT id FROM teams WHERE name=? AND season_id=?)',
            (entry['user_id'], season_id, entry['team_name'], season_id)
        ).fetchone()
        if exists:
            repaired += 1
            continue

        # Team suchen: Name → Short → Keyword
        old_name  = entry['team_name']
        old_short = entry['team_short'].upper()
        league    = entry['league']

        match = name_idx.get(old_name.lower())
        if not match and old_short:
            match = short_idx.get(old_short) or short_idx.get(old_short[:3])
        if not match:
            td_words = set(old_name.lower().split()) - stopwords
            best = None; best_score = 0
            for t in new_teams:
                if t['league'] != league:
                    continue
                db_words = set(t['name'].lower().split()) - stopwords
                score = len(td_words & db_words)
                if score > best_score:
                    best_score = score; best = t
            if best_score > 0:
                match = best

        if match:
            db.execute(
                'INSERT OR IGNORE INTO predictions (user_id,season_id,team_id,predicted_rank) '
                'VALUES (?,?,?,?)',
                (entry['user_id'], season_id, match['id'], entry['predicted_rank'])
            )
            repaired += 1
        else:
            unmatched += 1

    if repaired or unmatched == 0:
        db.commit()
        if unmatched == 0:
            db.execute("DELETE FROM api_cache WHERE key='pred_team_backup'")
            db.commit()
    return repaired, unmatched


# ── Team-Upsert (Predictions-sichere Import-Funktion) ──────────────
def _upsert_teams(db, table, season_id, league):
    """
    Teams aus API-Tabelle in DB übernehmen — Predictions bleiben IMMER erhalten.
    Matching (Priorität):
      1. TLA (3-Buchstaben-Kürzel, z.B. BVB, BMG) — am zuverlässigsten
      2. fd_id / openliga_id — nach erstem Match gespeichert
      3. Bester Keyword-Score (maximale Wortüberschneidung, stopwords bereinigt)
    Neue Teams werden angelegt, vorhandene aktualisiert (Name, IDs, Logo).
    """
    # Wörter die in vielen Teamnamen vorkommen und beim Matching stören
    stopwords = {
        'fc','sc','sv','vfl','vfb','rb','tsg','bsc','fsv','ssc',
        '04','05','06','07','08','09','1.','2.',
        '1899','1900','1902','1903','1905','1907','1909',
        'borussia',   # Dortmund UND Mönchengladbach → kein Alleinmerkmal!
        'eintracht',  # Frankfurt UND Braunschweig
        'sportclub','sport','club',
    }

    # Vorhandene Teams aus DB mit mehreren Schlüsseln indexieren
    db_teams = db.execute(
        'SELECT * FROM teams WHERE season_id=? AND league=?',
        (season_id, league)
    ).fetchall()
    db_list = [dict(r) for r in db_teams]

    existing = {}  # key → team-dict
    for r in db_list:
        short = (r['short_name'] or '').strip()
        # Schlüssel 1: voller Short-Name (z.B. "BMG", "M'gladbach")
        if short:
            existing[short.upper()] = r
        # Schlüssel 2: erste 3 Zeichen (deckt "M'G" → "M'G", aber auch "BMG" → "BMG")
        if len(short) >= 3:
            existing[short[:3].upper()] = r
        # Schlüssel 3: fd_id
        if r.get('fd_id'):
            existing[f'fd:{r["fd_id"]}'] = r
        # Schlüssel 4: openliga_id
        if r.get('openliga_id'):
            existing[f'ol:{r["openliga_id"]}'] = r

    updated = inserted = 0
    for td in table:
        tla    = (td.get('TLA') or td.get('ShortName', ''))[:3].upper()
        fd_id  = td['TeamInfoId'] if td.get('_source') == 'football-data.org' else None
        ol_id  = td['TeamInfoId'] if td.get('_source') != 'football-data.org'  else None
        logo   = td.get('TeamIconUrl', '') or ''
        name   = td['TeamName']
        short  = (td.get('ShortName', '') or tla)[:20]

        # 1. TLA-Match
        match = existing.get(tla)

        # 2. ID-Match (fd_id oder openliga_id)
        if not match and fd_id:
            match = existing.get(f'fd:{fd_id}')
        if not match and ol_id:
            match = existing.get(f'ol:{ol_id}')

        # 3. Bester Keyword-Score (kein first-match, sondern höchste Überschneidung)
        if not match:
            td_words = set(name.lower().split()) - stopwords
            if td_words:
                best = None
                best_score = 0
                for ex in db_list:
                    db_words = set(ex['name'].lower().split()) - stopwords
                    score = len(td_words & db_words)
                    if score > best_score:
                        best_score = score
                        best = ex
                if best_score > 0:
                    match = best

        if match:
            db.execute(
                'UPDATE teams SET name=?, short_name=?,'
                ' openliga_id=COALESCE(?,openliga_id),'
                ' fd_id=COALESCE(?,fd_id),'
                ' logo_url=CASE WHEN ?!=\'\' THEN ? ELSE logo_url END'
                ' WHERE id=?',
                (name, short, ol_id, fd_id, logo, logo, match['id'])
            )
            updated += 1
        else:
            db.execute(
                'INSERT INTO teams (name,short_name,openliga_id,fd_id,season_id,league,logo_url)'
                ' VALUES (?,?,?,?,?,?,?)',
                (name, short, ol_id, fd_id, season_id, league, logo)
            )
            inserted += 1
    return updated, inserted

@app.route('/admin/teams/import_both', methods=['POST'])
@admin_required
def admin_import_both():
    import requests as _req
    season = get_active_season()
    if not season: flash('Keine aktive Saison.','danger'); return redirect(url_for('admin_teams'))
    db = get_db(); msgs = []
    for league in ('bl1','bl2'):
        try:
            table = ol.get_table(league, season['year'])
            upd, ins = _upsert_teams(db, table, season['id'], league)
            msgs.append(f'{LEAGUES[league]}: {upd} aktualisiert, {ins} neu')
        except _req.exceptions.ConnectTimeout:
            flash(f'{LEAGUES[league]}: Timeout – API nicht erreichbar.','danger')
        except _req.exceptions.ConnectionError:
            flash(f'{LEAGUES[league]}: Keine Verbindung zur API möglich.','danger')
        except Exception as e:
            flash(f'Fehler {LEAGUES[league]}: {e}','danger')
    if msgs:
        db.commit()
        flash(' | '.join(msgs), 'success')
    # Backup-Reparatur immer versuchen
    repaired, unmatched = _repair_predictions_from_backup(db, season['id'])
    if repaired:
        flash(f'✅ {repaired} Tipp-Verknüpfungen wiederhergestellt.', 'success')
    return redirect(url_for('admin_teams'))

@app.route('/admin/teams/import', methods=['POST'])
@admin_required
def admin_import_teams():
    import requests as _req
    season = get_active_season(); league = request.form.get('league','bl1')
    if not season: flash('Keine aktive Saison.','danger'); return redirect(url_for('admin_teams'))
    db = get_db()
    try:
        table = ol.get_table(league, season['year'])
        upd, ins = _upsert_teams(db, table, season['id'], league)
        db.commit()
        msg = f'{LEAGUES[league]}: {upd} Teams aktualisiert, {ins} neu hinzugefügt.'
        flash(msg, 'success')
    except _req.exceptions.ConnectTimeout:
        flash('Timeout – API nicht erreichbar. Bitte in ein paar Minuten erneut versuchen.','danger')
    except _req.exceptions.ConnectionError:
        flash('Keine Verbindung zur API möglich.','danger')
    except Exception as e:
        flash(f'Fehler: {e}','danger')
    # Backup-Reparatur immer versuchen (unabhängig vom API-Ergebnis)
    repaired, unmatched = _repair_predictions_from_backup(db, season['id'])
    if repaired:
        flash(f'✅ {repaired} Tipp-Verknüpfungen wiederhergestellt.', 'success')
    return redirect(url_for('admin_teams'))

@app.route('/admin/teams/add', methods=['POST'])
@admin_required
def admin_add_team():
    season = get_active_season(); name = request.form.get('name','').strip()
    league = request.form.get('league','bl1')
    if not name: flash('Teamname erforderlich.','danger')
    else:
        db = get_db()
        db.execute('INSERT INTO teams (name,short_name,season_id,league) VALUES (?,?,?,?)',
            (name, request.form.get('short_name','').strip(), season['id'], league)); db.commit()
        flash(f'"{name}" ({LEAGUES[league]}) hinzugefügt.','success')
    return redirect(url_for('admin_teams'))

@app.route('/admin/teams/<int:tid>/delete', methods=['POST'])
@admin_required
def admin_delete_team(tid):
    db = get_db()
    t = db.execute(
        'SELECT t.*, s.id as sid FROM teams t JOIN seasons s ON t.season_id=s.id WHERE t.id=?',
        (tid,)
    ).fetchone()
    if not t:
        return redirect(url_for('admin_teams'))

    # Predictions dieses Teams ins Backup mergen (damit Reload sie wiederherstellt)
    existing_backup = db.execute(
        "SELECT data FROM api_cache WHERE key='pred_team_backup'"
    ).fetchone()
    backup = json.loads(existing_backup['data']) if existing_backup else []
    if isinstance(backup, dict):  # altes Format kompatibel
        backup = list(backup.values())

    new_entries = []
    for pred in db.execute(
        'SELECT p.user_id, p.season_id, p.predicted_rank FROM predictions p WHERE p.team_id=?',
        (tid,)
    ).fetchall():
        new_entries.append({
            'user_id':       pred['user_id'],
            'season_id':     pred['season_id'],
            'predicted_rank': pred['predicted_rank'],
            'team_name':     t['name'],
            'team_short':    t['short_name'] or '',
            'league':        t['league'],
        })

    if new_entries:
        backup.extend(new_entries)
        db.execute(
            "INSERT OR REPLACE INTO api_cache (key,last_ts,data) VALUES ('pred_team_backup',datetime('now'),?)",
            (json.dumps(backup),)
        )

    # Team sicher löschen: erst Predictions (gesichert), dann standings, dann team
    db.execute('DELETE FROM predictions WHERE team_id=?', (tid,))
    db.execute('DELETE FROM standings WHERE team_id=?', (tid,))
    db.execute('DELETE FROM teams WHERE id=?', (tid,))
    db.commit()
    flash(
        f'"{t["name"]}" gelöscht. '
        + (f'{len(new_entries)} Tipp-Verknüpfungen gesichert — nach dem Neu-Laden automatisch wiederhergestellt.' if new_entries else ''),
        'success'
    )
    return redirect(url_for('admin_teams'))

@app.route('/admin/teams/repair', methods=['POST'])
@admin_required
def admin_repair_tips():
    """Stellt Tipp-Verknüpfungen aus dem Backup wieder her (nach Team-Änderungen)."""
    season = get_active_season()
    if not season:
        flash('Keine aktive Saison.', 'danger')
        return redirect(url_for('admin_teams'))
    db = get_db()
    repaired, unmatched = _repair_predictions_from_backup(db, season['id'])
    if repaired:
        flash(f'✅ {repaired} Tipp-Verknüpfungen wiederhergestellt'
              + (f', {unmatched} konnten nicht zugeordnet werden.' if unmatched else '.'), 'success')
    elif unmatched:
        flash(f'⚠️ {unmatched} Tipps konnten keinem Team zugeordnet werden.', 'warning')
    else:
        flash('Kein Backup vorhanden — keine Reparatur nötig.', 'info')
    return redirect(url_for('admin_teams'))


@admin_required
def admin_clear_teams():
    """
    Löscht Teams und Standings.
    Predictions werden NICHT gelöscht — ihre team_id-Verweise werden als
    Name-Backup in api_cache gespeichert und beim nächsten Import automatisch
    auf die neuen Team-IDs umgeschrieben.
    """
    season = get_active_season(); league = request.form.get('league')
    if season:
        db = get_db()
        q  = 'SELECT id FROM teams WHERE season_id=?' + (' AND league=?' if league else '')
        p  = [season['id']] + ([league] if league else [])
        team_ids = [t['id'] for t in db.execute(q, p).fetchall()]
        if team_ids:
            # Vor dem Löschen: vollständige Prediction-Daten sichern
            backup = []
            for pred in db.execute(
                'SELECT p.user_id, p.season_id, p.predicted_rank, '
                't.name as team_name, t.short_name, t.league '
                'FROM predictions p JOIN teams t ON p.team_id=t.id '
                'WHERE t.season_id=? AND p.team_id IN ({})'.format(
                    ','.join(str(x) for x in team_ids)),
                (season['id'],)
            ).fetchall():
                backup.append({
                    'user_id':      pred['user_id'],
                    'season_id':    pred['season_id'],
                    'predicted_rank': pred['predicted_rank'],
                    'team_name':    pred['team_name'],
                    'team_short':   pred['short_name'] or '',
                    'league':       pred['league'],
                })
            # Backup speichern
            db.execute(
                "INSERT OR REPLACE INTO api_cache (key,last_ts,data) "
                "VALUES ('pred_team_backup',datetime('now'),?)",
                (json.dumps(backup),)
            )
            # Predictions, Standings, Teams, Scores löschen
            # Reihenfolge: erst Kind-Tabellen (predictions, standings), dann Eltern (teams)
            placeholders = ','.join(str(x) for x in team_ids)
            db.execute(
                f'DELETE FROM predictions WHERE season_id=? AND team_id IN ({placeholders})',
                (season['id'],)
            )
            db.execute(
                f'DELETE FROM standings WHERE team_id IN ({placeholders})'
            )
            db.execute(
                f'DELETE FROM teams WHERE id IN ({placeholders})'
            )
            db.execute('DELETE FROM scores WHERE season_id=?', (season['id'],))
            db.commit()
            n_preds = len(backup)
            flash(
                f'Teams ({LEAGUES.get(league,"alle Ligen")}) gelöscht. '
                f'{n_preds} Tipp-Verknüpfungen gesichert — '
                f'werden nach dem nächsten Team-Import automatisch wiederhergestellt.',
                'warning'
            )
        else:
            flash('Keine Teams zum Löschen gefunden.', 'info')
    return redirect(url_for('admin_teams'))

@app.route('/admin/telegram/reset-dedup', methods=['POST'])
@admin_required
def admin_telegram_reset_dedup():
    """Setzt die Deduplizierungs-Spieltage für Telegram-Benachrichtigungen zurück."""
    db  = get_db()
    key = request.form.get('key', 'both')
    if key in ('rangliste', 'both'):
        db.execute("DELETE FROM config WHERE key='tg_last_rangliste_md'")
    if key in ('highlight', 'both'):
        db.execute("DELETE FROM config WHERE key='tg_last_highlight_md'")
    db.commit()
    labels = {'rangliste': 'Rangliste', 'highlight': 'Highlight', 'both': 'Rangliste + Highlight'}
    flash(f'✅ Telegram-Deduplizierung zurückgesetzt: {labels.get(key, key)}. '
          f'Beim nächsten Seitenaufruf wird die Benachrichtigung erneut gesendet.', 'success')
    return redirect(url_for('admin_diagnose'))


@app.route('/admin/diagnose')
@admin_required
def admin_diagnose():
    db     = get_db()
    season = get_active_season()
    if not season:
        return '<p>Keine aktive Saison.</p>', 200, {'Content-Type': 'text/html'}
    sid = season['id']
    d = {
        'teams_bl1':        db.execute("SELECT COUNT(*) FROM teams WHERE season_id=? AND league='bl1'",(sid,)).fetchone()[0],
        'teams_bl2':        db.execute("SELECT COUNT(*) FROM teams WHERE season_id=? AND league='bl2'",(sid,)).fetchone()[0],
        'teams_fd_id':      db.execute("SELECT COUNT(*) FROM teams WHERE season_id=? AND fd_id IS NOT NULL AND fd_id>0",(sid,)).fetchone()[0],
        'predictions':      db.execute("SELECT COUNT(*) FROM predictions WHERE season_id=?",(sid,)).fetchone()[0],
        'predictions_ok':   db.execute("SELECT COUNT(*) FROM predictions p JOIN teams t ON p.team_id=t.id WHERE p.season_id=?",(sid,)).fetchone()[0],
        'predictions_bad':  db.execute("SELECT COUNT(*) FROM predictions p LEFT JOIN teams t ON p.team_id=t.id WHERE p.season_id=? AND t.id IS NULL",(sid,)).fetchone()[0],
        'users_with_tips':  db.execute("SELECT COUNT(DISTINCT user_id) FROM predictions WHERE season_id=?",(sid,)).fetchone()[0],
        'standings':        db.execute("SELECT COUNT(*) FROM standings s JOIN teams t ON s.team_id=t.id WHERE s.season_id=?",(sid,)).fetchone()[0],
        'scores':           db.execute("SELECT COUNT(*) FROM scores WHERE season_id=?",(sid,)).fetchone()[0],
    }

    # ── Telegram-Status ──────────────────────────────────────
    _tg_tok_row = db.execute("SELECT value FROM config WHERE key='telegram_token'").fetchone()
    _tg_cht_row = db.execute("SELECT value FROM config WHERE key='telegram_chat_id'").fetchone()
    tg_token   = (_tg_tok_row['value'] or '').strip() if _tg_tok_row else ''
    tg_chat    = (_tg_cht_row['value'] or '').strip() if _tg_cht_row else ''
    tg_hl_en   = tg.is_enabled(db, 'notify_highlight')
    tg_rl_en   = tg.is_enabled(db, 'notify_rangliste')
    _hl_row    = db.execute("SELECT value FROM config WHERE key='tg_last_highlight_md'").fetchone()
    _rl_row    = db.execute("SELECT value FROM config WHERE key='tg_last_rangliste_md'").fetchone()
    tg_hl_last = int(_hl_row['value']) if _hl_row and (_hl_row['value'] or '').isdigit() else '–'
    tg_rl_last = int(_rl_row['value']) if _rl_row and (_rl_row['value'] or '').isdigit() else '–'
    # Aktueller letzter abgeschlossener Spieltag (live)
    try:
        cur_md = ol.get_last_finished_matchday('bl1', season['year']) or '?'
    except Exception:
        cur_md = '?'
    # is_matchday_complete live prüfen
    try:
        md_complete = tg.is_matchday_complete(db, season)
        md_complete_str = '✅ Ja' if md_complete else '⚠️ Nein – nicht alle Spiele beendet'
    except Exception as e:
        md_complete_str = f'⚠️ Fehler: {e}'
    # Log-Einträge
    tg_log = tg.get_log(db)

    status_icon = {
        'sent':    '✅',
        'skipped': '⏭️',
        'blocked': '🚫',
        'error':   '❌',
    }
    type_label = {'highlight': 'Spieltag-Highlight', 'rangliste': 'Rangliste'}

    def tg_row(label, val, icon=''):
        return f'<tr><td>{label}</td><td>{val}</td><td>{icon}</td></tr>'

    tg_html = f'''
<tr><td colspan=3><strong>Telegram-Benachrichtigungen</strong></td></tr>
{tg_row('Token konfiguriert', 'Ja' if tg_token else 'Nein', '✅' if tg_token else '❌')}
{tg_row('Chat-ID konfiguriert', 'Ja' if tg_chat else 'Nein', '✅' if tg_chat else '❌')}
{tg_row('Ranglisten-Benachrichtigung', 'aktiv' if tg_rl_en else 'deaktiviert', '✅' if tg_rl_en else '⏸️')}
{tg_row('Highlight-Benachrichtigung', 'aktiv' if tg_hl_en else 'deaktiviert', '✅' if tg_hl_en else '⏸️')}
{tg_row('Letzter abgeschl. Spieltag (live)', str(cur_md), 'ℹ️')}
{tg_row('Spieltag vollständig? (is_matchday_complete)', md_complete_str, '')}
{tg_row('Zuletzt gesendeter Spieltag – Highlight', str(tg_hl_last), 'ℹ️')}
{tg_row('Zuletzt gesendeter Spieltag – Rangliste', str(tg_rl_last), 'ℹ️')}
'''

    if tg_log:
        log_rows = ''.join(
            f'<tr><td>{e["ts"]}</td><td>{type_label.get(e["type"], e["type"])}</td>'
            f'<td>Spieltag {e["matchday"]}</td>'
            f'<td>{status_icon.get(e["status"], e["status"])} {e["status"]}</td>'
            f'<td style="color:#555;font-size:.8rem">{e.get("detail","")}</td></tr>'
            for e in tg_log
        )
        tg_log_html = f'''
<h3 style="color:#1a5e2a;margin-top:2rem">📋 Benachrichtigungs-Log (neueste zuerst)</h3>
<table>
<tr><th>Zeitpunkt</th><th>Typ</th><th>Spieltag</th><th>Status</th><th>Detail</th></tr>
{log_rows}
</table>'''
    else:
        tg_log_html = '<p style="color:#888;margin-top:1rem">Noch keine Log-Einträge vorhanden.</p>'

    html = f'''<!doctype html><html><head><meta charset="utf-8">
<title>Diagnose – {season["name"]}</title>
<style>body{{font-family:sans-serif;max-width:800px;margin:2rem auto;padding:1rem}}
table{{width:100%;border-collapse:collapse;margin-bottom:1rem}}
td,th{{padding:.5rem .75rem;border:1px solid #ddd;text-align:left;vertical-align:top}}
th{{background:#1a5e2a;color:#fff}}
tr:nth-child(even){{background:#f9f9f9}}
h2,h3{{color:#1a5e2a}}.ok{{color:green}}.warn{{color:orange}}</style></head><body>
<h2>⚽ Tippcup Diagnose – {season["name"]}</h2>
<table>
<tr><th>Bereich</th><th>Wert</th><th>Status</th></tr>
<tr><td>Saison</td><td>{season["name"]}</td><td>✅</td></tr>
<tr><td colspan=3><strong>Teams</strong></td></tr>
<tr><td>&nbsp;&nbsp;1. Bundesliga</td><td>{d["teams_bl1"]}</td><td>{"✅" if d["teams_bl1"]==18 else "⚠️"}</td></tr>
<tr><td>&nbsp;&nbsp;2. Bundesliga</td><td>{d["teams_bl2"]}</td><td>{"✅" if d["teams_bl2"]==18 else "⚠️"}</td></tr>
<tr><td>&nbsp;&nbsp;Mit football-data.org ID (BL1)</td><td>{d["teams_fd_id"]}</td><td>{"✅" if d["teams_fd_id"]==18 else "ℹ️ Nach erstem Tabellen-Abruf vollständig"}</td></tr>
<tr><td colspan=3><strong>Tipps</strong></td></tr>
<tr><td>&nbsp;&nbsp;Gesamt</td><td>{d["predictions"]}</td><td>{"✅" if d["predictions"]>0 else "⚠️ Keine Tipps"}</td></tr>
<tr><td>&nbsp;&nbsp;Korrekt verknüpft</td><td>{d["predictions_ok"]}</td><td>{"✅" if d["predictions_ok"]==d["predictions"] else "⚠️"}</td></tr>
<tr><td>&nbsp;&nbsp;Defekt</td><td>{d["predictions_bad"]}</td><td>{"✅" if d["predictions_bad"]==0 else "⚠️ Reparatur nötig"}</td></tr>
<tr><td>&nbsp;&nbsp;Spieler mit Tipps</td><td>{d["users_with_tips"]}</td><td>{"✅" if d["users_with_tips"]>0 else "⚠️"}</td></tr>
<tr><td colspan=3><strong>Auswertung</strong></td></tr>
<tr><td>&nbsp;&nbsp;Tabellenplätze (Standings)</td><td>{d["standings"]}</td><td>{"✅" if d["standings"]==36 else "⚠️ Tabellen abrufen!"}</td></tr>
<tr><td>&nbsp;&nbsp;Berechnete Scores</td><td>{d["scores"]}</td><td>{"✅" if d["scores"]>0 else "⚠️ Punkte berechnen!"}</td></tr>
{tg_html}
<tr><td colspan=3 style="padding-top:.5rem">
  <form method="POST" action="/admin/telegram/reset-dedup" style="display:inline-flex;gap:.5rem;flex-wrap:wrap">
    <input type="hidden" name="csrf_token" value="{get_csrf_token()}">
    <input type="hidden" name="key" value="rangliste">
    <button style="padding:.3rem .7rem;background:#0d6efd;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:.8rem">
      🔄 Rangliste-Deduplizierung zurücksetzen
    </button>
  </form>
  <form method="POST" action="/admin/telegram/reset-dedup" style="display:inline-flex;gap:.5rem;flex-wrap:wrap;margin-top:.25rem">
    <input type="hidden" name="csrf_token" value="{get_csrf_token()}">
    <input type="hidden" name="key" value="highlight">
    <button style="padding:.3rem .7rem;background:#6c757d;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:.8rem">
      🔄 Highlight-Deduplizierung zurücksetzen
    </button>
  </form>
  <form method="POST" action="/admin/telegram/reset-dedup" style="display:inline-flex;gap:.5rem;flex-wrap:wrap;margin-top:.25rem">
    <input type="hidden" name="csrf_token" value="{get_csrf_token()}">
    <input type="hidden" name="key" value="both">
    <button style="padding:.3rem .7rem;background:#dc3545;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:.8rem">
      🔄 Beide zurücksetzen
    </button>
  </form>
</td></tr>
</table>
{tg_log_html}
<p style="margin-top:1.5rem;color:#666;font-size:.85rem">
<a href="/admin/season">← Admin Saison</a>
</p></body></html>'''
    return html, 200, {'Content-Type': 'text/html; charset=utf-8'}



    db = get_db()

    # Alle aktuellen Teams der Saison (Name → id)
    current_teams = {}
    for t in db.execute('SELECT id, name, short_name, league FROM teams WHERE season_id=?',
                        (season['id'],)).fetchall():
        current_teams[(t['name'].lower().strip(), t['league'])] = t['id']
        if t['short_name']:
            current_teams[(t['short_name'].lower().strip(), t['league'])] = t['id']


    # Backup aus api_cache laden (gespeichert beim Team-Löschen)
    backup_row = db.execute("SELECT data FROM api_cache WHERE key='pred_team_backup'").fetchone()
    backup = json.loads(backup_row['data']) if backup_row else {}

    # Alle Predictions mit ungültiger team_id finden
    all_preds = db.execute(
        '''SELECT p.id, p.user_id, p.team_id, p.predicted_rank,
                  t.name as tname, t.league as tleague
           FROM predictions p
           LEFT JOIN teams t ON p.team_id = t.id AND t.season_id = ?
           WHERE p.season_id=?''',
        (season['id'], season['id'])
    ).fetchall()

    repaired = 0; unmatched_list = []

    for pred in all_preds:
        if pred['tname']:
            continue  # Noch gültig verknüpft

        # Name aus Backup holen
        pred_backup = backup.get(str(pred['id']))
        if not pred_backup:
            unmatched_list.append(pred['id'])
            continue

        old_name   = pred_backup['name']
        league     = pred_backup['league']

        # Exakter Name-Match
        new_tid = current_teams.get((old_name.lower().strip(), league))
        if not new_tid:
            # Fuzzy-Match
            for (name_key, lg), tid in current_teams.items():
                if lg == league and (name_key in old_name.lower() or
                                     old_name.lower() in name_key):
                    new_tid = tid
                    break

        if new_tid:
            db.execute('UPDATE predictions SET team_id=? WHERE id=?', (new_tid, pred['id']))
            repaired += 1
        else:
            unmatched_list.append(pred['id'])

    unmatched = len(unmatched_list)

    # Backup löschen nach erfolgreicher Reparatur
    if repaired > 0 and unmatched == 0:
        db.execute("DELETE FROM api_cache WHERE key='pred_team_backup'")

    db.commit()

    if repaired > 0:
        # Scores neu berechnen
        try:
            calculate_scores_for_season(season['id'])
        except Exception as e:
            app.logger.debug(f"Ignorierter Fehler: {e}")
        flash(f'✅ {repaired} Tipp-Verknüpfungen wiederhergestellt'
              + (f', {unmatched} nicht gefunden.' if unmatched else '.'),
              'success')
    else:
        flash(f'Keine defekten Verknüpfungen gefunden (unmatched: {unmatched}).', 'info')

    return redirect(url_for('admin_teams'))

@app.route('/admin/season', methods=['GET','POST'])
@admin_required
def admin_season():
    db = get_db()
    if request.method == 'POST':
        action = request.form.get('action'); season = get_active_season()
        if action == 'create':
            year = request.form.get('year','').strip()
            if not year or not year.isdigit(): flash('Ungültiges Jahr.','danger')
            else:
                db.execute('UPDATE seasons SET is_active=0')
                db.execute('INSERT INTO seasons (year,name,is_active) VALUES (?,?,1)',
                    (int(year), request.form.get('name','').strip() or f'Bundesliga {year}/{int(year)+1}'))
                db.commit(); flash('Neue Saison erstellt.','success')
        elif action == 'toggle_lock' and season:
            new = 0 if season['tips_locked'] else 1
            if new == 0:
                # Öffnen: alle Liga-Sperren zurücksetzen
                db.execute(
                    'UPDATE seasons SET tips_locked=0, locked_bl1=0, locked_bl2=0 WHERE id=?',
                    (season['id'],)
                )
            else:
                db.execute('UPDATE seasons SET tips_locked=1 WHERE id=?', (season['id'],))
            db.commit()
            flash('Tippabgabe ' + ('gesperrt.' if new else 'geöffnet.'), 'info')
        elif action == 'toggle_started' and season:
            new = 0 if season['season_started'] else 1
            db.execute('UPDATE seasons SET season_started=? WHERE id=?',(new,season['id']))
            if new: db.execute('UPDATE seasons SET tips_locked=1 WHERE id=?',(season['id'],))
            db.commit(); flash('Status geändert.','info')
        elif action == 'update_standings' and season:
            ok, msg = update_standings_from_api(season, force=True)
            flash('Tabellen aktualisiert ✓' if ok else f'Fehler: {msg}', 'success' if ok else 'danger')
        elif action == 'fetch_deadlines' and season:
            import requests as _req
            try:
                result = fetch_deadlines(season)
                if result:
                    msgs = [f'{LEAGUES[lg]}: {dt.strftime("%d.%m.%Y %H:%M")} Uhr' for lg, dt in result.items()]
                    flash('Tippschluss ermittelt: ' + ' · '.join(msgs), 'success')
                else:
                    flash('Keine Spieltermine gefunden – OpenligaDB nicht erreichbar.', 'warning')
            except Exception as e:
                flash(f'Fehler: {e}', 'danger')
        elif action == 'set_deadline' and season:
            for lg in ('bl1','bl2'):
                val = request.form.get(f'deadline_{lg}','').strip()
                if val:
                    try:
                        dt = datetime.fromisoformat(val)
                        db.execute(f'UPDATE seasons SET deadline_{lg}=? WHERE id=?', (dt.isoformat(), season['id']))
                    except Exception:
                        flash(f'Ungültiges Datum für {LEAGUES[lg]}.', 'danger')
            db.commit()
            flash('Tippschluss manuell gesetzt.', 'success')
        elif action == 'recalculate' and season:
            calculate_scores_for_season(season['id']); flash('Punktestände neu berechnet.','success')
        elif action == 'reset_snapshots' and season:
            # Alle Verlaufs-Snapshots löschen und aktuellen Stand neu eintragen
            db.execute('DELETE FROM ranking_snapshots WHERE season_id=?', (season['id'],))
            db.commit()
            _save_ranking_snapshot(season['id'])
            flash('Verlaufs-Snapshots zurückgesetzt. Ab jetzt wird jeder abgeschlossene Spieltag einmalig erfasst.', 'success')
        elif action == 'rebuild_history' and season:
            try:
                ok, skipped, errs = rebuild_snapshots_from_history(season['id'])
                msg = f'Verlauf rekonstruiert: {ok} Spieltage neu, {skipped} bereits vorhanden.'
                if errs:
                    msg += f' Fehler: {"; ".join(errs[:5])}'
                    flash(msg, 'warning')
                else:
                    flash(msg, 'success')
            except Exception as e:
                flash(f'Fehler bei Rekonstruktion: {e}', 'danger')
        elif action == 'activate':
            sid = request.form.get('season_id')
            if sid:
                db.execute('UPDATE seasons SET is_active=0')
                db.execute('UPDATE seasons SET is_active=1 WHERE id=?',(sid,)); db.commit()
                flash('Saison aktiviert.','success')
        elif action == 'delete_season':
            sid = request.form.get('season_id')
            if sid:
                s = db.execute('SELECT * FROM seasons WHERE id=?', (sid,)).fetchone()
                if not s:
                    flash('Saison nicht gefunden.', 'danger')
                elif s['is_active']:
                    flash('Aktive Saison kann nicht gelöscht werden. Zuerst eine andere Saison aktivieren.', 'danger')
                else:
                    sid_int = int(sid)
                    db.execute('PRAGMA foreign_keys = OFF')
                    for tbl in ('predictions', 'scores', 'ranking_snapshots',
                                'matchday_highlights', 'email_reminders', 'user_badges',
                                'standings', 'teams'):
                        try: db.execute(f'DELETE FROM {tbl} WHERE season_id=?', (sid_int,))
                        except Exception as e: app.logger.debug(f'Ignoriert: {e}')
                    db.execute('DELETE FROM seasons WHERE id=?', (sid_int,))
                    db.commit()
                    db.execute('PRAGMA foreign_keys = ON')
                    flash(f'Saison „{s["name"]}" wurde vollständig gelöscht.', 'success')
        return redirect(url_for('admin_season'))
    season = get_active_season()
    all_seasons = db.execute('SELECT * FROM seasons ORDER BY year DESC').fetchall()
    return render_template('admin/season.html', season=season, all_seasons=all_seasons,
                           api_provider=ol.get_active_provider())



@app.route('/admin/season/<int:sid>/export-csv')
@admin_required
def admin_season_export_csv(sid):
    """Exportiert eine Saison als CSV im legacy_results-Format."""
    import csv, io, math
    try:
        db = get_db()
        s = db.execute('SELECT * FROM seasons WHERE id=?', (sid,)).fetchone()
        if not s:
            flash('Saison nicht gefunden.', 'danger')
            return redirect(url_for('admin_season'))

        year = s['year']
        season_label = f'{year}/{year+1}'

        scores = db.execute(
            """SELECT sc.*, u.display_name, u.username
               FROM scores sc JOIN users u ON sc.user_id = u.id
               WHERE sc.season_id = ? AND u.is_active = 1
               ORDER BY sc.score DESC, sc.std_deviation ASC, sc.volltreffer DESC, sc.user_id ASC""",
            (sid,)
        ).fetchall()

        sp1 = db.execute(
            """SELECT MAX(st.matches_played) FROM standings st
               JOIN teams t ON st.team_id=t.id
               WHERE st.season_id=? AND t.league='bl1'""", (sid,)
        ).fetchone()[0] or 34
        sp2 = db.execute(
            """SELECT MAX(st.matches_played) FROM standings st
               JOIN teams t ON st.team_id=t.id
               WHERE st.season_id=? AND t.league='bl2'""", (sid,)
        ).fetchone()[0] or 34

        def per_league_stats(user_id, league):
            rows = db.execute(
                """SELECT ABS(p.predicted_rank - st.current_rank) AS dev
                   FROM predictions p
                   JOIN teams t   ON p.team_id = t.id
                   JOIN standings st ON st.team_id = t.id AND st.season_id = p.season_id
                   WHERE p.season_id=? AND p.user_id=? AND t.league=?""",
                (sid, user_id, league)
            ).fetchall()
            if not rows:
                return 0, 0.0, 0, 0
            devs    = [r['dev'] for r in rows]
            n       = len(devs)
            abw     = sum(devs)
            max_abw = max(devs)
            treffer = sum(1 for d in devs if d == 0)
            stdabw  = round(math.sqrt(sum(d*d for d in devs) / n), 3) if n else 0.0
            return abw, stdabw, max_abw, treffer

        buf = io.StringIO()
        writer = csv.writer(buf, delimiter=';')
        writer.writerow([
            'Saison','Spieltag_1L','Spieltag_2L','Rang','Tipper',
            'Abw_1L','Stdabw_1L','MaxAbw_1L','Treffer_1L',
            'Abw_2L','Stdabw_2L','MaxAbw_2L','Treffer_2L',
            'Abw_Ges','Stdabw_Ges','MaxAbw_Ges','Treffer_Ges'
        ])
        for rang, sc in enumerate(scores, 1):
            uid  = sc['user_id']
            name = sc['display_name'] or sc['username']
            abw1, std1, max1, tr1 = per_league_stats(uid, 'bl1')
            abw2, std2, max2, tr2 = per_league_stats(uid, 'bl2')
            abw_ges = sc['total_deviation']
            std_ges = round(sc['std_deviation'] or 0, 3)
            max_ges = sc['max_deviation'] or 0
            tr_ges  = sc['volltreffer'] or 0
            writer.writerow([
                season_label, sp1, sp2, rang, name,
                abw1, str(std1).replace('.', ','), max1, tr1,
                abw2, str(std2).replace('.', ','), max2, tr2,
                abw_ges, str(std_ges).replace('.', ','), max_ges, tr_ges
            ])

        filename = f'tippcup_{season_label.replace("/", "-")}_abschluss.csv'
        resp = make_response(buf.getvalue())
        resp.headers['Content-Type'] = 'text/csv; charset=utf-8'
        resp.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
        return resp

    except Exception:
        app.logger.exception('CSV-Export Fehler')
        flash('CSV-Export fehlgeschlagen. Details siehe Server-Log.', 'danger')
        return redirect(url_for('admin_season'))


# ════════════════════════════════════════════════════════════
# ROUTEN – STATISTIKEN & ANALYSE
# ════════════════════════════════════════════════════════════
# ── Ewige Tabelle ─────────────────────────────

@app.route('/admin/rebuild_history_stream')
@admin_required
def rebuild_history_stream():
    """
    Server-Sent Events – streamt Rebuild-Fortschritt live.
    Lädt alle Saisonspiele einmalig, berechnet Tabellen lokal.
    Eigene SQLite-Verbindung da Flask-g im Generator nicht verfügbar.
    """
    import time, sqlite3 as _sqlite3
    from datetime import datetime as _dt

    season = get_active_season()
    if not season:
        def _err():
            yield "data: ERROR:Keine aktive Saison\n\n"
        return app.response_class(_err(), mimetype='text/event-stream')

    season_id = season['id']
    year      = season['year']

    def generate():
        try:
            db = _sqlite3.connect(DATABASE)
            db.row_factory = _sqlite3.Row
        except Exception as e:
            yield f"data: ERROR:DB-Fehler: {e}\n\n"
            return

        try:
            existing = {r['matchday'] for r in db.execute(
                'SELECT DISTINCT matchday FROM ranking_snapshots WHERE season_id=? AND matchday IS NOT NULL',
                (season_id,)
            ).fetchall()}

            users = db.execute(
                """SELECT DISTINCT u.id, u.display_name, u.username
                   FROM predictions p JOIN users u ON p.user_id=u.id
                   WHERE p.season_id=? AND u.is_active=1""",
                (season_id,)
            ).fetchall()
            if not users:
                yield "data: ERROR:Keine Tipps gefunden\n\n"
                return

            preds_raw = db.execute(
                'SELECT user_id, team_id, predicted_rank FROM predictions WHERE season_id=?',
                (season_id,)
            ).fetchall()
            preds_by_user = {}
            for p in preds_raw:
                preds_by_user.setdefault(p['user_id'], {})[p['team_id']] = p['predicted_rank']

            teams_by_olid = {}
            teams_by_name = {}
            for t in db.execute(
                'SELECT id, openliga_id, name, short_name, league FROM teams WHERE season_id=?',
                (season_id,)
            ).fetchall():
                if t['openliga_id']:
                    teams_by_olid[int(t['openliga_id'])] = {'team_id': t['id'], 'league': t['league']}
                teams_by_name[t['name'].lower()] = {'team_id': t['id'], 'league': t['league']}
                if t['short_name']:
                    teams_by_name[t['short_name'].lower()] = {'team_id': t['id'], 'league': t['league']}

            n_teams = {r['league']: r['c'] for r in db.execute(
                'SELECT league, COUNT(*) as c FROM teams WHERE season_id=? GROUP BY league',
                (season_id,)
            ).fetchall()}
            a_max = {lg: (n ** 2) // 2 for lg, n in n_teams.items()}

            try:
                current_md = ol.get_current_matchday_nr('bl1') or 999
            except Exception:
                current_md = 999

            yield "data: INFO:Lade alle Spiele der Saison...\n\n"
            all_matches = {}
            for league in ('bl1', 'bl2'):
                try:
                    all_matches[league] = ol.ol_get_all_season_matches(league, year)
                    yield f"data: INFO:{league.upper()}: {len(all_matches[league])} Spiele geladen\n\n"
                    time.sleep(0.5)
                except Exception as e:
                    yield f"data: WARN:{league.upper()} Ladefehler: {e}\n\n"
                    all_matches[league] = []

            total = current_md - 1
            done  = 0
            yield f"data: START:{total}\n\n"

            for matchday in range(1, current_md):
                if matchday in existing:
                    done += 1
                    yield f"data: SKIP:{matchday}\n\n"
                    continue

                standings = {}
                for league in ('bl1', 'bl2'):
                    table = ol.compute_table_after_matchday(
                        all_matches.get(league, []), matchday)
                    for ol_tid, entry in table.items():
                        if ol_tid == '_ranked_ids':
                            continue
                        rank = entry.get('_rank')
                        name = entry.get('name', '')
                        info = teams_by_olid.get(int(ol_tid) if ol_tid else 0) or                                teams_by_name.get(name.lower())
                        if info and rank:
                            standings[info['team_id']] = {'rank': rank, 'league': info['league']}

                if not standings:
                    yield f"data: WARN:MD{matchday}: keine Daten\n\n"
                    done += 1
                    continue

                user_scores = []
                for u in users:
                    uid   = u['id']
                    upred = preds_by_user.get(uid, {})
                    dev   = {'bl1': 0, 'bl2': 0}
                    for team_id, pred_rank in upred.items():
                        if team_id not in standings:
                            continue
                        d  = abs(pred_rank - standings[team_id]['rank'])
                        lg = standings[team_id]['league']
                        dev[lg] += d
                    s1 = a_max.get('bl1', 162) - dev['bl1']
                    s2 = a_max.get('bl2', 162) - dev['bl2']
                    user_scores.append({'uid': uid, 'score': s1 + s2})

                user_scores.sort(key=lambda x: -x['score'])
                ts_str = _dt.now().strftime('%Y-%m-%d %H:%M:%S') + f'.{matchday:03d}'
                for rank, us in enumerate(user_scores, 1):
                    db.execute(
                        """INSERT INTO ranking_snapshots
                           (season_id, user_id, rank, score, matchday, label, created_at)
                           VALUES (?,?,?,?,?,?,?)""",
                        (season_id, us['uid'], rank, us['score'],
                         matchday, f'Spieltag {matchday}', ts_str)
                    )
                db.commit()
                done += 1
                yield f"data: OK:{matchday}\n\n"

            new_count = sum(1 for m in range(1, current_md) if m not in existing)
            yield f"data: DONE:{new_count}:{len(existing)}\n\n"

        except Exception as e:
            yield f"data: ERROR:{e}\n\n"
        finally:
            db.close()

    resp = app.response_class(generate(), mimetype='text/event-stream')
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['X-Accel-Buffering'] = 'no'
    return resp


@app.route('/ewige-tabelle')
@login_required
def ewige_tabelle():
    db = get_db()
    # Alle abgeschlossenen + aktiven Saisons mit Scores
    seasons = db.execute(
        'SELECT * FROM seasons ORDER BY year ASC'
    ).fetchall()
    # Pro User: Summe aller Scores + Detailaufschlüsselung
    rows = db.execute(
        """SELECT u.id, u.username, u.display_name,
                  COUNT(sc.season_id)    as seasons_played,
                  SUM(sc.score)          as total_score,
                  SUM(sc.score_bl1)      as total_bl1,
                  SUM(sc.score_bl2)      as total_bl2,
                  SUM(sc.total_deviation) as total_deviation,
                  AVG(sc.score)          as avg_score,
                  MAX(sc.score)          as best_score,
                  MIN(sc.score)          as worst_score
           FROM users u
           JOIN scores sc ON u.id = sc.user_id
           WHERE u.is_active=1
           GROUP BY u.id
           ORDER BY total_score DESC, seasons_played DESC""",
    ).fetchall()
    # Detailscores pro Saison für jeden User
    detail = {}
    for s in seasons:
        sc_rows = db.execute(
            'SELECT user_id, score FROM scores WHERE season_id=?', (s['id'],)
        ).fetchall()
        for sc in sc_rows:
            detail.setdefault(sc['user_id'], {})[s['id']] = sc['score']
    # Legacy-Archiv-Stats pro User (nur für verknüpfte Accounts)
    legacy_stats = {}
    legacy_all = db.execute(
        '''SELECT lr.user_id,
                  COUNT(*) as n,
                  AVG(lr.rang * 100.0 / sm.total) as avg_abw,
                  SUM(CASE WHEN lr.rang=1 THEN 1 ELSE 0 END) as siege
           FROM legacy_results lr
           JOIN (SELECT season, MAX(rang) as total FROM legacy_results GROUP BY season) sm
             ON lr.season = sm.season
           WHERE lr.user_id IS NOT NULL
           GROUP BY lr.user_id
           ORDER BY avg_abw ASC'''
    ).fetchall()
    for rank, r in enumerate(legacy_all, 1):
        legacy_stats[r['user_id']] = dict(r)
        legacy_stats[r['user_id']]['archiv_rang'] = rank
    legacy_total = len(legacy_all)
    return render_template('ewige_tabelle.html',
        rows=rows, seasons=seasons, detail=detail,
        max_score=MAX_SCORE, leagues=LEAGUES,
        legacy_stats=legacy_stats, legacy_total=legacy_total)

# ── Manuelle Tabelleneingabe (Admin) ───────────
@app.route('/admin/standings/manual', methods=['GET','POST'])
@admin_required
def admin_manual_standings():
    season = get_active_season()
    if not season:
        flash('Keine aktive Saison.','warning')
        return redirect(url_for('admin_dashboard'))
    db = get_db()
    if request.method == 'POST':
        league  = request.form.get('league','bl1')
        teams   = get_season_teams(season['id'], league)
        errors  = []
        updates = {}
        used_ranks = set()
        for team in teams:
            rank_val = request.form.get(f'rank_{team["id"]}','').strip()
            pts_val  = request.form.get(f'pts_{team["id"]}','0').strip()
            sp_val   = request.form.get(f'sp_{team["id"]}','0').strip()
            w_val    = request.form.get(f'w_{team["id"]}','0').strip()
            d_val    = request.form.get(f'd_{team["id"]}','0').strip()
            l_val    = request.form.get(f'l_{team["id"]}','0').strip()
            gf_val   = request.form.get(f'gf_{team["id"]}','0').strip()
            ga_val   = request.form.get(f'ga_{team["id"]}','0').strip()
            if not rank_val or not rank_val.isdigit():
                errors.append(f'Platz für {team["name"]} fehlt.'); continue
            rank = int(rank_val)
            if rank in used_ranks:
                errors.append(f'Platz {rank} doppelt vergeben.'); continue
            used_ranks.add(rank)
            updates[team['id']] = {
                'rank': rank,
                'pts':  int(pts_val)  if pts_val.isdigit()  else 0,
                'sp':   int(sp_val)   if sp_val.isdigit()   else 0,
                'w':    int(w_val)    if w_val.isdigit()    else 0,
                'd':    int(d_val)    if d_val.isdigit()    else 0,
                'l':    int(l_val)    if l_val.isdigit()    else 0,
                'gf':   int(gf_val)   if gf_val.isdigit()   else 0,
                'ga':   int(ga_val)   if ga_val.isdigit()   else 0,
            }
        if errors:
            for e in errors: flash(e,'danger')
        else:
            for tid, v in updates.items():
                db.execute(
                    """INSERT INTO standings
                       (season_id,team_id,current_rank,points,matches_played,wins,draws,losses,goals_for,goals_against,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                       ON CONFLICT(season_id,team_id) DO UPDATE SET
                       current_rank=excluded.current_rank, points=excluded.points,
                       matches_played=excluded.matches_played, wins=excluded.wins,
                       draws=excluded.draws, losses=excluded.losses,
                       goals_for=excluded.goals_for, goals_against=excluded.goals_against,
                       updated_at=CURRENT_TIMESTAMP""",
                    (season['id'], tid, v['rank'], v['pts'], v['sp'],
                     v['w'], v['d'], v['l'], v['gf'], v['ga'])
                )
            db.execute('UPDATE seasons SET last_updated=CURRENT_TIMESTAMP WHERE id=?',(season['id'],))
            db.commit()
            calculate_scores_for_season(season['id'])
            flash(f'{LEAGUES.get(league)} manuell gespeichert und Punkte berechnet ✓','success')
            return redirect(url_for('admin_manual_standings', league=league))

    league = request.args.get('league','bl1')
    teams  = get_season_teams(season['id'], league)
    # Bestehende Standings vorladen
    existing = {}
    for s in db.execute(
        'SELECT team_id,current_rank,points,matches_played,wins,draws,losses,goals_for,goals_against FROM standings WHERE season_id=?',
        (season['id'],)
    ).fetchall():
        existing[s['team_id']] = dict(s)
    # Sortiert nach aktuellem Rang (oder alphabetisch falls noch leer)
    teams = sorted(teams, key=lambda t: existing.get(t['id'], {}).get('current_rank', 99))
    return render_template('admin/manual_standings.html',
        season=season, teams=teams, existing=existing,
        league=league, leagues=LEAGUES,
        api_provider=ol.get_active_provider())

# ── Spieltage / Live-Ergebnisse ───────────────
@app.route('/spieltage')
@app.route('/spieltage/<league>/<int:matchday>')
@login_required
def spieltage(league='bl1', matchday=None):
    import requests as _req
    season = get_active_season()
    year   = season['year'] if season else local_now().year

    # Aktuellen Spieltag ermitteln falls nicht angegeben
    if matchday is None:
        try:
            matchday = ol.get_current_matchday_nr(league)
        except Exception:
            matchday = 1

    matches    = []
    error      = None
    has_live   = False

    try:
        matches  = ol.get_matchday_normalized(league, year, matchday)
        has_live = any(m['is_live'] for m in matches)
    except _req.exceptions.ConnectTimeout:
        error = 'Timeout – OpenligaDB nicht erreichbar.'
    except _req.exceptions.ConnectionError:
        error = 'Keine Verbindung zu OpenligaDB.'
    except Exception as e:
        error = f'Fehler: {e}'

    # Spieltag-Navigation: max 34 Spieltage
    return render_template('spieltage.html',
        season=season, league=league, matchday=matchday,
        matches=matches, has_live=has_live, error=error,
        leagues=LEAGUES, year=year,
        max_matchday=34, now_utc=local_now(),
        now_local=local_now_str()
    )

@app.route('/spieltage/api/<league>/<int:year>/<int:matchday>')
@login_required
def spieltage_api(league, year, matchday):
    """
    JSON-Endpunkt für Live-Aktualisierung.
    Nutzt getlastchangedate: lädt volle Daten nur bei tatsächlicher Änderung.
    Wenn sich Spiele geändert haben → Tabelle & Bestenliste automatisch aktualisieren.
    """
    db        = get_db()
    cache_key = f'matches_{league}_{year}_{matchday}'
    cache     = _cache_get(cache_key)
    known_ts  = cache['last_ts'] if cache else ''

    try:
        # Für OpenligaDB: leichte Vorab-Prüfung via getlastchangedate
        # Für football-data.org: Hash-Vergleich nach dem Laden
        fd_key = os.environ.get('FOOTBALL_DATA_API_KEY', '')

        if not fd_key:
            # OpenligaDB: erst Timestamp prüfen (sparsamer API-Aufruf)
            changed, new_ts = ol.has_changed_since(league, year, matchday, known_ts)
            if not changed and cache and cache['data']:
                matches  = json.loads(cache['data'])
                has_live = any(m['is_live'] for m in matches)
                interval = ol.INTERVAL_LIVE if has_live else ol.INTERVAL_NORMAL
                return jsonify({
                    'ok': True, 'matches': matches, 'has_live': has_live,
                    'cached': True, 'next_check_sec': interval,
                    'scores_updated': False
                })

        # Daten laden
        matches, new_hash = ol.get_matchday_with_hash(league, year, matchday)
        new_ts = new_hash if fd_key else new_ts

        # Hash-Vergleich bei football-data.org: unverändert → Cache zurückgeben
        if fd_key and new_hash == known_ts and cache and cache['data']:
            matches  = json.loads(cache['data'])
            has_live = any(m['is_live'] for m in matches)
            interval = ol.INTERVAL_LIVE if has_live else ol.INTERVAL_NORMAL
            return jsonify({
                'ok': True, 'matches': matches, 'has_live': has_live,
                'cached': True, 'next_check_sec': interval,
                'scores_updated': False
            })

        has_live = any(m['is_live'] for m in matches)
        _cache_set(cache_key, new_ts, json.dumps(matches))

        # Tabelle & Bestenliste aktualisieren wenn Spiele beendet wurden
        scores_updated = False
        any_finished   = any(m['is_finished'] for m in matches)
        if any_finished:
            season = get_active_season()
            if season and season['season_started']:
                try:
                    # Tabellen-Cache für diese Liga invalidieren damit sofort neu geladen wird
                    table_key = f'table_{league}_{year}'
                    _cache_set(table_key, '', '')  # Timestamp leeren = erzwingt Reload
                    ok, _ = update_standings_from_api(season, force=True)
                    if ok:
                        scores_updated = True
                except Exception as e:
                    app.logger.debug(f"Ignorierter Fehler: {e}")

        interval = ol.INTERVAL_LIVE if has_live else ol.INTERVAL_NORMAL
        return jsonify({
            'ok': True, 'matches': matches, 'has_live': has_live,
            'cached': False, 'next_check_sec': interval,
            'scores_updated': scores_updated
        })

    except Exception as e:
        # Bei Fehler: gecachte Daten nutzen
        if cache and cache['data']:
            try:
                matches  = json.loads(cache['data'])
                has_live = any(m['is_live'] for m in matches)
                return jsonify({
                    'ok': True, 'matches': matches, 'has_live': has_live,
                    'cached': True, 'next_check_sec': ol.INTERVAL_NORMAL,
                    'scores_updated': False,
                    'warning': 'API nicht erreichbar – gecachte Daten'
                })
            except Exception as e:
                app.logger.debug(f"Ignorierter Fehler: {e}")
        return jsonify({'ok': False, 'error': str(e)})

# ── Admin Backup & Restore ─────────────────────
def build_backup_zip_bytes(include_db=True, include_uploads=True, include_config=True):
    """
    Baut ein Backup-ZIP im Speicher und gibt die Rohbytes zurück.
    Gemeinsam genutzt vom manuellen Download (admin_backup) und dem
    automatischen täglichen Backup (run_scheduled_backup_if_due).
    """
    import zipfile
    timestamp   = local_now().strftime('%Y%m%d_%H%M%S')
    db_path     = os.environ.get('DB_PATH', DATABASE)
    uploads_dir = os.path.join(BASE_DIR, 'static', 'uploads')

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        if include_db:
            db_tmp = os.path.join(tempfile.gettempdir(), f'tippcup_db_{timestamp}.db')
            try:
                get_db().execute(f"VACUUM INTO '{db_tmp}'")
            except Exception:
                shutil.copy2(db_path, db_tmp)
            zf.write(db_tmp, 'tippspiel.db')
            try: os.remove(db_tmp)
            except Exception: pass

        if include_config:
            for fname in ('app.py', 'passenger_wsgi.py', 'telegram_bot.py',
                          'openliga.py', 'requirements.txt'):
                fpath = os.path.join(BASE_DIR, fname)
                if os.path.exists(fpath):
                    zf.write(fpath, f'config/{fname}')

        if include_uploads and os.path.isdir(uploads_dir):
            for root, _, files in os.walk(uploads_dir):
                for fname in files:
                    full = os.path.join(root, fname)
                    rel  = os.path.relpath(full, uploads_dir)
                    zf.write(full, f'uploads/{rel}')

    buf.seek(0)
    return buf.getvalue()


@app.route('/admin/backup', methods=['POST'])
@admin_required
def admin_backup():
    """Selektives Backup als ZIP – Inhalt per Checkboxen wählbar, direkter Download."""
    include_db      = 'include_db'      in request.form
    include_uploads = 'include_uploads' in request.form
    include_config  = 'include_config'  in request.form

    if not any([include_db, include_uploads, include_config]):
        flash('Bitte mindestens eine Komponente auswählen.', 'warning')
        return redirect(url_for('admin_backup_page'))

    timestamp = local_now().strftime('%Y%m%d_%H%M%S')
    parts     = []
    if include_db:      parts.append('db')
    if include_uploads: parts.append('uploads')
    if include_config:  parts.append('config')
    zip_name  = f'tippcup_backup_{timestamp}_{"_".join(parts)}.zip'

    try:
        zip_bytes = build_backup_zip_bytes(include_db, include_uploads, include_config)
        return send_file(io.BytesIO(zip_bytes), as_attachment=True,
                         download_name=zip_name, mimetype='application/zip')
    except Exception as e:
        flash(f'Backup fehlgeschlagen: {e}', 'danger')
        return redirect(url_for('admin_backup_page'))


# ── Automatisches tägliches Backup ────────────────────────────
BACKUPS_DIR = os.path.join(BASE_DIR, 'backups')

def get_or_create_backup_cron_token():
    """Erzeugt beim ersten Aufruf ein zufälliges Token für den externen Cron-Trigger."""
    token = get_setting('backup_cron_token')
    if not token:
        token = secrets.token_urlsafe(32)
        set_setting('backup_cron_token', token)
    return token

def prune_old_backups(retention_days):
    """Löscht automatische Backups, die älter als retention_days sind. Manuell
    heruntergeladene Backups betrifft das nicht – die liegen nie auf der Platte,
    sondern werden direkt als Download gestreamt (siehe admin_backup)."""
    if not os.path.isdir(BACKUPS_DIR):
        return
    cutoff = local_now() - timedelta(days=retention_days)
    for fname in os.listdir(BACKUPS_DIR):
        if not fname.endswith('.zip'):
            continue
        fpath = os.path.join(BACKUPS_DIR, fname)
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(fpath))
            if mtime < cutoff:
                os.remove(fpath)
        except Exception as e:
            app.logger.debug(f"Ignorierter Fehler beim Backup-Aufräumen: {e}")

def run_scheduled_backup_if_due(force=False):
    """
    Prüft, ob das letzte automatische Backup >= 23h her ist, und erstellt bei
    Bedarf eins auf der Festplatte (backups/-Verzeichnis, per .gitignore vom
    Repo ausgeschlossen). Läuft entweder über einen leichten Trigger bei
    Seitenaufrufen (siehe dashboard()) oder über den externen Cron-Endpoint
    /cron/backup/<token>. Ist sicher gegen doppelte Ausführung durch parallele
    Requests, da der "Belegt"-Zeitstempel vor der eigentlichen Arbeit gesetzt wird.
    """
    with app.app_context():
        db = get_db()
        if not force and get_setting('backup_auto_enabled', '1') == '0':
            return False
        now = local_now()
        if not force:
            last_raw = get_setting('backup_last_auto')
            if last_raw:
                try:
                    last = datetime.fromisoformat(last_raw)
                    if now - last < timedelta(hours=23):
                        return False
                except Exception as e:
                    app.logger.debug(f"Ignorierter Fehler: {e}")
        # Zeitstempel SOFORT setzen, bevor die eigentliche Arbeit beginnt –
        # verhindert, dass zwei fast gleichzeitige Trigger (Seitenaufruf +
        # externer Cron) doppelt loslaufen.
        set_setting('backup_last_auto', now.isoformat())
        try:
            os.makedirs(BACKUPS_DIR, exist_ok=True)
            include_db      = get_setting('backup_auto_include_db', '1') == '1'
            include_uploads = get_setting('backup_auto_include_uploads', '1') == '1'
            include_config  = get_setting('backup_auto_include_config', '1') == '1'
            zip_bytes = build_backup_zip_bytes(include_db, include_uploads, include_config)
            zip_name  = f'tippcup_backup_{now.strftime("%Y%m%d_%H%M%S")}_auto.zip'
            zip_path  = os.path.join(BACKUPS_DIR, zip_name)
            if os.path.exists(zip_path):
                # Zwei Läufe innerhalb derselben Sekunde (z.B. manuelles "Jetzt
                # erstellen" kurz nach einem automatischen Lauf) - Kollision
                # durch kurzen Zufalls-Suffix vermeiden statt zu überschreiben.
                zip_name = f'tippcup_backup_{now.strftime("%Y%m%d_%H%M%S")}_{secrets.token_hex(3)}_auto.zip'
                zip_path = os.path.join(BACKUPS_DIR, zip_name)
            with open(zip_path, 'wb') as f:
                f.write(zip_bytes)
            retention_days = int(get_setting('backup_retention_days', '14'))
            prune_old_backups(retention_days)
            set_setting('backup_last_success', now.isoformat())
            app.logger.info(f'Automatisches Backup erstellt: {zip_name}')
            return True
        except Exception:
            app.logger.exception('Automatisches Backup fehlgeschlagen')
            return False

def maybe_trigger_backup_async():
    """Wird von einer normalen Nutzer-Anfrage (dashboard) aus aufgerufen –
    läuft in einem Hintergrund-Thread, damit kein Seitenaufruf durch das
    Backup verzögert wird."""
    if app.config.get('TESTING'):
        return  # In Tests nie Hintergrund-Threads starten (Race Conditions mit der Test-DB)
    import threading
    def _bg():
        try:
            run_scheduled_backup_if_due()
        except Exception:
            app.logger.exception('Hintergrund-Backup-Trigger fehlgeschlagen')
    threading.Thread(target=_bg, daemon=True).start()

@app.route('/cron/backup/<token>')
def cron_backup(token):
    """
    Externer Trigger-Endpoint für Plesk 'Geplante Aufgaben' oder einen Dienst
    wie cron-job.org – ruft z.B. einmal täglich per curl/HTTP-GET diese URL
    auf. Sessionlos, daher kein CSRF-Token nötig (siehe CSRF_EXEMPT_ENDPOINTS).
    Der Token wird zufällig generiert und ist in der Admin-Backup-Seite sichtbar.
    """
    with app.app_context():
        expected = get_or_create_backup_cron_token()
    if not expected or not hmac.compare_digest(expected, token):
        return jsonify({'ok': False, 'error': 'invalid token'}), 403
    ok = run_scheduled_backup_if_due(force=True)
    return jsonify({'ok': ok})


@app.route('/admin/backup/page')
@admin_required
def admin_backup_page():
    """Backup & Restore Seite."""
    db_path  = os.environ.get('DB_PATH', DATABASE)
    db_size  = db_mtime = 0
    if os.path.exists(db_path):
        stat     = os.stat(db_path)
        db_size  = stat.st_size
        db_mtime = datetime.fromtimestamp(stat.st_mtime).strftime('%d.%m.%Y %H:%M:%S')
    uploads_dir  = os.path.join(BASE_DIR, 'static', 'uploads')
    upload_size  = sum(
        os.path.getsize(os.path.join(r, f))
        for r, _, files in os.walk(uploads_dir) for f in files
    ) if os.path.isdir(uploads_dir) else 0
    upload_count = sum(len(fs) for _, _, fs in os.walk(uploads_dir)) \
                   if os.path.isdir(uploads_dir) else 0
    db = get_db()
    stats = {
        'users':       db.execute('SELECT COUNT(*) FROM users WHERE is_active=1').fetchone()[0],
        'predictions': db.execute('SELECT COUNT(*) FROM predictions').fetchone()[0],
        'seasons':     db.execute('SELECT COUNT(*) FROM seasons').fetchone()[0],
    }

    # Automatisches Backup: Status + vorhandene Dateien
    auto_enabled          = get_setting('backup_auto_enabled', '1') == '1'
    auto_include_db       = get_setting('backup_auto_include_db', '1') == '1'
    auto_include_uploads  = get_setting('backup_auto_include_uploads', '1') == '1'
    auto_include_config   = get_setting('backup_auto_include_config', '1') == '1'
    retention_days = int(get_setting('backup_retention_days', '14'))
    last_attempt   = get_setting('backup_last_auto')
    last_success   = get_setting('backup_last_success')
    cron_token     = get_or_create_backup_cron_token()
    cron_url       = url_for('cron_backup', token=cron_token, _external=True)

    auto_backups = []
    if os.path.isdir(BACKUPS_DIR):
        for fname in sorted(os.listdir(BACKUPS_DIR), reverse=True):
            if not fname.endswith('.zip'):
                continue
            fpath = os.path.join(BACKUPS_DIR, fname)
            auto_backups.append({
                'name': fname,
                'size': os.path.getsize(fpath),
                'mtime': datetime.fromtimestamp(os.path.getmtime(fpath)).strftime('%d.%m.%Y %H:%M:%S'),
            })

    return render_template('admin/backup.html',
        db_size=db_size, db_mtime=db_mtime, stats=stats,
        upload_size=upload_size, upload_count=upload_count,
        auto_enabled=auto_enabled, retention_days=retention_days,
        auto_include_db=auto_include_db, auto_include_uploads=auto_include_uploads,
        auto_include_config=auto_include_config,
        last_attempt=last_attempt, last_success=last_success,
        cron_url=cron_url, auto_backups=auto_backups)


@app.route('/admin/backup/auto-settings', methods=['POST'])
@admin_required
def admin_backup_auto_settings():
    """Speichert Ein/Aus, Aufbewahrungsdauer und Inhalt für das automatische Backup."""
    enabled = '1' if request.form.get('auto_enabled') == 'on' else '0'
    try:
        retention_days = max(1, min(365, int(request.form.get('retention_days', 14))))
    except (TypeError, ValueError):
        retention_days = 14
    include_db      = '1' if request.form.get('auto_include_db')      == 'on' else '0'
    include_uploads = '1' if request.form.get('auto_include_uploads') == 'on' else '0'
    include_config  = '1' if request.form.get('auto_include_config')  == 'on' else '0'
    if include_db == '0' and include_uploads == '0' and include_config == '0':
        flash('Bitte mindestens eine Komponente für das automatische Backup auswählen.', 'warning')
        return redirect(url_for('admin_backup_page'))
    set_setting('backup_auto_enabled', enabled)
    set_setting('backup_retention_days', str(retention_days))
    set_setting('backup_auto_include_db', include_db)
    set_setting('backup_auto_include_uploads', include_uploads)
    set_setting('backup_auto_include_config', include_config)
    flash('Einstellungen für automatisches Backup gespeichert.', 'success')
    return redirect(url_for('admin_backup_page'))


@app.route('/admin/backup/regenerate-token', methods=['POST'])
@admin_required
def admin_backup_regenerate_token():
    """Erzeugt ein neues Cron-Token – die alte URL funktioniert danach nicht mehr."""
    new_token = secrets.token_urlsafe(32)
    set_setting('backup_cron_token', new_token)
    flash('Neues Cron-Token erzeugt. Bitte die URL in Plesk/eurem Cron-Dienst aktualisieren!', 'warning')
    return redirect(url_for('admin_backup_page'))


@app.route('/admin/backup/run-now', methods=['POST'])
@admin_required
def admin_backup_run_now():
    """Löst manuell sofort ein automatisches Backup aus (landet in backups/, wie der Cron-Trigger)."""
    ok = run_scheduled_backup_if_due(force=True)
    flash('Backup erstellt.' if ok else 'Backup fehlgeschlagen – Details im Server-Log.',
          'success' if ok else 'danger')
    return redirect(url_for('admin_backup_page'))


def _safe_backup_filename(filename):
    """Verhindert Path-Traversal: nur exakt ein Dateiname aus dem backups/-Verzeichnis selbst."""
    if not filename or '/' in filename or '\\' in filename or filename in ('.', '..'):
        return None
    fpath = os.path.join(BACKUPS_DIR, filename)
    if not os.path.isfile(fpath):
        return None
    # Sicherstellen, dass der aufgelöste Pfad wirklich innerhalb von BACKUPS_DIR liegt
    if os.path.realpath(fpath) != os.path.realpath(os.path.join(BACKUPS_DIR, filename)):
        return None
    if os.path.dirname(os.path.realpath(fpath)) != os.path.realpath(BACKUPS_DIR):
        return None
    return fpath


@app.route('/admin/backup/download/<path:filename>')
@admin_required
def admin_backup_download(filename):
    fpath = _safe_backup_filename(filename)
    if not fpath:
        flash('Backup-Datei nicht gefunden.', 'danger')
        return redirect(url_for('admin_backup_page'))
    return send_file(fpath, as_attachment=True, download_name=os.path.basename(fpath),
                     mimetype='application/zip')


@app.route('/admin/backup/delete/<path:filename>', methods=['POST'])
@admin_required
def admin_backup_delete(filename):
    fpath = _safe_backup_filename(filename)
    if not fpath:
        flash('Backup-Datei nicht gefunden.', 'danger')
        return redirect(url_for('admin_backup_page'))
    try:
        os.remove(fpath)
        flash('Backup gelöscht.', 'success')
    except Exception:
        app.logger.exception('Backup konnte nicht gelöscht werden')
        flash('Löschen fehlgeschlagen.', 'danger')
    return redirect(url_for('admin_backup_page'))


def _apply_restore_from_zip_bytes(zip_bytes, restore_db=True, restore_uploads=True, restore_config=False):
    """
    Spielt ein Backup-ZIP selektiv ein. Jede der drei Komponenten kann
    einzeln an-/abgewählt werden (z.B. nur Datenbank, ohne Medien
    anzufassen). Wird sowohl vom Upload-Restore (admin_restore) als auch
    vom Direkt-Restore aus einem vorhandenen Auto-Backup verwendet.

    Gibt (restored: list[str], error: str|None) zurück. Bei error!=None
    wurde NICHTS verändert (Validierungsfehler vor jeder Schreiboperation).
    """
    import zipfile, sqlite3 as _sq3
    db_path     = os.environ.get('DB_PATH', DATABASE)
    uploads_dir = os.path.join(BASE_DIR, 'static', 'uploads')
    restored    = []

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()

        # DB zuerst validieren (in ein Temp-File), bevor irgendwas
        # angefasst wird -- verhindert einen halb durchgeführten Restore.
        db_tmp = None
        if restore_db and 'tippspiel.db' in names:
            db_tmp = db_path + '.restore_tmp'
            with open(db_tmp, 'wb') as out:
                out.write(zf.read('tippspiel.db'))
            try:
                c = _sq3.connect(db_tmp)
                c.execute('SELECT COUNT(*) FROM users')
                c.close()
            except Exception as ve:
                os.remove(db_tmp)
                return [], f'DB im ZIP ungültig: {ve}'

        if db_tmp:
            shutil.copy2(db_path, db_path + '.before_restore')
            shutil.move(db_tmp, db_path)
            restored.append('Datenbank')

        if restore_uploads:
            upload_files = [n for n in names if n.startswith('uploads/')]
            if upload_files:
                os.makedirs(uploads_dir, exist_ok=True)
                for zname in upload_files:
                    rel = zname[len('uploads/'):]
                    if not rel: continue
                    dest = os.path.join(uploads_dir, rel)
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with open(dest, 'wb') as out:
                        out.write(zf.read(zname))
                restored.append(f'{len(upload_files)} Medien')

        if restore_config:
            config_files = [n for n in names if n.startswith('config/')]
            if config_files:
                for zname in config_files:
                    rel  = zname[len('config/'):]
                    dest = os.path.join(BASE_DIR, rel)
                    with open(dest, 'wb') as out:
                        out.write(zf.read(zname))
                restored.append(f'{len(config_files)} Konfigdateien')

    return restored, None


@app.route('/admin/restore', methods=['POST'])
@admin_required
def admin_restore():
    """Restore aus ZIP (selektiv per Checkbox) oder reiner .db-Datei."""
    import sqlite3 as _sq3
    f = request.files.get('backup_file')
    if not f or not f.filename:
        flash('Keine Datei ausgewählt.', 'danger')
        return redirect(url_for('admin_backup_page'))

    db_path     = os.environ.get('DB_PATH', DATABASE)
    fname_lower = f.filename.lower()
    restore_db      = 'restore_db'      in request.form
    restore_uploads = 'restore_uploads' in request.form
    restore_config  = 'restore_config'  in request.form

    if fname_lower.endswith('.zip'):
        if not any([restore_db, restore_uploads, restore_config]):
            flash('Bitte mindestens eine Komponente zum Wiederherstellen auswählen.', 'warning')
            return redirect(url_for('admin_backup_page'))
        try:
            restored, error = _apply_restore_from_zip_bytes(
                f.read(), restore_db, restore_uploads, restore_config)
            if error:
                flash(error, 'danger')
                return redirect(url_for('admin_backup_page'))
            if not restored:
                flash('Die ausgewählten Komponenten waren im ZIP nicht enthalten.', 'warning')
                return redirect(url_for('admin_backup_page'))
            session.clear()
            flash(f'✅ Wiederhergestellt: {", ".join(restored)}. Bitte neu anmelden.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            app.logger.exception('Restore fehlgeschlagen')
            flash(f'Wiederherstellung fehlgeschlagen: {e}', 'danger')
            return redirect(url_for('admin_backup_page'))

    elif fname_lower.endswith('.db'):
        # Legacy: reine .db-Datei (immer nur die Datenbank, kein Auswahl-UI nötig)
        try:
            tmp = db_path + '.restore_tmp'
            f.save(tmp)
            try:
                c = _sq3.connect(tmp)
                c.execute('SELECT COUNT(*) FROM users')
                c.close()
            except Exception as ve:
                os.remove(tmp)
                flash(f'Ungültige DB-Datei: {ve}', 'danger')
                return redirect(url_for('admin_backup_page'))
            shutil.copy2(db_path, db_path + '.before_restore')
            shutil.move(tmp, db_path)
            session.clear()
            flash('✅ Datenbank wiederhergestellt. Bitte neu anmelden.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            app.logger.exception('Restore fehlgeschlagen')
            flash(f'Wiederherstellung fehlgeschlagen: {e}', 'danger')
            return redirect(url_for('admin_backup_page'))

    else:
        flash('Ungültiges Format – nur .zip oder .db erlaubt.', 'danger')
        return redirect(url_for('admin_backup_page'))


@app.route('/admin/backup/restore-from-auto/<path:filename>', methods=['POST'])
@admin_required
def admin_backup_restore_from_auto(filename):
    """Stellt direkt aus einem vorhandenen automatischen Backup wieder her –
    ohne den Umweg über Download + erneuten Upload."""
    fpath = _safe_backup_filename(filename)
    if not fpath:
        flash('Backup-Datei nicht gefunden.', 'danger')
        return redirect(url_for('admin_backup_page'))

    restore_db      = 'restore_db'      in request.form
    restore_uploads = 'restore_uploads' in request.form
    restore_config  = 'restore_config'  in request.form
    if not any([restore_db, restore_uploads, restore_config]):
        flash('Bitte mindestens eine Komponente zum Wiederherstellen auswählen.', 'warning')
        return redirect(url_for('admin_backup_page'))

    try:
        with open(fpath, 'rb') as f:
            zip_bytes = f.read()
        restored, error = _apply_restore_from_zip_bytes(
            zip_bytes, restore_db, restore_uploads, restore_config)
        if error:
            flash(error, 'danger')
            return redirect(url_for('admin_backup_page'))
        if not restored:
            flash('Die ausgewählten Komponenten waren in diesem Backup nicht enthalten.', 'warning')
            return redirect(url_for('admin_backup_page'))
        session.clear()
        flash(f'✅ Wiederhergestellt aus {os.path.basename(fpath)}: {", ".join(restored)}. '
              f'Bitte neu anmelden.', 'success')
        return redirect(url_for('login'))
    except Exception as e:
        app.logger.exception('Restore aus Auto-Backup fehlgeschlagen')
        flash(f'Wiederherstellung fehlgeschlagen: {e}', 'danger')
        return redirect(url_for('admin_backup_page'))

# ── Admin E-Mail ──────────────────────────────

@app.route('/admin/api-config', methods=['GET', 'POST'])
@admin_required
def admin_api_config():
    if request.method == 'POST':
        action = request.form.get('action', 'save_key')

        if action == 'clear_push':
            get_db().execute('DELETE FROM push_subscriptions')
            get_db().commit()
            flash('Alle Push-Subscriptions gelöscht. Nutzer müssen Push neu aktivieren.', 'warning')
            return redirect(url_for('admin_api_config'))

        if action == 'rotate_vapid':
            # VAPID_KEY_VERSION erhöhen → alte Subscriptions werden beim nächsten Start gelöscht
            db = get_db()
            kv = db.execute("SELECT data FROM api_cache WHERE key='vapid_key_version'").fetchone()
            cur = kv['data'] if kv else VAPID_KEY_VERSION
            # Neue Version = v + (n+1)
            try:
                n = int(cur.lstrip('v')) + 1
            except Exception:
                n = 99
            new_ver = f'v{n}'
            db.execute("INSERT OR REPLACE INTO api_cache (key,last_ts,data) VALUES ('vapid_key_version',datetime('now'),?)",
                       (new_ver,))
            db.execute('DELETE FROM push_subscriptions')
            db.commit()
            flash(f'VAPID-Version auf {new_ver} gesetzt. Alle Subscriptions gelöscht — Push bitte auf allen Geräten neu aktivieren.', 'warning')
            return redirect(url_for('admin_api_config'))

        # Standard: API-Key speichern
        key = request.form.get('football_data_api_key', '').strip()
        set_config('football_data_api_key', key)
        if key:
            os.environ['FOOTBALL_DATA_API_KEY'] = key
        else:
            os.environ.pop('FOOTBALL_DATA_API_KEY', None)
        flash('API-Einstellungen gespeichert.', 'success')
        return redirect(url_for('admin_api_config'))

    db = get_db()
    current_key = get_config('football_data_api_key', os.environ.get('FOOTBALL_DATA_API_KEY', ''))
    provider    = ol.get_active_provider()

    # Push-Statistiken
    push_total = db.execute('SELECT COUNT(*) FROM push_subscriptions').fetchone()[0]
    push_users = db.execute('SELECT COUNT(DISTINCT user_id) FROM push_subscriptions').fetchone()[0]
    push_by_user = db.execute(
        '''SELECT u.display_name, u.username, COUNT(ps.id) as n
           FROM push_subscriptions ps JOIN users u ON ps.user_id=u.id
           GROUP BY ps.user_id ORDER BY u.display_name'''
    ).fetchall()
    # VAPID-Status
    vapid_ok      = bool(VAPID_PRIVATE_KEY and VAPID_PUBLIC_KEY)
    vapid_version = db.execute("SELECT data FROM api_cache WHERE key='vapid_key_version'").fetchone()
    vapid_ver     = vapid_version['data'] if vapid_version else VAPID_KEY_VERSION

    return render_template('admin/api_config.html',
        current_key=current_key, provider=provider,
        push_total=push_total, push_users=push_users, push_by_user=push_by_user,
        vapid_ok=vapid_ok, vapid_ver=vapid_ver,
        vapid_public_short=VAPID_PUBLIC_KEY[:24] + '…' if VAPID_PUBLIC_KEY else '–')

@app.route('/admin/security')
@admin_required
def admin_security():
    db       = get_db()
    cutoff   = _utc_cutoff(minutes=LOCKOUT_MIN)
    cutoff24 = _utc_cutoff(hours=24)
    locked = db.execute(
        """SELECT ip, COUNT(*) as attempts,
                  MAX(created_at) as last_attempt,
                  GROUP_CONCAT(DISTINCT username) as usernames
           FROM login_attempts
           WHERE created_at > ?
           GROUP BY ip
           HAVING attempts >= ?
           ORDER BY last_attempt DESC""",
        (cutoff, MAX_ATTEMPTS)
    ).fetchall()
    recent = db.execute(
        """SELECT ip, username, created_at
           FROM login_attempts
           WHERE created_at > ?
           ORDER BY created_at DESC LIMIT 50""",
        (cutoff24,)
    ).fetchall()
    return render_template('admin/security.html',
        locked=locked, recent=recent,
        max_attempts=MAX_ATTEMPTS, lockout_min=LOCKOUT_MIN)

@app.route('/admin/security/unblock', methods=['POST'])
@admin_required
def admin_unblock():
    ip = request.form.get('ip','').strip()
    if ip:
        get_db().execute('DELETE FROM login_attempts WHERE ip=?', (ip,))
        get_db().commit()
        flash(f'IP {ip} entsperrt.', 'success')
    return redirect(url_for('admin_security'))

@app.route('/admin/security/clear', methods=['POST'])
@admin_required
def admin_clear_attempts():
    get_db().execute('DELETE FROM login_attempts')
    get_db().commit()
    flash('Alle Login-Versuche gelöscht.', 'success')
    return redirect(url_for('admin_security'))

@app.route('/admin/email', methods=['GET','POST'])
@admin_required
def admin_email():
    db     = get_db()
    season = get_active_season()
    cfg    = get_smtp_config()

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'save_smtp':
            db.execute(
                """UPDATE smtp_config SET host=?,port=?,username=?,password=?,
                   sender_name=?,sender_email=?,use_tls=?,updated_at=CURRENT_TIMESTAMP
                   WHERE id=1""",
                (request.form.get('host','').strip(),
                 int(request.form.get('port', 587) or 587),
                 request.form.get('username','').strip(),
                 request.form.get('password','').strip(),
                 request.form.get('sender_name','Tippcup Bundesliga').strip(),
                 request.form.get('sender_email','').strip(),
                 1 if request.form.get('use_tls') else 0)
            )
            db.commit()
            flash('SMTP-Einstellungen gespeichert.', 'success')

        elif action == 'test_smtp':
            test_addr = request.form.get('test_email','').strip()
            if not test_addr:
                flash('Bitte Test-E-Mail-Adresse eingeben.', 'danger')
            else:
                ok, errs = send_email(
                    [(session['display_name'], test_addr)],
                    'Tippcup Bundesliga – SMTP-Test',
                    '<h2>✅ SMTP funktioniert!</h2><p>Dein Tippcup Bundesliga kann E-Mails senden.</p>'
                )
                if ok:
                    flash(f'Test-E-Mail erfolgreich an {test_addr} gesendet.', 'success')
                else:
                    flash(f'Fehler: {errs[0] if errs else "Unbekannt"}', 'danger')

        elif action == 'send_all':
            subject  = request.form.get('subject','').strip()
            body     = request.form.get('body','').strip()
            filter_  = request.form.get('filter','all')

            if not subject or not body:
                flash('Betreff und Text erforderlich.', 'danger')
            else:
                # Empfänger bestimmen
                q = 'SELECT username, display_name, email FROM users WHERE is_active=1 AND email IS NOT NULL AND email != ""'
                if filter_ == 'no_tip':
                    sid = season['id'] if season else 0
                    q  += f' AND id NOT IN (SELECT DISTINCT user_id FROM predictions WHERE season_id={sid})'
                elif filter_ == 'incomplete':
                    sid = season['id'] if season else 0
                    q  += f''' AND id NOT IN (
                        SELECT user_id FROM predictions WHERE season_id={sid}
                        GROUP BY user_id
                        HAVING COUNT(*) = (SELECT COUNT(*) FROM teams WHERE season_id={sid})
                    )'''
                recipients = db.execute(q).fetchall()

                if not recipients:
                    flash('Keine Empfänger mit E-Mail-Adresse gefunden.', 'warning')
                else:
                    # HTML aus Plain-Text aufbereiten
                    body_html = '<div style="font-family:sans-serif;max-width:600px">'
                    body_html += f'<h2 style="color:#1a5e2a">⚽ {subject}</h2>'
                    for line in body.splitlines():
                        body_html += f'<p>{line}</p>' if line.strip() else '<br>'
                    body_html += '<hr><p style="color:#999;font-size:12px">Tippcup Bundesliga</p></div>'

                    to_list = [(r['display_name'] or r['username'], r['email']) for r in recipients]
                    ok_cnt, errs = send_email(to_list, subject, body_html, body)

                    if ok_cnt:
                        flash(f'E-Mail an {ok_cnt} Empfänger gesendet.', 'success')
                    if errs:
                        for e in errs[:5]:
                            flash(f'Fehler: {e}', 'warning')
                        if len(errs) > 5:
                            flash(f'... und {len(errs)-5} weitere Fehler.', 'warning')

        return redirect(url_for('admin_email'))

    # Statistiken für Anzeige
    total_users   = db.execute('SELECT COUNT(*) as c FROM users WHERE is_active=1').fetchone()['c']
    with_email    = db.execute('SELECT COUNT(*) as c FROM users WHERE is_active=1 AND email IS NOT NULL AND email != ""').fetchone()['c']
    without_email = total_users - with_email
    users_list    = db.execute('SELECT id,username,display_name,email,is_admin FROM users WHERE is_active=1 ORDER BY username').fetchall()

    return render_template('admin/email.html',
        cfg=cfg, season=season, total_users=total_users,
        with_email=with_email, without_email=without_email,
        users_list=users_list, leagues=LEAGUES)

# ══════════════════════════════════════════════
# NEUE FEATURES
# ══════════════════════════════════════════════

# ── 1. Tippschluss-Erinnerungsmail ────────────
def send_reminder_emails(season):
    """Sendet Erinnerungsmail an alle, die noch nicht getippt haben."""
    if not season or season['tips_locked']:
        return 0, 'Tippabgabe bereits gesperrt.'
    # Früheste Deadline bestimmen
    db = get_db()
    earliest = None
    for lg in ('bl1','bl2'):
        dl = season[f'deadline_{lg}']
        if dl:
            try:
                dt = datetime.fromisoformat(str(dl))
                if earliest is None or dt < earliest:
                    earliest = dt
            except Exception as e:
                app.logger.debug(f"Ignorierter Fehler: {e}")
    if earliest and (earliest - local_now()).total_seconds() > 7 * 86400:
        return 0, 'Deadline noch mehr als 7 Tage entfernt.'
    # Wer hat noch nicht getippt und hat eine E-Mail?
    team_count = sum(team_count_per_league(season['id']).values())
    recipients = db.execute(
        """SELECT u.id, u.username, u.display_name, u.email
           FROM users u
           WHERE u.is_active=1 AND u.email IS NOT NULL AND u.email != ''
             AND u.id NOT IN (
               SELECT DISTINCT user_id FROM predictions
               WHERE season_id=?
               GROUP BY user_id HAVING COUNT(*)=?
             )
             AND u.id NOT IN (
               SELECT user_id FROM email_reminders WHERE season_id=?
             )""",
        (season['id'], team_count, season['id'])
    ).fetchall()
    if not recipients:
        return 0, 'Alle haben bereits getippt oder wurden erinnert.'
    deadline_str = earliest.strftime('%d.%m.%Y um %H:%M Uhr') if earliest else 'bald'
    sent = 0
    for u in recipients:
        name     = u['display_name'] or u['username']
        body_html = f"""
        <div style="font-family:sans-serif;max-width:600px">
          <h2 style="color:#1a5e2a">⚽ Tippcup Bundesliga – Erinnerung!</h2>
          <p>Hallo {name},</p>
          <p>Du hast für <strong>{season['name']}</strong> noch keinen Tipp abgegeben.</p>
          <p><strong>⏰ Tippschluss: {deadline_str}</strong></p>
          <p>Jetzt noch schnell tippen und dabei sein!</p>
          <hr>
          <p style="color:#999;font-size:12px">Tippcup Bundesliga · Diese Mail wurde automatisch versendet.</p>
        </div>"""
        ok, errs = send_email(
            [(name, u['email'])],
            f'⏰ Erinnerung: Tipp für {season["name"]} noch ausstehend!',
            body_html
        )
        if ok:
            db.execute('INSERT OR IGNORE INTO email_reminders (user_id, season_id) VALUES (?,?)',
                       (u['id'], season['id']))
            sent += 1
    db.commit()
    return sent, f'{sent} Erinnerungen gesendet.'

@app.route('/admin/send_reminder', methods=['POST'])
@admin_required
def admin_send_reminder():
    season = get_active_season()
    sent, msg = send_reminder_emails(season)
    flash(msg, 'success' if sent > 0 else 'info')
    return redirect(url_for('admin_season'))

# ── 2. Head-to-Head Vergleich ────────────────
@app.route('/vergleich')
@app.route('/vergleich/<int:uid1>/<int:uid2>')
@login_required
def head2head(uid1=None, uid2=None):
    season = get_active_season()
    db     = get_db()
    # Query-Parameter aus GET-Formular lesen (falls keine URL-Pfad-Parameter)
    if uid1 is None:
        try: uid1 = int(request.args.get('uid1', 0)) or None
        except: uid1 = None
    if uid2 is None:
        try: uid2 = int(request.args.get('uid2', 0)) or None
        except: uid2 = None
    users  = db.execute(
        'SELECT id,username,display_name FROM users WHERE is_active=1 ORDER BY display_name'
    ).fetchall()
    if not uid1 or not uid2 or not season or not season['season_started']:
        return render_template('head2head.html', season=season, users=users,
                               uid1=uid1, uid2=uid2, data=None, leagues=LEAGUES)
    u1 = db.execute('SELECT * FROM users WHERE id=?',(uid1,)).fetchone()
    u2 = db.execute('SELECT * FROM users WHERE id=?',(uid2,)).fetchone()
    if not u1 or not u2:
        flash('Benutzer nicht gefunden.','danger')
        return redirect(url_for('head2head'))
    standings_dict = {s['team_id']: s['current_rank']
                      for lg in ('bl1','bl2') for s in get_standings(season['id'], lg)}
    sc1 = db.execute('SELECT * FROM scores WHERE user_id=? AND season_id=?',(uid1,season['id'])).fetchone()
    sc2 = db.execute('SELECT * FROM scores WHERE user_id=? AND season_id=?',(uid2,season['id'])).fetchone()
    # Tipps beider Spieler pro Team
    def get_preds(uid):
        return {p['team_id']: p['predicted_rank'] for p in db.execute(
            'SELECT team_id,predicted_rank FROM predictions WHERE user_id=? AND season_id=?',
            (uid, season['id'])
        ).fetchall()}
    p1, p2 = get_preds(uid1), get_preds(uid2)
    # Teamlisten sortiert nach aktuellem Rang
    teams = {}
    for lg in ('bl1','bl2'):
        lg_teams = get_season_teams(season['id'], lg)
        teams[lg] = sorted(lg_teams, key=lambda t: standings_dict.get(t['id'], 99))
    # Wer lag bei welchem Team besser?
    wins1 = wins2 = draws = 0
    for tid, actual in standings_dict.items():
        r1 = p1.get(tid); r2 = p2.get(tid)
        if r1 is None or r2 is None: continue
        d1, d2 = abs(r1 - actual), abs(r2 - actual)
        if d1 < d2: wins1 += 1
        elif d2 < d1: wins2 += 1
        else: draws += 1
    data = {'u1':u1,'u2':u2,'sc1':sc1,'sc2':sc2,'p1':p1,'p2':p2,
            'wins1':wins1,'wins2':wins2,'draws':draws}
    return render_template('head2head.html', season=season, users=users,
                           uid1=uid1, uid2=uid2, data=data, teams=teams,
                           standings_dict=standings_dict, leagues=LEAGUES)

# ── 3. Spieltag-Highlight ────────────────────
def save_matchday_highlight(season_id, matchday):
    """Speichert wer sich beim aktuellen Spieltag am meisten verbessert hat."""
    db = get_db()
    # Aktuelle Rangliste
    current = db.execute(
        """SELECT user_id, score FROM scores
           JOIN users u ON scores.user_id=u.id
           WHERE season_id=? AND u.is_active=1
           ORDER BY score DESC, std_deviation ASC, volltreffer DESC, user_id ASC""",
        (season_id,)
    ).fetchall()
    # Vorherige Rangliste (letzter Snapshot)
    prev_snap = db.execute(
        """SELECT user_id, rank, score FROM ranking_snapshots
           WHERE season_id=? AND matchday < ?
           ORDER BY id DESC LIMIT 1""",
        (season_id, matchday)
    ).fetchone()
    if not prev_snap:
        return
    prev_scores = {r['user_id']: (r['rank'], r['score']) for r in db.execute(
        """SELECT rs.user_id, rs.rank, rs.score FROM ranking_snapshots rs
           WHERE rs.season_id=? AND rs.id IN (
               SELECT MAX(id) FROM ranking_snapshots
               WHERE season_id=? AND matchday < ?
               GROUP BY user_id
           )""",
        (season_id, season_id, matchday)
    ).fetchall()}
    best_improvement = -999
    best_uid = None
    for rank_after, s in enumerate(current, 1):
        uid = s['user_id']
        if uid in prev_scores:
            rank_before, score_before = prev_scores[uid]
            improvement = s['score'] - score_before
            if improvement > best_improvement:
                best_improvement = improvement
                best_uid = uid
                best_data = (rank_before, rank_after, score_before, s['score'])
    if best_uid and best_improvement > 0:
        db.execute(
            """INSERT OR REPLACE INTO matchday_highlights
               (season_id,matchday,user_id,rank_before,rank_after,score_before,score_after,improvement)
               VALUES (?,?,?,?,?,?,?,?)""",
            (season_id, matchday, best_uid, *best_data, best_improvement)
        )
        db.commit()

@app.route('/highlights')
@login_required
def highlights():
    season = get_active_season()
    db     = get_db()
    rows   = []
    if season:
        rows = db.execute(
            """SELECT h.*, u.display_name, u.username
               FROM matchday_highlights h
               JOIN users u ON h.user_id=u.id
               WHERE h.season_id=?
               ORDER BY h.matchday DESC""",
            (season['id'],)
        ).fetchall()
    return render_template('highlights.html', season=season, rows=rows)

# ── 4. Saisonrückblick ───────────────────────
@app.route('/rueckblick')
@login_required
def rueckblick():
  try:
    season = get_active_season()
    if not season:
        flash('Keine aktive Saison.','warning')
        return redirect(url_for('dashboard'))
    db = get_db()
    scores = db.execute(
        """SELECT u.id,u.username,u.display_name,u.full_name,u.favorite_club,
                  sc.score,sc.score_bl1,sc.score_bl2,
                  sc.total_deviation,sc.volltreffer,sc.max_deviation,sc.std_deviation
           FROM scores sc JOIN users u ON sc.user_id=u.id
           WHERE sc.season_id=? AND u.is_active=1
           ORDER BY sc.score DESC, sc.std_deviation ASC, sc.volltreffer DESC, sc.user_id ASC""",
        (season['id'],)
    ).fetchall()
    standings = {'bl1': get_standings(season['id'],'bl1'),
                 'bl2': get_standings(season['id'],'bl2')}
    highlights_rows = db.execute(
        """SELECT h.*,u.display_name,u.username
           FROM matchday_highlights h JOIN users u ON h.user_id=u.id
           WHERE h.season_id=? ORDER BY h.matchday""",
        (season['id'],)
    ).fetchall()
    # Angsthasen
    angsthasen_results, has_prev = calculate_angsthasen(season)
    # Ewige Tabelle
    seasons_all = db.execute('SELECT * FROM seasons ORDER BY year ASC').fetchall()
    ewige_rows  = db.execute(
        """SELECT u.id, u.username, u.display_name,
                  COUNT(sc.season_id) as seasons_played,
                  SUM(sc.score)       as total_score,
                  AVG(sc.score)       as avg_score,
                  MAX(sc.score)       as best_score
           FROM users u JOIN scores sc ON u.id=sc.user_id
           WHERE u.is_active=1
           GROUP BY u.id ORDER BY total_score DESC"""
    ).fetchall()
    ewige_detail = {}
    for s in seasons_all:
        for sc in db.execute('SELECT user_id,score FROM scores WHERE season_id=?',(s['id'],)).fetchall():
            ewige_detail.setdefault(sc['user_id'], {})[s['id']] = sc['score']
    # Team-Statistik: wer wurde am häufigsten auf Platz 1 getippt
    team_stats = db.execute(
        """SELECT t.name, t.short_name, t.logo_url, t.league,
                  COUNT(*) as tip_count,
                  SUM(CASE WHEN p.predicted_rank=1 THEN 1 ELSE 0 END) as top1_tips
           FROM predictions p JOIN teams t ON p.team_id=t.id
           WHERE t.season_id=? GROUP BY t.id ORDER BY top1_tips DESC, tip_count DESC""",
        (season['id'],)
    ).fetchall()
    # Badges der Saison
    season_badges = {}
    for row in db.execute(
        'SELECT ub.user_id, ub.badge_type, u.display_name, u.username FROM user_badges ub JOIN users u ON ub.user_id=u.id WHERE ub.season_id=?',
        (season['id'],)
    ).fetchall():
        season_badges.setdefault(row['user_id'], []).append({
            'type': row['badge_type'], 'name': row['display_name'] or row['username'],
            **BADGE_DEFS.get(row['badge_type'], {})
        })
    # Größte Einzel-Abweichung pro User und Liga (wiederverwendet aus leaderboard)
    max_dev_map = {}
    rows_dev = db.execute(
        """SELECT p.user_id, t.league, t.name AS team_name,
                  ABS(p.predicted_rank - s.current_rank) AS dev,
                  p.predicted_rank, s.current_rank
           FROM predictions p
           JOIN teams t     ON p.team_id = t.id
           JOIN standings s ON s.team_id = t.id AND s.season_id = p.season_id
           WHERE p.season_id = ?
           ORDER BY p.user_id, t.league, dev DESC""",
        (season['id'],)
    ).fetchall()
    for r in rows_dev:
        uid = r['user_id']
        lg  = r['league']
        if uid not in max_dev_map:
            max_dev_map[uid] = {}
        if lg not in max_dev_map[uid]:
            max_dev_map[uid][lg] = {
                'dev': r['dev'], 'team': r['team_name'],
                'tipp': r['predicted_rank'], 'actual': r['current_rank'],
            }
    wildcard_bl1_ids = set()
    wildcard_bl2_ids = set()
    max_bl1 = max_bl2 = -1
    for uid, ligs in max_dev_map.items():
        if 'bl1' in ligs:
            if ligs['bl1']['dev'] > max_bl1:
                max_bl1 = ligs['bl1']['dev']; wildcard_bl1_ids = {uid}
            elif ligs['bl1']['dev'] == max_bl1:
                wildcard_bl1_ids.add(uid)
        if 'bl2' in ligs:
            if ligs['bl2']['dev'] > max_bl2:
                max_bl2 = ligs['bl2']['dev']; wildcard_bl2_ids = {uid}
            elif ligs['bl2']['dev'] == max_bl2:
                wildcard_bl2_ids.add(uid)
    # ── Zu spät abgegeben & Nachfrist ──────────────────────────
    dl_bl1 = season['deadline_bl1']
    dl_bl2 = season['deadline_bl2']
    tip_times_bl1 = {r['user_id']: r['last_tip'] for r in db.execute(
        """SELECT p.user_id, MAX(p.updated_at) as last_tip
           FROM predictions p JOIN teams t ON p.team_id=t.id
           WHERE p.season_id=? AND t.league='bl1' GROUP BY p.user_id""", (season['id'],)).fetchall()}
    tip_times_bl2 = {r['user_id']: r['last_tip'] for r in db.execute(
        """SELECT p.user_id, MAX(p.updated_at) as last_tip
           FROM predictions p JOIN teams t ON p.team_id=t.id
           WHERE p.season_id=? AND t.league='bl2' GROUP BY p.user_id""", (season['id'],)).fetchall()}
    all_active_users = db.execute(
        'SELECT id, username, display_name FROM users WHERE is_active=1'
    ).fetchall()
    zu_spaet = []
    for u in all_active_users:
        uid  = u['id']
        name = u['display_name'] or u['username']
        has_bl1 = uid in tip_times_bl1
        has_bl2 = uid in tip_times_bl2
        late_bl1 = dl_bl1 and has_bl1 and tip_times_bl1[uid] > str(dl_bl1)
        late_bl2 = dl_bl2 and has_bl2 and tip_times_bl2[uid] > str(dl_bl2)
        missing_bl1 = dl_bl1 and not has_bl1
        missing_bl2 = dl_bl2 and not has_bl2
        nf = db.execute('SELECT * FROM nachfrist WHERE user_id=? AND season_id=?',
                        (uid, season['id'])).fetchone()
        if late_bl1 or late_bl2 or missing_bl1 or missing_bl2 or nf:
            zu_spaet.append({
                'name': name, 'user_id': uid,
                'late_bl1': late_bl1, 'late_bl2': late_bl2,
                'missing_bl1': missing_bl1, 'missing_bl2': missing_bl2,
                'nachfrist': dict(nf) if nf else None,
                'nachfrist_active': bool(nf and not nf['revoked_at']),
            })

    # ── Volltreffer-König ────────────────────────────────────────
    vk_scores = sorted(scores, key=lambda s: s['volltreffer'] or 0, reverse=True)
    max_vt = vk_scores[0]['volltreffer'] if vk_scores else 0
    volltreffer_koenige = [s for s in vk_scores if (s['volltreffer'] or 0) == max_vt and max_vt > 0]

    # ── Größter Aufsteiger / Absteiger ──────────────────────────
    # Vergleich: Ranking-Snapshot Spieltag 1 vs. Endstand
    first_snap = db.execute(
        """SELECT user_id, rank FROM ranking_snapshots
           WHERE season_id=? ORDER BY matchday ASC LIMIT 1""", (season['id'],)
    ).fetchone()
    aufsteiger = absteiger = None
    if first_snap:
        early_ranks = {r['user_id']: r['rank'] for r in db.execute(
            """SELECT user_id, rank FROM ranking_snapshots
               WHERE season_id=? AND matchday=(
                   SELECT MIN(matchday) FROM ranking_snapshots WHERE season_id=?)""",
            (season['id'], season['id'])).fetchall()}
        final_ranks = {s['id']: i+1 for i, s in enumerate(scores)}
        best_gain = worst_gain = 0
        for s in scores:
            uid = s['id']
            if uid in early_ranks and uid in final_ranks:
                gain = early_ranks[uid] - final_ranks[uid]  # positiv = aufgestiegen
                name = s['display_name'] or s['username']
                if gain > best_gain:
                    best_gain = gain
                    aufsteiger = {'name': name, 'gain': gain,
                                  'from': early_ranks[uid], 'to': final_ranks[uid]}
                if gain < worst_gain:
                    worst_gain = gain
                    absteiger = {'name': name, 'gain': gain,
                                 'from': early_ranks[uid], 'to': final_ranks[uid]}

    return render_template('rueckblick.html',
        season=season, scores=scores, standings=standings,
        highlights=highlights_rows, team_stats=team_stats,
        angsthasen=angsthasen_results, has_prev=has_prev,
        ewige_rows=ewige_rows, ewige_detail=ewige_detail, seasons_all=seasons_all,
        wildcard_id=get_wildcard_id(scores),
        wildcard_bl1_ids=wildcard_bl1_ids, wildcard_bl2_ids=wildcard_bl2_ids,
        season_badges=season_badges, badge_defs=BADGE_DEFS,
        max_dev_map=max_dev_map,
        zu_spaet=zu_spaet, volltreffer_koenige=volltreffer_koenige,
        aufsteiger=aufsteiger, absteiger=absteiger,
        leagues=LEAGUES, max_score=MAX_SCORE,
        now_local=local_now_str())
  except Exception:
    app.logger.exception('Rueckblick Fehler')
    return render_template('errors/500.html'), 500

# ── Team-Statistik ───────────────────────────
@app.route('/teamstatistik')
@login_required
def teamstatistik():
    season = get_active_season()
    if not season or not season['season_started']:
        flash('Statistik ist erst nach Saisonbeginn verfügbar.', 'info')
        return redirect(url_for('dashboard'))
    db  = get_db()
    sid = season['id']
    n_users = db.execute(
        'SELECT COUNT(DISTINCT user_id) FROM predictions WHERE season_id=?', (sid,)
    ).fetchone()[0]

    def team_stats(league):
        return db.execute(
            """SELECT t.id, t.name, t.short_name, t.logo_url,
                      COALESCE(s.current_rank, 0) as actual_rank,
                      COUNT(p.id)                  as tipper_count,
                      AVG(p.predicted_rank)         as avg_tip,
                      MIN(p.predicted_rank)         as min_tip,
                      MAX(p.predicted_rank)         as max_tip,
                      SUM(CASE WHEN p.predicted_rank=1  THEN 1 ELSE 0 END) as tips_rank1,
                      SUM(CASE WHEN p.predicted_rank<=3 THEN 1 ELSE 0 END) as tips_top3,
                      SUM(CASE WHEN p.predicted_rank>=16 THEN 1 ELSE 0 END) as tips_absteiger,
                      SUM(CASE WHEN p.predicted_rank=COALESCE(s.current_rank,0)
                               THEN 1 ELSE 0 END) as volltreffer
               FROM predictions p
               JOIN teams t ON p.team_id=t.id
               LEFT JOIN standings s ON s.team_id=t.id AND s.season_id=p.season_id
               WHERE t.season_id=? AND t.league=?
               GROUP BY t.id
               ORDER BY COALESCE(s.current_rank, 99)""",
            (sid, league)
        ).fetchall()

    # Umstrittenste Teams: größte Streuung der Tipps
    contested = db.execute(
        """SELECT t.name, t.short_name, t.logo_url, t.league,
                  COALESCE(s.current_rank,0) as actual_rank,
                  AVG(p.predicted_rank) as avg_tip,
                  MIN(p.predicted_rank) as min_tip,
                  MAX(p.predicted_rank) as max_tip,
                  MAX(p.predicted_rank)-MIN(p.predicted_rank) as spread,
                  COUNT(p.id) as tipper_count
           FROM predictions p
           JOIN teams t ON p.team_id=t.id
           LEFT JOIN standings s ON s.team_id=t.id AND s.season_id=p.season_id
           WHERE t.season_id=?
           GROUP BY t.id HAVING tipper_count >= 2
           ORDER BY spread DESC LIMIT 5""",
        (sid,)
    ).fetchall()

    # Konsensus-Teams: kleinste Streuung
    consensus = db.execute(
        """SELECT t.name, t.short_name, t.logo_url, t.league,
                  COALESCE(s.current_rank,0) as actual_rank,
                  AVG(p.predicted_rank) as avg_tip,
                  MIN(p.predicted_rank) as min_tip,
                  MAX(p.predicted_rank) as max_tip,
                  MAX(p.predicted_rank)-MIN(p.predicted_rank) as spread,
                  COUNT(p.id) as tipper_count
           FROM predictions p
           JOIN teams t ON p.team_id=t.id
           LEFT JOIN standings s ON s.team_id=t.id AND s.season_id=p.season_id
           WHERE t.season_id=?
           GROUP BY t.id HAVING tipper_count >= 2
           ORDER BY spread ASC, avg_tip ASC LIMIT 5""",
        (sid,)
    ).fetchall()

    return render_template('teamstatistik.html',
        season=season, n_users=n_users,
        stats_bl1=team_stats('bl1'), stats_bl2=team_stats('bl2'),
        contested=contested, consensus=consensus,
        leagues=LEAGUES)

# ── Spieltag-Kalender ────────────────────────────────────────

# ── Web Push ─────────────────────────────────────────────────
@app.route('/push/vapid-public-key')
@login_required
def push_vapid_key():
    return jsonify({'key': VAPID_PUBLIC_KEY})

@app.route('/push/renew', methods=['POST'])
def push_renew():
    """Subscription-Erneuerung ohne Session (für SW pushsubscriptionchange).
    Authentifizierung über den alten Endpoint — nur bekannte Endpoints werden erneuert."""
    data     = request.json or {}
    endpoint = data.get('endpoint','')
    keys     = data.get('keys', {})
    old_ep   = data.get('old_endpoint','')
    if not endpoint or not keys.get('p256dh') or not keys.get('auth') or not old_ep:
        return jsonify({'ok': False, 'error': 'Fehlende Felder'})
    db = get_db()
    # Alten Endpoint finden — damit verifizieren wir die Identität
    old_sub = db.execute(
        'SELECT * FROM push_subscriptions WHERE endpoint=?', (old_ep,)
    ).fetchone()
    if not old_sub:
        return jsonify({'ok': False, 'error': 'Unbekannter alter Endpoint'})
    # Alte löschen, neue eintragen
    db.execute('DELETE FROM push_subscriptions WHERE endpoint=?', (old_ep,))
    db.execute(
        'INSERT OR REPLACE INTO push_subscriptions (user_id,endpoint,p256dh,auth) VALUES (?,?,?,?)',
        (old_sub['user_id'], endpoint, keys['p256dh'], keys['auth'])
    )
    db.commit()
    app.logger.info(f'Push Subscription erneuert für user_id={old_sub["user_id"]}')
    return jsonify({'ok': True})

@app.route('/push/subscribe', methods=['POST'])
@login_required
def push_subscribe():
    data     = request.json or {}
    endpoint = data.get('endpoint','')
    keys     = data.get('keys', {})
    old_ep   = data.get('old_endpoint')  # Firefox Rotation: alter Endpoint
    if not endpoint or not keys.get('p256dh') or not keys.get('auth'):
        return jsonify({'ok': False})
    db = get_db()
    # Alten Endpoint löschen (Firefox Rotation) bevor neuer eingetragen wird
    if old_ep and old_ep != endpoint:
        db.execute('DELETE FROM push_subscriptions WHERE endpoint=?', (old_ep,))
    db.execute(
        'INSERT OR REPLACE INTO push_subscriptions (user_id,endpoint,p256dh,auth) VALUES (?,?,?,?)',
        (session['user_id'], endpoint, keys['p256dh'], keys['auth'])
    )
    db.commit()
    return jsonify({'ok': True})

@app.route('/push/unsubscribe', methods=['POST'])
@login_required
def push_unsubscribe():
    db = get_db()
    db.execute('DELETE FROM push_subscriptions WHERE user_id=?', (session['user_id'],))
    db.commit()
    return jsonify({'ok': True})

@app.route('/push/diagnose')
@admin_required
def push_diagnose():
    result = {}
    try:
        from pywebpush import webpush, WebPushException
        result['pywebpush'] = 'OK'
    except ImportError as e:
        result['pywebpush'] = f'FEHLER: {e}'
        return jsonify(result)

    result['vapid_public']  = VAPID_PUBLIC_KEY[:20] + '…'
    result['vapid_private'] = 'OK' if VAPID_PRIVATE_KEY else 'FEHLT'

    db   = get_db()
    subs = db.execute(
        'SELECT ps.*, u.username FROM push_subscriptions ps JOIN users u ON ps.user_id=u.id'
    ).fetchall()

    test_results = []
    for sub in subs:
        entry = {'user': sub['username'], 'endpoint': sub['endpoint'][:60]+'…'}
        try:
            parsed = urlparse(sub['endpoint'])
            claims = {**VAPID_CLAIMS, 'aud': f"{parsed.scheme}://{parsed.netloc}"}
            resp = webpush(
                subscription_info={"endpoint": sub['endpoint'],
                                   "keys": {"p256dh": sub['p256dh'], "auth": sub['auth']}},
                data=json.dumps({"title":"Tippcup Diagnose","body":f"Test für {sub['username']}","url":"/"}),
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims=claims,
                content_encoding="aes128gcm",
                ttl=86400,
            )
            entry['status'] = f'✓ OK (HTTP {resp.status_code if resp else "?"})'
        except Exception as e:
            resp_status = getattr(getattr(e,'response',None),'status_code','?')
            resp_body   = getattr(getattr(e,'response',None),'text','')[:150]
            entry['status'] = f'✗ {type(e).__name__} HTTP {resp_status}: {resp_body or str(e)[:120]}'
            if str(resp_status) in ('404','410'):
                db.execute('DELETE FROM push_subscriptions WHERE id=?', (sub['id'],))
                db.commit()
                entry['status'] += ' → Subscription gelöscht'
        test_results.append(entry)

    result['tests'] = test_results
    result['total'] = len(subs)
    return jsonify(result)

@app.route('/push/clear-all', methods=['GET', 'POST'])
@admin_required
def push_clear_all():
    """Löscht alle Subscriptions — nötig nach VAPID-Key-Wechsel."""
    db = get_db()
    db.execute('DELETE FROM push_subscriptions')
    db.commit()
    return jsonify({'ok': True, 'message': 'Alle Push-Subscriptions gelöscht. Bitte Push auf allen Geräten neu aktivieren.'})

@app.route('/push/test', methods=['POST'])
@login_required
def push_test():
    db  = get_db()
    sub = db.execute('SELECT * FROM push_subscriptions WHERE user_id=?',
                     (session['user_id'],)).fetchone()
    if not sub:
        return jsonify({'ok': False, 'error': 'Keine Subscription gefunden'})
    try:
        from pywebpush import webpush
        parsed = urlparse(sub['endpoint'])
        claims = {**VAPID_CLAIMS, 'aud': f"{parsed.scheme}://{parsed.netloc}"}
        webpush(
            subscription_info={
        "endpoint": sub['endpoint'],
        "keys": {"p256dh": sub['p256dh'], "auth": sub['auth']}
            },
            data=json.dumps({"title": "Tippcup 🏆", "body": "Push funktioniert!", "url": "/"}),
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims=claims,
        )
        return jsonify({'ok': True})
    except Exception as ex:
        app.logger.error(f'Push-Test Fehler: {ex}')
        return jsonify({'ok': False, 'error': str(ex)})

# ── Badges ───────────────────────────────────────────────────
@app.route('/badges')
@login_required
def badges():
    db      = get_db()
    season  = get_active_season()
    seasons = db.execute('SELECT * FROM seasons ORDER BY year DESC').fetchall()
    users   = db.execute(
        'SELECT id, username, display_name FROM users WHERE is_active=1 ORDER BY display_name'
    ).fetchall()
    # Alle Badges aller Spieler — dedupliziert (DISTINCT verhindert Anzeige-Duplikate)
    all_badges = db.execute(
        '''SELECT DISTINCT ub.user_id, ub.season_id, ub.badge_type,
                  u.display_name, u.username
           FROM user_badges ub JOIN users u ON ub.user_id=u.id
           ORDER BY ub.season_id DESC, u.display_name'''
    ).fetchall()
    return render_template('badges.html', season=season, seasons=seasons,
                           users=users, all_badges=all_badges, badge_defs=BADGE_DEFS)

@app.route('/admin/badges/assign', methods=['POST'])
@admin_required
def admin_assign_badges():
    sid = request.form.get('season_id') or (get_active_season() or {}).get('id')
    if sid:
        calculate_and_assign_badges(int(sid))
        flash('Abzeichen wurden neu berechnet.', 'success')
    return redirect(url_for('badges'))

# ── 6. Dark Mode ─────────────────────────────
@app.route('/toggle_theme', methods=['POST'])
@login_required
def toggle_theme():
    current = session.get('theme','light')
    session['theme'] = 'dark' if current == 'light' else 'light'
    return jsonify({'theme': session['theme']})

# ── 7. Sprache ───────────────────────────────
@app.context_processor
def inject_globals():
    """Globale Template-Variable: Theme."""
    return {'theme': session.get('theme', 'light')}


# ════════════════════════════════════════════════════════════
# ROUTEN – MEDIEN & EXTERNE LINKS
# ════════════════════════════════════════════════════════════

import uuid, mimetypes
from werkzeug.utils import secure_filename

ALLOWED_IMAGES = {'jpg','jpeg','png','gif','webp'}
ALLOWED_DOCS   = {'pdf','doc','docx','xls','xlsx','ppt','pptx','txt','zip'}
ALLOWED_VIDEOS = {'mp4','mov','avi','mkv','webm','m4v'}
ALLOWED_AUDIO  = {'mp3','ogg','wav','m4a','aac','flac','opus'}
ALLOWED_ALL    = ALLOWED_IMAGES | ALLOWED_DOCS | ALLOWED_VIDEOS | ALLOWED_AUDIO
MAX_FILE_MB    = 50
MAX_VIDEO_MB   = 200

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.',1)[1].lower() in ALLOWED_ALL

def file_type(filename):
    ext = filename.rsplit('.',1)[-1].lower() if '.' in filename else ''
    if ext in ALLOWED_IMAGES: return 'image'
    if ext in ALLOWED_VIDEOS: return 'video'
    if ext in ALLOWED_AUDIO:  return 'audio'
    return 'document'

def upload_dir():
    d = os.path.join(BASE_DIR, 'static', 'uploads')
    os.makedirs(d, exist_ok=True)
    return d

def _extract_zip_images(file_storage, folder_id, overwrite, db):
    """Entpackt alle Bilddateien aus einem ZIP-Upload und speichert sie einzeln."""
    import zipfile, io
    extracted = skipped = 0
    no_images = True
    try:
        data = file_storage.read()
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for member in zf.infolist():
                if member.is_dir():
                    continue
                # Nur Dateiname ohne Pfad-Anteil (kein Path-Traversal)
                orig = os.path.basename(member.filename.replace('\\', '/'))
                if not orig or orig.startswith('.'):
                    continue
                ext = orig.rsplit('.', 1)[-1].lower() if '.' in orig else ''
                if ext not in ALLOWED_IMAGES:
                    continue
                no_images = False
                safe_name = secure_filename(orig)
                if not safe_name:
                    continue
                # Größenprüfung (uncompressed)
                if member.file_size > MAX_FILE_MB * 1024 * 1024:
                    app.logger.debug(f'ZIP-Bild zu groß, übersprungen: {orig}')
                    continue
                # Duplikat-Check
                existing = db.execute(
                    'SELECT id, filename FROM media_files WHERE orig_name=? AND '
                    + ('folder_id=?' if folder_id else 'folder_id IS NULL'),
                    (safe_name, folder_id) if folder_id else (safe_name,)
                ).fetchone()
                if existing and not overwrite:
                    skipped += 1
                    continue
                # Bild schreiben
                img_data     = zf.read(member)
                new_filename = f'{uuid.uuid4().hex}.{ext}'
                tmp_path     = os.path.join(upload_dir(), new_filename)
                with open(tmp_path, 'wb') as out:
                    out.write(img_data)
                size = len(img_data)
                if existing and overwrite:
                    try: os.remove(os.path.join(upload_dir(), existing['filename']))
                    except Exception: pass
                    db.execute(
                        'UPDATE media_files SET filename=?,file_size=? WHERE id=?',
                        (new_filename, size, existing['id'])
                    )
                else:
                    db.execute(
                        'INSERT INTO media_files (folder_id,filename,orig_name,file_type,file_size)'
                        ' VALUES (?,?,?,?,?)',
                        (folder_id, new_filename, safe_name, 'image', size)
                    )
                extracted += 1
    except zipfile.BadZipFile:
        flash('ZIP-Datei ist beschädigt oder kein gültiges ZIP.', 'danger')
    except Exception as e:
        app.logger.warning(f'ZIP-Extraktion fehlgeschlagen: {e}')
        flash(f'ZIP-Extraktion fehlgeschlagen: {e}', 'danger')
    return extracted, skipped, no_images

@app.route('/medien')
@login_required
def medien():
    db = get_db()
    folders = db.execute(
        'SELECT * FROM media_folders ORDER BY created_at DESC, id DESC'
    ).fetchall()
    newest_folder = db.execute(
        'SELECT id FROM media_folders ORDER BY created_at DESC, id DESC LIMIT 1'
    ).fetchone()
    newest_folder_id = newest_folder['id'] if newest_folder else None
    # Dateien pro Ordner
    folder_files = {}
    for f in folders:
        folder_files[f['id']] = db.execute(
            'SELECT * FROM media_files WHERE folder_id=? ORDER BY sort_order, orig_name',
            (f['id'],)
        ).fetchall()
    # Dateien ohne Ordner
    unassigned = db.execute(
        'SELECT * FROM media_files WHERE folder_id IS NULL ORDER BY sort_order, orig_name'
    ).fetchall()
    # Externe Links
    ext_links = db.execute(
        'SELECT * FROM external_links WHERE is_active=1 ORDER BY sort_order, title'
    ).fetchall()
    # Statistik: Anzahl nach Typ / Dateiendung
    all_files = db.execute('SELECT file_type, orig_name FROM media_files').fetchall()
    media_stats = {'image': 0, 'video': 0, 'audio': 0, 'pdf': 0, 'word': 0, 'excel': 0, 'other_doc': 0}
    for _f in all_files:
        ft = _f['file_type']
        if ft == 'image':
            media_stats['image'] += 1
        elif ft == 'video':
            media_stats['video'] += 1
        elif ft == 'audio':
            media_stats['audio'] += 1
        elif ft == 'document':
            ext = _f['orig_name'].rsplit('.', 1)[-1].lower() if '.' in _f['orig_name'] else ''
            if ext == 'pdf':
                media_stats['pdf'] += 1
            elif ext in ('doc', 'docx'):
                media_stats['word'] += 1
            elif ext in ('xls', 'xlsx'):
                media_stats['excel'] += 1
            else:
                media_stats['other_doc'] += 1
    return render_template('medien.html',
        folders=folders, folder_files=folder_files,
        unassigned=unassigned, ext_links=ext_links,
        newest_folder_id=newest_folder_id, media_stats=media_stats)

@app.route('/medien/uploads/<filename>')
@login_required
def media_file(filename):
    """Geschützte Auslieferung von Upload-Dateien mit korrektem MIME-Type."""
    import mimetypes
    # MIME-Types für Browser-native Wiedergabe
    mime_map = {
        'mp4':'video/mp4','webm':'video/webm','mov':'video/quicktime',
        'avi':'video/x-msvideo','mkv':'video/x-matroska','m4v':'video/mp4',
        'mp3':'audio/mpeg','ogg':'audio/ogg','wav':'audio/wav',
        'm4a':'audio/mp4','aac':'audio/aac','flac':'audio/flac','opus':'audio/ogg',
    }
    ext = filename.rsplit('.',1)[-1].lower() if '.' in filename else ''
    mimetype = mime_map.get(ext) or mimetypes.guess_type(filename)[0]
    return send_from_directory(upload_dir(), filename, mimetype=mimetype)

# ── Admin: Medienverwaltung ───────────────────
@app.route('/admin/medien', methods=['GET','POST'])
@admin_required
def admin_medien():
    db = get_db()
    if request.method == 'POST':
        action = request.form.get('action','')

        if action == 'upload':
            folder_id = request.form.get('folder_id') or None
            overwrite = request.form.get('overwrite') == '1'
            files     = request.files.getlist('files')
            uploaded = skipped = overwritten = zip_extracted = 0
            for f in files:
                if not f or not f.filename: continue
                if not allowed_file(f.filename): continue
                ext      = f.filename.rsplit('.',1)[-1].lower()
                # ZIP → Bilder entpacken statt ZIP speichern
                if ext == 'zip':
                    z_ok, z_skip, z_empty = _extract_zip_images(f, folder_id, overwrite, db)
                    zip_extracted += z_ok
                    skipped       += z_skip
                    if z_empty:
                        flash(f'{secure_filename(f.filename)}: Keine Bilddateien im ZIP gefunden.', 'warning')
                    continue
                is_vid   = ext in ALLOWED_VIDEOS
                max_mb   = MAX_VIDEO_MB if is_vid else MAX_FILE_MB
                safe_name = secure_filename(f.filename)
                # Duplikat prüfen: gleicher Originaldateiname im gleichen Ordner
                existing = db.execute(
                    'SELECT id, filename FROM media_files WHERE orig_name=? AND '
                    + ('folder_id=?' if folder_id else 'folder_id IS NULL'),
                    (safe_name, folder_id) if folder_id else (safe_name,)
                ).fetchone()
                if existing and not overwrite:
                    skipped += 1
                    continue
                # Datei speichern
                new_filename = f'{uuid.uuid4().hex}.{ext}'
                tmp_path     = os.path.join(upload_dir(), new_filename)
                f.save(tmp_path)
                size = os.path.getsize(tmp_path)
                if size > max_mb * 1024 * 1024:
                    os.remove(tmp_path)
                    flash(f'{f.filename}: Datei zu groß (max. {max_mb} MB)', 'warning')
                    continue
                if existing and overwrite:
                    # Alte Datei vom Disk löschen, DB-Eintrag aktualisieren
                    try: os.remove(os.path.join(upload_dir(), existing['filename']))
                    except Exception as e:
                        app.logger.debug(f"Ignorierter Fehler: {e}")
                    db.execute(
                        'UPDATE media_files SET filename=?,file_size=? WHERE id=?',
                        (new_filename, size, existing['id'])
                    )
                    overwritten += 1
                else:
                    db.execute(
                        'INSERT INTO media_files (folder_id,filename,orig_name,file_type,file_size) VALUES (?,?,?,?,?)',
                        (folder_id, new_filename, safe_name, file_type(f.filename), size)
                    )
                    uploaded += 1
            db.commit()
            parts = []
            if uploaded:       parts.append(f'{uploaded} hochgeladen')
            if zip_extracted:  parts.append(f'{zip_extracted} Bild(er) aus ZIP extrahiert')
            if overwritten:    parts.append(f'{overwritten} überschrieben')
            if skipped:        parts.append(f'{skipped} übersprungen (bereits vorhanden)')
            flash(', '.join(parts) + '.' if parts else 'Keine Dateien verarbeitet.', 'success' if not skipped else 'warning')

        elif action == 'add_folder':
            name = request.form.get('name','').strip()
            desc = request.form.get('description','').strip()
            if name:
                db.execute('INSERT INTO media_folders (name,description) VALUES (?,?)', (name, desc))
                db.commit()
                flash(f'Ordner „{name}" erstellt.', 'success')

        elif action == 'edit_folder':
            fid  = request.form.get('folder_id')
            name = request.form.get('name','').strip()
            desc = request.form.get('description','').strip()
            if fid and name:
                db.execute('UPDATE media_folders SET name=?,description=? WHERE id=?',(name,desc,fid))
                db.commit()
                flash('Ordner aktualisiert.','success')

        elif action == 'delete_folder':
            fid = request.form.get('folder_id')
            # Dateien in diesen Ordner → keinem Ordner zuweisen
            db.execute('UPDATE media_files SET folder_id=NULL WHERE folder_id=?',(fid,))
            db.execute('DELETE FROM media_folders WHERE id=?',(fid,))
            db.commit()
            flash('Ordner gelöscht (Dateien bleiben erhalten).','success')

        elif action == 'delete_file':
            fid  = request.form.get('file_id')
            row  = db.execute('SELECT filename FROM media_files WHERE id=?',(fid,)).fetchone()
            if row:
                try: os.remove(os.path.join(upload_dir(), row['filename']))
                except Exception as e:
                    app.logger.debug(f"Ignorierter Fehler: {e}")
                db.execute('DELETE FROM media_files WHERE id=?',(fid,))
                db.commit()
                flash('Datei gelöscht.','success')

        elif action == 'bulk_move':
            folder_id = request.form.get('folder_id') or None
            for fid in request.form.getlist('file_ids'):
                db.execute('UPDATE media_files SET folder_id=? WHERE id=?',(folder_id,fid))
            db.commit()
            flash(f'{len(request.form.getlist("file_ids"))} Datei(en) verschoben.','success')

        elif action == 'move_file':
            file_id   = request.form.get('file_id')
            folder_id = request.form.get('folder_id') or None
            overwrite = request.form.get('overwrite') == '1'
            if file_id:
                file_row = db.execute('SELECT * FROM media_files WHERE id=?', (file_id,)).fetchone()
                if file_row:
                    # Duplikat prüfen: gleicher Name im Zielordner?
                    dupe = db.execute(
                        'SELECT id FROM media_files WHERE orig_name=? AND id!=? AND '
                        + ('folder_id=?' if folder_id else 'folder_id IS NULL'),
                        (file_row['orig_name'], file_id, folder_id) if folder_id
                        else (file_row['orig_name'], file_id)
                    ).fetchone()
                    if dupe and not overwrite:
                        flash(f'⚠️ „{file_row["orig_name"]}" existiert bereits im Zielordner. '
                              f'Bitte „Überschreiben" bestätigen.', 'warning')
                        # Redirect mit overwrite-Flag-Hint zurück
                        return redirect(url_for('admin_medien') + '?dupe_file=' + str(file_id)
                                        + '&dupe_folder=' + str(folder_id or ''))
                    if dupe and overwrite:
                        # Altes Duplikat löschen
                        old = db.execute('SELECT filename FROM media_files WHERE id=?', (dupe['id'],)).fetchone()
                        if old:
                            try: os.remove(os.path.join(upload_dir(), old['filename']))
                            except Exception as e:
                                app.logger.debug(f"Ignorierter Fehler: {e}")
                        db.execute('DELETE FROM media_files WHERE id=?', (dupe['id'],))
                    db.execute('UPDATE media_files SET folder_id=? WHERE id=?', (folder_id, file_id))
                    db.commit()

        elif action == 'bulk_delete':
            ids = request.form.getlist('file_ids')
            for fid in ids:
                row = db.execute('SELECT filename FROM media_files WHERE id=?',(fid,)).fetchone()
                if row:
                    try: os.remove(os.path.join(upload_dir(), row['filename']))
                    except Exception as e:
                        app.logger.debug(f"Ignorierter Fehler: {e}")
                    db.execute('DELETE FROM media_files WHERE id=?',(fid,))
            db.commit()
            flash(f'{len(ids)} Datei(en) gelöscht.','success')

        elif action == 'add_link':
            db.execute(
                'INSERT INTO external_links (title,url,description,icon) VALUES (?,?,?,?)',
                (request.form.get('title','').strip(), request.form.get('url','').strip(),
                 request.form.get('description','').strip(), request.form.get('icon','bi-link-45deg'))
            )
            db.commit(); flash('Link hinzugefügt.','success')

        elif action == 'edit_link':
            lid = request.form.get('link_id')
            db.execute(
                'UPDATE external_links SET title=?,url=?,description=?,icon=?,is_active=? WHERE id=?',
                (request.form.get('title','').strip(), request.form.get('url','').strip(),
                 request.form.get('description','').strip(),
                 request.form.get('icon','bi-link-45deg'),
                 1 if request.form.get('is_active') else 0, lid)
            )
            db.commit(); flash('Link aktualisiert.','success')

        elif action == 'delete_link':
            db.execute('DELETE FROM external_links WHERE id=?',(request.form.get('link_id'),))
            db.commit(); flash('Link gelöscht.','success')

        return redirect(url_for('admin_medien'))

    folders   = db.execute('SELECT * FROM media_folders ORDER BY sort_order,name').fetchall()
    all_files = db.execute(
        """SELECT mf.*, fo.name as folder_name
           FROM media_files mf
           LEFT JOIN media_folders fo ON mf.folder_id=fo.id
           ORDER BY fo.name NULLS LAST, mf.orig_name""",
    ).fetchall()
    links      = db.execute('SELECT * FROM external_links ORDER BY sort_order,title').fetchall()
    total_size = sum(f['file_size'] for f in all_files)
    return render_template('admin/medien.html',
        folders=folders, all_files=all_files, links=links,
        total_size=total_size, max_mb=MAX_FILE_MB)

# ── Admin: Externe Links ──────────────────────
@app.route('/admin/links', methods=['GET','POST'])
@admin_required
def admin_links():
    db = get_db()
    if request.method == 'POST':
        action = request.form.get('action','')
        if action == 'add':
            db.execute(
                'INSERT INTO external_links (title,url,description,icon) VALUES (?,?,?,?)',
                (request.form.get('title','').strip(),
                 request.form.get('url','').strip(),
                 request.form.get('description','').strip(),
                 request.form.get('icon','bi-link-45deg').strip())
            )
            db.commit(); flash('Link hinzugefügt.','success')
        elif action == 'edit':
            lid = request.form.get('link_id')
            db.execute(
                'UPDATE external_links SET title=?,url=?,description=?,icon=?,is_active=? WHERE id=?',
                (request.form.get('title','').strip(),
                 request.form.get('url','').strip(),
                 request.form.get('description','').strip(),
                 request.form.get('icon','bi-link-45deg').strip(),
                 1 if request.form.get('is_active') else 0, lid)
            )
            db.commit(); flash('Link aktualisiert.','success')
        elif action == 'delete':
            db.execute('DELETE FROM external_links WHERE id=?',(request.form.get('link_id'),))
            db.commit(); flash('Link gelöscht.','success')
        return redirect(url_for('admin_links'))
    links = db.execute('SELECT * FROM external_links ORDER BY sort_order,title').fetchall()
    return render_template('admin/links.html', links=links)


# ════════════════════════════════════════════════════════════
# ROUTEN – TELEGRAM & WEB PUSH
# ════════════════════════════════════════════════════════════


@app.route('/admin/telegram', methods=['GET','POST'])
@admin_required
def admin_telegram():
    db = get_db()
    if request.method == 'POST':
        action = request.form.get('action','save')

        if action == 'save':
            token   = request.form.get('telegram_token','').strip()
            chat_id = request.form.get('telegram_chat_id','').strip()
            link    = request.form.get('telegram_group_link','').strip()
            db.execute("INSERT OR REPLACE INTO config (key,value) VALUES ('telegram_token',?)", (token,))
            db.execute("INSERT OR REPLACE INTO config (key,value) VALUES ('telegram_chat_id',?)", (chat_id,))
            db.execute("INSERT OR REPLACE INTO config (key,value) VALUES ('telegram_group_link',?)", (link,))
            # Benachrichtigungs-Einstellungen
            tg.save_settings(db, {
                'notify_rangliste': bool(request.form.get('notify_rangliste')),
                'notify_highlight': bool(request.form.get('notify_highlight')),
                'notify_deadline':  bool(request.form.get('notify_deadline')),
            })
            flash('Telegram-Einstellungen gespeichert.', 'success')

        elif action == 'test':
            ok, err = tg.test_connection(db)
            if ok:
                flash('✅ Test-Nachricht erfolgreich gesendet!', 'success')
            else:
                flash(f'❌ Fehler: {err}', 'danger')

        elif action == 'set_webhook':
            token = db.execute("SELECT value FROM config WHERE key='telegram_token'").fetchone()
            if token and token['value']:
                base  = request.url_root.rstrip('/')
                hook  = f'{base}/telegram/webhook/{token["value"]}'
                result = tg.set_webhook(token['value'], hook)
                if result.get('ok'):
                    flash(f'✅ Webhook registriert: {hook}', 'success')
                else:
                    flash(f'❌ Webhook-Fehler: {result.get("description","")}', 'danger')
            else:
                flash('Erst Token speichern.', 'danger')

        elif action == 'delete_webhook':
            token = db.execute("SELECT value FROM config WHERE key='telegram_token'").fetchone()
            if token and token['value']:
                tg.delete_webhook(token['value'])
                flash('Webhook entfernt.', 'info')

        elif action == 'notify_deadline':
            season = get_active_season()
            if season:
                # Wer hat noch nicht getippt?
                all_users = db.execute(
                    'SELECT id, display_name, username FROM users WHERE is_active=1'
                ).fetchall()
                tipped = {r['user_id'] for r in db.execute(
                    'SELECT DISTINCT user_id FROM predictions WHERE season_id=?', (season['id'],)
                ).fetchall()}
                missing = [u['display_name'] or u['username']
                           for u in all_users if u['id'] not in tipped]
                ok, err = tg.notify_deadline_reminder(db, season['name'], 24, missing)
                if ok:
                    flash(f'✅ Erinnerung gesendet ({len(missing)} noch ohne Tipp).', 'success')
                else:
                    flash(f'❌ Fehler: {err}', 'danger')

        return redirect(url_for('admin_telegram'))

    # GET: Konfiguration laden
    def cfg(key):
        row = db.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
        return row['value'] if row else ''

    token     = cfg('telegram_token')
    bot_info  = None
    if token:
        bot_info = tg.get_bot_info(token)

    settings = tg.get_settings(db)
    return render_template('admin/telegram.html',
        telegram_token=token,
        telegram_chat_id=cfg('telegram_chat_id'),
        telegram_group_link=cfg('telegram_group_link'),
        bot_info=bot_info,
        settings=settings,
        season=get_active_season())


@app.route('/telegram/webhook/<token>', methods=['POST'])
def telegram_webhook(token):
    """Empfängt Bot-Updates von Telegram."""
    db = get_db()
    stored = db.execute("SELECT value FROM config WHERE key='telegram_token'").fetchone()
    if not stored or stored['value'] != token:
        return '', 403
    try:
        update   = request.get_json(force=True)
        base_url = request.url_root.rstrip('/')
        tg.handle_bot_command(db, update, base_url)
    except Exception as e:
        app.logger.debug(f"Ignorierter Fehler: {e}")
    return '', 200


# ── Hilfe / Benutzerhandbuch ─────────────────────────────────


# ════════════════════════════════════════════════════════════
# ROUTEN – ARCHIV (HISTORISCHE DATEN)
# ════════════════════════════════════════════════════════════

@app.route('/archiv')
@login_required
def archiv():
    try:
        db = get_db()
        season_param = request.args.get('season', '')
        tipper_param = request.args.get('tipper', '')
        seasons = [r['season'] for r in db.execute(
            'SELECT DISTINCT season FROM legacy_results ORDER BY season DESC'
        ).fetchall()]
        if not seasons:
            return render_template('archiv.html', seasons=[], rows=[], season_param='',
                                   tipper_param='', all_tippers=[],
                                   max_abw1=None, max_abw2=None)
        if not season_param and seasons:
            season_param = seasons[0]
        rows = db.execute(
            '''SELECT lr.*, u.display_name, u.username
               FROM legacy_results lr
               LEFT JOIN users u ON lr.user_id = u.id
               WHERE lr.season = ?
               ORDER BY lr.rang ASC''',
            (season_param,)
        ).fetchall()
        all_tippers = [r['tipper'] for r in db.execute(
            'SELECT DISTINCT tipper FROM legacy_results ORDER BY tipper'
        ).fetchall()]
        vals1 = [r['maxabw_1l'] for r in rows if r['maxabw_1l'] is not None]
        vals2 = [r['maxabw_2l'] for r in rows if r['maxabw_2l'] is not None]
        max_abw1 = max(vals1) if vals1 else None
        max_abw2 = max(vals2) if vals2 else None
        return render_template('archiv.html', seasons=seasons, rows=rows,
                               season_param=season_param, tipper_param=tipper_param,
                               all_tippers=all_tippers,
                               max_abw1=max_abw1, max_abw2=max_abw2)
    except Exception:
        app.logger.exception('Archiv Fehler')
        return render_template('errors/500.html'), 500


@app.route('/archiv/karriere/<tipper_name>')
@login_required
def archiv_karriere(tipper_name):
    db = get_db()
    rows = db.execute(
        '''SELECT lr.*, u.display_name, u.username
           FROM legacy_results lr
           LEFT JOIN users u ON lr.user_id = u.id
           WHERE lr.tipper = ?
           ORDER BY lr.season ASC''',
        (tipper_name,)
    ).fetchall()
    if not rows:
        flash(f'Keine Daten für Tipper „{tipper_name}".', 'warning')
        return redirect(url_for('archiv'))
    seasons_count = len(rows)
    best = min(rows, key=lambda r: r['rang'])
    wins = sum(1 for r in rows if r['rang'] == 1)
    top3 = sum(1 for r in rows if r['rang'] <= 3)
    vals1 = [r['maxabw_1l'] for r in rows if r['maxabw_1l'] is not None]
    vals2 = [r['maxabw_2l'] for r in rows if r['maxabw_2l'] is not None]
    max_abw1 = max(vals1) if vals1 else None
    max_abw2 = max(vals2) if vals2 else None
    return render_template('archiv_karriere.html',
                           tipper=tipper_name, rows=rows,
                           seasons_count=seasons_count, best=best,
                           wins=wins, top3=top3,
                           max_abw1=max_abw1, max_abw2=max_abw2)


@app.route('/archiv/ewige-tabelle')
@login_required
def archiv_ewige_tabelle():
    db = get_db()
    # Alle Saisons chronologisch
    seasons = [r['season'] for r in db.execute(
        'SELECT DISTINCT season FROM legacy_results ORDER BY season ASC'
    ).fetchall()]
    # Gesamtwert = AVG(rang * 100.0 / total_teilnehmer_in_saison)
    # Entspricht dem normierten Rang-Prozentsatz der Referenz-Tabelle (kleiner = besser)
    tipper_rows = db.execute(
        '''SELECT lr.tipper, u.display_name, u.username, u.id as user_id,
                  COUNT(*) as teilnahmen,
                  AVG(lr.rang * 100.0 / sm.total) as gesamt,
                  MIN(lr.rang) as best_rang,
                  SUM(CASE WHEN lr.rang=1 THEN 1 ELSE 0 END) as siege
           FROM legacy_results lr
           LEFT JOIN users u ON lr.user_id = u.id
           JOIN (SELECT season, MAX(rang) as total
                 FROM legacy_results GROUP BY season) sm
             ON lr.season = sm.season
           GROUP BY lr.tipper
           ORDER BY gesamt ASC'''
    ).fetchall()
    # Rang-Nummer vergeben
    tipper_rows_ranked = []
    for i, r in enumerate(tipper_rows, 1):
        tipper_rows_ranked.append({'rang': i, **dict(r)})
    # Teilnehmerzahl pro Saison (für normierte Zellwerte)
    season_totals = {r['season']: r['total'] for r in db.execute(
        'SELECT season, MAX(rang) as total FROM legacy_results GROUP BY season'
    ).fetchall()}
    # Normierter Rang-Prozentsatz pro Tipper+Saison (= Zellwert der Referenztabelle)
    detail = {}
    for r in db.execute(
        'SELECT tipper, season, rang FROM legacy_results'
    ).fetchall():
        total = season_totals.get(r['season'], 1)
        pct = round(r['rang'] * 100.0 / total, 2)
        detail.setdefault(r['tipper'], {})[r['season']] = {
            'pct': pct, 'rang': r['rang'], 'total': total
        }
    return render_template('archiv_ewige_tabelle.html',
                           seasons=seasons, tipper_rows=tipper_rows_ranked,
                           detail=detail)


@app.route('/archiv/angsthasen')
@login_required
def archiv_angsthasen():
    db = get_db()
    season_param = request.args.get('season', '')
    seasons = [r['season'] for r in db.execute(
        'SELECT DISTINCT season FROM legacy_angsthasen ORDER BY season DESC'
    ).fetchall()]
    if not seasons:
        return render_template('archiv_angsthasen.html', seasons=[], rows=[],
                               season_param='', meta=None)
    if not season_param:
        season_param = seasons[0]
    rows = db.execute(
        '''SELECT la.*, u.display_name, u.username
           FROM legacy_angsthasen la
           LEFT JOIN users u ON la.user_id = u.id
           WHERE la.season = ?
           ORDER BY la.rang ASC''',
        (season_param,)
    ).fetchall()
    # Meta (n/k) aus erster Zeile
    meta = dict(rows[0]) if rows else None
    return render_template('archiv_angsthasen.html',
                           seasons=seasons, rows=rows,
                           season_param=season_param, meta=meta)


@app.route('/archiv/angsthasen/karriere/<name>')
@login_required
def archiv_angsthasen_karriere(name):
    db = get_db()
    rows = db.execute(
        '''SELECT la.*, u.display_name, u.username
           FROM legacy_angsthasen la
           LEFT JOIN users u ON la.user_id = u.id
           WHERE la.name = ?
           ORDER BY la.season ASC''',
        (name,)
    ).fetchall()
    if not rows:
        flash(f'Keine Angsthasen-Daten für „{name}".', 'warning')
        return redirect(url_for('archiv_angsthasen'))
    best = min(rows, key=lambda r: r['rang'])
    wins = sum(1 for r in rows if r['rang'] == 1)
    avg_abw = sum(r['abw_ges'] for r in rows if r['abw_ges']) / len(rows)
    return render_template('archiv_angsthasen_karriere.html',
                           name=name, rows=rows, best=best,
                           wins=wins, avg_abw=avg_abw)
@app.route('/admin/archiv/import', methods=['GET', 'POST'])
@admin_required
def admin_archiv_import():
    db = get_db()
    msg = None
    if request.method == 'POST':
        action = request.form.get('action', '')

        if action == 'import_csv':
            import csv, io
            try:
                f = request.files.get('csvfile')
                if not f:
                    flash('Keine Datei ausgewählt.', 'danger')
                    return redirect(url_for('admin_archiv_import'))
                content = f.read().decode('utf-8-sig')
                reader = csv.DictReader(io.StringIO(content), delimiter=';')
                name_map = {r['csv_name']: r['user_id'] for r in
                            db.execute('SELECT csv_name, user_id FROM legacy_name_map').fetchall()}
                inserted = updated = 0

                def _f(v):
                    try: return float(str(v).replace(',', '.')) if str(v).strip() else None
                    except: return None
                def _i(v):
                    try: return int(float(str(v))) if str(v).strip() else None
                    except: return None

                for row in reader:
                    season  = row['Saison'].strip()
                    tipper  = row['Tipper'].strip()
                    user_id = name_map.get(tipper)
                    rang    = _i(row.get('Rang', '')) or 99  # NOT NULL guard
                    existing = db.execute(
                        'SELECT id FROM legacy_results WHERE season=? AND tipper=?',
                        (season, tipper)
                    ).fetchone()
                    if existing:
                        db.execute('''UPDATE legacy_results SET
                            rang=?, user_id=?, spieltag_1l=?, spieltag_2l=?,
                            abw_1l=?, stdabw_1l=?, maxabw_1l=?, treffer_1l=?,
                            abw_2l=?, stdabw_2l=?, maxabw_2l=?, treffer_2l=?,
                            abw_ges=?, stdabw_ges=?, maxabw_ges=?, treffer_ges=?
                            WHERE season=? AND tipper=?''',
                            (rang, user_id,
                             _i(row['Spieltag_1L']), _i(row['Spieltag_2L']),
                             _f(row['Abw_1L']), _f(row['Stdabw_1L']), _f(row['MaxAbw_1L']), _i(row['Treffer_1L']),
                             _f(row['Abw_2L']), _f(row['Stdabw_2L']), _f(row['MaxAbw_2L']), _i(row['Treffer_2L']),
                             _f(row['Abw_Ges']), _f(row['Stdabw_Ges']), _f(row['MaxAbw_Ges']), _i(row['Treffer_Ges']),
                             season, tipper))
                        updated += 1
                    else:
                        db.execute('''INSERT INTO legacy_results
                            (season, rang, tipper, user_id, spieltag_1l, spieltag_2l,
                             abw_1l, stdabw_1l, maxabw_1l, treffer_1l,
                             abw_2l, stdabw_2l, maxabw_2l, treffer_2l,
                             abw_ges, stdabw_ges, maxabw_ges, treffer_ges)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                            (season, rang, tipper, user_id,
                             _i(row['Spieltag_1L']), _i(row['Spieltag_2L']),
                             _f(row['Abw_1L']), _f(row['Stdabw_1L']), _f(row['MaxAbw_1L']), _i(row['Treffer_1L']),
                             _f(row['Abw_2L']), _f(row['Stdabw_2L']), _f(row['MaxAbw_2L']), _i(row['Treffer_2L']),
                             _f(row['Abw_Ges']), _f(row['Stdabw_Ges']), _f(row['MaxAbw_Ges']), _i(row['Treffer_Ges'])))
                        inserted += 1
                db.commit()
                flash(f'Import abgeschlossen: {inserted} neu, {updated} aktualisiert.', 'success')
            except Exception:
                app.logger.exception('Import-Fehler (Abschlusstabellen)')
                flash('Import fehlgeschlagen. Details siehe Server-Log.', 'danger')
            return redirect(url_for('admin_archiv_import'))

        elif action == 'delete_season':
            season_del = request.form.get('season_name', '').strip()
            if season_del:
                count = db.execute(
                    'SELECT COUNT(*) FROM legacy_results WHERE season=?', (season_del,)
                ).fetchone()[0]
                db.execute('DELETE FROM legacy_results WHERE season=?', (season_del,))
                db.commit()
                flash(f'Saison „{season_del}" gelöscht ({count} Einträge).', 'success')
            return redirect(url_for('admin_archiv_import'))

        elif action == 'save_map':
            all_names = [r['tipper'] for r in
                         db.execute('SELECT DISTINCT tipper FROM legacy_results').fetchall()]
            for csv_name in all_names:
                uid_str = request.form.get(f'map_{csv_name}', '')
                uid = int(uid_str) if uid_str.isdigit() else None
                db.execute(
                    'INSERT OR REPLACE INTO legacy_name_map (csv_name, user_id) VALUES (?,?)',
                    (csv_name, uid)
                )
                db.execute(
                    'UPDATE legacy_results SET user_id=? WHERE tipper=?', (uid, csv_name)
                )
                db.execute(
                    'UPDATE legacy_angsthasen SET user_id=? WHERE name=?', (uid, csv_name)
                )
            db.commit()
            flash('Name-Mapping gespeichert.', 'success')
            return redirect(url_for('admin_archiv_import'))

        elif action == 'import_angsthasen':
            import csv, io
            try:
                f = request.files.get('csvfile')
                if not f:
                    flash('Keine Datei ausgewählt.', 'danger')
                    return redirect(url_for('admin_archiv_import'))
                content = f.read().decode('utf-8-sig')
                reader = csv.DictReader(io.StringIO(content), delimiter=';')
                name_map = {r['csv_name']: r['user_id'] for r in
                            db.execute('SELECT csv_name, user_id FROM legacy_name_map').fetchall()}
                inserted = updated = 0
                def _i(v):
                    try: return int(float(str(v))) if str(v).strip() else None
                    except: return None
                def _f(v):
                    try: return float(str(v).replace(',', '.')) if str(v).strip() else None
                    except: return None
                for row in reader:
                    season  = row['Saison'].strip()
                    name    = row['Name'].strip()
                    user_id = name_map.get(name)
                    rang    = _i(row.get('Rang', '')) or 99
                    existing = db.execute(
                        'SELECT id FROM legacy_angsthasen WHERE season=? AND name=?',
                        (season, name)
                    ).fetchone()
                    vals = (rang, user_id,
                            _i(row.get('Abw_1L')), _i(row.get('Abw_2L')),
                            _i(row.get('Abw_Ges')), _f(row.get('Abw_Gew')),
                            _i(row.get('n_1L')), _i(row.get('k_1L')),
                            _i(row.get('n_2L')), _i(row.get('k_2L')))
                    if existing:
                        db.execute('''UPDATE legacy_angsthasen SET
                            rang=?, user_id=?, abw_1l=?, abw_2l=?,
                            abw_ges=?, abw_gew=?, n_1l=?, k_1l=?, n_2l=?, k_2l=?
                            WHERE season=? AND name=?''',
                            vals + (season, name))
                        updated += 1
                    else:
                        db.execute('''INSERT INTO legacy_angsthasen
                            (season, rang, name, user_id, abw_1l, abw_2l,
                             abw_ges, abw_gew, n_1l, k_1l, n_2l, k_2l)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                            (season, rang, name, user_id) + vals[2:])
                        inserted += 1
                db.commit()
                flash(f'Angsthasen-Import: {inserted} neu, {updated} aktualisiert.', 'success')
            except Exception:
                app.logger.exception('Import-Fehler (Angsthasen)')
                flash('Import fehlgeschlagen. Details siehe Server-Log.', 'danger')
            return redirect(url_for('admin_archiv_import'))

    # GET – Übersichtsseite
    try:
        count = db.execute('SELECT COUNT(*) FROM legacy_results').fetchone()[0]
        seasons_count = db.execute('SELECT COUNT(DISTINCT season) FROM legacy_results').fetchone()[0]
        angst_count = db.execute('SELECT COUNT(*) FROM legacy_angsthasen').fetchone()[0]
        angst_seasons = db.execute('SELECT COUNT(DISTINCT season) FROM legacy_angsthasen').fetchone()[0]
        all_names = [r['tipper'] for r in
                     db.execute('SELECT DISTINCT tipper FROM legacy_results ORDER BY tipper').fetchall()]
        users = db.execute(
            'SELECT id, username, display_name FROM users WHERE is_active=1 ORDER BY display_name, username'
        ).fetchall()
        name_map = {r['csv_name']: r['user_id'] for r in
                    db.execute('SELECT csv_name, user_id FROM legacy_name_map').fetchall()}
        available_seasons = [r['season'] for r in db.execute(
            'SELECT DISTINCT season FROM legacy_results ORDER BY season DESC'
        ).fetchall()]
        return render_template('admin/archiv_import.html',
                               count=count, seasons_count=seasons_count,
                               angst_count=angst_count, angst_seasons=angst_seasons,
                               all_names=all_names, users=users, name_map=name_map,
                               available_seasons=available_seasons)
    except Exception:
        app.logger.exception('Fehler in admin_archiv_import')
        return render_template('errors/500.html'), 500


@app.route('/tipp-status')
@login_required
def tipp_status():
    """Öffentliche Übersicht: Wer hat wann getippt, wer fehlt noch."""
    season = get_active_season()
    if not season:
        flash('Keine aktive Saison.', 'info')
        return redirect(url_for('dashboard'))
    db  = get_db()
    sid = season['id']

    # Alle aktiven User
    users = db.execute(
        'SELECT id, username, display_name FROM users WHERE is_active=1 ORDER BY display_name, username'
    ).fetchall()

    # Letzter Tipp-Zeitpunkt pro User UND Liga getrennt
    tip_times_bl1 = {r['user_id']: r['last_tip'] for r in db.execute(
        """SELECT p.user_id, MAX(p.updated_at) as last_tip
           FROM predictions p JOIN teams t ON p.team_id=t.id
           WHERE p.season_id=? AND t.league='bl1'
           GROUP BY p.user_id""", (sid,)
    ).fetchall()}
    tip_times_bl2 = {r['user_id']: r['last_tip'] for r in db.execute(
        """SELECT p.user_id, MAX(p.updated_at) as last_tip
           FROM predictions p JOIN teams t ON p.team_id=t.id
           WHERE p.season_id=? AND t.league='bl2'
           GROUP BY p.user_id""", (sid,)
    ).fetchall()}
    # Letzter Tipp-Zeitpunkt gesamt (für Anzeige)
    tip_times = {r['user_id']: r['last_tip'] for r in db.execute(
        'SELECT user_id, MAX(updated_at) as last_tip FROM predictions WHERE season_id=? GROUP BY user_id',
        (sid,)
    ).fetchall()}

    # Anzahl getippter Teams pro User (vollständig = 36 Teams)
    tip_counts = {r['user_id']: r['cnt'] for r in db.execute(
        'SELECT user_id, COUNT(*) as cnt FROM predictions WHERE season_id=? GROUP BY user_id',
        (sid,)
    ).fetchall()}

    # Deadlines
    dl_bl1 = season['deadline_bl1']
    dl_bl2 = season['deadline_bl2']

    rows = []
    for u in users:
        uid      = u['id']
        last_tip = tip_times.get(uid)
        cnt      = tip_counts.get(uid, 0)
        complete = cnt >= 36   # 18 BL1 + 18 BL2

        # Rechtzeitigkeit pro Liga separat prüfen
        last_bl1 = tip_times_bl1.get(uid)
        last_bl2 = tip_times_bl2.get(uid)
        on_time_bl1 = on_time_bl2 = None
        if dl_bl1 and last_bl1:
            try: on_time_bl1 = last_bl1 <= str(dl_bl1)
            except Exception: pass
        if dl_bl2 and last_bl2:
            try: on_time_bl2 = last_bl2 <= str(dl_bl2)
            except Exception: pass

        rows.append({
            'user':        u,
            'last_tip':    last_tip,
            'count':       cnt,
            'complete':    complete,
            'on_time_bl1': on_time_bl1,
            'on_time_bl2': on_time_bl2,
        })

    # Sortierung: erst vollständig abgegeben, dann fehlend; innerhalb nach Zeit
    rows.sort(key=lambda r: (not r['complete'], r['last_tip'] or '9999'))

    return render_template('tipp_status.html',
        season=season, rows=rows,
        dl_bl1=dl_bl1, dl_bl2=dl_bl2,
        total_users=len(users),
        done_count=sum(1 for r in rows if r['complete']))


@app.route('/hilfe')
@login_required
def hilfe():
    """Benutzerhandbuch direkt in der App."""
    sections = [
        {
            'icon': '🚀',
            'title': 'Quick-Start',
            'content': """
<p>Willkommen bei Tippcup! In wenigen Schritten bist du dabei:</p>
<h3>Schritt 1 – Einloggen</h3>
<p>Benutzername und Passwort erhältst du vom Administrator. Nach dem ersten Login unbedingt das Passwort unter <strong>Profil</strong> ändern.</p>
<h3>Schritt 2 – Tipp abgeben</h3>
<p>Klicke auf <strong>Mein Tipp</strong> und bringe alle 18 Mannschaften beider Bundesligen per Drag&nbsp;&amp;&nbsp;Drop in die gewünschte Reihenfolge. Dann <strong>Tipp speichern</strong>.</p>
<div class="alert alert-warning">⚠️ Tipps können nur vor dem Tippschluss abgegeben werden. Das genaue Datum siehst du auf der Startseite.</div>
<h3>Schritt 3 – Bestenliste verfolgen</h3>
<p>Unter <strong>Bestenliste</strong> siehst du die Rangliste aller Mitspieler. Die Punkte werden nach jedem Spieltag automatisch aktualisiert.</p>
<h3>Schritt 4 – Profil vervollständigen</h3>
<p>Unter <strong>Profil</strong> kannst du Spielernamen, vollen Namen, Lieblingsverein und E-Mail-Adresse eintragen. Vollen Namen und Lieblingsverein sehen andere als Tooltip beim Hover.</p>
"""
        },
        {
            'icon': '🧭',
            'title': 'Navigation & Oberfläche',
            'content': """
<table>
<tr><th>Menüpunkt</th><th>Inhalt</th></tr>
<tr><td>⚽ <strong>Startseite</strong></td><td>Live-Tabelle beider Bundesligen, Tippschluss-Countdown</td></tr>
<tr><td>📋 <strong>Mein Tipp</strong></td><td>Tipp abgeben oder bearbeiten</td></tr>
<tr><td>🏆 <strong>Bestenliste</strong></td><td>Rangliste aller Teilnehmer mit Punktestand</td></tr>
<tr><td>📊 <strong>Alle Tipps</strong></td><td>Tipps aller Mitspieler vergleichen</td></tr>
<tr><td>📈 <strong>Mehr →</strong></td><td>Verlauf, Angsthasen, Rückblick, Head-to-Head, Highlights, Abzeichen, Statistiken, Ewige Tabelle, Spieltage, Medien</td></tr>
<tr><td>💬 <strong>Telegram</strong></td><td>Link zur Telegram-Gruppe (wenn konfiguriert)</td></tr>
</table>
<h3>Dark Mode</h3>
<p>Oben rechts das 🌙/☀️-Symbol klicken um zwischen hellem und dunklem Design zu wechseln.</p>
<h3>Spielernamen-Tooltip</h3>
<p>Bewege die Maus über einen Spielernamen in der Bestenliste — ein Tooltip zeigt vollen Namen und Lieblingsverein (sofern im Profil eingetragen).</p>
"""
        },
        {
            'icon': '✏️',
            'title': 'Tipps abgeben',
            'content': """
<h3>So funktioniert der Tipp</h3>
<p>Zu Saisonbeginn tippt jeder die <strong>komplette Abschlusstabelle</strong> der 1. und 2. Bundesliga — alle 18 Mannschaften je Liga in der erwarteten Reihenfolge.</p>
<h3>Schritt-für-Schritt</h3>
<ul>
<li><strong>Mein Tipp</strong> im Menü aufrufen</li>
<li>Mannschaften per <strong>Drag &amp; Drop</strong> in die gewünschte Reihenfolge bringen</li>
<li>Alternativ: Rang direkt in das Zahlenfeld eingeben</li>
<li><strong>Tipp speichern</strong> klicken</li>
<li>Grüne Bestätigungsmeldung abwarten</li>
</ul>
<h3>Tipp bearbeiten</h3>
<p>Solange die Tippabgabe offen ist, kann der Tipp beliebig oft geändert werden.</p>
<h3>Tipp anderer Spieler ansehen</h3>
<p>Unter <strong>Alle Tipps</strong> siehst du nach Saisonbeginn alle abgegebenen Tipps. Klicke auf einen Spielernamen für die Detailansicht.</p>
"""
        },
        {
            'icon': '🏆',
            'title': 'Bestenliste & Punkte',
            'content': """
<h3>Punkteberechnung</h3>
<p>Die Punkte werden nach jedem abgeschlossenen Spieltag automatisch berechnet:</p>
<table>
<tr><th>Liga</th><th>Formel</th><th>Maximum</th></tr>
<tr><td>1. Bundesliga</td><td>162 &minus; &Sigma;|Tipp &minus; Ist|</td><td>162 Punkte</td></tr>
<tr><td>2. Bundesliga</td><td>162 &minus; &Sigma;|Tipp &minus; Ist|</td><td>162 Punkte</td></tr>
<tr><td><strong>Gesamt</strong></td><td>BL1 + BL2</td><td><strong>324 Punkte</strong></td></tr>
</table>
<p>Je näher dein Tipp an der Realität, desto mehr Punkte. Ein <strong>Volltreffer</strong> (exakte Platzierung) gibt keine Strafpunkte.</p>
<h3>Gleichstand</h3>
<p>Bei gleicher Punktzahl entscheidet: 1) niedrigere Standardabweichung σ (konsistentere Tipps) · 2) mehr Volltreffer</p>
<h3>Abzeichen</h3>
<table>
<tr><th>Abzeichen</th><th>Wer bekommt es?</th></tr>
<tr><td>🏆 Saisonsieger</td><td>Meiste Gesamtpunkte</td></tr>
<tr><td>🎯 Volltreffer-König</td><td>Meiste exakte Platzierungs-Treffer</td></tr>
<tr><td>🔩 Eiserner Tipper</td><td>Konsistentester Tipper (niedrigstes σ)</td></tr>
<tr><td>🎲 Wildcard</td><td>Höchste Einzelabweichung</td></tr>
<tr><td>🐇 Größter Angsthase</td><td>Tipp am nächsten an der Vorjahrestabelle</td></tr>
<tr><td>🦁 Mutigster Tipper</td><td>Tipp am weitesten von der Vorjahrestabelle entfernt</td></tr>
</table>
"""
        },
        {
            'icon': '📊',
            'title': 'Statistiken & Auswertungen',
            'content': """
<table>
<tr><th>Seite</th><th>Inhalt</th></tr>
<tr><td>📈 <strong>Verlauf</strong></td><td>Platzierungs-Liniengrafik und sortierbare Tabelle über alle Spieltage</td></tr>
<tr><td>📋 <strong>Alle Tipps</strong></td><td>Vergleich aller Tipps mit aktueller Abweichung, sortierbar</td></tr>
<tr><td>🐇 <strong>Angsthasen</strong></td><td>Wer tippt nah an der Vorjahrestabelle vs. wer wagt Neues</td></tr>
<tr><td>⚔️ <strong>Head-to-Head</strong></td><td>Direktvergleich zweier Spieler Platz für Platz</td></tr>
<tr><td>🗓️ <strong>Rückblick</strong></td><td>Saisonauswertung: Abschlusstabelle, Highlights, Ewige Tabelle, Team-Statistiken</td></tr>
<tr><td>⭐ <strong>Highlights</strong></td><td>Bester Tipper je Spieltag</td></tr>
<tr><td>⚽ <strong>Spieltage</strong></td><td>Live-Spieltagsergebnisse beider Bundesligen</td></tr>
<tr><td>🌐 <strong>Ewige Tabelle</strong></td><td>Saisonübergreifende Rangliste aller App-Saisons + Archiv-Vergleich</td></tr>
</table>
<h3>Verlauf-Tipps</h3>
<ul>
<li>Spielernamen in der Legende anklicken um einzelne Linien ein-/auszublenden</li>
<li>Zwischen <em>Platzierungs-</em> und <em>Score-Ansicht</em> wechseln</li>
<li>Spalten in der Verlaufstabelle sind sortierbar</li>
</ul>
"""
        },
        {
            'icon': '🕰️',
            'title': 'Archiv (1993–heute)',
            'content': """
<p>Das Archiv enthält die komplette Tipp-Geschichte seit der Saison 1993/94 — lange bevor die App existierte.</p>
<table>
<tr><th>Seite</th><th>Inhalt</th></tr>
<tr><td>📁 <strong>Archiv</strong></td><td>Abschlusstabelle jeder Saison mit Rang, Abweichungen und Max-Abweichungen. Höchste Max-Abweichung wird mit 🔥 markiert.</td></tr>
<tr><td>🌟 <strong>Ewige Tabelle (Archiv)</strong></td><td>Alle Tipper sortiert nach normiertem Rang-Durchschnitt. Saison-Zellen farbcodiert: 🥇 Gold, Grün = Top 3, Rot = Letzter.</td></tr>
<tr><td>😱 <strong>Angsthasen (Archiv)</strong></td><td>Historische Angsthasen-Tabellen mit Abweichungen pro Liga. 😎 = Bester, 😱 = Letzter.</td></tr>
<tr><td>👤 <strong>Karriere-Ansicht</strong></td><td>Rang-Verlauf-Chart und Detailtabelle für jeden einzelnen Tipper über alle Saisons.</td></tr>
</table>
<h3>Ewige Tabelle – Berechnungsmethode</h3>
<p>Der Gesamtwert ist ein <strong>normierter Rang-Durchschnitt</strong>: Rang × 100 ÷ Anzahl Teilnehmer der Saison, gemittelt über alle Saisons. Kleiner = besser. Beispiel: Rang 2 von 16 Teilnehmern = 12,5 Punkte.</p>
<h3>Historische Karriere im Profil</h3>
<p>Auf deiner Profilseite erscheint bei verknüpftem Account automatisch ein Abschnitt mit deinen historischen Platzierungen, Siegen und Top-3-Platzierungen.</p>
"""
        },
        {
            'icon': '👤',
            'title': 'Profil & Einstellungen',
            'content': """
<h3>Profil aufrufen</h3>
<p>Oben rechts auf deinen Namen klicken → <strong>Profil</strong></p>
<h3>Felder</h3>
<table>
<tr><th>Feld</th><th>Beschreibung</th></tr>
<tr><td><strong>Spielername</strong></td><td>Anzeigename überall in der App</td></tr>
<tr><td><strong>Voller Name</strong></td><td>Erscheint im Hover-Tooltip (Bestenliste, Rückblick)</td></tr>
<tr><td><strong>Lieblingsverein</strong></td><td>Erscheint ebenfalls im Tooltip (mit ⚽)</td></tr>
<tr><td><strong>E-Mail</strong></td><td>Für Tippschluss-Erinnerungen</td></tr>
<tr><td><strong>Neues Passwort</strong></td><td>Leer lassen wenn nicht ändern</td></tr>
</table>
<div class="alert alert-info">💡 Zum Speichern muss immer das <strong>aktuelle Passwort</strong> eingegeben werden.</div>
<h3>Meine Tipps im Profil</h3>
<p>Zeigt deine Tipps für BL1 und BL2 mit aktueller Platzierung und Abweichung. Spalten sind sortierbar (Ist, Tipp, Abw.).</p>
"""
        },
        {
            'icon': '🔔',
            'title': 'Push-Benachrichtigungen',
            'content': """
<h3>Push aktivieren</h3>
<p>Oben rechts auf das 🔔-Symbol klicken → Browser fragt nach Erlaubnis → <strong>Erlauben</strong></p>
<h3>Was wird gemeldet?</h3>
<ul>
<li>Du wirst von jemandem überholt</li>
<li>Du überholst jemanden</li>
</ul>
<h3>Browserunterstützung</h3>
<table>
<tr><th>Browser</th><th>Unterstützung</th></tr>
<tr><td>Chrome, Edge</td><td>✅ Vollständig</td></tr>
<tr><td>Firefox</td><td>✅ Vollständig</td></tr>
<tr><td>Safari (macOS)</td><td>✅ Ab Safari 16</td></tr>
<tr><td>Safari (iOS)</td><td>✅ Ab iOS 16.4 – Seite muss zum Home-Bildschirm hinzugefügt werden</td></tr>
</table>
<h3>Push deaktivieren</h3>
<p>Erneut auf das 🔔-Symbol klicken → <strong>Push deaktivieren</strong></p>
"""
        },
        {
            'icon': '💬',
            'title': 'Telegram-Gruppe',
            'content': """
<h3>Beitreten</h3>
<p>Wenn in der Navigation ein <strong>Telegram</strong>-Link erscheint, klicke darauf um der Gruppe beizutreten.</p>
<h3>Bot-Befehle</h3>
<table>
<tr><th>Befehl</th><th>Antwort</th></tr>
<tr><td><code>/rangliste</code></td><td>Aktuelle Top-10 mit Punktestand</td></tr>
<tr><td><code>/spieltag</code></td><td>Letzter Spieltag – bester Tipper</td></tr>
<tr><td><code>/help</code></td><td>Alle verfügbaren Befehle</td></tr>
</table>
<h3>Automatische Nachrichten</h3>
<ul>
<li>🏆 Rangliste (Top 3) nach jedem vollständig abgeschlossenen Spieltag</li>
<li>⚽ Spieltag-Highlight — wer war bester Tipper</li>
<li>⏰ Tippschluss-Erinnerung (manuell vom Admin)</li>
</ul>
"""
        },
        {
            'icon': '🔧',
            'title': 'Häufige Fragen & Probleme',
            'content': """
<h3>Ich kann meinen Tipp nicht speichern</h3>
<p>Die Tippabgabe wurde vom Admin gesperrt (Tippschluss). Nach dem Tippschluss sind keine Änderungen möglich.</p>

<h3>Meine Punkte wurden nicht aktualisiert</h3>
<p>Der Admin muss manuell die Tabelle abrufen. Punkte werden nicht automatisch bei jedem Spieltag aktualisiert.</p>

<h3>Tooltip erscheint nicht beim Hover</h3>
<p>Prüfe ob unter <strong>Profil</strong> ein <em>voller Name</em> oder <em>Lieblingsverein</em> eingetragen ist — der Tooltip erscheint nur wenn mindestens eines der beiden Felder gefüllt ist.</p>

<h3>Push-Benachrichtigungen kommen nicht</h3>
<ul>
<li>Browser-Einstellungen prüfen: Benachrichtigungen für die Seite erlaubt?</li>
<li>Safari (iOS): Seite zum Home-Bildschirm hinzufügen, dann Push erneut aktivieren</li>
<li>Push deaktivieren und neu aktivieren</li>
</ul>

<h3>Login-Sperre nach mehreren Fehlversuchen</h3>
<p>Nach 5 Fehlversuchen wird die IP für 15 Minuten gesperrt. Warten oder Administrator kontaktieren.</p>

<h3>Angsthasen-Seite zeigt keine Daten</h3>
<p>Für die Berechnung wird die Vorjahrestabelle benötigt. Diese wird automatisch gespeichert — bitte abwarten bis die ersten Spieltage gespielt wurden.</p>

<h3>Telegram-Link erscheint nicht in der Navigation</h3>
<p>Der Administrator muss unter <em>Admin → Telegram</em> einen Gruppen-Einladungslink eintragen.</p>
<div class="alert alert-info">💡 Bei weiteren Fragen wende dich an deinen Administrator.</div>
"""
        },
    ]
    return render_template('hilfe.html', sections=sections)


@app.route('/admin/doku')
@admin_required
def admin_doku():
    """Technische Dokumentation im Admin-Bereich."""
    import pathlib
    doc_path = pathlib.Path(BASE_DIR) / 'docs' / 'TECHNISCHE_DOKUMENTATION.md'
    try:
        import markdown as md_lib
        content = md_lib.markdown(
            doc_path.read_text(encoding='utf-8'),
            extensions=['tables', 'fenced_code', 'toc']
        )
    except Exception:
        content = '<pre>' + doc_path.read_text(encoding='utf-8') + '</pre>'
    return render_template('admin/doku.html', content=content,
                           title='Technische Dokumentation',
                           filename='TECHNISCHE_DOKUMENTATION.md')

@app.route('/admin/handbuch')
@admin_required
def admin_handbuch():
    """Benutzerhandbuch im Admin-Bereich (Markdown)."""
    import pathlib
    doc_path = pathlib.Path(BASE_DIR) / 'docs' / 'BENUTZERHANDBUCH.md'
    try:
        import markdown as md_lib
        content = md_lib.markdown(
            doc_path.read_text(encoding='utf-8'),
            extensions=['tables', 'fenced_code', 'toc']
        )
    except Exception:
        content = '<pre>' + doc_path.read_text(encoding='utf-8') + '</pre>'
    return render_template('admin/doku.html', content=content,
                           title='Benutzerhandbuch',
                           filename='BENUTZERHANDBUCH.md')



# ════════════════════════════════════════════════════════════
# DOMAIN-MIGRATION (tippcup.com)
# ════════════════════════════════════════════════════════════

@app.route('/admin/migration')
@admin_required
def admin_migration():
    """Übersicht und Tools für den Domain-Umzug."""
    db    = get_db()
    psubs = db.execute('SELECT COUNT(*) FROM push_subscriptions').fetchone()[0]
    tg_wh = (db.execute("SELECT value FROM config WHERE key='telegram_webhook_url'").fetchone() or {}).get('value', '')
    import socket
    hostname = socket.gethostname()
    return render_template_string('''<!doctype html><html><head><meta charset="utf-8">
<title>Domain-Migration</title>
<style>
body{font-family:sans-serif;max-width:700px;margin:2rem auto;padding:1rem}
.card{border:1px solid #ddd;border-radius:8px;padding:1.25rem;margin-bottom:1rem}
.card h3{margin:0 0 .75rem;color:#1a5e2a}
.badge-ok{background:#d1e7dd;color:#0f5132;padding:.2rem .5rem;border-radius:4px;font-size:.8rem}
.badge-warn{background:#fff3cd;color:#664d03;padding:.2rem .5rem;border-radius:4px;font-size:.8rem}
.badge-err{background:#f8d7da;color:#842029;padding:.2rem .5rem;border-radius:4px;font-size:.8rem}
.btn{display:inline-block;padding:.4rem .9rem;border-radius:5px;text-decoration:none;
     color:#fff;background:#1a5e2a;border:none;cursor:pointer;font-size:.9rem;margin:.2rem 0}
.btn-danger{background:#dc3545}
.btn-info{background:#0d6efd}
pre{background:#f8f9fa;padding:.75rem;border-radius:4px;font-size:.8rem;overflow-x:auto}
</style></head><body>
<h2>🔀 Domain-Migration Tippcup</h2>

<div class="card">
  <h3>1. Status</h3>
  <p>Server: <code>{{ hostname }}</code></p>
  <p>Push-Subscriptions in DB: 
    <span class="{{ "badge-warn" if psubs > 0 else "badge-ok" }}">
      {{ psubs }} {% if psubs > 0 %}(werden nach Umzug ungültig){% else %}(keine){% endif %}
    </span>
  </p>
  <p>Telegram Webhook: 
    <span class="{{ "badge-ok" if "tippcup.com" in (tg_wh or "") else "badge-warn" }}">
      {{ tg_wh or "nicht gesetzt" }}
    </span>
  </p>
</div>

<div class="card">
  <h3>2. Push-Subscriptions löschen</h3>
  <p class="text-muted" style="color:#666;font-size:.9rem">
    Push-Subscriptions sind an die alte Domain gebunden und müssen nach dem Umzug
    gelöscht werden. Nutzer aktivieren Push auf der neuen Domain neu.
  </p>
  <form method="POST" action="/admin/migration/clear-push"
        onsubmit="return confirm('Alle {{ psubs }} Push-Subscriptions löschen?')">
    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
    <button class="btn btn-danger">🗑️ Alle Push-Subscriptions löschen ({{ psubs }})</button>
  </form>
</div>

<div class="card">
  <h3>3. Telegram Webhook aktualisieren</h3>
  <p style="font-size:.9rem;color:#666">
    Nach dem Umzug muss der Telegram-Bot-Webhook auf die neue Domain zeigen.
  </p>
  <form method="POST" action="/admin/migration/update-webhook">
    <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
    <input type="text" name="new_url" placeholder="https://tippcup.com/telegram/webhook"
           style="width:100%;padding:.4rem;margin-bottom:.5rem;box-sizing:border-box;font-size:.9rem">
    <button class="btn btn-info">🤖 Webhook aktualisieren</button>
  </form>
</div>

<div class="card">
  <h3>4. Checkliste Umzug</h3>
  <pre>☐ 1. tippcup.com in Plesk anlegen + SSL (Let's Encrypt)
☐ 2. App-Dateien auf neue Domain kopieren
☐ 3. passenger_wsgi.py: DB_PATH anpassen (neuer Pfad)
☐ 4. VAPID-Keys: GLEICHE Keys aus alter passenger_wsgi.py übernehmen!
☐ 5. DB-Datei kopieren: tippspiel.db → neuer Pfad
☐ 6. App auf neuer Domain testen
☐ 7. Push-Subscriptions löschen (Button oben)
☐ 8. Telegram Webhook aktualisieren (Button oben)
☐ 9. 301-Redirect: liga.tippcup.com → tippcup.com (Plesk → Hosting → Redirects)
☐10. Nutzer informieren: PWA neu installieren, Push neu aktivieren</pre>
</div>

<p><a href="/admin/season">← Admin</a></p>
</body></html>''', psubs=psubs, tg_wh=tg_wh, hostname=hostname)


@app.route('/admin/migration/clear-push', methods=['POST'])
@admin_required
def admin_migration_clear_push():
    db = get_db()
    n  = db.execute('SELECT COUNT(*) FROM push_subscriptions').fetchone()[0]
    db.execute('DELETE FROM push_subscriptions')
    db.commit()
    flash(f'✅ {n} Push-Subscriptions gelöscht. Nutzer können Push auf der neuen Domain neu aktivieren.', 'success')
    return redirect(url_for('admin_migration'))


@app.route('/admin/migration/update-webhook', methods=['POST'])
@admin_required
def admin_migration_update_webhook():
    new_url = request.form.get('new_url', '').strip()
    if not new_url.startswith('https://'):
        flash('URL muss mit https:// beginnen.', 'danger')
        return redirect(url_for('admin_migration'))
    # Telegram Webhook setzen
    tg_token = get_config('telegram_token', '')
    if not tg_token:
        flash('Kein Telegram-Token konfiguriert.', 'danger')
        return redirect(url_for('admin_migration'))
    try:
        import requests as _req
        r = _req.post(
            f'https://api.telegram.org/bot{tg_token}/setWebhook',
            json={'url': new_url}, timeout=10
        )
        data = r.json()
        if data.get('ok'):
            get_db().execute(
                "INSERT OR REPLACE INTO config (key,value) VALUES ('telegram_webhook_url',?)",
                (new_url,)
            )
            get_db().commit()
            flash(f'✅ Telegram Webhook gesetzt: {new_url}', 'success')
        else:
            flash(f'Telegram Fehler: {data.get("description","?")}', 'danger')
    except Exception as e:
        flash(f'Fehler: {e}', 'danger')
    return redirect(url_for('admin_migration'))


# ════════════════════════════════════════════════════════════
# CRON, SONSTIGES
# ════════════════════════════════════════════════════════════


@app.route('/cron/update/<token>')
def cron_update(token):
    """
    Wird von einem Plesk-Cronjob aufgerufen um Tabelle + Scores automatisch
    zu aktualisieren. Token schützt vor unbefugtem Zugriff.
    Empfehlung: alle 30 Min aufrufen (z.B. */30 * * * *)
    """
    expected = get_config('cron_token', '')
    if not expected or token != expected:
        return 'Unauthorized', 401
    season = get_active_season()
    if not season or not season['season_started']:
        return 'no active season', 200
    try:
        update_standings_from_api(season, force=True)
        return 'ok', 200
    except Exception as e:
        app.logger.error(f'cron_update Fehler: {e}')
        return f'error: {e}', 500


def page_not_found(e):
    return render_template('errors/404.html'), 404

@app.errorhandler(500)
def internal_error(e):
    return render_template('errors/500.html'), 500

@app.errorhandler(403)
def forbidden(e):
    return render_template('errors/403.html'), 403

# ── robots.txt ────────────────────────────────
@app.route('/sw.js')
def service_worker():
    resp = send_from_directory(app.static_folder, 'sw.js',
                               mimetype='application/javascript')
    resp.headers['Service-Worker-Allowed'] = '/'
    resp.headers['Cache-Control'] = 'no-cache'
    return resp


@app.route('/robots.txt')
def robots():
    return ('User-agent: *\nDisallow: /\n', 200,
            {'Content-Type': 'text/plain'})

# ── favicon ───────────────────────────────────
@app.route('/favicon.ico')
def favicon():
    return app.send_static_file('favicon.svg')

def _load_api_key_from_db():
    """API-Key aus DB in Umgebungsvariable laden – einmalig beim Start."""
    try:
        with app.app_context():
            db_key = get_config('football_data_api_key', '')
            if db_key and not os.environ.get('FOOTBALL_DATA_API_KEY'):
                os.environ['FOOTBALL_DATA_API_KEY'] = db_key
            elif not db_key and os.environ.get('FOOTBALL_DATA_API_KEY'):
                # Migration: Key aus passenger_wsgi.py in DB übernehmen
                set_config('football_data_api_key', os.environ['FOOTBALL_DATA_API_KEY'])
    except Exception as e:
        app.logger.debug(f"Ignorierter Fehler: {e}")

# API-Key beim Import laden (sowohl python app.py als auch WSGI)
_load_api_key_from_db()

if __name__ == '__main__':
    init_db()
    app.run(debug=os.environ.get('FLASK_DEBUG', '0') == '1')
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=False)
