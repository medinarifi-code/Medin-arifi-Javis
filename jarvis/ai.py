"""Das Gehirn von JARVIS: Groq + OpenRouter (beides kostenlos) mit Werkzeugen und Streaming.

Zuverlässigkeit: Jede Frage probiert bei Problemen der Reihe nach mehrere Modelle – erst Groq,
dann die kostenlosen OpenRouter-Modelle (Rate-Limit, zu große Anfrage, Modell weg, kaputter
Werkzeug-Aufruf, Netzwerkfehler, falscher Key) und zuletzt eine Antwort ohne Werkzeuge.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Callable

import groq
import httpx
import openai
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
OPENROUTER_URL = "https://openrouter.ai/api/v1"
OPENROUTER_MODELS = [
    "deepseek/deepseek-chat-v3-0324:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "qwen/qwen3-235b-a22b:free",
    "mistralai/mistral-small-3.2-24b-instruct:free",
]
OPENROUTER_VISION = ["meta-llama/llama-4-maverick:free", "qwen/qwen2.5-vl-72b-instruct:free"]
MAX_OPENROUTER = 4
API_ERRORS = (groq.APIError, openai.APIError)
KEY_NAMES = {"groq": "GROQ_API_KEY", "openrouter": "OPENROUTER_API_KEY"}
MAX_STEPS = 15        # Werkzeug-Runden pro Frage
MAX_ATTEMPTS = 18     # Anfragen pro Frage (inkl. Wiederholungen)
HISTORY_LIMIT = 12    # Nachrichten, die mitgeschickt werden
TOOL_RESULT_KEEP = 1200  # so lang bleiben alte Werkzeug-Ergebnisse im Verlauf
BUSY_TEXT = "Ich bin gerade etwas überlastet. Bitte versuch es in einer Minute nochmal."


def short_error(message: str, limit: int = 160) -> str:
    text = re.sub(r"(gsk_|sk-or-)[\w-]+", "***", str(message or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


class _GiveUp(Exception):
    """Alle Wege probiert – Fehler an den Nutzer melden."""


class _NoAnswer(Exception):
    """Kein Modell hat eine Antwort geliefert."""


# ---------------------------------------------------------------------------- OpenRouter-Katalog
_catalog: dict = {"time": 0.0, "text": None, "vision": None}


def _fetch_openrouter_catalog() -> list[dict]:
    """Öffentliche Modell-Liste von OpenRouter (kein Key nötig)."""
    r = httpx.get(f"{OPENROUTER_URL}/models", timeout=10)
    r.raise_for_status()
    return r.json().get("data", [])


def openrouter_models(vision: bool = False, override: list[str] | None = None) -> list[str]:
    """Kostenlose OpenRouter-Modelle mit Werkzeug-Unterstützung (1 h zwischengespeichert)."""
    if override and not vision:
        return list(override)
    key = "vision" if vision else "text"
    if _catalog[key] is not None and time.time() - _catalog["time"] < 3600:
        return _catalog[key]
    preferred = OPENROUTER_VISION if vision else OPENROUTER_MODELS
    try:
        data = _fetch_openrouter_catalog()
        free = [m for m in data if str(m.get("id", "")).endswith(":free")
                and "tools" in (m.get("supported_parameters") or [])]
        if vision:
            free = [m for m in free if "image" in ((m.get("architecture") or {}).get("input_modalities") or [])]
        ids = [m["id"] for m in free]
        found = [m for m in preferred if m in ids] + [m for m in ids if m not in preferred]
        result = found[:MAX_OPENROUTER] or preferred
    except Exception as e:  # noqa: BLE001 - dann eben die Standardliste
        log.info("OpenRouter-Modellliste nicht abrufbar: %s", e)
        result = preferred
    _catalog[key] = result
    _catalog["time"] = time.time()
    return result


def _kind(e: Exception) -> str:
    """Fehlerart unabhängig davon, ob der Fehler aus dem Groq- oder OpenAI-SDK kommt."""
    name, status = type(e).__name__, getattr(e, "status_code", None)
    if name in ("AuthenticationError", "PermissionDeniedError") or status in (401, 402, 403):
        return "auth"
    if name in ("APIConnectionError", "APITimeoutError", "InternalServerError") or (status or 0) >= 500:
        return "net"
    if name == "RateLimitError" or status == 429:
        return "rate"
    return "other"


class Jarvis:
    """Ein Gespräch (eine Browser-Sitzung)."""

    def __init__(self, cfg: Config, client=None, router=None):
        self.cfg = cfg
        self.clients: dict = {}
        if client is not None or cfg.groq_api_key:
            self.clients["groq"] = client or Groq(api_key=cfg.groq_api_key, max_retries=0, timeout=60)
        if router is not None or cfg.openrouter_api_key:
            self.clients["openrouter"] = router or openai.OpenAI(
                base_url=OPENROUTER_URL, api_key=cfg.openrouter_api_key, max_retries=0, timeout=60,
                default_headers={"X-Title": "JARVIS"})
        preferred = [cfg.groq_model] if cfg.groq_model else []
        self.models = preferred + [m for m in DEFAULT_MODELS if m not in preferred]
        self._auth_failed: dict[str, Exception] = {}
        self.history: list[dict] = []
        self.known_addresses: set[str] = set()
        self.tools_ok = True
        self.vision_models: list[str] | None = None
        self.vision = False  # aktuelle Frage enthält Bilder

    @property
    def client(self):
        """Groq-Client (für Websuche/Vision-Liste)."""
        return self.clients.get("groq")

    def _candidates(self, skip: set[str] = frozenset()) -> list[tuple[str, str]]:
        """(Anbieter, Modell) in Reihenfolge: erst Groq, dann kostenlose OpenRouter-Modelle."""
        out: list[tuple[str, str]] = []
        if "groq" in self.clients and "groq" not in skip:
            if self.vision:
                if self.vision_models is None:
                    self.vision_models = self._find_vision_models()
                out += [("groq", m) for m in self.vision_models]
            else:
                out += [("groq", m) for m in self.models]
        if "openrouter" in self.clients and "openrouter" not in skip:
            out += [("openrouter", m) for m in openrouter_models(self.vision, self.cfg.openrouter_models)]
        return out

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
        if not self.clients:
            on_text("Mir fehlt noch ein kostenloser API-Key (GROQ_API_KEY oder OPENROUTER_API_KEY). "
                    "Bitte bei Render unter Environment eintragen.")
            return
        self._auth_failed = {}
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
        except API_ERRORS as e:
            log.error("KI-Fehler: %s", short_error(str(e), 500))
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
    def _explain(self, e: Exception) -> str:
        failed = [p for p in self.clients if p in self._auth_failed]
        if failed and len(failed) == len(self.clients):
            names = " und ".join(KEY_NAMES[p] for p in failed)
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

    def _recover(self, e: Exception, state: dict, cands: list) -> None:
        """Passt die Einstellungen an, damit der nächste Versuch klappt – oder wirft _GiveUp."""
        kind = _kind(e)
        low = str(getattr(e, "message", "") or e).lower()
        status = getattr(e, "status_code", None)
        provider = cands[min(state["idx"], len(cands) - 1)][0]

        if kind == "auth":
            # Key dieses Anbieters geht nicht → Anbieter für diese Frage überspringen
            self._auth_failed[provider] = e
            before = sum(1 for c in cands[:state["idx"]] if c[0] != provider)
            state["skip"].add(provider)
            remaining = self._candidates(state["skip"])
            if not remaining:
                raise _GiveUp
            log.warning("%s abgelehnt (%s) – nutze anderen Anbieter", KEY_NAMES[provider], status)
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
        # 404 (Modell weg), Bild nicht unterstützt, sonstige 4xx: anderes Modell
        return self._next_model(state, cands, f"Fehler {status}")

    # ------------------------------------------------------------------ Schleife
    def _turn(self, ctx: ToolContext, on_text: OnText, cand: tuple[str, str], state: dict) -> tuple[str, list[dict]]:
        """Eine Anfrage, live gestreamt. Rückgabe: (Text, Werkzeug-Aufrufe)."""
        provider, model = cand
        kwargs = {"model": model, "messages": self._messages(ctx), "stream": True, "temperature": 0.6}
        tools = self._tools(ctx)
        if tools:
            kwargs.update(tools=tools, tool_choice="auto")
        text, calls = "", {}
        for chunk in self.clients[provider].chat.completions.create(**kwargs):
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
        state = {"idx": 0, "net": 0, "waited429": False, "tool_retry": False, "trimmed": False, "emitted": False,
                 "skip": set()}
        steps = attempts = 0
        while steps < MAX_STEPS:
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
        if self.client is None:
            return ("Fehler: Die Internetsuche braucht den kostenlosen GROQ_API_KEY. Antworte aus deinem Wissen "
                    "und sage, dass es nicht ganz aktuell sein könnte.")
        prompt = f"Suche im Internet und fasse die wichtigsten aktuellen Fakten kurz auf Deutsch zusammen: {query}"
        last = None
        for model in SEARCH_MODELS:
            try:
                res = self.client.chat.completions.create(model=model, messages=[{"role": "user", "content": prompt}])
                return res.choices[0].message.content or "Keine Ergebnisse gefunden."
            except API_ERRORS as e:
                last = e
                log.warning("Websuche mit %s fehlgeschlagen: %s", model, short_error(str(e), 300))
        return ("Fehler: Die Internetsuche ist gerade nicht verfügbar "
                f"({short_error(str(last), 100)}). Antworte aus deinem Wissen und sage, dass es nicht ganz aktuell sein könnte.")
