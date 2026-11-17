import json
from types import SimpleNamespace as NS

import groq
import pytest
import httpx
from fastapi.testclient import TestClient

from jarvis import server
from jarvis.ai import Jarvis
from jarvis.config import load_config


def text_chunk(t):
    return NS(choices=[NS(delta=NS(content=t, tool_calls=None))])


def tool_chunk(index, id=None, name=None, args=None):
    tc = NS(index=index, id=id, function=NS(name=name, arguments=args))
    return NS(choices=[NS(delta=NS(content=None, tool_calls=[tc]))])


def err(cls, code, msg, headers=None):
    resp = httpx.Response(code, request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions"),
                          json={"error": {"message": msg}}, headers=headers or {})
    return cls(msg, response=resp, body={"error": {"message": msg}})


class FakeCompletions:
    def __init__(self, turns):
        self.turns, self.calls = list(turns), []

    def create(self, **kw):
        self.calls.append(kw)
        t = self.turns.pop(0)
        if isinstance(t, Exception):
            raise t
        return iter(t) if kw.get("stream") else t


def jarvis(tmp_path, turns):
    cfg = load_config()
    cfg.output_dir, cfg.groq_api_key, cfg.groq_model = tmp_path, "gsk_fake", None
    return Jarvis(cfg, client=NS(chat=NS(completions=FakeCompletions(turns))))


def test_tool_loop_creates_file(tmp_path):
    j = jarvis(tmp_path, [
        [text_chunk("Moment. "), tool_chunk(0, id="c1", name="create_file", args='{"filename": "n.t'),
         tool_chunk(0, args='xt", "content": "hi"}')],
        [text_chunk("Fertig.")],
    ])
    out, events = [], []
    j.ask("Datei", out.append, lambda s: out.append(f"[{s}]"), events.append)
    assert out == ["Moment. ", "[Erstelle Datei n.txt]", "Fertig."]
    assert events == [{"type": "file", "name": "n.txt", "url": "/files/n.txt"}]
    assert [m["role"] for m in j.history] == ["user", "assistant", "tool", "assistant"]
    assert j.history[2]["tool_call_id"] == "c1"


def test_mail_tools_only_when_connected(tmp_path):
    j = jarvis(tmp_path, [[text_chunk("a")], [text_chunk("b")]])
    j.ask("x", lambda t: None, lambda s: None)
    names = [t["function"]["name"] for t in j.client.chat.completions.calls[0]["tools"]]
    assert "list_emails" not in names and "get_datetime" not in names
    j.ask("y", lambda t: None, lambda s: None, mailbox=object())
    names = [t["function"]["name"] for t in j.client.chat.completions.calls[1]["tools"]]
    assert "send_email" in names
    assert j.client.chat.completions.calls[1]["messages"][0]["role"] == "system"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    sleeps = []
    monkeypatch.setattr("jarvis.ai.time.sleep", sleeps.append)
    return sleeps


def models_used(j):
    return [c["model"] for c in j.client.chat.completions.calls]


def test_rate_limit_waits_then_switches(tmp_path, no_sleep):
    j = jarvis(tmp_path, [err(groq.RateLimitError, 429, "rate limit", {"retry-after": "2"}), [text_chunk("Hallo")]])
    out = []
    j.ask("hi", out.append, lambda s: None)
    assert out == ["Hallo"] and no_sleep == [2.0] and models_used(j) == ["openai/gpt-oss-120b"] * 2
    # langes retry-after → sofort anderes Modell
    j = jarvis(tmp_path, [err(groq.RateLimitError, 429, "rate limit", {"retry-after": "40"}), [text_chunk("Hallo")]])
    j.ask("hi", out.append, lambda s: None)
    assert models_used(j) == ["openai/gpt-oss-120b", "llama-3.3-70b-versatile"]


def test_too_large_trims_then_next_model(tmp_path):
    too_big = lambda: err(groq.APIStatusError, 413, "Request too large for model: tokens per minute (TPM)")  # noqa: E731
    j = jarvis(tmp_path, [too_big(), too_big(), [text_chunk("ok")]])
    for i in range(6):
        j.history.append({"role": "user" if i % 2 == 0 else "assistant", "content": str(i)})
    out = []
    j.ask("neu", out.append, lambda s: None)
    assert out == ["ok"]
    assert models_used(j) == ["openai/gpt-oss-120b", "openai/gpt-oss-120b", "llama-3.3-70b-versatile"]
    assert len(j.client.chat.completions.calls[1]["messages"]) == 2  # System + aktuelle Frage


