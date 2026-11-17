# J.A.R.V.I.S. OS – dein persönliches KI-Betriebssystem

Ein persönlicher Assistent wie bei Iron Man: ein **HUD Command Center** (Navy/Neon-Cyan) mit echten Live-Daten,
mehreren spezialisierten Agenten, Langzeitgedächtnis, autonomen Missionen, Automationen und Sprache. Läuft im Browser
und als App auf dem iPhone, kostenlos auf Render.

**Was JARVIS kann (alles echt, nichts gefakt):**
- 💬 **AI Chat** mit Text, Sprache und Anhängen (Fotos, PDF, Office). Er erstellt PowerPoint, Word, Excel, PDF, Diagramme, KI-Bilder, QR-Codes, MP3, ZIP, Webseiten und Code.
- 🤖 **8 Agenten**: Master, Research, Coding, Browser, Memory, Business, Personal Assistant, Security. JARVIS wählt automatisch den passenden (oder du wählst selbst).
- 🚀 **Autonomer Modus**: Ziel eingeben → Master Agent plant → Agenten arbeiten Schritt für Schritt → automatische Prüfung (z. B. fehlende Dateien einer Webseite, Syntaxfehler) → Zusammenfassung. Live-Fortschritt, Pausieren und Abbrechen. Dazu der **Simulationsmodus**: nur planen, nichts ausführen.
- 🧠 **Langzeitgedächtnis**: Fakten, Vorlieben, Ziele, Projekte, Entscheidungen und Personen. Duplikate werden erkannt, Wichtiges lernt JARVIS automatisch aus Gesprächen (abschaltbar). Mit **Memory Map / Knowledge Graph**, Bearbeiten und Löschen.
- 📅 **Tagesplanung**: „Plane meinen Tag morgen“. JARVIS priorisiert, erkennt Überschneidungen, nutzt freie Zeitfenster und schlägt einen Zeitplan vor. **Eingetragen wird erst nach deiner Freigabe.** Danach kommen Erinnerungen.
- 📁 **Project Brain**: Projekte mit Fortschritt, Entscheidungen, nächsten Schritten und erkannten Risiken (Engpässe, Überfälliges, Stillstand). Dazu ein KI-Statusbericht.
- ⚡ **Automation Center**: No-Code-Baukasten aus Auslöser (täglich/wöchentlich/alle X Std/neue Mail/neue Datei) → Bedingung → Aktion mit Agent → Benachrichtigung. Mit Vorlagen, „Jetzt ausführen“ und Laufprotokoll.
- 🧬 **Modellverwaltung**: Groq, OpenRouter, Gemini und Ollama mit echtem Status, Erfolgsquote, Antwortzeit, Tokens und Verbindungstest. Die Reihenfolge je Aufgabentyp (schnell/stark/Code/Recherche/Bilder) legst du selbst fest, bei Ausfall wechselt JARVIS automatisch.
- ⚖️ **Second Opinion**: mehrere Modelle antworten unabhängig, JARVIS vergleicht sie und bildet eine geprüfte Antwort.
- 🔁 **Self-Improvement**: JARVIS analysiert Fehler und 👎-Bewertungen und schlägt Regeln vor. Aktiv werden sie nur mit deiner Zustimmung, Sicherheitsregeln und eigener Code bleiben unangetastet.
- 🛡️ **Security Center**: Freigaben, Berechtigungen, Sicherheitsprotokoll und Status der Schlüssel (nur ob gesetzt, nie der Wert).
- 📈 **System Monitor & Live Intelligence Center**: echte Server-Auslastung, Dienste-Status und der vollständige, filterbare Verlauf aller Aktionen.
- 🎙️ **Stimme**: natürliche Stimmen (8 zur Auswahl), Dauergespräch, Unterbrechen per Tippen, Wake Word „Hey JARVIS“ (wo der Browser es kann).
- 🔎 Globale Suche (Strg/⌘+K), Benachrichtigungen 🔔, anpassbare Widgets, Tastenkürzel.

---

## 1. Kostenlosen Groq-Key holen (ohne Kreditkarte)
1. <https://console.groq.com> öffnen und anmelden (mit Google-Konto oder E-Mail)
2. Links auf **„API Keys“** → **„Create API Key“** → Name `jarvis` → **Submit**
3. Key sofort kopieren (beginnt mit `gsk_…`, wird nur einmal angezeigt)

