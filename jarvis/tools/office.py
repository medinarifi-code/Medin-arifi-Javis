"""Office-Werkzeuge: PowerPoint (.pptx), Word (.docx) und Excel (.xlsx) erstellen."""

from __future__ import annotations

from pathlib import Path

from .files import ToolError, ToolResult, existing_path, rel, safe_path, unique

# ---------------------------------------------------------------------------
# PowerPoint im "Stark Industries"-HUD-Stil
# ---------------------------------------------------------------------------

IMAGE_TYPES = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")


def image_file(output_dir: Path, path: str | None) -> tuple[Path, int, int] | None:
    """Bild aus dem JARVIS-Ordner (z. B. 'uploads/foto.jpg') als (Pfad, Breite, Höhe); WebP wird zu PNG."""
    if not path:
        return None
    p = existing_path(output_dir, path)
    from PIL import Image

    try:
        with Image.open(p) as im:
            w, h = im.size
            if p.suffix.lower() not in (".png", ".jpg", ".jpeg", ".gif", ".bmp"):  # z. B. WebP für Office umwandeln
                conv = p.with_suffix(".office.png")
                im.convert("RGBA").save(conv)
                p = conv
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"'{path}' ist kein lesbares Bild ({e}).") from e
    return p, w, h


def fit(x, y, w, h, img_w, img_h):
    """Bild unverzerrt in ein Rechteck einpassen (zentriert)."""
    scale = min(w / img_w, h / img_h)
    nw, nh = int(img_w * scale), int(img_h * scale)
    return x + (w - nw) // 2, y + (h - nh) // 2, nw, nh


THEMES = {
    "jarvis": {  # dunkel, Arc-Reactor-Blau
        "bg": "0B1622", "panel": "112233", "accent": "00D4FF", "accent2": "FFB300",
        "title": "FFFFFF", "text": "D6E6F2", "muted": "7FA3BF",
    },
    "ironman": {  # dunkel, Rot/Gold
        "bg": "1A0A0A", "panel": "2A1010", "accent": "E23B2E", "accent2": "F2C14E",
        "title": "FFFFFF", "text": "F1E3D3", "muted": "B08F7A",
    },
    "hell": {  # hell und seriös für Schule/Arbeit
        "bg": "FFFFFF", "panel": "EEF3F8", "accent": "0A66C2", "accent2": "F59E0B",
        "title": "0F172A", "text": "1E293B", "muted": "64748B",
    },
}


