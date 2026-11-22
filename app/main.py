from __future__ import annotations

import json
import logging
import os
import shutil
import queue
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import traceback
import webbrowser
import zipfile
from pathlib import Path
from tkinter import (
    BOTH,
    END,
    LEFT,
    RIGHT,
    X,
    Y,
    BooleanVar,
    Canvas,
    Checkbutton,
    PanedWindow,
    IntVar,
    Menu,
    StringVar,
    Text,
    Tk,
    Toplevel,
    filedialog,
    messagebox,
    simpledialog,
)
from tkinter import ttk
from ui_assets import AnimatedButton
from typing import Any, Optional

from config import APPDATA_ROOT, DB_PATH, LOG_DIR, SESSION_PATH, load_config, save_config
from core import Decision, KnowledgeBase, canonical_answer, is_noise_question, matches_learning_exclusion, normalize, parse_ticket, split_phrases
from local_ai import AIResult, LocalAIAnalyzer
from telegram_service import TelegramService
from reply_guard import source_key
from core import template_key
from tray import WindowsTray
from voice import VoiceCommands, list_microphones, speak

APP_VERSION = "2.9.9.6"
CHANGELOG_TEXT = """Ares Assistant 2.9.9.6

• Пять тем оформления и атмосфера: «Небесный экспресс», «Уютный вечер», «Цветение»,
  «Золотой час», «Плед и какао» (+ «Аврора»). Палитра в шапке или Ctrl+Shift+T.
• Защита шаблонов от удаления: замок у шаблона, режим «Защита», точный подсчёт
  перед удалением; защищённые не удаляются ни одной командой.
• Новое оформление «Aurora»: градиентные кнопки, плавные переходы, скользящее меню,
  анимированные счётчики, переключатели, новый Ctrl+K и уведомления.
• Живой голос William вместо «робота»; встроена рабочая edge-tts 7.3.1.
• Приветствия и реакции на оскорбления — из файла voice_phrases.json, несколько фраз,
  случайная или одна фиксированная, кнопки «Тест» в настройках.
• Распознавание тикетов с матом и оскорблениями (с обходами) и голосовое оповещение.
• Новый раздел «Перебаны» из приложенного SIMPLE-проекта: запуск, остановка,
  очередь, журнал, предпросмотр команды и расписание.
• Используется текущий Telegram-аккаунт: /p repunish → AresStaffRobot,
  разрешённые команды → AresMineRobot, затем подтверждение всех ID пачки.
• Причины: 1.1, 1.6, 1.8, 1.9, 1.10, 1.11, 2.7. Фильтр сохранён из исходника.
• Ручной запуск, каждые 5/10 часов или ежедневно по локальному времени ПК.
• Прогресс сохраняется отдельно для аккаунта; отправленные ID не дублируются.
• /p reset: порядок ID в подсказке бота больше не вызывает ложную ошибку.
  Чужие, пропущенные и повторяющиеся ID по-прежнему отклоняются.
• Мгновенная остановка после запуска освобождает кнопки и обработчик бота.

2.9.9.5

• Отдельный раздел «Снятия»: запуск, остановка, прогресс и журнал.
• /p reset получает список у @AresStaffRobot через текущий Telegram-аккаунт.
• Команды r grief pex user <ник> group set default — в @AresMineRobot.
• Между командами не менее 5 секунд, в том числе между списками.
• /p reset <ID> подтверждает отправленные команды; цикл идёт до пустого списка.
• Все части списка и правки собираются до отправки, старые сообщения пропускаются.
• Прогресс сохраняется отдельно для аккаунта; отправленные ID не повторяются.
• При неопределённой доставке есть явная кнопка проверки отправки.
• Общая пауза и отключение Telegram останавливают очередь.
• Снятия и /c list обращаются к StaffRobot по очереди.

2.9.9.4

• «Сбросить и возобновить» отменяет зависший запрос и заново подключает обработчик.
• Истечение 5/15 секунд означает уведомление о задержке; результат ждём до 60 секунд.
• Поздний ответ относится к исходному запросу и автоматически снимает блокировку.
• Пропущенные события проверяются по новым сообщениям и правкам в диалоге бота.
• Закрытый сервис не меняет статус новой сессии; кнопка доступна после сбоя.
• Сохранение настроек не перезаписывает состояние активного запроса.

2.9.9.3

• Длинный ответ /c list отправляется всеми частями по порядку.
• Количество аккаунтов и ников помогает дождаться продолжения списка.
• Сбор завершается после 3 секунд без новых частей; предел сбора — 60 секунд.
• Повторные сообщения не дублируются, правки заменяют собранную часть.
• Если список не завершён, полученные части отправляются с предупреждением.

2.9.9.2

• ПКМ по шаблону → «Удалить» с подтверждением по его названию.
• Удаляется только шаблон под курсором и его примеры; другие отметки остаются.
• ПКМ по заголовку и пустому месту не открывает меню удаления.

2.9.9.1

• «⚡ Запрос выполняется!» больше не пересылается как готовый ответ.
• Обработчик ждёт результат после служебного сообщения.
• Поддерживается замена служебного сообщения результатом через редактирование.
• Служебные сообщения не сбрасывают общий таймаут запроса.

2.9.9.0

• Команды /c list <число> <ник> из ЛС пользователя 123456789.
• Запрос к @AresStaffRobot и ответ в тот же диалог через текущую сессию.
• Настройки → Команды: включение, ID, таймаут и пересылка оригинала.
• Один запрос за раз; уведомления об ошибках и таймаут 15 секунд.
• После таймаута — проверка диалога и ручное возобновление.
• Пауза и отключение Ares останавливают обработчик команд.

2.9.8.0

• Плавная анимация кнопок; исправлены белые края при наведении.
• Чёткие значки разделов и фирменная иконка EXE, окна и трея.
• Раздел «Изменения» в меню «Программа» (Alt+9).
• До двух автоответов на тикет; после «не помогло» — ручной ответ.
• Для TG/VK-клиента тот же шаблон — до двух раз за последние 24 часа.
• Защита включается отдельно у каждого шаблона. Черновики лимит не расходуют.
• Ручной ответ сотрудника останавливает следующие автоответы на тикет.

2.9.7.0: графитовый интерфейс, Ctrl+K, поиск истории, удобный редактор шаблонов, исправление двойной вставки Ctrl+V."""
SOURCE_ROOT = Path(os.environ.get("ARES_ASSISTANT_HOME", Path(__file__).resolve().parents[1]))
SEED_PATH = SOURCE_ROOT / "data" / "log.ticket"
LAUNCHER_EXE = os.environ.get("ARES_LAUNCHER_EXE", "")
INSTALL_SOURCE_FILE = os.environ.get("ARES_INSTALL_SOURCE_FILE", "")
RUNTIME_ROOT = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "AresAssistantRuntime"
IPC_PORT = 45231

LOG_FILE = LOG_DIR / "app.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8")],
)
LOGGER = logging.getLogger("AresAssistant")


def install_exception_hook() -> None:
    def hook(exc_type, exc_value, exc_traceback):
        LOGGER.critical("Unhandled exception", exc_info=(exc_type, exc_value, exc_traceback))
        try:
            messagebox.showerror(
                "Ares Assistant — ошибка",
                f"Произошла непредвиденная ошибка:\n\n{exc_value}\n\nЛог: {LOG_FILE}",
            )
        except Exception:
            pass

    sys.excepthook = hook


class SingleInstance:
    def __init__(self, callback):
        self.callback = callback
        self.socket: Optional[socket.socket] = None
        self.thread: Optional[threading.Thread] = None

    def acquire(self) -> bool:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(("127.0.0.1", IPC_PORT))
        except OSError:
            try:
                sock.sendto(b"SHOW", ("127.0.0.1", IPC_PORT))
            finally:
                sock.close()
            return False
        self.socket = sock
        self.thread = threading.Thread(target=self._listen, name="SingleInstanceIPC", daemon=True)
        self.thread.start()
        return True

    def _listen(self) -> None:
        assert self.socket is not None
        while True:
            try:
                data, _ = self.socket.recvfrom(1024)
                if data == b"SHOW":
                    self.callback("show", None)
            except OSError:
                return
            except Exception:
                LOGGER.exception("IPC listener error")

    def close(self) -> None:
        if self.socket:
            try:
                self.socket.close()
            except Exception:
                pass


