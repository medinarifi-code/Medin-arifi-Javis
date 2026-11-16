"""Das Gehirn von JARVIS: Google Gemini (kostenlos) mit Werkzeugen und Streaming."""

from __future__ import annotations

import itertools
import logging
import re
import time
from typing import Callable

from google import genai
from google.genai import errors, types

from .config import Config
from .persona import system_prompt
from .tools import ToolContext, ToolError, describe, execute, tool_definitions

log = logging.getLogger("jarvis")

OnText = Callable[[str], None]
OnStatus = Callable[[str], None]

# Ist ein Modell nicht verfügbar (oder Kontingent leer), wird automatisch das nächste probiert.
DEFAULT_MODELS = ["gemini-flash-latest", "gemini-2.5-flash", "gemini-2.5-flash-lite"]
MAX_STEPS = 20


def short_error(message: str, limit: int = 160) -> str:
    text = re.sub(r"AIza[\w-]+", "***", str(message or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


class Jarvis:
    """Ein Gespräch (eine Browser-Sitzung)."""

    def __init__(self, cfg: Config, client: genai.Client | None = None):
        self.cfg = cfg
        self.client = client or genai.Client(api_key=cfg.gemini_api_key)
        self.models = [cfg.gemini_model] if cfg.gemini_model else list(DEFAULT_MODELS)
        self.history: list[types.Content] = []
        self.known_addresses: set[str] = set()
        self.tools_ok = True

    @property
    def model(self) -> str:
        return self.models[0]

    def reset(self) -> None:
        self.history.clear()

    # ------------------------------------------------------------------ öffentlich
    def ask(self, text: str, on_text: OnText, on_status: OnStatus, on_event=None, mailbox=None) -> None:
        if not self.cfg.gemini_api_key:
            on_text("Mir fehlt noch der kostenlose GEMINI_API_KEY. Bitte bei Render unter Environment eintragen.")
            return
        ctx = ToolContext(output_dir=self.cfg.output_dir, mailbox=mailbox, user_message=text,
                          known_addresses=self.known_addresses, on_event=on_event)
        checkpoint = len(self.history)
        self.history.append(types.Content(role="user", parts=[types.Part(text=text)]))
        try:
            self._run(ctx, on_text, on_status)
        except errors.APIError as e:
            log.error("Gemini-Fehler %s (%s): %s", e.code, self.model, getattr(e, "message", e))
            del self.history[checkpoint:]
            on_text(self._explain(e))
        except Exception as e:  # noqa: BLE001
            log.exception("Unerwarteter Fehler")
            del self.history[checkpoint:]
            on_text(f"Da ist etwas schiefgelaufen ({type(e).__name__}: {short_error(str(e), 100)}). Bitte noch einmal versuchen.")

    # ------------------------------------------------------------------ intern
    def _config(self, ctx: ToolContext) -> types.GenerateContentConfig:
        connected = ctx.mailbox is not None
        decls = []
        for t in tool_definitions(connected):
            if t["parameters"]:
                decls.append(types.FunctionDeclaration(name=t["name"], description=t["description"],
                                                       parameters_json_schema=t["parameters"]))
            else:
                decls.append(types.FunctionDeclaration(name=t["name"], description=t["description"]))
        return types.GenerateContentConfig(
            system_instruction=system_prompt(self.cfg, connected),
            tools=[types.Tool(function_declarations=decls)] if self.tools_ok else None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

    def _explain(self, e: errors.APIError) -> str:
        msg = str(getattr(e, "message", "") or e)
        low = msg.lower()
        if e.code == 429:
            return "Das kostenlose Kontingent ist gerade ausgeschöpft. Bitte eine Minute warten und es erneut versuchen."
        if "location" in low and "not supported" in low:
            return "Google bietet die kostenlose KI in dieser Region nicht an. Bitte JARVIS über den Render-Link benutzen."
        if e.code in (400, 401, 403) and ("api key" in low or "api_key" in low or "permission" in low):
            return "Mein GEMINI_API_KEY wird abgelehnt. Bitte den Key bei Render prüfen."
        if e.code in (500, 502, 503, 504):
            return "Die Google-Server sind gerade überlastet. Bitte gleich noch einmal versuchen."
        return f"Fehler von Google (Statuscode {e.code}): {short_error(msg)}"

    def _open_stream(self, ctx: ToolContext):
        """Startet eine Anfrage: Modellwechsel bei 404/429, einmal warten bei Überlastung,
        Reparatur bei abgelehnten Werkzeugen bzw. kaputtem Verlauf."""
        waited, repaired = False, set()
        while True:
            try:
                stream = self.client.models.generate_content_stream(
                    model=self.model, contents=self.history, config=self._config(ctx))
                return next(stream, None), stream
            except errors.APIError as e:
                low = str(getattr(e, "message", "") or e).lower()
                if e.code in (429, 500, 503) and not waited:
                    waited = True
                    time.sleep(2)
                    continue
                if e.code in (404, 429) and len(self.models) > 1:
                    log.warning("Modell %s nicht nutzbar (%s) - nehme %s", self.model, e.code, self.models[1])
                    self.models.pop(0)
                    waited = False
                    continue
                if e.code == 400 and "history" not in repaired and len(self.history) > 1 and any(
                        w in low for w in ("thought_signature", "thought signature", "function call turn",
                                           "function response turn", "comes immediately after")):
                    log.warning("Verlauf abgelehnt - neu beginnen: %s", low[:200])
                    self.history[:] = [self.history[-1]]
                    repaired.add("history")
                    continue
                if e.code == 400 and self.tools_ok and any(w in low for w in ("function", "schema", "tool", "parameter")):
                    log.warning("Werkzeuge abgelehnt - ohne Werkzeuge weiter: %s", low[:200])
                    self.tools_ok = False
                    continue
                raise

    def _run(self, ctx: ToolContext, on_text: OnText, on_status: OnStatus) -> None:
        for _ in range(MAX_STEPS):
            first, stream = self._open_stream(ctx)
            parts: list[types.Part] = []
            calls: list[types.FunctionCall] = []
            finish = None
            for chunk in itertools.chain([first] if first is not None else [], stream):  # live streamen
                cand = chunk.candidates[0] if chunk.candidates else None
                if cand is None:
                    continue
                finish = cand.finish_reason or finish
                for part in (cand.content.parts if cand.content and cand.content.parts else []):
                    parts.append(part)
                    if part.thought:
                        continue
                    if part.text:
                        on_text(part.text)
                    if part.function_call:
                        calls.append(part.function_call)

            if not parts:
                reason = str(finish or "")
                on_text("Darauf kann ich leider nicht antworten." if any(w in reason for w in ("SAFETY", "PROHIBITED", "BLOCK"))
                        else "Ich habe gerade keine Antwort erhalten. Bitte noch einmal versuchen.")
                self.history.pop()
                return
            self.history.append(types.Content(role="model", parts=parts))
            if not calls:
                return
            self.history.append(types.Content(role="user", parts=[self._call(c, ctx, on_status) for c in calls]))
        on_text(" Ich habe die maximale Anzahl an Arbeitsschritten erreicht.")

    def _call(self, call: types.FunctionCall, ctx: ToolContext, on_status: OnStatus) -> types.Part:
        name, args = call.name or "", dict(call.args or {})
        on_status(describe(name, args))
        try:
            if name == "search_web":
                payload = {"result": self._search(str(args.get("query", "")))}
            else:
                payload = {"result": execute(name, args, ctx)}
        except ToolError as e:
            payload = {"error": str(e)}
        except Exception as e:  # noqa: BLE001
            log.exception("Werkzeug %s fehlgeschlagen", name)
            payload = {"error": f"{type(e).__name__}: {e}"}
        return types.Part(function_response=types.FunctionResponse(id=call.id, name=name, response=payload))

    def _search(self, query: str) -> str:
        res = self.client.models.generate_content(
            model=self.model,
            contents=f"Recherchiere im Internet und fasse die wichtigsten aktuellen Fakten auf Deutsch zusammen: {query}",
            config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())]),
        )
        return res.text or "Keine Ergebnisse gefunden."

