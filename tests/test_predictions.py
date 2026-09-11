"""
Tests für die Tipp-Validierung über die /predict-Route:
- vollständiger Tipp wird korrekt gespeichert
- doppelte Platzierungen werden abgelehnt
- unvollständige Tipps werden abgelehnt
- eine gesperrte Liga kann nicht mehr getippt werden (serverseitige Deadline-Prüfung)
- ein bereits gespeicherter Tipp wird beim erneuten Aufruf korrekt vorausgefüllt/aktualisiert
"""
from tests.conftest import login
import app as tc


def _submit(client, season_teams_bl1, season_teams_bl2, ranks_bl1, ranks_bl2, csrf_token):
    data = {'csrf_token': csrf_token}
    for team, rank in zip(season_teams_bl1, ranks_bl1):
        data[f'rank_{team["id"]}'] = str(rank)
    for team, rank in zip(season_teams_bl2, ranks_bl2):
        data[f'rank_{team["id"]}'] = str(rank)
    return client.post('/predict', data=data, follow_redirects=True)


class TestPredictionValidation:

    def test_complete_valid_tip_is_saved(self, client, db, make_user, make_season, get_teams):
        make_user(username='u1', password='pw12345')
        sid = make_season(n_teams_bl1=4, n_teams_bl2=4)
        db.execute('UPDATE seasons SET is_active=1 WHERE id=?', (sid,))
        db.commit()
        login(client, 'u1', 'pw12345')
        bl1, bl2 = get_teams(sid, 'bl1'), get_teams(sid, 'bl2')

        with client.session_transaction() as sess:
            token = sess.get('csrf_token')
        r = _submit(client, bl1, bl2, [1, 2, 3, 4], [1, 2, 3, 4], token)
        assert r.status_code == 200
        assert 'erfolgreich gespeichert' in r.get_data(as_text=True)

        uid = db.execute("SELECT id FROM users WHERE username='u1'").fetchone()['id']
        count = db.execute(
            'SELECT COUNT(*) as c FROM predictions WHERE user_id=? AND season_id=?', (uid, sid)
        ).fetchone()['c']
        assert count == 8

    def test_duplicate_rank_is_rejected(self, client, db, make_user, make_season, get_teams):
        make_user(username='u1', password='pw12345')
        sid = make_season(n_teams_bl1=4, n_teams_bl2=4)
        login(client, 'u1', 'pw12345')
        bl1, bl2 = get_teams(sid, 'bl1'), get_teams(sid, 'bl2')

        with client.session_transaction() as sess:
            token = sess.get('csrf_token')
        # Platz 1 doppelt vergeben in BL1
        r = _submit(client, bl1, bl2, [1, 1, 3, 4], [1, 2, 3, 4], token)
        assert 'doppelt' in r.get_data(as_text=True)

        uid = db.execute("SELECT id FROM users WHERE username='u1'").fetchone()['id']
        count = db.execute(
            'SELECT COUNT(*) as c FROM predictions WHERE user_id=? AND season_id=?', (uid, sid)
        ).fetchone()['c']
        assert count == 0, "Bei einem ungültigen Tipp darf NICHTS gespeichert werden"

    def test_incomplete_tip_is_rejected(self, client, db, make_user, make_season, get_teams):
        make_user(username='u1', password='pw12345')
        sid = make_season(n_teams_bl1=4, n_teams_bl2=4)
        login(client, 'u1', 'pw12345')
        bl1, bl2 = get_teams(sid, 'bl1'), get_teams(sid, 'bl2')

        with client.session_transaction() as sess:
            token = sess.get('csrf_token')
        data = {'csrf_token': token}
        # Nur 3 von 4 BL1-Teams befüllt, BL2 komplett fehlt
        for team, rank in zip(bl1[:3], [1, 2, 3]):
            data[f'rank_{team["id"]}'] = str(rank)
        r = client.post('/predict', data=data, follow_redirects=True)

        uid = db.execute("SELECT id FROM users WHERE username='u1'").fetchone()['id']
        count = db.execute(
            'SELECT COUNT(*) as c FROM predictions WHERE user_id=? AND season_id=?', (uid, sid)
        ).fetchone()['c']
        assert count == 0, "Ein unvollständiger Tipp darf nicht fälschlich als vollständig gelten"

    def test_locked_league_cannot_be_tipped(self, client, db, make_user, make_season, get_teams):
        """Serverseitige Deadline-Prüfung: gesperrte Liga darf nicht mehr überschrieben werden,
        selbst wenn im POST-Request Werte dafür mitgeschickt werden."""
        make_user(username='u1', password='pw12345')
        sid = make_season(n_teams_bl1=4, n_teams_bl2=4, locked_bl1=1, locked_bl2=0)
        login(client, 'u1', 'pw12345')
        bl1, bl2 = get_teams(sid, 'bl1'), get_teams(sid, 'bl2')

        with client.session_transaction() as sess:
            token = sess.get('csrf_token')
        # BL1 ist gesperrt -> nur BL2 muss vollständig sein, BL1-Werte werden ignoriert
        r = _submit(client, bl1, bl2, [1, 2, 3, 4], [1, 2, 3, 4], token)
        assert 'erfolgreich gespeichert' in r.get_data(as_text=True)

        uid = db.execute("SELECT id FROM users WHERE username='u1'").fetchone()['id']
        bl1_count = db.execute(
            'SELECT COUNT(*) as c FROM predictions WHERE user_id=? AND season_id=? AND team_id IN (%s)'
            % ','.join('?' * len(bl1)),
            [uid, sid] + [t['id'] for t in bl1]
        ).fetchone()['c']
        assert bl1_count == 0, "Für eine gesperrte Liga darf trotz mitgeschickter Werte kein Tipp gespeichert werden"

    def test_existing_prediction_is_updated_not_duplicated(self, client, db, make_user, make_season, get_teams):
        """Ein zweites Absenden desselben Tipps darf keinen Duplikat-Datensatz erzeugen (ON CONFLICT UPDATE)."""
        make_user(username='u1', password='pw12345')
        sid = make_season(n_teams_bl1=4, n_teams_bl2=4)
        login(client, 'u1', 'pw12345')
        bl1, bl2 = get_teams(sid, 'bl1'), get_teams(sid, 'bl2')

        with client.session_transaction() as sess:
            token = sess.get('csrf_token')
        _submit(client, bl1, bl2, [1, 2, 3, 4], [1, 2, 3, 4], token)
        # Änderung: Tipp korrigieren
        with client.session_transaction() as sess:
            token = sess.get('csrf_token')
        _submit(client, bl1, bl2, [2, 1, 3, 4], [1, 2, 3, 4], token)

        uid = db.execute("SELECT id FROM users WHERE username='u1'").fetchone()['id']
        count = db.execute(
            'SELECT COUNT(*) as c FROM predictions WHERE user_id=? AND season_id=?', (uid, sid)
        ).fetchone()['c']
        assert count == 8, "Erneutes Speichern darf keine Duplikate erzeugen"
        new_rank = db.execute(
            'SELECT predicted_rank FROM predictions WHERE user_id=? AND team_id=?', (uid, bl1[0]['id'])
        ).fetchone()['predicted_rank']
        assert new_rank == 2, "Die Änderung muss korrekt übernommen worden sein"

    def test_login_required_to_access_predict(self, client, make_season):
        make_season()
        r = client.get('/predict', follow_redirects=False)
        assert r.status_code in (302, 401, 403)
