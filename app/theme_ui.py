"""Контроллер темы: выбор, сохранение, применение, атмосфера и панель «Оформление».

Как применяется тема
--------------------
1. Палитра P (общий словарь, его видят все модули интерфейса) обновляется на месте
   токенами новой темы (themes.full_palette).
2. ui_assets.set_theme() перезагружает уже открытые картинки НА МЕСТЕ — элементы ttk,
   созданные один раз, сразу получают новые пиксели.
3. Интерфейс перестраивается под «вуалью» — окном цвета фона поверх рабочей области:
   вуаль проявляется (~140 мс), под ней пересобираются виджеты, затем цвет вуали плавно
   переходит в новый фон и она растворяется (~120 + 240 мс). Перестройка идёт целиком
   под непрозрачной вуалью, поэтому сдвиги вёрстки не видны.
4. Перед перестройкой сохраняются: открытая страница, значения переменных, которые
   строители создают заново, и содержимое текстовых полей (несохранённые правки не
   теряются). Атмосфера старой темы останавливается (таймер отменён, частицы удалены),
   трассировки переменных и обработчики колеса мыши на корне снимаются — иначе они
   копились бы при каждой смене темы.

Выбор хранится в config.json ("theme", "atmosphere") и применяется в _configure_style,
то есть до первого показа окна (окно до этого прозрачно) — без вспышки другой темы.
Неизвестные или повреждённые значения заменяются темой и уровнем по умолчанию. Если
сохранить настройки не удалось, тема всё равно применяется до перезапуска.
"""
from __future__ import annotations

import logging
import os
import time
import tkinter as tk
from tkinter import ttk

import ui_assets
import ui_widgets
from ui_assets import photo, icon_name, nine_slice, animate, lerp_color, ease_out_cubic, ease_in_cubic, AnimatedButton
from ui_overlays import PopupMenu, attach_tooltip, _overlay_window, _alive
from ui_widgets import SegmentedControl
from themes import (THEMES, THEME_ORDER, ATMOSPHERE_LEVELS, ATMOSPHERE_LABELS, full_palette, get_theme_id,
                    get_atmosphere, theme_info)
from atmosphere import Atmosphere
from config import save_config

LOGGER = logging.getLogger(__name__)
# Порядок важности токенов для карты цветов: если в старой теме два токена совпадали
# по значению, а в новой разошлись, виджет получает цвет более важного токена.
TOKEN_PRIORITY = ("background", "surface", "surface_high", "sidebar", "field", "text", "text_soft", "muted",
                  "muted_dim", "accent", "accent_hover", "accent_deep", "on_accent", "selection", "border",
                  "border_soft", "green", "amber", "red", "blue", "cyan", "hover_row", "stripe", "surface_3")
ASSET_ONLY_TOKENS = {"accent2"}
# Цвета, которыми рисуются иконки и «цветные» картинки (чипы, точки). Для имён картинок
# строится отдельная карта: в светлых темах белый — это и фон поля, и текст на акценте,
# а иконка должна стать цветом текста на акценте новой темы, а не цветом поля.
ICON_TOKENS = ("on_accent", "accent", "text", "text_soft", "muted", "muted_dim", "green", "amber", "red", "blue",
               "cyan", "danger_hover", "background")
TK_COLOR_OPTIONS = ("background", "foreground", "highlightbackground", "highlightcolor", "insertbackground",
                    "selectbackground", "selectforeground", "activebackground", "activeforeground",
                    "disabledforeground", "inactiveselectbackground", "troughcolor", "readonlybackground",
                    "disabledbackground", "selectcolor")
CANVAS_OPTIONS = {"text": ("fill", "activefill", "disabledfill"), "line": ("fill", "activefill"),
                  "rectangle": ("fill", "outline"), "oval": ("fill", "outline"), "polygon": ("fill", "outline"),
                  "arc": ("fill", "outline")}


