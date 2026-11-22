"""Canvas widgets of the «Aurora» interface: sliding navigation, glowing progress,
status pills, count-up numbers, action tiles, toasts and the dashboard hero.

Everything is plain Tk 8.6: pre-rendered PNGs from tools/build_theme.py plus
short frame animations driven by ``ui_assets.animate``.
"""
from __future__ import annotations

import math
import time
import tkinter as tk
from tkinter import ttk, font as tkfont

from ui_assets import (photo, icon_name, nine_slice, animate, stop_animation, lerp_color, asset_bytes,
                       ease_out_cubic, ease_out_quint, ease_in_out_cubic, ease_in_cubic)


def _alive(widget):
    try:
        return bool(widget.winfo_exists())
    except tk.TclError:
        return False


# Палитра активной темы — тот же словарь, что modern_ui.P (заполняется при применении темы).
PALETTE = {}


def overlay_key():
    """Прозрачный цвет для скруглённых всплывающих окон: почти цвет фона темы.

    Полупрозрачные края скругления смешиваются с этим цветом; если он близок к фону,
    на светлых темах по углам не появляется тёмная кайма. От фона он отличается на
    единицу в синем канале, чтобы не совпасть ни с одним настоящим пикселем.
    """
    bg = PALETTE.get('background', '#010203').lstrip('#')
    r, g, b = (int(bg[i:i + 2], 16) for i in (0, 2, 4))
    return '#%02X%02X%02X' % (r, g, b + 1 if b < 255 else b - 1)


def _viewable(widget):
    try:
        return bool(widget.winfo_viewable())
    except tk.TclError:
        return False


# ------------------------------------------------------------------ navigation
class NavItem:
    """Compatibility handle for one rail entry: badge, selected, draw() and geometry."""
    def __init__(self, rail, index):
        self._rail, self._index = rail, index
        self.selected = False

    @property
    def badge(self):
        return self._rail.items[self._index]['badge']

    @badge.setter
    def badge(self, value):
        self._rail.items[self._index]['badge'] = str(value or '')

    def draw(self, event=None):
        self._rail.refresh_badge(self._index)

    def winfo_rooty(self):
        return self._rail.winfo_rooty() + self._rail.items[self._index]['y']

    def winfo_rootx(self):
        return self._rail.winfo_rootx() + self._rail.X

    def winfo_height(self):
        return self._rail.ITEM_H

    def winfo_width(self):
        return self._rail.W

    def winfo_exists(self):
        return _alive(self._rail)

    def winfo_ismapped(self):
        return self._rail.winfo_ismapped()