def test_each_question_starts_with_best_model(tmp_path):
    j = jarvis(tmp_path, [err(groq.BadRequestError, 400, "weird"), [text_chunk("a")], [text_chunk("b")]])
    j.ask("1", lambda t: None, lambda s: None)
    j.ask("2", lambda t: None, lambda s: None)
    assert models_used(j) == ["openai/gpt-oss-120b", "llama-3.3-70b-versatile", "openai/gpt-oss-120b"]


def test_network_error_is_retried(tmp_path, no_sleep):
    req = httpx.Request("POST", "https://api.groq.com")
    j = jarvis(tmp_path, [groq.APIConnectionError(request=req), err(groq.InternalServerError, 503, "busy"), [text_chunk("da")]])
    out = []
    j.ask("hi", out.append, lambda s: None)
    assert out == ["da"] and no_sleep == [1.5, 3.0]


def test_everything_fails_gives_friendly_message(tmp_path):
    bad = [err(groq.BadRequestError, 400, "kaputt gsk_SECRET") for _ in range(20)]
    j = jarvis(tmp_path, bad)
    out = []
    j.ask("hi", out.append, lambda s: None)
    assert out == ["Ich bin gerade etwas überlastet. Bitte versuch es in einer Minute nochmal."]
    assert j.history == [] and "Statuscode" not in out[0]
    calls = j.client.chat.completions.calls
    assert "tools" in calls[0] and "tools" not in calls[-1]  # letzter Versuch ohne Werkzeuge
    assert len(calls) <= 14


def test_bad_key(tmp_path):
    j = jarvis(tmp_path, [err(groq.AuthenticationError, 401, "Invalid API Key")])
    out = []
    j.ask("hi", out.append, lambda s: None)
    assert "GROQ_API_KEY wird abgelehnt" in out[0] and j.history == [] and len(j.client.chat.completions.calls) == 1


def test_tool_use_failed_recovers(tmp_path):
    fail = lambda: err(groq.BadRequestError, 400, "tool_use_failed: Failed to call a function")  # noqa: E731
    j = jarvis(tmp_path, [fail(), fail(), [text_chunk("ok")]])
    out = []
    j.ask("hi", out.append, lambda s: None)
    assert out == ["ok"] and models_used(j) == ["openai/gpt-oss-120b"] * 2 + ["llama-3.3-70b-versatile"]


def test_empty_answer_tries_next_model(tmp_path):
    j = jarvis(tmp_path, [[], [text_chunk("Hier")]])
    out = []
    j.ask("hi", out.append, lambda s: None)
    assert out == ["Hier"]


def test_old_tool_results_are_shortened(tmp_path):
    j = jarvis(tmp_path, [[tool_chunk(0, id="r", name="list_files", args="{}")], [text_chunk("ok")]])
    for i in range(80):
        (tmp_path / f"datei-mit-langem-namen-{i}.txt").write_text("x")
    j.ask("Welche Dateien?", lambda t: None, lambda s: None)
    tool_msg = next(m for m in j.history if m["role"] == "tool")
    assert len(tool_msg["content"]) <= 1220 and tool_msg["content"].endswith("[…gekürzt]")


def test_search_web(tmp_path):
    answer = NS(choices=[NS(message=NS(content="Heute scheint die Sonne."))])
    j = jarvis(tmp_path, [[tool_chunk(0, id="s", name="search_web", args='{"query": "Wetter"}')], answer,
                          [text_chunk("Sonnig, Sir.")]])
    out = []
    j.ask("Wetter?", out.append, lambda s: None)
    assert out == ["Sonnig, Sir."] and j.history[2]["content"] == "Heute scheint die Sonne."
    assert j.client.chat.completions.calls[1]["model"] == "groq/compound-mini"


def test_history_is_trimmed(tmp_path):
    j = jarvis(tmp_path, [[text_chunk("x")]])
    for i in range(30):
        j.history.append({"role": "user" if i % 2 == 0 else "assistant", "content": str(i)})
    j.ask("neu", lambda t: None, lambda s: None)
    msgs = j.client.chat.completions.calls[0]["messages"]
    assert len(msgs) <= 13 and msgs[1]["role"] == "user" and msgs[-1]["content"] == "neu"


