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

from sqlalchemy import desc, select, update

from .agents import AGENTS, route
from .ai import Cancelled, Jarvis
from .config import load_config
from .db import DB, feedback as feedback_t, messages as messages_t, missions as missions_t, now
from .mail import MailError

log = logging.getLogger("jarvis")
STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_SESSIONS = 30

cfg = load_config()
START_TIME = time.time()
app = FastAPI(title="J.A.R.V.I.S.", docs_url=None, redoc_url=None)

_imap_box = None
_imap_key = None
_core = None
_core_lock = threading.Lock()
_last_mailbox = None


def core():
    """Der JARVIS-Kern (Datenbank, Agenten, Gedächtnis, Automatisierungen) – wird beim ersten Zugriff erstellt."""
    global _core
    with _core_lock:
        if _core is None:
            from .core import JarvisCore

            try:
                db = DB()
            except Exception as e:  # noqa: BLE001 - z. B. falsche DATABASE_URL: lieber lokal weiterlaufen
                log.exception("Datenbank nicht erreichbar – nutze lokale SQLite-Datei")
                db = DB(url="sqlite:///" + str(Path(os.getenv("JARVIS_DATA_DIR") or Path.home() / ".jarvis") / "jarvis.db"))
                db.error = f"DATABASE_URL funktioniert nicht ({type(e).__name__}) – Daten nur bis Neustart"
            _core = JarvisCore(cfg, db)
            _core.mailbox_provider = _background_mailbox
        return _core


def _background_mailbox():
    """Postfach für Hintergrund-Aufgaben: zuletzt im Chat genutztes Postfach oder das IMAP-Postfach."""
    box = _last_mailbox
    if box is not None and getattr(getattr(box, "auth", None), "connected", True):
        return box
    try:
        return imap_mailbox()
    except MailError:
        return None


@app.on_event("startup")
def _startup():
    c = core()
    try:
        c.restore_files()
    except Exception:  # noqa: BLE001
        log.exception("Dateien konnten nicht wiederhergestellt werden")
    # Missionen, die durch einen Neustart unterbrochen wurden, ehrlich als abgebrochen markieren
    c.db.run(update(missions_t).where(missions_t.c.status.in_(["planning", "running", "waiting", "paused"]))
             .values(status="failed", result="Durch Server-Neustart unterbrochen.", updated=now()))
    if not os.getenv("JARVIS_NO_SCHEDULER"):
        c.automations.start()


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
PUBLIC = {"/api/info", "/api/login", "/api/cron"}


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
        c = core()
        self.jarvis = Jarvis(cfg, registry=c.registry, core=c)
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
    agent: str = Field(default="auto", max_length=20)
    attachments: list[Attachment] = Field(default_factory=list, max_length=4)


class LoginBody(BaseModel):
    password: str = Field(max_length=200)


