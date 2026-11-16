import base64

import pytest

from jarvis.uploads import UploadError, process, save_attachment


def b64(data: bytes, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(data).decode()


def test_pdf_and_docx_text(tmp_path):
    from jarvis.tools.media import create_pdf
    create_pdf(tmp_path, "quelle.pdf", "Vertrag", [{"paragraphs": ["Miete 900 Euro"]}])
    up = save_attachment(tmp_path, "Mein Vertrag.pdf", b64((tmp_path / "quelle.pdf").read_bytes(), "application/pdf"))
    assert up.kind == "document" and up.path == "uploads/Mein Vertrag.pdf" and "Miete 900 Euro" in up.text
    assert "Miete 900 Euro" in up.prompt_part()

    from docx import Document
    d = Document(); d.add_paragraph("Hallo Welt"); d.save(tmp_path / "x.docx")
    up = save_attachment(tmp_path, "brief.docx", b64((tmp_path / "x.docx").read_bytes(), "application/octet-stream"))
    assert "Hallo Welt" in up.text


def test_long_text_is_shortened_in_history(tmp_path):
    up = save_attachment(tmp_path, "lang.txt", b64(("abc " * 10000).encode(), "text/plain"))
    assert "read_file" in up.prompt_part() and len(up.history_part()) < 2000


def test_rejects_bad_files(tmp_path):
    with pytest.raises(UploadError, match="nicht unterstützt"):
        save_attachment(tmp_path, "x.exe", b64(b"MZ", "application/x-msdownload"))
    with pytest.raises(UploadError, match="kein lesbares Bild"):
        save_attachment(tmp_path, "kaputt.jpg", b64(b"nope", "image/jpeg"))
    with pytest.raises(UploadError, match="zu groß"):
        save_attachment(tmp_path, "gross.txt", b64(b"a" * (9 * 1024 * 1024), "text/plain"))
    with pytest.raises(UploadError, match="Maximal"):
        process(tmp_path, [{"name": "a.txt", "data": b64(b"a", "text/plain")}] * 5)
    up = save_attachment(tmp_path, "../../böse.txt", b64(b"hi", "text/plain"))
    assert up.path == "uploads/böse.txt"
