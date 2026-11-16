"""Gemeinsame Hilfen für die Postfächer."""

from __future__ import annotations

import html
import re
from datetime import datetime


class MailError(Exception):
    """Verständlicher Fehler, der an JARVIS zurückgegeben wird."""


def html_to_text(value: str) -> str:
    value = re.sub(r"(?is)<(script|style|head).*?</\1>", " ", value)
    value = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t\xa0]+", " ", value)
    return re.sub(r"\n\s*\n+", "\n\n", value).strip()


def short_date(value: datetime | None) -> str:
    if value is None:
        return ""
    try:
        value = value.astimezone()
    except ValueError:
        pass
    return value.strftime("%d.%m.%Y %H:%M")
