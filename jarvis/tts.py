"""Sprache: Text → MP3 (Stimme) und Aufnahme → Text (Spracherkennung). Beides kostenlos.

Stimme: Microsoft-Neural-Stimme über edge-tts (kein Key nötig), Ersatz: Google gTTS.
Erkennung: Groq Whisper mit dem vorhandenen GROQ_API_KEY.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
from collections import OrderedDict

log = logging.getLogger("jarvis.tts")

DEFAULT_VOICE = "de-DE-ConradNeural"   # tiefe, ruhige Männerstimme
VOICES = {   # wählbare Stimmen (Microsoft Neural, kostenlos)
    "de-DE-ConradNeural": "Conrad – tief, ruhig (Standard)",
    "de-DE-KillianNeural": "Killian – jung, klar",
    "de-DE-FlorianMultilingualNeural": "Florian – natürlich",
    "de-DE-KatjaNeural": "Katja – weiblich, klar",
    "de-DE-SeraphinaMultilingualNeural": "Seraphina – weiblich, warm",
    "de-AT-JonasNeural": "Jonas – österreichisch",
    "de-CH-JanNeural": "Jan – schweizerisch",
    "en-GB-RyanNeural": "Ryan – britisch (Englisch)",
}
MAX_CHARS = 600
CACHE_SIZE = 64
TIMEOUT = 15
STT_MODELS = ["whisper-large-v3-turbo", "whisper-large-v3"]
STT_MAX_BYTES = 10 * 1024 * 1024


class SpeechError(Exception):
    pass


async def _edge(text: str, voice: str) -> bytes:
    import edge_tts

    audio = bytearray()
    async for chunk in edge_tts.Communicate(text, voice, rate="+5%").stream():
        if chunk.get("type") == "audio":
            audio += chunk["data"]
    if not audio:
        raise SpeechError("edge-tts lieferte kein Audio")
    return bytes(audio)


def _gtts(text: str, lang: str) -> bytes:
    from gtts import gTTS

    buf = io.BytesIO()
    gTTS(text, lang=lang).write_to_fp(buf)
    data = buf.getvalue()
    if not data:
        raise SpeechError("gTTS lieferte kein Audio")
    return data


class Speech:
    def __init__(self, voice: str | None = None):
        self.voice = voice or os.getenv("JARVIS_VOICE") or DEFAULT_VOICE
        self.lang = self.voice.split("-")[0] if "-" in self.voice else "de"
        self.cache: OrderedDict[str, bytes] = OrderedDict()
        # austauschbar für Tests
        self.primary = _edge
        self.backup = _gtts

    async def synth(self, text: str, voice: str | None = None) -> bytes:
        text = " ".join(text.split())[:MAX_CHARS]
        if not text:
            raise SpeechError("kein Text")
        voice = voice if voice in VOICES else self.voice
        key = f"{voice}|{text}"
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        try:
            audio = await asyncio.wait_for(self.primary(text, voice), TIMEOUT)
        except Exception as e:  # noqa: BLE001 - dann eben die Ersatz-Stimme
            log.warning("Microsoft-Stimme nicht verfügbar (%s) – nehme Google-Stimme", e)
            try:
                audio = await asyncio.wait_for(asyncio.to_thread(self.backup, text, voice.split("-")[0]), TIMEOUT)
            except Exception as e2:  # noqa: BLE001
                log.error("Keine Stimme verfügbar: %s", e2)
                raise SpeechError(str(e2)) from e2
        self.cache[key] = audio
        while len(self.cache) > CACHE_SIZE:
            self.cache.popitem(last=False)
        return audio


def transcribe(client, data: bytes, mime: str) -> str:
    """Aufnahme → Text mit Groq Whisper (probiert zwei Modelle)."""
    if not data:
        raise SpeechError("Leere Aufnahme")
    ext = "m4a" if "mp4" in mime or "m4a" in mime or "aac" in mime else "ogg" if "ogg" in mime else "wav" if "wav" in mime else "webm"
    last = None
    for model in STT_MODELS:
        try:
            res = client.audio.transcriptions.create(file=(f"aufnahme.{ext}", data), model=model, language="de",
                                                     response_format="json", temperature=0)
            return (getattr(res, "text", "") or "").strip()
        except Exception as e:  # noqa: BLE001
            last = e
            log.warning("Spracherkennung mit %s fehlgeschlagen: %s", model, e)
    raise SpeechError(f"Spracherkennung fehlgeschlagen: {last}")