class LegacyAresAssistantApp:
    def __init__(self, root: Tk):
        self.root = root
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.config = load_config()
        self.knowledge = KnowledgeBase(DB_PATH, SEED_PATH)
        self.local_ai = LocalAIAnalyzer(self.config)
        self.ai_inflight: set[str] = set()
        self.telegram: Optional[TelegramService] = None
        self.voice: Optional[VoiceCommands] = None
        self.tray = WindowsTray(lambda command: self.emit("command", command))
        self.shutting_down = False
        self.uninstalling = False
        self.paused = False
        self.last_ticket: Optional[tuple[str, str, Decision]] = None
        self.history_data: dict[str, dict[str, Any]] = {}
        self.template_selected_id: Optional[int] = None
        self.template_checked_ids: set[int] = set()
        self.template_visible_ids: list[int] = []
        self.template_rows_cache: dict[int, dict[str, Any]] = {}
        self._template_search_job: Optional[str] = None
        self._template_drag_anchor: Optional[str] = None
        self._template_drag_base_selection: set[str] = set()
        self._template_drag_ctrl = False
        self._template_drag_moved = False
        self.microphone_map: dict[str, str] = {"Системный микрофон по умолчанию": ""}
        self.qr_dialog: Optional[Toplevel] = None
        self.qr_canvas: Optional[Canvas] = None

        self._configure_root()
        self._configure_style()
        self._create_variables()
        self._build_ui()
        self._install_global_edit_shortcuts()
        self._load_settings_into_ui()
        self._refresh_all()

        self.root.protocol("WM_DELETE_WINDOW", self.hide_window)
        self.root.after(100, self._drain_events)
        self.root.after(400, self.refresh_microphones)
        self.root.after(800, self._startup_services)
        if "--hidden" not in sys.argv and "--background" not in sys.argv:
            self.root.after(1200, self._maybe_show_whats_new)
        if "--hidden" in sys.argv or "--background" in sys.argv:
            self.root.withdraw()

    def emit(self, event: str, payload: Any = None) -> None:
        self.events.put((event, payload))

    def _configure_root(self) -> None:
        self.root.title("Ares Assistant")
        screen_w = max(1024, int(self.root.winfo_screenwidth()))
        screen_h = max(640, int(self.root.winfo_screenheight()))
        raw_geometry = str(self.config.get("last_window_geometry", "1440x900") or "1440x900")
        match = re.match(r"^(\d+)x(\d+)", raw_geometry)
        requested_w = int(match.group(1)) if match else 1440
        requested_h = int(match.group(2)) if match else 900
        width = min(requested_w, max(1024, screen_w - 36))
        height = min(requested_h, max(640, screen_h - 72))
        width = max(min(1120, screen_w - 20), width)
        height = max(min(700, screen_h - 40), height)
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(min(1120, max(960, screen_w - 20)), min(700, max(620, screen_h - 40)))
        self.root.configure(bg="#0A0F17")

    def _configure_style(self) -> None:
        """Modern single-accent dark theme tuned for Windows/HiDPI."""
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        # One restrained graphite/ink palette. Blue is the only accent.
        background = "#0A0F17"
        surface = "#101722"
        surface_2 = "#141E2B"
        surface_3 = "#182434"
        field = "#0C131D"
        border = "#243247"
        border_soft = "#1B2737"
        text = "#EAF0F7"
        text_soft = "#C3CFDD"
        muted = "#8797AB"
        accent = "#4E78B7"
        accent_hover = "#5B87C7"
        accent_pressed = "#3E679F"
        selection = "#294765"
        neutral = "#182333"
        neutral_hover = "#202E41"
        disabled = "#657286"

        self.palette = {
            "background": background,
            "surface": surface,
            "surface_high": surface_2,
            "surface_3": surface_3,
            "field": field,
            "border": border,
            "border_soft": border_soft,
            "text": text,
            "text_soft": text_soft,
            "muted": muted,
            "accent": accent,
            "accent_hover": accent_hover,
            "selection": selection,
            "neutral": neutral,
        }

        self.root.configure(bg=background)
        # Tk-backed popups/listboxes otherwise keep the Windows white defaults.
        self.root.option_add("*Font", "{Segoe UI} 10")
        self.root.option_add("*Listbox.background", field)
        self.root.option_add("*Listbox.foreground", text)
        self.root.option_add("*Listbox.selectBackground", selection)
        self.root.option_add("*Listbox.selectForeground", "#FFFFFF")
        self.root.option_add("*TCombobox*Listbox.background", field)
        self.root.option_add("*TCombobox*Listbox.foreground", text)
        self.root.option_add("*TCombobox*Listbox.selectBackground", selection)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#FFFFFF")

        style.configure(".", background=background, foreground=text, fieldbackground=field, font=("Segoe UI", 10))
        style.configure("TFrame", background=background)
        style.configure("Card.TFrame", background=surface)
        style.configure("Hero.TFrame", background=surface_2)
        style.configure("Toolbar.TFrame", background=surface)

        style.configure("TLabel", background=background, foreground=text)
        style.configure("Card.TLabel", background=surface, foreground=text_soft)
        style.configure("Hero.TLabel", background=surface_2, foreground=text)
        style.configure("Title.TLabel", background=background, foreground="#F7FAFD", font=("Segoe UI Semibold", 21))
        style.configure("HeroTitle.TLabel", background=surface_2, foreground="#F4F8FC", font=("Segoe UI Semibold", 16))
        style.configure("HeroSub.TLabel", background=surface_2, foreground=muted, font=("Segoe UI", 10))
        style.configure("Sub.TLabel", background=background, foreground=muted)
        style.configure("Status.TLabel", background=background, foreground="#B7C7DB", font=("Segoe UI Semibold", 10))
        style.configure("Pill.TLabel", background="#17263A", foreground="#C9D7E8", padding=(11, 6), font=("Segoe UI Semibold", 9))
        style.configure("Section.TLabel", background=surface, foreground="#F0F4F9", font=("Segoe UI Semibold", 11))
        style.configure("Muted.Card.TLabel", background=surface, foreground=muted)

        # Buttons: one accent + one neutral family. No green/red/amber backgrounds.
        style.configure("Accent.TButton", background=accent, foreground="#FFFFFF", borderwidth=0, focusthickness=0, padding=(14, 9), font=("Segoe UI Semibold", 10))
        style.map("Accent.TButton", background=[("pressed", accent_pressed), ("active", accent_hover), ("disabled", "#2B3544")], foreground=[("disabled", disabled)])
        style.configure("Secondary.TButton", background=neutral, foreground="#E4ECF5", borderwidth=0, focusthickness=0, padding=(12, 8))
        style.map("Secondary.TButton", background=[("pressed", "#15202E"), ("active", neutral_hover)], foreground=[("disabled", disabled)])
        style.configure("Ghost.TButton", background=surface, foreground="#BAC7D7", borderwidth=0, focusthickness=0, padding=(10, 7))
        style.map("Ghost.TButton", background=[("active", neutral_hover)], foreground=[("active", "#F2F6FA"), ("disabled", disabled)])
        style.configure("Success.TButton", background=neutral, foreground="#E4ECF5", borderwidth=0, focusthickness=0, padding=(12, 8))
        style.map("Success.TButton", background=[("active", neutral_hover)])
        style.configure("Warning.TButton", background=neutral, foreground="#E4ECF5", borderwidth=0, focusthickness=0, padding=(12, 8))
        style.map("Warning.TButton", background=[("active", neutral_hover)])
        style.configure("Danger.TButton", background=neutral, foreground="#DDBEC5", borderwidth=0, focusthickness=0, padding=(12, 8))
        style.map("Danger.TButton", background=[("active", "#282534")], foreground=[("active", "#F0D5DA")])

        style.configure("TNotebook", background=background, borderwidth=0, tabmargins=(0, 0, 0, 0))
        style.configure("TNotebook.Tab", background=surface, foreground="#8F9FB2", padding=(16, 10), borderwidth=0, focusthickness=0)
        style.map("TNotebook.Tab", background=[("selected", surface_3), ("active", "#131D2A")], foreground=[("selected", "#F5F8FC"), ("active", "#DCE5F0")])

        style.configure("Treeview", background=surface, fieldbackground=surface, foreground="#DDE6F0", rowheight=32, borderwidth=0, relief="flat")
        style.configure("Treeview.Heading", background=surface_3, foreground="#C7D3E2", relief="flat", font=("Segoe UI Semibold", 9), padding=(7, 8), borderwidth=0)
        style.map("Treeview", background=[("selected", selection)], foreground=[("selected", "#FFFFFF")])
        style.map("Treeview.Heading", background=[("active", "#1D2B3E")])

        style.configure("TEntry", fieldbackground=field, foreground="#F0F4F8", insertcolor="#F0F4F8", bordercolor=border, lightcolor=border, darkcolor=border, padding=8)
        style.map("TEntry", fieldbackground=[("disabled", "#111822")], foreground=[("disabled", disabled)], bordercolor=[("focus", accent)])
        style.configure("TCombobox", fieldbackground=field, background=field, foreground="#F0F4F8", arrowcolor="#AFC0D3", bordercolor=border, lightcolor=border, darkcolor=border, padding=7)
        style.map("TCombobox", fieldbackground=[("readonly", field), ("disabled", "#111822")], background=[("readonly", field)], foreground=[("readonly", "#F0F4F8"), ("disabled", disabled)], selectbackground=[("readonly", field)], selectforeground=[("readonly", "#F0F4F8")], bordercolor=[("focus", accent)])
        style.configure("TSpinbox", fieldbackground=field, background=field, foreground="#F0F4F8", arrowcolor="#AFC0D3", bordercolor=border, lightcolor=border, darkcolor=border, padding=6)
        style.map("TSpinbox", bordercolor=[("focus", accent)])

        # Using ttk checkbuttons instead of native Tk checkbuttons removes the white Windows indicator.
        style.configure("Dark.TCheckbutton", background=background, foreground=text_soft, padding=(0, 4), focusthickness=0)
        style.configure("DarkCard.TCheckbutton", background=surface, foreground=text_soft, padding=(0, 4), focusthickness=0)
        for check_style, bg in (("Dark.TCheckbutton", background), ("DarkCard.TCheckbutton", surface)):
            style.map(
                check_style,
                background=[("active", bg), ("disabled", bg)],
                foreground=[("active", "#F2F6FA"), ("disabled", disabled)],
                indicatorbackground=[("selected", accent), ("!selected", field), ("disabled", "#1A2230")],
                indicatorforeground=[("selected", "#FFFFFF"), ("!selected", field)],
            )

        style.configure("Card.TLabelframe", background=surface, foreground="#BBC9D9", bordercolor=border_soft, lightcolor=border_soft, darkcolor=border_soft, relief="solid", borderwidth=1)
        style.configure("Card.TLabelframe.Label", background=surface, foreground="#BBC9D9", font=("Segoe UI Semibold", 10))
        style.configure("TSeparator", background=border_soft)
        style.configure("TPanedwindow", background=border_soft, sashwidth=5)
        style.configure("Vertical.TScrollbar", background="#1A2636", troughcolor=surface, bordercolor=surface, arrowcolor="#8999AD", lightcolor="#1A2636", darkcolor="#1A2636")
        style.configure("Horizontal.TScrollbar", background="#1A2636", troughcolor=surface, bordercolor=surface, arrowcolor="#8999AD", lightcolor="#1A2636", darkcolor="#1A2636")

    def _create_variables(self) -> None:
        self.mode_var = StringVar(value="Черновик")
        self.status_var = StringVar(value="Ожидание подключения")
        self.monitor_button_var = StringVar(value="Подключить Telegram")
        self.pause_button_var = StringVar(value="Пауза")
        self.api_id_var = StringVar()
        self.api_hash_var = StringVar()
        self.source_chat_var = StringVar()
        self.response_chat_var = StringVar()
        self.command_relay_enabled_var = BooleanVar(value=True)
        self.command_relay_user_var = StringVar(value="123456789")
        self.command_relay_timeout_var = StringVar(value="15")
        self.command_relay_forward_var = BooleanVar(value=False)
        self.command_relay_status_var = StringVar(value="Подключи Telegram для обработки команд")
        self.draft_threshold_var = IntVar(value=80)
        self.auto_threshold_var = IntVar(value=96)
        self.auto_delay_var = StringVar(value="1.5")
        self.auto_limit_var = IntVar(value=6)
        self.local_ai_enabled_var = BooleanVar(value=False)
        self.local_ai_model_var = StringVar(value="qwen3.5:4b")
        self.local_ai_endpoint_var = StringVar(value="http://127.0.0.1:11434")
        self.local_ai_draft_threshold_var = IntVar(value=86)
        self.local_ai_auto_threshold_var = IntVar(value=94)
        self.local_ai_candidate_limit_var = IntVar(value=6)
        self.local_ai_status_var = StringVar(value="Локальный ИИ ещё не проверен")
        self.learning_enabled_var = BooleanVar(value=True)
        self.learning_noise_var = BooleanVar(value=True)
        self.learning_corrections_var = BooleanVar(value=True)
        self.learning_policy_var = StringVar(value="Умно: неизвестные + исправления")
        self.auto_close_enabled_var = BooleanVar(value=True)
        self.close_command_var = StringVar(value="")
        self.voice_enabled_var = BooleanVar(value=True)
        self.startup_voice_var = BooleanVar(value=True)
        self.wake_word_var = BooleanVar(value=False)
        self.autostart_var = BooleanVar(value=True)
        self.open_telegram_var = BooleanVar(value=True)
        self.microphone_var = StringVar(value="Системный микрофон по умолчанию")
        self.stats_var = StringVar(value="")
        self.last_ticket_var = StringVar(value="Тикетов пока нет")
        self.last_learned_var = StringVar(value="Пока ничего не обучено в этой сессии")

        self.template_name_var = StringVar()
        self.template_enabled_var = BooleanVar(value=True)
        self.template_match_mode_var = StringVar(value="Умный")
        self.template_priority_var = IntVar(value=0)
        self.template_reply_limit_var = BooleanVar(value=True)
        self.template_search_var = StringVar(value="")
        self.template_filter_var = StringVar(value="Все")
        self.template_selection_var = StringVar(value="Ничего не отмечено")
        self.template_summary_var = StringVar(value="")
        self.template_source_var = StringVar(value="Выбери шаблон слева или создай новый")

        self.simulator_status_var = StringVar(value="Готово к безопасной проверке")
        self.simulator_action_var = StringVar(value="Введи сообщение игрока и нажми «Проверить»")
        self.simulator_manual_name_var = StringVar(value="")
        self.simulator_busy = False
        self.simulator_candidate_map: dict[str, int] = {}
        self.simulator_last_question = ""
        self.training_candidate_map: dict[str, int] = {}
        self.training_selected_ticket_id = ""

    def _build_menu(self) -> None:
        menubar = Menu(self.root, tearoff=False)
        help_menu = Menu(menubar, tearoff=False)
        help_menu.add_command(label=f"Что нового в {APP_VERSION}", command=self.show_whats_new)
        help_menu.add_separator()
        help_menu.add_command(label="Экспорт обновления", command=self.export_update_bundle)
        help_menu.add_command(label="Импорт обновления", command=self.import_update_bundle)
        menubar.add_cascade(label="Справка", menu=help_menu)
        self.root.configure(menu=menubar)

    def _build_ui(self) -> None:
        self._build_menu()
        header = ttk.Frame(self.root)
        header.pack(fill=X, padx=20, pady=(16, 7))
        ttk.Label(header, text="Ares Assistant", style="Title.TLabel").pack(side=LEFT)
        ttk.Label(header, text=f"v{APP_VERSION}", style="Sub.TLabel").pack(side=LEFT, padx=(8, 0), pady=(7, 0))
        ttk.Label(header, textvariable=self.status_var, style="Status.TLabel").pack(side=RIGHT, pady=(5, 0))

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=BOTH, expand=True, padx=16, pady=(3, 14))
        self.dashboard_tab = ttk.Frame(self.notebook)
        self.simulator_tab = ttk.Frame(self.notebook)
        self.training_tab = ttk.Frame(self.notebook)
        self.templates_tab = ttk.Frame(self.notebook)
        self.ai_tab = ttk.Frame(self.notebook)
        self.history_tab = ttk.Frame(self.notebook)
        self.settings_tab = ttk.Frame(self.notebook)
        self.diagnostics_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.dashboard_tab, text="Главная")
        self.notebook.add(self.simulator_tab, text="Проверка")
        self.notebook.add(self.training_tab, text="Обучение")
        self.notebook.add(self.templates_tab, text="Шаблоны")
        self.notebook.add(self.ai_tab, text="Контекстный анализ")
        self.notebook.add(self.history_tab, text="История")
        self.notebook.add(self.settings_tab, text="Настройки")
        self.notebook.add(self.diagnostics_tab, text="Диагностика")

        self._build_dashboard()
        self._build_simulator()
        self._build_training()
        self._build_templates()
        self._build_local_ai()
        self._build_history()
        self._build_settings()
        self._build_diagnostics()

    def _card(self, parent, title: str) -> ttk.LabelFrame:
        return ttk.LabelFrame(parent, text=title, style="Card.TLabelframe", padding=13)

    def _text_widget(self, parent, height: int = 8, readonly: bool = False) -> Text:
        widget = Text(
            parent,
            height=height,
            width=1,
            bg="#0d1520",
            fg="#e8eef7",
            insertbackground="#e8eef7",
            selectbackground="#29435f",
            selectforeground="#f4f7fb",
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground="#243247",
            highlightcolor="#4E78B7",
            wrap="word",
            font=("Segoe UI", 10),
            padx=9,
            pady=9,
            undo=not readonly,
        )
        if readonly:
            widget.configure(state="disabled")
        self._bind_paste(widget)
        return widget

    def _bind_paste(self, widget) -> None:
        """Compatibility hook. Global edit shortcuts are installed once for every text field."""
        return

    def _install_global_edit_shortcuts(self) -> None:
        """Reliable Ctrl+A/C/V for every editable field, including Russian keyboard layout on Windows.

        Tk normally resolves shortcuts by the produced character, so Ctrl+V/C/A may stop working
        when the active layout is Russian. Windows still reports the physical keys as 86/67/65;
        we use those keycodes and perform the edit operation directly.
        """
        supported = {"text", "entry", "tentry", "spinbox", "tspinbox", "tcombobox"}

        def focused(event=None):
            widget = self.root.focus_get()
            if widget is None and event is not None:
                widget = getattr(event, "widget", None)
            if widget is None:
                return None
            try:
                if str(widget.winfo_class()).lower() not in supported:
                    return None
            except Exception:
                return None
            return widget

        def widget_state(widget) -> str:
            try:
                return str(widget.cget("state") or "normal").lower()
            except Exception:
                return "normal"

        def select_all(widget):
            if widget is None:
                return None
            try:
                cls = str(widget.winfo_class()).lower()
                if cls == "text":
                    widget.tag_remove("sel", "1.0", END)
                    widget.tag_add("sel", "1.0", "end-1c")
                    widget.mark_set("insert", "end-1c")
                    widget.see("insert")
                else:
                    widget.selection_range(0, END)
                    try:
                        widget.icursor(END)
                    except Exception:
                        pass
                return "break"
            except Exception:
                return None

        def copy_selection(widget):
            if widget is None:
                return None
            try:
                cls = str(widget.winfo_class()).lower()
                if cls == "text":
                    ranges = widget.tag_ranges("sel")
                    if len(ranges) < 2:
                        return "break"
                    value = widget.get(ranges[0], ranges[1])
                else:
                    try:
                        if not widget.selection_present():
                            return "break"
                    except Exception:
                        pass
                    first = int(widget.index("sel.first"))
                    last = int(widget.index("sel.last"))
                    value = str(widget.get())[first:last]
                self.root.clipboard_clear()
                self.root.clipboard_append(value)
                self.root.update_idletasks()
                return "break"
            except Exception:
                return None

        def paste_clipboard(widget):
            if widget is None:
                return None
            if widget_state(widget) in {"disabled", "readonly"}:
                return "break"
            try:
                value = self.root.clipboard_get()
            except Exception:
                return "break"
            try:
                cls = str(widget.winfo_class()).lower()
                if cls == "text":
                    ranges = widget.tag_ranges("sel")
                    if len(ranges) >= 2:
                        widget.delete(ranges[0], ranges[1])
                    widget.insert("insert", value)
                    widget.see("insert")
                else:
                    try:
                        if widget.selection_present():
                            widget.delete("sel.first", "sel.last")
                    except Exception:
                        pass
                    widget.insert("insert", value)
                return "break"
            except Exception:
                return None

        def ctrl_key(event):
            widget = focused(event)
            if widget is None:
                return None
            keycode = int(getattr(event, "keycode", 0) or 0) if os.name == "nt" else 0
            keysym = str(getattr(event, "keysym", "") or "").lower()
            # Physical A/C/V on Windows: 65/67/86. Cyrillic fallbacks: ф/с/м.
            if keycode == 65 or keysym in {"a", "cyrillic_ef"}:
                return select_all(widget)
            if keycode == 67 or keysym in {"c", "cyrillic_es"}:
                return copy_selection(widget)
            if keycode == 86 or keysym in {"v", "cyrillic_em"}:
                return paste_clipboard(widget)
            return None

        def shift_insert(event):
            return paste_clipboard(focused(event))

        try:
            # Run before Tk's native edit class. bind_all runs after that class,
            # which otherwise pastes once before our handler pastes a second time.
            edit_tag = "AresEditShortcuts"
            self.root.bind_class(edit_tag, "<Control-KeyPress>", ctrl_key)
            self.root.bind_class(edit_tag, "<Shift-Insert>", shift_insert)

            def tag_editor(widget):
                try:
                    if str(widget.winfo_class()).lower() in supported:
                        tags = tuple(widget.bindtags())
                        if edit_tag not in tags:
                            widget.bindtags((tags[0], edit_tag) + tags[1:])
                    for child in widget.winfo_children():
                        tag_editor(child)
                except Exception:
                    pass

            tag_editor(self.root)
            # Includes entries created later in search, QR and input dialogs.
            self.root.bind_all("<Map>", lambda event: tag_editor(event.widget), add="+")
        except Exception:
            LOGGER.exception("Unable to install global edit shortcuts")

    def _checkbutton(self, parent, text: str, variable: BooleanVar, card: bool = True, command=None) -> ttk.Checkbutton:
        return ttk.Checkbutton(
            parent,
            text=text,
            variable=variable,
            command=command,
            style="DarkCard.TCheckbutton" if card else "Dark.TCheckbutton",
            cursor="hand2",
        )

    def _build_dashboard(self) -> None:
        toolbar = ttk.Frame(self.dashboard_tab)
        toolbar.pack(fill=X, padx=8, pady=8)
        ttk.Label(toolbar, text="Режим:").pack(side=LEFT, padx=(0, 8))
        mode = ttk.Combobox(
            toolbar,
            textvariable=self.mode_var,
            values=("Черновик", "Автоответ", "Только обучение"),
            width=18,
            state="readonly",
        )
        mode.pack(side=LEFT)
        mode.bind("<<ComboboxSelected>>", lambda _: self._mode_changed())
        AnimatedButton(toolbar, textvariable=self.monitor_button_var, style="Accent.TButton", command=self.toggle_monitoring).pack(side=RIGHT)
        AnimatedButton(toolbar, textvariable=self.pause_button_var, style="Secondary.TButton", command=self.toggle_pause).pack(side=RIGHT, padx=(0, 8))

        stats_card = self._card(self.dashboard_tab, "Состояние базы")
        stats_card.pack(fill=X, padx=8, pady=(4, 8))
        ttk.Label(stats_card, textvariable=self.stats_var, style="Card.TLabel").pack(anchor="w")

        ticket_card = self._card(self.dashboard_tab, "Последний тикет")
        ticket_card.pack(fill=BOTH, expand=True, padx=8, pady=8)
        ttk.Label(ticket_card, textvariable=self.last_ticket_var, style="Card.TLabel", wraplength=1040, justify=LEFT).pack(fill=X, anchor="w")
        self.preview_text = self._text_widget(ticket_card, height=16, readonly=True)
        self.preview_text.pack(fill=BOTH, expand=True, pady=(10, 0))

        buttons = ttk.Frame(self.dashboard_tab)
        buttons.pack(fill=X, padx=8, pady=(0, 8))
        AnimatedButton(buttons, text="Открыть проверку ответа", style="Secondary.TButton", command=self.open_simulator).pack(side=LEFT)
        AnimatedButton(buttons, text="Повторно обработать последний", style="Secondary.TButton", command=self.retry_last_ticket).pack(side=LEFT, padx=8)
        AnimatedButton(buttons, text="Открыть @AresMineRobot", style="Secondary.TButton", command=self.open_response_chat).pack(side=RIGHT)

    def _build_simulator(self) -> None:
        hero = ttk.Frame(self.simulator_tab, style="Hero.TFrame", padding=16)
        hero.pack(fill=X, padx=10, pady=(12, 8))
        ttk.Label(hero, text="Песочница ответа", style="HeroTitle.TLabel").pack(anchor="w")
        ttk.Label(
            hero,
            text=(
                "Проверка полностью отделена от обучения. Здесь ничего не появляется автоматически: "
                "сам вставь текст сообщения игрока и нажми «Проверить». Песочница ничего не отправляет "
                "в Telegram, не закрывает тикеты и не меняет базу обучения."
            ),
            style="HeroSub.TLabel",
            wraplength=1100,
            justify=LEFT,
        ).pack(anchor="w", pady=(5, 0))

        pane = PanedWindow(
            self.simulator_tab,
            orient="horizontal",
            bd=0,
            relief="flat",
            bg="#263447",
            sashwidth=6,
            sashrelief="flat",
            showhandle=False,
            opaqueresize=True,
        )
        pane.pack(fill=BOTH, expand=True, padx=10, pady=(0, 10))
        left = self._card(pane, "Сообщение игрока")
        right = self._card(pane, "Что сделала бы программа")
        pane.add(left, minsize=460, stretch="always")
        pane.add(right, minsize=520, stretch="always")

        ttk.Label(
            left,
            text="Вставь сюда сообщение вручную. Реальные тикеты из Telegram сюда не подставляются.",
            style="Muted.Card.TLabel",
        ).pack(anchor="w", pady=(0, 6))
        self.simulator_input_text = self._text_widget(left, height=16)
        self.simulator_input_text.pack(fill=BOTH, expand=True)
        self.simulator_input_text.bind("<Control-Return>", lambda _e: (self.run_simulator(), "break")[1])

        input_actions = ttk.Frame(left, style="Card.TFrame")
        input_actions.pack(fill=X, pady=(9, 0))
        AnimatedButton(input_actions, text="Проверить  Ctrl+Enter", style="Accent.TButton", command=self.run_simulator).pack(side=LEFT)
        AnimatedButton(input_actions, text="Очистить", style="Secondary.TButton", command=self.clear_simulator).pack(side=RIGHT)

        ttk.Label(left, textvariable=self.simulator_status_var, style="Muted.Card.TLabel", wraplength=500, justify=LEFT).pack(anchor="w", pady=(9, 0))

        ttk.Label(right, textvariable=self.simulator_action_var, style="Section.TLabel", wraplength=620, justify=LEFT).pack(fill=X, anchor="w")
        self.simulator_summary_text = self._text_widget(right, height=8, readonly=True)
        self.simulator_summary_text.pack(fill=X, pady=(7, 9))
        self._set_text(self.simulator_summary_text, "Результат появится здесь.")

        result_tabs = ttk.Notebook(right)
        result_tabs.pack(fill=BOTH, expand=True)
        answer_page = ttk.Frame(result_tabs, style="Card.TFrame", padding=(4, 8))
        match_page = ttk.Frame(result_tabs, style="Card.TFrame", padding=(4, 8))
        candidates_page = ttk.Frame(result_tabs, style="Card.TFrame", padding=(4, 8))
        result_tabs.add(answer_page, text="Ответ")
        result_tabs.add(match_page, text="Почему")
        result_tabs.add(candidates_page, text="Кандидаты")

        ttk.Label(answer_page, text="Точный текст, который был бы подготовлен:", style="Card.TLabel").pack(anchor="w")
        self.simulator_answer_text = self._text_widget(answer_page, height=12, readonly=True)
        self.simulator_answer_text.pack(fill=BOTH, expand=True, pady=(5, 0))
        self._set_text(self.simulator_answer_text, "—")

        ttk.Label(match_page, text="Обоснование и ближайшая подтверждённая ситуация:", style="Card.TLabel").pack(anchor="w")
        self.simulator_reason_text = self._text_widget(match_page, height=12, readonly=True)
        self.simulator_reason_text.pack(fill=BOTH, expand=True, pady=(5, 0))
        self._set_text(self.simulator_reason_text, "—")

        candidate_holder = ttk.Frame(candidates_page, style="Card.TFrame")
        candidate_holder.pack(fill=BOTH, expand=True)
        self.simulator_candidates_tree = ttk.Treeview(
            candidate_holder,
            columns=("score", "name", "examples"),
            show="headings",
            selectmode="browse",
        )
        self.simulator_candidates_tree.heading("score", text="Предварительно")
        self.simulator_candidates_tree.heading("name", text="Шаблон")
        self.simulator_candidates_tree.heading("examples", text="Примеров")
        self.simulator_candidates_tree.column("score", width=115, stretch=False, anchor="center")
        self.simulator_candidates_tree.column("name", width=380)
        self.simulator_candidates_tree.column("examples", width=85, stretch=False, anchor="center")
        sim_scroll = ttk.Scrollbar(candidate_holder, orient="vertical", command=self.simulator_candidates_tree.yview)
        self.simulator_candidates_tree.configure(yscrollcommand=sim_scroll.set)
        self.simulator_candidates_tree.grid(row=0, column=0, sticky="nsew")
        sim_scroll.grid(row=0, column=1, sticky="ns")
        candidate_holder.rowconfigure(0, weight=1)
        candidate_holder.columnconfigure(0, weight=1)

        candidate_actions = ttk.Frame(candidates_page, style="Card.TFrame")
        candidate_actions.pack(fill=X, pady=(8, 0))
        AnimatedButton(
            candidate_actions,
            text="Открыть шаблон",
            style="Secondary.TButton",
            command=self.simulator_open_selected_template,
        ).pack(side=LEFT)
        ttk.Label(
            candidate_actions,
            text="Обучение выполняется только во вкладке «Обучение».",
            style="Muted.Card.TLabel",
        ).pack(side=LEFT, padx=(12, 0))
        self.simulator_candidates_tree.bind("<Double-1>", lambda _event: self.simulator_open_selected_template())

    def _build_training(self) -> None:
        hero = ttk.Frame(self.training_tab, style="Hero.TFrame", padding=16)
        hero.pack(fill=X, padx=10, pady=(12, 8))
        ttk.Label(hero, text="Умное обучение", style="HeroTitle.TLabel").pack(anchor="w")
        ttk.Label(
            hero,
            text=(
                "Здесь показываются только реальные тикеты, в которых программа не уверена. "
                "Выбери ближайший шаблон или напиши свой правильный ответ. После обучения тикет "
                "удаляется из очереди, а поля и кандидаты очищаются."
            ),
            style="HeroSub.TLabel",
            wraplength=1080,
            justify=LEFT,
        ).pack(anchor="w", pady=(5, 0))

        pane = PanedWindow(
            self.training_tab,
            orient="horizontal",
            bd=0,
            relief="flat",
            bg="#263447",
            sashwidth=6,
            sashrelief="flat",
            showhandle=False,
            opaqueresize=True,
        )
        pane.pack(fill=BOTH, expand=True, padx=10, pady=(0, 10))
        left = self._card(pane, "Очередь обучения")
        right = self._card(pane, "Решение тикета")
        pane.add(left, minsize=500, stretch="always")
        pane.add(right, minsize=520, stretch="always")

        queue_actions = ttk.Frame(left, style="Card.TFrame")
        queue_actions.pack(fill=X, pady=(0, 8))
        AnimatedButton(queue_actions, text="Удалить выбранные", style="Secondary.TButton", command=self.delete_pending).pack(side=LEFT)
        AnimatedButton(queue_actions, text="Очистить мусор", style="Secondary.TButton", command=self.cleanup_pending).pack(side=LEFT, padx=6)
        AnimatedButton(queue_actions, text="Удалить всё", style="Danger.TButton", command=self.delete_all_pending).pack(side=RIGHT)

        tree_holder = ttk.Frame(left, style="Card.TFrame")
        tree_holder.pack(fill=BOTH, expand=True)
        self.pending_tree = ttk.Treeview(
            tree_holder,
            columns=("ticket", "question", "date"),
            show="headings",
            selectmode="extended",
        )
        self.pending_tree.heading("ticket", text="Тикет")
        self.pending_tree.heading("question", text="Вопрос")
        self.pending_tree.heading("date", text="Обновлён")
        self.pending_tree.column("ticket", width=85, stretch=False)
        self.pending_tree.column("question", width=420)
        self.pending_tree.column("date", width=145, stretch=False)
        pending_scroll = ttk.Scrollbar(tree_holder, orient="vertical", command=self.pending_tree.yview)
        self.pending_tree.configure(yscrollcommand=pending_scroll.set)
        self.pending_tree.grid(row=0, column=0, sticky="nsew")
        pending_scroll.grid(row=0, column=1, sticky="ns")
        tree_holder.rowconfigure(0, weight=1)
        tree_holder.columnconfigure(0, weight=1)
        self.pending_tree.bind("<<TreeviewSelect>>", lambda _: self._pending_selected())
        self.pending_tree.bind("<Delete>", lambda _: self.delete_pending())

        details_notebook = ttk.Notebook(right)
        details_notebook.pack(fill=BOTH, expand=True)
        details_tab = ttk.Frame(details_notebook, style="Card.TFrame", padding=(4, 8))
        filters_tab = ttk.Frame(details_notebook, style="Card.TFrame", padding=(4, 8))
        close_filter_tab = ttk.Frame(details_notebook, style="Card.TFrame", padding=(4, 8))
        details_notebook.add(details_tab, text="Решение")
        details_notebook.add(filters_tab, text="Фильтр обучения")
        details_notebook.add(close_filter_tab, text="Автозакрытие")

        ttk.Label(details_tab, text="Полный вопрос:", style="Card.TLabel").pack(anchor="w")
        self.pending_question_text = self._text_widget(details_tab, height=7, readonly=True)
        self.pending_question_text.pack(fill=X, pady=(4, 8))

        solve_tabs = ttk.Notebook(details_tab)
        solve_tabs.pack(fill=BOTH, expand=True)
        candidates_tab = ttk.Frame(solve_tabs, style="Card.TFrame", padding=(5, 7))
        manual_tab = ttk.Frame(solve_tabs, style="Card.TFrame", padding=(8, 9))
        last_tab = ttk.Frame(solve_tabs, style="Card.TFrame", padding=(8, 9))
        solve_tabs.add(candidates_tab, text="Ближайшие кандидаты")
        solve_tabs.add(manual_tab, text="Свой ответ")
        solve_tabs.add(last_tab, text="Последнее сохранение")

        ttk.Label(
            candidates_tab,
            text="Выбери правильный шаблон. Процент — только предварительная близость; решение подтверждаешь ты.",
            style="Muted.Card.TLabel",
            wraplength=600,
            justify=LEFT,
        ).pack(anchor="w", pady=(0, 6))
        training_candidate_holder = ttk.Frame(candidates_tab, style="Card.TFrame")
        training_candidate_holder.pack(fill=BOTH, expand=True)
        self.training_candidates_tree = ttk.Treeview(
            training_candidate_holder,
            columns=("score", "name", "examples"),
            show="headings",
            selectmode="browse",
            height=7,
        )
        self.training_candidates_tree.heading("score", text="Близость")
        self.training_candidates_tree.heading("name", text="Шаблон")
        self.training_candidates_tree.heading("examples", text="Примеров")
        self.training_candidates_tree.column("score", width=90, stretch=False, anchor="center")
        self.training_candidates_tree.column("name", width=360)
        self.training_candidates_tree.column("examples", width=80, stretch=False, anchor="center")
        training_scroll = ttk.Scrollbar(training_candidate_holder, orient="vertical", command=self.training_candidates_tree.yview)
        self.training_candidates_tree.configure(yscrollcommand=training_scroll.set)
        self.training_candidates_tree.grid(row=0, column=0, sticky="nsew")
        training_scroll.grid(row=0, column=1, sticky="ns")
        training_candidate_holder.rowconfigure(0, weight=1)
        training_candidate_holder.columnconfigure(0, weight=1)
        self.training_candidates_tree.bind("<<TreeviewSelect>>", lambda _e: self._training_candidate_selected())
        self.training_candidates_tree.bind("<Double-1>", lambda _e: self.training_teach_selected())

        ttk.Label(candidates_tab, text="Ответ выбранного шаблона:", style="Card.TLabel").pack(anchor="w", pady=(7, 3))
        self.training_candidate_answer_text = self._text_widget(candidates_tab, height=4, readonly=True)
        self.training_candidate_answer_text.pack(fill=X)
        training_actions = ttk.Frame(candidates_tab, style="Card.TFrame")
        training_actions.pack(fill=X, pady=(7, 0))
        AnimatedButton(
            training_actions,
            text="Это правильный шаблон — обучить",
            style="Accent.TButton",
            command=self.training_teach_selected,
        ).pack(side=LEFT)
        AnimatedButton(
            training_actions,
            text="Открыть шаблон",
            style="Secondary.TButton",
            command=self.training_open_selected_template,
        ).pack(side=LEFT, padx=(8, 0))

        ttk.Label(
            manual_tab,
            text="Если правильного варианта среди кандидатов нет — вставь свой ответ. Можно только текст или полную команду t a ID ... @@@.",
            style="Muted.Card.TLabel",
            wraplength=610,
            justify=LEFT,
        ).pack(anchor="w", pady=(0, 8))
        training_name_row = ttk.Frame(manual_tab, style="Card.TFrame")
        training_name_row.pack(fill=X, pady=(0, 8))
        ttk.Label(training_name_row, text="Название шаблона (необязательно):", style="Card.TLabel").pack(side=LEFT)
        self.training_manual_name_var = StringVar(value="")
        self.training_manual_name_entry = ttk.Entry(training_name_row, textvariable=self.training_manual_name_var)
        self.training_manual_name_entry.pack(side=LEFT, fill=X, expand=True, padx=(10, 0))
        self._bind_paste(self.training_manual_name_entry)
        self.training_manual_answer_text = self._text_widget(manual_tab, height=9)
        self.training_manual_answer_text.pack(fill=BOTH, expand=True)
        training_manual_actions = ttk.Frame(manual_tab, style="Card.TFrame")
        training_manual_actions.pack(fill=X, pady=(8, 0))
        AnimatedButton(
            training_manual_actions,
            text="Создать шаблон и обучить",
            style="Accent.TButton",
            command=self.training_create_manual_template,
        ).pack(side=LEFT)
        AnimatedButton(
            training_manual_actions,
            text="Очистить",
            style="Secondary.TButton",
            command=lambda: (self.training_manual_answer_text.delete("1.0", END), self.training_manual_name_var.set("")),
        ).pack(side=LEFT, padx=(8, 0))

        ttk.Label(last_tab, text="Последний автоматически сохранённый ответ:", style="Card.TLabel").pack(anchor="w")
        self.learned_answer_text = self._text_widget(last_tab, height=11, readonly=True)
        self.learned_answer_text.pack(fill=BOTH, expand=True, pady=(5, 8))
        self._set_text(self.learned_answer_text, "Пока новых решений в этой сессии нет.")
        AnimatedButton(last_tab, text="Открыть @AresMineRobot", style="Secondary.TButton", command=self.open_response_chat).pack(anchor="w")

        ttk.Label(
            filters_tab,
            text="Не добавлять в обучение, если вопрос содержит одну из этих фраз · по одной на строку",
            style="Card.TLabel",
            wraplength=520,
            justify=LEFT,
        ).pack(anchor="w")
        self.learning_exclusions_text = self._text_widget(filters_tab, height=10)
        self.learning_exclusions_text.pack(fill=BOTH, expand=True, pady=(5, 9))
        self._checkbutton(
            filters_tab,
            "Отбрасывать служебные заголовки, даты, ID и сообщения без вопроса игрока",
            self.learning_noise_var,
            card=True,
        ).pack(anchor="w", pady=3)
        policy_box = ttk.Frame(filters_tab, style="Card.TFrame")
        policy_box.pack(fill=X, pady=(8, 2))
        ttk.Label(policy_box, text="Что сохранять в обучение", style="Card.TLabel").pack(side=LEFT)
        policy_combo = ttk.Combobox(
            policy_box,
            textvariable=self.learning_policy_var,
            values=(
                "Умно: неизвестные + исправления",
                "Только неизвестные",
                "Все ручные ответы",
            ),
            state="readonly",
            width=34,
        )
        policy_combo.pack(side=RIGHT, fill=X, expand=True, padx=(12, 0))
        ttk.Label(
            filters_tab,
            text=(
                "Рекомендуется «Умно»: уже известный правильный ответ повторно не записывается. "
                "Сохраняются только неизвестные ситуации и реальные исправления неверного решения."
            ),
            style="Muted.Card.TLabel",
            wraplength=520,
            justify=LEFT,
        ).pack(anchor="w", pady=(3, 4))

        ttk.Label(
            close_filter_tab,
            text="Закрывать тикет сразу, если сообщение содержит одну из этих фраз · по одной на строку",
            style="Card.TLabel",
            wraplength=520,
            justify=LEFT,
        ).pack(anchor="w")
        ttk.Label(
            close_filter_tab,
            text=(
                "Это глобальный фильтр. Совпадение проверяется по границам слов и имеет приоритет над ответами. "
                "При срабатывании программа отправляет t c <номер тикета>."
            ),
            style="Muted.Card.TLabel",
            wraplength=520,
            justify=LEFT,
        ).pack(anchor="w", pady=(4, 8))
        self.global_close_text = self._text_widget(close_filter_tab, height=12)
        self.global_close_text.pack(fill=BOTH, expand=True, pady=(0, 8))
        self._checkbutton(
            close_filter_tab,
            "Разрешить правила автозакрытия",
            self.auto_close_enabled_var,
            card=True,
        ).pack(anchor="w", pady=(2, 7))
        AnimatedButton(
            close_filter_tab,
            text="Сохранить фильтры",
            style="Accent.TButton",
            command=self.save_learning_filters,
        ).pack(anchor="e")
        filter_actions = ttk.Frame(filters_tab, style="Card.TFrame")
        filter_actions.pack(fill=X, pady=(10, 0))
        AnimatedButton(filter_actions, text="Сохранить фильтр", style="Accent.TButton", command=self.save_learning_filters).pack(side=LEFT)
        AnimatedButton(filter_actions, text="Применить и очистить очередь", style="Secondary.TButton", command=self.cleanup_pending).pack(side=LEFT, padx=6)

    def _build_templates(self) -> None:
        hero = ttk.Frame(self.templates_tab, style="Hero.TFrame", padding=18)
        hero.pack(fill=X, padx=10, pady=(12, 8))
        hero_left = ttk.Frame(hero, style="Hero.TFrame")
        hero_left.pack(side=LEFT, fill=X, expand=True)
        ttk.Label(hero_left, text="Центр шаблонов", style="HeroTitle.TLabel").pack(anchor="w")
        ttk.Label(
            hero_left,
            text=(
                "Управляй автоответами как библиотекой: ищи, отмечай, массово включай или удаляй. "
                "Короткие контекстные ответы больше не превращаются в опасные общие шаблоны — "
                "они сохраняются как точные правила и срабатывают только на ту же ситуацию."
            ),
            style="HeroSub.TLabel",
            wraplength=860,
            justify=LEFT,
        ).pack(anchor="w", pady=(5, 0))
        ttk.Label(hero, textvariable=self.template_summary_var, style="Pill.TLabel").pack(side=RIGHT, padx=(16, 0))

        toolbar = self._card(self.templates_tab, "Поиск и быстрые действия")
        toolbar.pack(fill=X, padx=10, pady=(0, 8))
        search_row = ttk.Frame(toolbar, style="Card.TFrame")
        search_row.pack(fill=X)
        ttk.Label(search_row, text="Поиск", style="Card.TLabel").pack(side=LEFT)
        search_entry = ttk.Entry(search_row, textvariable=self.template_search_var)
        search_entry.pack(side=LEFT, fill=X, expand=True, padx=(8, 10))
        search_entry.bind("<Escape>", lambda _: self.template_search_var.set(""))
        filter_box = ttk.Combobox(
            search_row,
            textvariable=self.template_filter_var,
            values=("Все", "Включённые", "Отключённые", "Мои", "Исходные"),
            state="readonly",
            width=16,
        )
        filter_box.pack(side=LEFT, padx=(0, 10))
        filter_box.bind("<<ComboboxSelected>>", lambda _: self._refresh_templates())
        AnimatedButton(search_row, text="＋ Новый шаблон", style="Accent.TButton", command=self.new_template).pack(side=RIGHT)
        self.template_search_var.trace_add("write", self._schedule_template_refresh)

        bulk_select_row = ttk.Frame(toolbar, style="Card.TFrame")
        bulk_select_row.pack(fill=X, pady=(9, 0))
        ttk.Label(bulk_select_row, text="Выбор:", style="Muted.Card.TLabel").pack(side=LEFT, padx=(0, 6))
        AnimatedButton(bulk_select_row, text="Отметить видимые", style="Ghost.TButton", command=lambda: self._mark_visible_templates(True)).pack(side=LEFT)
        AnimatedButton(bulk_select_row, text="Снять", style="Ghost.TButton", command=lambda: self._mark_visible_templates(False)).pack(side=LEFT, padx=4)
        AnimatedButton(bulk_select_row, text="Инвертировать", style="Ghost.TButton", command=self._invert_visible_templates).pack(side=LEFT)
        ttk.Label(bulk_select_row, textvariable=self.template_selection_var, style="Muted.Card.TLabel").pack(side=RIGHT)

        bulk_action_row = ttk.Frame(toolbar, style="Card.TFrame")
        bulk_action_row.pack(fill=X, pady=(5, 0))
        ttk.Label(bulk_action_row, text="Действия:", style="Muted.Card.TLabel").pack(side=LEFT, padx=(0, 6))
        AnimatedButton(bulk_action_row, text="Включить отмеченные", style="Secondary.TButton", command=lambda: self._set_marked_templates_enabled(True)).pack(side=LEFT)
        AnimatedButton(bulk_action_row, text="Выключить отмеченные", style="Secondary.TButton", command=lambda: self._set_marked_templates_enabled(False)).pack(side=LEFT, padx=6)
        AnimatedButton(bulk_action_row, text="Удалить отмеченные", style="Danger.TButton", command=self._delete_marked_templates).pack(side=LEFT)

        # Native PanedWindow is used here deliberately: ttk.Panedwindow could
        # collapse the first pane to zero width on some Windows scaling setups.
        pane = PanedWindow(
            self.templates_tab,
            orient="horizontal",
            bd=0,
            relief="flat",
            bg="#263447",
            sashwidth=8,
            sashrelief="flat",
            showhandle=False,
            opaqueresize=True,
        )
        pane.pack(fill=BOTH, expand=True, padx=10, pady=(0, 10))
        left = self._card(pane, "Библиотека ответов")
        right = self._card(pane, "Редактор шаблона")
        pane.add(left, minsize=430, stretch="always")
        pane.add(right, minsize=520, stretch="always")
        self.template_split_pane = pane

        left_actions = ttk.Frame(left, style="Card.TFrame")
        left_actions.pack(fill=X, pady=(0, 9))
        left_actions_top = ttk.Frame(left_actions, style="Card.TFrame")
        left_actions_top.pack(fill=X)
        AnimatedButton(left_actions_top, text="Включить все", style="Secondary.TButton", command=lambda: self._set_all_templates_enabled(True)).pack(side=LEFT, fill=X, expand=True)
        AnimatedButton(left_actions_top, text="Выключить все", style="Secondary.TButton", command=lambda: self._set_all_templates_enabled(False)).pack(side=LEFT, fill=X, expand=True, padx=(6, 0))
        left_actions_bottom = ttk.Frame(left_actions, style="Card.TFrame")
        left_actions_bottom.pack(fill=X, pady=(6, 0))
        AnimatedButton(left_actions_bottom, text="Вернуть базовые", style="Ghost.TButton", command=self._restore_seed_templates).pack(side=LEFT, fill=X, expand=True)
        AnimatedButton(left_actions_bottom, text="Удалить всё", style="Danger.TButton", command=self._delete_all_templates).pack(side=LEFT, fill=X, expand=True, padx=(6, 0))

        tree_holder = ttk.Frame(left, style="Card.TFrame")
        tree_holder.pack(fill=BOTH, expand=True)
        self.templates_tree = ttk.Treeview(
            tree_holder,
            columns=("marked", "status", "name", "priority", "mode", "examples", "manual"),
            show="headings",
            selectmode="extended",
            style="Treeview",
        )
        columns = (
            ("marked", "☑", 44, False),
            ("status", "Статус", 84, False),
            ("name", "Название шаблона", 215, True),
            ("priority", "Приор.", 62, False),
            ("mode", "Режим", 76, False),
            ("examples", "Прим.", 56, False),
            ("manual", "Твоих", 54, False),
        )
        for column, title, width, stretch in columns:
            self.templates_tree.heading(column, text=title)
            self.templates_tree.column(column, width=width, minwidth=width if not stretch else 120, stretch=stretch, anchor="center" if column != "name" else "w")
        tree_scroll = ttk.Scrollbar(tree_holder, orient="vertical", command=self.templates_tree.yview)
        tree_scroll_x = ttk.Scrollbar(tree_holder, orient="horizontal", command=self.templates_tree.xview)
        self.templates_tree.configure(yscrollcommand=tree_scroll.set, xscrollcommand=tree_scroll_x.set)
        tree_holder.rowconfigure(0, weight=1)
        tree_holder.columnconfigure(0, weight=1)
        self.templates_tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll.grid(row=0, column=1, sticky="ns")
        tree_scroll_x.grid(row=1, column=0, sticky="ew")

        self.templates_tree.bind("<<TreeviewSelect>>", lambda _: self._template_selected())
        self.templates_tree.bind("<ButtonPress-1>", self._template_tree_press, add="+")
        self.templates_tree.bind("<B1-Motion>", self._template_tree_drag, add="+")
        self.templates_tree.bind("<ButtonRelease-1>", self._template_tree_release, add="+")
        self.templates_tree.bind("<Double-1>", lambda _: self._toggle_focused_template_enabled())
        self.templates_tree.bind("<space>", self._toggle_selected_template_marks)
        self.templates_tree.bind("<Delete>", lambda _: self._delete_marked_templates())
        self.templates_tree.bind("<Control-a>", self._select_all_visible_templates)
        self.templates_tree.bind("<Button-3>", self._template_context_menu)
        self.templates_tree.tag_configure("enabled", background="#101722", foreground="#DDE6F0")
        self.templates_tree.tag_configure("disabled", background="#0E151F", foreground="#718095")
        self.templates_tree.tag_configure("manual", background="#101722", foreground="#DDE6F0")

        ttk.Label(
            left,
            text=(
                "Клик — выбрать · Ctrl/Shift — добавить или выбрать диапазон · "
                "протяни мышью — выделить несколько · Пробел — поставить галочки · Delete — удалить"
            ),
            style="Card.TLabel",
            wraplength=560,
            justify=LEFT,
        ).pack(fill=X, pady=(8, 0))

        editor_head = ttk.Frame(right, style="Card.TFrame")
        editor_head.pack(fill=X)
        name_block = ttk.Frame(editor_head, style="Card.TFrame")
        name_block.pack(side=LEFT, fill=X, expand=True)
        ttk.Label(name_block, text="Название шаблона", style="Card.TLabel").pack(anchor="w")
        ttk.Entry(name_block, textvariable=self.template_name_var).pack(fill=X, pady=(4, 0))
        self._checkbutton(
            editor_head,
            "Шаблон включён",
            self.template_enabled_var,
            card=True,
        ).pack(side=RIGHT, padx=(12, 0), pady=(20, 0))

        meta_row = ttk.Frame(right, style="Card.TFrame")
        meta_row.pack(fill=X, pady=(9, 7))
        ttk.Label(meta_row, text="Режим совпадения:", style="Card.TLabel").pack(side=LEFT)
        ttk.Combobox(
            meta_row,
            textvariable=self.template_match_mode_var,
            values=("Умный", "Только точное совпадение"),
            state="readonly",
            width=26,
        ).pack(side=LEFT, padx=(8, 12))
        ttk.Label(meta_row, text="Приоритет:", style="Card.TLabel").pack(side=LEFT)
        ttk.Spinbox(meta_row, from_=0, to=100, textvariable=self.template_priority_var, width=6).pack(side=LEFT, padx=(6, 12))
        ttk.Label(meta_row, textvariable=self.template_source_var, style="Card.TLabel").pack(side=LEFT)
        ttk.Label(
            right,
            text="Если в одном тикете подходят несколько шаблонов, больший приоритет выигрывает спор. 0 — обычный, 100 — максимальный.",
            style="Muted.Card.TLabel",
            wraplength=880,
            justify=LEFT,
        ).pack(fill=X, pady=(0, 7))

        editor_actions = ttk.Frame(right, style="Card.TFrame")
        editor_actions.pack(fill=X, pady=(0, 8))
        AnimatedButton(
            editor_actions,
            text="Очистить подозрительные",
            style="Ghost.TButton",
            command=self._cleanup_suspicious_templates,
        ).pack(side=LEFT)
        AnimatedButton(
            editor_actions,
            text="Новый",
            style="Secondary.TButton",
            command=self.new_template,
        ).pack(side=RIGHT, padx=(6, 0))
        AnimatedButton(
            editor_actions,
            text="Сохранить изменения",
            style="Accent.TButton",
            command=self.save_template,
        ).pack(side=RIGHT)

        editor_notebook = ttk.Notebook(right)
        editor_notebook.pack(fill=BOTH, expand=True, pady=(2, 0))
        answer_tab = ttk.Frame(editor_notebook, style="Card.TFrame", padding=(2, 8))
        rules_tab = ttk.Frame(editor_notebook, style="Card.TFrame", padding=(2, 8))
        examples_tab = ttk.Frame(editor_notebook, style="Card.TFrame", padding=(2, 8))
        editor_notebook.add(answer_tab, text="Текст ответа")
        editor_notebook.add(rules_tab, text="Триггеры, запреты и закрытие")
        editor_notebook.add(examples_tab, text="Обучающие ситуации")
        self.template_editor_notebook = editor_notebook

        ttk.Label(
            answer_tab,
            text="Полный текст ответа · номер всегда {ticket_id}",
            style="Card.TLabel",
        ).pack(anchor="w", pady=(0, 5))
        self.template_answer_text = self._text_widget(answer_tab, height=11)
        self.template_answer_text.pack(fill=BOTH, expand=True)
        ttk.Label(
            answer_tab,
            text="Ответ должен начинаться с «t a {ticket_id}» и заканчиваться на «@@@».",
            style="Card.TLabel",
        ).pack(anchor="w", pady=(6, 0))

        rule_tabs = ttk.Notebook(rules_tab)
        rule_tabs.pack(fill=BOTH, expand=True)

        trigger_page = ttk.Frame(rule_tabs, style="Card.TFrame", padding=10)
        stop_page = ttk.Frame(rule_tabs, style="Card.TFrame", padding=10)
        close_page = ttk.Frame(rule_tabs, style="Card.TFrame", padding=10)
        rule_tabs.add(trigger_page, text="Триггеры ответа")
        rule_tabs.add(stop_page, text="Стоп-фразы")
        rule_tabs.add(close_page, text="Автозакрытие")

        ttk.Label(trigger_page, text="Триггеры ответа", style="Section.TLabel").pack(anchor="w")
        ttk.Label(
            trigger_page,
            text=(
                "По одной фразе на строку. Триггеры только помогают найти кандидата — одно общее слово "
                "не может самостоятельно выбрать автоответ. Финальное решение проверяется по всему контексту. "
                "Точки, запятые, двоеточия, /, _, +, - и другие символы сохраняются как введены."
            ),
            style="Muted.Card.TLabel",
            wraplength=720,
            justify=LEFT,
        ).pack(anchor="w", pady=(3, 8))
        self.template_triggers_text = self._text_widget(trigger_page, height=12)
        self.template_triggers_text.pack(fill=BOTH, expand=True)

        ttk.Label(stop_page, text="Стоп-фразы ответа", style="Section.TLabel").pack(anchor="w")
        ttk.Label(
            stop_page,
            text=(
                "Если вопрос содержит одну из этих фраз, этот конкретный шаблон исключается из кандидатов, "
                "даже если остальные слова похожи. Символы в строке сохраняются без удаления."
            ),
            style="Muted.Card.TLabel",
            wraplength=720,
            justify=LEFT,
        ).pack(anchor="w", pady=(3, 8))
        self.template_negative_text = self._text_widget(stop_page, height=12)
        self.template_negative_text.pack(fill=BOTH, expand=True)

        ttk.Label(close_page, text="Автозакрытие тикета", style="Section.TLabel").pack(anchor="w")
        ttk.Label(
            close_page,
            text=(
                "По одной фразе на строку. При обнаружении такой фразы тикет закрывается без автоответа. "
                "Это отдельное точное правило и не зависит от обычных триггеров ответа. Символы сохраняются как введены."
            ),
            style="Muted.Card.TLabel",
            wraplength=720,
            justify=LEFT,
        ).pack(anchor="w", pady=(3, 8))
        self.template_close_text = self._text_widget(close_page, height=12)
        self.template_close_text.pack(fill=BOTH, expand=True)

        examples_head = ttk.Frame(examples_tab, style="Card.TFrame")
        examples_head.pack(fill=X, pady=(0, 5))
        ttk.Label(
            examples_head,
            text="Ситуации, на которых обучен шаблон",
            style="Card.TLabel",
        ).pack(side=LEFT)
        ttk.Label(
            examples_head,
            text="Твои примеры имеют больший вес",
            style="Card.TLabel",
        ).pack(side=RIGHT)
        self.template_examples_text = self._text_widget(examples_tab, height=12, readonly=True)
        self.template_examples_text.pack(fill=BOTH, expand=True)

        self.root.after_idle(lambda: self._set_templates_sash(pane))
        self.root.after(500, lambda: self._set_templates_sash(pane))

    def _build_history(self) -> None:
        toolbar = ttk.Frame(self.history_tab)
        toolbar.pack(fill=X, padx=8, pady=(10, 6))
        AnimatedButton(toolbar, text="Обновить", style="Secondary.TButton", command=self._refresh_history).pack(side=LEFT)
        AnimatedButton(toolbar, text="Экспорт базы", style="Secondary.TButton", command=self.export_knowledge).pack(side=LEFT, padx=8)
        AnimatedButton(toolbar, text="Снять отметку с выбранного тикета", style="Secondary.TButton", command=self.unmark_selected_history).pack(side=RIGHT)

        self.history_tree = ttk.Treeview(
            self.history_tab,
            columns=("ticket", "action", "confidence", "question", "date"),
            show="headings",
            selectmode="browse",
        )
        for column, title, width in (
            ("ticket", "Тикет", 80),
            ("action", "Действие", 145),
            ("confidence", "Уверенность", 95),
            ("question", "Вопрос", 610),
            ("date", "Дата", 150),
        ):
            self.history_tree.heading(column, text=title)
            self.history_tree.column(column, width=width, stretch=column == "question")
        self.history_tree.pack(fill=BOTH, expand=True, padx=8, pady=(0, 8))
        self.history_tree.bind("<Double-1>", lambda _: self.show_history_details())

    def _build_local_ai(self) -> None:
        hero = ttk.Frame(self.ai_tab, style="Hero.TFrame", padding=18)
        hero.pack(fill=X, padx=12, pady=(12, 8))
        ttk.Label(hero, text="Быстрый контекстный анализ", style="HeroTitle.TLabel").pack(anchor="w")
        ttk.Label(
            hero,
            text=(
                "Ollama больше не участвует в выборе ответа. Программа разбирает весь текст локально: "
                "якорное слово, соседние слова, действие игрока, похожие подтверждённые ситуации и "
                "конфликтующие признаки. Это работает мгновенно и без внешней модели."
            ),
            style="HeroSub.TLabel",
            wraplength=1000,
            justify=LEFT,
        ).pack(anchor="w", pady=(5, 0))

        card = self._card(self.ai_tab, "Как выбирается шаблон")
        card.pack(fill=BOTH, expand=True, padx=12, pady=(0, 12))
        text = (
            "1. Общие слова сами по себе ничего не выбирают. Например, слово «аккаунт» — только якорь.\n\n"
            "2. Программа смотрит слова рядом. «аккаунт забанили» = наказание/жалоба; "
            "«аккаунт потерял доступ» = восстановление; «аккаунт привязать другой» = привязка.\n\n"
            "3. Затем весь вопрос сравнивается с твоими подтверждёнными примерами из базы. Чем чаще похожая "
            "ситуация решалась одним шаблоном, тем выше его вес.\n\n"
            "4. Если смысл конфликтует с шаблоном или два варианта почти равны — программа не угадывает и "
            "оставляет тикет на ручной ответ.\n\n"
            "5. Автозакрытие работает только по точным фразам, которые ты сам добавил. Для закрытия нет fuzzy-поиска."
        )
        ttk.Label(card, text=text, style="Card.TLabel", wraplength=1050, justify=LEFT).pack(anchor="nw")
        ttk.Label(
            card,
            text="Режим: контекстный движок 2.9.6.1 · первичная привязка отделена от перепривязки",
            style="Muted.Card.TLabel",
        ).pack(anchor="w", pady=(16, 0))

    def _build_settings(self) -> None:
        # Settings can be taller than a 720/768p screen, so the page is scrollable.
        shell = ttk.Frame(self.settings_tab)
        shell.pack(fill=BOTH, expand=True, padx=8, pady=8)
        canvas = Canvas(
            shell,
            bg=self.palette.get("background", "#0A0F17"),
            highlightthickness=0,
            bd=0,
        )
        scroll = ttk.Scrollbar(shell, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side=LEFT, fill=BOTH, expand=True)
        scroll.pack(side=RIGHT, fill=Y)

        container = ttk.Frame(canvas)
        settings_window = canvas.create_window((0, 0), window=container, anchor="nw")
        container.bind(
            "<Configure>",
            lambda _event: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.bind(
            "<Configure>",
            lambda event: canvas.itemconfigure(settings_window, width=max(1, event.width - 2)),
        )
        canvas.bind(
            "<MouseWheel>",
            lambda event: canvas.yview_scroll(int(-1 * (event.delta / 120)), "units"),
        )

        telegram_card = self._card(container, "Telegram — вход через QR")
        telegram_card.pack(fill=X, pady=(0, 9))
        self._form_row(telegram_card, 0, "API ID", ttk.Entry(telegram_card, textvariable=self.api_id_var, width=40))
        self._form_row(telegram_card, 1, "API Hash", ttk.Entry(telegram_card, textvariable=self.api_hash_var, width=40, show="•"))
        self._form_row(telegram_card, 2, "Чат с тикетами", ttk.Entry(telegram_card, textvariable=self.source_chat_var, width=40))
        self._form_row(telegram_card, 3, "Чат для ответов", ttk.Entry(telegram_card, textvariable=self.response_chat_var, width=40))
        ttk.Label(
            telegram_card,
            text="При отсутствии сохранённой сессии появится QR: Telegram → Настройки → Устройства → Подключить устройство.",
            style="Card.TLabel",
            wraplength=900,
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(7, 0))

        safety_card = self._card(container, "Ответы и обучение")
        safety_card.pack(fill=X, pady=(0, 9))
        self._form_row(safety_card, 0, "Порог черновика", ttk.Spinbox(safety_card, from_=70, to=99, textvariable=self.draft_threshold_var, width=8))
        self._form_row(safety_card, 1, "Порог автоответа", ttk.Spinbox(safety_card, from_=85, to=100, textvariable=self.auto_threshold_var, width=8))
        self._form_row(safety_card, 2, "Задержка перед автоотправкой, сек.", ttk.Entry(safety_card, textvariable=self.auto_delay_var, width=10))
        self._form_row(safety_card, 3, "Максимум автоответов в минуту", ttk.Spinbox(safety_card, from_=1, to=30, textvariable=self.auto_limit_var, width=8))
        self._checkbutton(safety_card, "Разрешить умное обучение по моим исходящим ответам", self.learning_enabled_var, card=True).grid(row=4, column=0, columnspan=2, sticky="w", pady=(7, 0))
        ttk.Label(safety_card, text="Точная политика обучения настраивается во вкладке «Обучение» → «Фильтр обучения».", style="Muted.Card.TLabel").grid(row=5, column=0, columnspan=2, sticky="w", pady=(0, 4))
        self._checkbutton(safety_card, "Открывать Telegram после вставки черновика", self.open_telegram_var, card=True).grid(row=6, column=0, columnspan=2, sticky="w")
        self._checkbutton(safety_card, "Разрешить правила автозакрытия тикетов", self.auto_close_enabled_var, card=True).grid(row=7, column=0, columnspan=2, sticky="w", pady=(4, 0))
        ttk.Label(
            safety_card,
            text="Закрытие отправляется в чат ответов фиксированной командой: t c {ticket_id}. Например: t c 82646.",
            style="Card.TLabel",
            wraplength=900,
            justify=LEFT,
        ).grid(row=8, column=0, columnspan=2, sticky="w", pady=(4, 0))

        windows_card = self._card(container, "Windows, микрофон и голосовые команды")
        windows_card.pack(fill=X)
        self._checkbutton(windows_card, "Голосовые команды", self.voice_enabled_var, card=True).grid(row=0, column=0, sticky="w", pady=3)
        self._checkbutton(windows_card, "Фраза «Алексей, приветствую!» при запуске", self.startup_voice_var, card=True).grid(row=1, column=0, sticky="w", pady=3)
        self._checkbutton(windows_card, "Требовать слово «Алексей» перед каждой командой", self.wake_word_var, card=True).grid(row=2, column=0, sticky="w", pady=3)
        self._checkbutton(windows_card, "Запускать вместе с Windows", self.autostart_var, card=True).grid(row=3, column=0, sticky="w", pady=3)
        microphone_row = ttk.Frame(windows_card, style="Card.TFrame")
        microphone_row.grid(row=4, column=0, sticky="ew", pady=(7, 2))
        ttk.Label(microphone_row, text="Микрофон:", style="Card.TLabel").pack(side=LEFT)
        self.microphone_combo = ttk.Combobox(microphone_row, textvariable=self.microphone_var, state="readonly", width=55)
        self.microphone_combo.pack(side=LEFT, fill=X, expand=True, padx=8)
        AnimatedButton(microphone_row, text="Обновить", style="Secondary.TButton", command=self.refresh_microphones).pack(side=RIGHT)
        ttk.Label(
            windows_card,
            text="Команды: открыть, скрыть, режим черновик, режим авто, режим обучения, пауза, продолжить.",
            style="Card.TLabel",
            wraplength=900,
        ).grid(row=5, column=0, sticky="w", pady=(6, 0))
        windows_card.columnconfigure(0, weight=1)

        update_card = self._card(container, "Обновление и резервная копия")
        update_card.pack(fill=X, pady=(9, 0))
        ttk.Label(
            update_card,
            text=(
                "Обычное обновление EXE не удаляет твою базу. Для страховки можно сохранить пакет: "
                "шаблоны, триггеры, стоп-фразы, автозакрытие, приоритеты, обучающие примеры и настройки."
            ),
            style="Card.TLabel",
            wraplength=900,
            justify=LEFT,
        ).pack(anchor="w", pady=(0, 8))
        update_actions = ttk.Frame(update_card, style="Card.TFrame")
        update_actions.pack(fill=X)
        AnimatedButton(update_actions, text="Экспорт обновления", style="Secondary.TButton", command=self.export_update_bundle).pack(side=LEFT)
        AnimatedButton(update_actions, text="Импорт обновления", style="Secondary.TButton", command=self.import_update_bundle).pack(side=LEFT, padx=6)
        AnimatedButton(update_actions, text="Что нового", style="Ghost.TButton", command=self.show_whats_new).pack(side=LEFT)

        AnimatedButton(container, text="Сохранить настройки", style="Accent.TButton", command=self.save_settings).pack(anchor="e", pady=(10, 0))

    def _form_row(self, parent, row: int, label: str, widget) -> None:
        ttk.Label(parent, text=label, style="Card.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 16), pady=4)
        widget.grid(row=row, column=1, sticky="ew", pady=4)
        parent.columnconfigure(1, weight=1)

    def _build_diagnostics(self) -> None:
        card = self._card(self.diagnostics_tab, "Проверка и обслуживание")
        card.pack(fill=BOTH, expand=True, padx=10, pady=10)
        self.diagnostics_text = self._text_widget(card, height=22, readonly=True)
        self.diagnostics_text.pack(fill=BOTH, expand=True)
        row = ttk.Frame(card, style="Card.TFrame")
        row.pack(fill=X, pady=(10, 0))
        AnimatedButton(row, text="Диагностика", style="Accent.TButton", command=self.run_diagnostics).pack(side=LEFT)
        AnimatedButton(row, text="Открыть лог", style="Secondary.TButton", command=lambda: os.startfile(LOG_FILE)).pack(side=LEFT, padx=6)
        AnimatedButton(row, text="Собрать пакет логов", style="Secondary.TButton", command=self.export_diagnostics_bundle).pack(side=LEFT, padx=(0, 6))
        AnimatedButton(row, text="Папка данных", style="Secondary.TButton", command=lambda: os.startfile(APPDATA_ROOT)).pack(side=LEFT)
        AnimatedButton(row, text="Сбросить Telegram-сессию", style="Secondary.TButton", command=self.reset_session).pack(side=RIGHT, padx=(0, 8))
        AnimatedButton(row, text="Полностью удалить программу", style="Danger.TButton", command=self.uninstall_everything).pack(side=RIGHT)

    def _load_settings_into_ui(self) -> None:
        labels = {"draft": "Черновик", "auto": "Автоответ", "learn": "Только обучение"}
        self.mode_var.set(labels.get(str(self.config.get("mode", "draft")), "Черновик"))
        self.api_id_var.set(str(self.config.get("api_id", "")))
        self.api_hash_var.set(str(self.config.get("api_hash", "")))
        self.source_chat_var.set(str(self.config.get("source_chat", "@AresMineRobot")))
        self.response_chat_var.set(str(self.config.get("response_chat", "@AresMineRobot")))
        self.command_relay_enabled_var.set(bool(self.config.get("command_relay_enabled", True)))
        self.command_relay_user_var.set(str(self.config.get("command_relay_user_id", "123456789")))
        self.command_relay_timeout_var.set(str(self.config.get("command_relay_timeout_seconds", 15)))
        self.command_relay_forward_var.set(bool(self.config.get("command_relay_forward", False)))
        if self.telegram is None:
            self.command_relay_status_var.set(
                "Нужна проверка диалога с ботом перед следующим запросом" if self.config.get("command_relay_needs_check")
                else ("Подключи Telegram для обработки команд" if self.command_relay_enabled_var.get() else "Перенаправление команд выключено")
            )
        self.draft_threshold_var.set(int(self.config.get("draft_threshold", 80)))
        self.auto_threshold_var.set(int(self.config.get("auto_threshold", 96)))
        self.auto_delay_var.set(str(self.config.get("auto_delay_seconds", 1.5)))
        self.auto_limit_var.set(int(self.config.get("auto_limit_per_minute", 6)))
        self.local_ai_enabled_var.set(bool(self.config.get("local_ai_enabled", True)))
        self.local_ai_model_var.set(str(self.config.get("local_ai_model", "qwen3.5:4b")))
        self.local_ai_endpoint_var.set(str(self.config.get("local_ai_endpoint", "http://127.0.0.1:11434")))
        self.local_ai_draft_threshold_var.set(int(self.config.get("local_ai_draft_threshold", 86)))
        self.local_ai_auto_threshold_var.set(int(self.config.get("local_ai_auto_threshold", 94)))
        self.local_ai_candidate_limit_var.set(max(4, min(16, int(self.config.get("local_ai_candidate_limit", 6)))))
        self.learning_enabled_var.set(bool(self.config.get("learning_enabled", True)))
        self.learning_noise_var.set(bool(self.config.get("learning_noise_filter", True)))
        self.learning_corrections_var.set(bool(self.config.get("learning_only_corrections", True)))
        policy = str(self.config.get("learning_policy", "") or "")
        if not policy:
            policy = "unknown_or_corrections" if bool(self.config.get("learning_only_corrections", True)) else "all"
        self.learning_policy_var.set({
            "unknown_or_corrections": "Умно: неизвестные + исправления",
            "unknown_only": "Только неизвестные",
            "all": "Все ручные ответы",
        }.get(policy, "Умно: неизвестные + исправления"))
        self.auto_close_enabled_var.set(bool(self.config.get("auto_close_enabled", True)))
        self.close_command_var.set(str(self.config.get("close_command_template", "")))
        if hasattr(self, "learning_exclusions_text"):
            exclusions = self.config.get("learning_exclusions", []) or []
            if isinstance(exclusions, str):
                exclusions = split_phrases(exclusions)
            self._set_text(self.learning_exclusions_text, "\n".join(str(item) for item in exclusions), readonly=False)
        if hasattr(self, "global_close_text"):
            close_phrases = self.config.get("global_close_phrases", []) or []
            if isinstance(close_phrases, str):
                close_phrases = split_phrases(close_phrases)
            self._set_text(self.global_close_text, "\n".join(str(item) for item in close_phrases), readonly=False)
        self.voice_enabled_var.set(bool(self.config.get("voice_enabled", True)))
        self.startup_voice_var.set(bool(self.config.get("startup_voice", True)))
        self.wake_word_var.set(bool(self.config.get("voice_wake_word_required", False)))
        self.autostart_var.set(bool(self.config.get("start_with_windows", True)))
        self.open_telegram_var.set(bool(self.config.get("open_telegram_on_draft", True)))

    def _config_from_ui(self) -> dict[str, Any]:
        relay_user = self.command_relay_user_var.get().strip()
        if not relay_user.isascii() or not relay_user.isdecimal() or int(relay_user) <= 0:
            raise ValueError("Для команд укажи положительный числовой Telegram ID одного человека")
        try:
            relay_timeout = int(self.command_relay_timeout_var.get())
        except ValueError as exc:
            raise ValueError("Таймаут команд должен быть целым числом секунд") from exc
        if not 5 <= relay_timeout <= 60:
            raise ValueError("Таймаут команд должен быть от 5 до 60 секунд")
        try:
            delay = float(self.auto_delay_var.get().replace(",", "."))
        except ValueError as exc:
            raise ValueError("Задержка автоответа должна быть числом") from exc
        if self.auto_threshold_var.get() < self.draft_threshold_var.get():
            raise ValueError("Порог автоответа не может быть ниже порога черновика")
        if self.local_ai_auto_threshold_var.get() < self.local_ai_draft_threshold_var.get():
            raise ValueError("Порог автоответа локального ИИ не может быть ниже порога черновика")
        microphone_id = self.microphone_map.get(self.microphone_var.get(), str(self.config.get("microphone_token_id", "")))
        return {
            **self.config,
            "mode": {"Черновик": "draft", "Автоответ": "auto", "Только обучение": "learn"}.get(self.mode_var.get(), "draft"),
            "api_id": self.api_id_var.get().strip(),
            "api_hash": self.api_hash_var.get().strip(),
            "auth_method": "qr",
            "source_chat": self.source_chat_var.get().strip(),
            "response_chat": self.response_chat_var.get().strip(),
            "command_relay_enabled": bool(self.command_relay_enabled_var.get()),
            "command_relay_user_id": str(int(relay_user)),
            "command_relay_timeout_seconds": relay_timeout,
            "command_relay_forward": bool(self.command_relay_forward_var.get()),
            "draft_threshold": int(self.draft_threshold_var.get()),
            "auto_threshold": int(self.auto_threshold_var.get()),
            "auto_delay_seconds": max(0.0, delay),
            "auto_limit_per_minute": int(self.auto_limit_var.get()),
            "local_ai_enabled": False,
            "context_engine_version": 2,
            "local_ai_model": self.local_ai_model_var.get().strip() or "qwen3.5:4b",
            "local_ai_endpoint": self.local_ai_endpoint_var.get().strip() or "http://127.0.0.1:11434",
            "local_ai_draft_threshold": int(self.local_ai_draft_threshold_var.get()),
            "local_ai_auto_threshold": int(self.local_ai_auto_threshold_var.get()),
            "local_ai_candidate_limit": max(4, min(16, int(self.local_ai_candidate_limit_var.get()))),
            "local_ai_timeout_seconds": 5,
            "local_ai_context_size": 2048,
            "local_ai_num_predict": 48,
            "local_ai_keep_alive": "60m",
            "local_ai_prewarm": True,
            "local_ai_fast_path_confidence": 82,
            "local_ai_timeout_fallback_confidence": 80,
            "local_ai_speed_profile_version": 2,
            "learning_enabled": bool(self.learning_enabled_var.get()),
            "learning_noise_filter": bool(self.learning_noise_var.get()),
            "learning_policy": {
                "Умно: неизвестные + исправления": "unknown_or_corrections",
                "Только неизвестные": "unknown_only",
                "Все ручные ответы": "all",
            }.get(self.learning_policy_var.get(), "unknown_or_corrections"),
            "learning_only_corrections": self.learning_policy_var.get() != "Все ручные ответы",
            "learning_exclusions": split_phrases(self.learning_exclusions_text.get("1.0", END)) if hasattr(self, "learning_exclusions_text") else list(self.config.get("learning_exclusions", []) or []),
            "auto_close_enabled": bool(self.auto_close_enabled_var.get()),
            "global_close_phrases": split_phrases(self.global_close_text.get("1.0", END)) if hasattr(self, "global_close_text") else list(self.config.get("global_close_phrases", []) or []),
            "close_command_template": self.close_command_var.get().strip(),
            "voice_enabled": bool(self.voice_enabled_var.get()),
            "startup_voice": bool(self.startup_voice_var.get()),
            "voice_wake_word_required": bool(self.wake_word_var.get()),
            "microphone_token_id": microphone_id,
            "start_with_windows": bool(self.autostart_var.get()),
            "open_telegram_on_draft": bool(self.open_telegram_var.get()),
        }

    def save_settings(self, show_message: bool = True) -> bool:
        try:
            self.config = self._config_from_ui()
        except ValueError as exc:
            if show_message:
                messagebox.showerror("Ошибка настроек", str(exc))
            return False
        save_config(self.config)
        if self.telegram:
            # Состояние активного запроса принадлежит рабочему потоку. Старая
            # копия настроек UI не должна заново включить/снять блокировку.
            self.telegram.config.update({key: value for key, value in self.config.items()
                                         if key != "command_relay_needs_check"})
            self.telegram.update_command_relay()
        else:
            self.command_relay_status_var.set("Подключи Telegram для обработки команд" if self.config["command_relay_enabled"] else "Перенаправление команд выключено")
        self._apply_autostart()
        self._apply_voice_setting()
        if show_message:
            messagebox.showinfo("Ares Assistant", "Настройки сохранены")
        return True

    def resume_command_relay(self) -> None:
        if not self.telegram or not self.telegram.connected:
            messagebox.showinfo("Команды", "Сначала подключи Telegram кнопкой вверху окна.")
            return
        if not self.config.get("command_relay_enabled", False):
            messagebox.showinfo("Команды", "Включи перенаправление и сохрани настройки.")
            return
        self.command_relay_status_var.set("Сбрасываю текущий запрос и перезапускаю команды…")
        self.command_relay_resume_button.state(["disabled"])
        if self.telegram.resume_command_relay() is False:
            self.command_relay_resume_button.state(["!disabled"])
            self.command_relay_status_var.set("Не удалось запустить сброс. Отключи и снова подключи Telegram.")

    def _local_ai_ui_config(self) -> dict[str, Any]:
        config = dict(self.config)
        config.update(
            {
                "local_ai_enabled": bool(self.local_ai_enabled_var.get()),
                "local_ai_model": self.local_ai_model_var.get().strip() or "qwen3.5:4b",
                "local_ai_endpoint": self.local_ai_endpoint_var.get().strip() or "http://127.0.0.1:11434",
                "local_ai_draft_threshold": int(self.local_ai_draft_threshold_var.get()),
                "local_ai_auto_threshold": int(self.local_ai_auto_threshold_var.get()),
                "local_ai_candidate_limit": max(4, min(16, int(self.local_ai_candidate_limit_var.get()))),
            "local_ai_timeout_seconds": 5,
            "local_ai_context_size": 2048,
            "local_ai_num_predict": 48,
            "local_ai_keep_alive": "60m",
            "local_ai_prewarm": True,
            "local_ai_fast_path_confidence": 82,
            "local_ai_timeout_fallback_confidence": 80,
            "local_ai_speed_profile_version": 2,
            }
        )
        return config

    def check_local_ai(self, quiet: bool = False) -> None:
        if not bool(self.local_ai_enabled_var.get()):
            self.local_ai_status_var.set("ИИ выключен")
            return
        self.local_ai_status_var.set("Проверяю Ollama…")
        config = self._local_ai_ui_config()

        def worker() -> None:
            analyzer = LocalAIAnalyzer(config)
            status = analyzer.status()
            self.emit("local_ai_status", {"status": status, "quiet": quiet})

        threading.Thread(target=worker, name="LocalAIStatus", daemon=True).start()

    def prewarm_local_ai(self) -> None:
        """Warm Ollama in the background; never block Telegram or the UI."""
        if not bool(self.config.get("local_ai_enabled", True)) or not bool(self.config.get("local_ai_prewarm", True)):
            return
        config = dict(self.config)

        def worker() -> None:
            analyzer = LocalAIAnalyzer(config)
            ok, message, elapsed_ms = analyzer.prewarm()
            self.emit(
                "local_ai_prewarm",
                {"ok": ok, "message": message, "elapsed_ms": elapsed_ms},
            )

        threading.Thread(target=worker, name="LocalAIPrewarm", daemon=True).start()

    def pull_local_ai_model(self) -> None:
        analyzer = LocalAIAnalyzer(self._local_ai_ui_config())
        ok, message = analyzer.start_pull_console()
        self.local_ai_status_var.set(message)
        if not ok:
            messagebox.showwarning(
                "Локальный ИИ",
                message + "\n\nНажми «Установить Ollama», затем снова «Скачать модель».",
            )

    @staticmethod
    def open_ollama_download() -> None:
        webbrowser.open("https://ollama.com/download/windows")

    def _apply_autostart(self, enabled: Optional[bool] = None) -> None:
        try:
            import winreg

            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
            should_enable = bool(self.config.get("start_with_windows")) if enabled is None else enabled
            if should_enable and LAUNCHER_EXE:
                winreg.SetValueEx(key, "AresAssistant", 0, winreg.REG_SZ, f'"{LAUNCHER_EXE}" --background')
            else:
                try:
                    winreg.DeleteValue(key, "AresAssistant")
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
        except Exception:
            LOGGER.exception("Unable to update autostart")

    def _apply_voice_setting(self) -> None:
        if self.voice:
            self.voice.stop()
            self.voice = None
        if self.config.get("voice_enabled"):
            self.voice = VoiceCommands(
                lambda command: self.emit("command", command),
                microphone_token_id=str(self.config.get("microphone_token_id", "")),
                wake_word_required=bool(self.config.get("voice_wake_word_required", False)),
            )
            self.voice.start()

    def refresh_microphones(self) -> None:
        try:
            devices = list_microphones()
            self.microphone_map = {name: token_id for name, token_id in devices}
            self.microphone_combo.configure(values=list(self.microphone_map))
            configured = str(self.config.get("microphone_token_id", ""))
            selected = next((name for name, token_id in devices if token_id == configured), devices[0][0])
            self.microphone_var.set(selected)
        except Exception:
            LOGGER.exception("Microphone refresh failed")

    def _startup_services(self) -> None:
        try:
            removed = self.knowledge.cleanup_pending_noise(self._learning_exclusions())
            if removed:
                LOGGER.info("Removed %s noisy/excluded pending tickets on startup", removed)
                self._refresh_all()
        except Exception:
            LOGGER.exception("Pending cleanup on startup failed")
        self.tray.start()
        self._apply_autostart()
        self._apply_voice_setting()
        if self.config.get("startup_voice"):
            # Phrases, their mode and the voice live in voice_phrases.json (ModernUI.speak_phrase).
            say = getattr(self, "speak_phrase", None)
            if say:
                say("greeting")
            else:
                speak(str(self.config.get("startup_phrase", "Алексей, приветствую!")))
        if self.config.get("monitoring"):
            self.start_monitoring()

    def _drain_events(self) -> None:
        try:
            while True:
                event, payload = self.events.get_nowait()
                self._handle_event(event, payload)
        except queue.Empty:
            pass
        except Exception:
            LOGGER.exception("Event processing failed")
        if not self.shutting_down:
            self.root.after(100, self._drain_events)

    def _handle_event(self, event: str, payload: Any) -> None:
        if event == "telegram_ticket":
            self.process_ticket(str(payload))
        elif event == "telegram_status":
            status_text = str(payload)
            self.status_var.set("Пауза — Telegram подключён" if self.paused and "подключ" in status_text.lower() else ("Пауза" if self.paused else status_text))
            self.monitor_button_var.set("Отключить Telegram" if "подключ" in status_text.lower() else "Подключить Telegram")
        elif event == "telegram_error":
            self.status_var.set("Ошибка Telegram")
            messagebox.showerror("Telegram", str(payload))
        elif event == "telegram_notice":
            self.status_var.set(str(payload))
        elif event == "command_relay_status":
            info = dict(payload or {})
            if info.get("service_id") is not None and info["service_id"] != id(self.telegram):
                return  # Закрытый сервис не меняет статус новой сессии.
            self.command_relay_status_var.set(str(info.get("message", "")))
            if hasattr(self, "command_relay_resume_button"):
                self.command_relay_resume_button.state(["!disabled"] if info.get("can_restart", True) else ["disabled"])
            needs_check = bool(info.get("needs_check", False))
            if needs_check != bool(self.config.get("command_relay_needs_check", False)):
                self.config["command_relay_needs_check"] = needs_check
                save_config(self.config)
        elif event == "reply_handoff":
            info = dict(payload or {})
            self._reply_handoff(info.get("context"), str(info.get("reason", "Нужен ручной ответ")))
        elif event == "telegram_action_done":
            self._action_completed(dict(payload or {}))
        elif event == "telegram_action_skipped":
            if self.paused:
                self.status_var.set("Пауза — действия остановлены")
        elif event == "learned":
            learned = dict(payload or {})
            ticket_id = str(learned.get("ticket_id", ""))
            answer = str(learned.get("answer", ""))
            template_name = str(learned.get("template_name", ""))
            skipped = bool(learned.get("skipped", False))
            reason = str(learned.get("reason", ""))
            LOGGER.info("Learning event ticket=%s skipped=%s template=%s reason=%s", ticket_id, skipped, template_name, reason)
            self.last_learned_var.set(f"Тикет #{ticket_id} → {template_name}")
            self._set_text(self.learned_answer_text, answer + (f"\n\nНе добавлено в обучение: {reason}" if skipped else ""))
            if skipped:
                self.status_var.set(f"Тикет #{ticket_id}: обучение не требовалось")
            else:
                self.tray.notify("Решение сохранено", f"Программа запомнила решение по тикету #{ticket_id}")
            self._refresh_all()
        elif event == "local_ai_ticket_result":
            self._handle_local_ai_ticket_result(dict(payload or {}))
        elif event == "simulator_result":
            self._handle_simulator_result(dict(payload or {}))
        elif event == "simulator_error":
            self.simulator_busy = False
            self.simulator_status_var.set("Ошибка проверки")
            messagebox.showerror("Песочница ответа", str(payload))
        elif event == "local_ai_status":
            info = dict(payload or {})
            status = info.get("status")
            if status is not None:
                if bool(getattr(status, "available", False)):
                    if bool(getattr(status, "model_installed", False)):
                        version = str(getattr(status, "version", "") or "")
                        suffix = f" · Ollama {version}" if version else ""
                        self.local_ai_status_var.set(f"Готово: {getattr(status, 'model', '')}{suffix}")
                    else:
                        self.local_ai_status_var.set(f"Ollama работает, но модель {getattr(status, 'model', '')} не скачана")
                else:
                    self.local_ai_status_var.set(str(getattr(status, "error", "Ollama недоступен")))
        elif event == "local_ai_prewarm":
            info = dict(payload or {})
            if bool(info.get("ok")):
                elapsed = int(info.get("elapsed_ms", 0) or 0)
                self.local_ai_status_var.set(f"Готово: {self.config.get('local_ai_model', 'qwen3.5:4b')} · прогрето ({elapsed / 1000:.1f} сек)")
                LOGGER.info("Local AI prewarmed in %sms", elapsed)
            else:
                LOGGER.info("Local AI prewarm failed: %s", info.get("message", ""))
        elif event == "qr_login":
            self._show_qr(str((payload or {}).get("url", "")))
        elif event == "qr_authorized":
            self._close_qr()
        elif event == "login_password_needed":
            password = simpledialog.askstring("Telegram", "Введите облачный пароль двухэтапной защиты:", show="•", parent=self.root)
            if self.telegram:
                self.telegram.submit_auth("password", password or "")
        elif event in {"command", "show"}:
            self.handle_command(str(payload) if payload else "show")

    def _show_qr(self, url: str) -> None:
        if not url:
            return
        try:
            import qrcode  # type: ignore

            matrix = qrcode.QRCode(border=2)
            matrix.add_data(url)
            matrix.make(fit=True)
            cells = matrix.get_matrix()
        except Exception as exc:
            messagebox.showerror("Telegram QR", f"Не удалось построить QR-код: {exc}")
            return

        if not self.qr_dialog or not self.qr_dialog.winfo_exists():
            dialog = Toplevel(self.root)
            dialog.title("Вход в Telegram через QR")
            dialog.geometry("420x500")
            dialog.resizable(False, False)
            dialog.configure(bg=getattr(self, "palette", {}).get("background", "#0e121a"))
            ttk.Label(dialog, text="Отсканируй QR-код", style="Title.TLabel").pack(pady=(16, 6))
            ttk.Label(
                dialog,
                text="Telegram → Настройки → Устройства → Подключить устройство",
                style="Sub.TLabel",
                wraplength=370,
                justify="center",
            ).pack(pady=(0, 8))
            self.qr_canvas = Canvas(dialog, width=340, height=340, bg="white", highlightthickness=0)
            self.qr_canvas.pack(pady=8)
            ttk.Label(dialog, text="Если код истечёт, он обновится автоматически.", style="Sub.TLabel").pack()
            dialog.transient(self.root)
            self.qr_dialog = dialog
        assert self.qr_canvas is not None
        self.qr_canvas.delete("all")
        size = 340
        count = len(cells)
        cell = size / count
        for y, row in enumerate(cells):
            for x, filled in enumerate(row):
                if filled:
                    self.qr_canvas.create_rectangle(x * cell, y * cell, (x + 1) * cell + 1, (y + 1) * cell + 1, fill="black", outline="black")
        self.qr_dialog.deiconify()
        self.qr_dialog.lift()

    def _close_qr(self) -> None:
        if self.qr_dialog and self.qr_dialog.winfo_exists():
            self.qr_dialog.destroy()
        self.qr_dialog = None
        self.qr_canvas = None

    def toggle_monitoring(self) -> None:
        if self.telegram:
            self.stop_monitoring()
        else:
            self.start_monitoring()

    def start_monitoring(self) -> None:
        if self.telegram:
            return
        if not self.save_settings(show_message=False):
            return
        self.telegram = TelegramService(self.config, self.knowledge, SESSION_PATH, self.emit)
        self.telegram.set_paused(self.paused)
        self.telegram.start()
        self.config["monitoring"] = True
        save_config(self.config)
        self.status_var.set("Подключение…")
        self.monitor_button_var.set("Отключить Telegram")

    def stop_monitoring(self) -> None:
        if self.telegram:
            self.telegram.stop()
        self.telegram = None
        self.config["monitoring"] = False
        save_config(self.config)
        self.status_var.set("Отключено")
        self.command_relay_status_var.set("Telegram отключён · подключи для обработки команд")
        self.command_relay_resume_button.state(["!disabled"])
        self.monitor_button_var.set("Подключить Telegram")
        self._close_qr()

    def toggle_pause(self) -> None:
        self.paused = not self.paused
        if self.telegram:
            self.telegram.set_paused(self.paused)
        self.pause_button_var.set("Продолжить" if self.paused else "Пауза")
        if self.paused:
            self.status_var.set("Пауза — всё остановлено")
        else:
            self.status_var.set("Telegram подключён" if self.telegram and self.telegram.connected else "Ожидание подключения")
        self.tray.notify(
            "Ares Assistant",
            "Полная пауза: тикеты, черновики, автоответы и обучение остановлены"
            if self.paused
            else "Обработка и обучение продолжены",
        )

    def _learning_exclusions(self) -> list[str]:
        value = self.config.get("learning_exclusions", []) or []
        if isinstance(value, str):
            return split_phrases(value)
        return [str(item) for item in value if str(item).strip()]

    def _global_close_phrases(self) -> list[str]:
        value = self.config.get("global_close_phrases", []) or []
        if isinstance(value, str):
            return split_phrases(value)
        return [str(item) for item in value if str(item).strip()]

    def _question_is_learnable(self, question: str) -> bool:
        if bool(self.config.get("learning_noise_filter", True)) and is_noise_question(question):
            return False
        # Anything configured for immediate closing is also excluded from learning,
        # so acknowledgements like "вопрос решён" never pollute the knowledge base.
        exclusions = list(self._learning_exclusions()) + list(self._global_close_phrases())
        return not matches_learning_exclusion(question, exclusions)

    def process_ticket(self, raw_text: str, force: bool = False) -> None:
        if self.paused and not force:
            return
        parsed = parse_ticket(raw_text)
        if not parsed:
            LOGGER.info("Ignored incoming service/noise message: %s", " ".join(raw_text.split())[:900])
            return
        ticket_id, question = parsed
        reply_context = self.knowledge.reply_guard.ingest(source_key(self.config), ticket_id, raw_text, question)
        if reply_context['duplicate'] and not force:
            return
        learnable = self._question_is_learnable(question)
        if force:
            self.knowledge.unmark_processed(ticket_id)

        # The legacy classifier is now the fast first stage. It handles exact rules,
        # deterministic close rules and builds a conservative lexical/context baseline.
        # Fuzzy template selection can then be verified by the local neural model.
        decision = self.knowledge.classify(
            ticket_id,
            question,
            int(self.config.get("draft_threshold", 80)),
            int(self.config.get("auto_threshold", 96)),
            add_unknown_to_pending=False,
            global_close_phrases=self._global_close_phrases(),
            respect_processed=not reply_context["followup"],
        )
        mode = str(self.config.get("mode", "draft"))

        exact_known = False
        normalized_question = normalize(question)
        if normalized_question in self.knowledge.exact_rules:
            exact_known = True
        elif decision.confidence == 100 and decision.matched_question and normalize(decision.matched_question) == normalized_question:
            exact_known = True

        fast_trusted = self._baseline_is_fast_trusted(decision, mode)
        # 2.9.6: Ollama is no longer part of ticket decisions. The deterministic
        # context engine is the only classifier, so no ticket waits on a model timeout.
        should_use_ai = False
        if fast_trusted:
            LOGGER.info(
                "Ticket #%s used fast semantic path confidence=%s template=%r",
                ticket_id, decision.confidence, decision.template_name,
            )

        if should_use_ai:
            if ticket_id in self.ai_inflight:
                LOGGER.info("Ticket #%s is already being checked by local AI", ticket_id)
                return
            candidates = self.knowledge.ai_candidates(
                question,
                int(self.config.get("local_ai_candidate_limit", 6)),
            )
            if candidates:
                self.ai_inflight.add(ticket_id)
                self.status_var.set(f"Локальный ИИ анализирует тикет #{ticket_id}…")
                LOGGER.info("Ticket #%s sent to local AI with %s candidates", ticket_id, len(candidates))
                config_snapshot = dict(self.config)

                def worker() -> None:
                    # Shared analyzer keeps the same warmed Ollama model/fast profile.
                    self.local_ai.update_config(config_snapshot)
                    result = self.local_ai.analyze(question, candidates)
                    self.emit(
                        "local_ai_ticket_result",
                        {
                            "raw_text": raw_text,
                            "ticket_id": ticket_id,
                            "question": question,
                            "learnable": learnable,
                            "force": force,
                            "baseline": decision,
                            "ai_result": result,
                        },
                    )

                threading.Thread(target=worker, name=f"LocalAITicket-{ticket_id}", daemon=True).start()
                return

        self._finalize_ticket(raw_text, ticket_id, question, decision, learnable, force, reply_context)

    def _baseline_is_fast_trusted(self, decision: Decision, mode: str) -> bool:
        """Trust an already-known answer without waiting for Ollama.

        The deterministic layer already compares the whole ticket against confirmed
        situations and refuses one-word trigger matches. In Draft mode any DRAFT has
        already passed that semantic threshold, so asking a generative model again only
        adds latency and can turn a good answer into UNKNOWN on timeout.

        Auto mode is stricter: a very well-supported template may bypass Ollama, while
        borderline/one-off matches still go through the second-stage judge.
        """
        if decision.decision != "DRAFT":
            return False

        confidence = int(decision.confidence or 0)
        examples = int(decision.template_count or 0)

        if mode != "auto":
            return True

        if bool(decision.auto_allowed):
            return True

        if examples >= 12 and confidence >= 83:
            return True
        if examples >= 6 and confidence >= 86:
            return True
        if examples >= 2 and confidence >= 90:
            return True
        return False

    def _handle_local_ai_ticket_result(self, payload: dict[str, Any]) -> None:
        ticket_id = str(payload.get("ticket_id", ""))
        self.ai_inflight.discard(ticket_id)
        raw_text = str(payload.get("raw_text", ""))
        question = str(payload.get("question", ""))
        learnable = bool(payload.get("learnable", True))
        force = bool(payload.get("force", False))
        baseline = payload.get("baseline")
        if not isinstance(baseline, Decision):
            baseline = Decision(ticket_id, "UNKNOWN", 0, reason="Не удалось получить базовое решение")
        ai_result = payload.get("ai_result")
        if not isinstance(ai_result, AIResult):
            ai_result = AIResult("error", None, 0, "Локальный ИИ вернул неверный результат", available=False, error="bad result")

        if self.paused and not force:
            self.status_var.set("Пауза — результат ИИ не отправлен")
            LOGGER.info("Ticket #%s AI result discarded because program is paused", ticket_id)
            return

        mode = str(self.config.get("mode", "draft"))
        draft_threshold = int(self.config.get("local_ai_draft_threshold", 86))
        auto_threshold = int(self.config.get("local_ai_auto_threshold", 94))
        required_threshold = auto_threshold if mode == "auto" else draft_threshold

        if ai_result.decision == "use" and ai_result.template_id is not None and ai_result.confidence >= required_threshold:
            decision = self.knowledge.decision_from_template(
                ticket_id,
                question,
                int(ai_result.template_id),
                int(ai_result.confidence),
                f"Локальный ИИ подтвердил смысл: {ai_result.reason}",
                auto_threshold=auto_threshold,
            )
        else:
            safe_fallback = bool(self.config.get("local_ai_safe_fallback", True))
            baseline_fallback = (
                ai_result.decision == "error"
                and baseline.decision == "DRAFT"
                and (mode != "auto" or self._baseline_is_fast_trusted(baseline, mode))
            )
            if ai_result.decision == "error" and (not safe_fallback or baseline_fallback):
                decision = baseline
                if baseline_fallback:
                    decision.reason = (
                        f"ИИ не успел, но подтверждённая база уже нашла ответ "
                        f"({baseline.confidence}%, примеров: {baseline.template_count})"
                    )
            else:
                if ai_result.decision == "error":
                    reason = f"ИИ не успел за быстрый лимит; нужен ручной ответ: {ai_result.error or ai_result.reason}"
                    confidence = 0
                elif ai_result.decision == "use":
                    reason = (
                        f"Локальный ИИ нашёл похожий шаблон, но уверенность {ai_result.confidence}% "
                        f"ниже безопасного порога {required_threshold}%: {ai_result.reason}"
                    )
                    confidence = int(ai_result.confidence)
                else:
                    reason = f"Локальный ИИ не подтвердил ни один шаблон: {ai_result.reason}"
                    confidence = int(ai_result.confidence)
                self.knowledge.observe_ticket(ticket_id, question, None)
                decision = Decision(
                    ticket_id=ticket_id,
                    decision="UNKNOWN",
                    confidence=max(0, min(100, confidence)),
                    reason=reason,
                    matched_question=baseline.matched_question,
                    template_count=baseline.template_count,
                    template_id=None,
                    template_name="",
                )

        LOGGER.info(
            "Local AI ticket #%s result=%s confidence=%s template=%r ai_ms=%s load_ms=%s reason=%s",
            ticket_id,
            decision.decision,
            decision.confidence,
            decision.template_name,
            int(getattr(ai_result, "elapsed_ms", 0) or 0),
            int(getattr(ai_result, "load_ms", 0) or 0),
            decision.reason,
        )
        self._finalize_ticket(raw_text, ticket_id, question, decision, learnable, force)

    def _finalize_ticket(
        self,
        raw_text: str,
        ticket_id: str,
        question: str,
        decision: Decision,
        learnable: bool,
        force: bool = False,
        reply_context=None,
    ) -> None:
        LOGGER.info(
            "Ticket #%s decision=%s confidence=%s template=%r learnable=%s reason=%s question=%r",
            ticket_id,
            decision.decision,
            decision.confidence,
            decision.template_name,
            learnable,
            decision.reason,
            question[:1200],
        )
        self.last_ticket = (raw_text, question, decision)
        self.last_ticket_var.set(
            f"Тикет #{ticket_id} · {decision.decision} · уверенность {decision.confidence}% · "
            f"шаблон: {decision.template_name or 'не выбран'} · примеров: {decision.template_count}\n{question}"
        )
        preview = decision.draft or f"{decision.reason}\n\nБлижайшая сохранённая ситуация:\n{decision.matched_question}"
        self._set_text(self.preview_text, preview)

        if self.paused and not force:
            return

        reply_context = reply_context or self.knowledge.reply_guard.ingest(source_key(self.config), ticket_id, raw_text, question)
        template = self.knowledge.get_template(decision.template_id) if decision.template_id else None
        reply_context.update(template_id=decision.template_id, template_key=template_key(template['answer_template'] if template else decision.draft), confidence=decision.confidence, answer=decision.draft)
        mode = str(self.config.get("mode", "draft"))
        if mode == 'auto' and decision.decision not in ('CLOSE', 'IGNORE'):
            reason = self.knowledge.reply_guard.check(reply_context, reply_context['template_key'], bool(template.get('reply_limit_enabled', True)) if template else True)
            if reason:
                self._reply_handoff(reply_context, reason)
                return
        if decision.decision == "CLOSE":
            if not bool(self.config.get("auto_close_enabled", True)):
                self.knowledge.add_history(ticket_id, question, "close_rule_disabled", 100, "", decision.reason)
            elif not self.telegram or not self.telegram.connected:
                self.knowledge.add_history(ticket_id, question, "close_preview_only", 100, "", "Telegram не подключён")
            else:
                self.telegram.close_ticket(ticket_id)
                self.knowledge.add_history(ticket_id, question, "close_queued", 100, "", decision.reason)
        elif mode == "learn":
            if learnable:
                self.knowledge.add_pending(ticket_id, question)
                self.knowledge.add_history(ticket_id, question, "learn_waiting", decision.confidence, decision.draft, "Ожидается твоё исходящее решение")
                self.tray.notify("Режим обучения", f"Тикет #{ticket_id}: ответь вручную, программа решит, нужно ли это запоминать")
            else:
                self.knowledge.add_history(ticket_id, question, "learn_filtered", decision.confidence, "", "Исключено фильтром обучения")
        elif decision.decision == "IGNORE":
            self.knowledge.add_history(ticket_id, question, "ignored", decision.confidence, "", decision.reason)
        elif decision.decision == "UNKNOWN":
            action = "unknown" if learnable else "unknown_filtered"
            if learnable:
                self.knowledge.add_pending(ticket_id, question)
            self.knowledge.add_history(ticket_id, question, action, decision.confidence, "", decision.reason)
            if learnable:
                self.tray.notify("Нужен ручной ответ", f"Тикет #{ticket_id} добавлен в очередь обучения")
        elif not self.telegram or not self.telegram.connected:
            self.knowledge.add_history(ticket_id, question, "preview_only", decision.confidence, decision.draft, "Telegram не подключён")
        elif mode == "auto":
            # A DRAFT reaches this branch only after the full semantic gate for Auto mode.
            self.telegram.send_message(decision.draft, ticket_id, reply_context)
            self.knowledge.add_history(ticket_id, question, "auto_queued", decision.confidence, decision.draft, decision.template_name)
        else:
            self.telegram.save_draft(decision.draft, ticket_id, reply_context)
            self.knowledge.add_history(ticket_id, question, "draft_queued", decision.confidence, decision.draft, decision.template_name)
        self._refresh_all()

    def _action_completed(self, payload: dict[str, Any]) -> None:
        action = str(payload.get("action", ""))
        ticket_id = str(payload.get("ticket_id", ""))
        if action == 'draft' and payload.get('context'):
            self.knowledge.reply_guard.mark_handled(payload['context'], 'draft')
        if ticket_id:
            self.knowledge.mark_processed(ticket_id, action)
        if action == "draft":
            self.tray.notify("Черновик готов", f"Ответ для тикета #{ticket_id} вставлен без отправки")
            if self.config.get("open_telegram_on_draft"):
                self.open_response_chat()
        elif action == "auto":
            context = payload.get('context') or {}
            self.knowledge.add_history(ticket_id, context.get('question',''), 'auto_sent', int(context.get('confidence',0)), context.get('answer',''), 'Отправка подтверждена Telegram')
            self.tray.notify("Ответ отправлен", f"Тикет #{ticket_id} обработан автоматически")
        elif action == "close":
            self.tray.notify("Тикет закрыт", f"Тикет #{ticket_id} закрыт правилом автозакрытия")
        self._refresh_all()

    def _reply_handoff(self, context, reason):
        if not context:
            return
        ticket_id, question = context['ticket'], context['question']
        self.knowledge.reply_guard.mark_handled(context, 'handoff')
        self.knowledge.add_pending(ticket_id, question)
        self.knowledge.add_history(ticket_id, question, 'manual_required', 0, '', reason)
        self.status_var.set(f'Тикет #{ticket_id}: нужен ручной ответ')
        self.tray.notify('Нужен ручной ответ', f'Тикет #{ticket_id}: {reason}')
        if self.last_ticket and self.last_ticket[2].ticket_id == ticket_id:
            self._set_text(self.preview_text, 'НУЖЕН РУЧНОЙ ОТВЕТ\n\n' + reason + '\n\nАвтоответ остановлен. Обращение добавлено в очередь обучения.')
        self._refresh_all()

    def _mode_changed(self) -> None:
        self.config["mode"] = {"Черновик": "draft", "Автоответ": "auto", "Только обучение": "learn"}.get(self.mode_var.get(), "draft")
        save_config(self.config)

    def handle_command(self, command: str) -> None:
        if command == "show":
            self.show_window()
            speak("Открываю")
        elif command == "hide":
            self.hide_window()
            speak("Скрываю")
        elif command == "mode_draft":
            self.mode_var.set("Черновик")
            self._mode_changed()
            speak("Режим черновика")
        elif command == "mode_auto":
            self.mode_var.set("Автоответ")
            self._mode_changed()
            speak("Режим автоответа")
        elif command == "mode_learn":
            self.mode_var.set("Только обучение")
            self._mode_changed()
            speak("Режим обучения")
        elif command in {"pause", "toggle_pause"}:
            self.toggle_pause()
            speak("Пауза" if self.paused else "Продолжаю работу")
        elif command == "resume":
            if self.paused:
                self.toggle_pause()
            if not self.telegram or not self.telegram.connected:
                self.start_monitoring()
            speak("Продолжаю работу")
        elif command == "exit":
            self.shutdown()

    def hide_window(self) -> None:
        if self.shutting_down:
            return
        try:
            self.config["last_window_geometry"] = self.root.geometry()
            save_config(self.config)
        except Exception:
            pass
        self.root.withdraw()
        self.tray.notify("Ares Assistant", "Программа продолжает работать в трее")

    def show_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        try:
            self.root.attributes("-topmost", True)
            self.root.after(150, lambda: self.root.attributes("-topmost", False))
        except Exception:
            pass
        self.root.focus_force()

    def open_simulator(self) -> None:
        try:
            self.notebook.select(self.simulator_tab)
            self.simulator_input_text.focus_set()
        except Exception:
            LOGGER.exception("Unable to open simulator")

    def test_ticket(self) -> None:
        # Kept for compatibility with old menu/buttons. The old dialog could execute
        # the real ticket pipeline; all testing is now routed to the safe sandbox.
        self.open_simulator()

    def simulator_use_last(self) -> None:
        # Compatibility hook from old builds. The sandbox is intentionally manual-only.
        self.open_simulator()

    def clear_simulator(self) -> None:
        if self.simulator_busy:
            return
        self.simulator_input_text.delete("1.0", END)
        self.simulator_action_var.set("Введи сообщение игрока и нажми «Проверить»")
        self.simulator_status_var.set("Готово к безопасной проверке")
        self._set_text(self.simulator_summary_text, "Результат появится здесь.")
        self._set_text(self.simulator_answer_text, "—")
        self._set_text(self.simulator_reason_text, "—")
        for item in self.simulator_candidates_tree.get_children():
            self.simulator_candidates_tree.delete(item)
        self.simulator_candidate_map.clear()
        self.simulator_last_question = ""

    @staticmethod
    def _simulator_action_label(mode: str, decision: Decision, learnable: bool) -> str:
        if decision.decision == "CLOSE":
            return "ЗАКРЫЛА БЫ ТИКЕТ БЕЗ ОТВЕТА"
        if mode == "learn":
            return "НЕ ОТПРАВИЛА БЫ — включён режим «Только обучение»"
        if decision.decision == "IGNORE":
            return "ПРОИГНОРИРОВАЛА БЫ СООБЩЕНИЕ"
        if decision.decision == "UNKNOWN":
            return "НЕ ОТПРАВИЛА БЫ — нужен ручной ответ" if learnable else "НЕ ОТПРАВИЛА БЫ — вопрос исключён из обучения"
        if mode == "auto":
            return "ОТПРАВИЛА БЫ АВТООТВЕТ"
        return "СОХРАНИЛА БЫ ОТВЕТ КАК ЧЕРНОВИК"

    def run_simulator(self) -> None:
        if self.simulator_busy:
            return
        raw = self.simulator_input_text.get("1.0", END).strip()
        if not raw:
            self.simulator_status_var.set("Сначала введи сообщение игрока")
            return

        parsed = parse_ticket(raw)
        if parsed:
            _source_ticket_id, question = parsed
        else:
            # For the sandbox a bare player message is valid input. Service wrappers
            # still go through parse_ticket when present.
            question = raw.strip()
        if not question:
            self.simulator_status_var.set("Не удалось выделить вопрос игрока")
            return

        self.simulator_busy = True
        self.simulator_action_var.set("Анализирую тем же ядром, что реальный тикет…")
        self.simulator_status_var.set("Проверка безопасна: Telegram, история и обучение не изменяются")
        self._set_text(self.simulator_summary_text, "Идёт анализ…")
        self._set_text(self.simulator_answer_text, "—")
        self._set_text(self.simulator_reason_text, "—")
        for item in self.simulator_candidates_tree.get_children():
            self.simulator_candidates_tree.delete(item)

        config_snapshot = dict(self.config)
        exclusions = list(self._learning_exclusions())
        learning_noise = bool(self.config.get("learning_noise_filter", True))

        def worker() -> None:
            temp_dir = Path(tempfile.mkdtemp(prefix="ares-simulator-"))
            temp_db_path = temp_dir / "knowledge.db"
            temp_kb: Optional[KnowledgeBase] = None
            try:
                destination = sqlite3.connect(temp_db_path)
                try:
                    with self.knowledge.lock:
                        self.knowledge.connection.backup(destination)
                finally:
                    destination.close()

                temp_kb = KnowledgeBase(temp_db_path, SEED_PATH)
                ticket_id = "999999"
                mode = str(config_snapshot.get("mode", "draft"))
                close_exclusions = list(config_snapshot.get("global_close_phrases", []) or [])
                learnable = (
                    not (learning_noise and is_noise_question(question))
                    and not matches_learning_exclusion(question, exclusions + close_exclusions)
                )

                baseline = temp_kb.classify(
                    ticket_id,
                    question,
                    int(config_snapshot.get("draft_threshold", 80)),
                    int(config_snapshot.get("auto_threshold", 96)),
                    add_unknown_to_pending=False,
                    global_close_phrases=list(config_snapshot.get("global_close_phrases", []) or []),
                )
                decision = baseline
                candidate_limit = int(config_snapshot.get("local_ai_candidate_limit", 10))
                candidates = temp_kb.ai_candidates(question, candidate_limit)
                top_candidates = [
                    {
                        "id": int(item.get("id", 0) or 0),
                        "score": int(round(float(item.get("rank_score", 0.0)) * 100)),
                        "name": str(item.get("name", "")),
                        "examples": int(item.get("example_count", 0) or 0),
                    }
                    for item in candidates[:14]
                ]

                normalized_question = normalize(question)
                exact_known = normalized_question in temp_kb.exact_rules
                if not exact_known and baseline.confidence == 100 and baseline.matched_question:
                    exact_known = normalize(baseline.matched_question) == normalized_question

                ai_result: Optional[AIResult] = None
                baseline_confidence = int(baseline.confidence or 0)
                baseline_examples = int(baseline.template_count or 0)
                fast_trusted = False
                if baseline.decision == "DRAFT":
                    if mode != "auto":
                        fast_trusted = True
                    elif bool(baseline.auto_allowed):
                        fast_trusted = True
                    elif baseline_examples >= 12 and baseline_confidence >= 83:
                        fast_trusted = True
                    elif baseline_examples >= 6 and baseline_confidence >= 86:
                        fast_trusted = True
                    elif baseline_examples >= 2 and baseline_confidence >= 90:
                        fast_trusted = True
                should_use_ai = False
                if should_use_ai:
                    analyzer = LocalAIAnalyzer(config_snapshot)
                    ai_result = analyzer.analyze(question, candidates)
                    draft_threshold = int(config_snapshot.get("local_ai_draft_threshold", 86))
                    auto_threshold = int(config_snapshot.get("local_ai_auto_threshold", 94))
                    required_threshold = auto_threshold if mode == "auto" else draft_threshold
                    if ai_result.decision == "use" and ai_result.template_id is not None and ai_result.confidence >= required_threshold:
                        decision = temp_kb.decision_from_template(
                            ticket_id,
                            question,
                            int(ai_result.template_id),
                            int(ai_result.confidence),
                            f"Локальный ИИ подтвердил смысл: {ai_result.reason}",
                            auto_threshold=auto_threshold,
                        )
                    else:
                        safe_fallback = bool(config_snapshot.get("local_ai_safe_fallback", True))
                        baseline_fallback = (
                            ai_result.decision == "error"
                            and baseline.decision == "DRAFT"
                            and (
                                mode != "auto"
                                or bool(baseline.auto_allowed)
                                or (int(baseline.template_count or 0) >= 12 and int(baseline.confidence or 0) >= 83)
                                or (int(baseline.template_count or 0) >= 6 and int(baseline.confidence or 0) >= 86)
                                or (int(baseline.template_count or 0) >= 2 and int(baseline.confidence or 0) >= 90)
                            )
                        )
                        if ai_result.decision == "error" and (not safe_fallback or baseline_fallback):
                            decision = baseline
                            if baseline_fallback:
                                decision.reason = (
                                    f"ИИ не успел, но подтверждённая база уже нашла ответ "
                                    f"({baseline.confidence}%, примеров: {baseline.template_count})"
                                )
                        else:
                            if ai_result.decision == "error":
                                reason = f"ИИ не успел за быстрый лимит; нужен ручной ответ: {ai_result.error or ai_result.reason}"
                                confidence = 0
                            elif ai_result.decision == "use":
                                reason = (
                                    f"Локальный ИИ нашёл похожий шаблон, но уверенность {ai_result.confidence}% "
                                    f"ниже безопасного порога {required_threshold}%: {ai_result.reason}"
                                )
                                confidence = int(ai_result.confidence)
                            else:
                                reason = f"Локальный ИИ не подтвердил ни один шаблон: {ai_result.reason}"
                                confidence = int(ai_result.confidence)
                            decision = Decision(
                                ticket_id=ticket_id,
                                decision="UNKNOWN",
                                confidence=max(0, min(100, confidence)),
                                reason=reason,
                                matched_question=baseline.matched_question,
                                template_count=baseline.template_count,
                                template_id=None,
                                template_name="",
                            )

                action = self._simulator_action_label(mode, decision, learnable)
                draft = str(decision.draft or "")
                if draft:
                    draft = draft.replace(f"t a {ticket_id}", "t a <ID>", 1)
                ai_text = "Контекстный движок: без Ollama, решение рассчитано локально"
                if ai_result is not None:
                    latency = int(getattr(ai_result, "elapsed_ms", 0) or 0)
                    if ai_result.decision == "error":
                        ai_text = f"Не успел за {latency / 1000:.1f} сек: {ai_result.error or ai_result.reason}"
                    elif ai_result.decision == "use":
                        ai_text = f"{latency / 1000:.1f} сек · шаблон #{ai_result.template_id}, {ai_result.confidence}% · {ai_result.reason}"
                    else:
                        ai_text = f"{latency / 1000:.1f} сек · UNKNOWN, {ai_result.confidence}% · {ai_result.reason}"

                self.emit(
                    "simulator_result",
                    {
                        "question": question,
                        "mode": mode,
                        "learnable": learnable,
                        "action": action,
                        "decision": decision.decision,
                        "confidence": decision.confidence,
                        "template": decision.template_name,
                        "reason": decision.reason,
                        "matched": decision.matched_question,
                        "draft": draft,
                        "ai": ai_text,
                        "candidates": top_candidates,
                    },
                )
            except Exception as exc:
                LOGGER.exception("Simulator failed")
                self.emit("simulator_error", str(exc))
            finally:
                if temp_kb is not None:
                    try:
                        temp_kb.close()
                    except Exception:
                        pass
                shutil.rmtree(temp_dir, ignore_errors=True)

        threading.Thread(target=worker, name="AresSimulator", daemon=True).start()

    def _handle_simulator_result(self, payload: dict[str, Any]) -> None:
        self.simulator_busy = False
        mode_map = {"draft": "Черновик", "auto": "Автоответ", "learn": "Только обучение"}
        mode = mode_map.get(str(payload.get("mode", "draft")), str(payload.get("mode", "")))
        action = str(payload.get("action", ""))
        decision = str(payload.get("decision", "UNKNOWN"))
        confidence = int(payload.get("confidence", 0) or 0)
        template = str(payload.get("template", "") or "не выбран")
        learnable = bool(payload.get("learnable", True))
        ai_text = str(payload.get("ai", ""))
        question = str(payload.get("question", ""))
        self.simulator_last_question = question
        self.simulator_candidate_map.clear()

        self.simulator_action_var.set(action)
        self.simulator_status_var.set("Проверка завершена · данные программы не изменены")
        summary = (
            f"Текущий режим: {mode}\n"
            f"Финальное решение: {decision}\n"
            f"Уверенность: {confidence}%\n"
            f"Шаблон: {template}\n"
            f"Попал бы в обучение: {'да' if learnable else 'нет'}\n"
            f"Анализ: {ai_text}"
        )
        self._set_text(self.simulator_summary_text, summary)

        draft = str(payload.get("draft", ""))
        if not draft:
            draft = "Ответ не был бы отправлен." if decision in {"UNKNOWN", "IGNORE", "CLOSE"} else "—"
        self._set_text(self.simulator_answer_text, draft)

        reason = str(payload.get("reason", ""))
        matched = str(payload.get("matched", ""))
        reason_text = f"Вопрос после разбора:\n{question}\n\nПричина решения:\n{reason or '—'}"
        if matched:
            reason_text += f"\n\nБлижайшая подтверждённая ситуация:\n{matched}"
        self._set_text(self.simulator_reason_text, reason_text)

        for item in self.simulator_candidates_tree.get_children():
            self.simulator_candidates_tree.delete(item)
        first_item = ""
        for index, row in enumerate(payload.get("candidates", []) or []):
            score = int(row.get("score", 0) or 0)
            name = str(row.get("name", ""))
            examples = int(row.get("examples", 0) or 0)
            template_id = int(row.get("id", 0) or 0)
            iid = f"sim-{template_id or index}"
            self.simulator_candidate_map[iid] = template_id
            self.simulator_candidates_tree.insert("", END, iid=iid, values=(f"{score}%", name, examples))
            if not first_item:
                first_item = iid
        if first_item:
            self.simulator_candidates_tree.selection_set(first_item)
            self.simulator_candidates_tree.focus(first_item)

    def _simulator_selected_template_id(self) -> Optional[int]:
        selected = self.simulator_candidates_tree.selection()
        if not selected:
            focused = self.simulator_candidates_tree.focus()
            selected = (focused,) if focused else ()
        if not selected:
            return None
        template_id = int(self.simulator_candidate_map.get(selected[0], 0) or 0)
        return template_id or None

    def _simulator_current_question(self) -> str:
        """Return the question that is currently visible in the sandbox input.

        Training actions must never rely on simulator_last_question because the operator
        may edit/paste a new message after the previous check.
        """
        raw = self.simulator_input_text.get("1.0", END).strip()
        if not raw:
            return ""
        parsed = parse_ticket(raw)
        return str(parsed[1] if parsed else raw).strip()

    def simulator_teach_selected(self) -> None:
        if self.simulator_busy:
            return
        template_id = self._simulator_selected_template_id()
        if not template_id:
            messagebox.showinfo("Проверка", "Сначала выбери подходящий шаблон в списке кандидатов")
            return
        question = self._simulator_current_question()
        if not question:
            messagebox.showinfo("Проверка", "Сначала вставь сообщение игрока")
            return
        template = self.knowledge.get_template(template_id)
        if not template:
            messagebox.showerror("Проверка", "Выбранный шаблон уже удалён")
            return
        template_name = str(template.get("name", f"ID {template_id}"))
        stale_candidates = bool(self.simulator_last_question and normalize(question) != normalize(self.simulator_last_question))
        if not bool(template.get("enabled", 1)):
            if not messagebox.askyesno(
                "Шаблон выключен",
                f"Шаблон «{template_name}» сейчас выключен. Включить его и обучить этой ситуации?",
            ):
                return
            self.knowledge.set_templates_enabled([template_id], True)
            template = self.knowledge.get_template(template_id) or template
        if not messagebox.askyesno(
            "Обучить по выбранному шаблону",
            (
                f"Считать шаблон «{template_name}» правильным для этой ситуации?\n\n"
                f"{question}\n\n"
                + ("Внимание: текст изменён после последней проверки; сохраняется именно ТЕКУЩИЙ текст из поля.\n\n" if stale_candidates else "")
                + "Пример будет добавлен в основную базу обучения. В следующий раз похожие фразы "
                "будут сильнее тянуться к этому шаблону."
            ),
        ):
            return
        try:
            ok, message = self.knowledge.teach_question_to_template(question, template_id)
            if not ok:
                messagebox.showerror("Обучение", message)
                return
            self.last_learned_var.set(message)
            self._refresh_all()
            self.simulator_status_var.set(message + " · перепроверяю")
            self.run_simulator()
        except Exception as exc:
            LOGGER.exception("Sandbox training failed")
            messagebox.showerror("Обучение", f"Не удалось сохранить пример:\n{exc}")

    def simulator_create_manual_template(self) -> None:
        if self.simulator_busy:
            return
        # Always use the text currently visible in the sandbox.  The previous check may
        # belong to another ticket/message.
        question = self._simulator_current_question()
        if not question:
            messagebox.showinfo("Проверка", "Сначала вставь сообщение игрока")
            return

        answer = self.simulator_manual_answer_text.get("1.0", END).strip()
        if not answer:
            messagebox.showinfo("Проверка", "Напиши правильный ответ или вставь его через Ctrl+V")
            return
        name = self.simulator_manual_name_var.get().strip()
        preview = answer if len(answer) <= 500 else answer[:500] + "…"
        if not messagebox.askyesno(
            "Создать шаблон и обучить",
            (
                "Создать новый шаблон/использовать совпадающий и считать этот ответ правильным для ситуации?\n\n"
                f"Вопрос:\n{question}\n\nОтвет:\n{preview}\n\n"
                "После сохранения песочница сразу перепроверит сообщение."
            ),
        ):
            return
        try:
            ok, template_id, message = self.knowledge.create_template_from_manual_answer(question, answer, name)
            if not ok:
                messagebox.showerror("Обучение", message)
                return
            self.last_learned_var.set(message)
            self.template_selected_id = template_id
            self.simulator_manual_answer_text.delete("1.0", END)
            self.simulator_manual_name_var.set("")
            self._refresh_all()
            self.simulator_status_var.set(message + " · перепроверяю")
            self.run_simulator()
        except Exception as exc:
            LOGGER.exception("Manual sandbox template creation failed")
            messagebox.showerror("Обучение", f"Не удалось создать шаблон:\n{exc}")


    def simulator_open_selected_template(self) -> None:
        template_id = self._simulator_selected_template_id()
        if not template_id:
            messagebox.showinfo("Проверка", "Сначала выбери шаблон")
            return
        self.template_selected_id = template_id
        self.template_search_var.set("")
        self.template_filter_var.set("Все")
        self._refresh_templates()
        iid = str(template_id)
        if self.templates_tree.exists(iid):
            self.templates_tree.selection_set(iid)
            self.templates_tree.focus(iid)
            self.templates_tree.see(iid)
            self._template_selected()
        self.notebook.select(self.templates_tab)

    def retry_last_ticket(self) -> None:
        if not self.last_ticket:
            messagebox.showinfo("Ares Assistant", "Последнего тикета ещё нет")
            return
        raw_text, _, _ = self.last_ticket
        self.process_ticket(raw_text, force=True)

    def open_response_chat(self) -> None:
        chat = self.response_chat_var.get().strip().lstrip("@")
        try:
            if chat and not chat.lstrip("-").isdigit():
                os.startfile(f"tg://resolve?domain={chat}")
            else:
                os.startfile("tg://")
        except Exception:
            webbrowser.open(f"https://t.me/{chat}" if chat else "https://web.telegram.org")

    def _refresh_all(self) -> None:
        self._refresh_stats()
        self._refresh_pending()
        self._refresh_templates()
        self._refresh_history()

    def _refresh_stats(self) -> None:
        stats = self.knowledge.stats()
        self.stats_var.set(
            f"Примеров: {stats['examples']}   ·   Твоих решений: {stats['manual']}   ·   "
            f"Точных правил: {stats.get('exact', 0)}   ·   "
            f"Шаблонов: {stats['templates']} ({stats['disabled']} отключено)   ·   "
            f"Ждут ответа: {stats['pending']}   ·   Обработано: {stats['processed']}"
        )

    def _refresh_pending(self) -> None:
        if not hasattr(self, "pending_tree"):
            return
        selected = self.pending_tree.selection()
        selected_id = selected[0] if selected else None
        for item in self.pending_tree.get_children():
            self.pending_tree.delete(item)
        for ticket_id, question, updated_at in self.knowledge.pending_rows():
            one_line = " ".join(str(question).split())
            self.pending_tree.insert("", END, iid=str(ticket_id), values=(f"#{ticket_id}", one_line[:220], updated_at))
        if selected_id and self.pending_tree.exists(selected_id):
            self.pending_tree.selection_set(selected_id)

    def _pending_selected(self) -> None:
        selected = self.pending_tree.selection()
        if not selected:
            self._clear_training_solution()
            return
        ticket_id = str(selected[0])
        row = next((row for row in self.knowledge.pending_rows() if str(row[0]) == ticket_id), None)
        if not row:
            self._clear_training_solution()
            return
        question = str(row[1])
        self.training_selected_ticket_id = ticket_id
        self._set_text(self.pending_question_text, question)
        self._populate_training_candidates(question)

    def _clear_training_solution(self) -> None:
        self.training_selected_ticket_id = ""
        if hasattr(self, "pending_question_text"):
            self._set_text(self.pending_question_text, "")
        if hasattr(self, "training_candidates_tree"):
            for item in self.training_candidates_tree.get_children():
                self.training_candidates_tree.delete(item)
        self.training_candidate_map.clear()
        if hasattr(self, "training_candidate_answer_text"):
            self._set_text(self.training_candidate_answer_text, "")
        if hasattr(self, "training_manual_answer_text"):
            self.training_manual_answer_text.delete("1.0", END)
        if hasattr(self, "training_manual_name_var"):
            self.training_manual_name_var.set("")

    def _populate_training_candidates(self, question: str) -> None:
        if not hasattr(self, "training_candidates_tree"):
            return
        for item in self.training_candidates_tree.get_children():
            self.training_candidates_tree.delete(item)
        self.training_candidate_map.clear()
        self._set_text(self.training_candidate_answer_text, "")
        try:
            candidates = self.knowledge.ai_candidates(question, 12)
        except Exception:
            LOGGER.exception("Unable to build training candidates")
            candidates = []
        first_iid = ""
        for index, row in enumerate(candidates):
            template_id = int(row.get("id", 0) or 0)
            if not template_id:
                continue
            score = int(round(float(row.get("rank_score", 0.0) or 0.0) * 100))
            name = str(row.get("name", ""))
            examples = int(row.get("example_count", 0) or 0)
            iid = f"train-{template_id}-{index}"
            self.training_candidate_map[iid] = template_id
            self.training_candidates_tree.insert("", END, iid=iid, values=(f"{score}%", name, examples))
            if not first_iid:
                first_iid = iid
        if first_iid:
            self.training_candidates_tree.selection_set(first_iid)
            self.training_candidates_tree.focus(first_iid)
            self._training_candidate_selected()
        else:
            self._set_text(self.training_candidate_answer_text, "Подходящих шаблонов не найдено — используй вкладку «Свой ответ».")

    def _training_selected_template_id(self) -> Optional[int]:
        if not hasattr(self, "training_candidates_tree"):
            return None
        selected = self.training_candidates_tree.selection()
        if not selected:
            focused = self.training_candidates_tree.focus()
            selected = (focused,) if focused else ()
        if not selected:
            return None
        value = int(self.training_candidate_map.get(selected[0], 0) or 0)
        return value or None

    def _training_candidate_selected(self) -> None:
        template_id = self._training_selected_template_id()
        if not template_id:
            self._set_text(self.training_candidate_answer_text, "")
            return
        template = self.knowledge.get_template(template_id)
        if not template:
            self._set_text(self.training_candidate_answer_text, "Шаблон удалён.")
            return
        answer = str(template.get("answer_template", "")).strip() or "—"
        self._set_text(self.training_candidate_answer_text, answer)

    def _training_current_ticket(self) -> tuple[str, str]:
        ticket_id = str(self.training_selected_ticket_id or "")
        if not ticket_id:
            selected = self.pending_tree.selection() if hasattr(self, "pending_tree") else ()
            ticket_id = str(selected[0]) if selected else ""
        if not ticket_id:
            return "", ""
        row = next((row for row in self.knowledge.pending_rows() if str(row[0]) == ticket_id), None)
        if not row:
            return "", ""
        return ticket_id, str(row[1]).strip()

    def _finish_training_ticket(self, ticket_id: str, message: str) -> None:
        self.knowledge.delete_pending(str(ticket_id))
        if hasattr(self, "pending_tree"):
            try:
                self.pending_tree.selection_remove(*self.pending_tree.selection())
            except Exception:
                pass
        self._clear_training_solution()
        self._refresh_all()
        self.status_var.set(message)

    def training_teach_selected(self) -> None:
        ticket_id, question = self._training_current_ticket()
        if not ticket_id or not question:
            messagebox.showinfo("Обучение", "Сначала выбери тикет слева")
            return
        template_id = self._training_selected_template_id()
        if not template_id:
            messagebox.showinfo("Обучение", "Выбери правильный шаблон из кандидатов")
            return
        template = self.knowledge.get_template(template_id)
        if not template:
            messagebox.showerror("Обучение", "Шаблон удалён или не найден")
            return
        template_name = str(template.get("name", f"ID {template_id}"))
        if not bool(template.get("enabled", 1)):
            if not messagebox.askyesno("Шаблон выключен", f"Шаблон «{template_name}» выключен. Включить его и обучить?"):
                return
            self.knowledge.set_templates_enabled([template_id], True)
        if not messagebox.askyesno(
            "Обучить",
            f"Считать шаблон «{template_name}» правильным для тикета #{ticket_id}?\n\n{question}",
        ):
            return
        ok, message = self.knowledge.teach_question_to_template(question, template_id)
        if not ok:
            messagebox.showerror("Обучение", message)
            return
        self.last_learned_var.set(f"Тикет #{ticket_id} → {template_name}")
        self._finish_training_ticket(ticket_id, f"Тикет #{ticket_id} обучен: {template_name}")

    def training_create_manual_template(self) -> None:
        ticket_id, question = self._training_current_ticket()
        if not ticket_id or not question:
            messagebox.showinfo("Обучение", "Сначала выбери тикет слева")
            return
        answer = self.training_manual_answer_text.get("1.0", END).strip()
        if not answer:
            messagebox.showinfo("Обучение", "Вставь или напиши правильный ответ")
            return
        name = self.training_manual_name_var.get().strip()
        preview = answer if len(answer) <= 500 else answer[:500] + "…"
        if not messagebox.askyesno(
            "Создать шаблон и обучить",
            f"Сохранить этот ответ для тикета #{ticket_id}?\n\nВопрос:\n{question}\n\nОтвет:\n{preview}",
        ):
            return
        ok, template_id, message = self.knowledge.create_template_from_manual_answer(question, answer, name)
        if not ok:
            messagebox.showerror("Обучение", message)
            return
        template = self.knowledge.get_template(template_id) or {}
        template_name = str(template.get("name", name or template_id))
        self.last_learned_var.set(f"Тикет #{ticket_id} → {template_name}")
        self._finish_training_ticket(ticket_id, f"Тикет #{ticket_id} обучен: {template_name}")

    def training_open_selected_template(self) -> None:
        template_id = self._training_selected_template_id()
        if not template_id:
            messagebox.showinfo("Обучение", "Сначала выбери шаблон")
            return
        self.template_selected_id = template_id
        self.template_search_var.set("")
        self.template_filter_var.set("Все")
        self.notebook.select(self.templates_tab)
        self._refresh_templates()
        iid = str(template_id)
        if self.templates_tree.exists(iid):
            self.templates_tree.selection_set(iid)
            self.templates_tree.focus(iid)
            self.templates_tree.see(iid)
            self._template_selected()

    def delete_pending(self) -> None:
        selected = list(self.pending_tree.selection())
        if not selected:
            messagebox.showinfo("Обучение", "Выдели один или несколько тикетов")
            return
        if not messagebox.askyesno("Обучение", f"Удалить из очереди выбранные тикеты ({len(selected)})?"):
            return
        for ticket_id in selected:
            self.knowledge.delete_pending(str(ticket_id))
        self._clear_training_solution()
        self._refresh_pending()
        self._refresh_stats()

    def delete_all_pending(self) -> None:
        rows = self.knowledge.pending_rows()
        if not rows:
            return
        if not messagebox.askyesno("Обучение", f"Удалить ВСЕ ожидающие тикеты из очереди ({len(rows)})?\n\nУже обученные шаблоны не удаляются."):
            return
        count = self.knowledge.clear_pending()
        self.status_var.set(f"Очередь обучения очищена: {count}")
        self._clear_training_solution()
        self._refresh_all()

    def save_learning_filters(self) -> None:
        exclusions = split_phrases(self.learning_exclusions_text.get("1.0", END))
        self.config["learning_exclusions"] = exclusions
        self.config["learning_noise_filter"] = bool(self.learning_noise_var.get())
        self.config["learning_policy"] = {
            "Умно: неизвестные + исправления": "unknown_or_corrections",
            "Только неизвестные": "unknown_only",
            "Все ручные ответы": "all",
        }.get(self.learning_policy_var.get(), "unknown_or_corrections")
        self.config["learning_only_corrections"] = self.learning_policy_var.get() != "Все ручные ответы"
        if hasattr(self, "global_close_text"):
            self.config["global_close_phrases"] = split_phrases(self.global_close_text.get("1.0", END))
        self.config["auto_close_enabled"] = bool(self.auto_close_enabled_var.get())
        self.config["local_ai_enabled"] = False
        self.config["context_engine_version"] = 2
        save_config(self.config)
        if self.telegram:
            self.telegram.config.update({key: value for key, value in self.config.items()
                                             if key != "command_relay_needs_check"})
        close_count = len(self._global_close_phrases())
        self.status_var.set(f"Фильтры сохранены · исключений обучения: {len(exclusions)} · автозакрытие: {close_count}")

    def cleanup_pending(self) -> None:
        self.save_learning_filters()
        removed = self.knowledge.cleanup_pending_noise(self._learning_exclusions())
        self.status_var.set(f"Очередь очищена от мусора: {removed}")
        self._refresh_all()

    def _refresh_templates(self) -> None:
        if not hasattr(self, "templates_tree"):
            return
        selected_id = str(self.template_selected_id) if self.template_selected_id else None
        selected_before = set(self.templates_tree.selection())
        focused_before = self.templates_tree.focus()
        query = " ".join(self.template_search_var.get().lower().split())
        filter_value = self.template_filter_var.get()

        all_rows = self.knowledge.template_rows()
        self.template_rows_cache = {int(row["id"]): row for row in all_rows}
        filtered: list[dict[str, Any]] = []
        for row in all_rows:
            if filter_value == "Включённые" and not bool(row["enabled"]):
                continue
            if filter_value == "Отключённые" and bool(row["enabled"]):
                continue
            if filter_value == "Мои" and str(row["source"]) != "manual" and int(row["manual_count"] or 0) == 0:
                continue
            if filter_value == "Исходные" and str(row["source"]) != "seed":
                continue
            blob = " ".join(
                str(row.get(key, "") or "")
                for key in ("name", "answer_template", "triggers", "negative_triggers", "close_triggers", "example_text")
            ).lower()
            normalized_blob = " ".join(blob.split())
            if query and not all(term in normalized_blob for term in query.split()):
                continue
            filtered.append(row)

        for item in self.templates_tree.get_children():
            self.templates_tree.delete(item)

        self.template_visible_ids = []
        for row in filtered:
            template_id = int(row["id"])
            self.template_visible_ids.append(template_id)
            checked = template_id in self.template_checked_ids
            enabled = bool(row["enabled"])
            mode = "Умный" if str(row.get("match_mode", "smart")) == "smart" else "Точный"
            tags = ["enabled" if enabled else "disabled"]
            if str(row["source"]) == "manual" or int(row["manual_count"] or 0) > 0:
                tags.append("manual")
            self.templates_tree.insert(
                "",
                END,
                iid=str(template_id),
                values=(
                    "☑" if checked else "☐",
                    "Активен" if enabled else "Выключен",
                    row["name"],
                    int(row.get("priority", 0) or 0),
                    mode,
                    row["example_count"],
                    row["manual_count"] or 0,
                ),
                tags=tuple(tags),
            )

        enabled_count = sum(1 for row in all_rows if bool(row["enabled"]))
        self.template_summary_var.set(f"{enabled_count} активных · {len(all_rows)} всего")
        self.template_selection_var.set(
            f"Отмечено: {len(self.template_checked_ids)} · показано: {len(filtered)}"
            if self.template_checked_ids
            else f"Показано: {len(filtered)} из {len(all_rows)}"
        )
        restore_selection = [item for item in selected_before if self.templates_tree.exists(item)]
        if restore_selection:
            self.templates_tree.selection_set(*restore_selection)
            focus_target = focused_before if focused_before in restore_selection else restore_selection[-1]
            self.templates_tree.focus(focus_target)
        elif selected_id and self.templates_tree.exists(selected_id):
            self.templates_tree.selection_set(selected_id)
            self.templates_tree.focus(selected_id)

    def _template_selected(self) -> None:
        if not hasattr(self, "templates_tree"):
            return
        focused = self.templates_tree.focus()
        selected = self.templates_tree.selection()
        item_id = focused if focused in selected else (selected[0] if selected else "")
        if not item_id:
            return
        template_id = int(item_id)
        data = self.knowledge.get_template(template_id)
        if not data:
            return
        self.template_selected_id = template_id
        self.template_name_var.set(str(data["name"]))
        self.template_enabled_var.set(bool(data["enabled"]))
        self.template_match_mode_var.set("Умный" if str(data.get("match_mode", "smart")) == "smart" else "Только точное совпадение")
        self.template_priority_var.set(max(0, min(100, int(data.get("priority", 0) or 0))))
        self.template_reply_limit_var.set(bool(data.get("reply_limit_enabled", True)))
        source_label = "Базовый шаблон" if str(data.get("source", "")) == "seed" else "Твой/обученный шаблон"
        self.template_source_var.set(f"{source_label} · ID {template_id}")
        self._set_text(self.template_answer_text, str(data["answer_template"]), readonly=False)
        self._set_text(self.template_triggers_text, str(data["triggers"] or ""), readonly=False)
        self._set_text(self.template_negative_text, str(data["negative_triggers"] or ""), readonly=False)
        self._set_text(self.template_close_text, str(data.get("close_triggers", "") or ""), readonly=False)
        examples = self.knowledge.template_examples(template_id)
        lines: list[str] = []
        for index, (question, source, created_at) in enumerate(examples, start=1):
            label = "ТВОЁ РЕШЕНИЕ" if source == "manual" else "база"
            one_line = " ".join(str(question).split())
            lines.append(f"{index:02d}. [{label}] {one_line}\n    {created_at}")
        self._set_text(
            self.template_examples_text,
            "\n\n".join(lines) if lines else (
                "У шаблона пока нет примеров. Для умного режима добавь минимум два триггера. "
                "В точном режиме он начнёт работать после обучения на конкретной ситуации."
            ),
        )

    def save_template(self) -> None:
        answer = self.template_answer_text.get("1.0", END).strip()
        triggers = self.template_triggers_text.get("1.0", END)
        negative = self.template_negative_text.get("1.0", END)
        close_triggers = self.template_close_text.get("1.0", END)
        match_mode = "exact" if self.template_match_mode_var.get() == "Только точное совпадение" else "smart"
        try:
            if self.template_selected_id:
                self.knowledge.update_template(
                    self.template_selected_id,
                    self.template_name_var.get(),
                    answer,
                    self.template_enabled_var.get(),
                    triggers,
                    negative,
                    close_triggers,
                    match_mode,
                    self.template_priority_var.get(),
                    self.template_reply_limit_var.get(),
                )
                saved_id = self.template_selected_id
                notice = "Изменения сохранены"
            else:
                saved_id = self.knowledge.create_template(
                    self.template_name_var.get(),
                    answer,
                    self.template_enabled_var.get(),
                    triggers,
                    negative,
                    close_triggers,
                    match_mode,
                    self.template_priority_var.get(),
                    self.template_reply_limit_var.get(),
                )
                self.template_selected_id = saved_id
                notice = "Новый шаблон создан"
        except ValueError as exc:
            messagebox.showerror("Шаблоны", str(exc))
            return

        self._refresh_templates()
        if self.templates_tree.exists(str(saved_id)):
            self.templates_tree.selection_set(str(saved_id))
            self.templates_tree.focus(str(saved_id))
            self.templates_tree.see(str(saved_id))
        self._template_selected()
        self.status_var.set(notice)

    def _set_templates_sash(self, pane: PanedWindow) -> None:
        try:
            pane.update_idletasks()
            width = max(pane.winfo_width(), self.root.winfo_width() - 40)
            target = max(430, min(int(width * 0.43), max(430, width - 540)))
            pane.sash_place(0, target, 1)
        except Exception:
            LOGGER.exception("Failed to position template splitter")

    def _schedule_template_refresh(self, *_: Any) -> None:
        if self._template_search_job:
            try:
                self.root.after_cancel(self._template_search_job)
            except Exception:
                pass
        self._template_search_job = self.root.after(180, self._refresh_templates)

    def _template_tree_press(self, event: Any) -> Optional[str]:
        region = self.templates_tree.identify_region(event.x, event.y)
        column = self.templates_tree.identify_column(event.x)

        # Clicking the checkbox heading toggles all currently visible rows.
        if region == "heading" and column == "#1":
            all_checked = bool(self.template_visible_ids) and all(
                item in self.template_checked_ids for item in self.template_visible_ids
            )
            self._mark_visible_templates(not all_checked)
            return "break"

        if region not in {"cell", "tree"}:
            self._template_drag_anchor = None
            return None

        row_id = self.templates_tree.identify_row(event.y)
        if not row_id:
            self._template_drag_anchor = None
            return None

        ctrl = bool(event.state & 0x0004)
        shift = bool(event.state & 0x0001)

        if column == "#1":
            template_id = int(row_id)
            if template_id in self.template_checked_ids:
                self.template_checked_ids.remove(template_id)
            else:
                self.template_checked_ids.add(template_id)
            self.templates_tree.selection_set(row_id)
            self.templates_tree.focus(row_id)
            self.template_selected_id = template_id
            self._refresh_templates()
            self._template_selected()
            return "break"

        children = list(self.templates_tree.get_children())
        current_selection = set(self.templates_tree.selection())

        if shift and self._template_drag_anchor in children:
            anchor_index = children.index(self._template_drag_anchor)
            row_index = children.index(row_id)
            lo, hi = sorted((anchor_index, row_index))
            range_ids = set(children[lo : hi + 1])
            if ctrl:
                range_ids.update(current_selection)
            self.templates_tree.selection_set(*[item for item in children if item in range_ids])
        elif ctrl:
            if row_id in current_selection:
                self.templates_tree.selection_remove(row_id)
            else:
                self.templates_tree.selection_add(row_id)
            self._template_drag_anchor = row_id
        else:
            self.templates_tree.selection_set(row_id)
            self._template_drag_anchor = row_id

        self.templates_tree.focus(row_id)
        self._template_drag_base_selection = set(self.templates_tree.selection()) if ctrl else set()
        self._template_drag_ctrl = ctrl
        self._template_drag_moved = False
        self._template_selected()
        return "break"

    def _template_tree_drag(self, event: Any) -> Optional[str]:
        if not self._template_drag_anchor:
            return None

        height = self.templates_tree.winfo_height()
        if event.y < 4:
            self.templates_tree.yview_scroll(-1, "units")
        elif event.y > height - 4:
            self.templates_tree.yview_scroll(1, "units")

        row_id = self.templates_tree.identify_row(max(1, min(event.y, max(1, height - 2))))
        children = list(self.templates_tree.get_children())
        if not row_id or row_id not in children or self._template_drag_anchor not in children:
            return "break"

        anchor_index = children.index(self._template_drag_anchor)
        row_index = children.index(row_id)
        lo, hi = sorted((anchor_index, row_index))
        range_ids = set(children[lo : hi + 1])
        if self._template_drag_ctrl:
            range_ids.update(self._template_drag_base_selection)
        ordered = [item for item in children if item in range_ids]
        if ordered:
            self.templates_tree.selection_set(*ordered)
            self.templates_tree.focus(row_id)
            self._template_drag_moved = True
        return "break"

    def _template_tree_release(self, event: Any) -> Optional[str]:
        if self._template_drag_anchor:
            row_id = self.templates_tree.identify_row(event.y)
            if row_id:
                self.templates_tree.focus(row_id)
            self._template_selected()
        self._template_drag_anchor = None
        self._template_drag_base_selection.clear()
        self._template_drag_ctrl = False
        self._template_drag_moved = False
        return "break"

    def _toggle_selected_template_marks(self, event: Any = None) -> str:
        selected = [int(item) for item in self.templates_tree.selection()]
        if not selected:
            return "break"
        should_mark = any(item not in self.template_checked_ids for item in selected)
        for template_id in selected:
            if should_mark:
                self.template_checked_ids.add(template_id)
            else:
                self.template_checked_ids.discard(template_id)
        self._refresh_templates()
        return "break"

    def _select_all_visible_templates(self, event: Any = None) -> str:
        visible = [str(item) for item in self.template_visible_ids if self.templates_tree.exists(str(item))]
        if visible:
            self.templates_tree.selection_set(*visible)
            self.templates_tree.focus(visible[0])
            self._template_selected()
        return "break"

    def _mark_visible_templates(self, marked: bool) -> None:
        if marked:
            self.template_checked_ids.update(self.template_visible_ids)
        else:
            self.template_checked_ids.difference_update(self.template_visible_ids)
        self._refresh_templates()

    def _invert_visible_templates(self) -> None:
        for template_id in self.template_visible_ids:
            if template_id in self.template_checked_ids:
                self.template_checked_ids.remove(template_id)
            else:
                self.template_checked_ids.add(template_id)
        self._refresh_templates()

    def _marked_or_selected_template_ids(self) -> list[int]:
        if self.template_checked_ids:
            return sorted(self.template_checked_ids)
        return sorted({int(item) for item in self.templates_tree.selection()})

    def _set_marked_templates_enabled(self, enabled: bool) -> None:
        template_ids = self._marked_or_selected_template_ids()
        if not template_ids:
            messagebox.showinfo("Шаблоны", "Отметь галочками хотя бы один шаблон")
            return
        count = self.knowledge.set_templates_enabled(template_ids, enabled)
        self.status_var.set(f"{'Включено' if enabled else 'Выключено'} шаблонов: {count}")
        self._refresh_all()

    def _set_all_templates_enabled(self, enabled: bool) -> None:
        action = "включить" if enabled else "выключить"
        if not messagebox.askyesno("Шаблоны", f"Точно {action} все шаблоны?"):
            return
        count = self.knowledge.set_all_templates_enabled(enabled)
        self.status_var.set(f"{'Включено' if enabled else 'Выключено'} шаблонов: {count}")
        self._refresh_all()

    def _toggle_focused_template_enabled(self) -> None:
        item_id = self.templates_tree.focus()
        if not item_id:
            return
        row = self.template_rows_cache.get(int(item_id))
        if not row:
            return
        self.knowledge.set_templates_enabled([int(item_id)], not bool(row["enabled"]))
        self._refresh_all()

    def _template_context_menu(self, event: Any) -> str:
        tree = self.templates_tree
        menu = getattr(self, "_template_popup_menu", None)
        row_id = tree.identify_row(event.y)
        if tree.identify_region(event.x, event.y) not in {"cell", "tree"} or not row_id:
            if menu is not None:
                menu.unpost()
            return "break"
        template_id = int(row_id)
        if self.knowledge.get_template(template_id) is None:
            return "break"
        tree.focus_set()
        tree.selection_set(row_id)
        tree.focus(row_id)
        self._template_selected()
        if menu is None:
            themed = getattr(self, "_popup_menu", None)
            menu = self._template_popup_menu = themed(tree) if themed else Menu(
                tree, tearoff=False, bg="#191925", fg="#F3F2FA",
                activebackground="#2A2448", activeforeground="#F59AAE",
                bd=0, relief="flat", font=(getattr(self, "ui_font", "Segoe UI"), 10),
            )
        menu.delete(0, "end")
        # Capture this row's ID; marked templates and later focus changes
        # must not change the target of a context-menu deletion.
        command = lambda tid=template_id: self._delete_template_from_menu(tid)
        if isinstance(menu, Menu):
            menu.add_command(label="Удалить", command=command)
        else:
            menu.add_command(label="Удалить", command=command, icon="trash")
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def _delete_template_from_menu(self, template_id: int) -> None:
        template = self.knowledge.get_template(template_id)
        if template is None:
            return
        if not messagebox.askyesno(
            "Удаление шаблона",
            f"Удалить шаблон «{template['name']}» вместе с его обучающими примерами?\n\nЭто действие нельзя отменить.",
        ):
            return
        deleted = self.knowledge.delete_templates([template_id])
        self.template_checked_ids.discard(template_id)
        if self.template_selected_id == template_id:
            self.new_template()
        self.status_var.set(f"Удалено шаблонов: {deleted}")
        self._refresh_all()

    def _delete_marked_templates(self) -> None:
        template_ids = self._marked_or_selected_template_ids()
        if not template_ids:
            messagebox.showinfo("Шаблоны", "Отметь галочками шаблоны для удаления")
            return
        if not messagebox.askyesno(
            "Удаление шаблонов",
            f"Удалить выбранные шаблоны ({len(template_ids)}) вместе с их обучающими примерами?\n\nЭто действие нельзя отменить.",
        ):
            return
        deleted = self.knowledge.delete_templates(template_ids)
        self.template_checked_ids.difference_update(template_ids)
        if self.template_selected_id in template_ids:
            self.new_template()
        self.status_var.set(f"Удалено шаблонов: {deleted}")
        self._refresh_all()

    def _delete_all_templates(self) -> None:
        if not messagebox.askyesno(
            "Удалить все шаблоны",
            "Будут удалены ВСЕ шаблоны и их примеры. Точные правила, история и настройки останутся.\n\nПродолжить?",
        ):
            return
        if not messagebox.askyesno("Последнее подтверждение", "Точно удалить всю библиотеку шаблонов?"):
            return
        deleted = self.knowledge.delete_all_templates()
        self.template_checked_ids.clear()
        self.new_template()
        self.status_var.set(f"Удалена вся библиотека: {deleted} шаблонов")
        self._refresh_all()

    def _restore_seed_templates(self) -> None:
        if not messagebox.askyesno(
            "Вернуть базовые шаблоны",
            "Добавить обратно исходные подтверждённые шаблоны из поставки программы?\nТвои шаблоны не будут удалены.",
        ):
            return
        self.knowledge.restore_seed_templates()
        self.status_var.set("Базовые шаблоны восстановлены")
        self._refresh_all()

    def _cleanup_suspicious_templates(self) -> None:
        count = self.knowledge.cleanup_suspicious_templates()
        if count:
            messagebox.showinfo(
                "Очистка завершена",
                f"Убрано подозрительных или слишком коротких шаблонов: {count}.\n"
                "Их конкретные ситуации сохранены как точные правила.",
            )
        else:
            messagebox.showinfo("Очистка завершена", "Подозрительных шаблонов не найдено")
        self._refresh_all()

    def new_template(self) -> None:
        self.template_selected_id = None
        try:
            self.templates_tree.selection_remove(self.templates_tree.selection())
        except Exception:
            pass
        self.template_name_var.set("")
        self.template_enabled_var.set(True)
        self.template_match_mode_var.set("Умный")
        self.template_priority_var.set(0)
        self.template_reply_limit_var.set(True)
        self.template_source_var.set("Новый шаблон · сначала заполни полный ответ и минимум два триггера")
        self._set_text(self.template_answer_text, "t a {ticket_id}  @@@", readonly=False)
        self._set_text(self.template_triggers_text, "", readonly=False)
        self._set_text(self.template_negative_text, "", readonly=False)
        self._set_text(self.template_close_text, "", readonly=False)
        self._set_text(
            self.template_examples_text,
            "Новый умный шаблон без примеров должен иметь минимум два точных триггера.\n\n"
            "Для короткого ответа, который подходит только одной ситуации, выбери режим «Только точное совпадение».",
        )

    def _refresh_history(self) -> None:
        if not hasattr(self, "history_tree"):
            return
        self.history_data.clear()
        for item in self.history_tree.get_children():
            self.history_tree.delete(item)
        for row in self.knowledge.history_rows():
            history_id, ticket_id, question, action, confidence, answer, created_at, details = row
            one_line = " ".join(str(question or "").split())
            iid = str(history_id)
            self.history_data[iid] = {"ticket_id": ticket_id, "question": question, "answer": answer, "details": details}
            self.history_tree.insert(
                "",
                END,
                iid=iid,
                values=(f"#{ticket_id}" if ticket_id else "", {"manual_required": "Ручной ответ", "auto_sent": "Отправлено"}.get(action, action), confidence, one_line[:260], created_at),
            )

    def _selected_history_data(self) -> Optional[dict[str, Any]]:
        selected = self.history_tree.selection()
        return self.history_data.get(selected[0]) if selected else None

    def show_history_details(self) -> None:
        data = self._selected_history_data()
        if data:
            messagebox.showinfo(
                "История тикета",
                f"Тикет: #{data.get('ticket_id', '')}\n\nВопрос:\n{data.get('question', '')}\n\nОтвет:\n{data.get('answer', '')}\n\nПричина:\n{data.get('details', '')}",
            )

    def unmark_selected_history(self) -> None:
        data = self._selected_history_data()
        if not data or not data.get("ticket_id"):
            messagebox.showwarning("История", "Выберите запись с тикетом")
            return
        self.knowledge.unmark_processed(str(data["ticket_id"]))
        self._refresh_stats()
        messagebox.showinfo("История", "Отметка обработки снята. Защита от повторов и счётчики автоответов продолжают действовать.")

    def _maybe_show_whats_new(self) -> None:
        if str(self.config.get("last_seen_changelog_version", "")) != APP_VERSION:
            self.show_whats_new()

    def show_whats_new(self) -> None:
        messagebox.showinfo(f"Ares Assistant {APP_VERSION} — что нового", CHANGELOG_TEXT)
        self.config["last_seen_changelog_version"] = APP_VERSION
        try:
            save_config(self.config)
        except Exception:
            LOGGER.exception("Unable to save changelog state")

    def _write_update_bundle(self, path: Path) -> None:
        temp_dir = Path(tempfile.mkdtemp(prefix="AresAssistant_update_"))
        try:
            destination = sqlite3.connect(temp_dir / "knowledge.db")
            try:
                with self.knowledge.lock:
                    self.knowledge.connection.backup(destination)
            finally:
                destination.close()

            # Telegram secrets/session are intentionally not exported. The active installation
            # already keeps them across EXE updates, while the portable backup stays safer.
            settings = dict(self.config)
            for key in ("api_id", "api_hash", "phone", "auth_method", "monitoring", "last_window_geometry", "last_seen_changelog_version"):
                settings.pop(key, None)
            (temp_dir / "settings.json").write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
            manifest = {
                "format": "aresassistant-update",
                "format_version": 1,
                "app_version": APP_VERSION,
                "contains": ["knowledge.db", "settings.json"],
            }
            (temp_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            (temp_dir / "README.txt").write_text(
                "Ares Assistant update backup. Contains templates, triggers, stop phrases, priorities, learning examples and non-secret settings.\n",
                encoding="utf-8",
            )
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
                for name in ("manifest.json", "knowledge.db", "settings.json", "README.txt"):
                    archive.write(temp_dir / name, name)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def export_update_bundle(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Экспорт данных для обновления",
            defaultextension=".aresupdate",
            filetypes=(("Ares update backup", "*.aresupdate"), ("ZIP", "*.zip")),
            initialfile=f"AresAssistant_{APP_VERSION}_update.aresupdate",
        )
        if not path:
            return
        try:
            self._write_update_bundle(Path(path))
            messagebox.showinfo(
                "Экспорт обновления",
                "Готово. Сохранены шаблоны, триггеры, стоп-фразы, автозакрытие, приоритеты, обучение и настройки. Telegram-секреты не экспортируются.",
            )
        except Exception as exc:
            LOGGER.exception("Unable to export update bundle")
            messagebox.showerror("Экспорт обновления", f"Не удалось сохранить пакет: {exc}")

    def import_update_bundle(self) -> None:
        path = filedialog.askopenfilename(
            title="Импорт данных после обновления",
            filetypes=(("Ares update backup", "*.aresupdate"), ("ZIP", "*.zip"), ("Все файлы", "*.*")),
        )
        if not path:
            return
        temp_dir = Path(tempfile.mkdtemp(prefix="AresAssistant_import_"))
        try:
            with zipfile.ZipFile(path, "r") as archive:
                names = set(archive.namelist())
                if "knowledge.db" not in names or "manifest.json" not in names:
                    raise ValueError("Это не пакет обновления Ares Assistant")
                manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
                if manifest.get("format") != "aresassistant-update":
                    raise ValueError("Неизвестный формат пакета")
                (temp_dir / "knowledge.db").write_bytes(archive.read("knowledge.db"))
                settings_data = {}
                if "settings.json" in names:
                    settings_data = json.loads(archive.read("settings.json").decode("utf-8"))
                    if not isinstance(settings_data, dict):
                        settings_data = {}

            # Automatic safety snapshot before merging. It stays in AppData/backups.
            backup_dir = APPDATA_ROOT / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup_path = backup_dir / f"before_import_{APP_VERSION}.aresupdate"
            try:
                self._write_update_bundle(backup_path)
            except Exception:
                LOGGER.exception("Unable to create pre-import backup")

            stats = self.knowledge.merge_from_database(temp_dir / "knowledge.db")
            blocked = {"api_id", "api_hash", "phone", "auth_method", "monitoring", "last_window_geometry", "last_seen_changelog_version", "command_relay_needs_check"}
            for key, value in settings_data.items():
                if key not in blocked:
                    self.config[key] = value
            save_config(self.config)
            self.local_ai.config.update(self.config)
            if self.telegram:
                self.telegram.config.update({key: value for key, value in self.config.items()
                                             if key != "command_relay_needs_check"})
                self.telegram.update_command_relay()
            self._load_settings_into_ui()
            self._refresh_all()
            messagebox.showinfo(
                "Импорт обновления",
                "Данные объединены.\n\n"
                f"Шаблонов добавлено: {stats['templates_added']}\n"
                f"Шаблонов обновлено: {stats['templates_updated']}\n"
                f"Защищённых не изменено: {stats.get('templates_protected', 0)}\n"
                f"Новых примеров: {stats['examples_added']}\n"
                f"Точных правил: {stats['exact_rules']}\n\n"
                "Текущий Telegram-вход сохранён.",
            )
        except Exception as exc:
            LOGGER.exception("Unable to import update bundle")
            messagebox.showerror("Импорт обновления", f"Не удалось импортировать пакет: {exc}")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def export_knowledge(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Экспорт базы обучения",
            defaultextension=".ticket",
            filetypes=(("Ticket dataset", "*.ticket"), ("Text", "*.txt")),
            initialfile="ares_assistant_training.ticket",
        )
        if path:
            self.knowledge.export_examples(Path(path))
            messagebox.showinfo("Экспорт", "База обучения сохранена")

    def export_diagnostics_bundle(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Сохранить пакет диагностики",
            defaultextension=".zip",
            filetypes=(("ZIP", "*.zip"),),
            initialfile=f"AresAssistant_diagnostics_{APP_VERSION}.zip",
        )
        if not path:
            return
        try:
            safe_config = dict(self.config)
            for key in ("api_hash", "phone"):
                if key in safe_config:
                    safe_config[key] = "***"
            temp_dir = Path(tempfile.mkdtemp(prefix="AresAssistant_diag_"))
            try:
                (temp_dir / "config_sanitized.json").write_text(json.dumps(safe_config, ensure_ascii=False, indent=2), encoding="utf-8")
                (temp_dir / "version.txt").write_text(APP_VERSION, encoding="utf-8")
                if LOG_FILE.exists():
                    shutil.copy2(LOG_FILE, temp_dir / "app.log")
                if DB_PATH.exists():
                    # sqlite backup is safer than copying a live WAL database directly.
                    import sqlite3
                    destination = sqlite3.connect(temp_dir / "knowledge.db")
                    try:
                        with self.knowledge.lock:
                            self.knowledge.connection.backup(destination)
                    finally:
                        destination.close()
                self.knowledge.export_examples(temp_dir / "training_export.ticket")
                with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
                    for item in temp_dir.iterdir():
                        archive.write(item, item.name)
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)
            messagebox.showinfo("Диагностика", "Пакет логов, базы и обезличенных настроек сохранён")
        except Exception as exc:
            LOGGER.exception("Unable to export diagnostics bundle")
            messagebox.showerror("Диагностика", f"Не удалось собрать пакет: {exc}")

    def run_diagnostics(self) -> None:
        lines = [
            f"Ares Assistant {APP_VERSION}",
            f"Python: {sys.version}",
            f"Данные и обучение: {APPDATA_ROOT}",
            f"Среда программы: {RUNTIME_ROOT}",
            f"Лог: {LOG_FILE}",
            "",
        ]
        checks = [
            ("tkinter", lambda: self.root.winfo_exists()),
            ("Telethon", lambda: __import__("telethon")),
            ("qrcode", lambda: __import__("qrcode")),
            ("edge-tts", lambda: __import__("edge_tts")),
            ("pywin32", lambda: __import__("win32api")),
            ("Seed dataset", lambda: SEED_PATH.exists() and SEED_PATH.stat().st_size > 0),
            ("Database", lambda: DB_PATH.exists()),
        ]
        for name, check in checks:
            try:
                result = check()
                lines.append(f"[OK] {name}" if result is not False else f"[FAIL] {name}")
            except Exception as exc:
                lines.append(f"[FAIL] {name}: {exc}")
        lines.extend(
            [
                "",
                f"Telegram: {'подключён' if self.telegram and self.telegram.connected else 'не подключён'}",
                f"Режим: {self.mode_var.get()}",
                f"Голос: {'включён' if self.voice else 'выключен'}",
                f"Микрофон: {self.microphone_var.get()}",
            ]
        )
        self._set_text(self.diagnostics_text, "\n".join(lines))

    def reset_session(self) -> None:
        if self.telegram and self.telegram.connected:
            messagebox.showwarning("Telegram", "Сначала отключите Telegram")
            return
        session_files = list(SESSION_PATH.parent.glob(SESSION_PATH.name + "*"))
        if not session_files:
            messagebox.showinfo("Telegram", "Сохранённой сессии нет")
            return
        if not messagebox.askyesno("Telegram", "Удалить Telegram-сессию? При следующем входе появится QR-код."):
            return
        for path in session_files:
            try:
                path.unlink()
            except Exception:
                LOGGER.exception("Unable to delete session file %s", path)
        messagebox.showinfo("Telegram", "Сессия удалена")

    @staticmethod
    def _ps_quote(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    def uninstall_everything(self) -> None:
        if not messagebox.askyesno(
            "Полное удаление",
            "Будут удалены программа, автозапуск, Telegram-сессия, настройки, логи и вся обученная база. Продолжить?",
            icon="warning",
        ):
            return
        confirm = simpledialog.askstring("Подтверждение", "Для полного удаления напишите: УДАЛИТЬ", parent=self.root)
        if (confirm or "").strip().upper() != "УДАЛИТЬ":
            messagebox.showinfo("Полное удаление", "Удаление отменено")
            return

        self.uninstalling = True
        self._apply_autostart(False)
        paths = {str(APPDATA_ROOT), str(RUNTIME_ROOT)}
        for candidate in (LAUNCHER_EXE, INSTALL_SOURCE_FILE):
            candidate = str(candidate or "").strip()
            if candidate:
                paths.add(candidate)
        startup = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "AresAssistant.lnk"
        desktop_link = Path(os.environ.get("USERPROFILE", "")) / "Desktop" / "Ares Assistant.lnk"
        paths.update({str(startup), str(desktop_link)})

        script_path = Path(tempfile.gettempdir()) / f"AresAssistant_uninstall_{os.getpid()}.ps1"
        remove_lines = "\n".join(
            f"Remove-Item -LiteralPath {self._ps_quote(path)} -Recurse -Force -ErrorAction SilentlyContinue"
            for path in sorted(paths, key=len, reverse=True)
            if path
        )
        script = f"""
$ErrorActionPreference = 'SilentlyContinue'
Start-Sleep -Seconds 3
Remove-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run' -Name 'AresAssistant' -Force
schtasks.exe /Delete /TN AresAssistant /F | Out-Null
{remove_lines}
Start-Sleep -Milliseconds 400
Remove-Item -LiteralPath $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue
""".strip()
        script_path.write_text(script, encoding="utf-8-sig")
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script_path)],
            close_fds=True,
            creationflags=creationflags,
        )
        messagebox.showinfo("Полное удаление", "Ares Assistant закроется. Через несколько секунд все данные и привязки к Windows будут удалены.")
        self.shutdown(save_state=False)

    @staticmethod
    def _set_text(widget: Text, value: str, readonly: bool = True) -> None:
        try:
            widget.configure(state="normal")
            widget.delete("1.0", END)
            widget.insert("1.0", value)
            if readonly:
                widget.configure(state="disabled")
        except Exception:
            LOGGER.exception("Unable to update text widget")

    def shutdown(self, save_state: bool = True) -> None:
        if self.shutting_down:
            return
        self.shutting_down = True
        if save_state and not self.uninstalling:
            try:
                self.config["last_window_geometry"] = self.root.geometry()
                self.config["monitoring"] = bool(self.telegram and self.telegram.connected)
                save_config(self.config)
            except Exception:
                pass
        if self.telegram:
            self.telegram.stop()
        if self.voice:
            self.voice.stop()
        self.tray.stop()
        try:
            self.knowledge.close()
        except Exception:
            pass
        self.root.after(180, self.root.destroy)


from modern_ui import ModernUI


class AresAssistantApp(ModernUI, LegacyAresAssistantApp):
    pass


def main() -> int:
    install_exception_hook()
    if os.name == 'nt':
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('AresMine.AresAssistant')
        except Exception:
            LOGGER.debug('Unable to set Windows application ID', exc_info=True)
    root = Tk()
    app_holder: dict[str, Any] = {}

    def ipc_emit(event: str, payload: Any) -> None:
        app = app_holder.get("app")
        if app:
            app.emit(event, payload)

    instance = SingleInstance(ipc_emit)
    if not instance.acquire():
        root.destroy()
        return 0
    app = AresAssistantApp(root)
    app_holder["app"] = app
    try:
        root.mainloop()
    finally:
        instance.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
