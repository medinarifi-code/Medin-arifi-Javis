"""Zentrale KI-Modellverwaltung: Anbieter, Modelle, automatische Auswahl, Nutzungsstatistik.

Anbieter (Keys nur als Umgebungsvariablen auf dem Server – nie im Browser):
  groq        GROQ_API_KEY          kostenlos
  openrouter  OPENROUTER_API_KEY    kostenlose ":free"-Modelle
  gemini      GEMINI_API_KEY        kostenloses Kontingent (Google AI Studio)
  ollama      OLLAMA_URL            eigener Server (z. B. http://mein-pc:11434)

Alle Anbieter liefern dieselbe Schnittstelle: client.chat.completions.create(**kwargs) mit
OpenAI-artigen Stream-Chunks – dadurch bleibt die Agenten-Schleife in ai.py anbieterneutral.
"""

from __future__ import annotations

import json
import logging
import os
import time
from types import SimpleNamespace as NS
from typing import Iterator

import httpx

log = logging.getLogger("jarvis.providers")

OPENROUTER_URL = "https://openrouter.ai/api/v1"

# Anbieter-Metadaten
PROVIDERS: dict[str, dict] = {
    "groq": {"label": "Groq", "env": "GROQ_API_KEY", "free": True, "site": "console.groq.com/keys"},
    "openrouter": {"label": "OpenRouter", "env": "OPENROUTER_API_KEY", "free": True, "site": "openrouter.ai/keys",
                   "base": OPENROUTER_URL},
    "gemini": {"label": "Google Gemini", "env": "GEMINI_API_KEY", "free": True, "site": "aistudio.google.com",
               "base": "https://generativelanguage.googleapis.com/v1beta/openai/"},
    "ollama": {"label": "Ollama (eigener Server)", "env": "OLLAMA_URL", "free": True, "site": "ollama.com"},
}

# Modelle pro Anbieter und Aufgaben-Stufe (per Umgebungsvariable <ANBIETER>_MODELS überschreibbar)
MODELS: dict[str, dict[str, list[str]]] = {
    "groq": {
        "fast": ["llama-3.1-8b-instant", "openai/gpt-oss-20b"],
        "smart": ["openai/gpt-oss-120b", "llama-3.3-70b-versatile", "meta-llama/llama-4-scout-17b-16e-instruct"],
        "code": ["openai/gpt-oss-120b", "llama-3.3-70b-versatile"],
        "vision": ["meta-llama/llama-4-scout-17b-16e-instruct", "meta-llama/llama-4-maverick-17b-128e-instruct"],
    },
    "openrouter": {},   # wird aus dem Live-Katalog befüllt (nur kostenlose Modelle mit Werkzeugen)
    "gemini": {"fast": ["gemini-flash-lite-latest", "gemini-flash-latest"], "smart": ["gemini-flash-latest"],
               "code": ["gemini-flash-latest"], "vision": ["gemini-flash-latest"]},
    "ollama": {"fast": ["llama3.1"], "smart": ["llama3.1"], "code": ["qwen2.5-coder"], "vision": ["llava"]},
}
OPENROUTER_PREFERRED = ["deepseek/deepseek-chat-v3-0324:free", "meta-llama/llama-3.3-70b-instruct:free",
                        "qwen/qwen3-235b-a22b:free", "mistralai/mistral-small-3.2-24b-instruct:free"]
OPENROUTER_VISION = ["meta-llama/llama-4-maverick:free", "qwen/qwen2.5-vl-72b-instruct:free"]

TIERS = {
    "fast": "Schnelle Fragen",
    "smart": "Allgemein & komplexe Analyse",
    "code": "Programmieren",
    "research": "Recherche",
    "vision": "Bilder verstehen",
}
# Standard-Reihenfolge der Anbieter je Stufe (kostenlose zuerst). In den Einstellungen änderbar.
DEFAULT_ORDER = {
    "fast": ["groq", "gemini", "openrouter", "ollama"],
    "smart": ["groq", "gemini", "openrouter", "ollama"],
    "code": ["groq", "gemini", "openrouter", "ollama"],
    "research": ["groq", "gemini", "openrouter", "ollama"],
    "vision": ["groq", "gemini", "openrouter", "ollama"],
}

_catalog: dict = {"time": 0.0, "text": None, "vision": None}


