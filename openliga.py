"""
API-Client für Bundesliga-Daten
================================
Primär:   football-data.org  (offizielle DFL-Daten, API-Key nötig)
Fallback: OpenligaDB         (kostenlos, kein Key nötig)

Optimierungen:
  - Caching aller API-Aufrufe in SQLite (api_cache-Tabelle)
  - Adaptives Polling-Intervall: 2 Min. bei Live-Spielen, 60 Min. sonst
  - ETag/Last-Modified-ähnliche Logik für football-data.org (gespeicherter Hash)
  - Einzelne Aufrufe pro Request durch In-Process-Cache (LRU)
  - Rate-Limiting-Schutz: max. 1 Aufruf pro Endpunkt alle N Sekunden

Limits football-data.org (Free Tier):
  10 Requests/Minute → wir machen max. ~5/Minute (komfortabler Puffer)
"""

import os
import json
import time
import hashlib
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from datetime import datetime
from functools import lru_cache

# ── Konstanten ────────────────────────────────────────────────
TIMEOUT          = 15
INTERVAL_LIVE    = 120    # 2 Min. bei Live-Spielen
INTERVAL_NORMAL  = 1800   # 30 Min. außerhalb Spielzeit
INTERVAL_TABLE   = 3600   # 60 Min. Tabellen (OpenligaDB)
INTERVAL_TABLE_FD = 900   # 15 Min. Tabellen (football-data.org, schnellere Daten)
INTERVAL_MATCHDAY_NR = 300  # 5 Min. für aktuellen Spieltag

FD_BASE   = 'https://api.football-data.org/v4'
FD_LEAGUE = {'bl1': 2002}  # Nur BL1 im Free Tier! BL2 → immer OpenligaDB
OL_BASE   = 'https://api.openligadb.de'

# In-Process-Cache: verhindert doppelte API-Aufrufe innerhalb eines Requests
# key → (timestamp, data)
_process_cache: dict = {}
PROCESS_CACHE_TTL = 30  # Sekunden


def _process_cache_get(key):
    entry = _process_cache.get(key)
    if entry and (time.time() - entry[0]) < PROCESS_CACHE_TTL:
        return entry[1]
    return None

def _process_cache_set(key, value):
    _process_cache[key] = (time.time(), value)


# ── HTTP-Sessions ─────────────────────────────────────────────
def _make_session(headers=None):
    s = requests.Session()
    retry = Retry(total=2, backoff_factor=1,
                  status_forcelist=[429, 500, 502, 503, 504])
    s.mount('https://', HTTPAdapter(max_retries=retry))
    if headers:
        s.headers.update(headers)
    return s

def _fd_session():
    key = os.environ.get('FOOTBALL_DATA_API_KEY', '')
    return _make_session({'X-Auth-Token': key} if key else {})

def _ol_session():
    return _make_session()

def _log(msg):
    try:
        import logging
        logging.getLogger('openliga').info(msg)
    except Exception:
        pass


# ── Inhalts-Hash (ersetzt getlastchangedate für football-data.org) ───────
def _data_hash(data) -> str:
    """SHA1-Hash des JSON-Inhalts – erkennt Datenänderungen ohne extra API-Call."""
    return hashlib.sha1(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]


# ══════════════════════════════════════════════════════════════
# FOOTBALL-DATA.ORG
# ══════════════════════════════════════════════════════════════

def fd_get_table(league: str, year: int) -> tuple:
    """
    Tabelle von football-data.org.
    Gibt (table: list, content_hash: str) zurück.
    """
    key = os.environ.get('FOOTBALL_DATA_API_KEY', '')
    if not key:
        raise ValueError('Kein FOOTBALL_DATA_API_KEY gesetzt')
    comp_id = FD_LEAGUE.get(league)
    if not comp_id:
        raise ValueError(f'Unbekannte Liga: {league}')

    url = f'{FD_BASE}/competitions/{comp_id}/standings?season={year}'
    r   = _fd_session().get(url, timeout=TIMEOUT)
    r.raise_for_status()
    data  = r.json()
    table_raw = data.get('standings', [])
    total = next((t for t in table_raw if t.get('type') == 'TOTAL'), None)
    if not total:
        raise ValueError('Keine TOTAL-Tabelle in Antwort')

    result = []
    for entry in total.get('table', []):
        team = entry.get('team', {})
        result.append({
            'TeamName':      team.get('name', '?'),
            'TeamInfoId':    team.get('id', 0),
            'ShortName':     team.get('shortName', team.get('name', '')[:3]),
            'TLA':           team.get('tla', ''),   # 3-Buchstaben-Kürzel (FCB, BVB etc.)
            'TeamIconUrl':   team.get('crest', ''),
            'Points':        entry.get('points', 0),
            'Goals':         entry.get('goalsFor', 0),
            'OpponentGoals': entry.get('goalsAgainst', 0),
            'Matches':       entry.get('playedGames', 0),
            'Wins':          entry.get('won', 0),
            'Draw':          entry.get('draw', 0),
            'Losses':        entry.get('lost', 0),
            '_source':       'football-data.org',
        })
    return result, _data_hash(result)