class NavRail(tk.Canvas):
    """All sidebar entries on one canvas, so the selection pill can glide between them."""
    X, W, ITEM_H, STEP = 12, 198, 38, 39

    def __init__(self, parent, palette, fnt):
        super().__init__(parent, bg=palette['sidebar'], bd=0, highlightthickness=0,
                         takefocus=1, height=4, width=self.X * 2 + self.W)
        self.p, self.fnt = palette, fnt
        self.items = []
        self.active = -1
        self.hover = None
        self.focus_index = None
        self._y = 0
        self._dots_job = None
        self._pill = self.create_image(self.X, -60, anchor='nw', image=photo(self, 'nav_active.png'), state='hidden')
        self._ring = self.create_image(self.X, -60, anchor='nw', image=photo(self, 'nav_focus.png'), state='hidden')
        self.bind('<Motion>', self._motion)
        self.bind('<Leave>', lambda e: self._set_hover(None))
        self.bind('<ButtonRelease-1>', self._click)
        self.bind('<FocusIn>', lambda e: self._show_ring(self.active if self.focus_index is None else self.focus_index))
        self.bind('<FocusOut>', lambda e: self.itemconfigure(self._ring, state='hidden'))
        self.bind('<Up>', lambda e: self._move_focus(-1))
        self.bind('<Down>', lambda e: self._move_focus(1))
        self.bind('<Return>', lambda e: self._activate(self.focus_index))
        self.bind('<space>', lambda e: self._activate(self.focus_index))

    def add_section(self, title, gap=0):
        y = self._y + gap
        self.create_text(self.X + 15, y + 15, text=title, anchor='w', fill=self.p['muted_dim'],
                         font=self.fnt(8, 'bold'))
        self._y = y + 30
        self.configure(height=self._y)

    def add_item(self, label, glyph, command):
        y = self._y
        cy = y + self.ITEM_H // 2
        item = {
            'label': label, 'glyph': glyph, 'command': command, 'y': y, 'badge': '', 'shown': '',
            'level': 0.0, 'color': self.p['muted'], 'badge_ids': [],
            'hover': self.create_image(self.X, y, anchor='nw', image=photo(self, 'nav_hover_1.png'), state='hidden'),
            'icon': self.create_image(self.X + 14, cy, anchor='w', image=photo(self, icon_name(glyph, 20, self.p['muted']))),
            'text': self.create_text(self.X + 45, cy, text=label, anchor='w', fill=self.p['muted'], font=self.fnt(10)),
        }
        self.items.append(item)
        self._y += self.STEP
        self.configure(height=self._y)
        self.tag_raise(self._ring)
        return NavItem(self, len(self.items) - 1)

    # selection -----------------------------------------------------------
    def select(self, index):
        if index == self.active:
            return
        old, self.active = self.active, index
        y_new = self.items[index]['y']
        self.itemconfigure(self._pill, state='normal')
        if old < 0 or not _viewable(self):
            self.coords(self._pill, self.X, y_new)
        else:
            y_old = self.coords(self._pill)[1]
            animate(self, 'pill', 360, lambda t: self.coords(self._pill, self.X, y_old + (y_new - y_old) * t), ease_out_quint)
        for i in (old, index):
            if i >= 0:
                self._restyle(i)

    def _restyle(self, i):
        item = self.items[i]
        if i == self.active:
            target, glyph_color, weight = self.p['text'], self.p['accent'], 'bold'
            self.itemconfigure(item['hover'], state='hidden')
        else:
            target = lerp_color(self.p['muted'], self.p['text_soft'], item['level'])
            glyph_color = self.p['text_soft'] if item['level'] > .5 else self.p['muted']
            weight = 'normal'
        self.itemconfigure(item['icon'], image=photo(self, icon_name(item['glyph'], 20, glyph_color)))
        self.itemconfigure(item['text'], font=self.fnt(10, weight))
        start = item['color']

        def step(t):
            item['color'] = lerp_color(start, target, t)
            self.itemconfigure(item['text'], fill=item['color'])
        animate(self, f'color{i}', 260, step)

    # hover ---------------------------------------------------------------
    def _index_at(self, x, y):
        if not self.X <= x <= self.X + self.W:
            return None
        for i, item in enumerate(self.items):
            if item['y'] <= y < item['y'] + self.ITEM_H:
                return i
        return None

    def _motion(self, event):
        index = self._index_at(event.x, event.y)
        self.configure(cursor='hand2' if index is not None else '')
        self._set_hover(index)

    def _set_hover(self, index):
        if index == self.hover:
            return
        old, self.hover = self.hover, index
        for i in (old, index):
            if i is not None:
                self._fade_hover(i, 1.0 if i == index else 0.0)

    def _fade_hover(self, i, target):
        item = self.items[i]
        start = item['level']
        if i != self.active:
            color = self.p['text_soft'] if target else self.p['muted']
            self.itemconfigure(item['icon'], image=photo(self, icon_name(item['glyph'], 20, color)))

        def step(t):
            item['level'] = start + (target - start) * t
            k = round(item['level'] * 6)
            if k <= 0 or i == self.active:
                self.itemconfigure(item['hover'], state='hidden')
            else:
                self.itemconfigure(item['hover'], image=photo(self, f'nav_hover_{k}.png'), state='normal')
            if i != self.active:
                item['color'] = lerp_color(self.p['muted'], self.p['text_soft'], item['level'])
                self.itemconfigure(item['text'], fill=item['color'])
        stop_animation(self, f'color{i}') if i != self.active else None
        animate(self, f'hover{i}', 150 if target else 280, step)

    def _click(self, event):
        index = self._index_at(event.x, event.y)
        if index is not None:
            self._activate(index)

    def _activate(self, index):
        if index is not None and 0 <= index < len(self.items):
            self.items[index]['command']()
        return 'break'

    # keyboard focus ring --------------------------------------------------
    def _show_ring(self, index):
        if index is None or index < 0 or not self.items:
            index = 0
        self.focus_index = index
        self.coords(self._ring, self.X, self.items[index]['y'])
        self.itemconfigure(self._ring, state='normal')

    def _move_focus(self, delta):
        start = self.active if self.focus_index is None else self.focus_index
        self._show_ring((start + delta) % len(self.items))
        return 'break'

    # badges --------------------------------------------------------------
    def refresh_badge(self, i):
        item = self.items[i]
        badge = item['badge']
        if badge == item['shown']:
            return
        item['shown'] = badge
        for oid in item['badge_ids']:
            self.delete(oid)
        item['badge_ids'] = []
        cy = item['y'] + self.ITEM_H / 2
        right = self.X + self.W - 10
        if badge == '…':
            item['badge_ids'] = [self.create_oval(right - 21 + k * 8, cy - 2.5, right - 16 + k * 8, cy + 2.5,
                                                  fill=self.p['accent'], outline='') for k in range(3)]
            if self._dots_job is None:
                self._dots_tick()
        elif badge:
            image = self.create_image(right, cy, anchor='e', image=photo(self, 'badge.png'))
            text = self.create_text(right - 15, cy, text=badge, fill=self.p['accent'], font=self.fnt(8, 'bold'))
            item['badge_ids'] = [image, text]

    def _dots_tick(self):
        self._dots_job = None
        busy = [item for item in self.items if item['shown'] == '…']
        if not busy or not _alive(self):
            return
        phase = time.perf_counter() * 4.2
        for item in busy:
            for k, oid in enumerate(item['badge_ids']):
                level = (math.sin(phase - k * 1.1) + 1) / 2
                self.itemconfigure(oid, fill=lerp_color(self.p.get('pill_dim', '#3A3363'), self.p['accent'], level))
        self._dots_job = self._root().after(50, self._dots_tick)


# --------------------------------------------------------------------- sidebar
class SearchBox(tk.Canvas):
    """Sidebar search trigger that looks like an input with a keyboard hint."""
    def __init__(self, parent, palette, fnt, command, width=198, height=38):
        super().__init__(parent, bg=palette['sidebar'], width=width, height=height, bd=0,
                         highlightthickness=0, takefocus=1, cursor='hand2')
        self.p, self.command = palette, command
        self._bg = self.create_image(0, 0, anchor='nw', image=nine_slice(self, 'search.png', width, height, 12))
        self._icon = self.create_image(13, height / 2, anchor='w', image=photo(self, icon_name('search', 17, palette['muted'])))
        self._text = self.create_text(38, height / 2, anchor='w', text='Поиск', fill=palette['muted'], font=fnt(10))
        self.create_image(width - 9, height / 2, anchor='e', image=nine_slice(self, 'kbd.png', 50, 20, 6))
        self.create_text(width - 34, height / 2, text='Ctrl K', fill=palette['muted'], font=fnt(8))
        self._size = (width, height)
        for sequence, hover in (('<Enter>', True), ('<Leave>', False), ('<FocusIn>', True), ('<FocusOut>', False)):
            self.bind(sequence, lambda e, h=hover: self._hover(h))
        self.bind('<ButtonRelease-1>', lambda e: self.command())
        self.bind('<Return>', lambda e: self.command())

    def _hover(self, on):
        w, h = self._size
        self.itemconfigure(self._bg, image=nine_slice(self, 'search_hover.png' if on else 'search.png', w, h, 12))
        color = self.p['text_soft'] if on else self.p['muted']
        self.itemconfigure(self._icon, image=photo(self, icon_name('search', 17, color)))
        start = self.itemcget(self._text, 'fill')
        animate(self, 'text', 200, lambda t: self.itemconfigure(self._text, fill=lerp_color(start, color, t)))


