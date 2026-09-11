# vendor/ – gevendorte Python-Bibliotheken

Dieses Verzeichnis enthält `pywebpush` 1.14.1 und alle Abhängigkeiten,
fest eingebettet als Alternative zu `pip install` – nötig, weil das
Netcup/Plesk-Hosting nur FTP-Zugriff ohne SSH erlaubt (kein `pip`
direkt auf dem Server ausführbar).

## Gebaut für
- Python **3.9** (cp39)
- Linux x86_64, manylinux2014 / manylinux_2_17

## Enthaltene Pakete (exakte Versionen)
- pywebpush 1.14.1
- cryptography 43.0.3 (Rust-Core, `abi3` – plattformstabil über alle Python-3.x-Minor-Versionen)
- cffi 2.0.0 (inkl. `_cffi_backend.cpython-39-x86_64-linux-gnu.so`)
- py-vapid 1.9.2
- http-ece 1.2.1 (pure Python, kein offizielles Wheel auf PyPI – Quellcode direkt eingebettet)
- requests 2.32.5, urllib3 2.6.3, certifi, charset_normalizer, idna
- six 1.17.0, pycparser 2.23

## WICHTIG bei Änderungen
- **pywebpush NICHT aktualisieren** ohne Rücksprache – siehe Hinweis in
  `passenger_wsgi.example.py` (API-Inkompatibilität mit neueren Versionen).
- Falls der Server jemals auf eine andere Python-Version wechselt: dieses
  Verzeichnis muss neu gebaut werden (die `.so`-Dateien sind an die
  Python-Version gebunden, reines Kopieren reicht dann nicht).
- Neu bauen (von einer Maschine mit Internetzugriff, kein Zielserver nötig):
  ```bash
  pip download cryptography py-vapid requests six \
    --python-version 39 --implementation cp \
    --platform manylinux2014_x86_64 --only-binary=:all: -d /tmp/pw
  pip download pywebpush==1.14.1 --no-deps -d /tmp/pw
  # http-ece hat kein Wheel -> Sourcecode manuell aus dem sdist kopieren
  ```

Erstellt/aktualisiert: September 2026 (im Rahmen des GitHub-Transfers).
