"""Темы оформления: семантические токены и настройки атмосферы.

Устройство
----------
Каждая тема задаётся небольшим набором исходных цветов (seed). Из них функции
`palette()` и `asset_tokens()` выводят полный набор семантических токенов:
  * palette()      — цвета, которыми интерфейс красит виджеты во время работы
                     (фоны, поверхности, текст, акцент и текст на акценте, рамки,
                     наведение/выбор/фокус/отключено, статусы, служебные слои);
  * asset_tokens() — цвета для генератора картинок tools/build_theme.py
                     (кнопки, поля, переключатели, вкладки, герой, частицы...).
Код интерфейса берёт цвета только из палитры (P[...]), поэтому смена темы —
это смена палитры + подмена картинок, без правок в разметке.

Как добавить тему
-----------------
1. Добавьте запись в THEMES: id, название, описание, dark, seed-цвета, мотив
   героя (motif) и атмосферу (atmosphere).
2. При необходимости уточните отдельные токены в "palette"/"assets" (overrides).
3. Запустите `py -3 tools\\build_theme.py` — картинки появятся в
   app/assets/themes/<id>/ (тема aurora — базовая, её файлы лежат в app/assets).
4. Запустите tests/test_themes.py: он проверит токены и контраст текста.

Атмосфера
---------
atmosphere = {"kind": вид частиц, "colors": [...], "speed": множитель скорости,
"delicate"/"expressive": число частиц на 100 000 px² области}. Частицы — это
готовые спрайты particle_<n>.png из каталога темы; их рисует app/atmosphere.py.
"""
from __future__ import annotations

from typing import Any

DEFAULT_THEME = "aurora"
ATMOSPHERE_LEVELS = ("off", "delicate", "expressive")
DEFAULT_ATMOSPHERE = "delicate"
ATMOSPHERE_LABELS = {"off": "Выключена", "delicate": "Деликатная", "expressive": "Выразительная"}


# ----------------------------------------------------------------- цвета
def _rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def mix(a: str, b: str, t: float) -> str:
    """Смешивание цветов: t=0 → a, t=1 → b."""
    ra, rb = _rgb(a), _rgb(b)
    return "#%02X%02X%02X" % tuple(round(x + (y - x) * t) for x, y in zip(ra, rb))