# ---------------------------------------------------------------- Server
def client(monkeypatch, tmp_path, password="pw123"):
    monkeypatch.setattr(server.cfg, "web_password", password)
    monkeypatch.setattr(server.cfg, "output_dir", tmp_path)
    monkeypatch.setattr(server.cfg, "groq_api_key", "gsk_fake")
    monkeypatch.setattr(server.cfg, "mail_address", None)
    monkeypatch.setattr(server.cfg, "ms_client_id", None)

    def fake_ask(self, text, on_text, on_status, on_event=None, mailbox=None, uploads=None, agent=None):
        on_text("Antwort: " + text + "".join(f" [{u.kind}:{u.path}]" for u in uploads or []))
        (tmp_path / "f.txt").write_text("x")
        on_event({"type": "file", "name": "f.txt", "url": "/files/f.txt"})

    monkeypatch.setattr(Jarvis, "ask", fake_ask)
    monkeypatch.setattr(server, "_extract_memory", lambda q, a: None)
    server._sessions.clear()
    return TestClient(server.app)


def test_server_login_and_chat(monkeypatch, tmp_path):
    c = client(monkeypatch, tmp_path)
    assert c.get("/api/info").json()["needs_password"] is True
    assert c.post("/api/login", json={"password": "falsch"}).status_code == 401
    assert c.post("/api/login", json={"password": "pw123"}).status_code == 200
    assert c.post("/api/chat", json={"session": "abcdefgh1", "text": "Hi"}).status_code == 401
    r = c.post("/api/chat", json={"session": "abcdefgh1", "text": "Hi"}, headers={"X-Jarvis-Key": "pw123"})
    evs = [json.loads(l) for l in r.text.splitlines() if l]
    assert [e["type"] for e in evs] == ["agent", "text", "file", "done"]
    assert c.get("/files/f.txt").status_code == 401
    assert c.get("/files/f.txt?key=pw123").text == "x"
    assert c.get("/files/%2e%2e/%2e%2e/etc/passwd?key=pw123").status_code == 404
    assert "JARVIS" in c.get("/").text


def test_mail_status(monkeypatch, tmp_path):
    c = client(monkeypatch, tmp_path, password="")
    s = c.get("/api/mail/status?session=abcdefgh1").json()
    assert s["connected"] is False and s["outlook_available"] is False
    monkeypatch.setattr(server.cfg, "mail_address", "ich@gmail.com")
    monkeypatch.setattr(server.cfg, "mail_password", "pw")
    s = c.get("/api/mail/status?session=abcdefgh1").json()
    assert s["connected"] and s["provider"] == "imap" and s["address"] == "ich@gmail.com"
    assert c.post("/api/mail/outlook/start", json={"session": "abcdefgh1"}).status_code == 400


def test_mail_status_shows_setup_error_and_test(monkeypatch, tmp_path):
    c = client(monkeypatch, tmp_path, password="")
    monkeypatch.setattr(server.cfg, "mail_address", "medin")  # ohne @icloud.com
    monkeypatch.setattr(server.cfg, "mail_password", "pw")
    s = c.get("/api/mail/status?session=abcdefgh2").json()
    assert s["connected"] is False and "ganze E-Mail-Adresse" in s["error"]
    r = c.post("/api/mail/test", json={"session": "abcdefgh2"}).json()
    assert r["ok"] is False and "ganze E-Mail-Adresse" in r["message"]

    class OkBox:
        address = "medin@icloud.com"

        def test(self):
            return "Verbunden – Postfach medin@icloud.com ist erreichbar."

    monkeypatch.setattr(server, "imap_mailbox", lambda: OkBox())
    r = c.post("/api/mail/test", json={"session": "abcdefgh2"}).json()
    assert r["ok"] is True and "erreichbar" in r["message"]


