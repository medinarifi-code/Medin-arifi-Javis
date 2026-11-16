from google.genai import errors, types

from jarvis.brain import create_brain
from jarvis.brain_gemini import GeminiBrain
from jarvis.config import load_config


def _chunk(*parts):
    return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model", parts=list(parts)))])


class FakeModels:
    def __init__(self, turns):
        self.turns = turns
        self.calls = []

    def generate_content_stream(self, model, contents, config):
        self.calls.append((model, list(contents)))
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        return iter(turn)


def _brain(tmp_path, turns):
    cfg = load_config()
    cfg.output_dir = tmp_path
    cfg.anthropic_api_key = None
    cfg.gemini_api_key = "fake"
    cfg.provider_setting = "auto"
    brain = create_brain(cfg, web=True)
    assert isinstance(brain, GeminiBrain)
    brain.client = type("C", (), {"models": FakeModels(turns)})()
    return brain


def test_gemini_tool_loop(tmp_path):
    call = types.Part(function_call=types.FunctionCall(id="c1", name="create_file",
                                                       args={"filename": "a.txt", "content": "hallo"}))
    brain = _brain(tmp_path, [
        [_chunk(types.Part(text="Einen Moment, Sir. ")), _chunk(call)],
        [_chunk(types.Part(text="Erledigt."))],
    ])
    out, events = [], []
    brain.ask("Datei bitte", out.append, lambda s: out.append(f"[{s}]"), events.append)
    assert out == ["Einen Moment, Sir. ", "[Erstelle Datei a.txt]", "Erledigt."]
    assert (tmp_path / "a.txt").read_text() == "hallo"
    assert events[0]["type"] == "file" and events[0]["name"] == "a.txt"
    roles = [c.role for c in brain.history]
    assert roles == ["user", "model", "user", "model"]
    assert brain.history[2].parts[0].function_response.response == {"result": f"Datei gespeichert: {tmp_path / 'a.txt'}"}


def test_gemini_model_fallback_and_rate_limit(tmp_path, monkeypatch):
    monkeypatch.setattr("jarvis.brain_gemini.time.sleep", lambda s: None)
    not_found = errors.ClientError(404, {"error": {"code": 404, "message": "model not found", "status": "NOT_FOUND"}})
    limited = errors.ClientError(429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}})
    # letztes Modell: einmal warten + erneut versuchen, dann aufgeben
    brain = _brain(tmp_path, [not_found, [_chunk(types.Part(text="Hallo."))], limited, limited])
    out = []
    brain.ask("hi", out.append, lambda s: None)
    assert out == ["Hallo."]
    assert brain.model == "gemini-2.5-flash"
    brain.ask("nochmal", out.append, lambda s: None)
    assert "Kontingent" in out[-1]
    assert len(brain.history) == 2  # fehlgeschlagene Runde wurde entfernt


def test_function_declarations_build():
    from jarvis.brain_gemini import _declarations

    names = [d.name for d in _declarations()]
    assert "create_pptx" in names and "search_web" in names


def _err(code, message):
    cls = errors.ClientError if code < 500 else errors.ServerError
    return cls(code, {"error": {"code": code, "message": message, "status": "X"}})


def test_quota_on_first_model_switches_model(tmp_path, monkeypatch):
    monkeypatch.setattr("jarvis.brain_gemini.time.sleep", lambda s: None)
    brain = _brain(tmp_path, [_err(429, "quota"), _err(429, "quota"), [_chunk(types.Part(text="Hallo."))]])
    out = []
    brain.ask("hi", out.append, lambda s: None)
    assert out == ["Hallo."] and brain.model == "gemini-2.5-flash"


def test_rejected_tools_retry_without_tools(tmp_path):
    brain = _brain(tmp_path, [_err(400, "Invalid JSON payload: function_declarations[3].parameters"),
                              [_chunk(types.Part(text="Antwort ohne Werkzeuge."))]])
    out = []
    brain.ask("hi", out.append, lambda s: None)
    assert out == ["Antwort ohne Werkzeuge."] and brain.tools_ok is False


def test_broken_history_is_reset(tmp_path):
    brain = _brain(tmp_path, [[_chunk(types.Part(text="Erste."))],
                              _err(400, "Function call is missing a thought_signature"),
                              [_chunk(types.Part(text="Zweite."))]])
    out = []
    brain.ask("eins", out.append, lambda s: None)
    brain.ask("zwei", out.append, lambda s: None)
    assert out == ["Erste.", "Zweite."]
    assert [c.parts[0].text for c in brain.history] == ["zwei", "Zweite."]


def test_unknown_error_shows_reason(tmp_path):
    brain = _brain(tmp_path, [_err(400, "Something odd happened with key AIzaSECRET123")])
    out = []
    brain.ask("hi", out.append, lambda s: None)
    assert "Statuscode 400: Something odd happened" in out[0] and "AIzaSECRET" not in out[0]
    assert brain.history == []


def test_schema_is_sanitized():
    from jarvis.brain_gemini import _declarations

    decls = {d.name: d for d in _declarations()}
    assert decls["get_datetime"].parameters_json_schema is None
    text = str(decls["create_xlsx"].parameters_json_schema)
    assert "additionalProperties" not in text and "null" not in text
