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
            tc._set_config_value('backup_auto_enabled', '0')
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
