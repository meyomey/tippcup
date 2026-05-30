"""
Tippcup Telegram-Integration
- Nachrichten an Gruppe senden (Bot-API)
- Webhook für Bot-Befehle (/rangliste, /spieltag)
- Konfiguration: Admin → Telegram
"""
import os
import json
import logging
import requests

logger = logging.getLogger(__name__)

TELEGRAM_API = 'https://api.telegram.org/bot{token}/{method}'
TIMEOUT = 10


def _get_config(db):
    """Telegram-Konfiguration aus DB laden."""
    row = db.execute("SELECT value FROM config WHERE key='telegram_token'").fetchone()
    token = row['value'].strip() if row and row['value'] else ''
    row2 = db.execute("SELECT value FROM config WHERE key='telegram_chat_id'").fetchone()
    chat_id = row2['value'].strip() if row2 and row2['value'] else ''
    return token, chat_id


def send_message(db, text, parse_mode='HTML', disable_preview=True):
    """
    Sendet eine Nachricht an die konfigurierte Telegram-Gruppe.
    Gibt (success: bool, error: str) zurück.
    """
    token, chat_id = _get_config(db)
    if not token or not chat_id:
        return False, 'Telegram nicht konfiguriert (Token oder Chat-ID fehlt)'
    try:
        r = requests.post(
            TELEGRAM_API.format(token=token, method='sendMessage'),
            json={
                'chat_id':                  chat_id,
                'text':                     text,
                'parse_mode':               parse_mode,
                'disable_web_page_preview': disable_preview,
            },
            timeout=TIMEOUT
        )
        data = r.json()
        if data.get('ok'):
            return True, ''
        return False, data.get('description', 'Unbekannter Fehler')
    except Exception as e:
        logger.error(f'Telegram send_message Fehler: {e}')
        return False, str(e)


def test_connection(db):
    """Verbindung testen — sendet eine Test-Nachricht."""
    return send_message(db, '✅ <b>Tippcup</b> — Telegram-Verbindung funktioniert!')


def get_bot_info(token):
    """Bot-Informationen abrufen (Name, Username)."""
    try:
        r = requests.get(
            TELEGRAM_API.format(token=token, method='getMe'),
            timeout=TIMEOUT
        )
        data = r.json()
        if data.get('ok'):
            return data['result']
    except Exception as e:
        logger.debug(f"Ignorierter Fehler: {e}")
    return None


def set_webhook(token, webhook_url):
    """Webhook für Bot-Befehle registrieren."""
    try:
        r = requests.post(
            TELEGRAM_API.format(token=token, method='setWebhook'),
            json={'url': webhook_url},
            timeout=TIMEOUT
        )
        return r.json()
    except Exception as e:
        logger.error(f'Telegram setWebhook Fehler: {e}')
        return {'ok': False, 'description': str(e)}


def delete_webhook(token):
    """Webhook entfernen (Polling-Modus)."""
    try:
        r = requests.post(
            TELEGRAM_API.format(token=token, method='deleteWebhook'),
            timeout=TIMEOUT
        )
        return r.json()
    except Exception as e:
        logger.debug(f"Ignorierter Fehler: {e}")
        return {}


# ── Vorgefertigte Nachrichten ────────────────────────────────

def notify_standings_updated(db, season_name, top3):
    """Wird nach jedem Tabellenupdate aufgerufen."""
    lines = [f'🏆 <b>Rangliste aktualisiert</b> — {season_name}\n']
    medals = ['🥇', '🥈', '🥉']
    for i, entry in enumerate(top3[:3]):
        name  = entry.get('display_name') or entry.get('username', '?')
        score = entry.get('score', 0)
        lines.append(f'{medals[i]} {name} — {score} Pkt.')
    lines.append('\n📊 <a href="https://liga.tippcup.com/leaderboard">Zur Rangliste</a>')
    return send_message(db, '\n'.join(lines))


def notify_deadline_reminder(db, season_name, hours_left, missing_users):
    """Tippschluss-Erinnerung."""
    if hours_left <= 1:
        time_str = f'<b>Noch {hours_left*60:.0f} Minuten</b>'
    else:
        time_str = f'<b>Noch {hours_left:.0f} Stunden</b>'

    text = f'⏰ <b>Tippschluss naht!</b> — {season_name}\n{time_str} zum Abgeben!\n'
    if missing_users:
        names = ', '.join(missing_users[:5])
        if len(missing_users) > 5:
            names += f' +{len(missing_users)-5} weitere'
        text += f'\n⚠️ Noch nicht getippt: {names}'
    text += '\n\n✏️ <a href="https://liga.tippcup.com/tips">Jetzt tippen</a>'
    return send_message(db, text)


def notify_matchday_highlight(db, season_name, matchday, winner_name, winner_score):
    """Spieltag-Highlight-Nachricht."""
    text = (
        f'⚽ <b>Spieltag {matchday} abgeschlossen</b> — {season_name}\n\n'
        f'🎯 Bester Tipper: <b>{winner_name}</b> ({winner_score} Pkt.)\n\n'
        f'📊 <a href="https://liga.tippcup.com/leaderboard">Rangliste ansehen</a>'
    )
    return send_message(db, text)


