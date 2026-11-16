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


def test_gemini_model_fallback_and_rate_limit(tmp_path):
    not_found = errors.ClientError(404, {"error": {"code": 404, "message": "model not found", "status": "NOT_FOUND"}})
    limited = errors.ClientError(429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}})
    brain = _brain(tmp_path, [not_found, [_chunk(types.Part(text="Hallo."))], limited])
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
