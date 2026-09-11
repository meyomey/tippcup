"""
passenger_wsgi.example.py
─────────────────────────
Vorlage für die Phusion Passenger WSGI-Konfiguration (Plesk / Netcup).

ANLEITUNG:
  1. Diese Datei nach `passenger_wsgi.py` kopieren
  2. Alle Pflichtfelder ausfüllen
  3. Datei NICHT ins Git-Repository einchecken (steht in .gitignore)

Pfad auf dem Server (Beispiel tippcup.com):
  /var/www/vhosts/hosting12345.a2e64.netcup.net/tippcup.com/tippspiel/
"""

import sys, os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

# Lokale vendor-Bibliotheken (pywebpush 1.14.1 – nicht updaten!)
vendor_dir = os.path.join(BASE_DIR, 'vendor')
if os.path.isdir(vendor_dir):
    sys.path.insert(0, vendor_dir)

# ── Pflichtfelder ────────────────────────────────────────────────────────────
# SECRET_KEY: Zufällige Zeichenkette, min. 32 Zeichen
# Erzeugen: python3 -c "import secrets; print(secrets.token_hex(32))"
os.environ['SECRET_KEY'] = 'HIER_DEIN_GEHEIMER_SCHLUESSEL_EINTRAGEN'

# DB_PATH: Absoluter Pfad zur SQLite-Datenbank
# WICHTIG bei Domain-Umzug: auf neuen Pfad anpassen!
os.environ['DB_PATH'] = os.path.join(BASE_DIR, 'tippspiel.db')

# ── VAPID-Keys für Web-Push-Benachrichtigungen ───────────────────────────────
# WICHTIG: Bei Domain-Umzug DIESELBEN Keys behalten!
# Neue Keys = alle Push-Subscriptions ungültig.
# Keys aus der alten passenger_wsgi.py übernehmen.
#
# os.environ['VAPID_PRIVATE_KEY'] = 'dein-base64-private-key'
# os.environ['VAPID_PUBLIC_KEY']  = 'dein-base64-public-key'
# os.environ['VAPID_EMAIL']       = 'mailto:admin@tippcup.com'
# os.environ['VAPID_KEY_VERSION'] = 'v2'

# ── Debug-Modus (NUR lokal, nie in Produktion!) ──────────────────────────────
# os.environ['FLASK_DEBUG'] = '1'

# ── App starten ──────────────────────────────────────────────────────────────
from app import app, init_db
init_db()
application = app
