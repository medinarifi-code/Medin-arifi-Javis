"""Das Gehirn von JARVIS: Gespräch mit Claude inkl. Werkzeug-Schleife und Streaming."""

from __future__ import annotations

import json
from typing import Any, Callable

import anthropic

from .config import Config
from .persona import system_prompt
from .tools import TOOL_DEFINITIONS, ToolError, ToolResult, describe_call, execute_tool, validate

OnText = Callable[[str], None]
OnStatus = Callable[[str], None]
OnEvent = Callable[[dict[str, Any]], None]

MAX_STEPS = 25  # Sicherheitsgrenze für Werkzeug-Runden pro Anfrage


def create_brain(cfg: Config, web: bool = False):
    """Wählt das Gehirn: Claude (Anthropic) oder kostenlos Gemini (Google)."""
    if cfg.provider == "gemini":
        from .brain_gemini import GeminiBrain

        return GeminiBrain(cfg, web=web)
    return Brain(cfg, web=web)


class Brain:
    def __init__(self, cfg: Config, web: bool = False):
        self.cfg = cfg
        self.web = web  # Web-Modus: Dateien/Webseiten öffnet der Browser
        self.client = anthropic.Anthropic(api_key=cfg.anthropic_api_key) if cfg.anthropic_api_key else anthropic.Anthropic()
        self.system = system_prompt(cfg, web=web)
        self.messages: list[dict] = []
        self._on_event: OnEvent | None = None

    def reset(self) -> None:
        self.messages.clear()

    def ask(self, user_text: str, on_text: OnText, on_status: OnStatus, on_event: OnEvent | None = None) -> None:
        """Schickt eine Nutzeranfrage ab. Text wird live über on_text gestreamt.

        on_event erhält {"type": "file"|"open", ...}, wenn ein Werkzeug eine Datei erstellt
        oder etwas geöffnet werden soll (für die Weboberfläche).
        """
        self._on_event = on_event
        checkpoint = len(self.messages)
        self.messages.append({"role": "user", "content": user_text})
        try:
            self._run(on_text, on_status)
        except anthropic.AuthenticationError:
            self._rollback(checkpoint)
            on_text("Mein Zugangsschlüssel für das Sprachmodell wird abgelehnt. Bitte prüfen Sie den Anthropic-API-Key in der Datei .env.")
        except anthropic.RateLimitError:
            self._rollback(checkpoint)
            on_text("Ich bin gerade überlastet. Bitte versuchen Sie es in einem Moment noch einmal.")
        except anthropic.APIStatusError as e:
            self._rollback(checkpoint)
            on_text(f"Bei der Verbindung zu meinen Servern ist ein Fehler aufgetreten, Statuscode {e.status_code}.")
        except anthropic.APIConnectionError:
            self._rollback(checkpoint)
            on_text("Ich kann meine Server nicht erreichen. Bitte prüfen Sie die Internetverbindung.")

    def _rollback(self, checkpoint: int) -> None:
        # Unvollständige Runde entfernen, damit der Verlauf gültig bleibt.
        del self.messages[checkpoint:]

    def _stream_turn(self, on_text: OnText, on_status: OnStatus):
        with self.client.beta.messages.stream(
            model=self.cfg.model,
            max_tokens=64000,
            system=self.system,
            messages=self.messages,
            tools=TOOL_DEFINITIONS,
            thinking={"type": "adaptive"},
            output_config={"effort": self.cfg.effort},
            cache_control={"type": "ephemeral"},
            # Falls eine Anfrage fälschlich abgelehnt wird, übernimmt automatisch ein anderes Modell.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            for event in stream:
                if event.type == "text":
                    on_text(event.text)
                elif event.type == "content_block_start":
                    block = event.content_block
                    if block.type == "server_tool_use" and block.name == "web_search":
                        on_status("Durchsuche das Internet")
                    elif block.type == "tool_use":
                        on_status("Bereite Werkzeug vor")
                    elif block.type == "thinking":
                        on_status("Denke nach")
            return stream.get_final_message()

    def _run(self, on_text: OnText, on_status: OnStatus) -> None:
        json_retries = 0
        for _ in range(MAX_STEPS):
            try:
                response = self._stream_turn(on_text, on_status)
                json_retries = 0
            except ValueError:
                # Werkzeug-Eingabe war kein gültiges JSON -> Runde wiederholen (begrenzt).
                json_retries += 1
                if json_retries > 2:
                    raise
                continue

            if response.stop_reason == "pause_turn":
                self.messages.append({"role": "assistant", "content": response.content})
                continue

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            self.messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "refusal":
                on_text(" Das kann ich leider nicht übernehmen.")
                return
            if response.stop_reason == "max_tokens":
                if tool_uses:
                    # Abgeschnittene Werkzeug-Eingabe nicht ausführen.
                    self.messages.append({"role": "user", "content": [
                        {"type": "tool_result", "tool_use_id": b.id, "is_error": True,
                         "content": "Eingabe wurde abgeschnitten. Bitte in kleineren Teilen erneut versuchen."}
                        for b in tool_uses]})
                    continue
                return
            if not tool_uses:
                return

            results = []
            for block in tool_uses:
                results.append(self._execute(block, on_status))
            self.messages.append({"role": "user", "content": results})
        on_text(" Ich habe die maximale Anzahl an Arbeitsschritten erreicht.")

    def _execute(self, block, on_status: OnStatus) -> dict:
        args = block.input
        error = validate(block.name, args)
        if error:
            return {"type": "tool_result", "tool_use_id": block.id, "is_error": True,
                    "content": f"Ungültige Eingabe ({error}): {json.dumps(args, ensure_ascii=False)[:500]}"}
        on_status(describe_call(block.name, args))
        try:
            result = execute_tool(block.name, args, self.cfg.output_dir, client_side=self.web)
            if self._on_event and isinstance(result, ToolResult):
                if result.file is not None:
                    self._on_event({"type": "file", "path": str(result.file), "name": result.file.name,
                                    "action": result.action or "created"})
                elif result.url:
                    self._on_event({"type": "open", "url": result.url})
            return {"type": "tool_result", "tool_use_id": block.id, "content": result}
        except ToolError as e:
            return {"type": "tool_result", "tool_use_id": block.id, "is_error": True, "content": str(e)}
        except Exception as e:  # noqa: BLE001 - jeder Werkzeugfehler soll an die KI zurück
            return {"type": "tool_result", "tool_use_id": block.id, "is_error": True,
                    "content": f"{type(e).__name__}: {e}"}