class StatusPill(tk.Canvas):
    """Drop-in for a status ttk.Label: glowing dot, smooth colour change, live pulse."""
    DOTS = {'a898ff', '74e0b5', '6fd6ff', 'f2c886', 'f59aae', '8c8aa3'}

    def __init__(self, parent, bg, fnt, text='', foreground='#8C8AA3', pulse=('#A898FF', '#74E0B5'), size=9):
        super().__init__(parent, bg=bg, height=22, width=20, bd=0, highlightthickness=0)
        self._bgcolor = bg
        self._font = tkfont.Font(font=fnt(size))
        self._pulse = tuple(pulse)  # кортеж цветов: при смене темы перекрашивается вместе с виджетом
        self._raw, self._color, self._text_color = '', foreground, foreground
        self._job = None
        self._ring = self.create_oval(0, 0, 0, 0, outline='', width=1.4)
        self._dot = self.create_image(10, 11)
        self._label = self.create_text(22, 11, anchor='w', font=self._font, fill=foreground)
        self._apply(text, foreground, animated=False)

    def configure(self, cnf=None, **kw):
        if isinstance(cnf, dict):
            kw = {**cnf, **kw}
        text = kw.pop('text', None)
        color = kw.pop('foreground', None) or kw.pop('fg', None)
        kw.pop('style', None)
        if text is not None or color is not None:
            self._apply(self._raw if text is None else text, color or self._color)
        if kw:
            return super().configure(**kw)
    config = configure

    def cget(self, key):
        if key == 'text':
            return self._raw
        if key in ('foreground', 'fg'):
            return self._color
        return super().cget(key)

    def _apply(self, raw, color, animated=True):
        self._raw, self._color = str(raw), color
        clean = self._raw.lstrip('●').strip()
        self.itemconfigure(self._label, text=clean)
        hexcode = color.lstrip('#').lower()
        if asset_bytes(f'dot_{hexcode}.png') is not None:
            self.itemconfigure(self._dot, image=photo(self, f'dot_{hexcode}.png'), state='normal')
        else:
            self.itemconfigure(self._dot, state='hidden')
        super().configure(width=24 + self._font.measure(clean) + 4)
        start, target = self._text_color, color
        if animated and _viewable(self):
            def step(t):
                self._text_color = lerp_color(start, target, t)
                self.itemconfigure(self._label, fill=self._text_color)
            animate(self, 'color', 320, step)
        else:
            self._text_color = target
            self.itemconfigure(self._label, fill=target)
        if color.upper() in {c.upper() for c in self._pulse}:
            if self._job is None:
                self._t0 = time.perf_counter()
                self._tick()
        else:
            self.itemconfigure(self._ring, outline='')

    def _tick(self):
        self._job = None
        if not _alive(self) or self._color.upper() not in {c.upper() for c in self._pulse}:
            return
        if not _viewable(self):
            self._job = self._root().after(450, self._tick)
            return
        phase = ((time.perf_counter() - self._t0) % 1.8) / 1.8
        r = 4 + 6.5 * ease_out_cubic(min(1, phase * 1.25))
        self.coords(self._ring, 10 - r, 11 - r, 10 + r, 11 + r)
        self.itemconfigure(self._ring, outline=lerp_color(self._color, self._bgcolor, min(1, phase * 1.25)))
        self._job = self._root().after(33, self._tick)


