from pathlib import Path

import pytest

from jarvis.tools import ToolContext, ToolError, execute, tool_definitions


def ctx(tmp_path, **kw):
    events = []
    c = ToolContext(output_dir=tmp_path, on_event=events.append, **kw)
    return c, events


def test_pptx_docx_xlsx(tmp_path: Path):
    c, events = ctx(tmp_path)
    execute("create_pptx", {"filename": "Autos", "title": "Elektroautos", "slides": [
        {"title": "Warum?", "bullets": ["Leise", "Sauber"], "notes": "Hallo"},
        {"title": "Vergleich", "layout": "two_column", "bullets": ["A"], "right_bullets": ["B"]}]}, c)
    assert events[0] == {"type": "file", "name": "Autos.pptx", "url": "/files/Autos.pptx"}
    from pptx import Presentation
    assert len(Presentation(str(tmp_path / "Autos.pptx")).slides) == 3

    execute("create_docx", {"filename": "brief.docx", "title": "Brief", "sections": [{"heading": "A", "paragraphs": ["Text"]}]}, c)
    execute("create_xlsx", {"filename": "t.xlsx", "sheets": [{"name": "S", "headers": ["a", "b"], "rows": [["x", "5"], ["y", "=SUM(B2:B2)"]]}]}, c)
    from openpyxl import load_workbook
    ws = load_workbook(tmp_path / "t.xlsx").active
    assert ws["B2"].value == 5 and ws["B3"].value == "=SUM(B2:B2)"
    assert [e["name"] for e in events] == ["Autos.pptx", "brief.docx", "t.xlsx"]


def test_path_escape_and_missing_fields(tmp_path):
    c, _ = ctx(tmp_path)
    with pytest.raises(ToolError):
        execute("create_file", {"filename": "../x.txt", "content": "x"}, c)
    with pytest.raises(ToolError):
        execute("create_pptx", {"filename": "a", "title": "b"}, c)


def test_schemas_are_gemini_safe():
    import json
    text = json.dumps(tool_definitions(True))
    assert "additionalProperties" not in text and '"null"' not in text
    names = [t["name"] for t in tool_definitions(False)]
    assert "send_email" not in names and "search_web" in names
