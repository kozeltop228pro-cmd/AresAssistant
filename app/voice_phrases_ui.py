"""Settings → «Голос и Windows»: the voice, greeting phrases and reactions to insulting tickets."""
from __future__ import annotations

import collections
import json
import logging
import os
import subprocess
import time
import tkinter as tk
from tkinter import ttk

from core import parse_ticket
from ui_assets import AnimatedButton
from voice import DEFAULT_VOICE, VOICES, set_voice, speak
from voice_phrases import (MODES, PHRASES_PATH, TRIGGER_COUNT, clean_phrases, detect_insults, load_phrases,
                           pick_phrase, save_phrases)

LOGGER = logging.getLogger(__name__)


class VoicePhrasesUI:
    # ------------------------------------------------------------ runtime
    def _phrases(self):
        """Re-read voice_phrases.json, so hand edits apply to the very next phrase."""
        data, error = load_phrases(str(self.config.get("startup_phrase", "Алексей, приветствую!")))
        set_voice(str(data.get("voice", DEFAULT_VOICE)))
        if error and error != getattr(self, "_phrases_error", ""):
            self._phrases_error = error
            try:
                self._toast(error, "error")
            except Exception:
                LOGGER.warning(error)
        return data

    def speak_phrase(self, kind, ticket="", data=None):
        data = data if data is not None else self._phrases()
        phrase = pick_phrase(dict(data.get(kind) or {}), kind, ticket)
        if phrase:
            speak(phrase)
        return phrase

    def process_ticket(self, raw_text, force=False):
        if not force and not self.paused:
            try:
                self._announce_insults(raw_text)
            except Exception:
                LOGGER.exception("Insult check failed")
        return super().process_ticket(raw_text, force)

    def _announce_insults(self, raw_text):
        parsed = parse_ticket(raw_text)
        if not parsed:
            return []
        ticket_id, question = parsed
        data = self._phrases()
        section = dict(data.get("insult") or {})
        if not section.get("enabled", True):
            return []
        words = detect_insults(question, data.get("insult_triggers_extra") or [], data.get("insult_ignore") or [])
        if not words:
            return []
        seen = self.__dict__.setdefault("_insult_seen", collections.deque(maxlen=500))
        key = (str(ticket_id), question.strip())
        if key in seen:  # the same message delivered twice is announced once
            return words
        seen.append(key)
        LOGGER.info("Ticket #%s contains insults: %s", ticket_id, ", ".join(words))
        self._toast(f"Оскорбления в тикете #{ticket_id}", "error")
        try:
            cooldown = max(0.0, float(section.get("cooldown_seconds", 5)))
        except (TypeError, ValueError):
            cooldown = 5.0
        now = time.monotonic()
        if now - getattr(self, "_insult_spoken_at", -1e9) >= cooldown:
            self._insult_spoken_at = now
            self.speak_phrase("insult", ticket_id, data)
        return words

    def save_settings(self, show_message=True):
        saved = super().save_settings(show_message=show_message)
        if saved and getattr(self, "_phrase_editors", None) and self._phrases_changed():
            self._save_phrases_from_ui(quiet=True)
        return saved

    # ----------------------------------------------------------- settings
    def _build_voice_phrases(self, parent):
        data = self._phrases()
        self.voice_name_var = tk.StringVar()
        card = self._card(parent, "Голос ассистента")
        card.pack(fill="x", pady=(14, 14))
        row = ttk.Frame(card, style="Card.TFrame")
        row.pack(fill="x")
        ttk.Combobox(row, textvariable=self.voice_name_var, values=tuple(VOICES.values()), state="readonly", width=34).pack(side="left")
        AnimatedButton(row, text="▶  Тест голоса", style="Secondary.TButton", command=self._test_voice).pack(side="left", padx=(10, 0))
        ttk.Label(card, text="Этим голосом звучат приветствие, реакции на оскорбления и голосовые ответы. Нужен интернет; без него говорит голос Windows.",
                  style="Muted.Card.TLabel", wraplength=760).pack(anchor="w", pady=(10, 0))
        self._phrase_editors = {}
        insult_hint = (f"Каждый новый тикет проверяется по базе из {TRIGGER_COUNT} шаблонов мата и оскорблений, включая обходы: "
                       "латиница, транслит, «х*й», «с у к а», «сукааа». В фразе можно написать {ticket} — подставится номер тикета.")
        for kind, title, hint in (
                ("greeting", "Приветствие при запуске", "Звучит при запуске программы, если включено «Приветствие при запуске»."),
                ("insult", "Реакция на тикет с оскорблениями", insult_hint)):
            self._build_phrase_card(parent, kind, title, hint)
        card = self._card(parent, "Файл фраз")
        card.pack(fill="x", pady=(0, 14))
        ttk.Label(card, text=str(PHRASES_PATH), style="Card.TLabel", wraplength=760).pack(anchor="w", pady=(2, 6))
        ttk.Label(card, text="Фразы можно править прямо в файле: программа перечитывает его перед каждой фразой. "
                             "Там же есть свои слова-триггеры (insult_triggers_extra) и исключения (insult_ignore).",
                  style="Muted.Card.TLabel", wraplength=760).pack(anchor="w", pady=(0, 12))
        row = ttk.Frame(card, style="Card.TFrame")
        row.pack(fill="x")
        AnimatedButton(row, text="Сохранить фразы", style="Accent.TButton", command=self._save_phrases_from_ui).pack(side="left")
        AnimatedButton(row, text="Открыть файл", style="Secondary.TButton", command=self._open_phrases_file).pack(side="left", padx=10)
        AnimatedButton(row, text="Перечитать файл", style="Ghost.TButton", command=self._reload_phrases_ui).pack(side="left")
        self._fill_phrases_ui(data)

    def _build_phrase_card(self, parent, kind, title, hint):
        p = self.palette
        card = self._card(parent, title)
        card.pack(fill="x", pady=(0, 14))
        editor = {"mode": tk.StringVar(), "fixed": tk.StringVar(), "said": tk.StringVar()}
        if kind == "insult":
            editor["enabled"] = tk.BooleanVar(value=True)
            self._checkbutton(card, "Озвучивать тикеты с оскорблениями", editor["enabled"]).pack(anchor="w", pady=(0, 8))
        ttk.Label(card, text=hint, style="Muted.Card.TLabel", wraplength=760).pack(anchor="w", pady=(0, 10))
        grid = ttk.Frame(card, style="Card.TFrame")
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)
        ttk.Label(grid, text="Режим", style="Card.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 16), pady=4)
        ttk.Combobox(grid, textvariable=editor["mode"], values=tuple(MODES.values()), state="readonly").grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Label(grid, text="Одна фраза", style="Card.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 16), pady=4)
        fixed = ttk.Combobox(grid, textvariable=editor["fixed"])
        fixed.grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Label(card, text="Фразы для случайного выбора · по одной на строку", style="Muted.Card.TLabel").pack(anchor="w", pady=(10, 5))
        text = self._text_widget(card, height=5)
        text.pack(fill="x")
        editor.update(text=text, fixed_box=fixed)

        def sync(*_):
            fixed.configure(values=clean_phrases(text.get("1.0", "end").splitlines()))
            fixed.state(["!disabled"] if editor["mode"].get() == MODES["fixed"] else ["disabled"])
        text.bind("<KeyRelease>", sync, add="+")
        text.bind("<FocusOut>", sync, add="+")
        editor["mode"].trace_add("write", sync)
        editor["sync"] = sync
        row = ttk.Frame(card, style="Card.TFrame")
        row.pack(fill="x", pady=(12, 0))
        AnimatedButton(row, text="▶  Тест", style="Accent.TButton", command=lambda: self._test_phrase(kind)).pack(side="left")
        ttk.Label(row, textvariable=editor["said"], style="Muted.Card.TLabel", wraplength=560).pack(side="left", padx=(12, 0))
        if kind == "insult":
            ttk.Label(card, text="Проверить текст на оскорбления", style="Section.TLabel").pack(anchor="w", pady=(18, 8))
            check = ttk.Frame(card, style="Card.TFrame")
            check.pack(fill="x")
            self.insult_check_var = tk.StringVar()
            entry = ttk.Entry(check, textvariable=self.insult_check_var)
            entry.pack(side="left", fill="x", expand=True)
            entry.bind("<Return>", lambda e: self._check_insult_text())
            AnimatedButton(check, text="Проверить", style="Secondary.TButton", command=self._check_insult_text).pack(side="left", padx=(10, 0))
            self.insult_check_result = ttk.Label(card, text="Вставь текст тикета — покажу найденные слова.", style="Muted.Card.TLabel", wraplength=760)
            self.insult_check_result.pack(anchor="w", pady=(8, 0))
        self._phrase_editors[kind] = editor

    def _fill_phrases_ui(self, data):
        self.voice_name_var.set(VOICES.get(str(data.get("voice")), VOICES[DEFAULT_VOICE]))
        for kind, editor in self._phrase_editors.items():
            section = dict(data.get(kind) or {})
            editor["mode"].set(MODES.get(str(section.get("mode")), MODES["random"]))
            editor["fixed"].set(" ".join(str(section.get("fixed", "")).split()))
            self._set_text(editor["text"], "\n".join(clean_phrases(section.get("phrases", []))), readonly=False)
            if "enabled" in editor:
                editor["enabled"].set(bool(section.get("enabled", True)))
            editor["said"].set("")
            editor["sync"]()
        self._phrases_snapshot = self._phrases_state()

    def _section_from_ui(self, kind):
        editor = self._phrase_editors[kind]
        mode = next((key for key, label in MODES.items() if label == editor["mode"].get()), "random")
        section = {"mode": mode, "fixed": " ".join(editor["fixed"].get().split()),
                   "phrases": clean_phrases(editor["text"].get("1.0", "end").splitlines())}
        if "enabled" in editor:
            section["enabled"] = bool(editor["enabled"].get())
        return section

    def _voice_from_ui(self):
        return next((key for key, label in VOICES.items() if label == self.voice_name_var.get()), DEFAULT_VOICE)

    def _phrases_state(self):
        return json.dumps([self._voice_from_ui()] + [self._section_from_ui(k) for k in sorted(self._phrase_editors)],
                          ensure_ascii=False, sort_keys=True)

    def _phrases_changed(self):
        return self._phrases_state() != getattr(self, "_phrases_snapshot", None)

    def _save_phrases_from_ui(self, quiet=False):
        data = self._phrases()  # keeps hand-written keys such as extra triggers and the cooldown
        for kind in self._phrase_editors:
            section = self._section_from_ui(kind)
            if not section["phrases"] and not section["fixed"]:
                self._toast("Добавь хотя бы одну фразу" + (" приветствия" if kind == "greeting" else " реакции"), "error")
                return False
            data[kind] = {**dict(data.get(kind) or {}), **section}
        data["voice"] = self._voice_from_ui()
        try:
            save_phrases(data)
        except OSError as exc:
            self._toast(f"Не удалось сохранить фразы: {exc}", "error")
            return False
        set_voice(data["voice"])
        self._phrases_snapshot = self._phrases_state()
        if not quiet:
            self._toast("Фразы и голос сохранены")
        return True

    def _reload_phrases_ui(self):
        self._phrases_error = ""
        self._fill_phrases_ui(self._phrases())
        self._toast("Фразы перечитаны из файла", "info")

    def _open_phrases_file(self):
        self._phrases()  # creates the file with defaults on first use
        try:
            if os.name == "nt":
                subprocess.Popen(["notepad.exe", str(PHRASES_PATH)])
            else:
                subprocess.Popen(["xdg-open", str(PHRASES_PATH)])
        except OSError as exc:
            self._toast(f"Не удалось открыть файл: {exc}", "error")

    def _test_voice(self):
        set_voice(self._voice_from_ui())
        speak("Привет! Так звучит мой голос.")

    def _test_phrase(self, kind):
        """Plays the phrase exactly as the current (even unsaved) settings would pick it."""
        set_voice(self._voice_from_ui())
        phrase = pick_phrase(self._section_from_ui(kind), "test_" + kind, "12345" if kind == "insult" else "")
        editor = self._phrase_editors[kind]
        if not phrase:
            editor["said"].set("Добавь хотя бы одну фразу")
            return
        editor["said"].set(f"Звучит: «{phrase}»")
        speak(phrase)

    def _check_insult_text(self):
        data = self._phrases()
        words = detect_insults(self.insult_check_var.get(), data.get("insult_triggers_extra") or [], data.get("insult_ignore") or [])
        if words:
            self.insult_check_result.configure(text="Найдено: " + ", ".join(words), foreground=self.palette["red"])
        elif self.insult_check_var.get().strip():
            self.insult_check_result.configure(text="Оскорблений не найдено", foreground=self.palette["green"])
        else:
            self.insult_check_result.configure(text="Вставь текст тикета — покажу найденные слова.", foreground=self.palette["muted"])