# ----------------------------------------------------------------- progress
class GlowProgress(tk.Canvas):
    """ttk.Progressbar-compatible bar: gradient fill, glowing head, eased value changes."""
    STOPS = ('#7B5CFF', '#A898FF', '#6FD6FF')
    SEGMENTS = 36

    def __init__(self, parent, bg, track=None, variable=None, maximum=100, mode='determinate',
                 value=0, height=12, **_ignored):
        super().__init__(parent, bg=bg, height=height, width=60, bd=0, highlightthickness=0)
        # Цвета берутся из палитры активной темы (ModernUI заполняет PALETTE до построения окна).
        self.STOPS = (PALETTE.get('accent_deep', self.STOPS[0]), PALETTE.get('accent', self.STOPS[1]),
                      PALETTE.get('cyan', self.STOPS[2]))
        track = track or PALETTE.get('track', '#232335')
        self._own_track = track == PALETTE.get('track')
        self._max = float(maximum) or 100.0
        self._value = self._shown = float(value)
        self._mode = mode
        self._running = False
        self._job = None
        self._var = self._trace = None
        cy = height / 2
        self._cy = cy
        self._track = self.create_line(0, cy, 0, cy, fill=track, width=4, capstyle='round')
        self._segs = []
        for k in range(self.SEGMENTS):
            t = k / (self.SEGMENTS - 1)
            color = lerp_color(self.STOPS[0], self.STOPS[1], t * 2) if t < .5 else lerp_color(self.STOPS[1], self.STOPS[2], t * 2 - 1)
            self._segs.append(self.create_line(0, cy, 0, cy, fill=color, width=4,
                                               capstyle='round' if k == 0 else 'butt', state='hidden'))
        self._head = self.create_image(-40, cy, image=photo(self, 'progress_head.png'), state='hidden')
        self.bind('<Configure>', lambda e: self._redraw())
        self.bind('<Destroy>', self._cleanup, add='+')
        if variable is not None:
            self._bind_variable(variable)

    def restyle(self):
        """Смена темы: градиент полосы считается из палитры заново."""
        self.STOPS = (PALETTE.get('accent_deep', self.STOPS[0]), PALETTE.get('accent', self.STOPS[1]),
                      PALETTE.get('cyan', self.STOPS[2]))
        if self._own_track:
            self.itemconfigure(self._track, fill=PALETTE.get('track', '#232335'))
        for k, seg in enumerate(self._segs):
            t = k / (self.SEGMENTS - 1)
            color = lerp_color(self.STOPS[0], self.STOPS[1], t * 2) if t < .5 else lerp_color(self.STOPS[1], self.STOPS[2], t * 2 - 1)
            self.itemconfigure(seg, fill=color)

    def _bind_variable(self, variable):
        if self._var is not None and self._trace:
            try:
                self._var.trace_remove('write', self._trace)
            except (tk.TclError, ValueError):
                pass
        self._var = variable
        self._trace = variable.trace_add('write', lambda *a: self._from_variable())
        self._from_variable(animated=False)

    def _from_variable(self, animated=True):
        try:
            self._set_value(float(self._var.get()), animated)
        except (tk.TclError, ValueError):
            pass

    def _cleanup(self, event):
        if event.widget is self and self._var is not None and self._trace:
            try:
                self._var.trace_remove('write', self._trace)
            except (tk.TclError, ValueError):
                pass
            self._trace = None

    def configure(self, cnf=None, **kw):
        if isinstance(cnf, dict):
            kw = {**cnf, **kw}
        for key in ('style', 'orient', 'length', 'phase'):
            kw.pop(key, None)
        if 'maximum' in kw:
            self._max = float(kw.pop('maximum')) or 100.0
        if 'variable' in kw:
            self._bind_variable(kw.pop('variable'))
        if 'mode' in kw:
            self._mode = kw.pop('mode')
            if self._mode != 'indeterminate':
                self._running = False
        if 'value' in kw:
            self._set_value(float(kw.pop('value') or 0))
        else:
            self._redraw()
        if kw:
            return super().configure(**kw)
    config = configure

    def cget(self, key):
        if key == 'value':
            return self._value
        if key == 'mode':
            return self._mode
        if key == 'maximum':
            return self._max
        return super().cget(key)

    def start(self, interval=None):
        self._running = True
        self._t0 = time.perf_counter()
        if self._job is None:
            self._tick()

    def stop(self):
        self._running = False
        if self._job is not None:
            self._root().after_cancel(self._job)
            self._job = None
        self._redraw()

    def step(self, amount=1.0):
        self._set_value((self._value + amount) % self._max)

    def _set_value(self, value, animated=True):
        value = max(0.0, min(self._max, value))
        self._value = value
        start = self._shown
        if not animated or not _viewable(self) or abs(value - start) < 1e-6:
            self._shown = value
            self._redraw()
            return

        def tween(t):
            self._shown = start + (value - start) * t
            self._redraw()
        animate(self, 'value', 520, tween, ease_out_cubic)

    def _tick(self):
        self._job = None
        if not self._running or not _alive(self):
            return
        self._redraw()
        self._job = self._root().after(16, self._tick)

    def _redraw(self):
        if not _alive(self):
            return
        w = max(10, self.winfo_width())
        x0, x1, cy = 3, w - 3, self._cy
        self.coords(self._track, x0, cy, x1, cy)
        if self._mode == 'indeterminate' and self._running:
            phase = ((time.perf_counter() - self._t0) % 1.5) / 1.5
            span = (x1 - x0) * .32
            centre = x0 - span + (x1 - x0 + 2 * span) * ease_in_out_cubic(phase)
            a, b = max(x0, centre - span / 2), min(x1, centre + span / 2)
            head = b < x1
        else:
            frac = self._shown / self._max if self._max else 0
            a, b = x0, x0 + (x1 - x0) * frac
            head = 0.002 < frac < .998
        if b - a < 2:
            for seg in self._segs:
                self.itemconfigure(seg, state='hidden')
            self.itemconfigure(self._head, state='hidden')
            return
        step = (b - a) / self.SEGMENTS
        for k, seg in enumerate(self._segs):
            sa = a + step * k + (2 if k == 0 else 0)
            sb = a + step * (k + 1) + (0 if k == self.SEGMENTS - 1 else .8)
            self.coords(seg, sa, cy, max(sa + .1, sb), cy)
            self.itemconfigure(seg, state='normal')
        self.coords(self._head, b, cy)
        self.itemconfigure(self._head, state='normal' if head else 'hidden')


