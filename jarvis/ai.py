"""Das Gehirn von JARVIS: Groq (kostenlos) mit Werkzeugen und Streaming."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Callable

import groq
from groq import Groq

from .config import Config
from .persona import system_prompt
from .tools import ToolContext, ToolError, describe, execute, tool_definitions

log = logging.getLogger("jarvis")

OnText = Callable[[str], None]
OnStatus = Callable[[str], None]

# Ist ein Modell nicht verfügbar oder sein Gratis-Kontingent leer, nimmt JARVIS das nächste.
DEFAULT_MODELS = ["openai/gpt-oss-120b", "llama-3.3-70b-versatile", "openai/gpt-oss-20b", "llama-3.1-8b-instant"]
SEARCH_MODELS = ["groq/compound-mini", "groq/compound"]
MAX_STEPS = 15
HISTORY_LIMIT = 16  # so bleibt jede Anfrage im kostenlosen Token-Limit


def short_error(message: str, limit: int = 160) -> str:
    text = re.sub(r"gsk_[\w-]+", "***", str(message or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


class _Retry(Exception):
    """Interner Hinweis: Anfrage mit angepassten Einstellungen wiederholen."""


class Jarvis:
    """Ein Gespräch (eine Browser-Sitzung)."""

    def __init__(self, cfg: Config, client: Groq | None = None):
        self.cfg = cfg
        self.client = client or Groq(api_key=cfg.groq_api_key or "missing", max_retries=1, timeout=60)
        self.models = [cfg.groq_model] if cfg.groq_model else list(DEFAULT_MODELS)
        self.history: list[dict] = []
        self.known_addresses: set[str] = set()
        self.tools_ok = True

    @property
    def model(self) -> str:
        return self.models[0]

    def reset(self) -> None:
        self.history.clear()

    # ------------------------------------------------------------------ öffentlich
    def ask(self, text: str, on_text: OnText, on_status: OnStatus, on_event=None, mailbox=None) -> None:
        if not self.cfg.groq_api_key:
            on_text("Mir fehlt noch der kostenlose GROQ_API_KEY. Bitte bei Render unter Environment eintragen.")
            return
        ctx = ToolContext(output_dir=self.cfg.output_dir, mailbox=mailbox, user_message=text,
                          known_addresses=self.known_addresses, on_event=on_event)
        checkpoint = len(self.history)
        self.history.append({"role": "user", "content": text})
        try:
            self._run(ctx, on_text, on_status)
        except groq.APIError as e:
            log.error("Groq-Fehler (%s): %s", self.model, short_error(str(e), 500))
            del self.history[checkpoint:]
            on_text(self._explain(e))
        except Exception as e:  # noqa: BLE001
            log.exception("Unerwarteter Fehler")
            del self.history[checkpoint:]
            on_text(f"Da ist etwas schiefgelaufen ({type(e).__name__}: {short_error(str(e), 100)}). Bitte noch einmal versuchen.")

    # ------------------------------------------------------------------ Anfrage bauen
    def _messages(self, ctx: ToolContext) -> list[dict]:
        recent = self.history[-HISTORY_LIMIT:]
        while recent and recent[0]["role"] != "user":  # nie mit einem halben Werkzeug-Block beginnen
            recent = recent[1:]
        return [{"role": "system", "content": system_prompt(self.cfg, ctx.mailbox is not None)}, *recent]

    def _tools(self, ctx: ToolContext) -> list[dict] | None:
        if not self.tools_ok:
            return None
        return [{"type": "function", "function": {
            "name": t["name"], "description": t["description"],
            "parameters": t["parameters"] or {"type": "object", "properties": {}},
        }} for t in tool_definitions(ctx.mailbox is not None)]

    # ------------------------------------------------------------------ Fehler
    def _explain(self, e: groq.APIError) -> str:
        msg = str(getattr(e, "message", "") or e)
        if isinstance(e, groq.AuthenticationError):
            return "Mein GROQ_API_KEY wird abgelehnt. Bitte den Key bei Render prüfen (er beginnt mit gsk_)."
        if isinstance(e, groq.RateLimitError):
            return "Das kostenlose Kontingent ist gerade ausgeschöpft. Bitte eine Minute warten und es erneut versuchen."
        if isinstance(e, groq.PermissionDeniedError):
            return f"Groq verweigert den Zugriff: {short_error(msg)}"
        if isinstance(e, groq.APIConnectionError):
            return "Ich kann die Groq-Server gerade nicht erreichen. Bitte gleich noch einmal versuchen."
        if isinstance(e, groq.InternalServerError):
            return "Die Groq-Server sind gerade überlastet. Bitte gleich noch einmal versuchen."
        code = getattr(e, "status_code", "?")
        return f"Fehler von Groq (Statuscode {code}): {short_error(msg)}"

    def _handle(self, e: groq.APIError, state: dict) -> None:
        """Entscheidet, ob und wie eine fehlgeschlagene Anfrage wiederholt wird (sonst: weiterwerfen)."""
        low = str(getattr(e, "message", "") or e).lower()
        status = getattr(e, "status_code", None)
        if isinstance(e, groq.InternalServerError) and not state.get("waited"):
            state["waited"] = True
            time.sleep(2)
            raise _Retry
        model_gone = status == 404 or "decommissioned" in low or "does not exist" in low or "model_not_found" in low
        if (isinstance(e, groq.RateLimitError) or model_gone) and len(self.models) > 1:
            log.warning("Modell %s nicht nutzbar (%s) - nehme %s", self.model, status, self.models[1])
            self.models.pop(0)
            raise _Retry
        if status == 413 or "too large" in low or "context_length" in low or "reduce the length" in low:
            if not state.get("trimmed"):
                state["trimmed"] = True
                self.history[:] = self.history[-1:] if self.history[-1]["role"] == "user" else self.history[-2:]
                raise _Retry
        if "tool_use_failed" in low or "failed_generation" in low or "tool call validation" in low:
            tries = state.get("tool_fail", 0) + 1
            state["tool_fail"] = tries
            if tries == 1:
                raise _Retry
            if tries == 2 and len(self.models) > 1:
                self.models.pop(0)
                raise _Retry
            if self.tools_ok:
                log.warning("Werkzeug-Aufrufe scheitern - antworte ohne Werkzeuge")
                self.tools_ok = False
                raise _Retry
        raise e

    # ------------------------------------------------------------------ Schleife
    def _turn(self, ctx: ToolContext, on_text: OnText) -> tuple[str, list[dict], bool]:
        """Eine Anfrage, live gestreamt. Rückgabe: (Text, Werkzeug-Aufrufe, schon etwas ausgegeben?)"""
        kwargs = {"model": self.model, "messages": self._messages(ctx), "stream": True, "temperature": 0.6}
        tools = self._tools(ctx)
        if tools:
            kwargs.update(tools=tools, tool_choice="auto")
        text, calls, emitted = "", {}, False
        for chunk in self.client.chat.completions.create(**kwargs):
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                text += delta.content
                emitted = True
                on_text(delta.content)
            for tc in delta.tool_calls or []:
                slot = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                if tc.id:
                    slot["id"] = tc.id
                if tc.function and tc.function.name:
                    slot["name"] += tc.function.name
                if tc.function and tc.function.arguments:
                    slot["arguments"] += tc.function.arguments
        return text, [calls[i] for i in sorted(calls)], emitted

    def _run(self, ctx: ToolContext, on_text: OnText, on_status: OnStatus) -> None:
        state: dict = {}
        steps = 0
        while steps < MAX_STEPS:
            try:
                text, calls, _ = self._turn(ctx, on_text)
            except groq.APIError as e:
                try:
                    self._handle(e, state)
                except _Retry:
                    continue
            steps += 1
            if not calls:
                if not text.strip():
                    on_text("Ich habe gerade keine Antwort erhalten. Bitte noch einmal versuchen.")
                    self.history.pop()
                    return
                self.history.append({"role": "assistant", "content": text})
                return
            for i, c in enumerate(calls):
                c["id"] = c["id"] or f"call_{steps}_{i}"
            self.history.append({"role": "assistant", "content": text or None, "tool_calls": [
                {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}}
                for c in calls]})
            for c in calls:
                self.history.append({"role": "tool", "tool_call_id": c["id"], "content": self._call(c, ctx, on_status)})
        on_text(" Ich habe die maximale Anzahl an Arbeitsschritten erreicht.")

    def _call(self, call: dict, ctx: ToolContext, on_status: OnStatus) -> str:
        name = call["name"]
        try:
            args = json.loads(call["arguments"] or "{}")
            if not isinstance(args, dict):
                raise ValueError("kein Objekt")
        except ValueError:
            return "Fehler: Die Werkzeug-Eingabe war kein gültiges JSON. Bitte erneut versuchen."
        on_status(describe(name, args))
        try:
            if name == "search_web":
                return self._search(str(args.get("query", "")))
            return execute(name, args, ctx)
        except ToolError as e:
            return f"Fehler: {e}"
        except Exception as e:  # noqa: BLE001
            log.exception("Werkzeug %s fehlgeschlagen", name)
            return f"Fehler: {type(e).__name__}: {e}"

    def _search(self, query: str) -> str:
        prompt = f"Suche im Internet und fasse die wichtigsten aktuellen Fakten kurz auf Deutsch zusammen: {query}"
        last = None
        for model in SEARCH_MODELS:
            try:
                res = self.client.chat.completions.create(model=model, messages=[{"role": "user", "content": prompt}])
                return res.choices[0].message.content or "Keine Ergebnisse gefunden."
            except groq.APIError as e:
                last = e
                log.warning("Websuche mit %s fehlgeschlagen: %s", model, short_error(str(e), 300))
        return f"Fehler: Die Internetsuche ist gerade nicht verfügbar ({short_error(str(last), 100)}). Antworte aus deinem Wissen und sage, dass es nicht ganz aktuell sein könnte."