def luminance(color: str) -> float:
    def channel(v: int) -> float:
        c = v / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = _rgb(color)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    """Контраст по WCAG 2.x (1…21). Обычный текст — от 4.5, крупный и иконки — от 3."""
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# ----------------------------------------------------------------- темы
THEMES: dict[str, dict[str, Any]] = {
    "aurora": {
        "name": "Аврора",
        "description": "Фирменная тёмная тема: фиолетовое сияние и спокойный графит.",
        "dark": True,
        "seed": {
            "background": "#0B0B12", "sidebar": "#08080D", "surface": "#13131C", "surface_high": "#191925",
            "field": "#0E0E16", "text": "#F3F2FA", "text_soft": "#C9C7D9", "muted": "#8C8AA3",
            "accent": "#A898FF", "accent_deep": "#7B5CFF", "accent2": "#6FD6FF",
            "green": "#74E0B5", "amber": "#F2C886", "red": "#F59AAE", "blue": "#9CC0FF", "cyan": "#6FD6FF",
        },
        "motif": "aurora",
        "atmosphere": {"kind": "sparks", "colors": ["#A898FF", "#6FD6FF", "#FFFFFF"], "speed": 1.0,
                       "delicate": 7, "expressive": 18},
    },
    "anime": {
        "name": "Небесный экспресс",
        "description": "Аниме-ночь: звёздное небо, лиловые огни и поезд над облаками.",
        "dark": True,
        "seed": {
            "background": "#0A0D22", "sidebar": "#070919", "surface": "#121531", "surface_high": "#191D3E",
            "field": "#0D1029", "text": "#F4F0FB", "text_soft": "#CBC8E6", "muted": "#9290B8",
            "accent": "#B9A4FF", "accent_deep": "#6A4DDD", "accent2": "#F5A9D0",
            "green": "#7FE3C0", "amber": "#F7CF8A", "red": "#FF9DB8", "blue": "#8CCBFF", "cyan": "#86D9FF",
        },
        "motif": "anime",
        "atmosphere": {"kind": "stars", "colors": ["#FFFFFF", "#8CCBFF", "#F5A9D0"], "speed": 1.0,
                       "delicate": 10, "expressive": 24},
    },
    "winter": {
        "name": "Уютный вечер",
        "description": "Зима: морозный голубой, снег за окном и тёплый свет лампы.",
        "dark": False,
        "seed": {
            "background": "#E8EEF5", "sidebar": "#DDE6F0", "surface": "#F8FAFD", "surface_high": "#EFF4FA",
            "field": "#FFFFFF", "text": "#1C2938", "text_soft": "#33445A", "muted": "#536376",
            "accent": "#2D6194", "accent_deep": "#22507D", "accent2": "#E39A4E",
            "green": "#25805A", "amber": "#9A5A0C", "red": "#BF3A56", "blue": "#2C6AAB", "cyan": "#18809C",
        },
        "motif": "winter",
        "atmosphere": {"kind": "snow", "colors": ["#FFFFFF", "#DCE8F6"], "speed": 0.8,
                       "delicate": 12, "expressive": 30},
    },
    "spring": {
        "name": "Цветение",
        "description": "Весна: нежная сакура, свежая зелень и тёплая бумага.",
        "dark": False,
        "seed": {
            "background": "#F5F1E8", "sidebar": "#EDE7DA", "surface": "#FFFDF8", "surface_high": "#FAF5EB",
            "field": "#FFFFFF", "text": "#1F382B", "text_soft": "#334D3F", "muted": "#53665A",
            "accent": "#2C7651", "accent_deep": "#225F41", "accent2": "#E39AB0",
            "green": "#2C8455", "amber": "#946210", "red": "#B83F5F", "blue": "#356A9F", "cyan": "#1B7D8C",
        },
        "motif": "spring",
        "atmosphere": {"kind": "petals", "colors": ["#F4B6C8", "#F8CCD9", "#EFA2BA"], "speed": 0.9,
                       "delicate": 9, "expressive": 22},
    },
    "summer": {
        "name": "Золотой час",
        "description": "Лето: тёплый песок, бирюзовое море и медовый закат.",
        "dark": False,
        "seed": {
            "background": "#F5ECDA", "sidebar": "#EEE2CA", "surface": "#FFF9EE", "surface_high": "#FBF1DF",
            "field": "#FFFFFF", "text": "#36271A", "text_soft": "#4F3B29", "muted": "#6F5944",
            "accent": "#17797A", "accent_deep": "#11615F", "accent2": "#E3A93B",
            "green": "#357A45", "amber": "#94600F", "red": "#B04434", "blue": "#2B6895", "cyan": "#137A85",
        },
        "motif": "summer",
        "atmosphere": {"kind": "dust", "colors": ["#F6C76A", "#FFE3A6", "#FFFFFF"], "speed": 0.6,
                       "delicate": 9, "expressive": 22},
    },
    "autumn": {
        "name": "Плед и какао",
        "description": "Осень: шоколадные тона, тыквенный акцент и падающие листья.",
        "dark": True,
        "seed": {
            "background": "#1B1310", "sidebar": "#150E0B", "surface": "#251B16", "surface_high": "#2D211B",
            "field": "#1E1511", "text": "#F7EBDD", "text_soft": "#DDCBB7", "muted": "#AC937D",
            "accent": "#E79B55", "accent_deep": "#C8742C", "accent2": "#D9573A",
            "green": "#A3CF8C", "amber": "#F2C66E", "red": "#F29684", "blue": "#ADC7E2", "cyan": "#92D5CB",
        },
        "motif": "autumn",
        "atmosphere": {"kind": "leaves", "colors": ["#E58A3A", "#D9573A", "#E9B44C", "#B5651D"], "speed": 0.9,
                       "delicate": 7, "expressive": 18},
    },
}
THEME_ORDER = ("aurora", "anime", "winter", "spring", "summer", "autumn")

