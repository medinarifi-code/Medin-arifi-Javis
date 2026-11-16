"""Datei-Grundlagen: sichere Pfade, Text-Dateien erstellen, Dateien lesen und auflisten.

Alle Pfade sind auf den JARVIS-Ordner (output_dir) begrenzt - die KI kann weder außerhalb
schreiben noch lesen (wichtig auf dem Server, wo Zugangsdaten liegen).
"""

from __future__ import annotations

from pathlib import Path

READ_CHUNK = 12_000  # Zeichen pro read_file-Aufruf


class ToolError(Exception):
    """Fehler, der als verständliche Meldung an die KI zurückgegeben wird."""


class ToolResult(str):
    """Werkzeug-Ergebnis (Text für die KI) mit optionaler Datei für die Oberfläche."""

    file: Path | None

    def __new__(cls, text: str, file: Path | None = None):
        obj = super().__new__(cls, text)
        obj.file = file
        return obj


def safe_path(output_dir: Path, filename: str, default_suffix: str | None = None) -> Path:
    """Zielpfad innerhalb des JARVIS-Ordners (verhindert '..' und absolute Pfade)."""
    if not filename or not str(filename).strip():
        raise ToolError("Es wurde kein Dateiname angegeben.")
    root = output_dir.resolve()
    target = (root / str(filename).strip().lstrip("/\\")).resolve()
    if not target.is_relative_to(root) or target == root:
        raise ToolError("Dateien dürfen nur im JARVIS-Ordner gespeichert werden.")
    if default_suffix and target.suffix.lower() != default_suffix:
        target = target.with_name(target.name + default_suffix)
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def existing_path(output_dir: Path, path: str) -> Path:
    """Vorhandene Datei im JARVIS-Ordner (z. B. 'uploads/foto.jpg')."""
    if not path or not str(path).strip():
        raise ToolError("Es wurde kein Dateipfad angegeben.")
    root = output_dir.resolve()
    p = (root / str(path).strip().lstrip("/\\")).resolve()
    if not p.is_relative_to(root):
        raise ToolError("Es können nur Dateien aus dem JARVIS-Ordner gelesen werden.")
    if not p.is_file():
        raise ToolError(f"Die Datei '{path}' gibt es nicht. Mit list_files siehst du alle Dateien.")
    return p


def unique(path: Path) -> Path:
    """Hängt (2), (3) … an, falls die Datei schon existiert."""
    if not path.exists():
        return path
    i = 2
    while True:
        candidate = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not candidate.exists():
            return candidate
        i += 1


def rel(output_dir: Path, path: Path) -> str:
    return path.resolve().relative_to(output_dir.resolve()).as_posix()


def create_file(output_dir: Path, filename: str, content: str) -> ToolResult:
    path = unique(safe_path(output_dir, filename))
    path.write_text(content, encoding="utf-8")
    return ToolResult(f"Datei gespeichert: {rel(output_dir, path)}", file=path)


def extract_text(p: Path) -> str:
    """Text aus Dokumenten (PDF, Word, Excel, PowerPoint, Text/Code)."""
    suffix = p.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(p))
        pages = [f"--- Seite {i} ---\n{(page.extract_text() or '').strip()}" for i, page in enumerate(reader.pages, 1)]
        text = "\n".join(pages)
        if not text.replace("--- Seite", "").strip(" -0123456789\n"):
            return "(Dieses PDF enthält keinen lesbaren Text – vermutlich eingescannte Bilder.)"
        return text
    if suffix == ".pptx":
        from pptx import Presentation

        parts = []
        for i, slide in enumerate(Presentation(str(p)).slides, 1):
            texts = [sh.text_frame.text for sh in slide.shapes if sh.has_text_frame and sh.text_frame.text.strip()]
            parts.append(f"--- Folie {i} ---\n" + "\n".join(texts))
        return "\n".join(parts)
    if suffix == ".docx":
        from docx import Document

        doc = Document(str(p))
        lines = [par.text for par in doc.paragraphs]
        for table in doc.tables:
            for row in table.rows:
                lines.append(" | ".join(c.text for c in row.cells))
        return "\n".join(lines)
    if suffix in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook

        wb = load_workbook(str(p), data_only=True, read_only=True)
        parts = []
        for ws in wb.worksheets:
            parts.append(f"--- Blatt {ws.title} ---")
            for row in ws.iter_rows(values_only=True):
                if any(v is not None for v in row):
                    parts.append("\t".join("" if v is None else str(v) for v in row))
        return "\n".join(parts)
    if suffix in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".mp3", ".zip"):
        return f"({p.name} ist keine Textdatei – Größe {p.stat().st_size:,} Bytes.)".replace(",", ".")
    try:
        return p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return p.read_text(encoding="latin-1")


def read_file(output_dir: Path, path: str, start: int = 0) -> str:
    p = existing_path(output_dir, path)
    text = extract_text(p)
    start = max(0, int(start or 0))
    part = text[start:start + READ_CHUNK]
    end = start + len(part)
    note = (f"\n\n[Zeichen {start}–{end} von {len(text)}. Weiterlesen mit read_file(path, start={end}).]"
            if end < len(text) else "")
    return part + note


def list_files(output_dir: Path) -> str:
    root = output_dir.resolve()
    files = sorted((f for f in root.rglob("*") if f.is_file()), key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        return "Der JARVIS-Ordner ist noch leer."
    return "Dateien (neueste zuerst):\n" + "\n".join(
        f"- {rel(output_dir, f)} ({f.stat().st_size:,} Bytes)".replace(",", ".") for f in files[:60])