class CountLabel(ttk.Label):
    """Shows a numeric StringVar and glides to each new value (the variable keeps the exact text)."""
    def __init__(self, master, source, duration=750, **kw):
        kw.pop('textvariable', None)
        super().__init__(master, text=source.get(), **kw)
        self._source, self._duration = source, duration
        self._display = self._parse(source.get())
        self._trace = source.trace_add('write', lambda *a: self._changed())
        self.bind('<Destroy>', self._cleanup, add='+')

    @staticmethod
    def _parse(text):
        digits = str(text).replace(' ', '').replace(' ', '').replace(' ', '')
        return int(digits) if digits.isdigit() else None

    @staticmethod
    def _format(value):
        return f'{value:,}'.replace(',', ' ')

    def _changed(self, force_from=None):
        if not _alive(self):
            return
        raw = self._source.get()
        target = self._parse(raw)
        start = self._display if force_from is None else force_from
        if target is None or start is None or target == start or not _viewable(self):
            stop_animation(self, 'count')
            self._display = target
            self.configure(text=raw)
            return

        def step(t):
            self._display = round(start + (target - start) * t)
            self.configure(text=self._format(self._display))
        animate(self, 'count', self._duration, step, ease_out_cubic, done=lambda: self.configure(text=raw))

    def replay(self):
        """Count up from zero, used when the window first appears."""
        self._changed(force_from=0)

    def _cleanup(self, event):
        if event.widget is self and self._trace:
            try:
                self._source.trace_remove('write', self._trace)
            except (tk.TclError, ValueError):
                pass
            self._trace = None


class CanvasText:
    """Lets builders keep calling ``label.configure(text=...)`` on a canvas text item."""
    def __init__(self, canvas, item):
        self.canvas, self.item = canvas, item

    def configure(self, cnf=None, **kw):
        if isinstance(cnf, dict):
            kw = {**cnf, **kw}
        if 'text' in kw:
            self.canvas.itemconfigure(self.item, text=kw['text'])
    config = configure


# --------------------------------------------------------------- action tile
class ActionTile(tk.Canvas):
    """Dashboard quick action: icon chip, title, hint and an arrow that slides on hover."""
    H = 60

    def __init__(self, parent, palette, fnt, title, desc, glyph, color, command):
        super().__init__(parent, bg=palette['surface'], height=self.H, width=220, bd=0,
                         highlightthickness=0, takefocus=1, cursor='hand2')
        self.p, self.command, self._color = palette, command, color
        self._level = 0.0
        self._hovered = False
        self._bg = self.create_image(0, 0, anchor='nw')
        cy = self.H / 2
        self.create_image(13, cy, anchor='w', image=photo(self, f'chip34_{color.lstrip("#").lower()}.png'))
        self.create_image(30, cy, image=photo(self, icon_name(glyph, 17, color)))
        self._title = self.create_text(60, cy - 9, anchor='w', text=title, fill=palette['text_soft'], font=fnt(10, 'bold'))
        self._desc = self.create_text(60, cy + 10, anchor='w', text=desc, fill=palette['muted'], font=fnt(8))
        self._arrow = self.create_image(200, cy, anchor='e', image=photo(self, icon_name('arrow', 17, palette['muted'])))
        self.bind('<Configure>', lambda e: self._layout())
        self.bind('<Enter>', lambda e: self._hover(True))
        self.bind('<Leave>', lambda e: self._hover(False))
        self.bind('<FocusIn>', lambda e: self._hover(True))
        self.bind('<FocusOut>', lambda e: self._hover(False))
        self.bind('<ButtonRelease-1>', self._release)
        self.bind('<Return>', lambda e: self.command())
        self.bind('<space>', lambda e: self.command())

    def _layout(self):
        w = max(80, self.winfo_width())
        self.itemconfigure(self._bg, image=nine_slice(self, 'tile_hover.png' if self._hovered else 'tile.png', w, self.H, 12))
        self.coords(self._arrow, w - 16 + 5 * self._level, self.H / 2)

    def _hover(self, on):
        self._hovered = on
        self._layout()
        self.itemconfigure(self._arrow, image=photo(self, icon_name('arrow', 17, self.p['accent'] if on else self.p['muted'])))
        start, target = self._level, 1.0 if on else 0.0
        w = max(80, self.winfo_width())

        def step(t):
            self._level = start + (target - start) * t
            self.coords(self._arrow, w - 16 + 5 * self._level, self.H / 2)
            self.itemconfigure(self._title, fill=lerp_color(self.p['text_soft'], self.p['text'], self._level))
        animate(self, 'hover', 200 if on else 320, step, ease_out_cubic)

    def _release(self, event):
        if 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height():
            self.command()


# --------------------------------------------------------------------- toast
class Toast:
    """Rounded notification that slides up and fades in above the window corner."""
    KEY = '#010203'

    def __init__(self, root, palette, fnt):
        self.root, self.p, self.fnt = root, palette, fnt
        self.win = None

    def show(self, text, kind='ok', duration=2700):
        self.close(immediate=True)
        win = self.win = tk.Toplevel(self.root)
        win.withdraw()
        win.overrideredirect(True)
        try:
            key = overlay_key()
            win.attributes('-transparentcolor', key)
        except tk.TclError:
            key = self.p['background']
        win.configure(bg=key)
        font = tkfont.Font(font=self.fnt(10))
        text = str(text)
        width = min(560, max(270, 70 + font.measure(text) + 22))
        height = 56
        canvas = tk.Canvas(win, width=width, height=height, bg=key, bd=0, highlightthickness=0)
        canvas.pack()
        canvas.create_image(0, 0, anchor='nw', image=nine_slice(canvas, 'toast.png', width, height, 16))
        canvas.create_image(16, height / 2, anchor='w', image=photo(canvas, f'toast_{kind}.png'))
        canvas.create_text(54, height / 2 - 1, anchor='w', text=text, fill=self.p['text'], font=font,
                           width=width - 70)
        color = {'ok': self.p['green'], 'error': self.p['red']}.get(kind, self.p['accent'])
        bar = canvas.create_line(18, height - 4, width - 18, height - 4, fill=lerp_color(color, self.p.get('toast_track', '#1E1D2E'), .35),
                                 width=2, capstyle='round')
        self.root.update_idletasks()
        x = self.root.winfo_rootx() + self.root.winfo_width() - width - 28
        y = self.root.winfo_rooty() + self.root.winfo_height() - height - 44
        win.geometry(f'{width}x{height}+{x}+{y + 18}')
        try:
            win.attributes('-alpha', 0.0)
        except tk.TclError:
            pass
        try:
            win.transient(self.root)
            win.attributes('-topmost', True)
        except tk.TclError:
            pass
        win.deiconify()
        win.lift()

        def slide(t):
            win.geometry(f'+{x}+{round(y + 18 * (1 - t))}')
            try:
                win.attributes('-alpha', min(1.0, t * 1.2))
            except tk.TclError:
                pass
        animate(win, 'in', 300, slide, ease_out_cubic)
        animate(canvas, 'bar', duration, lambda t: canvas.coords(bar, 18, height - 4, 18 + (width - 36) * (1 - t), height - 4),
                lambda t: t)

    def close(self, immediate=False):
        win, self.win = self.win, None
        if win is None or not _alive(win):
            return
        if immediate:
            win.destroy()
            return
        try:
            x, y = win.winfo_x(), win.winfo_y()
        except tk.TclError:
            win.destroy()
            return

        def fade(t):
            win.geometry(f'+{x}+{round(y + 12 * t)}')
            try:
                win.attributes('-alpha', 1 - t)
            except tk.TclError:
                pass
        animate(win, 'out', 220, fade, ease_in_cubic, done=win.destroy)