def handle_bot_command(db, update, base_url):
    """
    Eingehenden Webhook-Update verarbeiten.
    Unterstützte Befehle: /start, /rangliste, /spieltag, /help
    """
    message = update.get('message') or update.get('channel_post', {})
    if not message:
        return

    text    = message.get('text', '')
    chat_id = message.get('chat', {}).get('id')
    if not chat_id or not text.startswith('/'):
        return

    cmd = text.split()[0].split('@')[0].lower()

    _, configured_chat_id = _get_config(db)

    if cmd == '/start':
        reply = (
            '👋 <b>Tippcup-Bot</b>\n\n'
            'Verfügbare Befehle:\n'
            '/rangliste — Aktuelle Bestenliste\n'
            '/spieltag — Letzter Spieltag\n'
            '/help — Diese Hilfe'
        )

    elif cmd == '/rangliste':
        from app import get_active_season
        with db:
            season = db.execute('SELECT * FROM seasons WHERE is_active=1').fetchone()
            if not season:
                reply = '❌ Keine aktive Saison gefunden.'
            else:
                scores = db.execute(
                    '''SELECT u.display_name, u.username, sc.score,
                              sc.std_deviation, sc.volltreffer
                       FROM scores sc JOIN users u ON sc.user_id=u.id
                       WHERE sc.season_id=? AND u.is_active=1
                       ORDER BY sc.score DESC, sc.std_deviation ASC,
                                sc.volltreffer DESC''',
                    (season['id'],)
                ).fetchall()
                if not scores:
                    reply = '📊 Noch keine Punkte berechnet.'
                else:
                    medals = ['🥇','🥈','🥉']
                    lines  = [f'🏆 <b>Rangliste — {season["name"]}</b>\n']
                    for i, s in enumerate(scores):
                        pre  = medals[i] if i < 3 else f'{i+1}.'
                        name = s['display_name'] or s['username']
                        lines.append(f'{pre} {name} — {s["score"]} Pkt.')
                    reply = '\n'.join(lines)

    elif cmd == '/spieltag':
        season = db.execute('SELECT * FROM seasons WHERE is_active=1').fetchone()
        if not season:
            reply = '❌ Keine aktive Saison.'
        else:
            highlight = db.execute(
                '''SELECT h.*, u.display_name, u.username
                   FROM matchday_highlights h JOIN users u ON h.user_id=u.id
                   WHERE h.season_id=? ORDER BY h.matchday DESC LIMIT 1''',
                (season['id'],)
            ).fetchone()
            if not highlight:
                reply = '⚽ Noch kein Spieltag-Highlight vorhanden.'
            else:
                name  = highlight['display_name'] or highlight['username']
                reply = (
                    f'⚽ <b>Spieltag {highlight["matchday"]}</b> — {season["name"]}\n\n'
                    f'🎯 Bester Tipper: <b>{name}</b>\n'
                    f'📊 <a href="{base_url}/leaderboard">Rangliste ansehen</a>'
                )

    elif cmd == '/help':
        reply = (
            '📖 <b>Tippcup-Bot Befehle</b>\n\n'
            '/rangliste — Aktuelle Bestenliste (alle Teilnehmer)\n'
            '/spieltag — Letzter Spieltag Highlight\n'
            '/help — Diese Hilfe\n\n'
            f'🌐 {base_url}'
        )
    else:
        return  # Unbekannte Befehle ignorieren

    # Antwort senden
    token, _ = _get_config(db)
    if token and reply:
        try:
            requests.post(
                TELEGRAM_API.format(token=token, method='sendMessage'),
                json={
                    'chat_id':                  chat_id,
                    'text':                     reply,
                    'parse_mode':               'HTML',
                    'disable_web_page_preview': True,
                },
                timeout=TIMEOUT
            )
        except Exception as e:
            logger.debug(f"Ignorierter Fehler: {e}")


# ── Konfiguration & Hilfsfunktionen ─────────────────────────

def is_enabled(db, setting_key):
    """Prüft ob eine Telegram-Benachrichtigung aktiviert ist (Standard: an)."""
    row = db.execute(
        "SELECT value FROM config WHERE key=?", (f'tg_{setting_key}',)
    ).fetchone()
    # Standard: aktiviert (1), außer explizit deaktiviert (0)
    return (row['value'] != '0') if row else True


def is_matchday_complete(db, season):
    """
    Prüft ob der zuletzt abgeschlossene Spieltag vollständig ist
    (alle Spiele mit is_finished=True). Nutzt get_last_finished_matchday
    statt get_current_matchday_nr um das Off-by-One-Problem zu vermeiden.
    """
    import openliga as ol
    try:
        for league in ('bl1', 'bl2'):
            matchday = ol.get_last_finished_matchday(league, season['year'])
            if not matchday:
                continue
            matches = ol.get_matchday(league, season['year'], matchday)
            if not matches:
                continue
            if any(not m.get('is_finished') for m in matches):
                return False
        return True
    except Exception as e:
        logger.debug(f"is_matchday_complete Fehler: {e}")
        return False


def get_settings(db):
    """Alle Telegram-Einstellungen als Dict zurückgeben."""
    settings = {
        'notify_rangliste': True,
        'notify_highlight': True,
        'notify_deadline':  True,
    }
    for key in settings:
        row = db.execute("SELECT value FROM config WHERE key=?", (f'tg_{key}',)).fetchone()
        if row:
            settings[key] = row['value'] != '0'
    return settings


def save_settings(db, settings_dict):
    """Telegram-Einstellungen speichern."""
    for key, value in settings_dict.items():
        db.execute(
            "INSERT OR REPLACE INTO config (key,value) VALUES (?,?)",
            (f'tg_{key}', '1' if value else '0')
        )
    db.commit()
