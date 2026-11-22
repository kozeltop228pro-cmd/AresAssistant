"""Reproducible generator for theme assets (needs Pillow).

Every surface is drawn at 4x and downsampled, so the shipped PNGs are
anti-aliased while the installed application still needs no Pillow.
Colours come from app/themes.py (semantic tokens): «Аврора» goes to
app/assets, every other theme to app/assets/themes/<id>/.

Usage:
    py -3 tools\\build_theme.py            — all themes
    py -3 tools\\build_theme.py winter     — one theme (plus shared files)
"""
from pathlib import Path
import json
import math
import random
import shutil
import sys
from PIL import Image, ImageDraw, ImageFilter, ImageColor, ImageChops
from icon_vectors import draw_vector_icon

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from themes import THEMES, THEME_ORDER, asset_tokens, full_palette  # noqa: E402

ASSETS = ROOT / "app" / "assets"
S = 4  # supersampling factor
target = ASSETS
T: dict = {}

GLYPHS = ('home', 'check', 'learn', 'templates', 'context', 'history', 'settings', 'diagnostics', 'search', 'arrow',
          'spark', 'changes', 'removals', 'repunish', 'send', 'pause', 'play', 'power', 'trash', 'broom',
          'check_all', 'uncheck', 'invert', 'toggle_on', 'toggle_off', 'plus', 'chevdown', 'chevup', 'chevleft',
          'chevright', 'info', 'warning', 'error', 'question', 'key', 'external', 'copy', 'download', 'upload',
          'refresh', 'save', 'stop', 'folder', 'file', 'lock', 'unlock', 'palette', 'flag', 'prio_low')
ICON_SIZES = (17, 20, 24)
# Colours the Aurora build always had (some are used directly by name in older code).
AURORA_ICON_COLORS = ("#A898FF", "#F3F2FA", "#8C8AA3", "#74E0B5", "#F2C886", "#C9C7D9", "#9CC0FF", "#F59AAE",
                      "#6FD6FF", "#FFFFFF", "#15121F", "#5E5C75", "#FFC2CF")


def rgb(color):
    return ImageColor.getrgb(color)[:3]


def rgba(color, alpha=1.0):
    return rgb(color) + (max(0, min(255, round(255 * alpha))),)


def mix(a, b, t):
    a, b = rgb(a), rgb(b)
    return "#%02X%02X%02X" % tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def ease(t):
    return (1 - math.cos(math.pi * t)) / 2


def layer(w, h):
    return Image.new("RGBA", (w * S, h * S), (0, 0, 0, 0))


def save(img, name, w, h):
    img.resize((w, h), Image.Resampling.LANCZOS).save(target / name)


def box(x1, y1, x2, y2):
    return [round(v * S) for v in (x1, y1, x2, y2)]


def mask_rr(w, h, rect, radius):
    m = Image.new("L", (w * S, h * S), 0)
    ImageDraw.Draw(m).rounded_rectangle(box(*rect), radius=radius * S, fill=255)
    return m


def vgradient(w, h, stops):
    """Vertical gradient (list of (pos, color, alpha)) at supersampled size."""
    H = h * S
    col = Image.new("RGBA", (1, H))
    for y in range(H):
        t = y / max(1, H - 1)
        for (p1, c1, a1), (p2, c2, a2) in zip(stops, stops[1:]):
            if p1 <= t <= p2:
                k = (t - p1) / max(1e-6, p2 - p1)
                c = rgb(mix(c1, c2, k))
                col.putpixel((0, y), c + (round(255 * (a1 + (a2 - a1) * k)),))
                break
    return col.resize((w * S, H), Image.Resampling.BILINEAR)


def hgradient(w, h, stops):
    W = w * S
    row = Image.new("RGBA", (W, 1))
    for x in range(W):
        t = x / max(1, W - 1)
        for (p1, c1, a1), (p2, c2, a2) in zip(stops, stops[1:]):
            if p1 <= t <= p2:
                k = (t - p1) / max(1e-6, p2 - p1)
                row.putpixel((x, 0), rgb(mix(c1, c2, k)) + (round(255 * (a1 + (a2 - a1) * k)),))
                break
    return row.resize((W, h * S), Image.Resampling.BILINEAR)


def fill_shape(img, shape_mask, paint):
    """Composite `paint` (RGBA, same size) through a mask onto img."""
    p = paint.copy()
    alpha = ImageChops.multiply(p.getchannel("A"), shape_mask)
    p.putalpha(alpha)
    img.alpha_composite(p)


def solid(w, h, color, alpha=1.0):
    return Image.new("RGBA", (w * S, h * S), rgba(color, alpha))


def outline(img, w, h, rect, radius, color, alpha, width=1.0, fade=None):
    """1px rounded border; `fade` = (top_alpha, bottom_alpha) multiplier."""
    ring = Image.new("L", (w * S, h * S), 0)
    d = ImageDraw.Draw(ring)
    d.rounded_rectangle(box(*rect), radius=radius * S, fill=255)
    x1, y1, x2, y2 = rect
    inner = (x1 + width, y1 + width, x2 - width, y2 - width)
    d.rounded_rectangle(box(*inner), radius=max(0, radius - width) * S, fill=0)
    paint = solid(w, h, color, min(1.0, alpha))
    if fade:
        g = vgradient(w, h, [(0, "#FFFFFF", min(1, fade[0])), (1, "#FFFFFF", min(1, fade[1]))]).getchannel("A")
        ring = ImageChops.multiply(ring, g)
    fill_shape(img, ring, paint)


def sheen(img, w, h, rect, radius, top):
    """Top highlight of a raised surface; light themes use a stronger white edge."""
    outline(img, w, h, rect, radius, T["highlight"], 1, fade=(min(1, top * T["hl"]), 0))


def inner_shade(img, w, h, rect, radius, top):
    outline(img, w, h, rect, radius, T["shade"], 1, fade=(top * T["shade_k"], 0))


def glow(img, w, h, rect, radius, color, alpha, blur):
    g = layer(w, h)
    ImageDraw.Draw(g).rounded_rectangle(box(*rect), radius=radius * S, fill=rgba(color, alpha))
    g = g.filter(ImageFilter.GaussianBlur(blur * S))
    img.alpha_composite(g)


def save_on(img, name, w, h, backdrop):
    """Flatten onto the colour the element always sits on: opaque photos take Tk's fast path."""
    flat = Image.new("RGBA", img.size, rgba(backdrop))
    flat.alpha_composite(img)
    save(flat, name, w, h)


# ---------------------------------------------------------------- buttons
BW, BH = 160, 40   # 9-slice source, ttk border 14; AnimatedButton flattens it onto its parent
M, R = 3, 10
BRECT = (M, M, BW - M, BH - M)


def button_image(kind, t, pressed=False, disabled=False):
    img = layer(BW, BH)
    shape = mask_rr(BW, BH, BRECT, R)
    if disabled:
        fill_shape(img, shape, vgradient(BW, BH, [(0, T["btn_disabled_top"], 1), (1, T["btn_disabled_bottom"], 1)]))
        outline(img, BW, BH, BRECT, R, T["btn_disabled_border"], 1)
        return img
    if not T["dark"]:
        # Light themes: a soft drop shadow makes raised controls readable on pale cards.
        glow(img, BW, BH, (M, M + 1.5, BW - M, BH - M + 1.5), R, T["shade"], .10 if kind != "ghost" else .0, 1.6)
    if kind == "accent":
        top = mix(T["acc_top"], T["acc_top_hover"], t)
        bottom = mix(T["acc_bottom"], T["acc_bottom_hover"], t)
        if pressed:
            top, bottom = T["acc_pressed_top"], T["acc_pressed_bottom"]
        glow(img, BW, BH, (M, M + 1, BW - M, BH - M + 1), R, T["accent_glow"],
             (0.30 + 0.35 * t if not pressed else 0.18) * (1 if T["dark"] else .55), 2.2)
        fill_shape(img, shape, vgradient(BW, BH, [(0, top, 1), (1, bottom, 1)]))
        outline(img, BW, BH, BRECT, R, "#FFFFFF", 1, fade=(0.34 + 0.12 * t, 0.04))
    elif kind in ("button", "danger"):
        danger = kind == "danger"
        top = mix(T["btn_top"], T["danger_top_hover"] if danger else T["btn_top_hover"], t)
        bottom = mix(T["btn_bottom"], T["danger_bottom_hover"] if danger else T["btn_bottom_hover"], t)
        if pressed:
            top, bottom = T["btn_pressed_top"], T["btn_pressed_bottom"]
        border = mix(T["btn_border"], T["danger_border_hover"] if danger else T["btn_border_hover"], t)
        fill_shape(img, shape, vgradient(BW, BH, [(0, top, 1), (1, bottom, 1)]))
        outline(img, BW, BH, BRECT, R, border, 1)
        sheen(img, BW, BH, (M + 1, M + 1, BW - M - 1, BH - M - 1), R - 1, 0.07 + 0.05 * t)
    elif kind == "ghost":
        # Outline button: a quiet frame at rest that fills in on hover.
        a = 0.85 * t if not pressed else 0.95
        if a > 0:
            fill_shape(img, shape, vgradient(BW, BH, [(0, T["ghost_top"], a), (1, T["ghost_bottom"], a)]))
        outline(img, BW, BH, BRECT, R, mix(T["ghost_border"], T["ghost_border_hover"], t), 1)
        sheen(img, BW, BH, (M + 1, M + 1, BW - M - 1, BH - M - 1), R - 1, 0.04 + 0.05 * t)
    return img


