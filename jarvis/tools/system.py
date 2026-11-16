"""System-Werkzeuge: Datum/Uhrzeit, Webseiten öffnen."""

from __future__ import annotations

import webbrowser
from datetime import datetime

WOCHENTAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


def get_datetime() -> str:
    now = datetime.now().astimezone()
    return f"{WOCHENTAGE[now.weekday()]}, {now:%d.%m.%Y}, {now:%H:%M} Uhr (Zeitzone {now.tzname()})"


def open_website(url: str, client_side: bool = False) -> str:
    from .files import ToolResult

    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    if not client_side:
        webbrowser.open(url)
    return ToolResult(f"Webseite geöffnet: {url}", url=url, action="open")
