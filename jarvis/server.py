"""Webserver von JARVIS (FastAPI). Start: python -m jarvis"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import queue
import secrets
import threading
from collections import OrderedDict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .ai import Jarvis
from .config import load_config
from .mail import MailError

log = logging.getLogger("jarvis")
STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_SESSIONS = 30

cfg = load_config()
app = FastAPI(title="J.A.R.V.I.S.", docs_url=None, redoc_url=None)

_imap_box = None


def imap_mailbox():
    """Gemeinsames IMAP-Postfach aus MAIL_ADDRESS/MAIL_APP_PASSWORD (oder None)."""
    global _imap_box
    if _imap_box is None and cfg.imap_enabled:
        from .mail.imap import ImapMailbox

        _imap_box = ImapMailbox(cfg.mail_address, cfg.mail_password, cfg.mail_imap_host, cfg.mail_smtp_host)
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


class ChatBody(SessionBody):
    text: str = Field(min_length=1, max_length=20000)


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
            try:
                mailbox = session.mailbox()
            except MailError as e:
                mailbox = None
                events.put({"type": "status", "text": f"E-Mail nicht verfügbar: {e}"})
            session.jarvis.ask(body.text,
                               on_text=lambda t: events.put({"type": "text", "text": t}),
                               on_status=lambda s: events.put({"type": "status", "text": s}),
                               on_event=events.put, mailbox=mailbox)
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
        result.update(connected=True, provider="imap", address=cfg.mail_address)
    return result


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