def build_buttons():
    for kind in ("accent", "button", "ghost", "danger"):
        for step in range(12):
            save(button_image(kind, ease(step / 11)), f"{kind}_frame_{step}.png", BW, BH)
        save(button_image(kind, 1, pressed=True), f"{kind}_pressed.png", BW, BH)
    save(button_image("button", 0, disabled=True), "button_disabled.png", BW, BH)


# ------------------------------------------------------------ cards & hero
def card(name, fill, border, highlight, glow_color=None, size=48, radius=14, backdrop=None, shadow=False):
    img = layer(size, size)
    r = (0, 0, size, size)
    if shadow and not T["dark"]:
        # Light cards separate from the page with a faint inner bottom edge.
        pass
    fill_shape(img, mask_rr(size, size, r, radius), solid(size, size, fill))
    if glow_color:
        inner = layer(size, size)
        ImageDraw.Draw(inner).rounded_rectangle(box(0, 0, size, size), radius=radius * S, outline=rgba(glow_color, .35), width=3 * S)
        inner = inner.filter(ImageFilter.GaussianBlur(2 * S))
        inner.putalpha(ImageChops.multiply(inner.getchannel("A"), mask_rr(size, size, r, radius)))
        img.alpha_composite(inner)
    outline(img, size, size, r, radius, border, 1)
    sheen(img, size, size, (1, 1, size - 1, size - 1), radius - 1, highlight)
    if backdrop:
        save_on(img, name, size, size, backdrop)
    else:
        save(img, name, size, size)


def soft_fill(name, color, alpha, size=32, radius=9):
    img = layer(size, size)
    fill_shape(img, mask_rr(size, size, (0, 0, size, size), radius), solid(size, size, color, alpha))
    save(img, name, size, size)


def build_cards():
    bg = T["background"]
    # Cards and heroes always sit on the page background.
    card("panel.png", T["surface"], T["border_soft"], .055, size=256, backdrop=bg)
    card("panel_hover.png", T["surface"], T["panel_hover_border"], .08, glow_color=T["accent_deep"], size=256, backdrop=bg)
    card("hero.png", T["surface_high"], T["hero_border"], .08, size=256, backdrop=bg)
    card("tile.png", T["tile"], T["tile_border"], .05, size=40, radius=12)
    card("tile_hover.png", T["tile_hover"], T["tile_hover_border"], .10, size=40, radius=12)
    card("palette.png", T["palette_bg"], T["palette_border"], .07, size=56, radius=18)
    card("menu.png", T["menu_bg"], T["menu_border"], .09, size=48, radius=13)
    card("dialog.png", T["dialog_bg"], T["dialog_border"], .09, size=64, radius=20)
    # Menu item highlight (flat, so 9-slicing never stripes) and its danger twin.
    soft_fill("menu_hover.png", T["menu_hover"], T["menu_hover_a"])
    soft_fill("menu_hover_danger.png", T["red"], .15 if T["dark"] else .11)


# ----------------------------------------------------------------- fields
FW, FH = 320, 36
FRECT = (2, 2, FW - 2, FH - 2)


def field(name, fill, border, focus=False):
    img = layer(FW, FH)
    if focus:
        glow(img, FW, FH, (1, 1, FW - 1, FH - 1), 9, T["accent_glow"], .45 if T["dark"] else .3, 1.2)
    fill_shape(img, mask_rr(FW, FH, FRECT, 8), solid(FW, FH, fill))
    outline(img, FW, FH, FRECT, 8, border, 1)
    if not focus:
        inner_shade(img, FW, FH, (3, 3, FW - 3, FH - 3), 7, .22)
    # Inputs live on cards; the hero mode selector gets its own flattened copy.
    save_on(img, name, FW, FH, T["surface"])
    save_on(img, name.replace("field", "field_hero"), FW, FH, T["hero_backdrop"])


def chevron(name, color, w=22, h=16, up=False, small=False):
    img = layer(w, h)
    d = ImageDraw.Draw(img)
    cx, cy = w / 2 - 3, h / 2
    s = 2.6 if small else 3.4
    pts = [(cx - s, cy + s / 2), (cx, cy - s / 2), (cx + s, cy + s / 2)] if up else \
          [(cx - s, cy - s / 2), (cx, cy + s / 2), (cx + s, cy - s / 2)]
    d.line([(round(x * S), round(y * S)) for x, y in pts], fill=rgba(color), width=round(1.6 * S), joint="curve")
    for x, y in (pts[0], pts[-1]):
        r = .8 * S
        d.ellipse((x * S - r, y * S - r, x * S + r, y * S + r), fill=rgba(color))
    save(img, name, w, h)


def field_frame(level, backdrop, name):
    """One step of the input animation: 0 rest, .45 hover, 1 focus with a soft glow."""
    img = layer(FW, FH)
    if level <= .45:
        k = level / .45
        border, fill, glow_a, shade = mix(T["field_border"], T["field_anim_hover"], k), T["field"], 0, .22
    else:
        k = (level - .45) / .55
        border = mix(T["field_anim_hover"], T["field_focus_border"], k)
        fill, glow_a, shade = mix(T["field"], T["field_focus_fill"], k), (.45 if T["dark"] else .3) * k, .22 * (1 - k)
    if glow_a:
        glow(img, FW, FH, (1, 1, FW - 1, FH - 1), 9, T["accent_glow"], glow_a, 1.2)
    fill_shape(img, mask_rr(FW, FH, FRECT, 8), solid(FW, FH, fill))
    outline(img, FW, FH, FRECT, 8, border, 1)
    if shade:
        inner_shade(img, FW, FH, (3, 3, FW - 3, FH - 3), 7, shade)
    save_on(img, name, FW, FH, backdrop)


def build_fields():
    field("field.png", T["field"], T["field_border"])
    field("field_hover.png", T["field"], T["field_border_hover"])
    field("field_focus.png", T["field_focus_fill"], T["field_focus_border"], focus=True)
    field("field_disabled.png", T["field_disabled"], T["field_disabled_border"])
    chevron("chevron.png", T["muted"])
    chevron("chevron_hover.png", T["text"])
    chevron("chevron_disabled.png", T["chevron_disabled"])
    chevron("chevron_open.png", T["accent"], up=True)
    # Inputs sit on cards, on the dashboard hero and inside dialogs; each gets flattened frames.
    for step in range(12):
        t = step / 11
        field_frame(t, T["surface"], f"field_anim_{step}.png")
        field_frame(t, T["hero_backdrop"], f"field_hero_anim_{step}.png")
        field_frame(t, T["dialog_bg"], f"field_dialog_anim_{step}.png")
    for direction in ("up", "down"):
        chevron(f"spin_{direction}.png", T["muted"], 20, 12, up=direction == "up", small=True)
        chevron(f"spin_{direction}_hover.png", T["accent"], 20, 12, up=direction == "up", small=True)


# ---------------------------------------------------------------- switches
SW, SH, GAP = 38, 22, 10


