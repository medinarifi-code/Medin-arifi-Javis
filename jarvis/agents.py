"""Die spezialisierten Agenten von JARVIS.

Jeder Agent hat eine Rolle (Zusatz zum System-Prompt), eigene Werkzeuge und eine bevorzugte Modell-Stufe.
Der Master Agent entscheidet pro Nachricht, welcher Agent übernimmt (route) und zerlegt große Ziele in
Missionen mit Schritten für mehrere Agenten (missions.py).
"""

from __future__ import annotations

CORE = ["remember", "recall"]
OFFICE = ["create_file", "create_pptx", "create_docx", "create_xlsx", "create_pdf", "read_file", "list_files"]
MEDIA = ["create_chart", "create_image", "edit_image", "create_qr", "create_audio", "create_zip"]

AGENTS: dict[str, dict] = {
    "master": {
        "name": "Master Agent", "icon": "🧠", "tier": None,
        "desc": "Zentrale Steuerung: beantwortet Fragen, verteilt Aufgaben, startet Missionen, prüft Ergebnisse.",
        "prompt": ("Du bist der Master Agent. Erledige Aufgaben selbst mit deinen Werkzeugen. Große, mehrstufige Ziele "
                   "(z. B. komplette Webseite, Geschäftsplan, umfangreiche Recherche + Dokument) startest du mit start_mission."),
        "tools": OFFICE + MEDIA + CORE + ["search_web", "fetch_url", "add_task", "list_tasks", "start_mission",
                                           "second_opinion", "delete_file", "notify_me", "mail"],
    },
    "research": {
        "name": "Research Agent", "icon": "🔎", "tier": "research",
        "desc": "Recherchiert, vergleicht Quellen, erkennt das Wichtige, schreibt Berichte.",
        "prompt": ("Du bist der Research Agent. Suche mit search_web (mehrere Suchanfragen aus verschiedenen Blickwinkeln), "
                   "lies wichtige Seiten mit fetch_url, vergleiche Quellen, nenne Unsicherheiten und erstelle bei Bedarf "
                   "einen Bericht (create_pdf/create_docx) mit Quellenangaben."),
        "tools": ["search_web", "fetch_url", "read_file", "create_pdf", "create_docx", "create_file", "create_chart"] + CORE,
    },
    "coding": {
        "name": "Coding Agent", "icon": "💻", "tier": "code",
        "desc": "Schreibt Code, findet Fehler, verbessert Projekte, baut Webseiten und Apps.",
        "prompt": ("Du bist der Coding Agent. Schreibe vollständigen, lauffähigen Code in Dateien (create_file), nie nur "
                   "Ausschnitte. Bei Webseiten: index.html, style.css, script.js in einem Ordner, modern und responsive. "
                   "Lies vorhandene Dateien mit read_file, erkläre gefundene Fehler kurz und speichere die korrigierte "
                   "Fassung. Bündle Projekte mit create_zip."),
        "tools": ["create_file", "read_file", "list_files", "create_zip", "search_web", "fetch_url"] + CORE,
    },
    "browser": {
        "name": "Browser Agent", "icon": "🌐", "tier": "research",
        "desc": "Öffnet und liest Webseiten, sammelt Informationen daraus.",
        "prompt": ("Du bist der Browser Agent. Rufe Webseiten mit fetch_url ab und ziehe die gewünschten Informationen heraus. "
                   "Hinweis: Formulare ausfüllen oder Klicks auf Webseiten sind auf dem Server nicht möglich – sag das ehrlich."),
        "tools": ["fetch_url", "search_web", "create_file", "create_pdf"] + CORE,
    },
    "memory": {
        "name": "Memory Agent", "icon": "🗂️", "tier": "fast",
        "desc": "Verwaltet dein Langzeitgedächtnis: merken, finden, aktualisieren, Zusammenhänge.",
        "prompt": ("Du bist der Memory Agent. Speichere wichtige Informationen mit remember (passende Art, Tags, Projekt), "
                   "finde frühere Infos und Entscheidungen mit recall und lösche nur mit forget (braucht Freigabe)."),
        "tools": ["remember", "recall", "forget", "list_tasks"],
    },
    "business": {
        "name": "Business Agent", "icon": "📈", "tier": "smart",
        "desc": "Geschäftsideen, Businesspläne, Kosten/Einnahmen, Szenarien, professionelle Dokumente.",
        "prompt": ("Du bist der Business Agent. Rechne konkret mit Zahlen, mache Annahmen sichtbar, erstelle bei Bedarf "
                   "Excel-Tabellen mit Formeln (create_xlsx), Diagramme (create_chart) und professionelle Dokumente "
                   "(create_pdf/create_docx/create_pptx). Bei Szenarien/Simulationen: mindestens drei Varianten "
                   "(vorsichtig/realistisch/optimistisch) vergleichen."),
        "tools": ["create_xlsx", "create_chart", "create_pdf", "create_docx", "create_pptx", "search_web"] + CORE,
    },
    "assistant": {
        "name": "Personal Assistant", "icon": "📅", "tier": "smart",
        "desc": "Aufgaben, Tagesplanung, Erinnerungen, Organisation, E-Mail.",
        "prompt": ("Du bist der Personal Assistant Agent. Für Tagespläne: hole plan_day, priorisiere (hoch zuerst, Fristen), "
                   "erkenne Überschneidungen, nutze freie Zeitfenster mit Pausen, und schlage den Plan mit propose_schedule vor "
                   "(der Nutzer gibt ihn frei, erst dann wird er eingetragen). Neue Aufgaben mit add_task, Erinnerungen über "
                   "remind_at."),
        "tools": ["add_task", "list_tasks", "update_task", "plan_day", "propose_schedule", "notify_me"] + CORE + ["mail"],
    },
    "security": {
        "name": "Security Agent", "icon": "🛡️", "tier": "fast",
        "desc": "Prüft sensible Aktionen, verwaltet Freigaben und Berechtigungen, führt das Protokoll.",
        "prompt": ("Du bist der Security Agent. Erkläre Sicherheitsfragen verständlich, prüfe das Protokoll mit "
                   "security_report und weise auf Risiken hin. Du führst selbst keine sensiblen Aktionen aus."),
        "tools": ["security_report"] + CORE,
    },
}

