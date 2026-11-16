"""Hochgeladene Dateien (Bilder & Dokumente) speichern und für die KI aufbereiten."""

from __future__ import annotations

import base64
import binascii
import io
import re
from dataclasses import dataclass
from pathlib import Path

from .tools.files import ToolError, extract_text, rel, safe_path, unique

MAX_BYTES = 8 * 1024 * 1024
MAX_FILES = 4
INLINE_CHARS = 8_000   # so viel Dokument-Text geht direkt an die KI
HISTORY_CHARS = 1_500   # so viel bleibt danach im Gesprächsverlauf
VISION_MAX_SIDE = 1280

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic"}
DOC_EXT = {".pdf", ".docx", ".xlsx", ".xlsm", ".pptx", ".txt", ".csv", ".md", ".json", ".html", ".htm", ".xml",
           ".py", ".js", ".css", ".log", ".ics", ".vcf", ".yaml", ".yml", ".tex", ".rtf"}
MIME_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif",
            "application/pdf": ".pdf", "text/plain": ".txt", "text/csv": ".csv"}


class UploadError(Exception):
    pass


@dataclass
class Upload:
    name: str        # angezeigter Name
    path: str        # relativ zum JARVIS-Ordner, z. B. uploads/foto.jpg
    kind: str        # "image" | "document"
    data_url: str = ""  # nur Bilder: für die KI
    text: str = ""      # nur Dokumente: Textinhalt

    def prompt_part(self) -> str:
        if self.kind == "image":
            return (f"[Angehängtes Bild: {self.path} – du siehst es. Du kannst es mit edit_image bearbeiten oder per "
                    f"image-Feld in PowerPoint/Word/PDF einbauen.]")
        body = self.text[:INLINE_CHARS]
        more = (f"\n[… gekürzt – weiterlesen mit read_file(path='{self.path}', start={INLINE_CHARS})]"
                if len(self.text) > INLINE_CHARS else "")
        return f"[Angehängte Datei: {self.path}]\n--- Inhalt ---\n{body}{more}\n--- Ende ---"

    def history_part(self) -> str:
        if self.kind == "image":
            return f"[Bild angehängt: {self.path}]"
        short = self.text[:HISTORY_CHARS]
        return (f"[Datei angehängt: {self.path} – Anfang des Inhalts:]\n{short}"
                + (f"\n[… vollständig lesbar mit read_file(path='{self.path}')]" if len(self.text) > HISTORY_CHARS else ""))


def _clean_name(name: str, ext: str) -> str:
    stem = Path(name or "datei").stem
    stem = re.sub(r"[^\w\-. ]+", "_", stem, flags=re.UNICODE).strip(" ._") or "datei"
    return stem[:60] + ext


def save_attachment(output_dir: Path, name: str, data: str) -> Upload:
    match = re.match(r"^data:([\w.+/-]*)(?:;[\w=.-]+)*;base64,(.*)$", data or "", re.S)
    if not match:
        raise UploadError(f"'{name}' konnte nicht gelesen werden.")
    mime, b64 = match.group(1).lower(), match.group(2)
    try:
        raw = base64.b64decode(b64, validate=False)
    except (binascii.Error, ValueError) as e:
        raise UploadError(f"'{name}' ist beschädigt.") from e
    if len(raw) > MAX_BYTES:
        raise UploadError(f"'{name}' ist zu groß (max. 8 MB).")
    ext = Path(name or "").suffix.lower() or MIME_EXT.get(mime, "")
    if ext == ".jpeg":
        ext = ".jpg"
    if ext not in IMAGE_EXT and ext not in DOC_EXT and not mime.startswith("image/"):
        raise UploadError(f"Dateityp von '{name}' wird nicht unterstützt. Möglich: Bilder, PDF, Word, Excel, "
                          "PowerPoint und Textdateien.")
    is_image = mime.startswith("image/") or ext in IMAGE_EXT
    if is_image:
        return _save_image(output_dir, name, raw)
    try:
        path = unique(safe_path(output_dir, "uploads/" + _clean_name(name, ext)))
    except ToolError as e:
        raise UploadError(str(e)) from e
    path.write_bytes(raw)
    try:
        text = extract_text(path)
    except Exception as e:  # noqa: BLE001
        text = f"(Inhalt konnte nicht gelesen werden: {type(e).__name__})"
    return Upload(name=name, path=rel(output_dir, path), kind="document", text=text.strip())


def _save_image(output_dir: Path, name: str, raw: bytes) -> Upload:
    from PIL import Image, ImageOps

    try:
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(raw)))
        im.load()
    except Exception as e:  # noqa: BLE001
        raise UploadError(f"'{name}' ist kein lesbares Bild (HEIC bitte als JPG senden).") from e
    if im.mode not in ("RGB", "L"):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        im = im.convert("RGBA")
        bg.paste(im, mask=im.split()[-1])
        im = bg
    im.thumbnail((VISION_MAX_SIDE, VISION_MAX_SIDE))
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=85)
    jpeg = buf.getvalue()
    path = unique(safe_path(output_dir, "uploads/" + _clean_name(name, ".jpg")))
    path.write_bytes(jpeg)
    data_url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()
    return Upload(name=name, path=rel(output_dir, path), kind="image", data_url=data_url)


def process(output_dir: Path, attachments: list[dict]) -> list[Upload]:
    if len(attachments) > MAX_FILES:
        raise UploadError(f"Maximal {MAX_FILES} Dateien pro Nachricht.")
    return [save_attachment(output_dir, a.get("name") or "datei", a.get("data") or "") for a in attachments]