def switch(t, disabled=False):
    img = layer(SW + GAP, SH)
    tr = (1, 2, SW - 1, SH - 2)
    m = mask_rr(SW + GAP, SH, tr, 9)
    if disabled:
        fill_shape(img, m, solid(SW + GAP, SH, T["sw_disabled"]))
        outline(img, SW + GAP, SH, tr, 9, T["sw_disabled_border"], 1)
        knob = T["sw_disabled_knob"]
    else:
        off = vgradient(SW + GAP, SH, [(0, T["sw_off_top"], 1), (1, T["sw_off_bottom"], 1)])
        on = vgradient(SW + GAP, SH, [(0, T["sw_on_top"], 1), (1, T["sw_on_bottom"], 1)])
        if t > 0:
            glow(img, SW + GAP, SH, tr, 9, T["accent_glow"], (.45 if T["dark"] else .25) * t, 1.6)
        fill_shape(img, m, Image.blend(off, on, t))
        outline(img, SW + GAP, SH, tr, 9, mix(T["sw_border_off"], T["sw_border_on"], t), 1)
        knob = mix(T["sw_knob_off"], T["sw_knob_on"], t)
    k = 7
    cx = 1 + 2 + k + (SW - 2 - 4 - 2 * k) * t
    cy = SH / 2
    shadow = layer(SW + GAP, SH)
    ImageDraw.Draw(shadow).ellipse(box(cx - k, cy - k + 1, cx + k, cy + k + 1), fill=(0, 0, 0, 110 if T["dark"] else 70))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(1.2 * S)))
    ImageDraw.Draw(img).ellipse(box(cx - k, cy - k, cx + k, cy + k), fill=rgba(knob))
    return img


def build_switches():
    for step in range(10):
        save(switch(ease(step / 9)), f"switch_{step}.png", SW + GAP, SH)
    save(switch(0, True), "switch_disabled_off.png", SW + GAP, SH)
    save(switch(1, True), "switch_disabled_on.png", SW + GAP, SH)


# -------------------------------------------------------------------- tabs
def tab(name, fill, border, highlight, alpha=1.0):
    w, h = 96, 28
    img = layer(w, h)
    r = (0, 0, w, h)
    if fill:
        if not T["dark"] and "selected" in name:
            glow(img, w, h, (0, 1, w, h + 1), 8, T["shade"], .10, 1.2)
        fill_shape(img, mask_rr(w, h, r, 8), solid(w, h, fill, alpha))
        outline(img, w, h, r, 8, border, alpha)
        sheen(img, w, h, (1, 1, w - 1, h - 1), 7, highlight)
    save(img, name, w, h)


def thumb(name, color, alpha, vertical=True):
    w, h = (12, 160) if vertical else (160, 12)
    img = layer(w, h)
    r = (3, 2, 9, 158) if vertical else (2, 3, 158, 9)
    ImageDraw.Draw(img).rounded_rectangle(box(*r), radius=3 * S, fill=rgba(color, alpha))
    save(img, name, w, h)


def build_tabs_and_lists():
    tab("tab_selected.png", T["tab_selected"], T["tab_selected_border"], .09)
    tab("tab_hover.png", T["tab_hover"], T["tab_hover_border"], .03)
    tab("tab.png", None, None, 0)
    for v, suffix in ((True, "v"), (False, "h")):
        thumb(f"thumb_{suffix}.png", T["thumb"], 1, v)
        thumb(f"thumb_{suffix}_hover.png", T["thumb_hover"], 1, v)
        thumb(f"thumb_{suffix}_pressed.png", T["thumb_pressed"], 1, v)
    img = layer(256, 40)
    img.alpha_composite(solid(256, 40, T["surface_high"]))
    ImageDraw.Draw(img).rectangle(box(0, 39, 256, 40), fill=rgba(T["heading_line"]))
    save(img, "heading.png", 256, 40)
    img = layer(256, 40)
    img.alpha_composite(solid(256, 40, T["heading_hover"]))
    ImageDraw.Draw(img).rectangle(box(0, 39, 256, 40), fill=rgba(T["heading_hover_line"]))
    save(img, "heading_hover.png", 256, 40)


# --------------------------------------------------------------- sidebar
NW, NH = 198, 38


def build_sidebar():
    img = layer(NW, NH)
    r = (0, 0, NW, NH)
    a = T["nav_fill_a"]
    fill_shape(img, mask_rr(NW, NH, r, 11), hgradient(NW, NH, [(0, T["accent_deep"], a), (.55, T["accent_deep"], a * .43),
                                                                (1, T["accent_deep"], a * .2)]))
    outline(img, NW, NH, r, 11, T["nav_outline"], 1, fade=(.30 if T["dark"] else .55, .05))
    bar = layer(NW, NH)
    ImageDraw.Draw(bar).rounded_rectangle(box(0, 10, 3.2, NH - 10), radius=1.6 * S, fill=rgba(T["nav_bar"]))
    g = bar.filter(ImageFilter.GaussianBlur(3 * S))
    img.alpha_composite(g)
    if T["dark"]:
        img.alpha_composite(g)
    img.alpha_composite(bar)
    save(img, "nav_active.png", NW, NH)
    for k in range(1, 7):
        img = layer(NW, NH)
        a = k / 6
        fill_shape(img, mask_rr(NW, NH, r, 11), solid(NW, NH, T["nav_overlay"], (.045 if T["dark"] else .05) * a))
        outline(img, NW, NH, r, 11, T["nav_overlay"], (.05 if T["dark"] else .07) * a)
        save(img, f"nav_hover_{k}.png", NW, NH)
    img = layer(NW, NH)
    outline(img, NW, NH, (1, 1, NW - 1, NH - 1), 10, T["nav_focus"], .8, width=1.4)
    save(img, "nav_focus.png", NW, NH)
    img = layer(30, 20)
    fill_shape(img, mask_rr(30, 20, (0, 0, 30, 20), 10), solid(30, 20, T["badge"]))
    outline(img, 30, 20, (0, 0, 30, 20), 10, T["badge_border"], 1)
    save(img, "badge.png", 30, 20)
    # search button (sidebar) 9-slice source
    rr = (0, 0, 40, 40)
    for name, fill, border in (("search.png", T["search"], T["search_border"]),
                               ("search_hover.png", T["search_hover"], T["search_hover_border"])):
        img = layer(40, 40)
        fill_shape(img, mask_rr(40, 40, rr, 11), solid(40, 40, fill))
        outline(img, 40, 40, rr, 11, border, 1)
        save(img, name, 40, 40)
    img = layer(34, 20)
    fill_shape(img, mask_rr(34, 20, (0, 0, 34, 20), 6), solid(34, 20, T["kbd"]))
    outline(img, 34, 20, (0, 0, 34, 20), 6, T["kbd_border"], 1)
    save(img, "kbd.png", 34, 20)


# ------------------------------------------------------------------ chips
def chip(name, color, size, radius):
    img = layer(size, size)
    r = (0, 0, size, size)
    k = 1 if T["dark"] else .75
    fill_shape(img, mask_rr(size, size, r, radius), vgradient(size, size, [(0, color, .20 * k), (1, color, .09 * k)]))
    outline(img, size, size, r, radius, color, .32 if T["dark"] else .4)
    sheen(img, size, size, (1, 1, size - 1, size - 1), radius - 1, .14)
    save(img, name, size, size)


def build_chips():
    colors = {T[k] for k in ("accent", "blue", "amber", "green", "red", "cyan")}
    for color in sorted(colors):
        chip(f"chip34_{color[1:].lower()}.png", color, 34, 10)
        chip(f"chip46_{color[1:].lower()}.png", color, 46, 14)


# ---------------------------------------------------------- dashboard hero
HH = 204
LEFT, RIGHT = 28, 560
HW = LEFT + 40 + RIGHT


def rnd(seed):
    return random.Random(seed)


def blur_layer(draw_fn, radius):
    g = layer(HW, HH)
    draw_fn(ImageDraw.Draw(g))
    return g.filter(ImageFilter.GaussianBlur(radius * S))


def motif_aurora(img):
    cx, cy = HW - 190, HH / 2
    deco = layer(HW, HH)
    dd = ImageDraw.Draw(deco)
    for radius, color, a in ((46, "#FFFFFF", .10), (70, T["accent"], .20), (96, "#FFFFFF", .07)):
        dd.ellipse(box(cx - radius, cy - radius, cx + radius, cy + radius), outline=rgba(color, a), width=round(1.1 * S))
    for x in range(int(cx - 170), int(cx + 170), 9):
        dd.line(box(x, cy, x + 3, cy), fill=rgba("#FFFFFF", .06), width=S)
    for y in range(0, HH, 9):
        dd.line(box(cx, y, cx, y + 3), fill=rgba("#FFFFFF", .06), width=S)
    return deco


