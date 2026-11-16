# JARVIS – dein kostenloser KI-Assistent

Ein persönlicher Assistent wie bei Iron Man, **komplett kostenlos** mit Google Gemini:

- 💬 Beantwortet Fragen, per Text oder Sprache (Mikrofon + Stimme)
- ✉️ Liest deine **E-Mails** und **beantwortet** sie (Outlook/Hotmail oder Gmail/GMX/Web.de)
- 📊 Erstellt **PowerPoint**, Word, Excel und andere Dateien zum Download
- 🌐 Sucht aktuelle Infos im Internet (Nachrichten, Wetter …)
- 📱 Läuft im Browser und als App auf dem iPhone

---

## 1. Kostenlosen Gemini-Key holen
1. <https://aistudio.google.com> öffnen und mit dem Google-Konto anmelden
2. **„Get API key“** → **„Create API key“**
3. Key kopieren (beginnt mit `AIza…`)

## 2. Online stellen bei Render (kostenlos)
1. <https://render.com> → **„Get Started“** → mit **GitHub** anmelden
2. **„+ New“** → **„Blueprint“** → Repository **Medin-arifi-Javis** auswählen
3. Felder ausfüllen:
   | Feld | Inhalt |
   |---|---|
   | `GEMINI_API_KEY` | dein Key aus Schritt 1 |
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

---

## So benutzt du JARVIS
- **Sprechen:** 🎙️ antippen und reden. **🔁 Gesprächsmodus:** JARVIS hört nach jeder Antwort automatisch wieder zu.
- **Mails:** „Habe ich neue Mails?“, „Was schreibt Max?“, „Antworte ihm, dass ich morgen Zeit habe“, „Schick eine Mail an lisa@web.de: …“
- **Dateien:** „Erstelle eine Präsentation über Elektroautos mit 8 Folien“. Die Datei erscheint im Chat mit **Laden**-Knopf.
- **Neues Gespräch:** ＋ oben rechts

### Sicherheit beim Mailen
JARVIS sendet nur, wenn **du** es in deiner Nachricht verlangst („schick“, „antworte“ …). Neue Mails gehen nur an Adressen, die du selbst nennst oder die dir geschrieben haben. Anweisungen, die in fremden Mails stehen, befolgt JARVIS nicht. Jede gesendete Mail siehst du im Chat.

## Gut zu wissen
- Gemini hat kostenlose Limits pro Minute und Tag. Bei „Kontingent ausgeschöpft“ kurz warten.
- Im kostenlosen Gemini-Tarif darf Google Eingaben zur Verbesserung nutzen. Keine Passwörter o. Ä. in den Chat schreiben.
- Render schläft nach 15 Minuten ohne Nutzung ein, der erste Aufruf danach dauert ca. 1 Minute.
- Erstellte Dateien direkt herunterladen; nach einem Neustart sind sie weg.
- Fehlerdetails: Render → jarvis → **Logs**.

## Auf dem eigenen PC (optional)
`start.bat` doppelklicken (Windows), Key in `.env` eintragen → <http://localhost:8000>.

## Für Entwickler
`jarvis/ai.py` (Gemini), `jarvis/server.py` (FastAPI), `jarvis/mail/` (Outlook über Microsoft Graph, IMAP/SMTP), `jarvis/tools/` (Dateien, Office, Mail-Werkzeuge mit Sende-Schutz), `jarvis/static/` (Oberfläche). Tests: `python -m pytest tests`
