"""Werkzeuge von JARVIS: Schemas (Gemini-tauglich) und Ausführung."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import files, mail, office
from .files import ToolError, ToolResult

S = {"type": "string"}
S_LIST = {"type": "array", "items": S}


def obj(props: dict, required: list[str] | None = None) -> dict:
    schema: dict = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema


FILE_TOOLS = [
    {"name": "create_file",
     "description": "Erstellt eine Textdatei (.txt, .md, .py, .html, .csv, .json …) zum Download. Für Code, Texte, Listen.",
     "parameters": obj({"filename": {**S, "description": "Dateiname mit Endung"},
                        "content": {**S, "description": "Vollständiger Inhalt"}}, ["filename", "content"])},
    {"name": "create_pptx",
     "description": ("Erstellt eine gestaltete PowerPoint-Präsentation (.pptx). Titelfolie entsteht automatisch. "
                     "Layouts: bullets (Standard), two_column (bullets links, right_bullets rechts), "
                     "section (Kapitelfolie), quote (bullets[0]=Zitat, bullets[1]=Quelle)."),
     "parameters": obj({
         "filename": S, "title": S, "subtitle": S,
         "theme": {"type": "string", "enum": ["jarvis", "ironman", "hell"], "description": "Design"},
         "slides": {"type": "array", "items": obj({
             "title": S,
             "layout": {"type": "string", "enum": ["bullets", "two_column", "section", "quote"]},
             "bullets": S_LIST, "left_title": S, "right_title": S, "right_bullets": S_LIST,
             "notes": {**S, "description": "Sprechernotizen"},
         }, ["title", "bullets"])},
     }, ["filename", "title", "slides"])},
    {"name": "create_docx",
     "description": "Erstellt ein Word-Dokument (.docx) mit Überschriften, Absätzen und Aufzählungen (Briefe, Berichte, Referate).",
     "parameters": obj({"filename": S, "title": S,
                        "sections": {"type": "array", "items": obj({"heading": S, "paragraphs": S_LIST, "bullets": S_LIST})}},
                       ["filename", "title", "sections"])},
    {"name": "create_xlsx",
     "description": "Erstellt eine Excel-Tabelle (.xlsx). Zellen als Text; Zahlen werden erkannt, Formeln mit '=' beginnen.",
     "parameters": obj({"filename": S,
                        "sheets": {"type": "array", "items": obj({
                            "name": S, "headers": S_LIST,
                            "rows": {"type": "array", "items": S_LIST}}, ["name", "rows"])}},
                       ["filename", "sheets"])},
    {"name": "list_files", "description": "Listet die bereits erstellten Dateien auf.", "parameters": None},
]

MAIL_TOOLS = [
    {"name": "list_emails", "description": "Zeigt die neuesten Mails im Posteingang (Absender, Betreff, Datum, id).",
     "parameters": obj({"unread_only": {"type": "boolean", "description": "nur ungelesene"},
                        "limit": {"type": "integer", "description": "Anzahl, Standard 10"}})},
    {"name": "search_emails", "description": "Sucht Mails nach Stichwort, Absender oder Betreff.",
     "parameters": obj({"query": S, "limit": {"type": "integer"}}, ["query"])},
    {"name": "read_email", "description": "Liest eine Mail vollständig (id aus list_emails/search_emails).",
     "parameters": obj({"id": S}, ["id"])},
    {"name": "reply_email",
     "description": "Beantwortet eine Mail (an den Absender). Nur benutzen, wenn der Nutzer es gerade verlangt hat.",
     "parameters": obj({"id": S, "text": {**S, "description": "Fertiger Antworttext inkl. Gruß"}}, ["id", "text"])},
    {"name": "send_email",
     "description": "Sendet eine neue Mail. Nur benutzen, wenn der Nutzer es gerade verlangt und die Adresse genannt hat.",
     "parameters": obj({"to": {**S, "description": "Empfänger-Adresse(n), Komma-getrennt"}, "subject": S, "text": S},
                       ["to", "subject", "text"])},
]

SEARCH_TOOL = {"name": "search_web",
               "description": "Sucht aktuelle Informationen im Internet (Nachrichten, Wetter, Sport, Preise, Fakten).",
               "parameters": obj({"query": S}, ["query"])}


@dataclass
class ToolContext:
    output_dir: Path
    mailbox: Any = None
    user_message: str = ""
    known_addresses: set[str] = field(default_factory=set)
    sends: int = 0
    on_event: Callable[[dict], None] | None = None

    def emit(self, event: dict) -> None:
        if self.on_event:
            self.on_event(event)


def tool_definitions(mail_connected: bool) -> list[dict]:
    return [*FILE_TOOLS, SEARCH_TOOL, *(MAIL_TOOLS if mail_connected else [])]


LABELS = {
    "create_file": "Erstelle Datei", "create_pptx": "Erstelle Präsentation", "create_docx": "Erstelle Word-Dokument",
    "create_xlsx": "Erstelle Excel-Tabelle", "list_files": "Prüfe Dateien", "search_web": "Suche im Internet",
    "list_emails": "Prüfe Posteingang", "search_emails": "Durchsuche Mails", "read_email": "Lese Mail",
    "reply_email": "Sende Antwort", "send_email": "Sende Mail",
}


def describe(name: str, args: dict) -> str:
    detail = args.get("filename") or args.get("query") or args.get("to") or ""
    return f"{LABELS.get(name, name)} {detail}".strip()


def _req(args: dict, *keys: str) -> None:
    for k in keys:
        if args.get(k) in (None, "", []):
            raise ToolError(f"Angabe '{k}' fehlt.")


def execute(name: str, args: dict, ctx: ToolContext) -> str:
    """Führt ein Werkzeug aus; erstellte Dateien werden als Event gemeldet."""
    out = ctx.output_dir
    if name == "create_file":
        _req(args, "filename", "content")
        result = files.create_file(out, args["filename"], str(args["content"]))
    elif name == "create_pptx":
        _req(args, "filename", "title", "slides")
        result = office.create_pptx(out, args["filename"], args["title"], list(args["slides"]),
                                    args.get("subtitle"), args.get("theme") or "jarvis")
    elif name == "create_docx":
        _req(args, "filename", "title")
        result = office.create_docx(out, args["filename"], args["title"], list(args.get("sections") or []))
    elif name == "create_xlsx":
        _req(args, "filename", "sheets")
        result = office.create_xlsx(out, args["filename"], list(args["sheets"]))
    elif name == "list_files":
        result = files.list_files(out)
    elif name == "list_emails":
        result = mail.list_emails(ctx, args.get("unread_only", False), args.get("limit", 10))
    elif name == "search_emails":
        _req(args, "query")
        result = mail.search_emails(ctx, str(args["query"]), args.get("limit", 10))
    elif name == "read_email":
        _req(args, "id")
        result = mail.read_email(ctx, str(args["id"]))
    elif name == "reply_email":
        _req(args, "id", "text")
        result = mail.reply_email(ctx, str(args["id"]), str(args["text"]))
    elif name == "send_email":
        _req(args, "to", "subject", "text")
        result = mail.send_email(ctx, str(args["to"]), str(args["subject"]), str(args["text"]))
    else:
        raise ToolError(f"Unbekanntes Werkzeug: {name}")

    if isinstance(result, ToolResult) and result.file is not None:
        rel = result.file.resolve().relative_to(out.resolve()).as_posix()
        ctx.emit({"type": "file", "name": result.file.name, "url": f"/files/{rel}"})
    return str(result)


__all__ = ["ToolContext", "ToolError", "ToolResult", "describe", "execute", "tool_definitions"]