def star_shape(d, x, y, r, color, a):
    d.polygon([(x * S, (y - r) * S), ((x + r * .22) * S, (y - r * .22) * S), ((x + r) * S, y * S),
               ((x + r * .22) * S, (y + r * .22) * S), (x * S, (y + r) * S), ((x - r * .22) * S, (y + r * .22) * S),
               ((x - r) * S, y * S), ((x - r * .22) * S, (y - r * .22) * S)], fill=rgba(color, a))


def motif_anime(img):
    """Небесный экспресс: ночное небо, луна, облака и поезд с тёплыми окнами (оригинальная сцена)."""
    x0 = HW - 420
    sky = layer(HW, HH)
    fill_shape(sky, Image.new("L", (HW * S, HH * S), 255),
               vgradient(HW, HH, [(0, "#161A48", 1), (.62, "#3B2C6E", 1), (1, "#7A4E8E", 1)]))
    deco = layer(HW, HH)
    deco.alpha_composite(sky)
    d = ImageDraw.Draw(deco)
    r = rnd(7)
    for _ in range(90):
        x, y, s = r.uniform(x0, HW), r.uniform(4, HH * .62), r.uniform(.5, 1.4)
        d.ellipse(box(x - s, y - s, x + s, y + s), fill=rgba(r.choice(("#FFFFFF", "#CFE6FF", "#FFD9EC")), r.uniform(.35, .95)))
    for x, y, s in ((HW - 250, 30, 5), (HW - 60, 70, 4), (HW - 330, 64, 3.5)):
        star_shape(d, x, y, s, "#FFFFFF", .9)
    # Moon with halo.
    mx, my, mr = HW - 118, 50, 24
    deco.alpha_composite(blur_layer(lambda g: g.ellipse(box(mx - 46, my - 46, mx + 46, my + 46), fill=rgba("#FFE9B8", .35)), 14))
    moon = layer(HW, HH)
    md = ImageDraw.Draw(moon)
    md.ellipse(box(mx - mr, my - mr, mx + mr, my + mr), fill=rgba("#FFF2CF"))
    md.ellipse(box(mx - mr + 11, my - mr - 6, mx + mr + 11, my + mr - 6), fill=(0, 0, 0, 0))
    deco.alpha_composite(moon)
    # Shooting star.
    streak = layer(HW, HH)
    sd = ImageDraw.Draw(streak)
    for i in range(30):
        t = i / 29
        sd.line(box(HW - 300 + 70 * t, 22 + 26 * t, HW - 300 + 70 * t + 3, 22 + 26 * t + 1.1), fill=rgba("#FFFFFF", .05 + .7 * t), width=S)
    deco.alpha_composite(streak)
    # Cloud banks.
    def clouds(g):
        for cx, cy, rx, ry, col, a in ((HW - 380, 178, 120, 34, "#C9A7E8", .55), (HW - 200, 186, 160, 36, "#E7B6D9", .6),
                                       (HW - 40, 176, 120, 40, "#B9A4FF", .5), (HW - 290, 196, 200, 26, "#F2C7DE", .55)):
            g.ellipse(box(cx - rx, cy - ry, cx + rx, cy + ry), fill=rgba(col, a))
    deco.alpha_composite(blur_layer(clouds, 9))
    # Track and train.
    ty = 148
    d = ImageDraw.Draw(deco)
    d.line(box(x0, ty + 9, HW, ty + 9), fill=rgba("#0E1030", .9), width=round(2 * S))
    for x in range(int(x0), HW, 22):
        d.line(box(x, ty + 9, x + 6, ty + 18), fill=rgba("#0E1030", .55), width=round(1.4 * S))
    glow_l = layer(HW, HH)
    gd = ImageDraw.Draw(glow_l)
    cars = [(HW - 380, 64), (HW - 312, 64), (HW - 244, 64), (HW - 176, 74)]
    for i, (x, w) in enumerate(cars):
        top = ty - 22 if i < 3 else ty - 26
        d.rounded_rectangle(box(x, top, x + w - 4, ty + 6), radius=6 * S, fill=rgba("#141638"))
        d.rounded_rectangle(box(x + 2, top + 1, x + w - 6, top + 4), radius=2 * S, fill=rgba("#2B2E66"))
        for wx in range(int(x + 8), int(x + w - 14), 13):
            d.rounded_rectangle(box(wx, top + 7, wx + 8, top + 14), radius=1.5 * S, fill=rgba("#FFD98A"))
            gd.ellipse(box(wx - 8, top - 2, wx + 16, top + 24), fill=rgba("#FFC86B", .30))
        for wx in (x + 10, x + w - 18):
            d.ellipse(box(wx, ty + 2, wx + 8, ty + 10), fill=rgba("#0A0B22"))
    lx = HW - 176 + 70
    d.polygon([(lx * S, (ty - 6) * S), ((lx + 14) * S, (ty + 4) * S), (lx * S, (ty + 6) * S)], fill=rgba("#141638"))
    gd.ellipse(box(lx + 2, ty - 10, lx + 60, ty + 10), fill=rgba("#FFE2A0", .35))
    deco.alpha_composite(glow_l.filter(ImageFilter.GaussianBlur(5 * S)))
    # Steam of little stars behind the train.
    for i in range(10):
        x = HW - 400 - i * 16 + r.uniform(-4, 4)
        y = ty - 30 - i * 4 + r.uniform(-4, 4)
        star_shape(ImageDraw.Draw(deco), x, y, 2 + r.uniform(0, 1.6), "#FFFFFF", .55 - i * .045)
    return deco


def motif_winter(img):
    """Уютный вечер: окно в зимний вечер, снег, домик с тёплым светом и гирлянда."""
    deco = layer(HW, HH)
    wx1, wy1, wx2, wy2 = HW - 330, 20, HW - 34, 186
    win = layer(HW, HH)
    fill_shape(win, mask_rr(HW, HH, (wx1, wy1, wx2, wy2), 16),
               vgradient(HW, HH, [(0, "#4F6F9C", 1), (.55, "#8FAED2", 1), (1, "#D6E3F1", 1)]))
    wd = ImageDraw.Draw(win)
    r = rnd(11)
    for _ in range(24):
        x, y = r.uniform(wx1 + 10, wx2 - 10), r.uniform(wy1 + 6, wy1 + 70)
        s = r.uniform(.5, 1.1)
        wd.ellipse(box(x - s, y - s, x + s, y + s), fill=rgba("#FFFFFF", r.uniform(.4, .9)))
    wd.ellipse(box(wx2 - 82, wy1 + 14, wx2 - 58, wy1 + 38), fill=rgba("#FFF6DD", .95))
    # Hills and pines.
    wd.ellipse(box(wx1 - 60, wy2 - 70, wx1 + 190, wy2 + 70), fill=rgba("#F4F8FC"))
    wd.ellipse(box(wx1 + 110, wy2 - 56, wx2 + 60, wy2 + 80), fill=rgba("#E7EFF8"))
    for px, ph in ((wx1 + 40, 46), (wx1 + 64, 34), (wx2 - 40, 50), (wx2 - 66, 38)):
        base = wy2 - 34 if px < wx1 + 100 else wy2 - 26
        wd.rectangle(box(px - 2, base - 4, px + 2, base + 2), fill=rgba("#5A4034"))
        # Ёлка из трёх ярусов, каждый присыпан снегом.
        for tier in range(3):
            top = base - ph + tier * ph * .26
            half = ph * (.18 + tier * .09)
            bottom = top + ph * .42
            wd.polygon([(px * S, top * S), ((px + half) * S, bottom * S), ((px - half) * S, bottom * S)], fill=rgba("#2D5560"))
            wd.polygon([(px * S, top * S), ((px + half * .45) * S, (top + ph * .17) * S), ((px - half * .45) * S, (top + ph * .17) * S)], fill=rgba("#FFFFFF", .9))
    # Cottage with a warm window.
    hx, hy = wx1 + 150, wy2 - 46
    wd.rectangle(box(hx, hy - 26, hx + 44, hy + 6), fill=rgba("#6A4A3C"))
    wd.polygon([((hx - 6) * S, (hy - 24) * S), ((hx + 22) * S, (hy - 46) * S), ((hx + 50) * S, (hy - 24) * S)], fill=rgba("#FFFFFF"))
    wd.rectangle(box(hx + 14, hy - 16, hx + 30, hy - 4), fill=rgba("#FFC36B"))
    deco.alpha_composite(win)
    deco.alpha_composite(blur_layer(lambda g: g.ellipse(box(hx - 10, hy - 34, hx + 54, hy + 18), fill=rgba("#FFB45A", .45)), 8))
    # Frame and sill.
    d = ImageDraw.Draw(deco)
    d.rounded_rectangle(box(wx1, wy1, wx2, wy2), radius=16 * S, outline=rgba("#FFFFFF"), width=round(5 * S))
    d.line(box((wx1 + wx2) / 2, wy1 + 2, (wx1 + wx2) / 2, wy2 - 2), fill=rgba("#FFFFFF"), width=round(4 * S))
    d.rounded_rectangle(box(wx1 - 10, wy2 - 4, wx2 + 10, wy2 + 6), radius=3 * S, fill=rgba("#C9D6E4"))
    # Garland of warm lights along the top.
    lights = layer(HW, HH)
    ld = ImageDraw.Draw(lights)
    pts = [(wx1 - 6 + (wx2 - wx1 + 12) * i / 20, wy1 + 4 + 9 * math.sin(math.pi * (i % 10) / 10)) for i in range(21)]
    d.line([(round(x * S), round(y * S)) for x, y in pts], fill=rgba("#6B7B8F"), width=round(1.2 * S))
    for i, (x, y) in enumerate(pts[1:-1:2]):
        col = ("#FFC86B", "#FF9A7A", "#FFE2A0")[i % 3]
        ld.ellipse(box(x - 8, y - 4, x + 8, y + 12), fill=rgba(col, .55))
        d.ellipse(box(x - 3, y + 1, x + 3, y + 8), fill=rgba(col))
    deco.alpha_composite(lights.filter(ImageFilter.GaussianBlur(4 * S)))
    # Cocoa mug on the sill.
    cx, cy = wx2 - 28, wy2 - 6
    d.rounded_rectangle(box(cx - 16, cy - 24, cx + 12, cy), radius=5 * S, fill=rgba("#C0563F"))
    d.ellipse(box(cx + 6, cy - 19, cx + 20, cy - 7), outline=rgba("#C0563F"), width=round(3 * S))
    d.ellipse(box(cx - 15, cy - 27, cx + 11, cy - 21), fill=rgba("#7A4A33"))
    for k, sx in enumerate((cx - 8, cx + 2)):
        pts = [(sx + 3 * math.sin(i / 2.2 + k), cy - 30 - i * 2.4) for i in range(10)]
        d.line([(round(x * S), round(y * S)) for x, y in pts], fill=rgba("#FFFFFF", .8), width=round(1.4 * S), joint="curve")
    return deco