# Точные значения «Авроры» — фирменная тема не должна меняться от перехода на токены.
_AURORA_PALETTE = {
    "surface_3": "#20202E", "border": "#2C2C3F", "border_soft": "#20202F", "muted_dim": "#5E5C75",
    "accent_hover": "#C0B4FF", "selection": "#2A2448", "neutral": "#20202E", "hover_row": "#1A1A26",
    "stripe": "#16161F", "on_accent": "#FFFFFF", "danger_hover": "#FFC2CF", "danger_text": "#F7A8B8",
    "danger_active": "#FFC9D3", "menu_bg": "#171723", "dialog_bg": "#15151F", "separator": "#2A2A40",
    "dialog_text_bg": "#101019", "dialog_text_border": "#26263A", "palette_surface": "#14141D",
    "hero_backdrop": "#191825", "list_select": "#2E2852", "field_focus_fill": "#100F1A", "shell_divider": "#15151E",
    "window_border": "#1C1C2A", "text_border": "#22223A", "text_focus": "#7E6BFF", "manual_tag": "#C6B9FF",
    "palette_line": "#24243A", "palette_line_soft": "#1F1F31", "pill_dim": "#3A3363", "track": "#232335",
    "toast_track": "#1E1D2E", "hero_text_base": "#1A1928", "shade": "#000000", "focus": "#A898FF",
}
_AURORA_ASSETS = {
    "accent_glow": "#8466FF",
    "btn_disabled_top": "#1B1B27", "btn_disabled_bottom": "#171722", "btn_disabled_border": "#262636",
    "acc_top": "#8C72FF", "acc_top_hover": "#A28DFF", "acc_bottom": "#6B4CF2", "acc_bottom_hover": "#7E62FF",
    "acc_pressed_top": "#6F55EC", "acc_pressed_bottom": "#5B40D8",
    "btn_top": "#20202F", "btn_top_hover": "#29293B", "btn_bottom": "#1A1A27", "btn_bottom_hover": "#222233",
    "btn_pressed_top": "#181823", "btn_pressed_bottom": "#15151F", "btn_border": "#2D2D42", "btn_border_hover": "#40405C",
    "danger_top_hover": "#3A1D2C", "danger_bottom_hover": "#2A1824", "danger_border_hover": "#6A3448",
    "ghost_top": "#232336", "ghost_bottom": "#1C1C2B", "ghost_border": "#2A2A40", "ghost_border_hover": "#4A4670",
    "highlight": "#FFFFFF", "hl": 1.0, "shade": "#000000", "shade_k": 1.0,
    "panel_hover_border": "#3B3566", "hero_border": "#2A2842", "tile": "#171722", "tile_border": "#24243A",
    "tile_hover": "#1D1B2E", "tile_hover_border": "#3D3768", "palette_bg": "#14141D", "palette_border": "#34344C",
    "menu_bg": "#171723", "menu_border": "#34344E", "dialog_bg": "#15151F", "dialog_border": "#363456",
    "menu_hover": "#8C7BFF", "menu_hover_a": .17,
    "field_border": "#272739", "field_border_hover": "#37374F", "field_anim_hover": "#3A3A55",
    "field_focus_fill": "#100F1A", "field_focus_border": "#8C7BFF", "field_disabled": "#111119",
    "field_disabled_border": "#1D1D2A", "hero_backdrop": "#191825", "chevron_disabled": "#4A4960",
    "sw_disabled": "#1A1A25", "sw_disabled_border": "#262636", "sw_disabled_knob": "#3F3E52",
    "sw_off_top": "#1E1E2C", "sw_off_bottom": "#242434", "sw_on_top": "#9580FF", "sw_on_bottom": "#6E50F4",
    "sw_border_off": "#34344A", "sw_border_on": "#A592FF", "sw_knob_off": "#8C8AA3", "sw_knob_on": "#FFFFFF",
    "tab_selected": "#25223A", "tab_selected_border": "#3A3560", "tab_hover": "#1D1D2B", "tab_hover_border": "#26263A",
    "thumb": "#33334A", "thumb_hover": "#54527A", "thumb_pressed": "#8C7BFF",
    "heading_line": "#2A2A3E", "heading_hover": "#20202F", "heading_hover_line": "#3A3560",
    "nav_outline": "#B9AAFF", "nav_bar": "#C4B8FF", "nav_overlay": "#FFFFFF", "nav_fill_a": .30,
    "nav_focus": "#A898FF", "badge": "#2C2452", "badge_border": "#4B3F86",
    "search": "#121219", "search_border": "#23233A", "search_hover": "#17171F", "search_hover_border": "#3A3560",
    "kbd": "#1C1C29", "kbd_border": "#2E2E44",
    "hero_top": "#1B1A29", "hero_bottom": "#171622", "hero_outline": "#2D2A47",
    "core_top": "#3D3270", "core_bottom": "#251F48", "core_border": "#8C7BFF", "spark": "#D9D1FF",
    "progress_head": "#C9BEFF", "toast_top": "#1F1E30", "toast_bottom": "#191826", "toast_border": "#3A3760",
    "row_active_border": "#B9AAFF",
}