# ---------------------------------------------------------------- dashboard
class DashboardHero(tk.Canvas):
    """Gradient hero with orbiting dots and a soft radar pulse behind the glass core."""
    LEFT, RIGHT, CENTER_FROM_RIGHT = 28, 560, 190

    def __init__(self, parent, palette, fnt, eyebrow, title, motif='aurora'):
        self.H = photo(parent, 'hero_left.png').height()
        super().__init__(parent, bg=palette['background'], height=self.H, width=700, bd=0, highlightthickness=0)
        self.p = palette
        # Орбиты и пульс — мотив «Авроры». У остальных тем сцена нарисована на картинке
        # героя, а движение даёт атмосфера (app/atmosphere.py).
        self.motif = motif
        self._bg = self.create_image(0, 0, anchor='nw')
        self._pulse = self.create_image(-400, 0, image=photo(self, 'pulse_0.png'), state='hidden')
        self._orbit = []
        if motif == 'aurora':
            for color, radius, speed, phase in ((palette['accent'], 70, .55, .6), (palette['green'], 46, -.85, 2.4),
                                                (palette['cyan'], 96, .32, 4.1)):
                oid = self.create_image(-50, 0, image=photo(self, f'dot_{color.lstrip("#").lower()}.png'))
                self._orbit.append((oid, radius, speed, phase))
        self._core = self.create_image(-400, 0, image=photo(self, 'hero_core.png'),
                                       state='normal' if motif == 'aurora' else 'hidden')
        self._eyebrow = self.create_text(32, 34, anchor='w', text=eyebrow, fill=palette['accent'], font=fnt(8, 'bold'))
        self._title = self.create_text(32, 47, anchor='nw', text=title, fill=palette['text'], font=fnt(21, 'bold'))
        self._row_label = self.create_text(32, 150, anchor='w', text='Режим работы', fill=palette['muted'], font=fnt(10))
        self._row_x = 32 + tkfont.Font(font=fnt(10)).measure('Режим работы') + 14
        self._hint = self.create_text(32, 184, anchor='w', text='', fill=palette['muted'], font=fnt(9))
        self.hint = CanvasText(self, self._hint)
        self._width = 0
        self._compose_job = None
        self._job = None
        self._t0 = time.perf_counter()
        self.bind('<Configure>', self._schedule_compose)
        self._tick()

    def place_control(self, widget):
        self.create_window(self._row_x, 150, anchor='w', window=widget)

    def set_motif(self, motif):
        """Смена темы: картинка героя собирается заново, орбиты есть только у «Авроры»."""
        self.__dict__.get('_images', {}).clear()
        self._width = 0
        self._compose()
        if motif == self.motif:
            return
        self.motif = motif
        for oid, *_ in self._orbit:
            self.delete(oid)
        self._orbit = []
        if motif == 'aurora':
            p = self.p
            for color, radius, speed, phase in ((p['accent'], 70, .55, .6), (p['green'], 46, -.85, 2.4), (p['cyan'], 96, .32, 4.1)):
                oid = self.create_image(-50, 0, image=photo(self, f'dot_{color.lstrip("#").lower()}.png'))
                self.tag_lower(oid, self._eyebrow)
                self._orbit.append((oid, radius, speed, phase))
            self.itemconfigure(self._core, state='normal')
            if self._job is None:
                self._tick()
        else:
            self.itemconfigure(self._core, state='hidden')
            self.itemconfigure(self._pulse, state='hidden')

    def _schedule_compose(self, event=None):
        if self._width == 0:
            self._compose()
        elif self._compose_job is None:
            self._compose_job = self._root().after(40, self._compose)

    def _compose(self):
        self._compose_job = None
        w = max(self.LEFT + self.RIGHT, self.winfo_width())
        if w == self._width:
            return
        self._width = w
        cache = self.__dict__.setdefault('_images', {})
        if w not in cache:
            cache.clear()
            img = tk.PhotoImage(master=self, width=w, height=self.H)
            left, mid, right = (photo(self, f'hero_{n}.png') for n in ('left', 'mid', 'right'))
            img.tk.call(img, 'copy', left, '-to', 0, 0, '-compositingrule', 'set')
            img.tk.call(img, 'copy', mid, '-to', self.LEFT, 0, w - self.RIGHT, self.H, '-compositingrule', 'set')
            img.tk.call(img, 'copy', right, '-to', w - self.RIGHT, 0, '-compositingrule', 'set')
            cache[w] = img
        self.itemconfigure(self._bg, image=cache[w])
        self._center = (w - self.CENTER_FROM_RIGHT, self.H / 2)
        self.coords(self._core, *self._center)
        self.coords(self._pulse, *self._center)

    def _tick(self):
        self._job = None
        if not _alive(self):
            return
        if not _viewable(self) or not self._width:
            self._job = self._root().after(400, self._tick)
            return
        if self.motif != 'aurora':
            return  # статичная сцена: таймер героя не нужен
        t = time.perf_counter() - self._t0
        cx, cy = self._center
        for oid, radius, speed, phase in self._orbit:
            angle = phase + speed * t
            self.coords(oid, cx + radius * math.cos(angle), cy + radius * math.sin(angle))
        cycle = (t % 3.0) / 3.0
        if cycle < .55:
            frame = min(17, int(cycle / .55 * 18))
            self.itemconfigure(self._pulse, image=photo(self, f'pulse_{frame}.png'), state='normal')
        else:
            self.itemconfigure(self._pulse, state='hidden')
        self._job = self._root().after(33, self._tick)

    def intro(self):
        """Fade the headline in from the hero surface."""
        base = self.p.get('hero_text_base', '#1A1928')
        targets = ((self._eyebrow, self.p['accent'], 0), (self._title, self.p['text'], 90),
                   (self._row_label, self.p['muted'], 170), (self._hint, self.p['muted'], 230))
        for item, color, delay in targets:
            x, y = self.coords(item)
            self.itemconfigure(item, fill=base)
            self.coords(item, x + 14, y)

            def run(item=item, color=color, x=x, y=y):
                def step(t):
                    self.coords(item, x + 14 * (1 - t), y)
                    self.itemconfigure(item, fill=lerp_color(base, color, t))
                animate(self, f'intro{item}', 620, step, ease_out_quint)
            self._root().after(delay, run)