def _fetch_openrouter_catalog() -> list[dict]:
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
    preferred = OPENROUTER_VISION if vision else OPENROUTER_PREFERRED
    try:
        data = _fetch_openrouter_catalog()
        free = [m for m in data if str(m.get("id", "")).endswith(":free")
                and "tools" in (m.get("supported_parameters") or [])]
        if vision:
            free = [m for m in free if "image" in ((m.get("architecture") or {}).get("input_modalities") or [])]
        ids = [m["id"] for m in free]
        result = ([m for m in preferred if m in ids] + [m for m in ids if m not in preferred])[:4] or preferred
    except Exception as e:  # noqa: BLE001
        log.info("OpenRouter-Katalog nicht abrufbar: %s", e)
        result = preferred
    _catalog[key] = result
    _catalog["time"] = time.time()
    return result


def classify(text: str, has_images: bool = False) -> str:
    """Wählt die Aufgaben-Stufe für die automatische Modellauswahl."""
    if has_images:
        return "vision"
    t = (text or "").lower()
    if any(w in t for w in ("code", "programmier", "python", "javascript", "html", "css", "bug", "fehler im code",
                            "funktion schreiben", "app entwickel", "webseite", "website", "skript", "script", "sql")):
        return "code"
    if any(w in t for w in ("recherch", "such", "vergleich", "aktuell", "news", "nachrichten", "quellen", "wetter")):
        return "research"
    if len(t) > 350 or any(w in t for w in ("analys", "strategie", "geschäftsplan", "businessplan", "plan ", "bericht",
                                            "simul", "szenari", "präsentation", "organisier", "konzept")):
        return "smart"
    if len(t) < 80:
        return "fast"
    return "smart"