### Optional: OpenRouter als Ersatz (kostenlos)
Ist Groq überlastet, antwortet JARVIS automatisch mit einem kostenlosen Modell von OpenRouter (z. B. DeepSeek, Llama, Qwen).
1. <https://openrouter.ai/keys> → **Create Key** → Key kopieren (beginnt mit `sk-or-…`)
2. Bei Render → **Environment** → `OPENROUTER_API_KEY` eintragen
Gratis-Limit von OpenRouter: ca. 50 Anfragen pro Tag (mehr mit etwas Guthaben). Internetsuche und Spracherkennung laufen weiter über Groq.

## 2. Online stellen bei Render (kostenlos)
1. <https://render.com> → **„Get Started“** → mit **GitHub** anmelden
2. **„+ New“** → **„Blueprint“** → Repository **Medin-arifi-Javis** auswählen
3. Felder ausfüllen:
   | Feld | Inhalt |
   |---|---|
   | `GROQ_API_KEY` | dein Key aus Schritt 1 |
   | `OPENROUTER_API_KEY` | optional, siehe oben |
   | `JARVIS_WEB_PASSWORD` | ein Passwort, das du dir ausdenkst (schützt JARVIS und deine Mails) |
   | `JARVIS_USER_NAME` | wie JARVIS dich nennt, z. B. `Medin` |
   | `MS_CLIENT_ID` | für Outlook, siehe 3A (sonst leer lassen) |
   | `MAIL_ADDRESS`, `MAIL_APP_PASSWORD` | für Gmail/GMX/iCloud, siehe 3B/3C (sonst leer lassen) |
   | `DATABASE_URL` | **empfohlen**: dauerhaftes Gedächtnis, siehe 4 |
   | `JARVIS_CRON_TOKEN` | optional: für pünktliche Automationen, siehe 5 |
   | `GEMINI_API_KEY`, `OLLAMA_URL` | optional: weitere KI-Anbieter, siehe 6 |
4. **„Deploy Blueprint“** und 3–5 Minuten warten. Oben steht dann dein Link (`https://jarvis-….onrender.com`).
5. **iPhone-App:** Link in Safari öffnen → Teilen □↑ → **„Zum Home-Bildschirm“**

Felder später ändern: Render → **jarvis** → **Environment** → speichern (JARVIS startet neu).


---


## 3A. E-Mail: Outlook / Hotmail verbinden
Microsoft verlangt dafür eine eigene „App-Registrierung“. Das musst du nur einmal machen, es dauert ca. 10 Minuten.

