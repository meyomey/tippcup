"""
Tests für das automatische tägliche Backup (run_scheduled_backup_if_due,
Cron-Endpoint, Pruning, Admin-Dateiverwaltung).
"""
import os
import time
import app as tc
from tests.conftest import login


class TestScheduledBackup:

    def test_first_run_creates_backup(self, app, db, tmp_path, make_user):
        tc.BACKUPS_DIR = str(tmp_path)
        make_user(username='admin1', is_admin=1)
        with app.app_context():
            ok = tc.run_scheduled_backup_if_due()
        assert ok is True
        files = os.listdir(tmp_path)
        assert len(files) == 1
        assert files[0].endswith('_auto.zip')

    def test_second_run_within_23h_is_skipped(self, app, db, tmp_path, make_user):
        tc.BACKUPS_DIR = str(tmp_path)
        make_user(username='admin1', is_admin=1)
        with app.app_context():
            tc.run_scheduled_backup_if_due()
            ok2 = tc.run_scheduled_backup_if_due()
        assert ok2 is False
        assert len(os.listdir(tmp_path)) == 1

    def test_force_ignores_time_window(self, app, db, tmp_path, make_user):
        tc.BACKUPS_DIR = str(tmp_path)
        make_user(username='admin1', is_admin=1)
        with app.app_context():
            tc.run_scheduled_backup_if_due()
            ok2 = tc.run_scheduled_backup_if_due(force=True)
        assert ok2 is True
        assert len(os.listdir(tmp_path)) == 2

    def test_disabled_setting_prevents_run(self, app, db, tmp_path, make_user):
        tc.BACKUPS_DIR = str(tmp_path)
        make_user(username='admin1', is_admin=1)
        with app.app_context():
            tc.set_setting('backup_auto_enabled', '0')
            ok = tc.run_scheduled_backup_if_due()
        assert ok is False
        assert len(os.listdir(tmp_path)) == 0

    def test_prune_removes_only_old_files(self, app, db, tmp_path):
        tc.BACKUPS_DIR = str(tmp_path)
        old = tmp_path / 'old_auto.zip'
        new = tmp_path / 'new_auto.zip'
        old.write_bytes(b'x')
        new.write_bytes(b'x')
        old_time = time.time() - 20 * 86400
        os.utime(old, (old_time, old_time))
        with app.app_context():
            tc.prune_old_backups(retention_days=14)
        remaining = os.listdir(tmp_path)
        assert 'old_auto.zip' not in remaining
        assert 'new_auto.zip' in remaining

    def test_cron_token_is_stable_across_calls(self, app, db):
        with app.app_context():
            t1 = tc.get_or_create_backup_cron_token()
            t2 = tc.get_or_create_backup_cron_token()
        assert t1 == t2
        assert len(t1) > 20


class TestCronBackupEndpoint:

    def test_wrong_token_is_rejected(self, client, db, tmp_path):
        tc.BACKUPS_DIR = str(tmp_path)
        r = client.get('/cron/backup/definitiv-falsches-token')
        assert r.status_code == 403

    def test_correct_token_triggers_backup(self, client, app, db, tmp_path):
        tc.BACKUPS_DIR = str(tmp_path)
        with app.app_context():
            token = tc.get_or_create_backup_cron_token()
        r = client.get(f'/cron/backup/{token}')
        assert r.status_code == 200
        assert r.get_json()['ok'] is True
        assert len(os.listdir(tmp_path)) == 1

    def test_cron_endpoint_needs_no_csrf_token(self, client, app, db, tmp_path):
        """Sessionloser externer Trigger – darf NICHT am CSRF-Schutz scheitern."""
        tc.BACKUPS_DIR = str(tmp_path)
        with app.app_context():
            token = tc.get_or_create_backup_cron_token()
        r = client.get(f'/cron/backup/{token}')
        assert r.status_code != 403 or r.get_json().get('error') != None


class TestBackupFileManagement:

    def test_download_path_traversal_is_blocked(self, client, db, make_user, tmp_path):
        tc.BACKUPS_DIR = str(tmp_path)
        make_user(username='admin1', password='geheim123', is_admin=1)
        login(client, 'admin1', 'geheim123')
        r = client.get('/admin/backup/download/..%2F..%2Fapp.py', follow_redirects=True)
        assert b'app.py' not in r.data or r.status_code == 200  # keine Datei ausgeliefert
        # Sicherer Check: Response ist keine ZIP-Datei
        assert r.headers.get('Content-Type') != 'application/zip'

    def test_non_admin_cannot_download_backups(self, client, db, make_user, tmp_path):
        tc.BACKUPS_DIR = str(tmp_path)
        make_user(username='u1', password='geheim123', is_admin=0)
        login(client, 'u1', 'geheim123')
        r = client.get('/admin/backup/download/irgendwas.zip', follow_redirects=False)
        assert r.status_code in (302, 403)

    def test_admin_can_list_and_see_backups_on_page(self, client, app, db, make_user, tmp_path):
        tc.BACKUPS_DIR = str(tmp_path)
        make_user(username='admin1', password='geheim123', is_admin=1)
        with app.app_context():
            tc.run_scheduled_backup_if_due(force=True)
        login(client, 'admin1', 'geheim123')
        r = client.get('/admin/backup/page')
        assert r.status_code == 200
        assert b'_auto.zip' in r.data