def color_map(old, new, tokens=None):
    """{'#старый': '#новый'} по семантическим токенам (в нижнем регистре)."""
    mapping = {}
    keys = list(tokens) if tokens else list(TOKEN_PRIORITY) + sorted(k for k in old if k not in TOKEN_PRIORITY)
    for key in keys:
        if key in ASSET_ONLY_TOKENS:
            continue
        before, after = old.get(key), new.get(key)
        if isinstance(before, str) and isinstance(after, str) and before.startswith("#") and len(before) == 7:
            mapping.setdefault(before.lower(), after)
    return mapping


ATMOSPHERE_HINTS = {
    "off": "Без частиц. Цвета и картинки темы остаются.",
    "delicate": "Немного частиц только в свободных местах — работе не мешают.",
    "expressive": "Больше частиц: заметнее, но рабочие области по-прежнему спокойные.",
}


class ThemeUI:
    # ------------------------------------------------------------ состояние
    def _configured_theme(self):
        return get_theme_id(self.config.get("theme"))

    def _atmosphere_level(self):
        return get_atmosphere(self.config.get("atmosphere"))

    def _theme_is_dark(self):
        return bool(THEMES[getattr(self, "_theme_id", "aurora")]["dark"])

    def _theme_motif(self):
        return THEMES[getattr(self, "_theme_id", "aurora")]["motif"]

    @staticmethod
    def _system_reduced_motion():
        """Windows: «Показывать анимацию в Windows» выключено → сокращённая анимация."""
        if os.name != "nt":
            return False
        try:
            import ctypes
            value = ctypes.c_int(1)
            # SPI_GETCLIENTAREAANIMATION
            if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(value), 0):
                return not bool(value.value)
        except Exception:
            LOGGER.exception("Unable to read the Windows animation setting")
        return False

    def _apply_theme_tokens(self, theme_id):
        theme_id = get_theme_id(theme_id)
        palette = ui_widgets.PALETTE
        if getattr(self, "_theme_id", None) != theme_id or not palette:
            palette.clear()
            palette.update(full_palette(theme_id))
            ui_assets.set_theme(self.root, theme_id)
            self._theme_id = theme_id
        ui_assets.REDUCED_MOTION = self._system_reduced_motion()
        try:
            self.root.configure(bg=palette["background"])
        except tk.TclError:
            pass

    def _element(self, style, name, *args, **kwargs):
        """element_create, безопасный при перестройке: элемент уже существует и
        показывает перезагруженные картинки, поэтому повторное создание не нужно."""
        try:
            style.element_create(name, *args, **kwargs)
        except tk.TclError as exc:
            if "Duplicate element" not in str(exc):
                raise

    # ------------------------------------------------------------ сборка
    def _install_theme_ui(self, sidebar):
        """Вызывается из _build_ui: кнопка «Оформление», холст атмосферы в боковой панели."""
        p = ui_widgets.PALETTE
        actions = self.pause_button.master
        self.theme_button = AnimatedButton(actions, text="", style="Ghost.TButton", command=self.open_theme_picker,
                                           image=self._theme_button_images(), compound="center", cursor="hand2")
        ttk.Style(self.theme_button).configure(str(self.theme_button.cget("style")), padding=(10, 10))
        self.theme_button.configure(width=0)
        self.theme_button.pack(side="left", padx=(0, 8), before=self.pause_button)
        attach_tooltip(self.theme_button, p, self.fnt, lambda: f"Оформление: «{THEMES[self._theme_id]['name']}» · Ctrl+Shift+T")
        self.root.bind("<Control-T>", lambda e: self.open_theme_picker())
        self.root.bind("<Control-Shift-Cyrillic_ie>", lambda e: self.open_theme_picker())
        # Пустой низ боковой панели — спокойное место для частиц; холст не принимает фокус.
        self.sidebar_atmosphere = tk.Canvas(sidebar, bg=p["sidebar"], highlightthickness=0, bd=0, takefocus=0, height=1)
        self.sidebar_atmosphere.pack(fill="both", expand=True, after=self.nav_rail)

    def _theme_button_images(self):
        p = ui_widgets.PALETTE
        normal = photo(self.root, icon_name("palette", 20, p["muted"]))
        hover = photo(self.root, icon_name("palette", 20, p["accent"]))
        return (normal, "active", hover, "focus", hover)

    # ------------------------------------------------------------ атмосфера
    def _start_atmosphere(self):
        self._stop_atmosphere()
        info = theme_info(self._theme_id)
        reduced = ui_assets.REDUCED_MOTION
        self.atmosphere = Atmosphere(self.root, info["atmosphere"], self._atmosphere_level(), reduced)
        hero = getattr(self, "hero", None)
        if hero is not None and _alive(hero):
            def hero_region(h=hero):
                w = max(h.winfo_width(), 700)
                return (w - 400, 8, w - 14, h.H - 8)
            self.atmosphere.add_layer(hero, hero_region, 1.0, below=getattr(hero, "_eyebrow", None))
        side = getattr(self, "sidebar_atmosphere", None)
        if side is not None and _alive(side):
            def side_region(c=side):
                return (12, 12, max(13, c.winfo_width() - 12), max(13, c.winfo_height() - 8))
            self.atmosphere.add_layer(side, side_region, .55)
        self.atmosphere.start()

    def _stop_atmosphere(self):
        atmosphere = getattr(self, "atmosphere", None)
        if atmosphere is not None:
            atmosphere.stop()
        self.atmosphere = None

    def set_atmosphere_level(self, level):
        level = get_atmosphere(level)
        if level == self._atmosphere_level() and getattr(self, "atmosphere", None) is not None:
            return
        self.config["atmosphere"] = level
        self._save_theme_config()
        # Пересчёт слоёв делается после раскладки окна, иначе размеры холстов ещё нулевые.
        self.root.after_idle(self._start_atmosphere)

    def _save_theme_config(self):
        try:
            save_config(self.config)
            return True
        except Exception:
            LOGGER.exception("Unable to save theme settings")
            try:
                self._toast("Не удалось сохранить выбор оформления — он действует до перезапуска", "error")
            except Exception:
                pass
            return False

    # ------------------------------------------------------------ смена темы
    def apply_theme(self, theme_id, reopen_picker=False):
        theme_id = get_theme_id(theme_id)
        if theme_id == getattr(self, "_theme_id", None):
            return
        self.config["theme"] = theme_id
        self._save_theme_config()
        old_bg = ui_widgets.PALETTE.get("background", "#000000")
        new_bg = full_palette(theme_id)["background"]
        veil = None
        reduced = self._system_reduced_motion()
        try:
            if not reduced and self.root.winfo_viewable():
                veil = self._make_veil(old_bg)
        except tk.TclError:
            veil = None

        def finish():
            name = THEMES[theme_id]["name"]
            self.status_var.set(f"Оформление: «{name}»")
            if reopen_picker:
                self.root.after(60, lambda: self.open_theme_picker(focus=theme_id))

        if veil is None:
            self._switch_interface(theme_id)
            finish()
            return

        def rebuild():
            started = time.perf_counter()
            self._switch_interface(theme_id)
            self.root.update_idletasks()
            LOGGER.info("Theme %s applied in %.0f ms", theme_id, (time.perf_counter() - started) * 1000)

            def recolor(t):
                if _alive(veil):
                    veil.configure(bg=lerp_color(old_bg, new_bg, t))

            def fade_out():
                def step(t):
                    try:
                        veil.attributes("-alpha", 1 - t)
                    except tk.TclError:
                        pass
                animate(veil, "out", 220, step, ease_in_cubic, done=lambda: (veil.destroy() if _alive(veil) else None, finish()))
            animate(veil, "recolor", 100, recolor, ease_out_cubic, done=fade_out)

        def step_in(t):
            try:
                veil.attributes("-alpha", t)
            except tk.TclError:
                pass
        animate(veil, "in", 120, step_in, ease_out_cubic, done=rebuild)

    def _make_veil(self, color):
        root = self.root
        root.update_idletasks()
        veil = tk.Toplevel(root)
        veil.withdraw()
        veil.overrideredirect(True)
        veil.configure(bg=color)
        veil.attributes("-alpha", 0.0)
        veil.geometry(f"{root.winfo_width()}x{root.winfo_height()}+{root.winfo_rootx()}+{root.winfo_rooty()}")
        try:
            veil.transient(root)
            veil.attributes("-topmost", True)
        except tk.TclError:
            pass
        veil.deiconify()
        veil.lift()
        self._theme_veil = veil
        return veil

    def _switch_interface(self, theme_id):
        """Быстрый путь — перекраска на месте; при любой ошибке — полная перестройка."""
        try:
            self._restyle_interface(theme_id)
        except Exception:
            LOGGER.exception("Live restyle failed, rebuilding the interface")
            self._rebuild_interface(theme_id)

    def _restyle_interface(self, theme_id):
        """Перекрашивает живой интерфейс: виджеты и их раскладка не пересоздаются.

        * картинки получают пиксели новой темы на месте (ui_assets.set_theme), «цветные»
          иконки — пиксели иконки нового цвета; 9-slice и иконки с отступом пересобираются;
        * стили ttk настраиваются заново (_configure_style);
        * у всех tk-виджетов, элементов холстов, тегов текста и таблиц цвета заменяются
          по карте «старый токен → новый токен»;
        * виджеты со «сплющенными» кадрами (кнопки, переключатели, поля) вызывают restyle().
        """
        root = self.root
        theme_id = get_theme_id(theme_id)
        if PopupMenu._open is not None:
            PopupMenu._open.close(immediate=True)
        for attr in ("_command_window", "_theme_picker_window"):
            window = getattr(self, attr, None)
            if _alive(window):
                window.destroy()
            setattr(self, attr, None)
        if self._toaster is not None:
            try:
                self._toaster.close(immediate=True)
            except tk.TclError:
                pass
            self._toaster = None
        self._stop_atmosphere()
        palette = ui_widgets.PALETTE
        old = dict(palette)
        new = full_palette(theme_id)
        mapping = color_map(old, new)
        image_mapping = color_map(old, new, ICON_TOKENS)
        palette.clear()
        palette.update(new)
        self._theme_id = theme_id
        ui_assets.set_theme(root, theme_id, image_mapping)
        self._recompose_padded_icons(image_mapping)
        self._configure_style()
        root.configure(bg=palette["background"])
        self._restyle_widgets(root, mapping)
        hero = getattr(self, "hero", None)
        if hero is not None and _alive(hero):
            hero.set_motif(THEMES[theme_id]["motif"])
        self._after_restyle()
        self._apply_window_chrome(caption_only=True)
        root.after(120, self._start_atmosphere)

    def _recompose_padded_icons(self, mapping):
        cache = self.root.__dict__.get("_ares_padded", {})
        rebuilt = {}
        for (glyph, size, color, gap, side), image in cache.items():
            new_color = mapping.get(str(color).lower(), color)
            if ui_assets.asset_bytes(icon_name(glyph, size, new_color)) is None:
                new_color = color  # такой иконки в новой теме нет — оставляем прежний цвет
            source = photo(self.root, icon_name(glyph, size, new_color))
            image.blank()
            image.tk.call(image, "copy", source, "-to", gap if side == "left" else 0, 0, "-compositingrule", "set")
            rebuilt.setdefault((glyph, size, new_color, gap, side), image)
        cache.clear()
        cache.update(rebuilt)

    def _restyle_widgets(self, top, mapping):
        def remap(value):
            return mapping.get(str(value).lower()) if value else None

        stack = [top]
        while stack:
            widget = stack.pop()
            try:
                stack.extend(widget.winfo_children())
            except tk.TclError:
                continue
            try:
                if isinstance(widget, ttk.Widget):
                    for option in ("foreground", "background"):
                        try:
                            new = remap(widget.cget(option))
                        except tk.TclError:
                            continue
                        if new:
                            widget.configure(**{option: new})
                    if isinstance(widget, ttk.Treeview):
                        for tag in widget.tk.splitlist(widget.tk.call(widget, "tag", "names")):
                            config = widget.tag_configure(tag)
                            changes = {k: remap(config.get(k)) for k in ("foreground", "background")}
                            changes = {k: v for k, v in changes.items() if v}
                            if changes:
                                widget.tag_configure(tag, **changes)
                else:
                    keys = set(widget.keys())
                    changes = {}
                    for option in TK_COLOR_OPTIONS:
                        if option in keys:
                            new = remap(widget.cget(option))
                            if new:
                                changes[option] = new
                    if changes:
                        widget.configure(**changes)
                    if isinstance(widget, tk.Canvas):
                        for item in widget.find_all():
                            options = CANVAS_OPTIONS.get(widget.type(item))
                            if not options:
                                continue
                            item_changes = {}
                            for option in options:
                                new = remap(widget.itemcget(item, option))
                                if new:
                                    item_changes[option] = new
                            if item_changes:
                                widget.itemconfigure(item, **item_changes)
                    elif isinstance(widget, tk.Text):
                        for tag in widget.tag_names():
                            tag_changes = {}
                            for option in ("foreground", "background", "selectbackground", "selectforeground"):
                                new = remap(widget.tag_cget(tag, option))
                                if new:
                                    tag_changes[option] = new
                            if tag_changes:
                                widget.tag_configure(tag, **tag_changes)
                # Цвета, которые сами виджеты хранят в атрибутах (StatusPill, кнопки и т. п.).
                for name, value in list(vars(widget).items()):
                    if isinstance(value, str) and len(value) == 7 and value.startswith("#"):
                        new = remap(value)
                        if new:
                            setattr(widget, name, new)
                    elif isinstance(value, tuple) and value and all(isinstance(v, str) for v in value):
                        mapped = tuple(remap(v) or v for v in value)
                        if mapped != value:
                            setattr(widget, name, mapped)
                hook = getattr(widget, "restyle", None)
                if callable(hook):
                    hook()
            except tk.TclError:
                LOGGER.debug("Restyle skipped %s", widget, exc_info=True)

    def _after_restyle(self):
        """Цвета, которые интерфейс вычисляет сам (смеси токенов), и зависящие от темы детали."""
        for name in ("_restyle_template_protection",):
            method = getattr(self, name, None)
            if callable(method):
                method()
        for method in (self._sync_header_icons, self._connection_changed, self._refresh_history):
            try:
                method()
            except Exception:
                LOGGER.exception("Restyle hook %s failed", getattr(method, "__name__", method))
        try:
            self.theme_button.configure(image=self._theme_button_images())
        except (AttributeError, tk.TclError):
            pass

    def _rebuild_interface(self, theme_id):
        """Пересобирает виджеты в новой теме, сохраняя страницу, ввод и выбор."""
        root = self.root
        try:
            page = self.notebook.index(self.notebook.select())
        except (tk.TclError, AttributeError):
            page = 0
        self._stop_atmosphere()
        if PopupMenu._open is not None:
            PopupMenu._open.close(immediate=True)
        for attr in ("_command_window", "_theme_picker_window"):
            window = getattr(self, attr, None)
            if _alive(window):
                window.destroy()
            setattr(self, attr, None)
        if self._toaster is not None:
            try:
                self._toaster.close(immediate=True)
            except tk.TclError:
                pass
            self._toaster = None
        variables = {name: (value, value.get()) for name, value in self.__dict__.items() if isinstance(value, tk.Variable)}
        texts = {}
        for name, value in self.__dict__.items():
            if isinstance(value, tk.Text) and _alive(value):
                texts[name] = value.get("1.0", "end-1c")
        veil = getattr(self, "_theme_veil", None)
        for child in list(root.winfo_children()):
            if child is not veil:
                child.destroy()
        # Трассировки и обработчики колеса создаёт сборка интерфейса; перед новой сборкой
        # их снимаем, чтобы они не накапливались от смены к смене.
        for name, (variable, _) in variables.items():
            try:
                for modes, callback in variable.trace_info():
                    variable.trace_remove(modes, callback)
            except tk.TclError:
                pass
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            root.unbind(sequence)
        self._apply_theme_tokens(theme_id)
        self._rebuilding_ui = True
        try:
            self._configure_style()
            self._build_ui()
            self._load_settings_into_ui()
            for name, (old, value) in variables.items():
                current = self.__dict__.get(name)
                if isinstance(current, tk.Variable) and current is not old:
                    try:
                        current.set(value)
                    except tk.TclError:
                        pass
            for name, content in texts.items():
                widget = self.__dict__.get(name)
                if isinstance(widget, tk.Text) and _alive(widget):
                    state = str(widget.cget("state"))
                    widget.configure(state="normal")
                    widget.delete("1.0", "end")
                    widget.insert("1.0", content)
                    widget.configure(state=state)
            try:
                self.notebook.select(page)
            except tk.TclError:
                pass
            self._refresh_all()
            self._connection_changed()
        finally:
            self._rebuilding_ui = False

    # ------------------------------------------------------------ панель выбора
    def open_theme_picker(self, focus=None):
        window = getattr(self, "_theme_picker_window", None)
        if _alive(window):
            window.lift()
            return "break"
        ThemePicker(self, focus or getattr(self, "_theme_id", "aurora"))
        self._prefetch_themes()
        return "break"

    def _prefetch_themes(self):
        """Пока открыта панель, картинки других тем декодируются в фоне небольшими порциями,
        чтобы выбор темы занимал доли секунды. Через 90 с после закрытия память освобождается."""
        job = getattr(self, "_prefetch_release_job", None)
        if job is not None:
            try:
                self.root.after_cancel(job)
            except tk.TclError:
                pass
            self._prefetch_release_job = None
        current = ui_widgets.PALETTE
        names = [name for _, name in getattr(self.root, "_ares_all", [])]
        order = [t for t in THEME_ORDER if t != self._theme_id]

        def run(index=0):
            if index >= len(order):
                return
            theme_id = order[index]
            mapping = color_map(current, full_palette(theme_id), ICON_TOKENS)
            wanted = [ui_assets.remap_name(n, mapping) for n in names]
            ui_assets.prefetch(self.root, theme_id, wanted, done=lambda: run(index + 1))
        self.root.after(250, run)

    def _schedule_prefetch_release(self):
        def release():
            self._prefetch_release_job = None
            ui_assets.release_prefetched(self.root)
        self._prefetch_release_job = self.root.after(90_000, release)

    def _theme_commands(self, terms):
        """Пункты для быстрого поиска (Ctrl+K): «Оформление» и сами темы."""
        p = ui_widgets.PALETTE
        commands = []
        if all(t in "оформление темы тема внешний вид атмосфера" for t in terms):
            commands.append(("Оформление", "Темы и атмосфера интерфейса", "palette", p["accent"], "Настройка",
                             self.open_theme_picker))
        if terms:
            for theme_id in THEME_ORDER:
                theme = THEMES[theme_id]
                haystack = f"{theme['name']} {theme['description']} тема".casefold()
                if all(t in haystack for t in terms):
                    commands.append((f"Тема «{theme['name']}»", theme["description"][:58], "palette", p["accent"], "Тема",
                                     lambda tid=theme_id: self.apply_theme(tid)))
        return commands


