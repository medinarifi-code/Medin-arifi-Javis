"""Konfiguration: liest Einstellungen aus der .env-Datei bzw. Umgebungsvariablen."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _default_output_dir() -> Path:
    docs = Path.home() / "Documents"
    if not docs.exists():
        docs = Path.home() / "Dokumente"
    return docs / "JARVIS"


@dataclass
class Config:
    anthropic_api_key: str | None
    elevenlabs_api_key: str | None
    elevenlabs_voice_id: str
    elevenlabs_model: str
    user_name: str
    output_dir: Path
    model: str
    effort: str
    language: str


def load_config() -> Config:
    output_dir = Path(os.getenv("JARVIS_OUTPUT_DIR") or _default_output_dir()).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    return Config(
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        elevenlabs_api_key=os.getenv("ELEVENLABS_API_KEY") or None,
        # Standard: "Daniel" (tiefe, britisch-ruhige Stimme) - spricht mit dem
        # Multilingual-Modell fließend Deutsch. Eigene Voice-ID in .env setzen.
        elevenlabs_voice_id=os.getenv("ELEVENLABS_VOICE_ID") or "onwK4e9ZLuTAKqWW03F9",
        elevenlabs_model=os.getenv("ELEVENLABS_MODEL") or "eleven_flash_v2_5",
        user_name=os.getenv("JARVIS_USER_NAME") or "Sir",
        output_dir=output_dir,
        model=os.getenv("JARVIS_MODEL") or "claude-opus-5-5",
        effort=os.getenv("JARVIS_EFFORT") or "low",
        language=os.getenv("JARVIS_LANGUAGE") or "de-DE",
    )
