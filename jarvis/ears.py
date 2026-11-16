"""Die Ohren von JARVIS: Spracheingabe über das Mikrofon (Google-Spracherkennung, kostenlos)."""

from __future__ import annotations


class Ears:
    def __init__(self, language: str = "de-DE"):
        import speech_recognition as sr

        self.sr = sr
        self.language = language
        self.recognizer = sr.Recognizer()
        self.recognizer.pause_threshold = 0.9  # Sekunden Stille = Satzende
        self.recognizer.dynamic_energy_threshold = True
        self.microphone = sr.Microphone()
        with self.microphone as source:
            self.recognizer.adjust_for_ambient_noise(source, duration=0.8)

    def listen(self, timeout: float | None = 8, phrase_limit: float | None = 45) -> str | None:
        """Hört zu und gibt den erkannten Text zurück (None = nichts verstanden)."""
        sr = self.sr
        try:
            with self.microphone as source:
                audio = self.recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_limit)
        except sr.WaitTimeoutError:
            return None
        try:
            return self.recognizer.recognize_google(audio, language=self.language)
        except sr.UnknownValueError:
            return None
        except sr.RequestError as e:
            raise RuntimeError(f"Spracherkennung nicht erreichbar: {e}") from e


def create_ears(language: str) -> tuple[Ears | None, str]:
    try:
        return Ears(language), "Mikrofon bereit"
    except Exception as e:  # noqa: BLE001 - z.B. kein Mikrofon / PyAudio fehlt
        return None, f"Kein Mikrofon ({type(e).__name__}: {e})"