class ThemePicker:
    """Компактная панель выбора темы: мини-превью, название, описание, отметка «Активна».

    Управление с клавиатуры: стрелки — выбор карточки, Enter/Пробел — применить,
    Tab — к переключателю атмосферы (стрелки ←/→ меняют уровень), Esc — закрыть.
    """
    CARD_W, CARD_H, GAP, PAD, COLS = 184, 176, 12, 24, 3

    def __init__(self, app, focus_id):
        self.app = app
        self.p = p = ui_widgets.PALETTE
        self.order = list(THEME_ORDER)
        self.index = self.order.index(focus_id) if focus_id in self.order else 0
        self.hover = None
        self.closing = False
        rows = (len(self.order) + self.COLS - 1) // self.COLS
        self.W = self.PAD * 2 + self.COLS * self.CARD_W + (self.COLS - 1) * self.GAP
        self.top = 86
        self.atmo_y = self.top + rows * (self.CARD_H + self.GAP) + 4
        self.H = self.atmo_y + 132
        win, key = _overlay_window(app.root)
        win.title("Оформление")
        app._theme_picker_window = self.win = win
        c = self.canvas = tk.Canvas(win, width=self.W, height=self.H, bg=key, bd=0, highlightthickness=0, takefocus=1)
        c.pack()
        c.create_image(0, 0, anchor="nw", image=nine_slice(c, "palette.png", self.W, self.H, 20))
        c.create_image(self.PAD + 2, 40, anchor="w", image=photo(c, icon_name("palette", 24, p["accent"])))
        c.create_text(self.PAD + 38, 31, anchor="w", text="Оформление", fill=p["text"], font=app.fnt(14, "bold"))
        c.create_text(self.PAD + 38, 54, anchor="w", text="Тема применяется сразу и запоминается",
                      fill=p["muted"], font=app.fnt(9))
        c.create_image(self.W - self.PAD, 40, anchor="e", image=nine_slice(c, "kbd.png", 40, 22, 6))
        c.create_text(self.W - self.PAD - 20, 40, text="Esc", fill=p["muted"], font=app.fnt(8))
        c.create_line(18, 74, self.W - 18, 74, fill=p["palette_line"])
        self.cards = []
        current = getattr(app, "_theme_id", "aurora")
        for i, theme_id in enumerate(self.order):
            col, row = i % self.COLS, i // self.COLS
            x = self.PAD + col * (self.CARD_W + self.GAP)
            y = self.top + row * (self.CARD_H + self.GAP)
            active = theme_id == current
            theme = THEMES[theme_id]
            card = {"id": theme_id, "x": x, "y": y, "active": active}
            card["bg"] = c.create_image(x, y, anchor="nw", image=self._card_image(active, False))
            c.create_image(x + (self.CARD_W - 132) // 2, y + 10, anchor="nw", image=photo(c, f"theme_preview_{theme_id}.png"))
            c.create_text(x + 14, y + 110, anchor="w", text=theme["name"], fill=p["text"], font=app.fnt(10, "bold"))
            c.create_text(x + 14, y + 124, anchor="nw", text=theme["description"], fill=p["muted"], font=app.fnt(8),
                          width=self.CARD_W - 26)
            if active:
                # Отметка активной темы — на превью, чтобы не спорить с длинным названием.
                bx, by = x + (self.CARD_W + 132) // 2 - 6, y + 16
                c.create_image(bx, by, anchor="ne", image=nine_slice(c, "menu.png", 78, 22, 9))
                c.create_image(bx - 72, by + 11, anchor="w", image=photo(c, icon_name("check", 17, p["green"])))
                c.create_text(bx - 53, by + 11, anchor="w", text="Активна", fill=p["green"], font=app.fnt(8, "bold"))
            self.cards.append(card)
        self.ring = c.create_image(-500, -500, anchor="nw", image=nine_slice(c, "nav_focus.png", self.CARD_W, self.CARD_H, 12))
        # Атмосфера.
        y = self.atmo_y
        c.create_text(self.PAD, y + 8, anchor="w", text="АТМОСФЕРА", fill=p["muted_dim"], font=app.fnt(8, "bold"))
        self.switch = SegmentedControl(c, p, app.fnt, [(level, ATMOSPHERE_LABELS[level]) for level in ATMOSPHERE_LEVELS],
                                       command=self._set_level, current=app._atmosphere_level(), bg=p["palette_surface"])
        c.create_window(self.PAD, y + 24, anchor="nw", window=self.switch)
        self.hint = c.create_text(self.PAD, y + 72, anchor="nw", text="", fill=p["muted"], font=app.fnt(9),
                                  width=self.W - self.PAD * 2)
        self._update_hint(app._atmosphere_level())
        c.create_line(18, self.H - 40, self.W - 18, self.H - 40, fill=p["palette_line_soft"])
        c.create_text(self.PAD, self.H - 20, anchor="w", fill=p["muted"], font=app.fnt(8),
                      text="← ↑ → ↓ — выбрать   ·   Enter — применить   ·   Tab — атмосфера   ·   Esc — закрыть")
        self._place_ring(animated=False)
        c.bind("<Motion>", self._motion)
        c.bind("<Leave>", lambda e: self._set_hover(None))
        c.bind("<ButtonRelease-1>", self._click)
        for seq, delta in (("<Left>", -1), ("<Right>", 1), ("<Up>", -self.COLS), ("<Down>", self.COLS)):
            c.bind(seq, lambda e, d=delta: self._move(d))
        for seq in ("<Return>", "<space>"):
            c.bind(seq, lambda e: self._apply(self.order[self.index]))
        win.bind("<Escape>", lambda e: self.close())
        c.bind("<FocusOut>", self._focus_out)
        self.switch.bind("<FocusOut>", self._focus_out, add="+")
        self._position()

    def _card_image(self, active, hover):
        name = "row_active.png" if active else ("tile_hover.png" if hover else "tile.png")
        return nine_slice(self.canvas, name, self.CARD_W, self.CARD_H, 12)

    def _position(self):
        root, win = self.app.root, self.win
        root.update_idletasks()
        x = root.winfo_rootx() + (root.winfo_width() - self.W) // 2
        y = root.winfo_rooty() + max(40, (root.winfo_height() - self.H) // 2 - 20)
        win.geometry(f"{self.W}x{self.H}+{max(0, x)}+{max(0, y) + 12}")
        try:
            win.transient(root)
        except tk.TclError:
            pass
        win.deiconify()
        win.lift()

        def appear(t):
            try:
                win.geometry(f"+{max(0, x)}+{round(max(0, y) + 12 * (1 - t))}")
                win.attributes("-alpha", t)
            except tk.TclError:
                pass
        animate(win, "in", 220, appear, ease_out_cubic)
        win.focus_force()
        self.canvas.focus_set()

    def _update_hint(self, level):
        text = ATMOSPHERE_HINTS[level]
        if ui_assets.REDUCED_MOTION and level != "off":
            text += " В Windows отключена анимация — частицы показаны неподвижно."
        self.canvas.itemconfigure(self.hint, text=text)

    def _set_level(self, level):
        self._update_hint(level)
        self.app.set_atmosphere_level(level)

    def _card_at(self, x, y):
        for i, card in enumerate(self.cards):
            if card["x"] <= x < card["x"] + self.CARD_W and card["y"] <= y < card["y"] + self.CARD_H:
                return i
        return None

    def _set_hover(self, index):
        if index == self.hover:
            return
        for i in (self.hover, index):
            if i is not None:
                card = self.cards[i]
                self.canvas.itemconfigure(card["bg"], image=self._card_image(card["active"], i == index))
        self.hover = index
        self.canvas.configure(cursor="hand2" if index is not None else "")

    def _motion(self, event):
        self._set_hover(self._card_at(event.x, event.y))

    def _click(self, event):
        index = self._card_at(event.x, event.y)
        if index is not None:
            self.index = index
            self._place_ring()
            self._apply(self.order[index])

    def _move(self, delta):
        self.index = max(0, min(len(self.order) - 1, self.index + delta))
        self._place_ring()
        return "break"

    def _place_ring(self, animated=True):
        card = self.cards[self.index]
        x1, y1 = card["x"], card["y"]
        coords = self.canvas.coords(self.ring)
        if not animated or not coords or coords[0] < 0:
            self.canvas.coords(self.ring, x1, y1)
            return
        x0, y0 = coords

        def step(t):
            self.canvas.coords(self.ring, x0 + (x1 - x0) * t, y0 + (y1 - y0) * t)
        animate(self.canvas, "ring", 180, step, ease_out_cubic)

    def _apply(self, theme_id):
        if theme_id == getattr(self.app, "_theme_id", None):
            self.close()
            return "break"
        self.close(immediate=True)
        self.app.root.after(10, lambda: self.app.apply_theme(theme_id, reopen_picker=True))
        return "break"

    def _focus_out(self, event):
        def check():
            if self.closing or not _alive(self.win):
                return
            try:
                focused = self.win.focus_get()
            except (KeyError, tk.TclError):
                focused = None
            if focused is None or not str(focused).startswith(str(self.win)):
                self.close()
        self.app.root.after(80, check)

    def close(self, immediate=False):
        if self.closing:
            return
        self.closing = True
        self.app._schedule_prefetch_release()
        win = self.win
        if getattr(self.app, "_theme_picker_window", None) is win:
            self.app._theme_picker_window = None
        if not _alive(win):
            return
        if immediate:
            win.destroy()
            return

        def fade(t):
            try:
                win.attributes("-alpha", 1 - t)
            except tk.TclError:
                pass
        animate(win, "out", 140, fade, ease_in_cubic, done=win.destroy)
        try:
            self.app.root.focus_set()
        except tk.TclError:
            pass
