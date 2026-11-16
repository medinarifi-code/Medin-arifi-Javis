"""Datei-Werkzeuge: Dateien erstellen, lesen, auflisten, öffnen."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

MAX_READ_CHARS = 100_000


class ToolError(Exception):
    """Fehler, der als verständliche Meldung an die KI zurückgegeben wird."""


class ToolResult(str):
    """Werkzeug-Ergebnis (Text für die KI) mit optionaler Datei bzw. URL für die Oberfläche."""

    file: Path | None
    url: str | None
    action: str | None

    def __new__(cls, text: str, file: Path | None = None, url: str | None = None, action: str | None = None):
        obj = super().__new__(cls, text)
        obj.file, obj.url, obj.action = file, url, action
        return obj


def safe_path(output_dir: Path, filename: str, default_suffix: str | None = None) -> Path:
    """Löst einen Dateinamen innerhalb des Ausgabeordners auf.

    Verhindert, dass die KI außerhalb des JARVIS-Ordners schreibt ("..", absolute Pfade).
    """
    if not filename or not filename.strip():
        raise ToolError("Es wurde kein Dateiname angegeben.")
    root = output_dir.resolve()
    target = (root / filename.strip()).resolve()
    if not target.is_relative_to(root):
        raise ToolError(f"Dateien dürfen nur im Ordner {root} gespeichert werden.")
    if default_suffix and target.suffix.lower() != default_suffix:
        target = target.with_name(target.name + default_suffix)
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def unique(path: Path) -> Path:
    """Hängt (2), (3) ... an, falls die Datei schon existiert."""
    if not path.exists():
        return path
    i = 2
    while True:
        candidate = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not candidate.exists():
            return candidate
        i += 1


def _resolve_existing(output_dir: Path, path: str) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = output_dir / p
    p = p.resolve()
    if not p.exists():
        raise ToolError(f"Die Datei {p} existiert nicht.")
    return p


def create_file(output_dir: Path, filename: str, content: str, overwrite: bool = False) -> str:
    path = safe_path(output_dir, filename)
    if not overwrite:
        path = unique(path)
    path.write_text(content, encoding="utf-8")
    return ToolResult(f"Datei gespeichert: {path}", file=path)


def read_file(output_dir: Path, path: str) -> str:
    p = _resolve_existing(output_dir, path)
    suffix = p.suffix.lower()
    if suffix == ".pptx":
        from pptx import Presentation

        prs = Presentation(str(p))
        parts = []
        for i, slide in enumerate(prs.slides, 1):
            texts = [sh.text_frame.text for sh in slide.shapes if sh.has_text_frame and sh.text_frame.text.strip()]
            parts.append(f"--- Folie {i} ---\n" + "\n".join(texts))
        text = "\n".join(parts)
    elif suffix == ".docx":
        from docx import Document

        text = "\n".join(par.text for par in Document(str(p)).paragraphs)
    elif suffix == ".xlsx":
        from openpyxl import load_workbook

        wb = load_workbook(str(p), data_only=True, read_only=True)
        parts = []
        for ws in wb.worksheets:
            parts.append(f"--- Blatt {ws.title} ---")
            for row in ws.iter_rows(values_only=True):
                parts.append("\t".join("" if v is None else str(v) for v in row))
        text = "\n".join(parts)
    else:
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = p.read_text(encoding="latin-1")
    if len(text) > MAX_READ_CHARS:
        text = text[:MAX_READ_CHARS] + f"\n\n[... gekürzt, Datei hat {len(text)} Zeichen ...]"
    return text


def list_files(output_dir: Path, folder: str | None = None) -> str:
    base = _resolve_existing(output_dir, folder) if folder else output_dir.resolve()
    if not base.is_dir():
        raise ToolError(f"{base} ist kein Ordner.")
    entries = sorted(base.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    if not entries:
        return f"Der Ordner {base} ist leer."
    lines = [f"Inhalt von {base}:"]
    for e in entries[:200]:
        if e.is_dir():
            lines.append(f"[Ordner] {e.name}")
        else:
            lines.append(f"{e.name} ({e.stat().st_size:,} Bytes)".replace(",", "."))
    return "\n".join(lines)


def open_path(output_dir: Path, path: str, client_side: bool = False) -> str:
    p = _resolve_existing(output_dir, path)
    if client_side:
        # Web-Modus: Der Browser öffnet bzw. lädt die Datei.
        if not p.is_file() or not p.is_relative_to(output_dir.resolve()):
            raise ToolError("Im Web-Modus können nur Dateien aus dem JARVIS-Ordner geöffnet werden.")
        return ToolResult(f"Datei wird im Browser bereitgestellt: {p.name}", file=p, action="open")
    if sys.platform.startswith("win"):
        os.startfile(str(p))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(p)])
    else:
        subprocess.Popen(["xdg-open", str(p)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return f"Geöffnet: {p}"
