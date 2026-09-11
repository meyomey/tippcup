"""
Tests für die Admin-Impersonation und den im Audit gefundenen Randfall:
Wird der Ursprungs-Admin während der Impersonation degradiert, darf beim
Zurückwechseln NICHT einfach wieder Admin-Rechte gesetzt werden.
"""
from tests.conftest import login
import app as tc


class TestImpersonation:

    def test_admin_can_impersonate_user(self, client, db, make_user):
        make_user(username='admin1', password='geheim123', is_admin=1)
        uid = make_user(username='user1', password='xxxxxxxx', is_admin=0)
        login(client, 'admin1', 'geheim123')
        with client.session_transaction() as sess:
            token = sess.get('csrf_token')
        r = client.post(f'/admin/users/{uid}/impersonate', data={'csrf_token': token},
                         follow_redirects=False)
        assert r.status_code == 302
        with client.session_transaction() as sess:
            assert sess.get('user_id') == uid
            assert sess.get('is_admin') is False
            assert sess.get('impersonator_id') is not None

    def test_stop_impersonate_restores_admin_when_still_admin(self, client, db, make_user):
        admin_id = make_user(username='admin1', password='geheim123', is_admin=1)
        uid = make_user(username='user1', password='xxxxxxxx', is_admin=0)
        login(client, 'admin1', 'geheim123')
        with client.session_transaction() as sess:
            token = sess.get('csrf_token')
        client.post(f'/admin/users/{uid}/impersonate', data={'csrf_token': token})

        r = client.get('/admin/stop-impersonate', follow_redirects=False)
        assert r.status_code == 302
        assert r.headers.get('Location', '').endswith('/admin/users')
        with client.session_transaction() as sess:
            assert sess.get('user_id') == admin_id
            assert sess.get('is_admin') is True

    def test_stop_impersonate_denies_reactivation_if_admin_was_demoted(self, client, db, make_user):
        """
        Regressionstest für den Audit-Fund: wird der Admin WÄHREND der
        Impersonation degradiert, darf stop-impersonate ihn NICHT wieder
        zum Admin machen, sondern muss die Session leeren.
        """
        admin_id = make_user(username='admin1', password='geheim123', is_admin=1)
        uid = make_user(username='user1', password='xxxxxxxx', is_admin=0)
        login(client, 'admin1', 'geheim123')
        with client.session_transaction() as sess:
            token = sess.get('csrf_token')
        client.post(f'/admin/users/{uid}/impersonate', data={'csrf_token': token})

        # Admin wird währenddessen degradiert (z.B. durch einen zweiten Admin)
        db.execute('UPDATE users SET is_admin=0 WHERE id=?', (admin_id,))
        db.commit()

        r = client.get('/admin/stop-impersonate', follow_redirects=False)
        assert r.headers.get('Location', '').endswith('/login'), (
            "Bei entzogenen Admin-Rechten muss zum Login weitergeleitet werden, "
            "nicht zurück als (fälschlich reaktivierter) Admin"
        )
        with client.session_transaction() as sess:
            assert sess.get('is_admin') is not True
            assert sess.get('user_id') is None, "Session muss komplett geleert worden sein"

    def test_stop_impersonate_denies_reactivation_if_admin_deactivated(self, client, db, make_user):
        """Gleicher Schutz, falls der Account statt entmachtet komplett deaktiviert wird."""
        admin_id = make_user(username='admin1', password='geheim123', is_admin=1)
        uid = make_user(username='user1', password='xxxxxxxx', is_admin=0)
        login(client, 'admin1', 'geheim123')
        with client.session_transaction() as sess:
            token = sess.get('csrf_token')
        client.post(f'/admin/users/{uid}/impersonate', data={'csrf_token': token})

        db.execute('UPDATE users SET is_active=0 WHERE id=?', (admin_id,))
        db.commit()

        r = client.get('/admin/stop-impersonate', follow_redirects=False)
        assert r.headers.get('Location', '').endswith('/login')
