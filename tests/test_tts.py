import asyncio
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from jarvis import server
from jarvis.tts import Speech, SpeechError, transcribe


def run(coro):
    return asyncio.run(coro)


def test_primary_voice_and_cache():
    sp = Speech(voice="de-DE-ConradNeural")
    calls = []

    async def fake_edge(text, voice):
        calls.append((text, voice))
        return b"ID3-edge"

    sp.primary = fake_edge
    assert run(sp.synth("Hallo   Sir.")) == b"ID3-edge"
    assert run(sp.synth("Hallo Sir.")) == b"ID3-edge"
    assert calls == [("Hallo Sir.", "de-DE-ConradNeural")]  # zweites Mal aus dem Speicher


def test_backup_voice_and_failure():
    sp = Speech()

    async def broken(text, voice):
        raise OSError("kein Netz")

    sp.primary = broken
    sp.backup = lambda text, lang: b"ID3-gtts" if lang == "de" else b""
    assert run(sp.synth("Test")) == b"ID3-gtts"

    def also_broken(text, lang):
        raise OSError("auch kein Netz")

    sp.backup = also_broken
    with pytest.raises(SpeechError):
        run(sp.synth("Anderer Text"))


def test_text_is_limited():
    sp = Speech()
    seen = []

    async def fake(text, voice):
        seen.append(text)
        return b"ID3"

    sp.primary = fake
    run(sp.synth("a" * 2000))
    assert len(seen[0]) == 600


def test_transcribe_falls_back_to_second_model():
    calls = []

    def create(**kw):
        calls.append((kw["model"], kw["file"][0]))
        if len(calls) == 1:
            raise RuntimeError("busy")
        return NS(text=" Wie wird das Wetter? ")

    client = NS(audio=NS(transcriptions=NS(create=create)))
    assert transcribe(client, b"data", "audio/mp4") == "Wie wird das Wetter?"
    assert calls == [("whisper-large-v3-turbo", "aufnahme.m4a"), ("whisper-large-v3", "aufnahme.m4a")]


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(server.cfg, "web_password", "pw123")
    monkeypatch.setattr(server.cfg, "groq_api_key", "gsk_fake")
    monkeypatch.setattr(server.cfg, "output_dir", tmp_path)

    async def fake_edge(text, voice):
        return b"ID3-" + text.encode()

    monkeypatch.setattr(server.speech, "primary", fake_edge)
    server.speech.cache.clear()
    return TestClient(server.app)


def test_tts_endpoint(client, monkeypatch):
    assert client.get("/api/tts", params={"text": "Hallo"}).status_code == 401
    r = client.get("/api/tts", params={"text": "Hallo", "key": "pw123"})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg" and r.content == b"ID3-Hallo"
    assert client.get("/api/tts", params={"text": " ", "key": "pw123"}).status_code == 400

    async def broken(text, voice):
        raise OSError()

    def broken2(text, lang):
        raise OSError()

    monkeypatch.setattr(server.speech, "primary", broken)
    monkeypatch.setattr(server.speech, "backup", broken2)
    assert client.get("/api/tts", params={"text": "Neu", "key": "pw123"}).status_code == 503


def test_stt_endpoint(client, monkeypatch):
    fake = NS(audio=NS(transcriptions=NS(create=lambda **kw: NS(text="Hallo JARVIS"))))
    monkeypatch.setattr(server, "_stt_client", fake)
    h = {"X-Jarvis-Key": "pw123", "Content-Type": "audio/mp4"}
    r = client.post("/api/stt", content=b"\x00" * 100, headers=h)
    assert r.json() == {"text": "Hallo JARVIS"}
    assert client.post("/api/stt", content=b"", headers=h).status_code == 400
    assert client.post("/api/stt", content=b"\x00" * (10 * 1024 * 1024 + 1), headers=h).status_code == 413
    assert client.post("/api/stt", content=b"x", headers={"Content-Type": "audio/mp4"}).status_code == 401