def blossom(d, x, y, r, petal, centre, a=1.0, rot=0.0):
    for i in range(5):
        ang = rot + i * 2 * math.pi / 5
        px, py = x + math.cos(ang) * r * .62, y + math.sin(ang) * r * .62
        d.ellipse(box(px - r * .52, py - r * .52, px + r * .52, py + r * .52), fill=rgba(petal, a))
    d.ellipse(box(x - r * .28, y - r * .28, x + r * .28, y + r * .28), fill=rgba(centre, a))


def motif_spring(img):
    """Цветение: ветка сакуры, мягкие холмы и бледное солнце."""
    deco = layer(HW, HH)
    wash = layer(HW, HH)
    fill_shape(wash, Image.new("L", (HW * S, HH * S), 255),
               vgradient(HW, HH, [(0, "#FDEFF3", 1), (.6, "#F6F1E6", 1), (1, "#EAF3E4", 1)]))
    deco.alpha_composite(wash)
    deco.alpha_composite(blur_layer(lambda g: g.ellipse(box(HW - 250, 24, HW - 170, 104), fill=rgba("#FFF4D2", .9)), 10))

    def hills(g):
        g.ellipse(box(HW - 460, 150, HW - 120, 330), fill=rgba("#CFE5C4"))
        g.ellipse(box(HW - 260, 160, HW + 80, 320), fill=rgba("#B9D9AE"))
    deco.alpha_composite(blur_layer(hills, 1.5))
    d = ImageDraw.Draw(deco)
    # Branch from the top-right corner.
    branch = [(HW + 4, 18), (HW - 60, 40), (HW - 130, 52), (HW - 210, 72), (HW - 270, 96)]
    d.line([(round(x * S), round(y * S)) for x, y in branch], fill=rgba("#7A5245"), width=round(5 * S), joint="curve")
    for (x1, y1), (x2, y2), w in ((((HW - 60, 40)), (HW - 92, 92), 3), (((HW - 130, 52)), (HW - 150, 18), 2.6),
                                  (((HW - 210, 72)), (HW - 236, 126), 2.4), (((HW - 30, 30)), (HW - 24, 82), 2.6)):
        d.line(box(x1, y1, x2, y2), fill=rgba("#7A5245"), width=round(w * S))
    r = rnd(5)
    spots = [(HW - 60, 40), (HW - 92, 92), (HW - 130, 52), (HW - 150, 18), (HW - 210, 72), (HW - 236, 126),
             (HW - 270, 96), (HW - 24, 82), (HW - 100, 64), (HW - 180, 60)]
    for x, y in spots:
        for _ in range(3):
            blossom(d, x + r.uniform(-14, 14), y + r.uniform(-10, 10), r.uniform(6, 9.5),
                    r.choice(("#F7C3D2", "#F4B0C4", "#FAD4DF")), "#D9708F", 1.0, r.uniform(0, 6.3))
    for _ in range(9):
        x, y = r.uniform(HW - 380, HW - 20), r.uniform(100, 190)
        d.ellipse(box(x - 3.6, y - 2.2, x + 3.6, y + 2.2), fill=rgba("#F4B0C4", .85))
    return deco


def motif_summer(img):
    """Золотой час: закатное солнце над бирюзовым морем, лучи и чайки."""
    deco = layer(HW, HH)
    sky = layer(HW, HH)
    fill_shape(sky, Image.new("L", (HW * S, HH * S), 255),
               vgradient(HW, HH, [(0, "#FCE4B0", 1), (.55, "#F7C27E", 1), (.7, "#F2A27A", 1), (1, "#F2A27A", 1)]))
    deco.alpha_composite(sky)
    sx, sy, horizon = HW - 190, 132, 138
    rays = layer(HW, HH)
    rd = ImageDraw.Draw(rays)
    for i in range(13):
        ang = math.pi + i * math.pi / 12
        rd.polygon([(sx * S, sy * S), ((sx + 300 * math.cos(ang - .05)) * S, (sy + 300 * math.sin(ang - .05)) * S),
                    ((sx + 300 * math.cos(ang + .05)) * S, (sy + 300 * math.sin(ang + .05)) * S)], fill=rgba("#FFF3D0", .22))
    deco.alpha_composite(rays.filter(ImageFilter.GaussianBlur(4 * S)))
    deco.alpha_composite(blur_layer(lambda g: g.ellipse(box(sx - 80, sy - 80, sx + 80, sy + 80), fill=rgba("#FFE7A8", .8)), 18))
    d = ImageDraw.Draw(deco)
    d.ellipse(box(sx - 34, sy - 34, sx + 34, sy + 34), fill=rgba("#FFD978"))
    # Sea.
    sea = layer(HW, HH)
    fill_shape(sea, mask_rr(HW, HH, (0, horizon, HW, HH + 30), 0),
               vgradient(HW, HH, [(0, "#2FA3A0", 1), (horizon / HH, "#2FA3A0", 1), (1, "#16716F", 1)]))
    deco.alpha_composite(sea)
    d = ImageDraw.Draw(deco)
    r = rnd(3)
    for i in range(16):
        y = horizon + 6 + i * 4
        half = 60 - i * 2.6 + r.uniform(-6, 6)
        d.line(box(sx - half, y, sx + half, y), fill=rgba("#FFE3A0", .75 - i * .04), width=round(1.4 * S))
    for _ in range(18):
        x, y = r.uniform(HW - 420, HW), r.uniform(horizon + 8, HH - 6)
        d.line(box(x, y, x + r.uniform(8, 20), y), fill=rgba("#BFF0EA", .45), width=round(1 * S))
    # Island and gulls.
    d.ellipse(box(HW - 420, horizon - 14, HW - 300, horizon + 12), fill=rgba("#2E6F67"))
    # Маяк на острове: белая башня с красными полосами и тёплым огнём.
    lx, base = HW - 372, horizon - 8
    d.polygon([((lx - 6) * S, base * S), ((lx - 4) * S, (base - 34) * S), ((lx + 4) * S, (base - 34) * S), ((lx + 6) * S, base * S)], fill=rgba("#FFF6E6"))
    for k in (8, 22):
        d.polygon([((lx - 5.6) * S, (base - k) * S), ((lx - 5.1) * S, (base - k - 6) * S), ((lx + 5.1) * S, (base - k - 6) * S), ((lx + 5.6) * S, (base - k) * S)], fill=rgba("#D2533F"))
    d.rectangle(box(lx - 4.5, base - 40, lx + 4.5, base - 34), fill=rgba("#FFD978"))
    d.polygon([((lx - 6) * S, (base - 40) * S), (lx * S, (base - 46) * S), ((lx + 6) * S, (base - 40) * S)], fill=rgba("#B8473A"))
    deco.alpha_composite(blur_layer(lambda g: g.ellipse(box(lx - 16, base - 52, lx + 16, base - 22), fill=rgba("#FFE7A8", .55)), 6))
    d = ImageDraw.Draw(deco)
    for gx, gy, s in ((HW - 120, 50, 7), (HW - 96, 62, 5), (HW - 280, 40, 6)):
        d.line(box(gx - s, gy, gx, gy + s * .5) + box(gx + s, gy, 0, 0)[:2], fill=rgba("#6B4A3A"), width=round(1.4 * S), joint="curve")
    return deco