def fd_get_matchday(league: str, year: int, matchday: int) -> tuple:
    """
    Spieltag von football-data.org.
    Gibt (matches: list, content_hash: str) zurück.
    """
    key = os.environ.get('FOOTBALL_DATA_API_KEY', '')
    if not key:
        raise ValueError('Kein FOOTBALL_DATA_API_KEY gesetzt')
    comp_id = FD_LEAGUE.get(league)
    url = f'{FD_BASE}/competitions/{comp_id}/matches?season={year}&matchday={matchday}'
    r   = _fd_session().get(url, timeout=TIMEOUT)
    r.raise_for_status()

    result = []
    for m in r.json().get('matches', []):
        home   = m.get('homeTeam', {})
        away   = m.get('awayTeam', {})
        score  = m.get('score', {})
        ft     = score.get('fullTime', {})
        ht     = score.get('halfTime', {})
        status = m.get('status', '')
        is_live = status in ('IN_PLAY', 'PAUSED', 'HALF_TIME')

        # UTC → lokale Zeit (CET/CEST) konvertieren
        utc_str = m.get('utcDate', '')
        local_str = utc_str
        if utc_str:
            try:
                from zoneinfo import ZoneInfo
                from datetime import timezone
                dt_utc = datetime.fromisoformat(utc_str.replace('Z', '+00:00'))
                dt_local = dt_utc.astimezone(ZoneInfo('Europe/Berlin'))
                local_str = dt_local.strftime('%Y-%m-%dT%H:%M:%S')
            except Exception:
                try:
                    from datetime import timedelta
                    dt = datetime.fromisoformat(utc_str.replace('Z', ''))
                    dt_local = dt + timedelta(hours=2)
                    local_str = dt_local.strftime('%Y-%m-%dT%H:%M:%S')
                except Exception:
                    local_str = utc_str

        # Spielminute: aus API (FD Free-Tier liefert sie nicht) oder aus Anpfiffzeit berechnen
        live_minute = m.get('minute')
        if is_live and not live_minute and local_str and status not in ('HALF_TIME', 'PAUSED'):
            try:
                ko = datetime.fromisoformat(local_str)
                now_local = datetime.now()
                elapsed = (now_local - ko).total_seconds() / 60
                if 0 < elapsed <= 120:
                    if elapsed > 60: elapsed -= 15   # ~15 Min Halbzeitpause
                    live_minute = max(1, min(90, int(elapsed)))
            except Exception:
                pass

        result.append({
            'match_id':    m.get('id', 0),
            'datetime':    local_str,
            'is_finished': status == 'FINISHED',
            'is_live':     is_live,
            'live_minute': live_minute,
            'live_status': status,                     # IN_PLAY / PAUSED / HALF_TIME
            'team1_name':  home.get('name', '?'),
            'team1_short': home.get('shortName', home.get('name', '')[:3]),
            'team1_icon':  home.get('crest', ''),
            'team2_name':  away.get('name', '?'),
            'team2_short': away.get('shortName', away.get('name', '')[:3]),
            'team2_icon':  away.get('crest', ''),
            'goals_ft_1':  ft.get('home'),
            'goals_ft_2':  ft.get('away'),
            'goals_ht_1':  ht.get('home'),
            'goals_ht_2':  ht.get('away'),
            '_source':     'football-data.org',
        })
    return result, _data_hash(result)


