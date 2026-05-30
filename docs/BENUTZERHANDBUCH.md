# Tippcup – Benutzerhandbuch

> Bundesliga-Tippspiel für geschlossene Gruppen | liga.tippcup.com

---

## Inhaltsverzeichnis

1. [Quick-Start-Guide](#1-quick-start-guide)
2. [Navigation & Oberfläche](#2-navigation--oberfläche)
3. [Tipps abgeben](#3-tipps-abgeben)
4. [Bestenliste & Punkte](#4-bestenliste--punkte)
5. [Statistik & Auswertungen](#5-statistik--auswertungen)
6. [Archiv (1993–heute)](#6-archiv-1993heute)
7. [Profil & Einstellungen](#7-profil--einstellungen)
8. [Push-Benachrichtigungen](#8-push-benachrichtigungen)
9. [Telegram-Gruppe](#9-telegram-gruppe)
10. [Admin-Bereich](#10-admin-bereich)
11. [Troubleshooting](#11-troubleshooting)

---

## 1. Quick-Start-Guide

### Erste Schritte in 4 Schritten

**Schritt 1 – Einloggen**

Öffne [liga.tippcup.com](https://liga.tippcup.com) im Browser. Benutzername und Passwort erhältst du vom Administrator.

**Schritt 2 – Tipp abgeben**

Klicke in der Navigation auf **Mein Tipp**. Ziehe die Teams per Drag & Drop in die gewünschte Reihenfolge für 1. und 2. Bundesliga. Klicke **Tipp speichern**.

> ⚠️ Tipps können nur vor dem Tippschluss abgegeben werden. Den Termin siehst du auf der Startseite.

**Schritt 3 – Bestenliste verfolgen**

Unter **Bestenliste** siehst du die aktuelle Rangliste aller Mitspieler mit Punktestand.

**Schritt 4 – Profil vervollständigen**

Unter **Profil** kannst du deinen Namen, Lieblingsverein und weitere Daten eintragen.

---

## 2. Navigation & Oberfläche

### Hauptmenü

| Menüpunkt | Inhalt |
|-----------|--------|
| ⚽ **Startseite** | Live-Tabelle beider Bundesligen, Tippschluss-Countdown |
| 📋 **Mein Tipp** | Tipp abgeben oder bearbeiten |
| 🏆 **Bestenliste** | Rangliste aller Teilnehmer |
| 📊 **Alle Tipps** | Tipps aller Mitspieler vergleichen |
| 📈 **Mehr →** | Verlauf, Angsthasen, Rückblick, Head-to-Head, Highlights, Abzeichen, Statistiken, Ewige Tabelle, Spieltage, Medien |
| 💬 **Telegram** | Link zur Telegram-Gruppe (wenn konfiguriert) |

### Dark Mode

Oben rechts das 🌙/☀️-Symbol klicken um zwischen hellem und dunklem Design zu wechseln. Die Einstellung wird gespeichert.

### Spielernamen-Tooltip

Bewege die Maus über einen Spielernamen in der Bestenliste, im Rückblick oder bei den Angsthasen — ein Tooltip zeigt vollen Namen und Lieblingsverein an (sofern im Profil eingetragen).

---

## 3. Tipps abgeben

### So funktioniert der Tipp

Zu Saisonbeginn tippt jeder Teilnehmer die **komplette Abschlusstabelle** der 1. und 2. Bundesliga — alle 18 Mannschaften je Liga in der erwarteten Reihenfolge.

### Tipp-Abgabe

1. **Mein Tipp** im Menü aufrufen
2. In der linken Spalte die Mannschaften per **Drag & Drop** in die gewünschte Reihenfolge bringen
3. Alternativ: Rang direkt in das Zahlenfeld eingeben
4. Auf **Tipp speichern** klicken
5. Bestätigung abwarten (grüne Meldung oben)

### Tipp bearbeiten

Solange die Tippabgabe offen ist, kann der Tipp beliebig oft geändert werden. Einfach erneut **Mein Tipp** aufrufen, ändern und speichern.

### Tippschluss

Der Tippschluss wird vom Administrator festgelegt (üblicherweise vor dem ersten Spieltag). Nach dem Tippschluss ist keine Änderung mehr möglich. Das genaue Datum steht auf der Startseite.

### Tipp anderer Spieler ansehen

Unter **Alle Tipps** siehst du nach Saisonbeginn alle abgegebenen Tipps. Klicke auf einen Spielernamen für die Detailansicht mit Abweichungs-Analyse.

---

## 4. Bestenliste & Punkte

### Punkteberechnung

Die Punkte werden nach jedem abgeschlossenen Spieltag automatisch berechnet.

**Formel:**

```
Maximale Abweichung = 162 Punkte pro Liga (18 Teams)
Score BL1 = 162 − Σ |Tipp-Platz(i) − Ist-Platz(i)|
Score BL2 = 162 − Σ |Tipp-Platz(i) − Ist-Platz(i)|
Gesamtscore = Score BL1 + Score BL2  (max. 324 Punkte)
```

**Beispiel:** Bayern liegt auf Platz 1, du hast Platz 1 getippt → Abweichung 0. Dortmund liegt auf Platz 3, du hast Platz 5 getippt → Abweichung 2.

Je näher dein Tipp an der Realität, desto mehr Punkte.

### Gleichstand

Bei gleicher Punktzahl entscheidet:
1. Niedrigere Standardabweichung σ (konsistentere Tipps)
2. Mehr Volltreffer (exakte Platzierungs-Treffer)

### Bestenlisten-Ansicht

Die Bestenliste hat zwei Ansichten:
- **Karten-Ansicht**: Kompakte Übersicht mit Score und Badges
- **Tabellen-Ansicht**: Detailansicht mit BL1/BL2-Scores, Abweichungen, σ und Volltreffer

Zwischen den Ansichten wechselst du mit den Buttons oben rechts.

### Abzeichen 🏅

Am Ende der Saison (oder manuell durch den Admin) werden Abzeichen vergeben:

| Abzeichen | Wer bekommt es? |
|-----------|-----------------|
| 🏆 Saisonsieger | Meiste Gesamtpunkte |
| 🎯 Volltreffer-König | Meiste exakte Treffer |
| 🔩 Eiserner Tipper | Konsistentester Tipper (niedrigstes σ) |
| 🎲 Wildcard | Höchste Einzelabweichung (mutigster Einzeltipp) |
| 🐇 Größter Angsthase | Tipp am nächsten an der Vorjahrestabelle |
| 🦁 Mutigster Tipper | Tipp am weitesten von der Vorjahrestabelle entfernt |

---

## 5. Statistik & Auswertungen

### Verlauf 📈

Zeigt wie sich die Platzierungen über die Spieltage entwickelt haben — als Liniengrafik und als sortierbare Tabelle. Zwischen **Platzierungs-** und **Score-Ansicht** wechseln.

- Spielernamen in der Legende anklicken um einzelne Linien ein-/auszublenden
- **Alle ausblenden** → dann einzelne Spieler einblenden zum Vergleich

### Alle Tipps 📋

Übersicht aller Tipps aller Teilnehmer. Nach Saisonbeginn mit aktueller Platzierung, Tipp und Abweichung. Sortierbar nach Ist-Platz, Tipp oder Abweichung.

Klick auf einen Spieler → Detailansicht mit League-Tabellen.

### Angsthasen 🐇🦁

Analysiert wie risikobereit der Tipp war: Wer tippt nahe an der Vorjahrestabelle ist ein „Angsthase", wer stark abweicht ein „mutiger Tipper".

> Auf- und Absteiger werden herausgefiltert — zählen nicht für die Berechnung.

### Head-to-Head ⚔️

Direkter Vergleich zweier Spieler Platz für Platz. Welcher Tipp lag näher an der Realität?

Spieler aus den Dropdown-Menüs wählen und **Vergleichen** klicken.

### Rückblick 🗓️

Umfassende Saisonauswertung:
- Abschlusstabelle mit Scores und Abweichungen
- Spieltag-Highlights (bester Tipper je Spieltag)
- Angsthasen-Übersicht
- Ewige Tabelle (alle Saisons)
- Team-Statistiken (welche Teams wurden am häufigsten auf welche Plätze getippt)

### Highlights ⭐

Liste der besten Tipper je Spieltag — wer hat beim jeweiligen Spieltag die meisten Punkte geholt?

### Team-Statistik

Zeigt welche Mannschaften wie oft auf welche Plätze getippt wurden. Hilfreich um zu sehen auf welchen Rang am häufigsten die Bayern getippt wurden.

### Ewige Tabelle

Saisonübergreifende Rangliste: Gesamtpunkte, Schnitt pro Saison, beste Einzelsaison.

### Spieltage ⚽

Live-Anzeige der aktuellen Spieltagsergebnisse beider Bundesligen. Zeigt laufende Spiele, Ergebnisse und Anstoßzeiten. Wird automatisch aktualisiert.

---

## 6. Archiv (1993–heute)

Siehe Abschnitt weiter oben.

## 7. Profil & Einstellungen

### Profil aufrufen

Oben rechts auf deinen Namen klicken → **Profil**.

### Daten bearbeiten

Im Abschnitt **Daten ändern** kannst du folgendes ändern:

| Feld | Beschreibung |
|------|-------------|
| **Spielername** | Wird überall angezeigt (Bestenliste, Tipps etc.) |
| **Voller Name** | Erscheint im Hover-Tooltip über deinem Spielernamen |
| **Lieblingsverein** | Erscheint ebenfalls im Hover-Tooltip (mit ⚽) |
| **E-Mail** | Für Rundbrief-Erinnerungen |
| **Mobilnummer** | Optional, nur für Admin sichtbar |
| **Neues Passwort** | Leer lassen wenn du es nicht ändern möchtest |

> Zum Speichern musst du immer dein **aktuelles Passwort** eingeben.

### Meine Tipps im Profil

Zeigt deine abgegebenen Tipps für BL1 und BL2 mit aktueller Platzierung und Abweichung — identisch zur Einzelansicht unter „Alle Tipps". Die Spalten sind sortierbar.

---

## 8. Push-Benachrichtigungen

### Push aktivieren

1. Oben rechts auf das 🔔-Symbol klicken (oder in den Browser-Einstellungen)
2. Browser fragt nach Erlaubnis → **Erlauben** klicken
3. Push ist jetzt aktiv — du bekommst Benachrichtigungen wenn sich deine Platzierung ändert

### Was wird gemeldet?

- Du wirst überholt (jemand zieht an dir vorbei)
- Du überholst jemanden

### Push deaktivieren

Erneut auf das 🔔-Symbol klicken → **Push deaktivieren**.

### Browserunterstützung

Push funktioniert in Chrome, Edge, Firefox und Safari (iOS 16.4+). In Safari muss die Website erst zum Home-Bildschirm hinzugefügt werden.

---

## 9. Telegram-Gruppe

### Zur Gruppe beitreten

Wenn in der Navigation ein **Telegram**-Link erscheint, klicke darauf um der Gruppe beizutreten.

### Bot-Befehle in der Gruppe

Der Tippcup-Bot antwortet auf folgende Befehle:

| Befehl | Antwort |
|--------|---------|
| `/rangliste` | Aktuelle Top-10 mit Punktestand |
| `/spieltag` | Letzter Spieltag — bester Tipper |
| `/help` | Alle verfügbaren Befehle |

### Automatische Nachrichten

Der Bot postet automatisch (wenn vom Admin aktiviert):
- 🏆 **Rangliste** nach jedem vollständig abgeschlossenen Spieltag (Top 3)
- ⚽ **Spieltag-Highlight** — wer war bester Tipper
- ⏰ **Tippschluss-Erinnerung** (manuell vom Admin ausgelöst)

---

## 10. Admin-Bereich

> Dieser Abschnitt ist nur für Administratoren relevant.

### Zugang

Oben rechts auf deinen Namen → **Admin** (nur sichtbar für Admins).

### Saison verwalten

**Admin → Saison:**

| Aktion | Beschreibung |
|--------|-------------|
| **Saison anlegen** | Neue Saison mit Jahr und Name |
| **Saison starten** | Saisonstart bekannt geben (Tippabgabe öffnet sich) |
| **Tippabgabe sperren** | Keine neuen Tipps mehr möglich |
| **Tippschluss laden** | Automatisch aus API (Anstoß erstes Spiel) |
| **Beide Tabellen abrufen** | API → Tabelle aktualisieren → Punkte berechnen → Highlights → Badges → Push |
| **Punkte neu berechnen** | Manuell Scores neu berechnen |
| **Erinnerungsmail** | E-Mail an alle ohne Tipp senden |

### Teams verwalten

**Admin → Teams:**

- **Beide Ligen importieren**: Lädt Teams von der API und aktualisiert die Datenbank. Bestehende Tipps bleiben immer erhalten.
- **Liga einzeln neu laden**: ⟳-Button neben der Liga
- **Tipps reparieren**: Falls nach einer Team-Änderung Tipps fehlen

> Die Teams werden beim Import intelligent gematcht — Borussia Dortmund und Borussia Mönchengladbach werden korrekt unterschieden.

### Benutzer verwalten

**Admin → Benutzer:**

- Neue Benutzer anlegen (Benutzername, Spielername, E-Mail, Passwort)
- Benutzer bearbeiten (auch Mobilnummer, Lieblingsverein)
- Benutzer deaktivieren (bleiben in der DB, können sich nicht mehr einloggen)
- Benutzer löschen (entfernt alle Tipps, Scores, Chat-Nachrichten dieses Benutzers)

### Saison verwalten

**Admin → Saison:**

- Neue Saison anlegen (deaktiviert automatisch die bisherige)
- Andere Saison aktivieren
- **Inaktive Saisons löschen** (🗑️-Button) – entfernt alle Tipps, Teams, Scores dieser Saison unwiderruflich. Die aktive Saison kann nicht gelöscht werden.

### API-Einstellungen

**Admin → API-Einstellungen:**

- **football-data.org Key** eintragen (kostenlos unter football-data.org/client/register)
- **Push-Status**: Wie viele Geräte sind registriert, wer hat Push aktiv
- **VAPID-Key-Rotation**: Falls Push-Probleme auftreten

### Telegram konfigurieren

**Admin → Telegram:**

1. **Bot-Token** von @BotFather eintragen
2. **Gruppen-Chat-ID** eintragen (negative Zahl)
3. **Gruppen-Einladungslink** eintragen (erscheint dann in der Navbar)
4. **Test senden** zum Überprüfen
5. **Benachrichtigungs-Einstellungen** (Rangliste, Highlight, Erinnerung) aktivieren/deaktivieren
6. **Bot-Befehle (Webhook)** aktivieren für `/rangliste`, `/spieltag` etc.

**Chat-ID herausfinden:**
1. Bot in Gruppe einladen und zum Admin machen
2. Jemand schreibt eine Nachricht in die Gruppe
3. Browser aufrufen: `https://api.telegram.org/botTOKEN/getUpdates`
4. In der JSON-Antwort: `"chat": {"id": -1001234567890}`

### E-Mail & Rundbrief

**Admin → E-Mail:**

SMTP-Daten konfigurieren, dann:
- **Erinnerungsmail**: Automatisch an alle Benutzer ohne Tipp
- **Rundbrief**: Eigene Nachricht an alle (oder ausgewählte) Benutzer

### Backup & Restore

**Admin → Backup:**

Wähle per Checkbox aus, was gesichert werden soll:

| Komponente | Inhalt | Empfehlung |
|---|---|---|
| ☑ Datenbank | Alle Tipps, Scores, Archiv, Chat | wöchentlich |
| ☐ Medien & Uploads | Bilder, Videos, Dokumente | monatlich |
| ☐ Konfigurationsdateien | app.py, passenger_wsgi.py, … | vor Code-Updates |

Das Backup wird als **ZIP-Archiv** heruntergeladen. Der Dateiname enthält Datum, Uhrzeit und Inhalt (z.B. `tippcup_backup_20250501_143022_db_uploads.zip`).

**Restore:** ZIP oder einzelne `.db`-Datei hochladen. Die DB wird vor der Übernahme auf Gültigkeit geprüft. Die aktuelle DB wird als `.before_restore`-Kopie gesichert. Nach dem Restore wird man automatisch abgemeldet.

### Diagnose

**Admin → Diagnose:**

Zeigt den aktuellen Zustand des Systems:
- Anzahl Teams, Tipps, Standings pro Saison
- **Telegram-Status**: Token/Chat-ID konfiguriert, Benachrichtigungen aktiv, letzter gesendeter Spieltag, `is_matchday_complete`-Ergebnis
- **Benachrichtigungs-Log**: Alle Versendungsversuche mit Zeitpunkt, Typ, Status und Begründung
- **Reset-Buttons** für Deduplizierung (wenn eine Benachrichtigung erneut gesendet werden soll)

### Tipp-Archiv

**Admin → Tipp-Archiv:**

- **Abschluss-CSV importieren**: Historische Saison-Abschlusstabellen importieren
- **Angsthasen-CSV importieren**: Historische Angsthasen-Tabellen importieren
- **Name-Mapping**: CSV-Namen mit App-Accounts verknüpfen (für Karriere-Ansicht)
- **Archiv-Saison löschen**: Einzelne Saison aus dem Archiv entfernen

### Sicherheit

**Admin → Sicherheit:**

Zeigt fehlgeschlagene Login-Versuche. Nach 5 Fehlversuchen wird eine IP für 15 Minuten gesperrt. Manuelle Entsperrung möglich.

### Abzeichen

**Admin → Abzeichen:**

Manuell **Abzeichen neu berechnen** wenn die automatische Berechnung nach dem Spieltag-Update nicht ausgeführt wurde.

---

## 11. Troubleshooting

### Ich kann meinen Tipp nicht mehr speichern

**Ursache:** Die Tippabgabe wurde vom Admin gesperrt (Tippschluss).
**Lösung:** Kontakt mit dem Administrator. Nach Tippschluss sind keine Änderungen möglich.

---

### Meine Punkte stimmen nicht / wurden nicht aktualisiert

**Ursache:** Die Tabelle wurde noch nicht abgerufen.
**Lösung:** Admin muss unter **Saison → Beide Tabellen abrufen** die Daten aktualisieren.

---

### Ich sehe keinen Telegram-Link in der Navigation

**Ursache:** Der Gruppen-Einladungslink wurde noch nicht konfiguriert.
**Lösung:** Admin muss unter **Admin → Telegram** den Einladungslink eintragen und speichern.

---

### Push-Benachrichtigungen kommen nicht an

**Mögliche Ursachen und Lösungen:**

| Problem | Lösung |
|---------|--------|
| Browser-Benachrichtigungen deaktiviert | Browser-Einstellungen → Seite erlauben |
| Safari (iOS) | Seite zum Home-Bildschirm hinzufügen, dann nochmal aktivieren |
| Push deaktiviert und neu aktiviert | Erneut auf 🔔 klicken → Erlauben |
| Subscription abgelaufen | Erneut Push aktivieren |

---

### Spielernamen-Tooltip erscheint nicht

**Ursache:** Kein voller Name oder Lieblingsverein im Profil eingetragen.
**Lösung:** Profil aufrufen → **Voller Name** und/oder **Lieblingsverein** eintragen → Speichern. Danach erscheint beim Hover über deinen Namen in der Bestenliste ein Tooltip.

---

### Meine Tipps sind nach dem Team-Import verschwunden

**Ursache:** Beim Team-Löschen wurden die Tipps vorübergehend deaktiviert.
**Lösung:** Admin → Teams → **Tipps reparieren** klicken. Die Tipps werden automatisch aus dem Backup wiederhergestellt.

---

### Die Seite lädt sehr langsam

**Mögliche Ursachen:**
- Externe API (football-data.org / OpenligaDB) antwortet langsam
- Server-Cache ist noch kalt (erster Aufruf nach Neustart)

**Lösung:** Seite nach 30 Sekunden nochmal laden. Falls dauerhaft langsam: Administrator informieren.

---

### Login-Fehler nach mehreren Versuchen

**Ursache:** Brute-Force-Schutz — nach 5 Fehlversuchen wird die IP für 15 Minuten gesperrt.
**Lösung:** 15 Minuten warten oder Administrator um manuelle Entsperrung bitten.

---

### Angsthasen-Seite zeigt keine Daten

**Ursache:** Für den Angsthasen-Vergleich wird die Vorjahrestabelle benötigt. Diese wird erst nach Saisonbeginn automatisch gespeichert.
**Lösung:** Abwarten bis die ersten Spieltage gespielt wurden.

---

### Telegram-Testmeldung funktioniert nicht

**Häufige Fehler:**

| Fehlermeldung | Ursache | Lösung |
|---------------|---------|--------|
| `Unauthorized` | Bot-Token falsch | Token bei @BotFather prüfen |
| `Bad Request: chat not found` | Chat-ID falsch | ID prüfen (muss negativ sein für Gruppen) |
| `Forbidden: bot was kicked` | Bot aus Gruppe entfernt | Bot erneut einladen und zum Admin machen |

---

*Tippcup wird entwickelt und gepflegt von Heiko · Stand Mai 2026*
