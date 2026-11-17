"""Weitere Dateitypen: PDF, Diagramme, KI-Bilder, QR-Codes, Audio, Bildbearbeitung, ZIP."""

from __future__ import annotations

import html
import random
import urllib.parse
import zipfile
from pathlib import Path
from typing import Callable

import httpx

from .files import ToolError, ToolResult, existing_path, rel, safe_path, unique
from .office import fit, image_file

IMAGE_FORMATS = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP", ".gif": "GIF", ".bmp": "BMP",
                 ".pdf": "PDF"}

# Für Tests austauschbar
http_client_factory: Callable[[], httpx.Client] = lambda: httpx.Client(timeout=120, follow_redirects=True)  # noqa: E731
_tts_factory = None


# --------------------------------------------------------------------------- PDF
_pdf_font: str | None = None


def _font() -> str:
    """Unicode-Schrift für PDFs (DejaVu aus matplotlib), sonst Helvetica."""
    global _pdf_font
    if _pdf_font:
        return _pdf_font
    try:
        import matplotlib
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont

        base = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
        pdfmetrics.registerFont(TTFont("DejaVu", str(base / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont("DejaVu-Bold", str(base / "DejaVuSans-Bold.ttf")))
        from reportlab.lib.fonts import addMapping

        addMapping("DejaVu", 0, 0, "DejaVu")
        addMapping("DejaVu", 1, 0, "DejaVu-Bold")
        _pdf_font = "DejaVu"
    except Exception:  # noqa: BLE001
        _pdf_font = "Helvetica"
    return _pdf_font


def create_pdf(output_dir: Path, filename: str, title: str, sections: list[dict]) -> ToolResult:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Image, ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    font = _font()
    bold = "DejaVu-Bold" if font == "DejaVu" else "Helvetica-Bold"
    ss = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=ss["BodyText"], fontName=font, fontSize=11, leading=15, spaceAfter=6)
    h1 = ParagraphStyle("h1", parent=ss["Title"], fontName=bold, fontSize=22, leading=26, textColor=colors.HexColor("#0B1622"))
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontName=bold, fontSize=15, leading=19,
                        textColor=colors.HexColor("#0A66C2"), spaceBefore=10)
    esc = lambda t: html.escape(str(t)).replace("\n", "<br/>")  # noqa: E731

    story = [Paragraph(esc(title), h1), Spacer(1, 0.3 * cm)]
    width = A4[0] - 4 * cm
    for sec in sections or []:
        if sec.get("heading"):
            story.append(Paragraph(esc(sec["heading"]), h2))
        for para in sec.get("paragraphs") or []:
            story.append(Paragraph(esc(para), body))
        if sec.get("bullets"):
            story.append(ListFlowable([ListItem(Paragraph(esc(b), body), leftIndent=12) for b in sec["bullets"]],
                                      bulletType="bullet", start="•", leftIndent=14))
        rows = sec.get("table") or []
        if rows:
            data = [[Paragraph(esc(c), body) for c in row] for row in rows]
            tbl = Table(data, repeatRows=1, colWidths=[width / max(len(r) for r in rows)] * max(len(r) for r in rows))
            tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E6F0FA")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#B0BEC5")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            story += [tbl, Spacer(1, 0.3 * cm)]
        img = image_file(output_dir, sec.get("image"))
        if img:
            _, _, w, h = fit(0, 0, width, 12 * cm, img[1], img[2])
            story += [Image(str(img[0]), width=w, height=h), Spacer(1, 0.3 * cm)]

    path = unique(safe_path(output_dir, filename, ".pdf"))
    SimpleDocTemplate(str(path), pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm,
                      topMargin=2 * cm, bottomMargin=2 * cm, title=title).build(story)
    return ToolResult(f"PDF gespeichert: {rel(output_dir, path)}", file=path)


