"""Die Stimme von JARVIS: ElevenLabs-Sprachausgabe, satzweise gestreamt für flüssiges Sprechen.

Ablauf: Text kommt stückweise von Claude -> SentenceBuffer schneidet ganze Sätze heraus ->
Synthese-Thread holt das Audio von ElevenLabs (PCM) -> Player-Thread spielt sofort ab.
So spricht JARVIS den ersten Satz, während der Rest der Antwort noch entsteht.
"""

from __future__ import annotations

import queue
import re
import threading

SAMPLE_RATE = 22050
_SENTENCE_END = re.compile(r"(?<=[.!?…:;])\s+|\n+")


def clean_for_speech(text: str) -> str:
    """Entfernt Markdown, URLs und Sonderzeichen, die beim Vorlesen stören."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[*_#`>|]+", "", text)
    text = re.sub(r"^\s*[-•▸]\s+", "", text, flags=re.M)
    return re.sub(r"\s+", " ", text).strip()


class SentenceBuffer:
    """Sammelt gestreamten Text und gibt fertige Sätze zurück."""

    def __init__(self, min_len: int = 25):
        self.buf = ""
        self.min_len = min_len

    def feed(self, chunk: str) -> list[str]:
        self.buf += chunk
        out = []
        while True:
            parts = _SENTENCE_END.split(self.buf, maxsplit=1)
            if len(parts) < 2:
                break
            head, rest = parts
            # Sehr kurze Stücke ("Ja.") mit dem nächsten Satz zusammenfassen -> natürlicherer Fluss.
            if len(head.strip()) < self.min_len and rest.strip():
                m = _SENTENCE_END.search(rest)
                if not m:
                    break
                head = head + " " + rest[: m.start()]
                rest = rest[m.end():]
            if head.strip():
                out.append(head.strip())
            self.buf = rest
        return out

    def flush(self) -> list[str]:
        rest, self.buf = self.buf.strip(), ""
        return [rest] if rest else []


class Voice:
    """Basisklasse ohne Ton (für --mute)."""

    enabled = False

    def say(self, text: str) -> None: ...
    def stop(self) -> None: ...
    def wait(self, poll=None) -> None: ...
    def busy(self) -> bool:
        return False
    def close(self) -> None: ...


class ElevenLabsVoice(Voice):
    enabled = True

    def __init__(self, api_key: str, voice_id: str, model_id: str, language: str = "de"):
        from elevenlabs import VoiceSettings
        from elevenlabs.client import ElevenLabs
        import sounddevice as sd

        self.client = ElevenLabs(api_key=api_key)
        self.voice_id = voice_id
        self.model_id = model_id
        self.language = language.split("-")[0]
        self.settings = VoiceSettings(stability=0.45, similarity_boost=0.8, style=0.15,
                                      use_speaker_boost=True, speed=1.05)
        self.sd = sd
        self._texts: queue.Queue[str | None] = queue.Queue()
        self._audio: queue.Queue[tuple[int, bytes | None]] = queue.Queue()
        self._generation = 0  # wird bei stop() erhöht -> alte Aufträge verwerfen
        self._pending = 0
        self._lock = threading.Lock()
        self._previous_text = ""
        self.last_error: str | None = None
        threading.Thread(target=self._synth_loop, daemon=True).start()
        threading.Thread(target=self._play_loop, daemon=True).start()

    # --- öffentliche API ---
    def say(self, text: str) -> None:
        text = clean_for_speech(text)
        if not text:
            return
        with self._lock:
            self._pending += 1
        self._texts.put(text)

    def busy(self) -> bool:
        with self._lock:
            return self._pending > 0

    def wait(self, poll=None) -> None:
        """Wartet, bis alles gesprochen ist. poll() -> True bricht ab (z.B. Tastendruck)."""
        import time

        while self.busy():
            if poll and poll():
                self.stop()
                return
            time.sleep(0.05)

    def stop(self) -> None:
        with self._lock:
            self._generation += 1
            self._pending = 0
        for q in (self._texts, self._audio):
            try:
                while True:
                    q.get_nowait()
            except queue.Empty:
                pass
        self._previous_text = ""

    def close(self) -> None:
        self.stop()

    # --- Hintergrund-Threads ---
    def _synth_loop(self) -> None:
        while True:
            text = self._texts.get()
            gen = self._generation
            kwargs = {
                "text": text,
                "model_id": self.model_id,
                "output_format": f"pcm_{SAMPLE_RATE}",
                "voice_settings": self.settings,
                "optimize_streaming_latency": 2,
            }
            if "flash" in self.model_id or "turbo" in self.model_id:
                kwargs["language_code"] = self.language  # erzwingt deutsche Aussprache
            if self._previous_text:
                kwargs["previous_text"] = self._previous_text  # gleichmäßige Betonung über Sätze hinweg
            try:
                leftover = b""
                for chunk in self.client.text_to_speech.stream(self.voice_id, **kwargs):
                    if gen != self._generation:
                        break
                    data = leftover + chunk
                    cut = len(data) - (len(data) % 2)  # 16-bit-Samples nicht zerschneiden
                    leftover = data[cut:]
                    if cut:
                        self._audio.put((gen, data[:cut]))
                self._previous_text = text[-300:]
                self.last_error = None
            except Exception as e:  # noqa: BLE001
                self.last_error = f"ElevenLabs-Fehler: {e}"
            self._audio.put((gen, None))  # Satzende-Marker

    def _play_loop(self) -> None:
        with self.sd.RawOutputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16") as out:
            while True:
                gen, chunk = self._audio.get()
                if gen != self._generation:
                    continue  # nach stop() veraltetes Audio verwerfen
                if chunk is None:
                    with self._lock:
                        self._pending = max(0, self._pending - 1)
                    continue
                out.write(chunk)


class WindowsVoice(Voice):
    """Notfall-Stimme ohne ElevenLabs (Windows-Systemstimme via pyttsx3)."""

    enabled = True

    def __init__(self):
        import pyttsx3

        self._q: queue.Queue[str] = queue.Queue()
        self._pending = 0
        self._lock = threading.Lock()
        self._pyttsx3 = pyttsx3
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        engine = self._pyttsx3.init()
        for v in engine.getProperty("voices"):
            if "de" in (v.id or "").lower() or "german" in (v.name or "").lower():
                engine.setProperty("voice", v.id)
                break
        engine.setProperty("rate", 185)
        while True:
            text = self._q.get()
            engine.say(text)
            engine.runAndWait()
            with self._lock:
                self._pending = max(0, self._pending - 1)

    def say(self, text: str) -> None:
        text = clean_for_speech(text)
        if text:
            with self._lock:
                self._pending += 1
            self._q.put(text)

    def busy(self) -> bool:
        with self._lock:
            return self._pending > 0

    def wait(self, poll=None) -> None:
        import time

        while self.busy():
            if poll and poll():
                self.stop()
                return
            time.sleep(0.05)

    def stop(self) -> None:
        try:
            while True:
                self._q.get_nowait()
                with self._lock:
                    self._pending = max(0, self._pending - 1)
        except queue.Empty:
            pass


def create_voice(cfg, mute: bool) -> tuple[Voice, str]:
    """Wählt die beste verfügbare Stimme. Gibt (Stimme, Beschreibung) zurück."""
    if mute:
        return Voice(), "Stumm"
    if cfg.elevenlabs_api_key:
        try:
            return (ElevenLabsVoice(cfg.elevenlabs_api_key, cfg.elevenlabs_voice_id, cfg.elevenlabs_model,
                                    cfg.language), f"ElevenLabs ({cfg.elevenlabs_model})")
        except Exception as e:  # noqa: BLE001
            reason = f"ElevenLabs nicht verfügbar: {e}"
    else:
        reason = "Kein ELEVENLABS_API_KEY gesetzt"
    try:
        return WindowsVoice(), f"Systemstimme ({reason})"
    except Exception:  # noqa: BLE001
        return Voice(), f"Stumm ({reason})"