1. <https://entra.microsoft.com> öffnen und mit deinem Microsoft-Konto anmelden.
   Falls Microsoft sagt, dass du kein Verzeichnis hast: zuerst ein kostenloses Azure-Konto anlegen (<https://azure.microsoft.com/free>). Microsoft kann dabei eine Kreditkarte zur Prüfung verlangen. Wenn du das nicht möchtest, nimm Weg 3B mit Gmail.
2. Links **„Anwendungen“** → **„App-Registrierungen“** → **„Neue Registrierung“**
   - Name: `JARVIS`
   - Kontotypen: **„Konten in einem beliebigen Organisationsverzeichnis und persönliche Microsoft-Konten“**
   - Umleitungs-URI: leer lassen → **„Registrieren“**
3. Auf der Übersichtsseite die **„Anwendungs-ID (Client)“** kopieren. Das ist dein `MS_CLIENT_ID`.
4. Links **„Authentifizierung“** → ganz unten **„Öffentliche Clientflows zulassen“** auf **Ja** → **Speichern**
5. Links **„API-Berechtigungen“** → **„Berechtigung hinzufügen“** → **Microsoft Graph** → **Delegierte Berechtigungen** → `Mail.Read` und `Mail.Send` anhaken → **Hinzufügen**
6. Bei Render `MS_CLIENT_ID` eintragen und speichern.
7. In JARVIS **☰ → Einstellungen → 📬 E-Mail verbinden** → **„Outlook verbinden“** → Code eingeben, mit Outlook anmelden, **Akzeptieren**. Fertig ✅

## 3B. E-Mail: Gmail verbinden (einfacher)
1. Im Google-Konto die **Bestätigung in zwei Schritten** einschalten: <https://myaccount.google.com/security>
2. **App-Passwort** erstellen: <https://myaccount.google.com/apppasswords> → Name `JARVIS` → **Erstellen** → das 16-stellige Passwort kopieren
3. Bei Render eintragen: `MAIL_ADDRESS` = deine Gmail-Adresse, `MAIL_APP_PASSWORD` = das App-Passwort

GMX/Web.de funktionieren genauso. Dort in den Einstellungen unter **„POP3/IMAP Abruf“** den Zugriff erlauben und dein normales Passwort oder App-Passwort nehmen.

## 3C. E-Mail: iCloud verbinden (@icloud.com, @me.com, @mac.com)
1. Am iPhone: **Einstellungen → [dein Name] → Anmelden & Sicherheit**. Die **Zwei-Faktor-Authentifizierung** muss an sein.
2. Auf <https://account.apple.com> (früher appleid.apple.com) anmelden → **Anmelden und Sicherheit** → **App-spezifische Passwörter** → **+** → Name `JARVIS` → **Erstellen**
3. Das Passwort kopieren (Format `abcd-efgh-ijkl-mnop`). **Nicht** dein normales Apple-Passwort!
4. Bei Render → **Environment**:
   - `MAIL_ADDRESS` = deine **ganze** Adresse, z. B. `name@icloud.com` (ohne Leerzeichen)
   - `MAIL_APP_PASSWORD` = das app-spezifische Passwort
5. In JARVIS: **Einstellungen → 📬 E-Mail verbinden** → **„Verbindung testen“**. Bei ✓ ist alles fertig.

Andere Anbieter (Firmen-/Schuladressen usw.) erkennt JARVIS meist automatisch. Klappt es nicht, bei Render zusätzlich `MAIL_IMAP_HOST` und `MAIL_SMTP_HOST` eintragen (z. B. `imap.firma.de` und `smtp.firma.de:465`).

---

---

## 4. Dauerhaftes Gedächtnis mit Neon (kostenlos, empfohlen)
Ohne Datenbank vergisst JARVIS bei jedem Neustart von Render alles (Gedächtnis, Aufgaben, Automationen, Dateien).
1. <https://neon.tech> → **Sign up** (mit GitHub oder Google) → Projekt anlegen (Region: Frankfurt)
2. Im Dashboard **„Connect“** → den **Connection string** kopieren (beginnt mit `postgresql://…`)
3. Bei Render → **Environment** → `DATABASE_URL` = dieser String → speichern

Danach speichert JARVIS alles dauerhaft, auch erstellte und hochgeladene Dateien (bis 15 MB pro Datei). Nach einem Neustart werden sie automatisch wiederhergestellt. Im Command Center unter **System Status** steht dann „Neon/Postgres – dauerhaft“.

## 5. Pünktliche Automationen (externer Wecker, kostenlos)
Render (gratis) schläft nach 15 Minuten ohne Besuch ein. Dann können Automationen und Erinnerungen nicht laufen. Lösung:
1. Bei Render `JARVIS_CRON_TOKEN` setzen, z. B. eine lange Zufallszeichenfolge
2. Auf <https://cron-job.org> (kostenlos) einen Job anlegen: URL `https://DEIN-LINK.onrender.com/api/cron?token=DEIN_TOKEN`, alle 10 Minuten

Der Aufruf weckt JARVIS und führt fällige Automationen, Erinnerungen und proaktive Hinweise aus.

## 6. Weitere KI-Anbieter (optional)
| Variable | Anbieter | Kosten |
|---|---|---|
| `GEMINI_API_KEY` | Google Gemini (<https://aistudio.google.com>) | kostenlos mit Limits |
| `OLLAMA_URL` | eigener Ollama-Server (z. B. `https://mein-server:11434`) | kostenlos, eigener Rechner |

Unter **KI-Modelle** siehst du alle Anbieter mit echtem Status und kannst die Reihenfolge je Aufgabentyp ändern.

---

## So benutzt du JARVIS
- **Command Center** (◎): Überblick mit AI Core, System Status, aktiven Agenten, Mission Timeline, Live Feed, LLM Status, System Monitor, Memory Center und Quick Commands. Die Widgets wählst und sortierst du unter **Einstellungen**.
- **Chat** (💬): Oben **Schreiben** oder **Sprechen**. Daneben der Agent (Automatisch oder fest). Der ■-Knopf bricht eine laufende Antwort ab. Unter jeder Antwort siehst du Agent und Modell und kannst 👍/👎 geben.
- **Mittlere Taste** (leuchtender Kern unten): sofort sprechen.
- **Beispiele:** „Plane meinen Tag morgen“ · „Merk dir, dass ich vegetarisch esse“ · „Erinnere mich morgen um 9 an den Zahnarzt“ · „Baue mir eine Webseite für mein Café“ (startet eine Mission) · „Recherchiere die besten E-Bikes 2026 und mach ein PDF“ · „Habe ich neue Mails?“
- **Freigaben:** Zeitpläne eintragen, Dateien oder Erinnerungen löschen: JARVIS fragt im Chat bzw. im Security Center nach. Erst nach **Freigeben** passiert etwas.
- **Sperre:** Bei jedem Öffnen kommt die Passwortabfrage. Nach 5 Minuten im Hintergrund sperrt JARVIS automatisch. 🔒 oben sperrt sofort.

### Sicherheit
- API-Keys liegen **nur** in den Render-Umgebungsvariablen, nie im Browser oder auf GitHub.
- Mails sendet JARVIS nur, wenn **du** es in deiner Nachricht verlangst, und nur an Adressen, die du nennst oder die dir geschrieben haben. Anweisungen in fremden Mails oder Webseiten befolgt er nicht. Das Senden lässt sich im Security Center ganz abschalten.
- Löschen, Zeitpläne und andere sensible Aktionen brauchen deine Freigabe. Käufe und Zahlungen kann JARVIS grundsätzlich nicht.
- Webseiten-Abruf ist gegen Zugriffe auf interne Adressen geschützt.

## Ehrliche Grenzen
- **Computer-Steuerung:** nicht eingebaut. Du nutzt nur das iPhone, und ein Server kann dein iPhone nicht fernsteuern. Dateien verwaltest du im Bereich **Dateien**.
- **Browser-Agent:** liest Webseiten. Klicken, Einloggen oder Formulare ausfüllen kann er nicht.
- **Wake Word:** funktioniert nur, solange JARVIS geöffnet und im Vordergrund ist, und nur in Browsern mit Spracherkennung. Auf dem iPhone als Home-Bildschirm-App oft nicht. Dann die Sprechen-Taste nutzen.
- **Kostenlose Limits:** Groq/OpenRouter/Gemini haben Tageslimits. JARVIS wechselt automatisch, sagt aber ehrlich Bescheid, wenn alle ausgeschöpft sind.
- **Render gratis:** schläft nach 15 Min ein, der erste Aufruf dauert dann ca. 1 Minute (siehe 5 für Automationen).
- KI-Bilder kommen kostenlos von Pollinations.ai (ohne Konto, ca. 1 Bild pro 15 Sekunden).
- Fehlerdetails: Render → jarvis → **Logs**.

## Auf dem eigenen PC (optional)
`start.bat` doppelklicken (Windows), Keys in `.env` eintragen → <http://localhost:8000>. Ohne `DATABASE_URL` nutzt JARVIS eine lokale SQLite-Datei (`~/.jarvis/jarvis.db`).

## Für Entwickler
| Datei | Aufgabe |
|---|---|
| `jarvis/server.py` | FastAPI: Login, Chat-Stream (NDJSON), Stimme, Mail, Dateien |
| `jarvis/api_core.py` | API des Command Centers (Dashboard, Agenten, Gedächtnis, Aufgaben, Missionen, Freigaben, Automationen, Modelle, Suche, Einstellungen, `/api/cron`) |
| `jarvis/core.py` | Kern: verbindet alles; Second Opinion, Self-Improvement, proaktive Hinweise, Project Brain |
| `jarvis/db.py` | Datenbank (Neon-Postgres oder SQLite) mit allen Tabellen, Datei-Sicherung |
| `jarvis/providers.py` | KI-Anbieter, automatische Modellwahl, Nutzungsstatistik |
| `jarvis/ai.py` | Gesprächsschleife mit Werkzeugen und robustem Failover |
| `jarvis/agents.py` | Agenten-Profile und Routing |
| `jarvis/missions.py` | Autonome Missionen (planen → ausführen → prüfen) |
| `jarvis/automations.py` | Automation Center + Zeitplaner |
| `jarvis/memory.py`, `jarvis/planner.py`, `jarvis/security.py` | Gedächtnis, Aufgaben/Tagesplanung, Freigaben/Berechtigungen |
| `jarvis/tools/` | Werkzeuge (Dateien, Office, Medien, Mail, Gehirn-Werkzeuge) |
| `jarvis/static/` | Oberfläche: `app.js` (Kern, Chat, Stimme), `pages.js` (alle Bereiche), `app.css` (HUD) |

Tests: `python -m pytest tests`
