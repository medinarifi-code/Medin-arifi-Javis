import json
from types import SimpleNamespace as NS

import groq
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


def err(cls, code, msg):
    resp = httpx.Response(code, request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions"),
                          json={"error": {"message": msg}})
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


def test_rate_limit_switches_model_and_errors(tmp_path):
    j = jarvis(tmp_path, [err(groq.RateLimitError, 429, "rate limit"), [text_chunk("Hallo")],
                          err(groq.BadRequestError, 400, "weird thing gsk_SECRET")])
    out = []
    j.ask("hi", out.append, lambda s: None)
    assert out == ["Hallo"] and j.model == "llama-3.3-70b-versatile"
    j.ask("hi2", out.append, lambda s: None)
    assert "Statuscode 400" in out[-1] and "SECRET" not in out[-1] and len(j.history) == 2


def test_bad_key(tmp_path):
    j = jarvis(tmp_path, [err(groq.AuthenticationError, 401, "Invalid API Key")])
    out = []
    j.ask("hi", out.append, lambda s: None)
    assert "GROQ_API_KEY wird abgelehnt" in out[0] and j.history == []


def test_tool_use_failed_recovers(tmp_path):
    fail = lambda: err(groq.BadRequestError, 400, "tool_use_failed: Failed to call a function")  # noqa: E731
    j = jarvis(tmp_path, [fail(), fail(), fail(), [text_chunk("ok")]])
    out = []
    j.ask("hi", out.append, lambda s: None)
    assert out == ["ok"] and j.tools_ok is False
    assert "tools" not in j.client.chat.completions.calls[-1]


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
    assert len(msgs) <= 17 and msgs[1]["role"] == "user" and msgs[-1]["content"] == "neu"


# ---------------------------------------------------------------- Server
def client(monkeypatch, tmp_path, password="pw123"):
    monkeypatch.setattr(server.cfg, "web_password", password)
    monkeypatch.setattr(server.cfg, "output_dir", tmp_path)
    monkeypatch.setattr(server.cfg, "groq_api_key", "gsk_fake")
    monkeypatch.setattr(server.cfg, "mail_address", None)
    monkeypatch.setattr(server.cfg, "ms_client_id", None)

    def fake_ask(self, text, on_text, on_status, on_event=None, mailbox=None):
        on_text("Antwort: " + text)
        (tmp_path / "f.txt").write_text("x")
        on_event({"type": "file", "name": "f.txt", "url": "/files/f.txt"})

    monkeypatch.setattr(Jarvis, "ask", fake_ask)
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
    assert [e["type"] for e in evs] == ["text", "file", "done"]
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
