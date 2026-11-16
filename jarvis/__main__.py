"""Startpunkt: python -m jarvis [--text] [--mute] [--conversation]"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import datetime

from . import hud
from .brain import create_brain
from .config import load_config
from .ears import create_ears
from .voice import SentenceBuffer, create_voice

EXIT_WORDS = {"exit", "quit", "beenden", "ende", "tschüss", "auf wiedersehen", "jarvis aus", "herunterfahren"}
RESET_WORDS = {"neues gespräch", "reset", "vergiss alles", "neu starten"}


def key_pressed() -> bool:
    """True, wenn (unter Windows) eine Taste gedrückt wurde -> Sprechen unterbrechen."""
    if sys.platform.startswith("win"):
        import msvcrt

        if msvcrt.kbhit():
            while msvcrt.kbhit():
                msvcrt.getwch()
            return True
    return False


def greeting(name: str) -> str:
    h = datetime.now().hour
    tageszeit = "Guten Morgen" if h < 11 else "Guten Tag" if h < 18 else "Guten Abend"
    extra = random.choice([
        "Alle Systeme sind online.",
        "Alle Systeme laufen innerhalb normaler Parameter.",
        "Ich bin bereit, wenn Sie es sind.",
    ])
    return f"{tageszeit}, {name}. {extra} Wie kann ich helfen?"


class Speaker:
    """Verbindet gestreamten Text mit Terminal-Ausgabe und Stimme."""

    def __init__(self, voice):
        self.voice = voice
        self.buffer = SentenceBuffer()
        self.mid_line = False

    def text(self, chunk: str) -> None:
        if not self.mid_line:
            hud.jarvis_prefix()
            self.mid_line = True
        hud.jarvis_chunk(chunk)
        for sentence in self.buffer.feed(chunk):
            self.voice.say(sentence)

    def status(self, msg: str) -> None:
        if self.mid_line:
            hud.newline()
            self.mid_line = False
        hud.status(msg)

    def finish(self) -> None:
        for sentence in self.buffer.flush():
            self.voice.say(sentence)
        if self.mid_line:
            hud.newline()
            self.mid_line = False


def main() -> None:
    parser = argparse.ArgumentParser(prog="jarvis", description="JARVIS - dein persönlicher KI-Assistent")
    parser.add_argument("--text", action="store_true", help="nur Tastatureingabe, kein Mikrofon")
    parser.add_argument("--mute", action="store_true", help="keine Sprachausgabe")
    parser.add_argument("-c", "--conversation", action="store_true",
                        help="Freihändiger Gesprächsmodus: hört nach jeder Antwort automatisch zu")
    args = parser.parse_args()

    if sys.platform.startswith("win"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

    cfg = load_config()
    voice, voice_info = create_voice(cfg, args.mute)
    ears, ears_info = (None, "deaktiviert (--text)") if args.text else create_ears(cfg.language)
    brain = create_brain(cfg)

    hud.boot([
        ("Neuronales Kernsystem", cfg.brain_label, cfg.brain_ready),
        ("Sprachsynthese", voice_info, voice_info.startswith("ElevenLabs")),
        ("Audiosensoren", ears_info, ears is not None),
        ("Dateisystem", str(cfg.output_dir), True),
    ])
    if not cfg.brain_ready:
        hud.warn(cfg.missing_key_hint + " Bitte in der Datei .env eintragen (siehe README).")
    hud.help_panel(ears is not None, args.conversation)

    speaker = Speaker(voice)
    speaker.text(greeting(cfg.user_name))
    speaker.finish()
    voice.wait(poll=key_pressed)

    conversation = args.conversation and ears is not None
    try:
        while True:
            # --- Eingabe ---
            if conversation:
                hud.listening()
                user_text = ears.listen(timeout=None)
                if not user_text:
                    continue
            else:
                user_text = hud.prompt().strip()
                if not user_text:
                    if ears is None:
                        continue
                    hud.listening()
                    try:
                        user_text = ears.listen()
                    except RuntimeError as e:
                        hud.warn(str(e))
                        continue
                    if not user_text:
                        hud.warn("Ich habe nichts verstanden.")
                        continue
                    hud.user_said(user_text)

            if conversation:
                hud.user_said(user_text)

            lowered = user_text.lower().strip(" .!?")
            if lowered in EXIT_WORDS:
                speaker.text(f"Sehr wohl, {cfg.user_name}. Ich fahre die Systeme herunter. Bis bald.")
                speaker.finish()
                voice.wait()
                break
            if lowered in RESET_WORDS:
                brain.reset()
                speaker.text("Gedächtnis gelöscht. Wir beginnen von vorn.")
                speaker.finish()
                voice.wait(poll=key_pressed)
                continue

            # --- Denken & Sprechen ---
            voice.stop()
            brain.ask(user_text, on_text=speaker.text, on_status=speaker.status)
            speaker.finish()
            voice.wait(poll=key_pressed)
            err = getattr(voice, "last_error", None)
            if err:
                hud.warn(err)
                voice.last_error = None
    except (KeyboardInterrupt, EOFError):
        hud.newline()
        hud.status("Systeme werden heruntergefahren")
    finally:
        voice.close()


if __name__ == "__main__":
    main()
