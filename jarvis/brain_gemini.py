"""Kostenloses Gehirn: Google Gemini (kostenloser API-Key von aistudio.google.com).

Gleiche Schnittstelle wie brain.Brain: ask(text, on_text, on_status, on_event) und reset().
"""

from __future__ import annotations

import json
from typing import Any

from google import genai
from google.genai import errors, types

from .brain import MAX_STEPS, OnEvent, OnStatus, OnText
from .config import Config
from .persona import system_prompt
from .tools import CUSTOM_TOOLS, ToolError, ToolResult, describe_call, execute_tool, validate

# Wird ein Modell nicht (mehr) angeboten, versucht JARVIS automatisch das nächste.
DEFAULT_MODELS = ["gemini-flash-latest", "gemini-2.5-flash"]

SEARCH_TOOL = {
    "name": "search_web",
    "description": "Sucht aktuelle Informationen im Internet (Nachrichten, Wetter, Preise, Ereignisse, Fakten) und gibt eine Zusammenfassung mit Quellen zurück.",
    "input_schema": {"type": "object", "properties": {"query": {"type": "string", "description": "Suchanfrage"}},
                     "required": ["query"], "additionalProperties": False},
}


def _declarations() -> list[types.FunctionDeclaration]:
    return [types.FunctionDeclaration(name=t["name"], description=t["description"],
                                      parameters_json_schema=t["input_schema"])
            for t in [*CUSTOM_TOOLS, SEARCH_TOOL]]


