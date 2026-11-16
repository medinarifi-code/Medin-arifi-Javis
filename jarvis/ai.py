"""Das Gehirn von JARVIS: Groq (kostenlos) mit Werkzeugen und Streaming.

Zuverlässigkeit: Jede Frage probiert bei Problemen der Reihe nach mehrere Groq-Modelle
(Rate-Limit, zu große Anfrage, Modell weg, kaputter Werkzeug-Aufruf, Netzwerkfehler) und
zuletzt eine Antwort ohne Werkzeuge. Erst wenn wirklich nichts geht, kommt eine freundliche Meldung.
"""

from __future__ import annotations

import json
import logging
import os
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

DEFAULT_MODELS = [
    "openai/gpt-oss-120b",
    "llama-3.3-70b-versatile",
    "meta-llama/llama-4-scout-17b-16e-instruct",  # hohes Token-Limit pro Minute
    "openai/gpt-oss-20b",
    "llama-3.1-8b-instant",
]
SEARCH_MODELS = ["groq/compound-mini", "groq/compound"]
VISION_MODELS = ["meta-llama/llama-4-scout-17b-16e-instruct", "meta-llama/llama-4-maverick-17b-128e-instruct"]
VISION_HINTS = ("llama-4", "vision", "-vl", "qwen3.6", "qwen-vl")
MAX_STEPS = 15        # Werkzeug-Runden pro Frage
MAX_ATTEMPTS = 14     # Anfragen an Groq pro Frage (inkl. Wiederholungen)
HISTORY_LIMIT = 12    # Nachrichten, die mitgeschickt werden
TOOL_RESULT_KEEP = 1200  # so lang bleiben alte Werkzeug-Ergebnisse im Verlauf
BUSY_TEXT = "Ich bin gerade etwas überlastet. Bitte versuch es in einer Minute nochmal."


