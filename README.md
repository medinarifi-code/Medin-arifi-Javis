# J.A.R.V.I.S. – Dein persönlicher KI-Assistent

Ein Sprachassistent wie bei Iron Man: Du redest mit JARVIS, und er antwortet mit einer natürlichen Stimme. Er kann alles, was eine normale KI kann (Fragen beantworten, erklären, Texte schreiben, programmieren, übersetzen, im Internet suchen), und er kann **Dateien erstellen**: PowerPoint (.pptx), Word (.docx), Excel (.xlsx), Code, Textdateien und mehr.

- **Gehirn:** Claude (Anthropic API, Modell `claude-opus-5-5`)
- **Stimme:** ElevenLabs. JARVIS fängt schon mit dem ersten Satz an zu sprechen, während der Rest der Antwort noch entsteht.
- **Ohren:** Mikrofon mit deutscher Spracherkennung
- **Oberfläche:** Als **Webseite** im Browser (mit animiertem Arc Reactor) oder im Terminal

---

## Einrichtung unter Windows (einmalig, ca. 5 Minuten)

### 1. Python installieren
Lade **Python 3.11 oder neuer** von <https://www.python.org/downloads/> herunter.
Wichtig: Beim Installieren das Häkchen **„Add Python to PATH“** setzen.

### 2. API-Keys besorgen
| Dienst | Wofür | Wo |
|---|---|---|
| **Anthropic** (Pflicht) | Das Gehirn | <https://console.anthropic.com> → *API Keys* → *Create Key* (Guthaben aufladen nötig) |
| **ElevenLabs** (empfohlen) | Die realistische Stimme | <https://elevenlabs.io> → Profil unten links → *API Keys* |

### 3. JARVIS starten
**Als Webseite (empfohlen):** Doppelklick auf **`start-web.bat`**. Der Browser öffnet sich automatisch unter <http://localhost:8000>.
Für das Mikrofon **Google Chrome oder Microsoft Edge** benutzen. Beim ersten Klick auf das Mikrofon musst du den Zugriff erlauben.

**Im Terminal:** Doppelklick auf **`start.bat`**.

Beim ersten Start wird alles automatisch installiert, danach öffnet sich die Datei `.env` im Editor.
Trage dort deine Keys ein:

```
ANTHROPIC_API_KEY=sk-ant-...
ELEVENLABS_API_KEY=sk_...
JARVIS_USER_NAME=Sir
```

Speichern, Editor schließen, und JARVIS fährt hoch.

---

## Bedienung der Webseite

| Aktion | So geht's |
|---|---|
| Sprechen | Auf das **Mikrofon** oder den **Arc Reactor** klicken (oder Leertaste), dann reden |
| Tippen | Text eingeben und Enter drücken |
| Freihändig reden | Schalter **Gesprächsmodus** einschalten: JARVIS hört nach jeder Antwort automatisch wieder zu |
| Unterbrechen | Auf den Arc Reactor klicken oder Esc drücken |
| Dateien | Erscheinen im Chat mit **Download**-Knopf, alle Dateien über das Ordner-Symbol oben rechts |
| Neues Gespräch | Pfeil-Symbol oben rechts |

**Vom Handy im WLAN:** In `.env` ein Passwort setzen (`JARVIS_WEB_PASSWORD=...`) und `start-web.bat --lan` starten. Dann am Handy `http://<IP-deines-PCs>:8000` öffnen. Die IP findest du mit `ipconfig`. Tippen und Sprachausgabe funktionieren dort. Das Mikrofon erlauben Handy-Browser nur über HTTPS, deshalb geht die Spracheingabe am Handy über die Diktier-Taste der Tastatur.

## Bedienung im Terminal

| Aktion | So geht's |
|---|---|
| Sprechen | **Enter** drücken, dann einfach reden |
| Tippen | Text eingeben und Enter drücken |
| Freihändig reden | `start.bat -c` (Gesprächsmodus: JARVIS hört nach jeder Antwort automatisch wieder zu) |
| Unterbrechen | Beliebige Taste drücken, während JARVIS spricht |
| Gedächtnis löschen | „neues Gespräch“ sagen |
| Beenden | „beenden“, „tschüss“ oder Strg+C |

Weitere Startoptionen: `start.bat --text` (ohne Mikrofon) und `start.bat --mute` (ohne Stimme).

### Beispiele
- „Erstelle mir eine Präsentation über Elektroautos mit 8 Folien für die Schule.“
- „Mach daraus das helle Design und öffne sie.“
- „Schreib mir eine Bewerbung als Word-Dokument für eine Ausbildung als Fachinformatiker.“
- „Erstelle eine Excel-Tabelle für mein monatliches Budget mit Summenformeln.“
- „Programmier mir ein Snake-Spiel in Python und speichere es.“
- „Was gibt es heute für Nachrichten?“ (Internetsuche)
- „Öffne YouTube.“
- „Erklär mir den Satz des Pythagoras.“

Alle erstellten Dateien landen in **Dokumente\JARVIS**.

---

## Stimme anpassen
In der `.env`:
- `ELEVENLABS_VOICE_ID`: eine andere Stimme aus der [ElevenLabs Voice Library](https://elevenlabs.io/app/voice-library). ID kopieren und eintragen. Standard ist „Daniel“ (tief, ruhig, britisch, passt zu JARVIS).
- `ELEVENLABS_MODEL`: `eleven_flash_v2_5` ist am schnellsten und reagiert am flüssigsten. `eleven_multilingual_v2` klingt etwas schöner, ist aber langsamer.

Ohne ElevenLabs-Key benutzt JARVIS automatisch die Windows-Systemstimme.

## Fehlerbehebung
- **„Kein Mikrofon“:** In Windows unter *Einstellungen → Datenschutz → Mikrofon* den Zugriff für Desktop-Apps erlauben. Falls `PyAudio` sich nicht installieren lässt: `.venv\Scripts\pip install pipwin && .venv\Scripts\pipwin install pyaudio`.
- **„Zugangsschlüssel wird abgelehnt“:** Den Anthropic-Key in `.env` prüfen und ob auf dem Konto Guthaben ist.
- **ElevenLabs-Fehler (401/quota):** Key prüfen oder Kontingent aufgebraucht. Im kostenlosen Tarif sind die Zeichen pro Monat begrenzt.
- **Webseite: kein Ton:** Einmal irgendwo auf die Seite klicken. Browser spielen Ton erst nach einer Interaktion ab. Prüfe außerdem den Schalter „Stimme“.
- **Webseite: Mikrofon geht nicht:** Chrome oder Edge verwenden (Firefox unterstützt keine Spracheingabe) und den Zugriff über das Schloss-Symbol in der Adressleiste erlauben.
- **Neu installieren:** Ordner `.venv` löschen und `start.bat` erneut starten.

## Für Entwickler
```
jarvis/
  __main__.py   Hauptschleife (Eingabe → Denken → Sprechen)
  brain.py      Claude-API mit Streaming und Werkzeug-Schleife
  voice.py      ElevenLabs-Stimme, satzweise gestreamt
  ears.py       Spracherkennung
  hud.py        Terminal-Oberfläche
  web.py        Webserver (FastAPI) für die Weboberfläche
  static/       Webseite (HTML, CSS, JavaScript)
  persona.py    Persönlichkeit (System-Prompt)
  tools/        Dateien, PowerPoint, Word, Excel, Webseiten
```
Tests: `python -m pytest tests`
