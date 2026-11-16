"""Werkzeuge von JARVIS: Schemas und Ausführung."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import files, mail, media, office
from .files import ToolError, ToolResult

S = {"type": "string"}
S_LIST = {"type": "array", "items": S}


def obj(props: dict, required: list[str] | None = None) -> dict:
    schema: dict = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema


I = {"type": "integer"}
N = {"type": "number"}
B = {"type": "boolean"}

# Bewusst knapp gehalten: Die Beschreibungen gehen bei jeder Frage mit (kostenloses Token-Limit!)
FILE_TOOLS = [
    {"name": "create_file", "description": "Textdatei jeder Art: txt, md, html, css, js, py, csv, json, xml, svg, ics, vcf …",
     "parameters": obj({"filename": S, "content": S}, ["filename", "content"])},
    {"name": "create_pptx",
     "description": "PowerPoint. layout: bullets|two_column(right_bullets)|section|quote|image. image = Bildpfad, z. B. uploads/foto.jpg.",
     "parameters": obj({
         "filename": S, "title": S, "subtitle": S, "theme": {"type": "string", "enum": ["jarvis", "ironman", "hell"]},
         "slides": {"type": "array", "items": obj({
             "title": S, "layout": {"type": "string", "enum": ["bullets", "two_column", "section", "quote", "image"]},
             "bullets": S_LIST, "left_title": S, "right_title": S, "right_bullets": S_LIST, "image": S, "notes": S,
         }, ["title", "bullets"])},
     }, ["filename", "title", "slides"])},
    {"name": "create_docx", "description": "Word-Dokument (Brief, Bericht, Referat); image = Bildpfad.",
     "parameters": obj({"filename": S, "title": S, "sections": {"type": "array", "items": obj(
         {"heading": S, "paragraphs": S_LIST, "bullets": S_LIST, "image": S})}}, ["filename", "title", "sections"])},
    {"name": "create_xlsx", "description": "Excel-Tabelle; Formeln beginnen mit '='.",
     "parameters": obj({"filename": S, "sheets": {"type": "array", "items": obj(
         {"name": S, "headers": S_LIST, "rows": {"type": "array", "items": S_LIST}}, ["name", "rows"])}},
         ["filename", "sheets"])},
    {"name": "create_pdf", "description": "PDF-Dokument; table = Zeilen (erste = Kopf), image = Bildpfad.",
     "parameters": obj({"filename": S, "title": S, "sections": {"type": "array", "items": obj(
         {"heading": S, "paragraphs": S_LIST, "bullets": S_LIST, "table": {"type": "array", "items": S_LIST}, "image": S})}},
         ["filename", "title", "sections"])},
    {"name": "create_chart", "description": "Diagramm als PNG.",
     "parameters": obj({"filename": S, "chart_type": {"type": "string", "enum": ["bar", "line", "pie", "scatter"]},
                        "title": S, "labels": S_LIST,
                        "series": {"type": "array", "items": obj({"name": S, "values": {"type": "array", "items": N}}, ["values"])},
                        "x_label": S, "y_label": S}, ["filename", "chart_type", "title", "labels", "series"])},
    {"name": "create_image", "description": "Malt ein Bild mit KI. prompt detailliert auf Englisch.",
     "parameters": obj({"prompt": S, "filename": S, "width": I, "height": I}, ["prompt"])},
    {"name": "create_qr", "description": "QR-Code (PNG).", "parameters": obj({"filename": S, "content": S}, ["content"])},
    {"name": "create_audio", "description": "MP3, die einen Text vorliest.",
     "parameters": obj({"filename": S, "text": S, "lang": S}, ["text"])},
    {"name": "edit_image",
     "description": "Bild bearbeiten/umwandeln (Format = Endung von filename, auch .pdf). crop = 'l,o,r,u' in Prozent.",
     "parameters": obj({"path": S, "filename": S, "width": I, "height": I, "rotate": N, "grayscale": B, "mirror": B,
                        "flip": B, "crop": S, "brightness": N, "contrast": N}, ["path", "filename"])},
    {"name": "create_zip", "description": "Dateien als ZIP bündeln.", "parameters": obj({"filename": S, "files": S_LIST}, ["files"])},
    {"name": "read_file", "description": "Datei lesen (PDF, Word, Excel, PowerPoint, Text); lang → mit start weiterlesen.",
     "parameters": obj({"path": S, "start": I}, ["path"])},
    {"name": "list_files", "description": "Alle Dateien auflisten.", "parameters": None},
]

MAIL_TOOLS = [
    {"name": "list_emails", "description": "Neueste Mails im Posteingang.", "parameters": obj({"unread_only": B, "limit": I})},
    {"name": "search_emails", "description": "Mails suchen.", "parameters": obj({"query": S, "limit": I}, ["query"])},
    {"name": "read_email", "description": "Mail lesen (id aus der Liste).", "parameters": obj({"id": S}, ["id"])},
    {"name": "reply_email", "description": "Mail beantworten – nur wenn der Nutzer es gerade verlangt.",
     "parameters": obj({"id": S, "text": S}, ["id", "text"])},
    {"name": "send_email", "description": "Neue Mail senden – nur wenn der Nutzer es verlangt und die Adresse nennt.",
     "parameters": obj({"to": S, "subject": S, "text": S}, ["to", "subject", "text"])},
]

SEARCH_TOOL = {"name": "search_web", "description": "Aktuelle Infos aus dem Internet (Nachrichten, Wetter, Preise …).",
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
    "create_pdf": "Erstelle PDF", "create_chart": "Zeichne Diagramm", "create_image": "Male Bild (dauert etwas)",
    "create_qr": "Erstelle QR-Code", "create_audio": "Erstelle Audio", "edit_image": "Bearbeite Bild",
    "create_zip": "Packe ZIP", "read_file": "Lese Datei",
    "list_emails": "Prüfe Posteingang", "search_emails": "Durchsuche Mails", "read_email": "Lese Mail",
    "reply_email": "Sende Antwort", "send_email": "Sende Mail",
}


def describe(name: str, args: dict) -> str:
    detail = args.get("filename") or args.get("path") or args.get("query") or args.get("to") or ""
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
    elif name == "read_file":
        _req(args, "path")
        result = files.read_file(out, str(args["path"]), args.get("start") or 0)
    elif name == "create_pdf":
        _req(args, "filename", "title")
        result = media.create_pdf(out, args["filename"], args["title"], list(args.get("sections") or []))
    elif name == "create_chart":
        _req(args, "labels", "series")
        result = media.create_chart(out, args.get("filename") or "diagramm", args.get("chart_type") or "bar",
                                    args.get("title") or "", [str(x) for x in args["labels"]], list(args["series"]),
                                    args.get("x_label") or "", args.get("y_label") or "")
    elif name == "create_image":
        _req(args, "prompt")
        result = media.create_image(out, str(args["prompt"]), args.get("filename") or "", args.get("width") or 1024,
                                    args.get("height") or 1024)
    elif name == "create_qr":
        _req(args, "content")
        result = media.create_qr(out, args.get("filename") or "qr-code", str(args["content"]))
    elif name == "create_audio":
        _req(args, "text")
        result = media.create_audio(out, args.get("filename") or "audio", str(args["text"]), args.get("lang") or "de")
    elif name == "edit_image":
        _req(args, "path", "filename")
        keys = ("width", "height", "rotate", "grayscale", "mirror", "flip", "crop", "brightness", "contrast")
        result = media.edit_image(out, str(args["path"]), str(args["filename"]), **{k: args.get(k) for k in keys})
    elif name == "create_zip":
        _req(args, "files")
        result = media.create_zip(out, args.get("filename") or "dateien", [str(f) for f in args["files"]])
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
