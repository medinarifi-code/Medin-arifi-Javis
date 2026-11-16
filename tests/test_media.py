import io
import zipfile
from pathlib import Path

import httpx
import pytest
from PIL import Image

from jarvis.tools import ToolContext, ToolError, execute, media


@pytest.fixture
def ctx(tmp_path):
    (tmp_path / "uploads").mkdir()
    Image.new("RGB", (800, 600), (200, 40, 40)).save(tmp_path / "uploads/foto.jpg")
    events = []
    c = ToolContext(output_dir=tmp_path, on_event=events.append)
    c.events = events
    return c


def test_pdf_with_unicode_table_and_image(ctx, tmp_path):
    execute("create_pdf", {"filename": "Bericht", "title": "Prüfbericht 5 €", "sections": [
        {"heading": "Übersicht", "paragraphs": ["Größe <ok> & gut"], "bullets": ["a"], "table": [["A", "B"], ["1", "2"]],
         "image": "uploads/foto.jpg"}]}, ctx)
    from pypdf import PdfReader
    text = PdfReader(str(tmp_path / "Bericht.pdf")).pages[0].extract_text()
    assert "Prüfbericht 5 €" in text and "Größe <ok> & gut" in text
    assert ctx.events[0]["url"] == "/files/Bericht.pdf"


@pytest.mark.parametrize("kind", ["bar", "line", "pie", "scatter"])
def test_charts(ctx, tmp_path, kind):
    execute("create_chart", {"filename": f"c-{kind}", "chart_type": kind, "title": "T", "labels": ["a", "b", "c"],
                             "series": [{"name": "x", "values": [1, "2,5", 3]}]}, ctx)
    assert Image.open(tmp_path / f"c-{kind}.png").size[0] > 1000


def test_chart_needs_numbers(ctx):
    with pytest.raises(ToolError, match="Zahlen"):
        execute("create_chart", {"labels": ["a"], "series": [{"values": ["viel"]}]}, ctx)


def test_qr_zip_edit(ctx, tmp_path):
    execute("create_qr", {"content": "https://example.com"}, ctx)
    assert Image.open(tmp_path / "qr-code.png").size[0] > 100
    execute("edit_image", {"path": "uploads/foto.jpg", "filename": "klein.png", "width": 200, "grayscale": True, "rotate": 90}, ctx)
    im = Image.open(tmp_path / "klein.png")
    assert im.size == (150, 200) and im.mode == "L"
    execute("edit_image", {"path": "uploads/foto.jpg", "filename": "foto.pdf", "crop": "0,0,50,50"}, ctx)
    assert (tmp_path / "foto.pdf").read_bytes()[:4] == b"%PDF"
    execute("create_zip", {"filename": "paket", "files": ["klein.png", "foto.pdf"]}, ctx)
    assert sorted(zipfile.ZipFile(tmp_path / "paket.zip").namelist()) == ["foto.pdf", "klein.png"]
    with pytest.raises(ToolError):
        execute("edit_image", {"path": "../geheim.png", "filename": "x.png"}, ctx)


def test_office_with_images(ctx, tmp_path):
    execute("create_pptx", {"filename": "p", "title": "T", "slides": [
        {"title": "A", "bullets": ["x"], "image": "uploads/foto.jpg"},
        {"title": "B", "layout": "image", "bullets": ["Unterschrift"], "image": "uploads/foto.jpg"}]}, ctx)
    from pptx import Presentation
    pics = [sh for sl in Presentation(str(tmp_path / "p.pptx")).slides for sh in sl.shapes if sh.shape_type == 13]
    assert len(pics) == 2
    execute("create_docx", {"filename": "d", "title": "T", "sections": [{"heading": "H", "image": "uploads/foto.jpg"}]}, ctx)
    from docx import Document
    assert len(Document(str(tmp_path / "d.docx")).inline_shapes) == 1
    with pytest.raises(ToolError, match="gibt es nicht"):
        execute("create_docx", {"filename": "e", "title": "T", "sections": [{"image": "uploads/fehlt.jpg"}]}, ctx)


def test_create_image(ctx, tmp_path, monkeypatch):
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), "purple").save(buf, "JPEG")
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        return httpx.Response(200, content=buf.getvalue(), headers={"content-type": "image/jpeg"})

    monkeypatch.setattr(media, "http_client_factory", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    execute("create_image", {"prompt": "iron man helmet in space", "filename": "helm"}, ctx)
    assert (tmp_path / "helm.jpg").exists() and "iron%20man%20helmet" in seen["url"]
    assert ctx.events[-1]["name"] == "helm.jpg"
    monkeypatch.setattr(media, "http_client_factory",
                        lambda: httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(429))))
    with pytest.raises(ToolError, match="15 Sekunden"):
        execute("create_image", {"prompt": "x"}, ctx)


def test_create_audio(ctx, tmp_path, monkeypatch):
    class FakeTTS:
        def __init__(self, text, lang):
            self.text = text

        def save(self, path):
            Path(path).write_bytes(b"ID3fake")

    monkeypatch.setattr(media, "_tts_factory", FakeTTS)
    execute("create_audio", {"filename": "gruss", "text": "Hallo Sir"}, ctx)
    assert (tmp_path / "gruss.mp3").read_bytes() == b"ID3fake"


def test_read_file_paged_and_confined(ctx, tmp_path):
    (tmp_path / "lang.txt").write_text("x" * 30000)
    part = execute("read_file", {"path": "lang.txt"}, ctx)
    assert "start=12000" in part
    assert execute("read_file", {"path": "lang.txt", "start": 24000}, ctx).count("x") == 6000
    with pytest.raises(ToolError):
        execute("read_file", {"path": "../../etc/passwd"}, ctx)