def get_theme_id(value: Any) -> str:
    """Безопасный выбор темы: неизвестное или повреждённое значение → тема по умолчанию."""
    return value if isinstance(value, str) and value in THEMES else DEFAULT_THEME


def get_atmosphere(value: Any) -> str:
    return value if isinstance(value, str) and value in ATMOSPHERE_LEVELS else DEFAULT_ATMOSPHERE


def _on_accent(seed: dict[str, str], dark: bool) -> str:
    """Текст на акцентной кнопке: белый или тёмный — что контрастнее на средней заливке."""
    fill = mix(seed["accent_deep"], seed["accent"], .2)
    ink = mix(seed["background"] if dark else seed["text"], "#000000", .35)
    return "#FFFFFF" if contrast("#FFFFFF", fill) >= contrast(ink, fill) else ink


def palette(theme_id: str) -> dict[str, str]:
    """Полная палитра времени выполнения для темы (семантические токены)."""
    theme = THEMES[get_theme_id(theme_id)]
    s, dark = theme["seed"], theme["dark"]
    text, surface, bg = s["text"], s["surface"], s["background"]
    p = dict(s)
    if dark:
        p.update({
            "surface_3": mix(surface, text, .06), "border": mix(surface, text, .13), "border_soft": mix(surface, text, .065),
            "muted_dim": mix(s["muted"], surface, .42), "accent_hover": mix(s["accent"], "#FFFFFF", .3),
            "selection": mix(surface, s["accent"], .2), "hover_row": mix(surface, text, .035),
            "stripe": mix(surface, text, .014), "danger_hover": mix(s["red"], "#FFFFFF", .45),
            "danger_text": mix(s["red"], "#FFFFFF", .15), "danger_active": mix(s["red"], "#FFFFFF", .5),
            "menu_bg": mix(surface, s["surface_high"], .45), "dialog_bg": mix(bg, surface, .75),
            "dialog_text_bg": mix(bg, surface, .3), "palette_surface": mix(surface, bg, .2),
            "list_select": mix(s["field"], s["accent"], .24), "field_focus_fill": mix(s["field"], s["accent"], .03),
            "shell_divider": mix(bg, text, .035), "window_border": mix(s["sidebar"], text, .09),
            "manual_tag": mix(s["text_soft"], s["accent"], .5), "pill_dim": mix(surface, s["accent"], .3),
            "track": mix(surface, text, .08), "toast_track": mix(surface, text, .07), "shade": "#000000",
        })
    else:
        p.update({
            "surface_3": mix(surface, text, .055), "border": mix(surface, text, .2), "border_soft": mix(surface, text, .1),
            "muted_dim": mix(s["muted"], surface, .24), "accent_hover": mix(s["accent"], "#000000", .14),
            "selection": mix(surface, s["accent"], .16), "hover_row": mix(surface, text, .04),
            "stripe": mix(surface, text, .018), "danger_hover": mix(s["red"], "#000000", .2),
            "danger_text": s["red"], "danger_active": mix(s["red"], "#000000", .25),
            "menu_bg": mix(surface, "#FFFFFF", .5), "dialog_bg": mix(surface, "#FFFFFF", .4),
            "dialog_text_bg": mix(s["surface_high"], bg, .3), "palette_surface": mix(surface, "#FFFFFF", .3),
            "list_select": mix(s["field"], s["accent"], .16), "field_focus_fill": s["field"],
            "shell_divider": mix(bg, text, .08), "window_border": mix(s["sidebar"], text, .14),
            "manual_tag": mix(s["accent"], text, .25), "pill_dim": mix(surface, s["accent"], .32),
            "track": mix(surface, text, .1), "toast_track": mix(surface, text, .1), "shade": "#1A2230",
        })
    p.update({
        "neutral": p["surface_3"], "on_accent": _on_accent(s, dark), "separator": mix(p["menu_bg"], text, .12),
        "dialog_text_border": mix(p["dialog_bg"], text, .1), "hero_backdrop": mix(s["surface_high"], bg, .1),
        "text_border": p["border_soft"], "text_focus": s["accent_deep"],
        "palette_line": p["border_soft"], "palette_line_soft": mix(surface, text, .05 if dark else .08),
        "hero_text_base": mix(s["surface_high"], bg, .2), "focus": s["accent"],
    })
    if theme_id == "aurora":
        p.update(_AURORA_PALETTE)
    p.update(theme.get("palette", {}))
    return p