def create_pptx(output_dir: Path, filename: str, title: str, slides: list[dict],
                subtitle: str | None = None, theme: str = "jarvis") -> str:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
    from pptx.util import Inches, Pt

    if not slides:
        raise ToolError("Die Präsentation braucht mindestens eine Inhaltsfolie.")
    t = THEMES.get((theme or "jarvis").lower(), THEMES["jarvis"])
    rgb = lambda hex_: RGBColor.from_string(hex_)  # noqa: E731

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    W, H = prs.slide_width, prs.slide_height
    blank = prs.slide_layouts[6]

    def background(slide):
        fill = slide.background.fill
        fill.solid()
        fill.fore_color.rgb = rgb(t["bg"])

    def rect(slide, x, y, w, h, color, shape=MSO_SHAPE.RECTANGLE):
        s = slide.shapes.add_shape(shape, x, y, w, h)
        s.fill.solid()
        s.fill.fore_color.rgb = rgb(color)
        s.line.fill.background()
        s.shadow.inherit = False
        return s

    def textbox(slide, x, y, w, h, text, size, color, bold=False, align=PP_ALIGN.LEFT,
                anchor=MSO_ANCHOR.TOP):
        tb = slide.shapes.add_textbox(x, y, w, h)
        tf = tb.text_frame
        tf.word_wrap = True
        tf.vertical_anchor = anchor
        p = tf.paragraphs[0]
        p.alignment = align
        run = p.add_run()
        run.text = text
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = rgb(color)
        run.font.name = "Segoe UI"
        return tb

    def bullets(slide, x, y, w, h, items, size=22):
        tb = slide.shapes.add_textbox(x, y, w, h)
        tf = tb.text_frame
        tf.word_wrap = True
        for i, item in enumerate(items):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.space_after = Pt(12)
            marker = p.add_run()
            marker.text = "▸  "
            marker.font.size = Pt(size)
            marker.font.color.rgb = rgb(t["accent"])
            marker.font.bold = True
            run = p.add_run()
            run.text = str(item)
            run.font.size = Pt(size)
            run.font.color.rgb = rgb(t["text"])
            run.font.name = "Segoe UI"
        return tb

    def footer(slide, number):
        rect(slide, 0, H - Inches(0.08), W, Inches(0.08), t["accent"])
        textbox(slide, W - Inches(1.6), H - Inches(0.55), Inches(1.3), Inches(0.4),
                f"{number:02d}", 12, t["muted"], align=PP_ALIGN.RIGHT)
        textbox(slide, Inches(0.6), H - Inches(0.55), Inches(8), Inches(0.4),
                title, 11, t["muted"])

    def notes(slide, text):
        if text:
            slide.notes_slide.notes_text_frame.text = text

    # --- Titelfolie ---
    s = prs.slides.add_slide(blank)
    background(s)
    ring = Inches(4.2)
    for i, color in enumerate([t["accent"], t["panel"], t["accent"]]):
        d = ring - Inches(0.6) * i
        c = s.shapes.add_shape(MSO_SHAPE.OVAL, W - Inches(3.2) - d / 2, H / 2 - d / 2, d, d)
        c.fill.background()
        c.line.color.rgb = rgb(color)
        c.line.width = Pt(3 if i != 1 else 10)
    rect(s, Inches(0.8), Inches(2.3), Inches(0.12), Inches(2.4), t["accent"])
    textbox(s, Inches(1.2), Inches(2.1), Inches(7.6), Inches(1.9), title, 44, t["title"], bold=True,
            anchor=MSO_ANCHOR.BOTTOM)
    if subtitle:
        textbox(s, Inches(1.2), Inches(4.05), Inches(7.6), Inches(1.0), subtitle, 22, t["accent"])
    rect(s, 0, H - Inches(0.08), W, Inches(0.08), t["accent"])

    # --- Inhaltsfolien ---
    for n, data in enumerate(slides, start=2):
        s = prs.slides.add_slide(blank)
        background(s)
        layout = (data.get("layout") or "bullets").lower()
        heading = data.get("title") or ""
        points = [str(b) for b in (data.get("bullets") or [])]
        img = image_file(output_dir, data.get("image"))

        if layout == "section":
            rect(s, 0, 0, Inches(0.25), H, t["accent"])
            textbox(s, Inches(1.0), Inches(2.4), Inches(11), Inches(1.6), heading, 48, t["title"], bold=True,
                    anchor=MSO_ANCHOR.MIDDLE)
            if points:
                textbox(s, Inches(1.0), Inches(4.0), Inches(11), Inches(1.2), points[0], 24, t["accent"])
            footer(s, n)
            notes(s, data.get("notes"))
            continue

        # Kopfzeile
        rect(s, Inches(0.6), Inches(0.55), Inches(0.1), Inches(0.9), t["accent"])
        textbox(s, Inches(0.9), Inches(0.45), Inches(11.5), Inches(1.1), heading, 34, t["title"], bold=True,
                anchor=MSO_ANCHOR.MIDDLE)
        rect(s, Inches(0.9), Inches(1.55), Inches(2.0), Inches(0.05), t["accent2"])

        if layout == "two_column":
            right = [str(b) for b in (data.get("right_bullets") or [])]
            col_w = Inches(5.8)
            for i, (head, items) in enumerate([(data.get("left_title"), points),
                                               (data.get("right_title"), right)]):
                x = Inches(0.9) + i * (col_w + Inches(0.3))
                card = rect(s, x, Inches(1.9), col_w, Inches(4.8), t["panel"], MSO_SHAPE.ROUNDED_RECTANGLE)
                card.adjustments[0] = 0.05
                y = Inches(2.1)
                if head:
                    textbox(s, x + Inches(0.3), y, col_w - Inches(0.6), Inches(0.6), head, 22, t["accent"], bold=True)
                    y += Inches(0.7)
                bullets(s, x + Inches(0.3), y, col_w - Inches(0.6), Inches(4.0), items, size=18)
        elif layout == "quote":
            quote = points[0] if points else heading
            textbox(s, Inches(1.5), Inches(2.2), Inches(10.3), Inches(2.8), f"„{quote}“", 36, t["title"],
                    align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
            if len(points) > 1:
                textbox(s, Inches(1.5), Inches(5.1), Inches(10.3), Inches(0.6), f"— {points[1]}", 20, t["accent"],
                        align=PP_ALIGN.CENTER)
        elif img and (layout == "image" or not points):
            x, y, w, h = fit(Inches(0.9), Inches(1.9), Inches(11.5), Inches(4.9), img[1], img[2])
            s.shapes.add_picture(str(img[0]), x, y, w, h)
            if points:
                textbox(s, Inches(0.9), Inches(6.7), Inches(11.5), Inches(0.5), points[0], 14, t["muted"],
                        align=PP_ALIGN.CENTER)
        else:
            size = 24 if len(points) <= 4 else 20 if len(points) <= 6 else 16
            if img:  # Stichpunkte links, Bild rechts
                bullets(s, Inches(0.9), Inches(1.95), Inches(6.4), Inches(4.8), points, size=min(size, 20))
                x, y, w, h = fit(Inches(7.6), Inches(1.95), Inches(5.0), Inches(4.8), img[1], img[2])
                s.shapes.add_picture(str(img[0]), x, y, w, h)
            else:
                bullets(s, Inches(0.9), Inches(1.95), Inches(11.5), Inches(4.8), points, size=size)

        footer(s, n)
        notes(s, data.get("notes"))

    path = unique(safe_path(output_dir, filename, ".pptx"))
    prs.save(str(path))
    return ToolResult(f"Präsentation mit {len(slides) + 1} Folien gespeichert: {rel(output_dir, path)}", file=path)


# ---------------------------------------------------------------------------
# Word
# ---------------------------------------------------------------------------

def create_docx(output_dir: Path, filename: str, title: str, sections: list[dict]) -> str:
    from docx import Document
    from docx.shared import Inches, Pt

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)
    doc.add_heading(title, level=0)
    for sec in sections:
        if sec.get("heading"):
            doc.add_heading(sec["heading"], level=1)
        for para in sec.get("paragraphs") or []:
            doc.add_paragraph(str(para))
        for item in sec.get("bullets") or []:
            doc.add_paragraph(str(item), style="List Bullet")
        img = image_file(output_dir, sec.get("image"))
        if img:
            width = 6.0 if img[1] >= img[2] else 6.0 * img[1] / img[2] * 0.8
            doc.add_picture(str(img[0]), width=Inches(width))
    path = unique(safe_path(output_dir, filename, ".docx"))
    doc.save(str(path))
    return ToolResult(f"Word-Dokument gespeichert: {rel(output_dir, path)}", file=path)


