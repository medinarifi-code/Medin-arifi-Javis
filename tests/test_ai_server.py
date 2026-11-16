import json

from fastapi.testclient import TestClient
from google.genai import errors, types

from jarvis import server
from jarvis.ai import Jarvis
from jarvis.config import load_config


def chunk(*parts):
    return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model", parts=list(parts)))])


def err(code, msg):
    cls = errors.ClientError if code < 500 else errors.ServerError
    return cls(code, {"error": {"code": code, "message": msg, "status": "X"}})


class FakeModels:
    def __init__(self, turns):
        self.turns, self.configs = list(turns), []

    def generate_content_stream(self, model, contents, config):
        self.configs.append(config)
        t = self.turns.pop(0)
        if isinstance(t, Exception):
            raise t
        return iter(t)


def jarvis(tmp_path, turns):
    cfg = load_config()
    cfg.output_dir, cfg.gemini_api_key, cfg.gemini_model = tmp_path, "AIzaFake", None
    j = Jarvis(cfg, client=type("C", (), {"models": FakeModels(turns)})())
    return j


def test_tool_loop_creates_file(tmp_path):
    call = types.Part(function_call=types.FunctionCall(id="c1", name="create_file", args={"filename": "n.txt", "content": "hi"}))
    j = jarvis(tmp_path, [[chunk(types.Part(text="Moment. ")), chunk(call)], [chunk(types.Part(text="Fertig."))]])
    out, events = [], []
    j.ask("Datei", out.append, lambda s: out.append(f"[{s}]"), events.append)
    assert out == ["Moment. ", "[Erstelle Datei n.txt]", "Fertig."]
    assert events == [{"type": "file", "name": "n.txt", "url": "/files/n.txt"}]
    assert [c.role for c in j.history] == ["user", "model", "user", "model"]


def test_mail_tools_only_when_connected(tmp_path):
    j = jarvis(tmp_path, [[chunk(types.Part(text="a"))], [chunk(types.Part(text="b"))]])
    j.ask("x", lambda t: None, lambda s: None)
    names = [d.name for d in j.client.models.configs[0].tools[0].function_declarations]
    assert "list_emails" not in names
    j.ask("y", lambda t: None, lambda s: None, mailbox=object())
    names = [d.name for d in j.client.models.configs[1].tools[0].function_declarations]
    assert "send_email" in names


def test_model_fallback_and_errors(tmp_path, monkeypatch):
    monkeypatch.setattr("jarvis.ai.time.sleep", lambda s: None)
    j = jarvis(tmp_path, [err(429, "quota"), err(429, "quota"), [chunk(types.Part(text="Hallo"))],
                          err(400, "weird thing with AIzaSECRET")])
    out = []
    j.ask("hi", out.append, lambda s: None)
    assert out == ["Hallo"] and j.model == "gemini-2.5-flash"
    j.ask("hi2", out.append, lambda s: None)
    assert "Statuscode 400" in out[-1] and "SECRET" not in out[-1] and len(j.history) == 2


def test_rejected_tools_retry(tmp_path):
    j = jarvis(tmp_path, [err(400, "Invalid function_declarations[2].parameters"), [chunk(types.Part(text="ok"))]])
    out = []
    j.ask("hi", out.append, lambda s: None)
    assert out == ["ok"] and j.tools_ok is False


# ---------------------------------------------------------------- Server
def client(monkeypatch, tmp_path, password="pw123"):
    monkeypatch.setattr(server.cfg, "web_password", password)
    monkeypatch.setattr(server.cfg, "output_dir", tmp_path)
    monkeypatch.setattr(server.cfg, "gemini_api_key", "AIzaFake")
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
