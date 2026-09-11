"""
Pytest-Fixtures für die Tippcup-Testsuite.

WICHTIG: app.py liest DB_PATH/SECRET_KEY beim Modul-Import (nicht pro
Request), daher müssen die Umgebungsvariablen VOR dem `import app`
gesetzt werden – siehe unten.
"""
import os
import sys
import pathlib
from datetime import timedelta

# Projekt-Root ins sys.path aufnehmen, damit `import app` funktioniert
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TEST_DB = str(pathlib.Path(__file__).parent / "_test_tippspiel.db")
os.environ["SECRET_KEY"] = "pytest-test-secret-key-nicht-fuer-produktion"
os.environ["DB_PATH"] = TEST_DB

import pytest
import app as tc  # tc = tippcup


@pytest.fixture()
def app():
    """Frische, leere Test-Datenbank pro Testfunktion."""
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    tc.init_db()
    tc.app.config.update(TESTING=True)
    yield tc.app
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def db(app):
    with app.app_context():
        yield tc.get_db()


def _csrf_token(client):
    """Holt ein gültiges CSRF-Token, indem eine beliebige GET-Seite geladen wird."""
    client.get('/login')
    with client.session_transaction() as sess:
        return sess.get('csrf_token')


@pytest.fixture()
def csrf_token(client):
    return _csrf_token(client)


def login(client, username, password):
    """Loggt einen Testnutzer ein (inkl. korrektem CSRF-Token) und folgt dem Redirect."""
    token = _csrf_token(client)
    return client.post('/login', data={
        'username': username, 'password': password, 'csrf_token': token,
    }, follow_redirects=True)


@pytest.fixture()
def make_user(db):
    """Factory-Fixture: erstellt einen Testnutzer und gibt seine ID zurück."""
    def _make(username='testuser', password='testpass123', is_admin=0, is_active=1,
              display_name=None):
        db.execute(
            """INSERT INTO users (username, password_hash, display_name, is_admin, is_active)
               VALUES (?,?,?,?,?)""",
            (username, tc.generate_password_hash(password),
             display_name or username, is_admin, is_active)
        )
        db.commit()
        return db.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone()['id']
    return _make


@pytest.fixture()
def make_season(db):
    """
    Factory-Fixture: erstellt eine Saison mit Teams für bl1/bl2.
    locked_bl1/locked_bl2 stellt direkt den Sperrzustand ein (ohne echte
    Deadline-Vergleiche/Netzwerkaufrufe auszulösen).
    """
    def _make(n_teams_bl1=4, n_teams_bl2=4, locked_bl1=0, locked_bl2=0,
              tips_locked=0, is_active=1, year=2027, name='Test-Saison'):
        now = tc.local_now()
        past = (now - timedelta(days=1)).isoformat()
        future = (now + timedelta(days=1)).isoformat()
        db.execute(
            """INSERT INTO seasons (year, name, is_active, tips_locked,
                                     locked_bl1, locked_bl2, deadline_bl1, deadline_bl2)
               VALUES (?,?,?,?,?,?,?,?)""",
            (year, name, is_active, tips_locked, locked_bl1, locked_bl2,
             past if locked_bl1 else future,
             past if locked_bl2 else future)
        )
        db.commit()
        sid = db.execute('SELECT id FROM seasons WHERE name=?', (name,)).fetchone()['id']
        for i in range(n_teams_bl1):
            db.execute('INSERT INTO teams (season_id, league, name) VALUES (?,?,?)',
                       (sid, 'bl1', f'BL1-Team-{i+1}'))
        for i in range(n_teams_bl2):
            db.execute('INSERT INTO teams (season_id, league, name) VALUES (?,?,?)',
                       (sid, 'bl2', f'BL2-Team-{i+1}'))
        db.commit()
        return sid
    return _make


@pytest.fixture()
def get_teams(db):
    def _get(season_id, league):
        return db.execute(
            'SELECT * FROM teams WHERE season_id=? AND league=? ORDER BY name',
            (season_id, league)
        ).fetchall()
    return _get
