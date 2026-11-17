"""Das Gehirn von JARVIS: Agenten-Schleife über alle verbundenen KI-Anbieter.

- Automatische Modellauswahl je Aufgabe (schnell / allgemein / Code / Recherche / Bild), siehe providers.py
- Agenten-Profile (Rolle, Werkzeuge, bevorzugte Stufe), siehe agents.py
- Gedächtnis-Kontext und gelernte Lektionen werden in den System-Prompt eingebaut
- Zuverlässigkeit: Bei Problemen werden der Reihe nach weitere Modelle/Anbieter probiert
  (Rate-Limit, zu große Anfrage, Modell weg, kaputter Werkzeug-Aufruf, Netzwerk, falscher Key),
  zuletzt eine Antwort ohne Werkzeuge. Erst wenn wirklich nichts geht, kommt eine freundliche Meldung.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Callable

import groq
import openai

from . import providers as prov
from .agents import AGENTS
from .config import Config
from .persona import system_prompt
from .tools import ToolContext, ToolError, describe, execute, tool_definitions

try:
    import anthropic
    _ANTHROPIC_ERRORS: tuple = (anthropic.APIError,)
except ImportError:  # pragma: no cover
    _ANTHROPIC_ERRORS = ()

log = logging.getLogger("jarvis")

OnText = Callable[[str], None]
OnStatus = Callable[[str], None]

# Klassische Modell-Liste (wenn Clients direkt übergeben werden, z. B. in Tests)
DEFAULT_MODELS = [
    "openai/gpt-oss-120b",
    "llama-3.3-70b-versatile",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "openai/gpt-oss-20b",
    "llama-3.1-8b-instant",
]
SEARCH_MODELS = ["groq/compound-mini", "groq/compound"]
API_ERRORS = (groq.APIError, openai.APIError, *_ANTHROPIC_ERRORS)
KEY_NAMES = {k: v["env"] for k, v in prov.PROVIDERS.items()}
MAX_STEPS = 15
MAX_ATTEMPTS = 18
HISTORY_LIMIT = 12
TOOL_RESULT_KEEP = 1200
BUSY_TEXT = "Ich bin gerade etwas überlastet. Bitte versuch es in einer Minute nochmal."


def short_error(message: str, limit: int = 160) -> str:
    text = re.sub(r"(gsk_|sk-or-|sk-ant-|sk-|AIza)[\w-]+", "***", str(message or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


class _GiveUp(Exception):
    pass


class _NoAnswer(Exception):
    pass


class Cancelled(Exception):
    """Mission wurde abgebrochen."""


def _kind(e: Exception) -> str:
    name, status = type(e).__name__, getattr(e, "status_code", None)
    if name in ("AuthenticationError", "PermissionDeniedError") or status in (401, 402, 403):
        return "auth"
    if name in ("APIConnectionError", "APITimeoutError", "InternalServerError", "OverloadedError") or (status or 0) >= 500:
        return "net"
    if name == "RateLimitError" or status == 429:
        return "rate"
    return "other"


class Jarvis:
    """Ein Gespräch (Browser-Sitzung) oder ein Missions-Schritt."""

    def __init__(self, cfg: Config, client=None, router=None, registry: prov.Registry | None = None, core=None,
                 agent: str = "master"):
        self.cfg = cfg
        overrides = {}
        if client is not None:
            overrides["groq"] = client
        if router is not None:
            overrides["openrouter"] = router
        self.core = core
        self.registry = registry or prov.Registry(cfg, core.db if core else None, overrides or None)
        self.registry.build()
        # Automatische Modellauswahl nur im echten Betrieb; mit direkt übergebenen Clients: klassische Liste
        self.auto_route = registry is not None or not overrides
        preferred = [cfg.groq_model] if cfg.groq_model else []
        self.models = preferred + [m for m in DEFAULT_MODELS if m not in preferred]
        self.history: list[dict] = []
        self.known_addresses: set[str] = set()
        self.tools_ok = True
        self.vision = False
        self.tier = "smart"
        self.agent = agent
        self.extra_system = ""
        self.should_stop: Callable[[], bool] = lambda: False
        self.last_route: dict = {}
        self._auth_failed: dict[str, Exception] = {}

    @property
    def clients(self) -> dict:
        return self.registry.clients

    @property
    def client(self):
        return self.clients.get("groq")

    def _candidates(self, skip: set[str] = frozenset()) -> list[tuple[str, str]]:
        if self.auto_route:
            return self.registry.candidates("vision" if self.vision else self.tier, skip)
        out: list[tuple[str, str]] = []
        if "groq" in self.clients and "groq" not in skip:
            models = prov.MODELS["groq"]["vision"] if self.vision else self.models
            out += [("groq", m) for m in models]
        if "openrouter" in self.clients and "openrouter" not in skip:
            out += [("openrouter", m) for m in prov.openrouter_models(self.vision, self.cfg.openrouter_models)]
        return out

    def reset(self) -> None:
        self.history.clear()

    # ------------------------------------------------------------------ öffentlich
    def ask(self, text: str, on_text: OnText, on_status: OnStatus, on_event=None, mailbox=None, uploads=None,
            agent: str | None = None) -> None:
        if not self.clients:
            on_text("Mir fehlt noch ein kostenloser API-Key (GROQ_API_KEY oder OPENROUTER_API_KEY). "
                    "Bitte bei Render unter Environment eintragen.")
            return
        self._auth_failed = {}
        uploads = uploads or []
        self.vision = any(u.kind == "image" for u in uploads)
        self.agent = agent or self.agent
        profile = AGENTS.get(self.agent, AGENTS["master"])
        self.tier = profile["tier"] or prov.classify(text, self.vision)
        ctx = ToolContext(output_dir=self.cfg.output_dir, mailbox=mailbox, user_message=text,
                          known_addresses=self.known_addresses, on_event=on_event, core=self.core, agent=self.agent)
        checkpoint = len(self.history)
        self.history.append({"role": "user", "content": self._user_content(text, uploads)})
        self.tools_ok = True
        self._context = self._build_context(text)
        try:
            self._run(ctx, on_text, on_status)
            if uploads and checkpoint < len(self.history):
                self.history[checkpoint] = {"role": "user", "content": "\n\n".join(
                    [text, *(u.history_part() for u in uploads)])}
            self._shrink_history()
        except Cancelled:
            del self.history[checkpoint:]
            raise
        except _NoAnswer:
            del self.history[checkpoint:]
            on_text(BUSY_TEXT)
        except API_ERRORS as e:
            log.error("KI-Fehler: %s", short_error(str(e), 500))
            del self.history[checkpoint:]
            on_text(self._explain(e))
        except Exception:  # noqa: BLE001
            log.exception("Unerwarteter Fehler")
            del self.history[checkpoint:]
            on_text("Da ist gerade etwas schiefgelaufen. Bitte versuch es nochmal.")
        finally:
            self.vision = False

    def _build_context(self, text: str) -> str:
        parts = []
        profile = AGENTS.get(self.agent, AGENTS["master"])
        if self.core is not None:
            parts.append(f"Aktiver Agent: {profile['name']}. {profile['prompt']}")
            try:
                mem = self.core.memory.context_for(text)
                if mem:
                    parts.append(mem)
                lessons = self.core.active_lessons()
                if lessons:
                    parts.append("Gelernte Regeln (vom Nutzer freigegeben):\n" + "\n".join(f"- {x}" for x in lessons))
            except Exception as e:  # noqa: BLE001
                log.info("Kontext nicht verfügbar: %s", e)
        if self.extra_system:
            parts.append(self.extra_system)
        return "\n\n".join(parts)

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
        for msg in self.history:
            content = msg.get("content")
            if msg.get("role") == "tool" and isinstance(content, str) and len(content) > TOOL_RESULT_KEEP:
                msg["content"] = content[:TOOL_RESULT_KEEP] + " […gekürzt]"

    # ------------------------------------------------------------------ Anfrage bauen
    def _messages(self, ctx: ToolContext, provider: str) -> list[dict]:
        recent = self.history[-HISTORY_LIMIT:]
        while recent and recent[0]["role"] != "user":
            recent = recent[1:]
        system = system_prompt(self.cfg, ctx.mailbox is not None)
        if getattr(self, "_context", ""):
            system += "\n\n" + self._context
        if provider != "anthropic":
            recent = [{k: v for k, v in m.items() if not k.startswith("_")} for m in recent]
        return [{"role": "system", "content": system}, *recent]

    def _tools(self, ctx: ToolContext) -> list[dict] | None:
        if not self.tools_ok:
            return None
        allowed = AGENTS.get(self.agent, AGENTS["master"])["tools"] if self.core is not None else None
        defs = tool_definitions(ctx.mailbox is not None, allowed, brain_tools=self.core is not None)
        return [{"type": "function", "function": {
            "name": t["name"], "description": t["description"],
            "parameters": t["parameters"] or {"type": "object", "properties": {}},
        }} for t in defs]

    # ------------------------------------------------------------------ Fehler
    def _explain(self, e: Exception) -> str:
        failed = [p for p in self.clients if p in self._auth_failed]
        if failed and len(failed) == len(self.clients):
            names = " und ".join(KEY_NAMES.get(p, p) for p in failed)
            verb = "werden" if len(failed) > 1 else "wird"
            return f"Mein {names} {verb} abgelehnt. Bitte den Key bei Render unter Environment prüfen."
        if type(e).__name__ in ("APIConnectionError", "APITimeoutError"):
            return "Ich kann die KI-Server gerade nicht erreichen. Bitte versuch es gleich nochmal."
        return BUSY_TEXT

    @staticmethod
    def _retry_after(e: Exception) -> float | None:
        try:
            value = e.response.headers.get("retry-after")  # type: ignore[union-attr]
            return float(value) if value is not None else None
        except (AttributeError, TypeError, ValueError):
            return None

    def _next_model(self, state: dict, cands: list, reason: str) -> None:
        if state["idx"] + 1 < len(cands):
            log.warning("Modell %s: %s – versuche %s", cands[state["idx"]], reason, cands[state["idx"] + 1])
            state.update(idx=state["idx"] + 1, net=0, waited429=False, tool_retry=False)
            return
        if self.tools_ok:
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

    def _recover(self, e: Exception, state: dict, cands: list) -> None:
        kind = _kind(e)
        low = str(getattr(e, "message", "") or e).lower()
        status = getattr(e, "status_code", None)
        provider = cands[min(state["idx"], len(cands) - 1)][0]

        if kind == "auth":
            self._auth_failed[provider] = e
            before = sum(1 for c in cands[:state["idx"]] if c[0] != provider)
            state["skip"].add(provider)
            remaining = self._candidates(state["skip"])
            if not remaining:
                raise _GiveUp
            log.warning("%s abgelehnt (%s) – nutze anderen Anbieter", KEY_NAMES.get(provider, provider), status)
            state.update(idx=min(before, len(remaining) - 1), net=0, waited429=False, tool_retry=False)
            return
        if kind == "net":
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
        if kind == "rate":
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
        return self._next_model(state, cands, f"Fehler {status}")

    # ------------------------------------------------------------------ Schleife
    def _turn(self, ctx: ToolContext, on_text: OnText, cand: tuple[str, str], state: dict) -> tuple[str, list[dict]]:
        provider, model = cand
        messages = self._messages(ctx, provider)
        kwargs = {"model": model, "messages": messages, "stream": True}
        if provider not in ("anthropic",):
            kwargs["temperature"] = 0.6
        tools = self._tools(ctx)
        if tools:
            kwargs.update(tools=tools, tool_choice="auto")
        text, calls = "", {}
        state["raw"] = None
        t0 = time.time()
        try:
            for chunk in self.clients[provider].chat.completions.create(**kwargs):
                if getattr(chunk, "raw_anthropic", None):
                    state["raw"] = chunk.raw_anthropic
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
        except Exception as e:
            self.registry.record(provider, model, False, time.time() - t0, error=short_error(str(e), 280))
            raise
        tin = sum(len(str(m.get("content") or "")) for m in messages) // 4
        self.registry.record(provider, model, True, time.time() - t0, tin, (len(text) + sum(len(c["arguments"]) for c in calls.values())) // 4)
        self.last_route = {"provider": provider, "model": model, "tier": "vision" if self.vision else self.tier}
        return text, [calls[i] for i in sorted(calls)]

    def _run(self, ctx: ToolContext, on_text: OnText, on_status: OnStatus) -> None:
        state = {"idx": 0, "net": 0, "waited429": False, "tool_retry": False, "trimmed": False, "emitted": False,
                 "skip": set(), "raw": None}
        steps = attempts = 0
        while steps < MAX_STEPS:
            if self.should_stop():
                raise Cancelled
            cands = self._candidates(state["skip"])
            if not cands:
                raise _NoAnswer
            cand = cands[min(state["idx"], len(cands) - 1)]
            attempts += 1
            state["emitted"] = False
            try:
                text, calls = self._turn(ctx, on_text, cand, state)
            except API_ERRORS as e:
                log.info("Fehler mit %s/%s: %s", cand[0], cand[1], short_error(str(e), 300))
                if state["emitted"] or attempts >= MAX_ATTEMPTS:
                    raise
                try:
                    self._recover(e, state, cands)
                except _GiveUp:
                    raise e from None
                continue
            if not calls and not text.strip():
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
            msg = {"role": "assistant", "content": text or None, "tool_calls": [
                {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}}
                for c in calls]}
            if state.get("raw"):
                msg["_anthropic"] = state["raw"]
            self.history.append(msg)
            for c in calls:
                if self.should_stop():
                    raise Cancelled
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
                if self.core is not None:
                    self.core.db.event(f"Recherche: {args.get('query', '')}", kind="info", agent="Research Agent",
                                       mission_id=ctx.mission_id)
                return self._search(str(args.get("query", "")))
            return execute(name, args, ctx)
        except ToolError as e:
            return f"Fehler: {e}"
        except Exception as e:  # noqa: BLE001
            log.exception("Werkzeug %s fehlgeschlagen", name)
            if self.core is not None:
                self.core.db.event(f"Werkzeug-Fehler: {name}", kind="err", agent=ctx.agent_label(), detail=str(e)[:500],
                                   mission_id=ctx.mission_id)
            return f"Fehler: {type(e).__name__}: {e}"

    def _search(self, query: str) -> str:
        if self.client is None:
            return ("Fehler: Die Internetsuche braucht den kostenlosen GROQ_API_KEY. Antworte aus deinem Wissen "
                    "und sage, dass es nicht ganz aktuell sein könnte.")
        prompt = f"Suche im Internet und fasse die wichtigsten aktuellen Fakten kurz auf Deutsch zusammen, mit Quellen: {query}"
        last = None
        for model in SEARCH_MODELS:
            t0 = time.time()
            try:
                res = self.client.chat.completions.create(model=model, messages=[{"role": "user", "content": prompt}])
                self.registry.record("groq", model, True, time.time() - t0)
                return res.choices[0].message.content or "Keine Ergebnisse gefunden."
            except API_ERRORS as e:
                last = e
                self.registry.record("groq", model, False, time.time() - t0, error=short_error(str(e), 200))
                log.warning("Websuche mit %s fehlgeschlagen: %s", model, short_error(str(e), 300))
        return ("Fehler: Die Internetsuche ist gerade nicht verfügbar "
                f"({short_error(str(last), 100)}). Antworte aus deinem Wissen und sage, dass es nicht ganz aktuell sein könnte.")