# --------------------------------------------------------------------------- Diagramme
def create_chart(output_dir: Path, filename: str, chart_type: str, title: str, labels: list[str],
                 series: list[dict], x_label: str = "", y_label: str = "") -> ToolResult:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if not series or not labels:
        raise ToolError("Für ein Diagramm braucht es 'labels' und mindestens eine 'series' mit Werten.")
    try:
        data = [(str(s.get("name") or f"Reihe {i + 1}"), [float(str(v).replace(",", ".")) for v in s.get("values") or []])
                for i, s in enumerate(series)]
    except ValueError as e:
        raise ToolError(f"Alle Werte müssen Zahlen sein ({e}).") from e
    palette = ["#0A66C2", "#F59E0B", "#10B981", "#EF4444", "#8B5CF6", "#06B6D4", "#EC4899", "#84CC16"]
    kind = (chart_type or "bar").lower()
    fig, ax = plt.subplots(figsize=(10, 6), dpi=150)
    if kind == "pie":
        values = data[0][1][:len(labels)]
        ax.pie(values, labels=labels[:len(values)], autopct="%1.0f%%", startangle=90, colors=palette,
               wedgeprops={"linewidth": 2, "edgecolor": "white"})
        ax.axis("equal")
    else:
        xs = range(len(labels))
        for i, (name, values) in enumerate(data):
            values = values[:len(labels)]
            color = palette[i % len(palette)]
            if kind == "line":
                ax.plot(list(xs)[:len(values)], values, marker="o", linewidth=2.5, color=color, label=name)
            elif kind == "scatter":
                ax.scatter(list(xs)[:len(values)], values, s=60, color=color, label=name)
            else:
                w = 0.8 / len(data)
                ax.bar([x - 0.4 + w * (i + 0.5) for x in list(xs)[:len(values)]], values, width=w, color=color, label=name)
        ax.set_xticks(list(xs))
        ax.set_xticklabels(labels, rotation=30 if max(len(str(l)) for l in labels) > 6 else 0, ha="right")
        ax.set_xlabel(x_label)
        ax.set_ylabel(y_label)
        ax.grid(axis="y", alpha=0.3)
        ax.spines[["top", "right"]].set_visible(False)
        if len(data) > 1:
            ax.legend(frameon=False)
    ax.set_title(title, fontsize=16, fontweight="bold", pad=14)
    fig.tight_layout()
    path = unique(safe_path(output_dir, filename, ".png"))
    fig.savefig(path)
    plt.close(fig)
    return ToolResult(f"Diagramm gespeichert: {rel(output_dir, path)}", file=path)


# --------------------------------------------------------------------------- KI-Bilder (Pollinations, kostenlos)
def create_image(output_dir: Path, prompt: str, filename: str = "", width: int = 1024, height: int = 1024) -> ToolResult:
    prompt = (prompt or "").strip()
    if not prompt:
        raise ToolError("Bitte beschreibe, was auf dem Bild sein soll.")
    width = max(256, min(int(width or 1024), 1536))
    height = max(256, min(int(height or 1024), 1536))
    params = {"width": width, "height": height, "nologo": "true", "model": "flux", "seed": random.randint(1, 10**9)}
    url = "https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt[:900], safe="")
    try:
        with http_client_factory() as client:
            r = client.get(url, params=params)
    except httpx.HTTPError as e:
        raise ToolError(f"Der Bild-Dienst ist gerade nicht erreichbar ({type(e).__name__}). Bitte später erneut versuchen.") from e
    if r.status_code == 429:
        raise ToolError("Der kostenlose Bild-Dienst erlaubt nur etwa ein Bild alle 15 Sekunden. Bitte kurz warten.")
    ctype = r.headers.get("content-type", "")
    if r.status_code != 200 or not ctype.startswith("image/"):
        raise ToolError(f"Der Bild-Dienst hat kein Bild geliefert (Status {r.status_code}). Bitte später erneut versuchen.")
    ext = ".png" if "png" in ctype else ".webp" if "webp" in ctype else ".jpg"
    name = filename or "bild"
    path = unique(safe_path(output_dir, Path(name).stem + ext))
    path.write_bytes(r.content)
    return ToolResult(f"Bild erstellt: {rel(output_dir, path)}", file=path)


