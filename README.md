# JARVIS – dein kostenloser KI-Assistent

Ein persönlicher Assistent wie bei Iron Man, **komplett kostenlos** mit der KI von Groq:

- 💬 Beantwortet Fragen, per Text oder Sprache (Mikrofon + Stimme)
- ✉️ Liest deine **E-Mails** und **beantwortet** sie (Outlook/Hotmail oder Gmail/GMX/Web.de)
- 📊 Erstellt **PowerPoint**, Word, Excel und andere Dateien zum Download
- 🌐 Sucht aktuelle Infos im Internet (Nachrichten, Wetter …)
- 📱 Läuft im Browser und als App auf dem iPhone

---

## 1. Kostenlosen Groq-Key holen (ohne Kreditkarte)
1. <https://console.groq.com> öffnen und anmelden (mit Google-Konto oder E-Mail)
2. Links auf **„API Keys“** → **„Create API Key“** → Name `jarvis` → **Submit**
3. Key sofort kopieren (beginnt mit `gsk_…`, wird nur einmal angezeigt)

## 2. Online stellen bei Render (kostenlos)
1. <https://render.com> → **„Get Started“** → mit **GitHub** anmelden
2. **„+ New“** → **„Blueprint“** → Repository **Medin-arifi-Javis** auswählen
3. Felder ausfüllen:
   | Feld | Inhalt |
   |---|---|
   | `GROQ_API_KEY` | dein Key aus Schritt 1 |
   | `JARVIS_WEB_PASSWORD` | ein Passwort, das du dir ausdenkst (schützt JARVIS und deine Mails) |
   | `JARVIS_USER_NAME` | wie JARVIS dich nennt, z. B. `Medin` |
   | `MS_CLIENT_ID` | für Outlook, siehe 3A (sonst leer lassen) |
   | `MAIL_ADDRESS`, `MAIL_APP_PASSWORD` | für Gmail/GMX, siehe 3B (sonst leer lassen) |
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
7. In JARVIS oben auf **✉️** tippen → **„Outlook verbinden“** → Code eingeben, mit Outlook anmelden, **Akzeptieren**. Fertig ✅

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
5. In JARVIS: ✉️ → **„Verbindung testen“**. Bei ✓ ist alles fertig.

Andere Anbieter (Firmen-/Schuladressen usw.) erkennt JARVIS meist automatisch. Klappt es nicht, bei Render zusätzlich `MAIL_IMAP_HOST` und `MAIL_SMTP_HOST` eintragen (z. B. `imap.firma.de` und `smtp.firma.de:465`).

---

## So benutzt du JARVIS
- **Sprechen:** 🎙️ antippen und reden. **🔁 Gesprächsmodus:** JARVIS hört nach jeder Antwort automatisch wieder zu.
- **Mails:** „Habe ich neue Mails?“, „Was schreibt Max?“, „Antworte ihm, dass ich morgen Zeit habe“, „Schick eine Mail an lisa@web.de: …“
- **Dateien erstellen:** „Erstelle eine Präsentation über Elektroautos mit 8 Folien“. Die Datei erscheint im Chat mit **Laden**-Knopf.
  JARVIS kann: PowerPoint, Word, Excel, **PDF**, **Diagramme**, **KI-Bilder** („Male mir ein Bild von …“), **QR-Codes**,
  **MP3** (Text vorlesen), **ZIP** sowie Webseiten (HTML), Code, CSV, JSON, SVG, Kalendertermine (.ics) und Kontakte (.vcf).
- **Bilder & Dateien schicken:** 📎 im Eingabefeld antippen. Am iPhone geht auch direkt „Foto aufnehmen“, am PC Einfügen (Strg+V) oder Hineinziehen.
  Bis zu 4 Dateien pro Nachricht: Fotos, Screenshots, PDF, Word, Excel, PowerPoint, Text.
  Beispiele: „Was steht auf dem Foto?“, „Löse die Aufgabe auf dem Bild“, „Fasse das PDF zusammen“,
  „Mach das Foto schwarz-weiß“, „Bau das Foto in eine Präsentation ein“.
- **Neues Gespräch:** ＋ oben rechts

### Sicherheit beim Mailen
JARVIS sendet nur, wenn **du** es in deiner Nachricht verlangst („schick“, „antworte“ …). Neue Mails gehen nur an Adressen, die du selbst nennst oder die dir geschrieben haben. Anweisungen, die in fremden Mails stehen, befolgt JARVIS nicht. Jede gesendete Mail siehst du im Chat.

## Gut zu wissen
- Groq hat kostenlose Limits pro Minute und Tag. Ist eins erreicht, wechselt JARVIS automatisch auf ein anderes Modell. Bei „Kontingent ausgeschöpft“ kurz warten.
- Schreib keine Passwörter o. Ä. in den Chat.
- Render schläft nach 15 Minuten ohne Nutzung ein, der erste Aufruf danach dauert ca. 1 Minute.
- Erstellte Dateien direkt herunterladen; nach einem Neustart sind sie weg.
- KI-Bilder kommen kostenlos von Pollinations.ai. Ohne Konto geht ca. 1 Bild pro 15 Sekunden, eventuell mit kleinem Wasserzeichen.
  Mit einem kostenlosen Konto auf <https://auth.pollinations.ai> den Token bei Render als `POLLINATIONS_TOKEN` eintragen.
- Die Bildbeschreibung für KI-Bilder geht an Pollinations. Hochgeladene Fotos gehen nur an Groq (zum Ansehen).
- Fehlerdetails: Render → jarvis → **Logs**.

## Auf dem eigenen PC (optional)
`start.bat` doppelklicken (Windows), Key in `.env` eintragen → <http://localhost:8000>.

## Für Entwickler
`jarvis/ai.py` (Groq), `jarvis/server.py` (FastAPI), `jarvis/mail/` (Outlook über Microsoft Graph, IMAP/SMTP), `jarvis/tools/` (Dateien, Office, Mail-Werkzeuge mit Sende-Schutz), `jarvis/static/` (Oberfläche). Tests: `python -m pytest tests`
