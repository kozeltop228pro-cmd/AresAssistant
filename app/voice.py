from __future__ import annotations

import asyncio
import ctypes
import logging
import re
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Optional

LOGGER = logging.getLogger(__name__)
SPEAKING = threading.Event()
DEFAULT_VOICE = "en-AU-WilliamMultilingualNeural"
FALLBACK_VOICE = "ru-RU-SvetlanaNeural"
# Voices that pronounce Russian naturally; multilingual ones read Cyrillic text as Russian.
VOICES = {
    "en-AU-WilliamMultilingualNeural": "William — мужской, живой",
    "en-US-AndrewMultilingualNeural": "Andrew — мужской",
    "en-US-BrianMultilingualNeural": "Brian — мужской",
    "de-DE-FlorianMultilingualNeural": "Florian — мужской",
    "fr-FR-RemyMultilingualNeural": "Remy — мужской",
    "it-IT-GiuseppeMultilingualNeural": "Giuseppe — мужской",
    "ru-RU-SvetlanaNeural": "Светлана — женский, русский",
    "en-US-AvaMultilingualNeural": "Ava — женский",
    "en-US-EmmaMultilingualNeural": "Emma — женский",
    "de-DE-SeraphinaMultilingualNeural": "Seraphina — женский",
    "fr-FR-VivienneMultilingualNeural": "Vivienne — женский",
}


_current_voice = DEFAULT_VOICE


def set_voice(name: str) -> None:
    """Voice for every phrase the assistant says (chosen in Settings → Голос и Windows)."""
    global _current_voice
    _current_voice = name if name in VOICES else DEFAULT_VOICE


def _edge_tts():
    """Prefer the bundled edge-tts: Microsoft rejects the 7.2.8 pinned by older installs (HTTP 403)."""
    vendor = str(Path(__file__).with_name("vendor"))
    if vendor not in sys.path:
        sys.path.insert(0, vendor)
    import edge_tts  # type: ignore

    return edge_tts


def _play_mp3(path: Path) -> None:
    alias = f"ares_voice_{threading.get_ident()}"
    winmm = ctypes.windll.winmm
    command = f'open "{path}" type mpegvideo alias {alias}'
    if winmm.mciSendStringW(command, None, 0, None) != 0:
        raise RuntimeError("Windows не смог открыть голосовой файл")
    try:
        winmm.mciSendStringW(f"play {alias} wait", None, 0, None)
    finally:
        winmm.mciSendStringW(f"close {alias}", None, 0, None)


def speak(text: str, voice: str = "") -> None:
    if not text.strip():
        return

    def worker() -> None:
        SPEAKING.set()
        path = Path(tempfile.gettempdir()) / f"ares_assistant_voice_{threading.get_ident()}.mp3"
        try:
            edge_tts = _edge_tts()
            for candidate in dict.fromkeys((voice or _current_voice, FALLBACK_VOICE)):
                try:
                    asyncio.run(edge_tts.Communicate(text, candidate).save(str(path)))
                    if path.exists() and path.stat().st_size:
                        _play_mp3(path)
                        return
                except Exception:
                    LOGGER.exception("Neural voice %s failed", candidate)
            raise RuntimeError("Нейроголос недоступен")
        except Exception:
            LOGGER.exception("Neural voice failed, using Windows SAPI")
            try:
                import pythoncom  # type: ignore
                import win32com.client  # type: ignore

                pythoncom.CoInitialize()
                speaker = win32com.client.Dispatch("SAPI.SpVoice")
                for installed_voice in speaker.GetVoices():
                    description = installed_voice.GetDescription().lower()
                    if "dmitry" in description or "дмитрий" in description:
                        speaker.Voice = installed_voice
                        break
                speaker.Speak(text)
            except Exception:
                LOGGER.exception("Fallback voice failed")
        finally:
            SPEAKING.clear()
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

    threading.Thread(target=worker, name="VoiceOutput", daemon=True).start()