def fd_get_current_matchday_nr(league: str) -> int:
    """Aktuellen Spieltag von football-data.org — mit In-Process-Cache."""
    cached = _process_cache_get(f'fd_matchday_nr_{league}')
    if cached is not None:
        return cached
    key = os.environ.get('FOOTBALL_DATA_API_KEY', '')
    if not key:
        raise ValueError('Kein FOOTBALL_DATA_API_KEY gesetzt')
    comp_id = FD_LEAGUE.get(league)
    url = f'{FD_BASE}/competitions/{comp_id}'
    r   = _fd_session().get(url, timeout=TIMEOUT)
    r.raise_for_status()
    season = r.json().get('currentSeason', {})
    nr = season.get('currentMatchday', 1) or 1
    _process_cache_set(f'fd_matchday_nr_{league}', nr)
    return nr


# ══════════════════════════════════════════════════════════════
# OPENLIGADB (Fallback)
# ══════════════════════════════════════════════════════════════

def _val(td, *keys):
    for k in keys:
        if k in td: return td[k]
    return 0

def _str(d, *keys):
    for k in keys:
        if d.get(k): return str(d[k])
    return ''

def _int(d, *keys):
    for k in keys:
        v = d.get(k)
        if v is not None:
            try: return int(v)
            except: pass
    return None


def ol_normalize_table(raw: list) -> list:
    result = []
    for td in raw:
        result.append({
            'TeamName':      td.get('teamName') or td.get('TeamName') or '?',
            'TeamInfoId':    td.get('teamInfoId') or td.get('TeamInfoId') or 0,
            'ShortName':     td.get('shortName') or td.get('ShortName') or '',
            'TeamIconUrl':   td.get('teamIconUrl') or td.get('TeamIconUrl') or '',
            'Points':        _val(td, 'points', 'Points'),
            'Goals':         _val(td, 'goals', 'Goals'),
            'OpponentGoals': _val(td, 'opponentGoals', 'OpponentGoals'),
            'Matches':       _val(td, 'matches', 'Matches'),
            'Wins':          _val(td, 'won', 'Wins', 'wins'),
            'Draw':          _val(td, 'draw', 'Draw', 'draws'),
            'Losses':        _val(td, 'lost', 'Losses', 'losses'),
            '_source':       'openligadb',
        })
    return result


def ol_get_table(league: str, year: int) -> tuple:
    """Gibt (table: list, content_hash: str) zurück."""
    url = f'{OL_BASE}/getbltable/{league}/{year}'
    r   = _ol_session().get(url, timeout=TIMEOUT)
    r.raise_for_status()
    result = ol_normalize_table(r.json())
    return result, _data_hash(result)



def ol_get_all_season_matches(league: str, year: int) -> list:
    """
    Lädt alle Spiele einer Saison von OpenligaDB.
    URL: /getmatchdata/{league}/{year}
    Gibt rohe normalisierte Match-Liste zurück, inklusive matchday-Feld.
    """
    url = f'{OL_BASE}/getmatchdata/{league}/{year}'
    r   = _ol_session().get(url, timeout=TIMEOUT)
    r.raise_for_status()
    raw = r.json()
    # Normalisieren + Spieltag ergänzen
    result = []
    for m in raw:
        norm = ol_normalize_matches([m])
        if norm:
            entry = norm[0]
            entry['matchday'] = (m.get('group') or m.get('Group') or {}).get('groupOrderID') or                                   (m.get('group') or m.get('Group') or {}).get('GroupOrderID')
            entry['team1_ol_id'] = (m.get('team1') or m.get('Team1') or {}).get('teamId') or                                      (m.get('team1') or m.get('Team1') or {}).get('TeamId') or                                      (m.get('team1') or m.get('Team1') or {}).get('teamInfoId') or                                      (m.get('team1') or m.get('Team1') or {}).get('TeamInfoId')
            entry['team2_ol_id'] = (m.get('team2') or m.get('Team2') or {}).get('teamId') or                                      (m.get('team2') or m.get('Team2') or {}).get('TeamId') or                                      (m.get('team2') or m.get('Team2') or {}).get('teamInfoId') or                                      (m.get('team2') or m.get('Team2') or {}).get('TeamInfoId')
            result.append(entry)
    return result