# ---------------------------------------------------------------- Anhänge
def _png_data_url():
    import base64
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (0, 120, 255)).save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def test_image_goes_to_vision_model_then_compacted(tmp_path):
    from jarvis.uploads import save_attachment

    up = save_attachment(tmp_path, "foto.png", _png_data_url())
    j = jarvis(tmp_path, [[text_chunk("Ein blaues Bild.")], [text_chunk("Ja.")]])
    j.client.models = NS(list=lambda: NS(data=[NS(id="llama-3.3-70b-versatile"),
                                               NS(id="meta-llama/llama-4-scout-17b-16e-instruct")]))
    out = []
    j.ask("Was ist das?", out.append, lambda s: None, uploads=[up])
    call = j.client.chat.completions.calls[0]
    assert call["model"] == "meta-llama/llama-4-scout-17b-16e-instruct"
    content = call["messages"][-1]["content"]
    assert content[1]["type"] == "image_url" and content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert "uploads/foto.jpg" in content[0]["text"]
    assert j.history[0]["content"] == "Was ist das?\n\n[Bild angehängt: uploads/foto.jpg]"
    j.ask("Danke", out.append, lambda s: None)
    assert j.client.chat.completions.calls[1]["model"] == "openai/gpt-oss-120b"  # Text wieder mit normalem Modell


def test_document_text_in_prompt(tmp_path):
    import base64

    from jarvis.uploads import save_attachment

    data = "data:text/plain;base64," + base64.b64encode("Geheimrezept: Pizza".encode()).decode()
    up = save_attachment(tmp_path, "rezept.txt", data)
    j = jarvis(tmp_path, [[text_chunk("Pizza.")]])
    j.ask("Was steht drin?", lambda t: None, lambda s: None, uploads=[up])
    msg = j.client.chat.completions.calls[0]["messages"][-1]["content"]
    assert isinstance(msg, str) and "Geheimrezept: Pizza" in msg and "uploads/rezept.txt" in msg


def test_chat_endpoint_with_attachment(monkeypatch, tmp_path):
    c = client(monkeypatch, tmp_path, password="")
    r = c.post("/api/chat", json={"session": "abcdefgh3", "text": "", "attachments": [{"name": "a.png", "type": "image/png", "data": _png_data_url()}]})
    evs = [json.loads(l) for l in r.text.splitlines() if l and json.loads(l)["type"] != "agent"]
    assert "Was siehst du" in evs[0]["text"] and "[image:uploads/a.jpg]" in evs[0]["text"]
    r = c.post("/api/chat", json={"session": "abcdefgh3", "text": "x", "attachments": [{"name": "virus.exe", "data": "data:application/octet-stream;base64,AAAA"}]})
    evs = [json.loads(l) for l in r.text.splitlines() if l and json.loads(l)["type"] != "agent"]
    assert evs[0]["type"] == "error" and "nicht unterstützt" in evs[0]["text"]


def test_dashboard_endpoints(monkeypatch, tmp_path):
    c = client(monkeypatch, tmp_path, password="pw123")
    (tmp_path / "uploads").mkdir()
    (tmp_path / "uploads" / "foto.jpg").write_bytes(b"x")
    (tmp_path / "Bericht.pdf").write_bytes(b"%PDF")
    assert c.get("/api/files").status_code == 401
    files = c.get("/api/files", headers={"X-Jarvis-Key": "pw123"}).json()
    assert {f["path"] for f in files} == {"uploads/foto.jpg", "Bericht.pdf"}
    assert next(f for f in files if f["name"] == "foto.jpg")["upload"] is True
    sysinfo = c.get("/api/system", headers={"X-Jarvis-Key": "pw123"}).json()
    assert 0 <= sysinfo["disk"] <= 100 and "cpu" in sysinfo and "ram" in sysinfo


def test_delete_files(monkeypatch, tmp_path):
    c = client(monkeypatch, tmp_path, password="pw123")
    h = {"X-Jarvis-Key": "pw123"}
    (tmp_path / "uploads").mkdir()
    (tmp_path / "uploads" / "a.jpg").write_bytes(b"x")
    (tmp_path / "b.pdf").write_bytes(b"x")
    (tmp_path.parent / "geheim.txt").write_text("nicht löschen")
    assert c.delete("/api/files", params={"path": "b.pdf"}).status_code == 401
    assert c.delete("/api/files", params={"path": "b.pdf"}, headers=h).json() == {"ok": True}
    assert not (tmp_path / "b.pdf").exists()
    assert c.delete("/api/files", params={"path": "b.pdf"}, headers=h).status_code == 404
    assert c.delete("/api/files", params={"path": "../geheim.txt"}, headers=h).status_code == 404
    assert (tmp_path.parent / "geheim.txt").exists()
    assert c.post("/api/files/delete-all", headers=h).json() == {"ok": True, "deleted": 1}
    assert list(tmp_path.iterdir()) == []
