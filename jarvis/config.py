"""Einstellungen aus der .env-Datei bzw. den Render-Umgebungsvariablen."""

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
    gemini_api_key: str | None
    gemini_model: str | None
    web_password: str
    user_name: str
    output_dir: Path
    # Outlook/Hotmail (Microsoft-Anmeldung)
    ms_client_id: str | None
    # Gmail/GMX/Web.de (App-Passwort)
    mail_address: str | None
    mail_password: str | None
    mail_imap_host: str | None
    mail_smtp_host: str | None

    @property
    def outlook_enabled(self) -> bool:
        return bool(self.ms_client_id)

    @property
    def imap_enabled(self) -> bool:
        return bool(self.mail_address and self.mail_password)


def load_config() -> Config:
    output_dir = Path(os.getenv("JARVIS_OUTPUT_DIR") or _default_output_dir()).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    env = lambda name: (os.getenv(name) or "").strip() or None  # noqa: E731
    return Config(
        gemini_api_key=env("GEMINI_API_KEY") or env("GOOGLE_API_KEY"),
        gemini_model=env("GEMINI_MODEL"),
        web_password=env("JARVIS_WEB_PASSWORD") or "",
        user_name=env("JARVIS_USER_NAME") or "Sir",
        output_dir=output_dir,
        ms_client_id=env("MS_CLIENT_ID"),
        mail_address=env("MAIL_ADDRESS"),
        mail_password=(env("MAIL_APP_PASSWORD") or "").replace(" ", "") or None,  # Google zeigt es mit Leerzeichen an
        mail_imap_host=env("MAIL_IMAP_HOST"),
        mail_smtp_host=env("MAIL_SMTP_HOST"),
    )