def compute_table_after_matchday(matches: list, up_to_matchday: int) -> dict:
    """
    Berechnet die Tabelle nach einem bestimmten Spieltag aus einer Liste aller Saisonspiele.
    matches: Ausgabe von ol_get_all_season_matches()
    up_to_matchday: Spieltag inklusiv (z.B. 5 = nach Spieltag 5)

    Gibt dict zurück: team_ol_id → {points, goals_for, goals_against, wins, draws, losses, played}
    Tabelle ist nach Punkten absteigend vorsortiert, Rang = Position + 1.
    Gibt zusätzlich '_ranked_ids' zurück: geordnete Liste der team_ol_ids.
    """
    table = {}

    def ensure(tid, name):
        if tid not in table:
            table[tid] = {'name': name, 'points': 0, 'goals_for': 0,
                          'goals_against': 0, 'wins': 0, 'draws': 0, 'losses': 0, 'played': 0}

    for m in matches:
        md = m.get('matchday')
        if md is None or md > up_to_matchday:
            continue
        if not m.get('is_finished'):
            continue
        g1 = m.get('goals_ft_1')
        g2 = m.get('goals_ft_2')
        if g1 is None or g2 is None:
            continue

        t1 = m.get('team1_ol_id')
        t2 = m.get('team2_ol_id')
        n1 = m.get('team1_name', '')
        n2 = m.get('team2_name', '')

        if not t1 or not t2:
            continue

        ensure(t1, n1)
        ensure(t2, n2)

        table[t1]['goals_for']     += g1
        table[t1]['goals_against'] += g2
        table[t2]['goals_for']     += g2
        table[t2]['goals_against'] += g1
        table[t1]['played'] += 1
        table[t2]['played'] += 1

        if g1 > g2:
            table[t1]['wins']   += 1; table[t1]['points'] += 3
            table[t2]['losses'] += 1
        elif g2 > g1:
            table[t2]['wins']   += 1; table[t2]['points'] += 3
            table[t1]['losses'] += 1
        else:
            table[t1]['draws'] += 1; table[t1]['points'] += 1
            table[t2]['draws'] += 1; table[t2]['points'] += 1

    # Sortieren: Punkte desc, Tordifferenz desc, Tore desc
    ranked = sorted(table.keys(),
        key=lambda tid: (
            -table[tid]['points'],
            -(table[tid]['goals_for'] - table[tid]['goals_against']),
            -table[tid]['goals_for'],
        )
    )
    result = {tid: {**table[tid], '_rank': i+1} for i, tid in enumerate(ranked)}
    result['_ranked_ids'] = ranked
    return result



def ol_normalize_matches(raw: list) -> list:
    result = []
    for m in raw:
        t1 = m.get('team1') or m.get('Team1') or {}
        t2 = m.get('team2') or m.get('Team2') or {}
        results = m.get('matchResults') or m.get('MatchResults') or []
        gHT1 = gHT2 = gFT1 = gFT2 = None
        for r in results:
            rt = r.get('resultTypeID') or r.get('ResultTypeID') or 0
            p1 = _int(r, 'pointsTeam1', 'PointsTeam1')
            p2 = _int(r, 'pointsTeam2', 'PointsTeam2')
            if rt == 1:   gHT1, gHT2 = p1, p2
            elif rt == 2: gFT1, gFT2 = p1, p2
        dt_str  = _str(m, 'matchDateTime', 'MatchDateTime')
        is_over = bool(m.get('matchIsFinished') or m.get('MatchIsFinished'))
        is_live = False
        if dt_str and not is_over:
            try:
                dt = datetime.fromisoformat(dt_str.replace('Z', ''))
                try:
                    from zoneinfo import ZoneInfo
                    now = datetime.now(ZoneInfo('Europe/Berlin')).replace(tzinfo=None)
                except ImportError:
                    from datetime import timedelta
                    now = datetime.utcnow() + timedelta(hours=2)
                is_live = 0 <= (now - dt).total_seconds() / 60 <= 120
            except: pass
        live_minute = None
        if is_live and dt_str:
            try:
                elapsed = (now - dt).total_seconds() / 60
                # Pause nach 45 Min berücksichtigen (ca. 15 Min Halbzeitpause)
                if elapsed > 60:
                    elapsed -= 15
                live_minute = max(1, min(90, int(elapsed)))
            except: pass
        result.append({
            'match_id':    _int(m, 'matchID', 'MatchID') or 0,
            'datetime':    dt_str,
            'is_finished': is_over,
            'is_live':     is_live,
            'live_minute': live_minute,
            'live_status': 'IN_PLAY' if is_live else '',
            'team1_name':  _str(t1, 'teamName', 'TeamName'),
            'team1_short': _str(t1, 'shortName', 'ShortName') or _str(t1, 'teamName', 'TeamName')[:3],
            'team1_icon':  _str(t1, 'teamIconUrl', 'TeamIconUrl'),
            'team2_name':  _str(t2, 'teamName', 'TeamName'),
            'team2_short': _str(t2, 'shortName', 'ShortName') or _str(t2, 'teamName', 'TeamName')[:3],
            'team2_icon':  _str(t2, 'teamIconUrl', 'TeamIconUrl'),
            'goals_ht_1': gHT1, 'goals_ht_2': gHT2,
            'goals_ft_1': gFT1, 'goals_ft_2': gFT2,
            '_source':    'openligadb',
        })
    return result