def short_error(message: str, limit: int = 160) -> str:
    text = re.sub(r"gsk_[\w-]+", "***", str(message or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


class _GiveUp(Exception):
    """Alle Wege probiert – Fehler an den Nutzer melden."""


class _NoAnswer(Exception):
    """Kein Modell hat eine Antwort geliefert."""


class Jarvis:
    """Ein Gespräch (eine Browser-Sitzung)."""

    def __init__(self, cfg: Config, client: Groq | None = None):
        self.cfg = cfg
        self.client = client or Groq(api_key=cfg.groq_api_key or "missing", max_retries=0, timeout=60)
        preferred = [cfg.groq_model] if cfg.groq_model else []
        self.models = preferred + [m for m in DEFAULT_MODELS if m not in preferred]
        self.history: list[dict] = []
        self.known_addresses: set[str] = set()
        self.tools_ok = True
        self.vision_models: list[str] | None = None
        self.vision = False  # aktuelle Frage enthält Bilder

    def _candidates(self) -> list[str]:
        if self.vision:
            if self.vision_models is None:
                self.vision_models = self._find_vision_models()
            return self.vision_models
        return self.models

    def _find_vision_models(self) -> list[str]:
        wanted = [m for m in [os.getenv("GROQ_VISION_MODEL")] if m] + VISION_MODELS
        try:
            available = [m.id for m in self.client.models.list().data]
        except Exception as e:  # noqa: BLE001 - Liste ist nur eine Hilfe
            log.info("Modell-Liste nicht abrufbar: %s", e)
            return wanted
        found = [m for m in wanted if m in available]
        found += [m for m in available if any(h in m.lower() for h in VISION_HINTS) and m not in found]
        return found or wanted

    def reset(self) -> None:
        self.history.clear()

    # ------------------------------------------------------------------ öffentlich
    def ask(self, text: str, on_text: OnText, on_status: OnStatus, on_event=None, mailbox=None, uploads=None) -> None:
        if not self.cfg.groq_api_key:
            on_text("Mir fehlt noch der kostenlose GROQ_API_KEY. Bitte bei Render unter Environment eintragen.")
            return
        uploads = uploads or []
        ctx = ToolContext(output_dir=self.cfg.output_dir, mailbox=mailbox, user_message=text,
                          known_addresses=self.known_addresses, on_event=on_event)
        checkpoint = len(self.history)
        self.history.append({"role": "user", "content": self._user_content(text, uploads)})
        self.vision = any(u.kind == "image" for u in uploads)
        self.tools_ok = True
        try:
            self._run(ctx, on_text, on_status)
            if uploads and checkpoint < len(self.history):
                # Verlauf schlank halten: Bilder/lange Texte durch kurze Hinweise ersetzen
                self.history[checkpoint] = {"role": "user", "content": "\n\n".join(
                    [text, *(u.history_part() for u in uploads)])}
            self._shrink_history()
        except _NoAnswer:
            del self.history[checkpoint:]
            on_text(BUSY_TEXT)
        except groq.APIError as e:
            log.error("Groq-Fehler: %s", short_error(str(e), 500))
            del self.history[checkpoint:]
            on_text(self._explain(e))
        except Exception as e:  # noqa: BLE001
            log.exception("Unerwarteter Fehler")
            del self.history[checkpoint:]
            on_text("Da ist gerade etwas schiefgelaufen. Bitte versuch es nochmal.")
        finally:
            self.vision = False

    @staticmethod
    def _user_content(text: str, uploads: list):
        if not uploads:
            return text
        full = "\n\n".join([text, *(u.prompt_part() for u in uploads)])
        images = [u for u in uploads if u.kind == "image"]
        if not images:
            return full
        return [{"type": "text", "text": full},
                *({"type": "image_url", "image_url": {"url": u.data_url}} for u in images)]

    def _shrink_history(self) -> None:
        """Lange Werkzeug-Ergebnisse (gelesene Mails, Dateien …) im Verlauf kürzen – spart Tokens."""
        for msg in self.history:
            content = msg.get("content")
            if msg.get("role") == "tool" and isinstance(content, str) and len(content) > TOOL_RESULT_KEEP:
                msg["content"] = content[:TOOL_RESULT_KEEP] + " […gekürzt]"

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
    @staticmethod
    def _explain(e: groq.APIError) -> str:
        if isinstance(e, groq.AuthenticationError):
            return "Mein GROQ_API_KEY wird abgelehnt. Bitte den Key bei Render prüfen (er beginnt mit gsk_)."
        if isinstance(e, groq.PermissionDeniedError):
            return "Groq verweigert gerade den Zugriff. Bitte prüfe dein Groq-Konto auf console.groq.com."
        if isinstance(e, groq.APIConnectionError):
            return "Ich kann die Groq-Server gerade nicht erreichen. Bitte versuch es gleich nochmal."
        return BUSY_TEXT

    @staticmethod
    def _retry_after(e: groq.APIError) -> float | None:
        try:
            value = e.response.headers.get("retry-after")  # type: ignore[union-attr]
            return float(value) if value is not None else None
        except (AttributeError, TypeError, ValueError):
            return None

    def _next_model(self, state: dict, cands: list[str], reason: str) -> None:
        if state["idx"] + 1 < len(cands):
            log.warning("Modell %s: %s – versuche %s", cands[state["idx"]], reason, cands[state["idx"] + 1])
            state.update(idx=state["idx"] + 1, net=0, waited429=False, tool_retry=False)
            return
        if self.tools_ok:
            # Letzter Ausweg: ohne Werkzeuge, mit kurzem Verlauf, wieder beim ersten Modell
            log.warning("Alle Modelle scheitern (%s) – letzter Versuch ohne Werkzeuge", reason)
            self.tools_ok = False
            self._trim_to_question()
            state.update(idx=0, net=0, waited429=False, tool_retry=False)
            return
        raise _GiveUp

    def _trim_to_question(self) -> None:
        for i in range(len(self.history) - 1, -1, -1):
            if self.history[i]["role"] == "user":
                self.history[:] = self.history[i:]
                return

    def _recover(self, e: groq.APIError, state: dict, cands: list[str]) -> None:
        """Passt die Einstellungen an, damit der nächste Versuch klappt – oder wirft _GiveUp."""
        if isinstance(e, (groq.AuthenticationError, groq.PermissionDeniedError)):
            raise _GiveUp
        low = str(getattr(e, "message", "") or e).lower()
        status = getattr(e, "status_code", None)

        if isinstance(e, (groq.APIConnectionError, groq.InternalServerError)) or (status or 0) >= 500:
            if state["net"] < 2:
                state["net"] += 1
                time.sleep(1.5 * state["net"])
                return
            return self._next_model(state, cands, "Server/Netzwerk")
        if status == 413 or "too large" in low or "context_length" in low or "reduce the length" in low:
            if not state["trimmed"]:
                state["trimmed"] = True
                self._trim_to_question()
                return
            return self._next_model(state, cands, "Anfrage zu groß")
        if isinstance(e, groq.RateLimitError) or status == 429:
            wait = self._retry_after(e)
            if wait is not None and wait <= 8 and not state["waited429"]:
                state["waited429"] = True
                time.sleep(max(0.5, wait))
                return
            return self._next_model(state, cands, "Rate-Limit")
        if "tool_use_failed" in low or "failed_generation" in low or "tool call validation" in low:
            if not state["tool_retry"]:
                state["tool_retry"] = True
                return
            return self._next_model(state, cands, "Werkzeug-Aufruf")
        # 404 (Modell weg), Bild nicht unterstützt, sonstige 4xx: anderes Modell
        return self._next_model(state, cands, f"Fehler {status}")

    # ------------------------------------------------------------------ Schleife
    def _turn(self, ctx: ToolContext, on_text: OnText, model: str, state: dict) -> tuple[str, list[dict]]:
        """Eine Anfrage, live gestreamt. Rückgabe: (Text, Werkzeug-Aufrufe)."""
        kwargs = {"model": model, "messages": self._messages(ctx), "stream": True, "temperature": 0.6}
        tools = self._tools(ctx)
        if tools:
            kwargs.update(tools=tools, tool_choice="auto")
        text, calls = "", {}
        for chunk in self.client.chat.completions.create(**kwargs):
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                text += delta.content
                state["emitted"] = True
                on_text(delta.content)
            for tc in delta.tool_calls or []:
                slot = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                if tc.id:
                    slot["id"] = tc.id
                if tc.function and tc.function.name:
                    slot["name"] += tc.function.name
                if tc.function and tc.function.arguments:
                    slot["arguments"] += tc.function.arguments
        return text, [calls[i] for i in sorted(calls)]

    def _run(self, ctx: ToolContext, on_text: OnText, on_status: OnStatus) -> None:
        state = {"idx": 0, "net": 0, "waited429": False, "tool_retry": False, "trimmed": False, "emitted": False}
        steps = attempts = 0
        while steps < MAX_STEPS:
            cands = self._candidates()
            model = cands[min(state["idx"], len(cands) - 1)]
            attempts += 1
            state["emitted"] = False
            try:
                text, calls = self._turn(ctx, on_text, model, state)
            except groq.APIError as e:
                log.info("Groq-Fehler mit %s: %s", model, short_error(str(e), 300))
                if state["emitted"] or attempts >= MAX_ATTEMPTS:
                    raise  # schon Text ausgegeben → nicht doppelt antworten
                try:
                    self._recover(e, state, cands)
                except _GiveUp:
                    raise e from None
                continue
            if not calls and not text.strip():
                # leere Antwort → anderes Modell probieren
                if attempts >= MAX_ATTEMPTS:
                    break
                try:
                    self._next_model(state, cands, "leere Antwort")
                except _GiveUp:
                    break
                continue
            steps += 1
            if not calls:
                self.history.append({"role": "assistant", "content": text})
                return
            for i, c in enumerate(calls):
                c["id"] = c["id"] or f"call_{steps}_{i}"
            self.history.append({"role": "assistant", "content": text or None, "tool_calls": [
                {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}}
                for c in calls]})
            for c in calls:
                self.history.append({"role": "tool", "tool_call_id": c["id"], "content": self._call(c, ctx, on_status)})
            state.update(net=0, waited429=False, tool_retry=False)
        else:
            on_text(" Ich habe die maximale Anzahl an Arbeitsschritten erreicht.")
            return
        raise _NoAnswer

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
        return ("Fehler: Die Internetsuche ist gerade nicht verfügbar "
                f"({short_error(str(last), 100)}). Antworte aus deinem Wissen und sage, dass es nicht ganz aktuell sein könnte.")
