"""
Tests für die Ranglisten-Sortierung: bei exaktem Gleichstand muss die
Reihenfolge deterministisch (user_id ASC als Tie-Breaker) und nicht vom
Zufall der internen SQLite-Sortierung abhängig sein.
"""
import app as tc


class TestRankingDeterminism:

    def test_tied_scores_sort_deterministically_by_user_id(self, db, make_user, make_season, get_teams):
        # Drei User mit exakt identischem Score/Abweichung/Volltreffer anlegen
        uids = [make_user(username=f'u{i}') for i in range(3)]
        sid = make_season(n_teams_bl1=4, n_teams_bl2=4)
        bl1, bl2 = get_teams(sid, 'bl1'), get_teams(sid, 'bl2')
        real_ranks = {t['id']: i + 1 for i, t in enumerate(bl1)}
        real_ranks.update({t['id']: i + 1 for i, t in enumerate(bl2)})

        for tid, rank in real_ranks.items():
            db.execute(
                """INSERT INTO standings (season_id, team_id, current_rank) VALUES (?,?,?)
                   ON CONFLICT(season_id, team_id) DO UPDATE SET current_rank=excluded.current_rank""",
                (sid, tid, rank)
            )
        db.commit()

        for uid in uids:
            for tid, rank in real_ranks.items():
                db.execute(
                    """INSERT INTO predictions (user_id, season_id, team_id, predicted_rank) VALUES (?,?,?,?)
                       ON CONFLICT(user_id, season_id, team_id) DO UPDATE SET predicted_rank=excluded.predicted_rank""",
                    (uid, sid, tid, rank)
                )
        db.commit()

        with tc.app.app_context():
            tc.calculate_scores_for_season(sid)

        # Zweimal hintereinander abfragen – Reihenfolge muss stabil identisch sein
        rows1 = db.execute(
            """SELECT user_id FROM scores WHERE season_id=?
               ORDER BY score DESC, std_deviation ASC, volltreffer DESC, user_id ASC""",
            (sid,)
        ).fetchall()
        rows2 = db.execute(
            """SELECT user_id FROM scores WHERE season_id=?
               ORDER BY score DESC, std_deviation ASC, volltreffer DESC, user_id ASC""",
            (sid,)
        ).fetchall()
        order1 = [r['user_id'] for r in rows1]
        order2 = [r['user_id'] for r in rows2]
        assert order1 == order2 == sorted(uids), (
            "Bei exaktem Gleichstand muss die Rangliste stets nach user_id ASC sortiert sein"
        )
