"""Webserver von JARVIS (FastAPI). Start: python -m jarvis"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
import shutil
import time
import queue
import secrets
import threading
from collections import OrderedDict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .ai import Jarvis
from .config import load_config
from .mail import MailError

log = logging.getLogger("jarvis")
STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_SESSIONS = 30

cfg = load_config()
START_TIME = time.time()
app = FastAPI(title="J.A.R.V.I.S.", docs_url=None, redoc_url=None)

_imap_box = None
_imap_key = None


def imap_mailbox():
    """Gemeinsames IMAP-Postfach aus MAIL_ADDRESS/MAIL_APP_PASSWORD (oder None). Wirft MailError bei falscher Einrichtung."""
    global _imap_box, _imap_key
    if not cfg.imap_enabled:
        return None
    key = (cfg.mail_address, cfg.mail_password, cfg.mail_imap_host, cfg.mail_smtp_host)
    if _imap_box is None or _imap_key != key:
        from .mail.imap import ImapMailbox

        _imap_box = ImapMailbox(*key)  # Fehler werden nicht zwischengespeichert
        _imap_key = key
    return _imap_box


# --------------------------------------------------------------------------- Passwortschutz
PUBLIC = {"/api/info", "/api/login"}


def _password_ok(given: str) -> bool:
    return secrets.compare_digest(given.encode(), cfg.web_password.encode())


@app.middleware("http")
async def check_password(request: Request, call_next):
    path = request.url.path
    if cfg.web_password and path.startswith(("/api/", "/files/")) and path not in PUBLIC:
        given = request.headers.get("x-jarvis-key") or request.query_params.get("key") or ""
        if not _password_ok(given):
            await asyncio.sleep(1.5)  # bremst Passwort-Raten aus
            return JSONResponse({"error": "Passwort erforderlich"}, status_code=401)
    return await call_next(request)


# --------------------------------------------------------------------------- Sitzungen
class Session:
    def __init__(self):
        self.jarvis = Jarvis(cfg)
        self.outlook = None
        self.lock = threading.Lock()

    def outlook_auth(self):
        if self.outlook is None and cfg.outlook_enabled:
            from .mail.outlook import OutlookAuth

            self.outlook = OutlookAuth(cfg.ms_client_id, cfg.web_password or "jarvis-local")
        return self.outlook

    def mailbox(self):
        auth = self.outlook_auth()
        if auth is not None and auth.connected:
            from .mail.outlook import OutlookMailbox

            return OutlookMailbox(auth)
        return imap_mailbox()


_sessions: OrderedDict[str, Session] = OrderedDict()
_sessions_lock = threading.Lock()


def get_session(session_id: str) -> Session:
    with _sessions_lock:
        if session_id not in _sessions:
            _sessions[session_id] = Session()
            while len(_sessions) > MAX_SESSIONS:
                _sessions.popitem(last=False)
        _sessions.move_to_end(session_id)
        return _sessions[session_id]


class SessionBody(BaseModel):
    session: str = Field(min_length=8, max_length=100)


class Attachment(BaseModel):
    name: str = Field(default="datei", max_length=200)
    type: str = Field(default="", max_length=120)
    data: str = Field(max_length=12_000_000)


class ChatBody(SessionBody):
    text: str = Field(default="", max_length=20000)
    attachments: list[Attachment] = Field(default_factory=list, max_length=4)


class LoginBody(BaseModel):
    password: str = Field(max_length=200)


# --------------------------------------------------------------------------- API
@app.get("/api/info")
def info():
    return {
        "needs_password": bool(cfg.web_password),
        "user_name": cfg.user_name,
        "ai_ready": bool(cfg.groq_api_key),
        "outlook_available": cfg.outlook_enabled,
        "imap_address": cfg.mail_address if cfg.imap_enabled else None,
    }


@app.post("/api/login")
async def login(body: LoginBody):
    if cfg.web_password and not _password_ok(body.password):
        await asyncio.sleep(1.5)
        return JSONResponse({"ok": False}, status_code=401)
    return {"ok": True}


def _restore_outlook(session: Session, request: Request) -> None:
    auth = session.outlook_auth()
    token = request.headers.get("x-mail-token")
    if auth is not None and token:
        auth.restore(token)


@app.post("/api/chat")
def chat(body: ChatBody, request: Request):
    """Antwort als NDJSON-Stream: text | status | file | mail_sent | mail_token | error | done"""
    session = get_session(body.session)
    _restore_outlook(session, request)
    events: queue.Queue[dict | None] = queue.Queue()

    def worker():
        if not session.lock.acquire(timeout=1):
            events.put({"type": "error", "text": "Ich bearbeite noch Ihre vorherige Frage."})
            events.put(None)
            return
        try:
            uploads = []
            if body.attachments:
                from .uploads import UploadError, process

                try:
                    uploads = process(cfg.output_dir, [a.model_dump() for a in body.attachments])
                except UploadError as e:
                    events.put({"type": "error", "text": str(e)})
                    return
            text = body.text.strip() or (
                "Was siehst du auf dem Bild? Beschreibe es kurz." if any(u.kind == "image" for u in uploads)
                else "Bitte fasse die angehängte Datei kurz zusammen." if uploads else "")
            if not text:
                events.put({"type": "error", "text": "Bitte eine Nachricht eingeben."})
                return
            try:
                mailbox = session.mailbox()
            except MailError as e:
                mailbox = None
                events.put({"type": "status", "text": f"E-Mail nicht verfügbar: {e}"})
            session.jarvis.ask(text,
                               on_text=lambda t: events.put({"type": "text", "text": t}),
                               on_status=lambda s: events.put({"type": "status", "text": s}),
                               on_event=events.put, mailbox=mailbox, uploads=uploads)
            auth = session.outlook
            if auth is not None and auth.connected and auth.changed():
                events.put({"type": "mail_token", "token": auth.export()})
        except Exception as e:  # noqa: BLE001
            log.exception("Chat-Fehler")
            events.put({"type": "error", "text": f"Interner Fehler: {type(e).__name__}"})
        finally:
            session.lock.release()
            events.put(None)

    threading.Thread(target=worker, daemon=True).start()

    def stream():
        while (event := events.get()) is not None:
            yield json.dumps(event, ensure_ascii=False) + "\n"
        yield json.dumps({"type": "done"}) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# --------------------------------------------------------------------------- Sprache
from .tts import STT_MAX_BYTES, Speech, SpeechError, transcribe  # noqa: E402

speech = Speech()
_stt_client = None


@app.get("/api/tts")
async def tts(text: str = ""):
    """Text → MP3 (natürliche Stimme). Der Browser spielt das mit einem <audio>-Element ab."""
    if not text.strip():
        raise HTTPException(400, "Kein Text")
    try:
        audio = await speech.synth(text)
    except SpeechError as e:
        raise HTTPException(503, "Stimme gerade nicht verfügbar") from e
    return Response(audio, media_type="audio/mpeg", headers={"Cache-Control": "private, max-age=3600"})


@app.post("/api/stt")
async def stt(request: Request):
    """Aufnahme (Roh-Daten im Body) → Text, mit Groq Whisper."""
    global _stt_client
    data = await request.body()
    if len(data) > STT_MAX_BYTES:
        raise HTTPException(413, "Aufnahme zu lang")
    if not data:
        raise HTTPException(400, "Leere Aufnahme")
    if not cfg.groq_api_key:
        raise HTTPException(503, "GROQ_API_KEY fehlt")
    if _stt_client is None:
        from groq import Groq

        _stt_client = Groq(api_key=cfg.groq_api_key, max_retries=1, timeout=60)
    try:
        text = await asyncio.to_thread(transcribe, _stt_client, data, request.headers.get("content-type", ""))
    except SpeechError as e:
        raise HTTPException(502, "Spracherkennung gerade nicht verfügbar") from e
    return {"text": text}


@app.post("/api/reset")
def reset(body: SessionBody):
    get_session(body.session).jarvis.reset()
    return {"ok": True}


# --------------------------------------------------------------------------- E-Mail verbinden
@app.get("/api/mail/status")
def mail_status(session: str, request: Request):
    s = get_session(session)
    _restore_outlook(s, request)
    auth = s.outlook
    result = {"outlook_available": cfg.outlook_enabled,
              "imap_address": cfg.mail_address if cfg.imap_enabled else None,
              "connected": False, "provider": None, "address": None, "pending": None, "error": None, "token": None}
    if auth is not None:
        result["pending"] = auth.pending()
        result["error"] = auth.error
        if auth.connected:
            result.update(connected=True, provider="outlook", address=auth.address, token=auth.export())
            return result
    if cfg.imap_enabled:
        try:
            box = imap_mailbox()
            result.update(connected=True, provider="imap", address=box.address)
        except MailError as e:
            result.update(provider="imap", address=cfg.mail_address, error=str(e))
    return result


@app.post("/api/mail/test")
def mail_test(body: SessionBody, request: Request):
    """Prüft, ob die Anmeldung am Postfach wirklich klappt."""
    s = get_session(body.session)
    _restore_outlook(s, request)
    try:
        box = s.mailbox()
        if box is None:
            return {"ok": False, "message": "Es ist noch kein Postfach eingerichtet."}
        if hasattr(box, "test"):
            return {"ok": True, "message": box.test()}
        box.list(limit=1)
        return {"ok": True, "message": f"Verbunden – Postfach {box.address} ist erreichbar."}
    except MailError as e:
        return {"ok": False, "message": str(e)}
    except Exception as e:  # noqa: BLE001
        log.exception("Mail-Test fehlgeschlagen")
        return {"ok": False, "message": f"Unerwarteter Fehler: {type(e).__name__}: {e}"}


@app.post("/api/mail/outlook/start")
def outlook_start(body: SessionBody):
    auth = get_session(body.session).outlook_auth()
    if auth is None:
        raise HTTPException(400, "Outlook ist nicht eingerichtet (MS_CLIENT_ID fehlt).")
    try:
        return auth.start()
    except MailError as e:
        raise HTTPException(502, str(e)) from e
    except Exception as e:  # noqa: BLE001 - z.B. ungültige Client-ID
        log.exception("Outlook-Start fehlgeschlagen")
        raise HTTPException(502, f"Microsoft-Anmeldung nicht möglich: {e}") from e


@app.post("/api/mail/outlook/disconnect")
def outlook_disconnect(body: SessionBody):
    auth = get_session(body.session).outlook
    if auth is not None:
        auth.disconnect()
    return {"ok": True}


# --------------------------------------------------------------------------- Dashboard-Daten
@app.get("/api/files")
def list_files():
    """Zuletzt erstellte/hochgeladene Dateien (für das Dashboard)."""
    root = cfg.output_dir.resolve()
    files = sorted((p for p in root.rglob("*") if p.is_file() and not p.name.endswith(".office.png")),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"name": p.name, "path": p.relative_to(root).as_posix(), "url": f"/files/{p.relative_to(root).as_posix()}",
             "size": p.stat().st_size, "time": int(p.stat().st_mtime),
             "upload": p.relative_to(root).parts[0] == "uploads"} for p in files[:40]]


@app.delete("/api/files")
def delete_file(path: str):
    """Löscht eine Datei aus dem JARVIS-Ordner (nur dort – Pfade wie '../' werden abgelehnt)."""
    from .tools.files import ToolError, existing_path

    try:
        target = existing_path(cfg.output_dir, path)
    except ToolError as e:
        raise HTTPException(404, str(e)) from e
    target.unlink()
    log.info("Datei gelöscht: %s", path)
    return {"ok": True}


@app.post("/api/files/delete-all")
def delete_all_files():
    root = cfg.output_dir.resolve()
    count = 0
    for p in sorted(root.rglob("*"), reverse=True):  # erst Dateien, dann leere Ordner
        if p.is_file():
            p.unlink()
            count += 1
        elif p.is_dir() and not any(p.iterdir()):
            p.rmdir()
    log.info("%s Dateien gelöscht", count)
    return {"ok": True, "deleted": count}


def _cpu_percent() -> float | None:
    try:
        return round(min(100.0, os.getloadavg()[0] / (os.cpu_count() or 1) * 100), 1)
    except (AttributeError, OSError):
        return None


def _ram_percent() -> float | None:
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                key, value = line.split(":", 1)
                info[key] = int(value.split()[0])
        return round(100 * (1 - info["MemAvailable"] / info["MemTotal"]), 1)
    except (OSError, KeyError, ValueError):
        return None


@app.get("/api/system")
def system():
    """Echte Auslastung des Servers (CPU-Last, Arbeitsspeicher, Speicherplatz)."""
    disk = shutil.disk_usage(cfg.output_dir)
    return {"cpu": _cpu_percent(), "ram": _ram_percent(), "disk": round(100 * disk.used / disk.total, 1),
            "uptime": int(time.time() - START_TIME), "sessions": len(_sessions)}


# --------------------------------------------------------------------------- Dateien
@app.get("/files/{rel_path:path}")
def get_file(rel_path: str):
    root = cfg.output_dir.resolve()
    target = (root / rel_path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(404, "Datei nicht gefunden")
    return FileResponse(target, media_type=mimetypes.guess_type(target.name)[0] or "application/octet-stream",
                        filename=target.name)


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