class TestGranularAutoBackupContent:
    """Welche Komponenten das AUTOMATISCHE Backup enthält, muss konfigurierbar sein."""

    def test_default_includes_all_three(self, app, db, tmp_path, make_user):
        tc.BACKUPS_DIR = str(tmp_path)
        make_user(username='admin1', is_admin=1)
        with app.app_context():
            tc.run_scheduled_backup_if_due(force=True)
        import zipfile
        fname = [f for f in os.listdir(tmp_path) if f.endswith('.zip')][0]
        with zipfile.ZipFile(tmp_path / fname) as zf:
            names = zf.namelist()
        assert 'tippspiel.db' in names
        assert any(n.startswith('config/') for n in names)

    def test_only_db_when_others_disabled(self, app, db, tmp_path, make_user):
        tc.BACKUPS_DIR = str(tmp_path)
        make_user(username='admin1', is_admin=1)
        with app.app_context():
            tc.set_setting('backup_auto_include_uploads', '0')
            tc.set_setting('backup_auto_include_config', '0')
            tc.run_scheduled_backup_if_due(force=True)
        import zipfile
        fname = [f for f in os.listdir(tmp_path) if f.endswith('.zip')][0]
        with zipfile.ZipFile(tmp_path / fname) as zf:
            names = zf.namelist()
        assert 'tippspiel.db' in names
        assert not any(n.startswith('uploads/') for n in names)
        assert not any(n.startswith('config/') for n in names)


class TestGranularRestore:
    """Beim Wiederherstellen müssen DB/Medien/Config unabhängig voneinander wählbar sein."""

    def _make_full_backup(self, app, tmp_path):
        tc.BACKUPS_DIR = str(tmp_path)
        with app.app_context():
            tc.set_setting('backup_auto_include_db', '1')
            tc.set_setting('backup_auto_include_uploads', '1')
            tc.set_setting('backup_auto_include_config', '1')
            tc.run_scheduled_backup_if_due(force=True)
        fname = [f for f in os.listdir(tmp_path) if f.endswith('.zip')][0]
        with open(tmp_path / fname, 'rb') as f:
            return fname, f.read()

    def test_restore_from_auto_only_config(self, client, app, db, make_user, tmp_path):
        make_user(username='admin1', password='geheim123', is_admin=1)
        fname, _ = self._make_full_backup(app, tmp_path)
        login(client, 'admin1', 'geheim123')
        with client.session_transaction() as sess:
            csrf = sess.get('csrf_token')
        r = client.post(f'/admin/backup/restore-from-auto/{fname}', data={
            'restore_config': 'on', 'csrf_token': csrf,
        }, follow_redirects=True)
        body = r.get_data(as_text=True)
        assert 'Konfigdateien' in body
        assert 'Datenbank' not in body.split('Wiederhergestellt')[1].split('.')[0]

    def test_restore_from_auto_requires_at_least_one_checkbox(self, client, app, db, make_user, tmp_path):
        make_user(username='admin1', password='geheim123', is_admin=1)
        fname, _ = self._make_full_backup(app, tmp_path)
        login(client, 'admin1', 'geheim123')
        with client.session_transaction() as sess:
            csrf = sess.get('csrf_token')
        r = client.post(f'/admin/backup/restore-from-auto/{fname}', data={
            'csrf_token': csrf,
        }, follow_redirects=True)
        assert 'mindestens eine Komponente' in r.get_data(as_text=True)
        with client.session_transaction() as sess:
            assert sess.get('user_id') is not None, "Ohne Auswahl darf nichts passieren, auch kein Logout"

    def test_upload_restore_only_db_selected(self, client, app, db, make_user, tmp_path):
        import io
        make_user(username='admin1', password='geheim123', is_admin=1)
        fname, zip_bytes = self._make_full_backup(app, tmp_path)
        login(client, 'admin1', 'geheim123')
        with client.session_transaction() as sess:
            csrf = sess.get('csrf_token')
        r = client.post('/admin/restore', data={
            'backup_file': (io.BytesIO(zip_bytes), fname),
            'restore_db': 'on',
            'csrf_token': csrf,
        }, content_type='multipart/form-data', follow_redirects=True)
        body = r.get_data(as_text=True)
        assert 'Wiederhergestellt: Datenbank' in body
        assert 'Medien' not in body.split('Wiederhergestellt')[1].split('.')[0]

    def test_upload_restore_zip_without_selection_shows_warning(self, client, app, db, make_user, tmp_path):
        import io
        make_user(username='admin1', password='geheim123', is_admin=1)
        fname, zip_bytes = self._make_full_backup(app, tmp_path)
        login(client, 'admin1', 'geheim123')
        with client.session_transaction() as sess:
            csrf = sess.get('csrf_token')
        r = client.post('/admin/restore', data={
            'backup_file': (io.BytesIO(zip_bytes), fname),
            'csrf_token': csrf,
        }, content_type='multipart/form-data', follow_redirects=True)
        assert 'mindestens eine Komponente' in r.get_data(as_text=True)

    def test_restore_from_auto_path_traversal_blocked(self, client, app, db, make_user, tmp_path):
        make_user(username='admin1', password='geheim123', is_admin=1)
        tc.BACKUPS_DIR = str(tmp_path)
        login(client, 'admin1', 'geheim123')
        with client.session_transaction() as sess:
            csrf = sess.get('csrf_token')
        r = client.post('/admin/backup/restore-from-auto/..%2F..%2Fapp.py', data={
            'restore_db': 'on', 'csrf_token': csrf,
        }, follow_redirects=True)
        assert 'nicht gefunden' in r.get_data(as_text=True)

    def test_non_admin_cannot_restore_from_auto(self, client, app, db, make_user, tmp_path):
        make_user(username='u1', password='geheim123', is_admin=0)
        tc.BACKUPS_DIR = str(tmp_path)
        login(client, 'u1', 'geheim123')
        with client.session_transaction() as sess:
            csrf = sess.get('csrf_token')
        r = client.post('/admin/backup/restore-from-auto/irgendwas.zip', data={
            'restore_db': 'on', 'csrf_token': csrf,
        }, follow_redirects=False)
        assert r.status_code in (302, 403)
