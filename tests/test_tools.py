from pathlib import Path

import pytest

from jarvis.tools import TOOL_DEFINITIONS, ToolError, execute_tool, validate
from jarvis.voice import SentenceBuffer, clean_for_speech


def test_create_pptx_all_layouts(tmp_path: Path):
    args = {
        "filename": "Elektroautos",
        "title": "Elektroautos",
        "subtitle": "Referat Klasse 10",
        "theme": "jarvis",
        "slides": [
            {"title": "Einleitung", "bullets": ["Was ist ein E-Auto?", "Geschichte", "Heute"], "notes": "Begrüßung"},
            {"title": "Vor- und Nachteile", "layout": "two_column", "bullets": ["Leise", "Lokal emissionsfrei"],
             "left_title": "Vorteile", "right_title": "Nachteile", "right_bullets": ["Reichweite", "Preis"]},
            {"title": "Kapitel 2", "layout": "section", "bullets": ["Technik"]},
            {"title": "Zitat", "layout": "quote", "bullets": ["Die Zukunft ist elektrisch.", "Unbekannt"]},
        ],
    }
    assert validate("create_pptx", args) is None
    result = execute_tool("create_pptx", args, tmp_path)
    path = tmp_path / "Elektroautos.pptx"
    assert str(path) in result

    from pptx import Presentation

    prs = Presentation(str(path))
    assert len(prs.slides) == 5
    texts = [sh.text_frame.text for sh in prs.slides[0].shapes if sh.has_text_frame]
    assert "Elektroautos" in texts
    assert prs.slides[1].notes_slide.notes_text_frame.text == "Begrüßung"

    # zweite Datei mit gleichem Namen überschreibt nicht
    execute_tool("create_pptx", args, tmp_path)
    assert (tmp_path / "Elektroautos (2).pptx").exists()

    read_back = execute_tool("read_file", {"path": "Elektroautos.pptx"}, tmp_path)
    assert "Vor- und Nachteile" in read_back


def test_create_docx_and_xlsx(tmp_path: Path):
    execute_tool("create_docx", {"filename": "brief.docx", "title": "Bewerbung",
                                 "sections": [{"heading": "Anschreiben", "paragraphs": ["Sehr geehrte Damen und Herren,"],
                                               "bullets": ["Punkt A"]}]}, tmp_path)
    assert "Sehr geehrte" in execute_tool("read_file", {"path": "brief.docx"}, tmp_path)

    execute_tool("create_xlsx", {"filename": "budget", "sheets": [
        {"name": "Budget", "headers": ["Posten", "Betrag"], "rows": [["Miete", 500], ["Essen", "250"], ["Summe", "=SUM(B2:B3)"]]}
    ]}, tmp_path)
    from openpyxl import load_workbook

    ws = load_workbook(tmp_path / "budget.xlsx").active
    assert ws["B3"].value == 250
    assert ws["B4"].value == "=SUM(B2:B3)"


def test_create_file_and_list(tmp_path: Path):
    execute_tool("create_file", {"filename": "code/hallo.py", "content": "print('hi')"}, tmp_path)
    assert (tmp_path / "code" / "hallo.py").read_text() == "print('hi')"
    assert "code" in execute_tool("list_files", {}, tmp_path)


def test_path_escape_blocked(tmp_path: Path):
    with pytest.raises(ToolError):
        execute_tool("create_file", {"filename": "../boese.txt", "content": "x"}, tmp_path)


def test_validation_errors():
    assert validate("create_pptx", {"filename": "a", "title": "b"}) is not None
    assert validate("create_file", {"filename": "a", "content": 3}) is not None
    assert validate("nope", {}) is not None


def test_tool_definitions_shape():
    names = [t["name"] for t in TOOL_DEFINITIONS]
    assert "create_pptx" in names and "web_search" in names
    assert len(names) == len(set(names))


def test_sentence_buffer():
    buf = SentenceBuffer()
    out = []
    for chunk in ["Guten Tag, Sir. Alle Sys", "teme sind online. Wie kann", " ich helfen?"]:
        out += buf.feed(chunk)
    out += buf.flush()
    assert " ".join(out) == "Guten Tag, Sir. Alle Systeme sind online. Wie kann ich helfen?"
    assert len(out) >= 2


def test_clean_for_speech():
    assert clean_for_speech("**Hallo** [Link](https://x.de) # Titel") == "Hallo Link Titel"
