"""JARVIS als Webseite: python -m jarvis.web  ->  http://localhost:8000

Der Server hält die API-Keys geheim; der Browser übernimmt Mikrofon (Web Speech API),
Wiedergabe der ElevenLabs-Stimme und die Downloads der erstellten Dateien.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
import os
import queue
import secrets
import threading
import webbrowser
from collections import OrderedDict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .brain import create_brain
from .config import load_config

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_SESSIONS = 50

cfg = load_config()
app = FastAPI(title="J.A.R.V.I.S.", docs_url=None, redoc_url=None)

# Passwortschutz (nur nötig, wenn der Server im Heimnetz erreichbar ist: --lan)
WEB_PASSWORD = os.getenv("JARVIS_WEB_PASSWORD") or ""


@app.middleware("http")
async def check_password(request: Request, call_next):
    if WEB_PASSWORD and request.url.path.startswith(("/api/", "/files/")) and request.url.path != "/api/status":
        given = request.headers.get("x-jarvis-key") or request.query_params.get("key") or ""
        if not secrets.compare_digest(given.encode(), WEB_PASSWORD.encode()):
            await asyncio.sleep(1.5)  # bremst Passwort-Raten aus
            return JSONResponse({"error": "Passwort erforderlich"}, status_code=401)
    return await call_next(request)


# ---------------------------------------------------------------------------
# Sitzungen: jede Browser-Sitzung hat ihr eigenes Gedächtnis
# ---------------------------------------------------------------------------

class Session:
    def __init__(self):
        self.brain = create_brain(cfg, web=True)
        self.lock = threading.Lock()


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


class ChatRequest(BaseModel):
    session: str = Field(min_length=8, max_length=100)
    text: str = Field(min_length=1, max_length=20000)


class SessionRequest(BaseModel):
    session: str = Field(min_length=8, max_length=100)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.get("/api/status")
def status():
    return {
        "user_name": cfg.user_name,
        "model": cfg.brain_label,
        "brain_ok": cfg.brain_ready,
        "voice": "elevenlabs" if cfg.elevenlabs_api_key else "browser",
        "language": cfg.language,
        "password_required": bool(WEB_PASSWORD),
    }


@app.post("/api/chat")
def chat(req: ChatRequest):
    """Streamt die Antwort als NDJSON: {"type": "text"|"status"|"file"|"open"|"error"|"done", ...}"""
    session = get_session(req.session)
    events: queue.Queue[dict | None] = queue.Queue()

    def worker():
        if not session.lock.acquire(timeout=1):
            events.put({"type": "error", "text": "Ich bearbeite noch Ihre vorherige Anfrage."})
            events.put(None)
            return
        try:
            if not cfg.brain_ready:
                events.put({"type": "text", "text": f"Mir fehlt noch mein Zugangsschlüssel: {cfg.missing_key_hint} Bitte eintragen und mich neu starten."})
                return
            session.brain.ask(
                req.text,
                on_text=lambda t: events.put({"type": "text", "text": t}),
                on_status=lambda s: events.put({"type": "status", "text": s}),
                on_event=lambda e: events.put(_public_event(e)),
            )
        except Exception as e:  # noqa: BLE001 - Fehler an die Oberfläche melden statt abzustürzen
            events.put({"type": "error", "text": f"Interner Fehler: {type(e).__name__}: {e}"})
        finally:
            session.lock.release()
            events.put(None)

    threading.Thread(target=worker, daemon=True).start()

    def stream():
        while True:
            event = events.get()
            if event is None:
                yield json.dumps({"type": "done"}) + "\n"
                return
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _public_event(event: dict) -> dict:
    """Ersetzt lokale Pfade durch Download-URLs."""
    if event.get("type") == "file":
        rel = Path(event["path"]).resolve().relative_to(cfg.output_dir.resolve()).as_posix()
        return {"type": "file", "name": event["name"], "url": f"/files/{rel}", "action": event.get("action")}
    return event


@app.post("/api/reset")
def reset(req: SessionRequest):
    get_session(req.session).brain.reset()
    return {"ok": True}


@app.get("/api/tts")
def tts(text: str, prev: str = ""):
    """Liefert die ElevenLabs-Stimme als gestreamtes MP3 (der Browser spielt sofort ab)."""
    if not cfg.elevenlabs_api_key:
        raise HTTPException(404, "Kein ElevenLabs-Key konfiguriert")
    text = text.strip()[:1500]
    if not text:
        raise HTTPException(400, "Kein Text")
    from elevenlabs.client import ElevenLabs
    from elevenlabs import VoiceSettings

    client = ElevenLabs(api_key=cfg.elevenlabs_api_key)
    kwargs = {
        "text": text,
        "model_id": cfg.elevenlabs_model,
        "output_format": "mp3_44100_128",
        "optimize_streaming_latency": 2,
        "voice_settings": VoiceSettings(stability=0.45, similarity_boost=0.8, style=0.15,
                                        use_speaker_boost=True, speed=1.05),
    }
    if "flash" in cfg.elevenlabs_model or "turbo" in cfg.elevenlabs_model:
        kwargs["language_code"] = cfg.language.split("-")[0]
    if prev:
        kwargs["previous_text"] = prev[-300:]
    try:
        audio = client.text_to_speech.stream(cfg.elevenlabs_voice_id, **kwargs)
        first = next(audio)  # Fehler (z.B. falscher Key) hier abfangen, bevor die Antwort startet
    except StopIteration:
        raise HTTPException(502, "Leere Antwort von ElevenLabs")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"ElevenLabs-Fehler: {e}")

    def body():
        yield first
        yield from audio

    return StreamingResponse(body(), media_type="audio/mpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/files")
def list_files():
    root = cfg.output_dir.resolve()
    files = sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"name": p.name, "url": f"/files/{p.relative_to(root).as_posix()}",
             "size": p.stat().st_size} for p in files[:50]]


@app.get("/files/{rel_path:path}")
def get_file(rel_path: str, download: bool = True):
    root = cfg.output_dir.resolve()
    target = (root / rel_path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(404, "Datei nicht gefunden")
    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    if download:
        return FileResponse(target, media_type=media_type, filename=target.name)
    return FileResponse(target, media_type=media_type)


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


def main() -> None:
    import logging

    import uvicorn

    # Fehlerdetails erscheinen im Terminal bzw. bei Render unter "Logs"
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(prog="jarvis.web", description="JARVIS Weboberfläche")
    parser.add_argument("--port", type=int, default=int(os.getenv("JARVIS_WEB_PORT", "8000")))
    parser.add_argument("--lan", action="store_true",
                        help="Im Heimnetz erreichbar machen (z.B. vom Handy). Erfordert JARVIS_WEB_PASSWORD in .env")
    parser.add_argument("--no-browser", action="store_true", help="Browser nicht automatisch öffnen")
    parser.add_argument("--cloud", action="store_true",
                        help="Betrieb auf einem Online-Server (Render & Co.): Port aus $PORT, Passwort Pflicht")
    args = parser.parse_args()

    host = "127.0.0.1"
    if args.lan or args.cloud:
        if not WEB_PASSWORD:
            raise SystemExit("Bitte zuerst JARVIS_WEB_PASSWORD setzen - sonst könnte jeder Ihr API-Guthaben verbrauchen.")
        host = "0.0.0.0"
    if args.cloud:
        args.port = int(os.getenv("PORT", args.port))
        args.no_browser = True

    url = f"http://localhost:{args.port}"
    print(f"\n  J.A.R.V.I.S. ist online:  {url}\n  (Beenden mit Strg+C)\n")
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=host, port=args.port, log_level="warning", proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
