import json

import pytest
from fastapi.testclient import TestClient

from jarvis import web
from jarvis.brain import Brain
from jarvis.tools import execute_tool


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(web.cfg, "output_dir", tmp_path)
    monkeypatch.setattr(web.cfg, "anthropic_api_key", "fake")

    def fake_ask(self, text, on_text, on_status, on_event=None):
        on_text("Sehr wohl. ")
        on_status("Erstelle Datei")
        r = execute_tool("create_file", {"filename": "notiz.txt", "content": text}, self.cfg.output_dir, client_side=True)
        on_event({"type": "file", "path": str(r.file), "name": r.file.name, "action": "created"})

    monkeypatch.setattr(Brain, "ask", fake_ask)
    web._sessions.clear()
    return TestClient(web.app)


def test_chat_streams_events_and_serves_file(client):
    res = client.post("/api/chat", json={"session": "test-session-1", "text": "Hallo JARVIS"})
    events = [json.loads(line) for line in res.text.splitlines() if line]
    assert [e["type"] for e in events] == ["text", "status", "file", "done"]
    assert events[2]["url"] == "/files/notiz.txt"
    got = client.get(events[2]["url"])
    assert got.status_code == 200 and got.text == "Hallo JARVIS"
    assert client.get("/api/files").json()[0]["name"] == "notiz.txt"


def test_file_route_blocks_traversal(client):
    assert client.get("/files/%2e%2e/%2e%2e/etc/passwd").status_code == 404


def test_index_and_status(client):
    assert "J.A.R.V.I.S." in client.get("/").text
    assert client.get("/api/status").json()["brain_ok"] is True


def test_open_website_is_client_side():
    r = execute_tool("open_website", {"url": "youtube.com"}, None, client_side=True)
    assert r.url == "https://youtube.com"