def asset_tokens(theme_id: str) -> dict[str, Any]:
    """Цвета для генератора картинок. Для «Авроры» — прежние значения один в один."""
    theme = THEMES[get_theme_id(theme_id)]
    s, dark, p = theme["seed"], theme["dark"], palette(theme_id)
    surface, text, accent, deep = s["surface"], s["text"], s["accent"], s["accent_deep"]
    t = dict(p)
    t["dark"] = dark
    if dark:
        t.update({
            "accent_glow": mix(deep, accent, .2),
            "btn_disabled_top": mix(surface, text, .04), "btn_disabled_bottom": mix(surface, text, .02),
            "btn_disabled_border": mix(surface, text, .09),
            "acc_top": mix(deep, accent, .14), "acc_top_hover": mix(deep, accent, .22),
            "acc_bottom": mix(deep, "#000000", .06), "acc_bottom_hover": mix(deep, accent, .08),
            "acc_pressed_top": mix(deep, "#000000", .08), "acc_pressed_bottom": mix(deep, "#000000", .2),
            "btn_top": mix(surface, text, .055), "btn_top_hover": mix(surface, text, .1),
            "btn_bottom": mix(surface, text, .03), "btn_bottom_hover": mix(surface, text, .07),
            "btn_pressed_top": mix(surface, s["background"], .3), "btn_pressed_bottom": mix(surface, s["background"], .6),
            "btn_border": mix(surface, text, .12), "btn_border_hover": mix(mix(surface, text, .16), accent, .25),
            "danger_top_hover": mix(surface, s["red"], .16), "danger_bottom_hover": mix(surface, s["red"], .09),
            "danger_border_hover": mix(surface, s["red"], .4),
            "ghost_top": mix(surface, text, .07), "ghost_bottom": mix(surface, text, .04),
            "ghost_border": mix(surface, text, .1), "ghost_border_hover": mix(mix(surface, text, .2), accent, .3),
            "highlight": "#FFFFFF", "hl": 1.0, "shade": "#000000", "shade_k": 1.0,
            "panel_hover_border": mix(surface, accent, .3), "hero_border": mix(s["surface_high"], accent, .14),
            "tile": mix(surface, text, .02), "tile_border": mix(surface, text, .08),
            "tile_hover": mix(surface, accent, .07), "tile_hover_border": mix(surface, accent, .3),
            "palette_bg": p["palette_surface"], "palette_border": mix(surface, text, .15),
            "menu_border": mix(p["menu_bg"], text, .15), "dialog_border": mix(p["dialog_bg"], accent, .22),
            "menu_hover": mix(deep, accent, .4), "menu_hover_a": .17,
            "field_border": mix(s["field"], text, .11), "field_border_hover": mix(s["field"], text, .18),
            "field_anim_hover": mix(s["field"], text, .2), "field_focus_border": mix(deep, accent, .5),
            "field_disabled": mix(s["field"], surface, .5), "field_disabled_border": mix(surface, text, .05),
            "chevron_disabled": mix(s["muted"], surface, .55),
            "sw_disabled": mix(surface, text, .03), "sw_disabled_border": mix(surface, text, .08),
            "sw_disabled_knob": mix(surface, text, .2), "sw_off_top": mix(surface, text, .05),
            "sw_off_bottom": mix(surface, text, .08), "sw_on_top": mix(deep, accent, .55), "sw_on_bottom": deep,
            "sw_border_off": mix(surface, text, .15), "sw_border_on": mix(accent, "#FFFFFF", .1),
            "sw_knob_off": s["muted"], "sw_knob_on": "#FFFFFF",
            "tab_selected": mix(surface, accent, .13), "tab_selected_border": mix(surface, accent, .3),
            "tab_hover": mix(surface, text, .05), "tab_hover_border": mix(surface, text, .08),
            "thumb": mix(surface, text, .15), "thumb_hover": mix(mix(surface, text, .25), accent, .25),
            "thumb_pressed": mix(deep, accent, .5), "heading_line": mix(s["surface_high"], text, .08),
            "heading_hover": mix(s["surface_high"], text, .04), "heading_hover_line": mix(surface, accent, .3),
            "nav_outline": mix(accent, "#FFFFFF", .15), "nav_bar": mix(accent, "#FFFFFF", .25),
            "nav_overlay": "#FFFFFF", "nav_fill_a": .30, "nav_focus": accent,
            "badge": mix(surface, accent, .25), "badge_border": mix(surface, accent, .45),
            "search": mix(s["sidebar"], text, .035), "search_border": mix(s["sidebar"], text, .1),
            "search_hover": mix(s["sidebar"], text, .06), "search_hover_border": mix(s["sidebar"], accent, .3),
            "kbd": mix(s["sidebar"], text, .08), "kbd_border": mix(s["sidebar"], text, .15),
            "hero_top": mix(s["surface_high"], text, .015), "hero_bottom": mix(s["surface_high"], s["background"], .25),
            "hero_outline": mix(s["surface_high"], accent, .18),
            "core_top": mix(deep, s["background"], .45), "core_bottom": mix(deep, s["background"], .7),
            "core_border": mix(deep, accent, .5), "spark": mix(accent, "#FFFFFF", .55),
            "progress_head": mix(accent, "#FFFFFF", .4), "toast_top": mix(surface, text, .06),
            "toast_bottom": mix(surface, text, .03), "toast_border": mix(surface, accent, .25),
            "row_active_border": mix(accent, "#FFFFFF", .15),
        })
    else:
        t.update({
            "accent_glow": accent,
            "btn_disabled_top": mix(surface, text, .04), "btn_disabled_bottom": mix(surface, text, .06),
            "btn_disabled_border": mix(surface, text, .1),
            "acc_top": accent, "acc_top_hover": mix(accent, "#FFFFFF", .05),
            "acc_bottom": deep, "acc_bottom_hover": mix(deep, accent, .5),
            "acc_pressed_top": mix(deep, "#000000", .05), "acc_pressed_bottom": mix(deep, "#000000", .15),
            "btn_top": mix(surface, "#FFFFFF", .7), "btn_top_hover": "#FFFFFF",
            "btn_bottom": mix(surface, text, .03), "btn_bottom_hover": mix(surface, accent, .04),
            "btn_pressed_top": mix(surface, text, .07), "btn_pressed_bottom": mix(surface, text, .05),
            "btn_border": mix(surface, text, .17), "btn_border_hover": mix(mix(surface, text, .2), accent, .4),
            "danger_top_hover": mix(surface, s["red"], .07), "danger_bottom_hover": mix(surface, s["red"], .11),
            "danger_border_hover": mix(surface, s["red"], .5),
            "ghost_top": mix(surface, accent, .05), "ghost_bottom": mix(surface, accent, .08),
            "ghost_border": mix(surface, text, .16), "ghost_border_hover": mix(surface, accent, .45),
            "highlight": "#FFFFFF", "hl": 1.6, "shade": "#1A2230", "shade_k": .35,
            "panel_hover_border": mix(surface, accent, .35), "hero_border": mix(s["surface_high"], text, .1),
            "tile": mix(surface, s["background"], .35), "tile_border": mix(surface, text, .1),
            "tile_hover": mix(surface, accent, .05), "tile_hover_border": mix(surface, accent, .35),
            "palette_bg": p["palette_surface"], "palette_border": mix(surface, text, .2),
            "menu_border": mix(p["menu_bg"], text, .18), "dialog_border": mix(p["dialog_bg"], text, .18),
            "menu_hover": accent, "menu_hover_a": .12,
            "field_border": mix(s["field"], text, .2), "field_border_hover": mix(s["field"], text, .32),
            "field_anim_hover": mix(s["field"], text, .34), "field_focus_border": accent,
            "field_disabled": mix(surface, text, .03), "field_disabled_border": mix(surface, text, .1),
            "chevron_disabled": mix(s["muted"], surface, .5),
            "sw_disabled": mix(surface, text, .05), "sw_disabled_border": mix(surface, text, .12),
            "sw_disabled_knob": mix(surface, text, .25), "sw_off_top": mix(surface, text, .1),
            "sw_off_bottom": mix(surface, text, .07), "sw_on_top": mix(accent, "#FFFFFF", .1), "sw_on_bottom": deep,
            "sw_border_off": mix(surface, text, .25), "sw_border_on": deep,
            "sw_knob_off": "#FFFFFF", "sw_knob_on": "#FFFFFF",
            "tab_selected": "#FFFFFF", "tab_selected_border": mix(surface, accent, .4),
            "tab_hover": mix(surface, text, .05), "tab_hover_border": mix(surface, text, .12),
            "thumb": mix(surface, text, .2), "thumb_hover": mix(surface, text, .35), "thumb_pressed": accent,
            "heading_line": mix(s["surface_high"], text, .14), "heading_hover": mix(s["surface_high"], text, .05),
            "heading_hover_line": mix(surface, accent, .45),
            "nav_outline": mix(accent, s["sidebar"], .45), "nav_bar": accent,
            "nav_overlay": text, "nav_fill_a": .16, "nav_focus": accent,
            "badge": mix(surface, accent, .18), "badge_border": mix(surface, accent, .45),
            "search": mix(s["sidebar"], "#FFFFFF", .55), "search_border": mix(s["sidebar"], text, .14),
            "search_hover": "#FFFFFF", "search_hover_border": mix(s["sidebar"], accent, .45),
            "kbd": mix(s["sidebar"], text, .05), "kbd_border": mix(s["sidebar"], text, .18),
            "hero_top": mix(s["surface_high"], "#FFFFFF", .35), "hero_bottom": s["surface_high"],
            "hero_outline": mix(s["surface_high"], text, .12),
            "core_top": mix(accent, "#FFFFFF", .55), "core_bottom": mix(accent, "#FFFFFF", .75),
            "core_border": accent, "spark": deep,
            "progress_head": mix(accent, "#FFFFFF", .2), "toast_top": "#FFFFFF",
            "toast_bottom": mix(surface, text, .02), "toast_border": mix(surface, text, .18),
            "row_active_border": accent,
        })
    t["hero_backdrop"] = p["hero_backdrop"] = mix(t["hero_top"], t["hero_bottom"], .73)
    t["menu_bg"], t["dialog_bg"], t["field_focus_fill"] = p["menu_bg"], p["dialog_bg"], p["field_focus_fill"]
    if theme_id == "aurora":
        t.update(_AURORA_ASSETS)
    t.update(theme.get("assets", {}))
    # Цвета иконок: все токены, которыми интерфейс рисует значки.
    t["icon_colors"] = sorted({p[k] for k in ("accent", "text", "muted", "green", "amber", "text_soft", "blue", "red",
                                              "cyan", "on_accent", "muted_dim", "danger_hover", "background")}
                              | {"#FFFFFF"})
    return t


def full_palette(theme_id: str) -> dict[str, str]:
    """Палитра, согласованная с картинками (hero_backdrop берётся из генератора)."""
    p = palette(theme_id)
    p["hero_backdrop"] = asset_tokens(theme_id)["hero_backdrop"]
    return p


def theme_info(theme_id: str) -> dict[str, Any]:
    theme = THEMES[get_theme_id(theme_id)]
    return {"id": get_theme_id(theme_id), "name": theme["name"], "description": theme["description"],
            "dark": theme["dark"], "motif": theme["motif"], "atmosphere": dict(theme["atmosphere"])}