class GeminiBrain:
    def __init__(self, cfg: Config, web: bool = False):
        self.cfg = cfg
        self.web = web
        self.client = genai.Client(api_key=cfg.gemini_api_key)
        self.models = [cfg.gemini_model] if cfg.gemini_model else list(DEFAULT_MODELS)
        self.config = types.GenerateContentConfig(
            system_instruction=system_prompt(cfg, web=web),
            tools=[types.Tool(function_declarations=_declarations())],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        self.history: list[types.Content] = []
        self._on_event: OnEvent | None = None

    @property
    def model(self) -> str:
        return self.models[0]

    def reset(self) -> None:
        self.history.clear()

    # ------------------------------------------------------------------
    def ask(self, user_text: str, on_text: OnText, on_status: OnStatus, on_event: OnEvent | None = None) -> None:
        self._on_event = on_event
        checkpoint = len(self.history)
        self.history.append(types.Content(role="user", parts=[types.Part(text=user_text)]))
        try:
            self._run(on_text, on_status)
        except errors.APIError as e:
            del self.history[checkpoint:]
            on_text(self._explain(e))
        except Exception as e:  # noqa: BLE001 - z.B. Netzwerkfehler
            del self.history[checkpoint:]
            on_text(f"Ich kann meine Server gerade nicht erreichen ({type(e).__name__}). Bitte versuchen Sie es gleich noch einmal.")

    def _explain(self, e: errors.APIError) -> str:
        msg = str(getattr(e, "message", "") or e)
        if e.code == 429:
            return "Das kostenlose Kontingent ist gerade ausgeschöpft. Bitte warten Sie eine Minute und versuchen Sie es erneut."
        if e.code in (400, 401, 403) and ("API key" in msg or "PERMISSION" in msg.upper()):
            return "Mein Gemini-Zugangsschlüssel wird abgelehnt. Bitte prüfen Sie den GEMINI_API_KEY."
        if e.code in (500, 503):
            return "Die Server von Google sind gerade überlastet. Bitte versuchen Sie es gleich noch einmal."
        return f"Bei der Verbindung zu meinen Servern ist ein Fehler aufgetreten, Statuscode {e.code}."

    def _stream(self):
        """Startet die Anfrage; probiert bei 'Modell nicht gefunden' das nächste Modell."""
        while True:
            try:
                stream = self.client.models.generate_content_stream(
                    model=self.model, contents=self.history, config=self.config)
                first = next(stream, None)
                return first, stream
            except errors.ClientError as e:
                if e.code == 404 and len(self.models) > 1:
                    self.models.pop(0)
                    continue
                raise

    def _run(self, on_text: OnText, on_status: OnStatus) -> None:
        for _ in range(MAX_STEPS):
            first, stream = self._stream()
            parts: list[types.Part] = []
            calls: list[types.FunctionCall] = []
            finish = None
            chunks = [first] if first is not None else []
            for chunk in _chain(chunks, stream):
                cand = chunk.candidates[0] if chunk.candidates else None
                if cand is None:
                    continue
                finish = cand.finish_reason or finish
                if not cand.content or not cand.content.parts:
                    continue
                for part in cand.content.parts:
                    parts.append(part)
                    if part.thought:
                        continue
                    if part.text:
                        on_text(part.text)
                    if part.function_call:
                        calls.append(part.function_call)

            if not parts:
                reason = str(finish or "")
                if "SAFETY" in reason or "PROHIBITED" in reason or "BLOCK" in reason:
                    on_text("Das kann ich leider nicht übernehmen.")
                else:
                    on_text("Entschuldigung, ich habe gerade keine Antwort erhalten. Bitte versuchen Sie es noch einmal.")
                self.history.pop()  # Nutzerfrage entfernen, damit der Verlauf gültig bleibt
                return

            self.history.append(types.Content(role="model", parts=parts))
            if not calls:
                return
            responses = [self._execute(call, on_status) for call in calls]
            self.history.append(types.Content(role="user", parts=responses))
        on_text(" Ich habe die maximale Anzahl an Arbeitsschritten erreicht.")

    # ------------------------------------------------------------------
    def _execute(self, call: types.FunctionCall, on_status: OnStatus) -> types.Part:
        name = call.name or ""
        args: Any = dict(call.args or {})

        def reply(payload: dict) -> types.Part:
            return types.Part(function_response=types.FunctionResponse(id=call.id, name=name, response=payload))

        if name == "search_web":
            on_status("Durchsuche das Internet")
            try:
                return reply({"result": self._search(str(args.get("query", "")))})
            except Exception as e:  # noqa: BLE001
                return reply({"error": f"Suche fehlgeschlagen: {e}"})

        error = validate(name, args)
        if error:
            return reply({"error": f"Ungültige Eingabe ({error}): {json.dumps(args, ensure_ascii=False)[:500]}"})
        on_status(describe_call(name, args))
        try:
            result = execute_tool(name, args, self.cfg.output_dir, client_side=self.web)
        except ToolError as e:
            return reply({"error": str(e)})
        except Exception as e:  # noqa: BLE001
            return reply({"error": f"{type(e).__name__}: {e}"})
        if self._on_event and isinstance(result, ToolResult):
            if result.file is not None:
                self._on_event({"type": "file", "path": str(result.file), "name": result.file.name,
                                "action": result.action or "created"})
            elif result.url:
                self._on_event({"type": "open", "url": result.url})
        return reply({"result": str(result)})

    def _search(self, query: str) -> str:
        """Eigene Anfrage mit Google-Suche (lässt sich nicht mit Funktionen kombinieren)."""
        res = self.client.models.generate_content(
            model=self.model,
            contents=f"Recherchiere im Internet und fasse die wichtigsten, aktuellen Fakten auf Deutsch zusammen: {query}",
            config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())]),
        )
        text = res.text or "Keine Ergebnisse gefunden."
        sources = []
        try:
            meta = res.candidates[0].grounding_metadata
            for chunk in (meta.grounding_chunks or [])[:5]:
                if chunk.web and chunk.web.title:
                    sources.append(chunk.web.title)
        except (AttributeError, IndexError, TypeError):
            pass
        return text + (f"\n\nQuellen: {', '.join(sources)}" if sources else "")


def _chain(first: list, rest):
    yield from first
    yield from rest