# --------------------------------------------------------------------------- QR-Code
def create_qr(output_dir: Path, filename: str, content: str) -> ToolResult:
    import qrcode

    if not content:
        raise ToolError("Was soll im QR-Code stehen (Link, Text, WLAN …)?")
    img = qrcode.make(content, box_size=12, border=3)
    path = unique(safe_path(output_dir, filename or "qr-code", ".png"))
    img.save(path)
    return ToolResult(f"QR-Code gespeichert: {rel(output_dir, path)}", file=path)


# --------------------------------------------------------------------------- Audio
def create_audio(output_dir: Path, filename: str, text: str, lang: str = "de") -> ToolResult:
    if not text:
        raise ToolError("Welcher Text soll vorgelesen werden?")
    try:
        if _tts_factory:
            tts = _tts_factory(text[:5000], lang or "de")
        else:
            from gtts import gTTS

            tts = gTTS(text[:5000], lang=(lang or "de")[:2])
        path = unique(safe_path(output_dir, filename or "audio", ".mp3"))
        tts.save(str(path))
    except ToolError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"Audio konnte nicht erstellt werden ({type(e).__name__}: {e}).") from e
    return ToolResult(f"Audio gespeichert: {rel(output_dir, path)}", file=path)


# --------------------------------------------------------------------------- Bildbearbeitung
def edit_image(output_dir: Path, path: str, filename: str, width: int | None = None, height: int | None = None,
               rotate: float | None = None, grayscale: bool = False, mirror: bool = False, flip: bool = False,
               crop: str | None = None, brightness: float | None = None, contrast: float | None = None) -> ToolResult:
    from PIL import Image, ImageEnhance, ImageOps

    src = existing_path(output_dir, path)
    out = safe_path(output_dir, filename or f"{src.stem}-bearbeitet{src.suffix}")
    fmt = IMAGE_FORMATS.get(out.suffix.lower())
    if not fmt:
        raise ToolError(f"Unbekanntes Bildformat '{out.suffix}'. Möglich: {', '.join(IMAGE_FORMATS)}")
    try:
        im = ImageOps.exif_transpose(Image.open(src))
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"'{path}' ist kein lesbares Bild.") from e
    if crop:
        try:
            l, t, r, b = (float(v) for v in str(crop).replace(";", ",").split(","))
        except ValueError as e:
            raise ToolError("crop bitte als 'links,oben,rechts,unten' in Prozent angeben, z. B. '10,10,90,90'.") from e
        w, h = im.size
        im = im.crop((int(w * l / 100), int(h * t / 100), int(w * r / 100), int(h * b / 100)))
    if width or height:
        w, h = im.size
        nw = int(width) if width else int(w * int(height) / h)
        nh = int(height) if height else int(h * int(width) / w)
        im = im.resize((max(1, min(nw, 8000)), max(1, min(nh, 8000))), Image.LANCZOS)
    if rotate:
        im = im.rotate(-float(rotate), expand=True)
    if mirror:
        im = ImageOps.mirror(im)
    if flip:
        im = ImageOps.flip(im)
    if grayscale:
        im = ImageOps.grayscale(im)
    if brightness:
        im = ImageEnhance.Brightness(im).enhance(float(brightness))
    if contrast:
        im = ImageEnhance.Contrast(im).enhance(float(contrast))
    if fmt in ("JPEG", "PDF", "BMP") and im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    out = unique(out)
    im.save(out, fmt)
    return ToolResult(f"Bild gespeichert: {rel(output_dir, out)}", file=out)


# --------------------------------------------------------------------------- ZIP
def create_zip(output_dir: Path, filename: str, files: list[str]) -> ToolResult:
    if not files:
        raise ToolError("Welche Dateien sollen ins ZIP?")
    paths = [existing_path(output_dir, f) for f in files]
    out = unique(safe_path(output_dir, filename or "dateien", ".zip"))
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in paths:
            z.write(p, arcname=p.name)
    return ToolResult(f"ZIP mit {len(paths)} Dateien gespeichert: {rel(output_dir, out)}", file=out)