# ---------------------------------------------------------------------------
# Excel
# ---------------------------------------------------------------------------

def create_xlsx(output_dir: Path, filename: str, sheets: list[dict]) -> str:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    if not sheets:
        raise ToolError("Die Tabelle braucht mindestens ein Blatt.")
    wb = Workbook()
    wb.remove(wb.active)
    for sh in sheets:
        ws = wb.create_sheet(title=(sh.get("name") or "Tabelle")[:31])
        headers = sh.get("headers") or []
        if headers:
            ws.append(headers)
            for cell in ws[1]:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="0B1622")
        for row in sh.get("rows") or []:
            ws.append([_cell(v) for v in row])
        for i in range(1, ws.max_column + 1):
            width = max((len(str(c.value)) for c in ws[get_column_letter(i)] if c.value is not None), default=8)
            ws.column_dimensions[get_column_letter(i)].width = min(max(10, width + 2), 60)
        ws.freeze_panes = "A2" if headers else None
    path = unique(safe_path(output_dir, filename, ".xlsx"))
    wb.save(str(path))
    return ToolResult(f"Excel-Tabelle gespeichert: {rel(output_dir, path)}", file=path)


def _cell(v):
    """Zahlen als Zahlen speichern; Formeln (beginnend mit '=') bleiben erhalten."""
    if not isinstance(v, str):
        return v
    s = v.strip()
    if s.startswith("="):
        return s
    for conv in (int, float):
        try:
            return conv(s)
        except ValueError:
            pass
    return v
