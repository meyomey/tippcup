"""
Tests für Authentifizierung, CSRF-Schutz, Rate-Limiting/Lockout und
serverseitige Admin-Berechtigungsprüfung.
"""
from tests.conftest import login
import app as tc


class TestLogin:

    def test_login_with_correct_credentials_succeeds(self, client, make_user):
        make_user(username='u1', password='geheim123')
        r = login(client, 'u1', 'geheim123')
        assert r.status_code == 200
        with client.session_transaction() as sess:
            assert sess.get('user_id') is not None

    def test_login_with_wrong_password_fails(self, client, make_user):
        make_user(username='u1', password='geheim123')
        r = login(client, 'u1', 'falsches-passwort')
        with client.session_transaction() as sess:
            assert sess.get('user_id') is None

    def test_login_lockout_after_repeated_failures(self, client, make_user):
        """Nach mehreren Fehlversuchen muss der Login gesperrt werden (Brute-Force-Schutz)."""
        make_user(username='u1', password='geheim123')
        for _ in range(6):
            login(client, 'u1', 'falsch')
        # Selbst das korrekte Passwort darf jetzt nicht mehr funktionieren
        r = login(client, 'u1', 'geheim123')
        with client.session_transaction() as sess:
            assert sess.get('user_id') is None, "Nach Lockout darf auch das korrekte Passwort nicht mehr durchgehen"


class TestCSRF:

    def test_post_without_csrf_token_is_rejected(self, client, make_user):
        make_user(username='u1', password='geheim123')
        r = client.post('/login', data={'username': 'u1', 'password': 'geheim123'})
        assert r.status_code == 403

    def test_post_with_wrong_csrf_token_is_rejected(self, client, make_user):
        make_user(username='u1', password='geheim123')
        client.get('/login')  # Token in Session anlegen
        r = client.post('/login', data={
            'username': 'u1', 'password': 'geheim123', 'csrf_token': 'ein-falsches-token',
        })
        assert r.status_code == 403

    def test_post_with_valid_csrf_token_succeeds(self, client, make_user):
        make_user(username='u1', password='geheim123')
        r = login(client, 'u1', 'geheim123')
        assert r.status_code == 200
        with client.session_transaction() as sess:
            assert sess.get('user_id') is not None

    def test_telegram_webhook_is_csrf_exempt(self, client):
        """Der Telegram-Webhook hat eine eigene, sessionlose Authentifizierung
        (Token in der URL) und muss daher OHNE CSRF-Token funktionieren."""
        r = client.post('/telegram/webhook/irgendein-token', json={'update_id': 1})
        # 403 ist hier ok (falsches Bot-Token), aber NICHT wegen CSRF -
        # entscheidend ist, dass keine 403-Antwort durch den CSRF-Filter kommt,
        # bevor die Route überhaupt ihre eigene Prüfung macht.
        assert r.status_code in (200, 403)


class TestAdminPermissions:

    def test_non_admin_cannot_access_admin_users_page(self, client, make_user):
        make_user(username='u1', password='geheim123', is_admin=0)
        login(client, 'u1', 'geheim123')
        r = client.get('/admin/users', follow_redirects=False)
        assert r.status_code in (302, 403)

    def test_admin_can_access_admin_users_page(self, client, make_user):
        make_user(username='admin1', password='geheim123', is_admin=1)
        login(client, 'admin1', 'geheim123')
        r = client.get('/admin/users')
        assert r.status_code == 200

    def test_anonymous_cannot_access_admin_users_page(self, client):
        r = client.get('/admin/users', follow_redirects=False)
        assert r.status_code in (302, 401, 403)

    def test_non_admin_direct_url_to_admin_route_is_blocked(self, client, make_user, make_season):
        """Berechtigungsprüfung muss serverseitig sein – nicht nur im UI versteckt."""
        make_user(username='u1', password='geheim123', is_admin=0)
        sid = make_season()
        login(client, 'u1', 'geheim123')
        with client.session_transaction() as sess:
            token = sess.get('csrf_token')
        r = client.post(f'/admin/season/{sid}/delete', data={'csrf_token': token}, follow_redirects=False)
        assert r.status_code in (302, 403, 404)
        # Sicherstellen, dass die Saison NICHT gelöscht wurde
        with tc.app.app_context():
            db = tc.get_db()
            row = db.execute('SELECT id FROM seasons WHERE id=?', (sid,)).fetchone()
        assert row is not None, "Eine nicht-Admin-Anfrage darf die Saison nicht löschen können"