def list_microphones() -> list[tuple[str, str]]:
    """Return SAPI audio input descriptions and persistent token ids."""
    result: list[tuple[str, str]] = [("Системный микрофон по умолчанию", "")]
    try:
        import pythoncom  # type: ignore
        import win32com.client  # type: ignore

        pythoncom.CoInitialize()
        category = win32com.client.Dispatch("SAPI.SpObjectTokenCategory")
        category.SetId(r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech\AudioInput", False)
        tokens = category.EnumerateTokens()
        for index in range(tokens.Count):
            token = tokens.Item(index)
            description = str(token.GetDescription())
            token_id = str(token.Id)
            result.append((description, token_id))
    except Exception:
        LOGGER.exception("Unable to enumerate microphones")
    return result


class VoiceCommands:
    COMMANDS = {
        "открой": "show",
        "покажи": "show",
        "скрой": "hide",
        "закрой окно": "hide",
        "режим черновик": "mode_draft",
        "режим черновика": "mode_draft",
        "режим авто": "mode_auto",
        "режим автоответ": "mode_auto",
        "режим автоответа": "mode_auto",
        "режим обучения": "mode_learn",
        "пауза": "pause",
        "продолжить": "resume",
    }

    def __init__(
        self,
        callback: Callable[[str], None],
        microphone_token_id: str = "",
        wake_word_required: bool = False,
    ):
        self.callback = callback
        self.microphone_token_id = microphone_token_id
        self.wake_word_required = wake_word_required
        self.running = threading.Event()
        self.thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.running.set()
        self.thread = threading.Thread(target=self._run, name="VoiceRecognition", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.running.clear()

    def _match_command(self, raw_text: str) -> Optional[str]:
        text = re.sub(r"[^а-яёa-z0-9 ]+", " ", raw_text.lower())
        text = re.sub(r"\s+", " ", text).strip()
        has_wake_word = text == "алексей" or text.startswith("алексей ")
        if self.wake_word_required and not has_wake_word:
            return None
        if has_wake_word:
            text = text[len("алексей") :].strip()
        if text in self.COMMANDS:
            return self.COMMANDS[text]
        if len(text.split()) <= 5:
            for phrase, command in self.COMMANDS.items():
                if text == phrase or text.endswith(" " + phrase):
                    return command
        return None

    def _select_audio_input(self, recognizer) -> None:
        if not self.microphone_token_id:
            return
        try:
            import win32com.client  # type: ignore

            token = win32com.client.Dispatch("SAPI.SpObjectToken")
            token.SetId(self.microphone_token_id, False)
            recognizer.AudioInput = token
        except Exception:
            LOGGER.exception("Unable to select configured microphone; using system default")

    def _run(self) -> None:
        try:
            import pythoncom  # type: ignore
            import win32com.client  # type: ignore

            pythoncom.CoInitialize()
            recognizer = win32com.client.Dispatch("SAPI.SpSharedRecognizer")
            self._select_audio_input(recognizer)
            context = recognizer.CreateRecoContext()
            grammar = context.CreateGrammar()
            grammar.DictationLoad()
            grammar.DictationSetState(1)
            owner = self

            class RecognitionEvents:
                def OnRecognition(self, stream_number, stream_position, recognition_type, result):  # noqa: N802
                    try:
                        if SPEAKING.is_set():
                            return
                        phrase = result.PhraseInfo.GetText()
                        command = owner._match_command(str(phrase))
                        if command:
                            owner.callback(command)
                    except Exception:
                        LOGGER.exception("Voice command event failed")

            event_sink = win32com.client.WithEvents(context, RecognitionEvents)
            _ = event_sink
            while self.running.is_set():
                pythoncom.PumpWaitingMessages()
                time.sleep(0.08)
            try:
                grammar.DictationSetState(0)
            except Exception:
                pass
        except Exception:
            LOGGER.exception("Voice recognition unavailable")