class Registry:
    def __init__(self, cfg, db=None, overrides: dict | None = None):
        self.cfg = cfg
        self.db = db
        self.clients: dict = dict(overrides or {})
        self._built = bool(overrides)
        self.order_override: dict[str, list[str]] = {}

    def key(self, name: str) -> str | None:
        if name == "groq":
            return self.cfg.groq_api_key
        if name == "openrouter":
            return self.cfg.openrouter_api_key
        return (os.getenv(PROVIDERS[name]["env"]) or "").strip() or None

    def build(self) -> None:
        if self._built:
            return
        self._built = True
        for name in PROVIDERS:
            k = self.key(name)
            if not k:
                continue
            try:
                self.clients[name] = self._make(name, k)
            except Exception as e:  # noqa: BLE001
                log.warning("Anbieter %s nicht nutzbar: %s", name, e)

    @staticmethod
    def _make(name: str, key: str):
        if name == "groq":
            from groq import Groq

            return Groq(api_key=key, max_retries=0, timeout=60)
        import openai

        if name == "ollama":
            return openai.OpenAI(base_url=key.rstrip("/") + "/v1", api_key="ollama", max_retries=0, timeout=120)
        headers = {"X-Title": "JARVIS"} if name == "openrouter" else None
        return openai.OpenAI(base_url=PROVIDERS[name]["base"], api_key=key, max_retries=0, timeout=60,
                             default_headers=headers)

    def available(self) -> list[str]:
        self.build()
        return [p for p in PROVIDERS if p in self.clients]

    def models_for(self, provider: str, tier: str) -> list[str]:
        env = os.getenv(provider.upper() + "_MODELS")
        if provider == "openrouter":
            if tier == "vision":
                return openrouter_models(True)
            return openrouter_models(False, getattr(self.cfg, "openrouter_models", None))
        if provider == "groq" and tier in ("smart", "code", "research") and self.cfg.groq_model:
            base = MODELS["groq"]["smart"]
            return [self.cfg.groq_model] + [m for m in base if m != self.cfg.groq_model]
        if env:
            return [m.strip() for m in env.split(",") if m.strip()]
        table = MODELS.get(provider, {})
        return table.get(tier) or table.get("smart") or []

    def order(self, tier: str) -> list[str]:
        saved = self.order_override.get(tier)
        if saved is None and self.db is not None:
            saved = (self.db.get_setting("model_order", {}) or {}).get(tier)
        saved = [p for p in (saved or []) if p in PROVIDERS]   # entfernte Anbieter ausblenden
        return saved or DEFAULT_ORDER.get(tier, DEFAULT_ORDER["smart"])

    def candidates(self, tier: str, skip: set[str] = frozenset()) -> list[tuple[str, str]]:
        """(Anbieter, Modell) für eine Stufe; bei 'fast' folgen die 'smart'-Modelle als Ersatz."""
        out: list[tuple[str, str]] = []
        tiers = [tier] if tier in ("smart", "vision") else [tier, "smart"]
        for t in tiers:
            for p in self.order("smart" if t == "research" else t):
                if p not in self.clients or p in skip:
                    continue
                for m in self.models_for(p, "smart" if t == "research" else t):
                    if (p, m) not in out:
                        out.append((p, m))
        return out

    def record(self, provider: str, model: str, ok: bool, latency: float, tokens_in: int = 0, tokens_out: int = 0,
               error: str = "") -> None:
        if self.db is None:
            return
        from .db import usage

        try:
            self.db.add(usage, provider=provider, model=model, ok=ok, latency=round(latency, 3),
                        tokens_in=tokens_in, tokens_out=tokens_out, error=error[:300])
        except Exception as e:  # noqa: BLE001
            log.debug("Statistik nicht gespeichert: %s", e)

    def complete(self, provider: str, model: str, messages: list[dict], max_chars: int = 20000) -> str:
        """Einfache Anfrage ohne Werkzeuge (für Gedächtnis, Second Opinion, Planung)."""
        t0 = time.time()
        text = ""
        try:
            for chunk in self.clients[provider].chat.completions.create(model=model, messages=messages, stream=True):
                if chunk.choices and chunk.choices[0].delta.content:
                    text += chunk.choices[0].delta.content
                    if len(text) > max_chars:
                        break
            self.record(provider, model, True, time.time() - t0, sum(len(str(m.get("content", ""))) for m in messages) // 4,
                        len(text) // 4)
            return text
        except Exception as e:
            self.record(provider, model, False, time.time() - t0, error=str(e))
            raise

    def quick(self, messages: list[dict], tier: str = "fast") -> str:
        """Erstes funktionierendes Modell einer Stufe (für Hintergrund-Aufgaben)."""
        last = None
        for p, m in self.candidates(tier)[:6]:
            try:
                out = self.complete(p, m, messages)
                if out.strip():
                    return out
            except Exception as e:  # noqa: BLE001
                last = e
        raise RuntimeError(f"Kein Modell erreichbar: {last}")

    def test(self, provider: str) -> dict:
        """Verbindungstest mit Zeitmessung (für die Modellverwaltung)."""
        self.build()
        if provider not in self.clients:
            return {"ok": False, "message": f"{PROVIDERS[provider]['env']} ist nicht gesetzt."}
        model = (self.models_for(provider, "fast") or ["?"])[0]
        t0 = time.time()
        try:
            out = self.complete(provider, model, [{"role": "user", "content": "Antworte nur mit: OK"}], 200)
            return {"ok": True, "model": model, "latency": round(time.time() - t0, 2), "message": out.strip()[:60]}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "model": model, "latency": round(time.time() - t0, 2), "message": str(e)[:200]}

    def status(self) -> list[dict]:
        """Status aller Anbieter inkl. Nutzung der letzten 24 h (echte Daten aus der Datenbank)."""
        self.build()
        stats: dict[str, dict] = {}
        if self.db is not None:
            from sqlalchemy import select

            from .db import usage

            since = time.time() - 86400
            for r in self.db.q(select(usage).where(usage.c.ts >= since)):
                s = stats.setdefault(r["provider"], {"calls": 0, "ok": 0, "lat": 0.0, "tin": 0, "tout": 0, "last_error": ""})
                s["calls"] += 1
                s["ok"] += int(bool(r["ok"]))
                s["lat"] += r["latency"] or 0
                s["tin"] += r["tokens_in"] or 0
                s["tout"] += r["tokens_out"] or 0
                if not r["ok"] and r["error"]:
                    s["last_error"] = r["error"]
        out = []
        for name, meta in PROVIDERS.items():
            s = stats.get(name, {})
            calls = s.get("calls", 0)
            out.append({
                "id": name, "label": meta["label"], "env": meta["env"], "free": meta["free"], "site": meta["site"],
                "configured": name in self.clients,
                "models": {t: self.models_for(name, t) for t in ("fast", "smart", "code", "vision")} if name in self.clients else {},
                "calls_24h": calls, "success_rate": round(100 * s.get("ok", 0) / calls) if calls else None,
                "avg_latency": round(s.get("lat", 0) / calls, 2) if calls else None,
                "tokens_24h": s.get("tin", 0) + s.get("tout", 0), "last_error": s.get("last_error", ""),
            })
        return out
