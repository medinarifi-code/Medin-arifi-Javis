import anthropic
import httpx

from jarvis import brain as brain_mod
from jarvis.brain import FallbackBrain, create_brain
from jarvis.config import load_config


def _status_error(cls, code, message):
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx.Response(code, request=req, json={"type": "error", "error": {"message": message}})
    return cls(message, response=resp, body=None)


class FakeGemini:
    instances = []

    def __init__(self, cfg, web=False):
        self.asked = []
        FakeGemini.instances.append(self)

    def ask(self, text, on_text, on_status, on_event=None):
        self.asked.append(text)
        on_text("Antwort von Gemini.")

    def reset(self):
        pass


def _cfg(tmp_path, gemini=True):
    cfg = load_config()
    cfg.output_dir = tmp_path
    cfg.anthropic_api_key = "sk-ant-test"
    cfg.gemini_api_key = "AIzaTest" if gemini else None
    cfg.provider_setting = "auto"
    return cfg


def _fail_claude(monkeypatch, error):
    def boom(self, on_text, on_status):
        raise error
    monkeypatch.setattr(brain_mod.Brain, "_run", boom)
    monkeypatch.setattr("jarvis.brain_gemini.GeminiBrain", FakeGemini)


def test_no_credit_switches_to_gemini(tmp_path, monkeypatch):
    _fail_claude(monkeypatch, _status_error(anthropic.BadRequestError, 400, "Your credit balance is too low to access the Anthropic API."))
    FakeGemini.instances.clear()
    b = create_brain(_cfg(tmp_path))
    assert isinstance(b, FallbackBrain)
    out, status = [], []
    b.ask("Hallo", out.append, status.append)
    assert out == ["Antwort von Gemini."]
    assert any("Gemini" in s for s in status)
    b.ask("Noch was", out.append, status.append)
    assert FakeGemini.instances[0].asked == ["Hallo", "Noch was"]


def test_no_credit_without_gemini_explains(tmp_path, monkeypatch):
    _fail_claude(monkeypatch, _status_error(anthropic.BadRequestError, 400, "Your credit balance is too low."))
    b = create_brain(_cfg(tmp_path, gemini=False))
    out = []
    b.ask("Hallo", out.append, lambda s: None)
    assert "kein Guthaben" in out[0] and "GEMINI_API_KEY" in out[0]


def test_other_400_shows_reason(tmp_path, monkeypatch):
    _fail_claude(monkeypatch, _status_error(anthropic.BadRequestError, 400, "messages: unexpected field"))
    b = create_brain(_cfg(tmp_path, gemini=False))
    out = []
    b.ask("Hallo", out.append, lambda s: None)
    assert "Statuscode 400" in out[0] and "unexpected field" in out[0]