KEYWORDS = {
    "coding": ["code", "programmier", "python", "javascript", "html", "css", "webseite", "website", "app ", "bug",
               "fehler im", "skript", "script", "funktion", "sql", "api"],
    "research": ["recherch", "vergleich", "quellen", "finde heraus", "was gibt es neues", "markt", "studie", "beste lösung"],
    "browser": ["http://", "https://", "www.", "webseite öffnen", "seite lesen", "lies die seite"],
    "business": ["geschäft", "business", "businessplan", "umsatz", "kosten", "einnahmen", "budget", "gewinn", "kalkul",
                 "simul", "szenari", "investition", "preis"],
    "assistant": ["aufgabe", "todo", "to-do", "termin", "tagesplan", "plan für morgen", "organisier", "erinner mich",
                  "kalender", "frist", "zeitplan", "tag planen", "woche planen"],
    "memory": ["merk dir", "merke dir", "erinnerst du", "was weißt du über mich", "vergiss", "gedächtnis"],
    "security": ["sicherheit", "protokoll", "berechtigung", "freigabe"],
}


FILE_WORDS = ["präsentation", "pptx", "powerpoint", "pdf", "excel", "word-dok", "bild ", "male ", "diagramm", "qr",
              "zip", "audio", "mp3", "foto"]


def route(text: str) -> str:
    """Wählt den passenden Agenten für eine Nachricht (schnell, ohne zusätzlichen KI-Aufruf)."""
    t = (text or "").lower()
    scores = {a: sum(1 for k in kws if k in t) for a, kws in KEYWORDS.items()}
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return "master"
    # Datei-Wünsche (Präsentation, Bild, PDF …) kann nur der Master mit allen Werkzeugen sicher erfüllen
    files = any(k in t for k in FILE_WORDS)
    if files and best not in ("coding", "business", "research"):
        return "master"
    # "Webseite erstellen" ist Coding, auch wenn eine URL vorkommt
    if best == "browser" and scores.get("coding", 0) >= scores["browser"]:
        return "coding"
    return best


def public() -> list[dict]:
    return [{"id": k, "name": v["name"], "icon": v["icon"], "desc": v["desc"], "tools": v["tools"],
             "tier": v["tier"] or "auto"} for k, v in AGENTS.items()]
