"""
Tests für die Punkteberechnung (calculate_scores_for_season) und
den Autofill-Mechanismus für fehlende Tipps.

Score-Formel pro Liga (siehe app.py, calculate_scores_for_season):
    a_l     = Summe(|Tipp-Platz - echter Platz|) über alle Teams der Liga
    a_max_l = n_l² // 2      (n_l = Anzahl Teams in der Liga)
    score_l = a_max_l - a_l
"""
import app as tc


def _set_standings(db, season_id, team_ranks):
    """team_ranks: dict {team_id: aktueller_platz}"""
    for tid, rank in team_ranks.items():
        db.execute(
            """INSERT INTO standings (season_id, team_id, current_rank)
               VALUES (?,?,?)
               ON CONFLICT(season_id, team_id) DO UPDATE SET current_rank=excluded.current_rank""",
            (season_id, tid, rank)
        )
    db.commit()


def _set_predictions(db, uid, season_id, team_ranks):
    for tid, rank in team_ranks.items():
        db.execute(
            """INSERT INTO predictions (user_id, season_id, team_id, predicted_rank)
               VALUES (?,?,?,?)
               ON CONFLICT(user_id, season_id, team_id) DO UPDATE SET predicted_rank=excluded.predicted_rank""",
            (uid, season_id, tid, rank)
        )
    db.commit()


class TestScoreCalculation:

    def test_perfect_prediction_gets_max_score(self, db, make_user, make_season, get_teams):
        """Wer die Liga exakt richtig tippt, muss den theoretischen Höchstwert a_max bekommen."""
        uid = make_user()
        sid = make_season(n_teams_bl1=4, n_teams_bl2=4)
        bl1 = get_teams(sid, 'bl1')
        bl2 = get_teams(sid, 'bl2')

        real_ranks = {t['id']: i + 1 for i, t in enumerate(bl1)}
        real_ranks.update({t['id']: i + 1 for i, t in enumerate(bl2)})
        _set_standings(db, sid, real_ranks)
        _set_predictions(db, uid, sid, real_ranks)  # exakt richtig getippt

        with tc.app.app_context():
            tc.calculate_scores_for_season(sid)

        row = db.execute('SELECT * FROM scores WHERE user_id=? AND season_id=?', (uid, sid)).fetchone()
        assert row is not None
        a_max_per_league = 4 * 4 // 2  # n²/2 für 4 Teams
        assert row['score'] == a_max_per_league * 2  # beide Ligen perfekt

    def test_known_deviation_matches_formula(self, db, make_user, make_season, get_teams):
        """Konkretes Zahlenbeispiel: Abweichung von 2 Plätzen bei einem Team in BL1."""
        uid = make_user()
        sid = make_season(n_teams_bl1=4, n_teams_bl2=4)
        bl1 = get_teams(sid, 'bl1')
        bl2 = get_teams(sid, 'bl2')

        real_ranks = {t['id']: i + 1 for i, t in enumerate(bl1)}
        real_ranks.update({t['id']: i + 1 for i, t in enumerate(bl2)})
        _set_standings(db, sid, real_ranks)

        # Tipp: BL2 exakt richtig, BL1 mit vertauschtem Team 1 <-> Team 3 (je 2 Plätze daneben)
        pred = dict(real_ranks)
        t1, t3 = bl1[0]['id'], bl1[2]['id']
        pred[t1], pred[t3] = real_ranks[t3], real_ranks[t1]
        _set_predictions(db, uid, sid, pred)

        with tc.app.app_context():
            tc.calculate_scores_for_season(sid)

        row = db.execute('SELECT * FROM scores WHERE user_id=? AND season_id=?', (uid, sid)).fetchone()
        a_max = 4 * 4 // 2
        # Abweichung BL1: |1-3| + |3-1| = 4 (die anderen beiden Teams stimmen)
        expected_bl1_score = a_max - 4
        expected_bl2_score = a_max  # exakt richtig
        assert row['score'] == expected_bl1_score + expected_bl2_score

    def test_tie_break_columns_present_for_deterministic_sort(self, db, make_user, make_season, get_teams):
        """std_deviation und volltreffer müssen befüllt werden (Grundlage für die Ranglisten-Sortierung)."""
        uid = make_user()
        sid = make_season()
        bl1 = get_teams(sid, 'bl1')
        bl2 = get_teams(sid, 'bl2')
        real_ranks = {t['id']: i + 1 for i, t in enumerate(bl1)}
        real_ranks.update({t['id']: i + 1 for i, t in enumerate(bl2)})
        _set_standings(db, sid, real_ranks)
        _set_predictions(db, uid, sid, real_ranks)

        with tc.app.app_context():
            tc.calculate_scores_for_season(sid)

        row = db.execute('SELECT * FROM scores WHERE user_id=? AND season_id=?', (uid, sid)).fetchone()
        assert row['std_deviation'] is not None
        assert row['volltreffer'] is not None