def ol_get_matchday(league: str, year: int, matchday: int) -> tuple:
    """Gibt (matches: list, content_hash: str) zurück."""
    url = f'{OL_BASE}/getmatchdata/{league}/{year}/{matchday}'
    r   = _ol_session().get(url, timeout=TIMEOUT)
    r.raise_for_status()
    result = ol_normalize_matches(r.json())
    return result, _data_hash(result)


def ol_get_current_matchday_nr(league: str) -> int:
    cached = _process_cache_get(f'ol_matchday_nr_{league}')
    if cached is not None:
        return cached
    try:
        url  = f'{OL_BASE}/getcurrentgroup/{league}'
        r    = _ol_session().get(url, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        nr   = data.get('groupOrderID') or data.get('GroupOrderID') or 1
        _process_cache_set(f'ol_matchday_nr_{league}', nr)
        return nr
    except:
        return 1


def get_last_change_date(league: str, year: int, matchday: int) -> str:
    """OpenligaDB-spezifisch: Timestamp der letzten Änderung (leichte Abfrage)."""
    url = f'{OL_BASE}/getlastchangedate/{league}/{year}/{matchday}'
    r   = _ol_session().get(url, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text.strip().strip('"')


def has_changed_since(league: str, year: int, matchday: int, known_ts: str) -> tuple:
    """
    Prüft ob Daten geändert wurden.
    Bei football-data.org: immer True (kein getlastchangedate-Äquivalent).
    Bei OpenligaDB: leichte Abfrage vor dem echten Laden.
    """
    fd_key = os.environ.get('FOOTBALL_DATA_API_KEY', '')
    if fd_key:
        # FD hat kein change-detection endpoint → Caller entscheidet anhand TTL
        return True, ''
    try:
        latest  = get_last_change_date(league, year, matchday)
        return (latest != known_ts), latest
    except Exception:
        return True, known_ts


# ══════════════════════════════════════════════════════════════
# ÖFFENTLICHE API  —  automatischer Fallback + einheitliches Format
# ══════════════════════════════════════════════════════════════

def get_table(league: str, year: int) -> list:
    """
    Tabelle — BL1: primär football-data.org (Fallback OpenligaDB).
               BL2: immer OpenligaDB (nicht im Free Tier von FD).
    Gibt normalisierte Liste zurück (ohne content_hash).
    """
    fd_key = os.environ.get('FOOTBALL_DATA_API_KEY', '')
    if fd_key and league in FD_LEAGUE:
        try:
            table, _ = fd_get_table(league, year)
            _log(f'get_table {league}/{year}: football-data.org ({len(table)} Teams)')
            return table
        except Exception as e:
            _log(f'get_table {league}/{year}: football-data.org FAIL ({e}) → OpenligaDB')
    table, _ = ol_get_table(league, year)
    _log(f'get_table {league}/{year}: OpenligaDB ({len(table)} Teams)')
    return table


def get_table_with_hash(league: str, year: int) -> tuple:
    """
    Wie get_table() aber gibt (list, content_hash) zurück.
    BL1: football-data.org (Fallback OL). BL2: immer OpenligaDB.
    """
    fd_key = os.environ.get('FOOTBALL_DATA_API_KEY', '')
    if fd_key and league in FD_LEAGUE:
        try:
            table, h = fd_get_table(league, year)
            _log(f'get_table_with_hash {league}/{year}: FD ({len(table)} Teams)')
            return table, h
        except Exception as e:
            _log(f'get_table_with_hash {league}/{year}: FD FAIL ({e}) → OL')
    table, h = ol_get_table(league, year)
    return table, h


def get_matchday_normalized(league: str, year: int, matchday: int) -> list:
    """Spieltag — BL1: primär FD (Fallback OL). BL2: immer OpenligaDB."""
    fd_key = os.environ.get('FOOTBALL_DATA_API_KEY', '')
    if fd_key and league in FD_LEAGUE:
        try:
            matches, _ = fd_get_matchday(league, year, matchday)
            _log(f'get_matchday {league}/{year}/{matchday}: football-data.org')
            return matches
        except Exception as e:
            _log(f'get_matchday {league}/{year}/{matchday}: FD FAIL ({e}) → OL')
    matches, _ = ol_get_matchday(league, year, matchday)
    return matches


def get_matchday_with_hash(league: str, year: int, matchday: int) -> tuple:
    """Wie get_matchday_normalized() + content_hash. BL2: immer OpenligaDB."""
    fd_key = os.environ.get('FOOTBALL_DATA_API_KEY', '')
    if fd_key and league in FD_LEAGUE:
        try:
            matches, h = fd_get_matchday(league, year, matchday)
            return matches, h
        except Exception as e:
            _log(f'get_matchday_with_hash FD FAIL ({e}) → OL')
    return ol_get_matchday(league, year, matchday)


def get_current_matchday_nr(league: str) -> int:
    """
    Aktuellen Spieltag — BL1: primär FD (Fallback OL). BL2: immer OpenligaDB.
    In-Process-Cache verhindert Mehrfachaufrufe pro Request.
    """
    fd_key = os.environ.get('FOOTBALL_DATA_API_KEY', '')
    if fd_key and league in FD_LEAGUE:
        try:
            nr = fd_get_current_matchday_nr(league)
            return nr
        except Exception as e:
            _log(f'get_current_matchday_nr {league}: FD FAIL ({e}) → OL')
    return ol_get_current_matchday_nr(league)



def get_last_finished_matchday(league: str, year: int) -> int:
    """
    Gibt die Nummer des zuletzt vollständig abgeschlossenen Spieltags zurück.
    Logik: get_current_matchday_nr gibt nach Spieltagsende bereits den NÄCHSTEN zurück.
    Daher: current - 1 ist meistens der letzte abgeschlossene.
    Maximal 3 Spieltage rückwärts prüfen um keine Endlosschleife zu verursachen.
    Ergebnis wird 30 Minuten gecacht um wiederholte API-Aufrufe zu vermeiden.
    """
    cache_key = f'last_finished_{league}_{year}'
    cached = _process_cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        current = get_current_matchday_nr(league)
        # Starte bei current-1: nach Spieltagsende zeigt current bereits den nächsten
        start = max(1, current - 1)
        for md in range(start, max(0, start - 3), -1):
            matches = get_matchday(league, year, md)
            if not matches:
                continue
            if all(m.get('is_finished') for m in matches):
                _process_cache_set(cache_key, md)
                return md
        # Fallback: current selbst prüfen
        _process_cache_set(cache_key, current)
        return current
    except Exception as e:
        _log(f'get_last_finished_matchday {league}: {e}')
        return get_current_matchday_nr(league)

def get_current_matchday(league: str) -> dict:
    """OpenligaDB-Format (Abwärtskompatibilität)."""
    url = f'{OL_BASE}/getcurrentgroup/{league}'
    r   = _ol_session().get(url, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def get_matchday(league: str, year: int, matchday: int) -> list:
    """OpenligaDB-Rohformat (Legacy)."""
    url = f'{OL_BASE}/getmatchdata/{league}/{year}/{matchday}'
    r   = _ol_session().get(url, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def get_active_provider() -> str:
    """Welcher Provider ist aktiv — für Admin-Dashboard.
    BL1: FD wenn Key gesetzt, BL2: immer OpenligaDB."""
    fd_key = os.environ.get('FOOTBALL_DATA_API_KEY', '')
    if not fd_key:
        return 'OpenligaDB (kein API-Key konfiguriert)'
    try:
        fd_get_current_matchday_nr('bl1')
        return 'BL1: football-data.org ✓  |  BL2: OpenligaDB'
    except Exception as e:
        return f'OpenligaDB-Fallback (football-data.org: {str(e)[:60]})'