# --------------------------------------------------------------------------- API
@app.get("/api/info")
def info():
    return {
        "needs_password": bool(cfg.web_password),
        "user_name": cfg.user_name,
        "ai_ready": cfg.ai_ready,
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
            c = core()
            for u in uploads:
                c.persist_file(cfg.output_dir / u.path)
            try:
                mailbox = session.mailbox()
            except MailError as e:
                mailbox = None
                events.put({"type": "status", "text": f"E-Mail nicht verfügbar: {e}"})
            if mailbox is not None:
                global _last_mailbox
                _last_mailbox = mailbox
            agent = body.agent if body.agent in AGENTS else route(text)
            events.put({"type": "agent", "id": agent, "name": AGENTS[agent]["name"], "icon": AGENTS[agent]["icon"]})
            answer: list[str] = []

            def on_text(t):
                answer.append(t)
                events.put({"type": "text", "text": t})

            _save_message(body.session, "user", text + "".join(f"\n[Anhang: {u.name}]" for u in uploads))
            from .api_core import _active_chats

            _active_chats[body.session] = {"agent": agent, "text": text, "since": time.time()}
            try:
                session.jarvis.ask(text, on_text=on_text, on_status=lambda s: events.put({"type": "status", "text": s}),
                                   on_event=events.put, mailbox=mailbox, uploads=uploads, agent=agent)
            finally:
                _active_chats.pop(body.session, None)
            reply = "".join(answer)
            _save_message(body.session, "assistant", reply)
            if session.jarvis.last_route:
                events.put({"type": "route", **session.jarvis.last_route})
            if reply and c.security.allowed("auto_memory"):
                threading.Thread(target=_extract_memory, args=(text, reply), daemon=True).start()
            auth = session.outlook
            if auth is not None and auth.connected and auth.changed():
                events.put({"type": "mail_token", "token": auth.export()})
        except Cancelled:
            events.put({"type": "status", "text": "Abgebrochen."})
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


def _save_message(session_id: str, role: str, content: str) -> None:
    if not content:
        return
    try:
        core().db.add(messages_t, session=session_id, ts=now(), role=role, content=content[:20000])
    except Exception as e:  # noqa: BLE001
        log.info("Nachricht nicht gespeichert: %s", e)


def _extract_memory(question: str, answer: str) -> None:
    c = core()
    saved = c.memory.extract(c.registry, question, answer)
    for m in saved:
        c.db.event(f"Gedächtnis {'aktualisiert' if m['state'] == 'updated' else 'neu'}: {m['title'][:80]}",
                   kind="ok", agent="Memory Agent")


@app.post("/api/chat/stop")
def chat_stop(body: SessionBody):
    """Bricht die laufende Antwort einer Sitzung ab (wirkt beim nächsten Zwischenschritt)."""
    s = get_session(body.session)
    s.jarvis.should_stop = lambda: True

    def reset():
        time.sleep(3)
        s.jarvis.should_stop = lambda: False

    threading.Thread(target=reset, daemon=True).start()
    return {"ok": True}


@app.get("/api/history")
def history(session: str, limit: int = 60):
    rows = core().db.q(select(messages_t).where(messages_t.c.session == session).order_by(desc(messages_t.c.id)).limit(limit))
    return list(reversed(rows))


class FeedbackBody(BaseModel):
    rating: int = Field(ge=-1, le=1)
    question: str = Field(default="", max_length=20000)
    answer: str = Field(default="", max_length=40000)


@app.post("/api/feedback")
def give_feedback(body: FeedbackBody):
    core().db.add(feedback_t, ts=now(), rating=body.rating, question=body.question, answer=body.answer)
    return {"ok": True}


# --------------------------------------------------------------------------- Sprache
from .tts import STT_MAX_BYTES, Speech, SpeechError, transcribe  # noqa: E402

speech = Speech()
_stt_client = None


@app.get("/api/tts")
async def tts(text: str = "", voice: str = ""):
    """Text → MP3 (natürliche Stimme). Der Browser spielt das mit einem <audio>-Element ab."""
    if not text.strip():
        raise HTTPException(400, "Kein Text")
    try:
        audio = await speech.synth(text, voice or None)
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
def list_files(limit: int = 40):
    """Zuletzt erstellte/hochgeladene Dateien (für das Dashboard)."""
    root = cfg.output_dir.resolve()
    files = sorted((p for p in root.rglob("*") if p.is_file() and not p.name.endswith(".office.png")),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"name": p.name, "path": p.relative_to(root).as_posix(), "url": f"/files/{p.relative_to(root).as_posix()}",
             "size": p.stat().st_size, "time": int(p.stat().st_mtime),
             "upload": p.relative_to(root).parts[0] == "uploads",
             "folder": p.parent.relative_to(root).as_posix() if p.parent != root else ""} for p in files[:limit]]


@app.delete("/api/files")
def delete_file(path: str):
    """Löscht eine Datei aus dem JARVIS-Ordner (nur dort – Pfade wie '../' werden abgelehnt)."""
    from .tools.files import ToolError, existing_path

    try:
        target = existing_path(cfg.output_dir, path)
    except ToolError as e:
        raise HTTPException(404, str(e)) from e
    rel_path = target.relative_to(cfg.output_dir.resolve()).as_posix()
    target.unlink()
    core().db.delete_file(rel_path)
    core().db.event(f"Datei gelöscht: {rel_path}", kind="security", agent="Du")
    log.info("Datei gelöscht: %s", path)
    return {"ok": True}


@app.post("/api/files/delete-all")
def delete_all_files():
    root = cfg.output_dir.resolve()
    count = 0
    for p in sorted(root.rglob("*"), reverse=True):  # erst Dateien, dann leere Ordner
        if p.is_file():
            core().db.delete_file(p.relative_to(root).as_posix())
            p.unlink()
            count += 1
        elif p.is_dir() and not any(p.iterdir()):
            p.rmdir()
    core().db.event(f"Alle Dateien gelöscht ({count})", kind="security", agent="Du")
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
    c = core()
    return {"cpu": _cpu_percent(), "ram": _ram_percent(), "disk": round(100 * disk.used / disk.total, 1),
            "uptime": int(time.time() - START_TIME), "sessions": len(_sessions),
            "db": {"kind": c.db.kind, "persistent": c.db.persistent, "files_mb": round(c.db.used_bytes() / 1048576, 1)},
            "threads": threading.active_count()}


from . import api_core  # noqa: E402,F401  (registriert die Kern-Endpunkte)

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