class TestAutofillMissingPredictions:
    """
    Regressionstests für den im Audit gefundenen Scoring-Bug: ein User ohne
    Tipp für eine gesperrte Liga darf NICHT automatisch die Höchstpunktzahl
    bekommen. Stattdessen muss autofill_missing_predictions() eine zufällige
    Tabelle erzeugen, die ganz normal (mit realistischer Abweichung) bewertet wird.
    """

    def test_missing_league_gets_random_fill_not_max_score(self, db, make_user, make_season, get_teams):
        uid = make_user()
        # BL1 gesperrt (Deadline vergangen), BL2 offen
        sid = make_season(n_teams_bl1=4, n_teams_bl2=4, locked_bl1=1, locked_bl2=0)
        bl1 = get_teams(sid, 'bl1')
        bl2 = get_teams(sid, 'bl2')
        real_ranks = {t['id']: i + 1 for i, t in enumerate(bl1)}
        real_ranks.update({t['id']: i + 1 for i, t in enumerate(bl2)})
        _set_standings(db, sid, real_ranks)

        # User hat NUR für BL2 getippt (BL1 fehlt komplett)
        bl2_pred = {t['id']: i + 1 for i, t in enumerate(bl2)}
        _set_predictions(db, uid, sid, bl2_pred)

        with tc.app.app_context():
            tc.autofill_missing_predictions(sid)

        # BL1 muss jetzt vollständig aufgefüllt sein (4 Teams)
        bl1_preds = db.execute(
            'SELECT team_id, predicted_rank, is_auto FROM predictions WHERE user_id=? AND season_id=? '
            'AND team_id IN (%s)' % ','.join('?' * len(bl1)),
            [uid, sid] + [t['id'] for t in bl1]
        ).fetchall()
        assert len(bl1_preds) == 4
        assert all(p['is_auto'] == 1 for p in bl1_preds), "Autofill-Tipps müssen als is_auto=1 markiert sein"
        # Es muss eine gültige Permutation von 1..4 sein
        assert sorted(p['predicted_rank'] for p in bl1_preds) == [1, 2, 3, 4]

        # Die ursprünglichen BL2-Tipps dürfen NICHT verändert worden sein
        bl2_preds_after = {p['team_id']: p['predicted_rank'] for p in db.execute(
            'SELECT team_id, predicted_rank FROM predictions WHERE user_id=? AND season_id=? '
            'AND team_id IN (%s)' % ','.join('?' * len(bl2)),
            [uid, sid] + [t['id'] for t in bl2]
        ).fetchall()}
        assert bl2_preds_after == bl2_pred

    def test_score_after_autofill_reflects_actual_random_deviation(self, db, make_user, make_season, get_teams):
        """
        Kern des Bugs: score darf NICHT pauschal a_max sein, wenn kein
        echter Tipp vorlag. Wir verifizieren, dass der gespeicherte Score
        exakt zu den (jetzt zufällig befüllten) predicted_rank-Werten passt,
        anstatt einfach den Bestwert anzunehmen.
        """
        uid = make_user()
        sid = make_season(n_teams_bl1=4, n_teams_bl2=4, locked_bl1=1, locked_bl2=1)
        bl1 = get_teams(sid, 'bl1')
        bl2 = get_teams(sid, 'bl2')
        real_ranks = {t['id']: i + 1 for i, t in enumerate(bl1)}
        real_ranks.update({t['id']: i + 1 for i, t in enumerate(bl2)})
        _set_standings(db, sid, real_ranks)
        # Der User hat GAR NICHTS getippt (beide Ligen gesperrt, kein Tipp vorhanden)
        # -> ohne den Fix würde er nicht mal in der Score-Tabelle auftauchen (da die
        #    Score-Query nur User mit >=1 Prediction berücksichtigt); mit autofill
        #    soll er jetzt eine vollständige, zufällige Tabelle bekommen.

        with tc.app.app_context():
            tc.calculate_scores_for_season(sid)

        preds = {p['team_id']: p['predicted_rank'] for p in db.execute(
            'SELECT team_id, predicted_rank FROM predictions WHERE user_id=? AND season_id=?',
            (uid, sid)
        ).fetchall()}
        assert len(preds) == 8  # alle 8 Teams (4+4) wurden zufällig befüllt

        a_bl1 = sum(abs(preds[t['id']] - real_ranks[t['id']]) for t in bl1)
        a_bl2 = sum(abs(preds[t['id']] - real_ranks[t['id']]) for t in bl2)
        a_max = 4 * 4 // 2
        expected_score = (a_max - a_bl1) + (a_max - a_bl2)

        row = db.execute('SELECT score FROM scores WHERE user_id=? AND season_id=?', (uid, sid)).fetchone()
        assert row['score'] == expected_score
        # Regressionsschutz: der alte Bug hätte hier score == a_max*2 (Bestwert) erzeugt,
        # das ist bei einer echten Zufallstabelle so gut wie unmöglich.
        assert row['score'] != a_max * 2 or a_bl1 == 0 and a_bl2 == 0

    def test_autofill_skipped_when_league_not_locked(self, db, make_user, make_season, get_teams):
        """Solange die Deadline nicht erreicht ist, darf NICHTS automatisch befüllt werden."""
        uid = make_user()
        sid = make_season(n_teams_bl1=4, n_teams_bl2=4, locked_bl1=0, locked_bl2=0)

        with tc.app.app_context():
            tc.autofill_missing_predictions(sid)

        count = db.execute(
            'SELECT COUNT(*) as c FROM predictions WHERE user_id=? AND season_id=?', (uid, sid)
        ).fetchone()['c']
        assert count == 0

    def test_autofill_skipped_when_nachfrist_active(self, db, make_user, make_season, get_teams):
        """Ein User mit aktiver Nachfrist darf NICHT automatisch befüllt werden – er hat noch Zeit."""
        uid = make_user()
        sid = make_season(n_teams_bl1=4, n_teams_bl2=4, locked_bl1=1, locked_bl2=1)
        db.execute(
            'INSERT INTO nachfrist (user_id, season_id, granted_at) VALUES (?,?,CURRENT_TIMESTAMP)',
            (uid, sid)
        )
        db.commit()

        with tc.app.app_context():
            tc.autofill_missing_predictions(sid)

        count = db.execute(
            'SELECT COUNT(*) as c FROM predictions WHERE user_id=? AND season_id=?', (uid, sid)
        ).fetchone()['c']
        assert count == 0, "Bei aktiver Nachfrist darf kein Zufallstipp erzeugt werden"

    def test_autofill_only_fills_missing_league_when_partially_tipped(self, db, make_user, make_season, get_teams):
        """Wenn eine Liga schon vollständig getippt ist, darf autofill sie nicht anfassen."""
        uid = make_user()
        sid = make_season(n_teams_bl1=4, n_teams_bl2=4, locked_bl1=1, locked_bl2=1)
        bl1 = get_teams(sid, 'bl1')
        bl1_pred = {t['id']: i + 1 for i, t in enumerate(bl1)}
        _set_predictions(db, uid, sid, bl1_pred)

        with tc.app.app_context():
            tc.autofill_missing_predictions(sid)

        bl1_after = {p['team_id']: p['predicted_rank'] for p in db.execute(
            'SELECT team_id, predicted_rank FROM predictions WHERE user_id=? AND season_id=? '
            'AND team_id IN (%s)' % ','.join('?' * len(bl1)),
            [uid, sid] + [t['id'] for t in bl1]
        ).fetchall()}
        assert bl1_after == bl1_pred, "Vollständig getippte Liga darf nicht überschrieben werden"
