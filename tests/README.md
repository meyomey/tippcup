# Tests

Automatisierte Tests für die kritischsten Funktionen (siehe Sicherheits-
Audit September 2026, CHANGELOG.md): Punkteberechnung, Tipp-Validierung,
Login/CSRF, Admin-Berechtigungen, Ranglisten-Determinismus, Impersonation.

## Ausführen

```bash
pip install -r requirements-dev.txt
pytest
```

Für mehr Output: `pytest -v`. Für einzelne Datei/Testklasse:
```bash
pytest tests/test_scoring.py -v
pytest tests/test_scoring.py::TestAutofillMissingPredictions -v
```

## Funktionsweise

Jeder Test läuft gegen eine frische, temporäre SQLite-Datenbank
(`tests/_test_tippspiel.db`, wird pro Test neu angelegt und danach
gelöscht — landet nie im Git-Repo, siehe `.gitignore`). Es wird NIE
gegen die echte `tippspiel.db` getestet.

`conftest.py` stellt Fixtures bereit:
- `client` — Flask-Test-Client
- `db` — Datenbankverbindung (im App-Context)
- `make_user(username, password, is_admin, is_active)` — legt einen Testnutzer an
- `make_season(n_teams_bl1, n_teams_bl2, locked_bl1, locked_bl2, ...)` — legt eine Saison mit Teams an
- `get_teams(season_id, league)` — liest die Teams einer Liga
- `login(client, username, password)` — loggt einen Testnutzer ein (inkl. CSRF-Token)

## Was noch fehlt (aus dem Audit, niedrige Priorität)

- Saisonwechsel-Tests (neue Saison, Auf-/Absteiger, historische Daten bleiben unberührt)
- Konkurrenz-/Race-Condition-Tests (parallele Tippabgaben)
- Tests für den Admin-Bereich über die Punkteberechnung hinaus (Backup/Restore, Medien-Upload)

Diese sind komplexer aufzusetzen (Zeitsteuerung, parallele Threads,
Datei-Uploads) und wurden bewusst zurückgestellt — bei Bedarf gerne
gezielt ergänzen.