def maple(d, x, y, r, color, rot=0.0, a=1.0):
    pts = []
    lobes = [(0, 1.0), (.35, .45), (.75, .95), (1.05, .5), (1.45, .78), (1.62, .3), (1.75, .2)]
    for sign in (1, -1):
        seq = lobes if sign == 1 else list(reversed(lobes))
        for ang, rad in seq:
            th = rot - math.pi / 2 + sign * ang
            pts.append(((x + math.cos(th) * r * rad) * S, (y + math.sin(th) * r * rad) * S))
    d.polygon(pts, fill=rgba(color, a))
    d.line(box(x, y, x + math.cos(rot + math.pi / 2) * r * .9, y + math.sin(rot + math.pi / 2) * r * .9),
           fill=rgba(mix(color, "#3A1E10", .45), a), width=round(1.2 * S))


def motif_autumn(img):
    """Плед и какао: клетчатый плед, кружка с паром, тёплый свет и кленовые листья."""
    deco = layer(HW, HH)
    deco.alpha_composite(blur_layer(lambda g: g.ellipse(box(HW - 330, -40, HW - 40, 220), fill=rgba("#E08A3C", .22)), 30))
    # Plaid corner.
    plaid = layer(HW, HH)
    pd = ImageDraw.Draw(plaid)
    px1, py1 = HW - 300, 128
    pd.rounded_rectangle(box(px1, py1, HW + 30, HH + 30), radius=18 * S, fill=rgba("#8E3B2A"))
    for i in range(0, 360, 26):
        pd.rectangle(box(px1 + i, py1, px1 + i + 9, HH + 30), fill=rgba("#B9643A", .55))
    for j in range(0, 120, 22):
        pd.rectangle(box(px1, py1 + j, HW + 30, py1 + j + 7), fill=rgba("#E6B06A", .30))
        pd.rectangle(box(px1, py1 + j + 12, HW + 30, py1 + j + 13.5), fill=rgba("#2A120C", .35))
    plaid = plaid.rotate(-6, resample=Image.Resampling.BICUBIC, center=((HW - 150) * S, 160 * S))
    deco.alpha_composite(plaid)
    d = ImageDraw.Draw(deco)
    # Mug.
    cx, cy = HW - 150, 128
    deco.alpha_composite(blur_layer(lambda g: g.ellipse(box(cx - 50, cy - 14, cx + 54, cy + 18), fill=rgba("#000000", .45)), 6))
    d = ImageDraw.Draw(deco)
    d.rounded_rectangle(box(cx - 30, cy - 52, cx + 26, cy + 4), radius=10 * S, fill=rgba("#F1DFC8"))
    d.ellipse(box(cx + 16, cy - 42, cx + 42, cy - 16), outline=rgba("#F1DFC8"), width=round(5 * S))
    d.ellipse(box(cx - 28, cy - 58, cx + 24, cy - 46), fill=rgba("#6B3B22"))
    d.ellipse(box(cx - 12, cy - 56, cx + 4, cy - 50), fill=rgba("#FFF4E6", .9))
    for k in range(3):
        sx = cx - 14 + k * 11
        pts = [(sx + 4 * math.sin(i / 2 + k * 1.3), cy - 64 - i * 3) for i in range(12)]
        d.line([(round(x * S), round(y * S)) for x, y in pts], fill=rgba("#FFF1E0", .45), width=round(2 * S), joint="curve")
    r = rnd(9)
    for _ in range(11):
        x, y = r.uniform(HW - 420, HW - 10), r.uniform(10, 120)
        maple(d, x, y, r.uniform(7, 12), r.choice(("#E58A3A", "#D9573A", "#E9B44C", "#C2702A")), r.uniform(-1, 1), .95)
    return deco


MOTIFS = {"aurora": motif_aurora, "anime": motif_anime, "winter": motif_winter, "spring": motif_spring,
          "summer": motif_summer, "autumn": motif_autumn}


def hero_strip(motif):
    w = HW
    img = layer(w, HH)
    r = (0, 0, w, HH)
    base = vgradient(w, HH, [(0, T["hero_top"], 1), (1, T["hero_bottom"], 1)])
    fill_shape(img, mask_rr(w, HH, r, 18), base)
    glows = layer(w, HH)
    gd = ImageDraw.Draw(glows)
    cx, cy = w - 190, HH / 2
    k = 1 if T["dark"] else .55
    gd.ellipse(box(cx - 170, cy - 150, cx + 170, cy + 150), fill=rgba(T["accent_deep"], .30 * k))
    gd.ellipse(box(w - 110, -90, w + 90, 90), fill=rgba(T["accent2"], .14 * k))
    gd.ellipse(box(LEFT + 40 + 60, -130, LEFT + 40 + 340, 60), fill=rgba(T["accent_deep"], .06 * k))
    glows = glows.filter(ImageFilter.GaussianBlur(44 * S))
    glows.putalpha(ImageChops.multiply(glows.getchannel("A"), mask_rr(w, HH, r, 18)))
    img.alpha_composite(glows)
    deco = MOTIFS[motif](img)
    # The scene fades out towards the text so the left part of the hero stays calm.
    start = (w - 430) / w if motif != "aurora" else (w - 400) / w
    end = (w - 300) / w if motif != "aurora" else (w - 330) / w
    fade = hgradient(w, HH, [(0, "#FFFFFF", 0), (start, "#FFFFFF", 0), (end, "#FFFFFF", 1), (1, "#FFFFFF", 1)]).getchannel("A")
    deco.putalpha(ImageChops.multiply(deco.getchannel("A"), ImageChops.multiply(fade, mask_rr(w, HH, r, 18))))
    img.alpha_composite(deco)
    outline(img, w, HH, r, 18, T["hero_outline"], 1)
    sheen(img, w, HH, (1, 1, w - 1, HH - 1), 17, .10)
    flat = Image.new("RGBA", img.size, rgba(T["background"]))
    flat.alpha_composite(img)
    full = flat.resize((w, HH), Image.Resampling.LANCZOS)
    full.crop((0, 0, LEFT, HH)).save(target / "hero_left.png")
    full.crop((LEFT + 10, 0, LEFT + 12, HH)).save(target / "hero_mid.png")
    full.crop((w - RIGHT, 0, w, HH)).save(target / "hero_right.png")
    return full


