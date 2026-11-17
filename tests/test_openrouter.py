from types import SimpleNamespace as NS

import groq
import httpx
import openai
import pytest

from jarvis import ai
from jarvis.ai import Jarvis
from jarvis.config import load_config


def text_chunk(t):
    return NS(choices=[NS(delta=NS(content=t, tool_calls=None))])


def gerr(cls, code, msg="x"):
    resp = httpx.Response(code, request=httpx.Request("POST", "https://api.groq.com"), json={"error": {"message": msg}})
    return cls(msg, response=resp, body=None)


def oerr(cls, code, msg="x"):
    resp = httpx.Response(code, request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions"),
                          json={"error": {"message": msg}})
    return cls(msg, response=resp, body=None)


class Fake:
    def __init__(self, name, turns, log):
        self.name, self.turns, self.log = name, list(turns), log
        self.chat = NS(completions=NS(create=self.create))

    def create(self, **kw):
        self.log.append((self.name, kw["model"]))
        t = self.turns.pop(0)
        if isinstance(t, Exception):
            raise t
        return iter(t)


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr("jarvis.ai.time.sleep", lambda s: None)
    ai._catalog.update(time=0.0, text=None, vision=None)
    monkeypatch.setattr(ai, "_fetch_openrouter_catalog", lambda: [
        {"id": "deepseek/deepseek-chat-v3-0324:free", "supported_parameters": ["tools"], "architecture": {"input_modalities": ["text"]}},
        {"id": "some/paid-model", "supported_parameters": ["tools"]},
        {"id": "no/tools:free", "supported_parameters": []},
        {"id": "meta-llama/llama-4-maverick:free", "supported_parameters": ["tools"], "architecture": {"input_modalities": ["text", "image"]}},
    ])


def make(tmp_path, groq_turns=None, router_turns=None, groq_key=True, router_key=True):
    cfg = load_config()
    cfg.output_dir, cfg.groq_model = tmp_path, None
    cfg.groq_api_key = "gsk_x" if groq_key else None
    cfg.openrouter_api_key = "sk-or-x" if router_key else None
    cfg.openrouter_models = []
    log = []
    j = Jarvis(cfg, client=Fake("groq", groq_turns or [], log) if groq_key else None,
               router=Fake("openrouter", router_turns or [], log) if router_key else None)
    return j, log


def test_catalog_only_free_models_with_tools():
    assert ai.openrouter_models() == ["deepseek/deepseek-chat-v3-0324:free", "meta-llama/llama-4-maverick:free"]
    assert ai.openrouter_models(vision=True) == ["meta-llama/llama-4-maverick:free"]
    assert ai.openrouter_models(override=["a:free"]) == ["a:free"]


def test_catalog_fallback_on_error(monkeypatch):
    def broken():
        raise httpx.ConnectError("nope")
    monkeypatch.setattr(ai, "_fetch_openrouter_catalog", broken)
    assert ai.openrouter_models() == ai.OPENROUTER_MODELS


def test_groq_rate_limited_everywhere_then_openrouter(tmp_path):
    rl = [gerr(groq.RateLimitError, 429) for _ in range(5)]
    j, log = make(tmp_path, rl, [[text_chunk("Antwort von OpenRouter")]])
    out = []
    j.ask("hi", out.append, lambda s: None)
    assert out == ["Antwort von OpenRouter"]
    assert [p for p, _ in log] == ["groq"] * 5 + ["openrouter"]
    assert log[-1][1] == "deepseek/deepseek-chat-v3-0324:free"


def test_only_openrouter_key(tmp_path):
    j, log = make(tmp_path, router_turns=[[text_chunk("ok")]], groq_key=False)
    out = []
    j.ask("hi", out.append, lambda s: None)
    assert out == ["ok"] and log == [("openrouter", "deepseek/deepseek-chat-v3-0324:free")]


def test_bad_groq_key_skips_to_openrouter(tmp_path):
    j, log = make(tmp_path, [gerr(groq.AuthenticationError, 401, "Invalid API Key")], [[text_chunk("ok")]])
    out = []
    j.ask("hi", out.append, lambda s: None)
    assert out == ["ok"] and [p for p, _ in log] == ["groq", "openrouter"]


def test_both_keys_bad(tmp_path):
    j, log = make(tmp_path, [gerr(groq.AuthenticationError, 401)], [oerr(openai.AuthenticationError, 401)])
    out = []
    j.ask("hi", out.append, lambda s: None)
    assert "GROQ_API_KEY und OPENROUTER_API_KEY werden abgelehnt" in out[0] and j.history == []


def test_openrouter_rate_limit_tries_next_free_model(tmp_path):
    j, log = make(tmp_path, router_turns=[oerr(openai.RateLimitError, 429), [text_chunk("zweites")]], groq_key=False)
    out = []
    j.ask("hi", out.append, lambda s: None)
    assert out == ["zweites"] and [m for _, m in log] == ["deepseek/deepseek-chat-v3-0324:free", "meta-llama/llama-4-maverick:free"]


def test_search_needs_groq(tmp_path):
    j, _ = make(tmp_path, groq_key=False)
    assert "GROQ_API_KEY" in j._search("Wetter")


def test_no_key_at_all(tmp_path):
    j, _ = make(tmp_path, groq_key=False, router_key=False)
    out = []
    j.ask("hi", out.append, lambda s: None)
    assert "OPENROUTER_API_KEY" in out[0]