# -------------------------------------------------------------- behaviours
def smooth_scroll(canvas, notches, pixels=70):
    """Glide a scrollable canvas instead of jumping by whole units."""
    region = canvas.bbox('all')
    if not region:
        return
    total = region[3] - min(0, region[1])
    view = canvas.winfo_height()
    if total <= view:
        return
    top = canvas.yview()[0] * total
    pending = canvas.__dict__.get('_ares_scroll_target')
    target = max(0, min(total - view, (pending if pending is not None else top) + notches * pixels))
    canvas._ares_scroll_target = target
    start = top

    def step(t):
        canvas.yview_moveto((start + (target - start) * t) / total)
    animate(canvas, 'scroll', 280, step, ease_out_cubic,
            done=lambda: setattr(canvas, '_ares_scroll_target', None))


def tree_hover(tree, background):
    """Highlight the Treeview row under the pointer (selected rows keep their colour)."""
    tree.tag_configure('ares_hover', background=background)
    state = {'iid': None}

    def show(iid):
        old = state['iid']
        if old == iid:
            return
        state['iid'] = None
        try:
            if old and tree.exists(old):
                tree.tk.call(tree, 'tag', 'remove', 'ares_hover', old)
            if iid and tree.exists(iid) and iid not in tree.selection():
                tree.tk.call(tree, 'tag', 'add', 'ares_hover', iid)
                state['iid'] = iid
        except tk.TclError:
            pass
    tree.bind('<Motion>', lambda e: show(tree.identify_row(e.y)), add='+')
    tree.bind('<Leave>', lambda e: show(None), add='+')
    tree.bind('<<TreeviewSelect>>', lambda e: show(None), add='+')


class SegmentedTabs(tk.Canvas):
    """Tab strip for a ttk.Notebook: a rounded track where the selected pill glides to the chosen tab."""
    H, GAP, PAD_X, INSET = 38, 2, 18, 4

    def __init__(self, parent, notebook, palette, fnt, bg):
        super().__init__(parent, height=self.H, bg=bg, bd=0, highlightthickness=0)
        self.nb, self.p = notebook, palette
        self.font = tkfont.Font(font=fnt(9, "bold"))
        self.tabs = []
        x = self.INSET
        for tab_id in notebook.tabs():
            text = str(notebook.tab(tab_id, "text"))
            w = self.font.measure(text) + self.PAD_X * 2
            self.tabs.append({"id": tab_id, "text": text, "x": x, "w": w, "level": 0.0})
            x += w + self.GAP
        self.total = x - self.GAP + self.INSET
        inner = self.H - 2 * self.INSET
        self.create_image(0, 0, anchor="nw", image=nine_slice(self, "search.png", self.total, self.H, 12))
        self._pill = self.create_image(-200, self.INSET, anchor="nw")
        for tab in self.tabs:
            tab["item"] = self.create_text(tab["x"] + tab["w"] / 2, self.H / 2, text=tab["text"], font=self.font,
                                           fill=palette["muted"])
        self.configure(width=self.total)
        self._pill_x, self._pill_w = None, None
        self._inner = inner
        self.current = None
        self.hover = None
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda e: self._set_hover(None))
        self.bind("<ButtonRelease-1>", self._click)
        notebook.bind("<<NotebookTabChanged>>", lambda e: self.sync(), add="+")
        self.sync(animated=False)

    def _tab_at(self, x, y):
        for tab in self.tabs:
            if tab["x"] <= x < tab["x"] + tab["w"] and 0 <= y < self.H:
                return tab
        return None

    def _motion(self, event):
        tab = self._tab_at(event.x, event.y)
        self.configure(cursor="hand2" if tab else "")
        self._set_hover(tab)

    def _set_hover(self, tab):
        if tab is self.hover:
            return
        old, self.hover = self.hover, tab
        for t in (old, tab):
            if t is not None and t is not self.current:
                self._fade(t, 1.0 if t is tab else 0.0)

    def _fade(self, tab, target):
        start = tab["level"]

        def step(t):
            tab["level"] = start + (target - start) * t
            if tab is not self.current:
                self.itemconfigure(tab["item"], fill=lerp_color(self.p["muted"], self.p["text_soft"], tab["level"]))
        animate(self, f"fade{tab['x']}", 160 if target else 260, step)

    def _click(self, event):
        tab = self._tab_at(event.x, event.y)
        if tab is not None:
            self.nb.select(tab["id"])

    def _draw_pill(self, x, w):
        w = max(24, int(round(w / 2.0) * 2))
        self._pill_x, self._pill_w = x, w
        self.itemconfigure(self._pill, image=nine_slice(self, "tab_selected.png", w, self._inner, 10))
        self.coords(self._pill, x, self.INSET)

    def sync(self, animated=True):
        try:
            selected = str(self.nb.select())
        except tk.TclError:
            return
        tab = next((t for t in self.tabs if str(t["id"]) == selected), None)
        if tab is None or tab is self.current:
            return
        old, self.current = self.current, tab
        if old is not None:
            animate(self, f"color{old['x']}", 240,
                    lambda t, o=old: self.itemconfigure(o["item"], fill=lerp_color(self.p["text"], self.p["muted"], t)))
            old["level"] = 0.0
        animate(self, f"color{tab['x']}", 240,
                lambda t: self.itemconfigure(tab["item"], fill=lerp_color(self.p["muted"], self.p["text"], t)))
        x1, w1 = tab["x"], tab["w"]
        if not animated or self._pill_x is None or not _viewable(self):
            self._draw_pill(x1, w1)
            return
        x0, w0 = self._pill_x, self._pill_w
        animate(self, "pill", 320, lambda t: self._draw_pill(x0 + (x1 - x0) * t, w0 + (w1 - w0) * t), ease_out_quint)

