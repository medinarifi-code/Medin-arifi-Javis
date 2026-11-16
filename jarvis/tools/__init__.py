"""Werkzeuge, die JARVIS benutzen kann: Schemas für Claude + Ausführung."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from . import files, office, system
from .files import ToolError, ToolResult

# Server-Werkzeug: Anthropic führt die Websuche selbst aus.
WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 5}

_STR = {"type": "string"}
_STR_LIST = {"type": "array", "items": _STR}


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


CUSTOM_TOOLS: list[dict] = [
    {
        "name": "create_file",
        "description": "Erstellt eine Textdatei beliebigen Typs (z.B. .txt, .md, .py, .html, .csv, .json, .js) im JARVIS-Ordner. Für Code, Notizen, Aufsätze, Listen, Webseiten usw.",
        "input_schema": _obj({
            "filename": {**_STR, "description": "Dateiname mit Endung, z.B. 'einkaufsliste.txt' oder 'projekt/main.py'"},
            "content": {**_STR, "description": "Vollständiger Inhalt der Datei"},
            "overwrite": {"type": "boolean", "description": "Vorhandene Datei überschreiben (Standard: false)"},
        }, ["filename", "content"]),
    },
    {
        "name": "create_pptx",
        "description": (
            "Erstellt eine professionell gestaltete PowerPoint-Präsentation (.pptx). Eine Titelfolie wird automatisch "
            "aus 'title' und 'subtitle' erzeugt; 'slides' enthält die weiteren Folien. Layouts: 'bullets' (Titel + "
            "Stichpunkte), 'two_column' (Vergleich: bullets links, right_bullets rechts), 'section' (Kapiteltrenner, "
            "erster Stichpunkt als Untertitel), 'quote' (erster Stichpunkt = Zitat, zweiter = Quelle)."
        ),
        "input_schema": _obj({
            "filename": {**_STR, "description": "Dateiname, z.B. 'Elektroautos.pptx'"},
            "title": {**_STR, "description": "Titel der Präsentation"},
            "subtitle": {**_STR, "description": "Untertitel, z.B. Name, Datum oder Fach"},
            "theme": {"type": "string", "enum": ["jarvis", "ironman", "hell"],
                      "description": "Design: jarvis (dunkel/blau), ironman (dunkel/rot-gold), hell (hell/seriös)"},
            "slides": {"type": "array", "items": _obj({
                "title": _STR,
                "layout": {"type": "string", "enum": ["bullets", "two_column", "section", "quote"]},
                "bullets": _STR_LIST,
                "left_title": _STR,
                "right_title": _STR,
                "right_bullets": _STR_LIST,
                "notes": {**_STR, "description": "Sprechernotizen für den Vortragenden"},
            }, ["title", "bullets"])},
        }, ["filename", "title", "slides"]),
    },
    {
        "name": "create_docx",
        "description": "Erstellt ein Word-Dokument (.docx) mit Überschriften, Absätzen und Aufzählungen. Für Briefe, Berichte, Referate, Bewerbungen usw.",
        "input_schema": _obj({
            "filename": _STR,
            "title": _STR,
            "sections": {"type": "array", "items": _obj({
                "heading": _STR,
                "paragraphs": _STR_LIST,
                "bullets": _STR_LIST,
            }, [])},
        }, ["filename", "title", "sections"]),
    },
    {
        "name": "create_xlsx",
        "description": "Erstellt eine Excel-Tabelle (.xlsx). Zellen dürfen Excel-Formeln enthalten (z.B. '=SUMME(B2:B10)' bzw. '=SUM(B2:B10)').",
        "input_schema": _obj({
            "filename": _STR,
            "sheets": {"type": "array", "items": _obj({
                "name": _STR,
                "headers": _STR_LIST,
                "rows": {"type": "array", "items": {"type": "array", "items": {"type": ["string", "number", "null"]}}},
            }, ["name", "rows"])},
        }, ["filename", "sheets"]),
    },
    {
        "name": "read_file",
        "description": "Liest den Inhalt einer Datei (Text, Code, .pptx, .docx, .xlsx). Relative Pfade beziehen sich auf den JARVIS-Ordner; absolute Pfade sind erlaubt.",
        "input_schema": _obj({"path": _STR}, ["path"]),
    },
    {
        "name": "list_files",
        "description": "Listet die Dateien im JARVIS-Ordner (oder in einem angegebenen Ordner) auf.",
        "input_schema": _obj({"folder": {**_STR, "description": "Optionaler Ordnerpfad"}}, []),
    },
    {
        "name": "open_file",
        "description": "Öffnet eine Datei oder einen Ordner mit dem Standardprogramm (z.B. PowerPoint). Relative Pfade beziehen sich auf den JARVIS-Ordner.",
        "input_schema": _obj({"path": _STR}, ["path"]),
    },
    {
        "name": "open_website",
        "description": "Öffnet eine Webseite im Standardbrowser des Nutzers (z.B. YouTube, Google, Netflix).",
        "input_schema": _obj({"url": _STR}, ["url"]),
    },
    {
        "name": "get_datetime",
        "description": "Gibt das aktuelle Datum, den Wochentag und die Uhrzeit zurück.",
        "input_schema": _obj({}, []),
    },
]

# Große Werkzeug-Eingaben (Dateiinhalte, Folien) direkt mitstreamen.
for _tool in CUSTOM_TOOLS:
    _tool["eager_input_streaming"] = True

TOOL_DEFINITIONS: list[dict] = [*CUSTOM_TOOLS, WEB_SEARCH_TOOL]
_SCHEMAS = {t["name"]: t["input_schema"] for t in CUSTOM_TOOLS}


def _check(value: Any, schema: dict, where: str) -> str | None:
    """Kleine JSON-Schema-Prüfung (Typen, Pflichtfelder, enum)."""
    types = schema.get("type")
    types = types if isinstance(types, list) else [types]
    ok = {
        "object": isinstance(value, dict), "array": isinstance(value, list),
        "string": isinstance(value, str), "boolean": isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "null": value is None,
    }
    if not any(ok.get(t, False) for t in types):
        return f"{where}: erwartet {'/'.join(types)}"
    if "enum" in schema and value not in schema["enum"]:
        return f"{where}: muss einer von {schema['enum']} sein"
    if isinstance(value, dict):
        for req in schema.get("required", []):
            if req not in value:
                return f"{where}: Pflichtfeld '{req}' fehlt"
        for k, v in value.items():
            sub = schema.get("properties", {}).get(k)
            if sub is None:
                if schema.get("additionalProperties") is False:
                    return f"{where}: unbekanntes Feld '{k}'"
                continue
            if err := _check(v, sub, f"{where}.{k}"):
                return err
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            if err := _check(item, schema["items"], f"{where}[{i}]"):
                return err
    return None


def validate(name: str, args: Any) -> str | None:
    schema = _SCHEMAS.get(name)
    if schema is None:
        return f"Unbekanntes Werkzeug: {name}"
    return _check(args, schema, "input")


def execute_tool(name: str, args: dict, output_dir: Path, client_side: bool = False) -> str:
    """Führt ein Werkzeug aus. Wirft ToolError bei erwartbaren Fehlern.

    client_side=True (Web-Modus): Dateien/Webseiten öffnet der Browser statt des Servers.
    """
    handlers: dict[str, Callable[[], str]] = {
        "create_file": lambda: files.create_file(output_dir, args["filename"], args["content"],
                                                 bool(args.get("overwrite", False))),
        "create_pptx": lambda: office.create_pptx(output_dir, args["filename"], args["title"], args["slides"],
                                                  args.get("subtitle"), args.get("theme", "jarvis")),
        "create_docx": lambda: office.create_docx(output_dir, args["filename"], args["title"], args["sections"]),
        "create_xlsx": lambda: office.create_xlsx(output_dir, args["filename"], args["sheets"]),
        "read_file": lambda: files.read_file(output_dir, args["path"]),
        "list_files": lambda: files.list_files(output_dir, args.get("folder")),
        "open_file": lambda: files.open_path(output_dir, args["path"], client_side),
        "open_website": lambda: system.open_website(args["url"], client_side),
        "get_datetime": system.get_datetime,
    }
    if name not in handlers:
        raise ToolError(f"Unbekanntes Werkzeug: {name}")
    return handlers[name]()


def describe_call(name: str, args: dict) -> str:
    """Kurzbeschreibung für die Anzeige im Terminal."""
    labels = {
        "create_file": "Erstelle Datei", "create_pptx": "Erstelle Präsentation", "create_docx": "Erstelle Word-Dokument",
        "create_xlsx": "Erstelle Excel-Tabelle", "read_file": "Lese Datei", "list_files": "Durchsuche Ordner",
        "open_file": "Öffne", "open_website": "Öffne Webseite", "get_datetime": "Prüfe Uhrzeit",
    }
    detail = args.get("filename") or args.get("path") or args.get("url") or args.get("folder") or ""
    return f"{labels.get(name, name)} {detail}".strip()


__all__ = ["TOOL_DEFINITIONS", "ToolError", "ToolResult", "execute_tool", "validate", "describe_call"]