def build_hero_bits():
    # glass core with spark in the middle of the hero rings (Aurora motif)
    core_size = 92
    img = layer(core_size, core_size)
    cr = (16, 16, core_size - 16, core_size - 16)
    glow(img, core_size, core_size, cr, 17, T["accent_deep"], .75, 9)
    fill_shape(img, mask_rr(core_size, core_size, cr, 17), vgradient(core_size, core_size, [(0, T["core_top"], 1), (1, T["core_bottom"], 1)]))
    outline(img, core_size, core_size, cr, 17, T["core_border"], .75)
    outline(img, core_size, core_size, (17, 17, core_size - 17, core_size - 17), 16, "#FFFFFF", 1, fade=(.30, 0))
    core = img.resize((core_size, core_size), Image.Resampling.LANCZOS)
    spark = glyph("spark", 30, T["spark"])
    core.alpha_composite(spark, (core_size // 2 - 15, core_size // 2 - 15))
    core.save(target / "hero_core.png")
    for color in sorted({T["accent"], T["green"], T["cyan"], T["amber"], T["red"], T["muted"]}):
        dot(f"dot_{color[1:].lower()}.png", color)
    dot("progress_head.png", T["progress_head"], 18, 2.6)
    for k in range(18):
        size = 220
        img = layer(size, size)
        t = k / 17
        radius = 34 + 72 * (1 - (1 - t) ** 2)
        a = .42 * (1 - t) ** 1.4
        ImageDraw.Draw(img).ellipse(box(size / 2 - radius, size / 2 - radius, size / 2 + radius, size / 2 + radius),
                                    outline=rgba(T["accent"], a), width=round(1.6 * S))
        save(img, f"pulse_{k}.png", size, size)


def dot(name, color, size=20, core_r=3.2):
    img = layer(size, size)
    c = size / 2
    g = layer(size, size)
    ImageDraw.Draw(g).ellipse(box(c - 6, c - 6, c + 6, c + 6), fill=rgba(color, .55))
    img.alpha_composite(g.filter(ImageFilter.GaussianBlur(3 * S)))
    ImageDraw.Draw(img).ellipse(box(c - core_r, c - core_r, c + core_r, c + core_r), fill=rgba(color))
    ImageDraw.Draw(img).ellipse(box(c - core_r * .45, c - core_r * .45, c + core_r * .45, c + core_r * .45), fill=rgba("#FFFFFF", .85))
    save(img, name, size, size)


# ------------------------------------------------------------ toast & misc
def build_toasts():
    for name, color in (("ok", T["green"]), ("error", T["red"]), ("info", T["accent"])):
        size = 26
        img = layer(size, size)
        ImageDraw.Draw(img).ellipse(box(0, 0, size, size), fill=rgba(color, .16))
        outline(img, size, size, (0, 0, size, size), size / 2, color, .45)
        d = ImageDraw.Draw(img)
        w = round(2 * S)
        if name == "ok":
            d.line(box(8, 13.5, 11.5, 17)[:2] + box(11.5, 17, 18.5, 9.5), fill=rgba(color), width=w, joint="curve")
        elif name == "error":
            d.line(box(13, 7.5, 13, 14.5), fill=rgba(color), width=w)
            d.ellipse(box(11.8, 17, 14.2, 19.4), fill=rgba(color))
        else:
            d.ellipse(box(11.8, 6.8, 14.2, 9.2), fill=rgba(color))
            d.line(box(13, 12, 13, 19), fill=rgba(color), width=w)
        save(img, f"toast_{name}.png", size, size)
    img = layer(48, 48)
    rr = (0, 0, 48, 48)
    fill_shape(img, mask_rr(48, 48, rr, 14), vgradient(48, 48, [(0, T["toast_top"], 1), (1, T["toast_bottom"], 1)]))
    outline(img, 48, 48, rr, 14, T["toast_border"], 1)
    sheen(img, 48, 48, (1, 1, 47, 47), 13, .12)
    save(img, "toast.png", 48, 48)
    img = layer(40, 40)
    # Flat fill: this source is 9-sliced, and a horizontal gradient would tile into stripes.
    fill_shape(img, mask_rr(40, 40, (0, 0, 40, 40), 10), solid(40, 40, T["accent_deep"], .22 if T["dark"] else .14))
    outline(img, 40, 40, (0, 0, 40, 40), 10, T["row_active_border"], 1, fade=(.30, .06))
    save(img, "row_active.png", 40, 40)


# ------------------------------------------------------------ particles
def particle_canvas(size):
    return Image.new("RGBA", (size * S, size * S), (0, 0, 0, 0))


def save_particle(img, name, size):
    img.resize((size, size), Image.Resampling.LANCZOS).save(target / name)


def build_particles(theme_id):
    """Спрайты атмосферы и манифест particles.json для app/atmosphere.py."""
    atmosphere = THEMES[theme_id]["atmosphere"]
    kind, colors = atmosphere["kind"], atmosphere["colors"]
    sprites = []
    if kind in ("sparks", "dust"):
        for ci, color in enumerate(colors):
            for size in (10, 16):
                img = particle_canvas(size)
                c = size / 2
                g = particle_canvas(size)
                ImageDraw.Draw(g).ellipse(box(c - size * .32, c - size * .32, c + size * .32, c + size * .32), fill=rgba(color, .55))
                img.alpha_composite(g.filter(ImageFilter.GaussianBlur(size * .14 * S)))
                core = size * (.11 if kind == "sparks" else .16)
                ImageDraw.Draw(img).ellipse(box(c - core, c - core, c + core, c + core), fill=rgba(mix(color, "#FFFFFF", .5)))
                name = f"particle_{ci}_{size}.png"
                save_particle(img, name, size)
                sprites.append([name])
    elif kind == "stars":
        for ci, color in enumerate(colors):
            for size in (9, 14):
                frames = []
                for f, scale in enumerate((.55, .8, 1.0)):
                    img = particle_canvas(size)
                    c = size / 2
                    g = particle_canvas(size)
                    ImageDraw.Draw(g).ellipse(box(c - 2.5, c - 2.5, c + 2.5, c + 2.5), fill=rgba(color, .5 * scale))
                    img.alpha_composite(g.filter(ImageFilter.GaussianBlur(1.6 * S)))
                    star_shape(ImageDraw.Draw(img), c, c, size * .46 * scale, color, .95)
                    name = f"particle_{ci}_{size}_{f}.png"
                    save_particle(img, name, size)
                    frames.append(name)
                sprites.append(frames)
        streak = Image.new("RGBA", (90 * S, 30 * S), (0, 0, 0, 0))
        sd = ImageDraw.Draw(streak)
        for i in range(60):
            t = i / 59
            sd.ellipse(((4 + 80 * t) * S - 1.4 * S * t, (4 + 22 * t) * S - 1.4 * S * t,
                        (4 + 80 * t) * S + 1.4 * S * t, (4 + 22 * t) * S + 1.4 * S * t), fill=rgba("#FFFFFF", .08 + .8 * t ** 2))
        streak.resize((90, 30), Image.Resampling.LANCZOS).save(target / "particle_streak.png")
    elif kind == "snow":
        for ci, color in enumerate(colors):
            for size in (7, 10, 14):
                img = particle_canvas(size)
                c = size / 2
                g = particle_canvas(size)
                ImageDraw.Draw(g).ellipse(box(c - size * .36, c - size * .36, c + size * .36, c + size * .36), fill=rgba("#7E9CC2", .35))
                img.alpha_composite(g.filter(ImageFilter.GaussianBlur(size * .1 * S)))
                d = ImageDraw.Draw(img)
                if size >= 14:
                    for k in range(3):
                        ang = k * math.pi / 3
                        d.line(box(c - math.cos(ang) * size * .38, c - math.sin(ang) * size * .38,
                                   c + math.cos(ang) * size * .38, c + math.sin(ang) * size * .38), fill=rgba(color), width=round(1.3 * S))
                else:
                    d.ellipse(box(c - size * .26, c - size * .26, c + size * .26, c + size * .26), fill=rgba(color))
                name = f"particle_{ci}_{size}.png"
                save_particle(img, name, size)
                sprites.append([name])
    elif kind in ("petals", "leaves"):
        for ci, color in enumerate(colors):
            size = 16 if kind == "petals" else 18
            frames = []
            for f in range(8):
                rot = f * math.pi / 4
                img = particle_canvas(size)
                d = ImageDraw.Draw(img)
                c = size / 2
                if kind == "petals":
                    petal = Image.new("RGBA", (size * S, size * S), (0, 0, 0, 0))
                    pd = ImageDraw.Draw(petal)
                    pd.ellipse(box(c - 6.2, c - 3.6, c + 6.2, c + 3.6), fill=rgba(color))
                    pd.ellipse(box(c + 3.4, c - 1.2, c + 7.4, c + 1.2), fill=(0, 0, 0, 0))
                    pd.ellipse(box(c - 5, c - 2, c + 1, c + 1), fill=rgba(mix(color, "#FFFFFF", .45), .7))
                    # Narrowing the petal in some frames reads as a slow flip in the air.
                    squash = 1 - .55 * abs(math.sin(rot))
                    petal = petal.resize((size * S, max(1, round(size * S * squash))), Image.Resampling.BICUBIC)
                    holder = particle_canvas(size)
                    holder.alpha_composite(petal, (0, round((size * S - petal.height) / 2)))
                    img = holder.rotate(math.degrees(rot), resample=Image.Resampling.BICUBIC)
                else:
                    maple(d, c, c + .5, size * .44, color, rot)
                name = f"particle_{ci}_{f}.png"
                save_particle(img, name, size)
                frames.append(name)
            sprites.append(frames)
    manifest = {"kind": kind, "sprites": sprites, "streak": "particle_streak.png" if kind == "stars" else ""}
    (target / "particles.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")


# ------------------------------------------------------------------ icons
class RasterCanvas:
    def __init__(self, size, scale=S):
        self.scale = scale
        self.image = Image.new('RGBA', (size * scale, size * scale))
        self.draw = ImageDraw.Draw(self.image)

    def coords(self, values):
        return [round(v * self.scale) for v in values]

    def create_line(self, *pts, fill, width, **kwargs):
        xy = list(zip(self.coords(pts)[::2], self.coords(pts)[1::2])); w = round(width * self.scale)
        self.draw.line(xy, fill=fill, width=w, joint='curve')
        for x, y in (xy[0], xy[-1]):
            self.draw.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill=fill)

    def create_rectangle(self, *pts, outline, width, **kwargs):
        self.draw.rounded_rectangle(self.coords(pts), radius=round(1.2 * self.scale), outline=outline, width=round(width * self.scale))

    def create_oval(self, *pts, outline, width, **kwargs):
        self.draw.ellipse(self.coords(pts), outline=outline, width=round(width * self.scale))

    def create_arc(self, *pts, start, extent, outline, width, **kwargs):
        a, b = self.coords(pts)[::2], self.coords(pts)[1::2]
        points = []
        for i in range(121):
            t = math.radians(start + extent * i / 120)
            points.append(((a[0] + a[1]) / 2 + (a[1] - a[0]) / 2 * math.cos(t), (b[0] + b[1]) / 2 - (b[1] - b[0]) / 2 * math.sin(t)))
        self.draw.line(points, fill=outline, width=round(width * self.scale), joint='curve')


def glyph(name, size, color):
    c = RasterCanvas(size, 6)
    draw_vector_icon(c, name, 0, 0, color, size)
    return c.image.resize((size, size), Image.Resampling.LANCZOS)


def build_icons(colors):
    """Иконки темы одним пакетом: icons.dat (PNG подряд) + icons.json (имя → смещение, длина).

    ui_assets.photo() по имени icon_<glyph>_<size>_<hex>.png берёт байты из пакета и
    декодирует только нужные иконки. Один файл вместо ~2000 и без медленных «листов»:
    Tk очень долго обрабатывает широкие картинки с множеством прозрачных участков.
    """
    import io
    index, blob = {}, bytearray()
    for size in ICON_SIZES:
        for color in colors:
            for name in GLYPHS:
                buffer = io.BytesIO()
                glyph(name, size, color).save(buffer, format="PNG", optimize=True)
                data = buffer.getvalue()
                index[f"icon_{name}_{size}_{color[1:].lower()}.png"] = [len(blob), len(data)]
                blob.extend(data)
    (target / "icons.dat").write_bytes(bytes(blob))
    (target / "icons.json").write_text(json.dumps(index, separators=(",", ":")), encoding="utf-8")


# ------------------------------------------------------------------- logo
def logo(size):
    s = size * 6
    img = Image.new('RGBA', (s, s))
    grad = Image.new('RGBA', (s, s))
    gd = ImageDraw.Draw(grad)
    for i in range(s * 2):
        t = i / (s * 2)
        gd.line([(i, 0), (0, i)], fill=rgba(mix(mix("#A48BFF", "#7B5CFF", t), "#5B8CFF", max(0, t - .55) * 1.4)), width=2)
    m = Image.new('L', (s, s), 0)
    ImageDraw.Draw(m).rounded_rectangle((s * .04, s * .04, s * .96, s * .96), radius=s * .25, fill=255)
    img.paste(grad, (0, 0), m)
    shine = Image.new('RGBA', (s, s))
    ImageDraw.Draw(shine).ellipse((-s * .3, -s * .75, s * 1.1, s * .45), fill=(255, 255, 255, 38))
    shine.putalpha(ImageChops.multiply(shine.getchannel('A'), m))
    img.alpha_composite(shine)
    d = ImageDraw.Draw(img)
    stroke = round(s * .075)
    d.line([(s * .28, s * .72), (s * .50, s * .27), (s * .72, s * .72)], fill="#FFFFFF", width=stroke, joint='curve')
    for x, y in ((s * .28, s * .72), (s * .72, s * .72)):
        d.ellipse((x - stroke / 2, y - stroke / 2, x + stroke / 2, y + stroke / 2), fill="#FFFFFF")
    d.line([(s * .37, s * .565), (s * .63, s * .565)], fill="#FFFFFF", width=round(s * .064))
    return img.resize((size, size), Image.Resampling.LANCZOS)


def build_shared():
    sizes = (16, 20, 24, 32, 40, 42, 48, 64, 128, 256)
    for size in sizes:
        logo(size).save(ASSETS / f'app_{size}.png')
    logo(256).save(ASSETS / 'app.ico', format='ICO', sizes=[(n, n) for n in sizes if n != 42],
                   append_images=[logo(n) for n in sizes if n not in (42, 256)])


def build_preview(theme_id, hero):
    """Мини-превью темы для панели выбора: фон, боковая панель, карточки, акцент и мотив."""
    p = full_palette(theme_id)
    w, h = 132, 84
    img = layer(w, h)
    fill_shape(img, mask_rr(w, h, (0, 0, w, h), 10), solid(w, h, p["background"]))
    fill_shape(img, mask_rr(w, h, (0, 0, 30, h), 10), solid(w, h, p["sidebar"]))
    d = ImageDraw.Draw(img)
    d.rectangle(box(20, 0, 30, h), fill=rgba(p["sidebar"]))
    for i, y in enumerate((14, 24, 34, 44)):
        d.rounded_rectangle(box(6, y, 24, y + 5), radius=2 * S, fill=rgba(p["accent"] if i == 0 else p["muted_dim"], 1 if i == 0 else .8))
    banner = hero.crop((HW - 300, 0, HW, HH)).resize((94 * S, 28 * S), Image.Resampling.LANCZOS)
    bm = mask_rr(w, h, (34, 6, 128, 34), 6).crop((34 * S, 6 * S, 128 * S, 34 * S)).resize(banner.size)
    banner.putalpha(bm)
    img.alpha_composite(banner, (34 * S, 6 * S))
    for x1, x2 in ((34, 79), (83, 128)):
        fill_shape(img, mask_rr(w, h, (x1, 40, x2, 78), 6), solid(w, h, p["surface"]))
        outline(img, w, h, (x1, 40, x2, 78), 6, p["border_soft"], 1)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(box(39, 46, 66, 50), radius=2 * S, fill=rgba(p["text"], .85))
    d.rounded_rectangle(box(39, 54, 72, 57), radius=1.5 * S, fill=rgba(p["muted"], .7))
    d.rounded_rectangle(box(88, 66, 116, 73), radius=3 * S, fill=rgba(p["accent"]))
    d.rounded_rectangle(box(88, 46, 120, 50), radius=2 * S, fill=rgba(p["text"], .85))
    outline(img, w, h, (0, 0, w, h), 10, p["border"], 1)
    img.resize((w, h), Image.Resampling.LANCZOS).save(ASSETS / f"theme_preview_{theme_id}.png")


def build(theme_id):
    global target, T
    T = asset_tokens(theme_id)
    if theme_id == "aurora":
        target = ASSETS
        for item in ASSETS.iterdir():
            if item.is_file() and not item.name.startswith(("app_", "app.ico", "theme_preview_")):
                item.unlink()
    else:
        target = ASSETS / "themes" / theme_id
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
    build_buttons()
    build_cards()
    build_fields()
    build_switches()
    build_tabs_and_lists()
    build_sidebar()
    build_chips()
    hero = hero_strip(THEMES[theme_id]["motif"])
    build_hero_bits()
    build_toasts()
    build_particles(theme_id)
    colors = set(T["icon_colors"])
    if theme_id == "aurora":
        colors |= set(AURORA_ICON_COLORS)
    build_icons(sorted(colors))
    build_preview(theme_id, hero)
    return len(list(target.iterdir()))


if __name__ == "__main__":
    ASSETS.mkdir(parents=True, exist_ok=True)
    requested = [a for a in sys.argv[1:] if a in THEMES] or list(THEME_ORDER)
    build_shared()
    for theme_id in requested:
        print(f"{theme_id}: {build(theme_id)} files")
    print(f"assets in {ASSETS}")