class SegmentedControl(tk.Canvas):
    """A small segmented switch (e.g. «Удаление | Защита»): the selected pill glides between options."""
    H, GAP, PAD_X, INSET = 34, 2, 14, 4

    def __init__(self, parent, palette, fnt, options, command=None, current=None, bg=None):
        super().__init__(parent, height=self.H, bg=bg or palette["surface"], bd=0, highlightthickness=0, takefocus=1)
        self.p, self.command = palette, command
        self.font = tkfont.Font(font=fnt(9, "bold"))
        self.items = []
        x = self.INSET
        for key, label in options:
            w = self.font.measure(label) + self.PAD_X * 2
            self.items.append({"key": key, "label": label, "x": x, "w": w})
            x += w + self.GAP
        self.total = x - self.GAP + self.INSET
        self.create_image(0, 0, anchor="nw", image=nine_slice(self, "search.png", self.total, self.H, 12))
        self._pill = self.create_image(-200, self.INSET, anchor="nw")
        self._ring = self.create_rectangle(0, 0, 0, 0, outline="", width=1)
        for item in self.items:
            item["text"] = self.create_text(item["x"] + item["w"] / 2, self.H / 2, text=item["label"],
                                            font=self.font, fill=palette["muted"])
        self.configure(width=self.total, cursor="hand2")
        self.current = None
        self._pill_x = self._pill_w = None
        self.bind("<ButtonRelease-1>", self._click)
        self.bind("<Left>", lambda e: self._step(-1))
        self.bind("<Right>", lambda e: self._step(1))
        self.bind("<FocusIn>", lambda e: self._focus(True))
        self.bind("<FocusOut>", lambda e: self._focus(False))
        self.select(current or self.items[0]["key"], notify=False, animated=False)

    def _focus(self, on):
        """Keyboard focus is shown as a thin accent ring around the track."""
        if on:
            self.coords(self._ring, 1, 1, self.total - 2, self.H - 2)
            self.itemconfigure(self._ring, outline=self.p["accent"])
        else:
            self.itemconfigure(self._ring, outline="")

    def _step(self, delta):
        keys = [item["key"] for item in self.items]
        index = keys.index(self.current) if self.current in keys else 0
        self.select(keys[(index + delta) % len(keys)])
        return "break"

    def _click(self, event):
        for item in self.items:
            if item["x"] <= event.x < item["x"] + item["w"]:
                self.select(item["key"])
                return

    def _draw_pill(self, x, w):
        w = max(24, int(round(w / 2.0) * 2))
        self._pill_x, self._pill_w = x, w
        self.itemconfigure(self._pill, image=nine_slice(self, "tab_selected.png", w, self.H - 2 * self.INSET, 10))
        self.coords(self._pill, x, self.INSET)

    def select(self, key, notify=True, animated=True):
        item = next((i for i in self.items if i["key"] == key), None)
        if item is None or key == self.current:
            return
        old = next((i for i in self.items if i["key"] == self.current), None)
        self.current = key
        for other in self.items:
            target = self.p["text"] if other is item else self.p["muted"]
            start = self.itemcget(other["text"], "fill")
            animate(self, f"c{other['x']}", 220 if animated else 0,
                    lambda t, o=other, s=start, g=target: self.itemconfigure(o["text"], fill=lerp_color(s, g, t)))
        x1, w1 = item["x"], item["w"]
        if not animated or old is None or self._pill_x is None or not _viewable(self):
            self._draw_pill(x1, w1)
        else:
            x0, w0 = self._pill_x, self._pill_w
            animate(self, "pill", 300, lambda t: self._draw_pill(x0 + (x1 - x0) * t, w0 + (w1 - w0) * t), ease_out_quint)
        if notify and self.command:
            self.command(key)