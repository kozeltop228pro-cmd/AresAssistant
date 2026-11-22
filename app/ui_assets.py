"""Native Tk image cache, easing helpers and animated ttk controls (theme-aware).

Картинки ищутся сначала в каталоге активной темы (assets/themes/<id>/), затем в
базовом assets/ (там лежит «Аврора» и общие файлы). Иконки хранятся листами
icons_<size>_<hex>.png; photo() вырезает нужный глиф по имени icon_<glyph>_<size>_<hex>.png.

Смена темы (set_theme) перезагружает уже созданные PhotoImage НА МЕСТЕ: те же
объекты получают новые пиксели, поэтому элементы ttk, созданные один раз через
element_create, сразу показывают новую тему. Составные кэши (9-slice, иконки с
отступом) сбрасываются — их заново строят виджеты новой темы.
"""
from pathlib import Path
import itertools
import json
import logging
import re
import time
import tkinter as tk
from tkinter import ttk

ASSETS = Path(__file__).with_name('assets')
FRAME_MS = 15
MAX_STEP = 1 / 30
_ids = itertools.count()
LOGGER = logging.getLogger(__name__)
ICON_RE = re.compile(r'icon_(.+)_(\d+)_([0-9a-f]{6})\.png$')
_STATE = {'theme': 'aurora'}
_BUNDLES = {}
# Картинки, которые код запрашивает по имени на каждом кадре (пульс «Авроры»): при смене
# темы их не перезагружаем сразу, а помечаем устаревшими и обновляем при запросе.
LAZY_PREFIXES = ('pulse_',)
# Сокращённая анимация (настройка Windows «Показывать анимацию»): animate() сразу ставит
# конечное состояние. Декоративная атмосфера при этом остаётся статичной картинкой.
REDUCED_MOTION = False


_LISTINGS = {}


def _listing(folder):
    """Имена файлов каталога (читается один раз): проверка наличия без обращения к диску."""
    key = str(folder)
    if key not in _LISTINGS:
        try:
            _LISTINGS[key] = {entry.name for entry in folder.iterdir() if entry.is_file()}
        except OSError:
            _LISTINGS[key] = set()
    return _LISTINGS[key]


def asset_path(name, theme=None):
    """Файл из каталога темы, иначе из базового каталога (None, если нет нигде)."""
    theme = theme or _STATE['theme']
    if theme and theme != 'aurora':
        folder = ASSETS / 'themes' / theme
        if name in _listing(folder):
            return folder / name
    return ASSETS / name if name in _listing(ASSETS) else None


def _bundle(theme):
    """Пакет иконок темы: (индекс, байты). Читается один раз."""
    if theme not in _BUNDLES:
        folder = ASSETS if theme == 'aurora' else ASSETS / 'themes' / theme
        try:
            index = json.loads((folder / 'icons.json').read_text(encoding='utf-8'))
            _BUNDLES[theme] = (index, (folder / 'icons.dat').read_bytes())
        except (OSError, ValueError):
            _BUNDLES[theme] = ({}, b'')
    return _BUNDLES[theme]


def icon_bytes(name, theme=None):
    theme = theme or _STATE['theme']
    for candidate in dict.fromkeys((theme, 'aurora')):
        index, data = _bundle(candidate)
        span = index.get(name)
        if span:
            return data[span[0]:span[0] + span[1]]
    return None


def asset_bytes(name, theme=None):
    """Байты картинки: файл темы, базовый файл или иконка из пакета (None — нет нигде)."""
    path = asset_path(name, theme)
    if path is not None:
        return path.read_bytes()
    if ICON_RE.match(name):
        return icon_bytes(name, theme)
    return None


HEX_IN_NAME = re.compile(r'(?<=_)([0-9a-f]{6})(?=\.png$)')


def _store(root):
    return root.__dict__.setdefault('_ares_theme_store', {})


def _decoded(root, name, theme=None):
    """Готовая (декодированная заранее) картинка темы или None."""
    return _store(root).get((theme or _STATE['theme'], name))


def _fill(root, image, name):
    """Загружает пиксели `name` в существующий PhotoImage. False — если источника нет.

    Сначала берётся заранее декодированная копия (prefetch), иначе файл читается
    байтами: Tk декодирует PNG из памяти втрое быстрее, чем открывая файл сам.
    """
    ready = _decoded(root, name)
    if ready is not None:
        image.blank()
        image.configure(width=ready.width(), height=ready.height())
        image.tk.call(image, 'copy', ready, '-compositingrule', 'set')
        return True
    data = asset_bytes(name)
    if data is None:
        return False
    if image.width() and image.height():
        # Живая картинка: одна операция копирования вместо «очистить и загрузить» —
        # одно уведомление виджетам вместо двух.
        fresh = tk.PhotoImage(master=root, data=data)
        if fresh.width() != image.width() or fresh.height() != image.height():
            image.configure(width=fresh.width(), height=fresh.height())
        image.tk.call(image, 'copy', fresh, '-compositingrule', 'set')
    else:
        image.configure(data=data)
    return True


def photo(widget, name):
    root = widget._root()
    if not hasattr(root, '_ares_images'):
        root._ares_images = {}
        root._ares_all = []
    stale = root.__dict__.get('_ares_stale')
    if stale and name in stale and name in root._ares_images:
        stale.discard(name)
        _fill(root, root._ares_images[name], name)
    if name not in root._ares_images:
        image = tk.PhotoImage(master=root)
        if not _fill(root, image, name):
            LOGGER.warning('Missing asset %s for theme %s', name, _STATE['theme'])
            image.configure(width=1, height=1)
        root._ares_images[name] = image
        # Все когда-либо выданные картинки отслеживаются: при смене темы каждая
        # получает новые пиксели на месте, даже если её имя (цвет) поменялось.
        root._ares_all.append([image, name])
    return root._ares_images[name]


def prefetch(root, theme_id, names=None, chunk=6, delay=12, done=None):
    """Заранее декодирует картинки темы небольшими порциями в простое главного цикла.

    Тогда смена темы лишь копирует готовые пиксели (быстро), а не читает PNG.
    names=None — все картинки, уже открытые в текущей теме.
    """
    store = _store(root)
    wanted = list(dict.fromkeys(names if names is not None else [n for _, n in getattr(root, '_ares_all', [])]))
    queue = [n for n in wanted if (theme_id, n) not in store and not n.startswith(LAZY_PREFIXES)]

    def step():
        for _ in range(chunk):
            if not queue:
                if done:
                    done()
                return
            name = queue.pop(0)
            data = asset_bytes(name, theme_id)
            if data is None:
                continue
            try:
                store[(theme_id, name)] = tk.PhotoImage(master=root, data=data)
            except (OSError, tk.TclError):
                LOGGER.exception('Unable to prefetch %s', name)
        try:
            root.after(delay, step)
        except tk.TclError:
            pass
    step()


def release_prefetched(root, keep=()):
    """Освобождает заранее декодированные темы, кроме перечисленных."""
    store = _store(root)
    for key in [k for k in store if k[0] not in keep]:
        store.pop(key, None)


def current_theme():
    return _STATE['theme']


def remap_name(name, color_map):
    """icon_home_17_a898ff.png → то же имя с цветом новой темы (по карте цветов)."""
    match = HEX_IN_NAME.search(name)
    if not match or not color_map:
        return name
    new = color_map.get('#' + match.group(1))
    return name if not new else name[:match.start()] + new.lstrip('#').lower() + name[match.end():]


def set_theme(root, theme_id, color_map=None):
    """Переключает каталог картинок и перезагружает все выданные картинки НА МЕСТЕ.

    color_map ({'#старый': '#новый'}) переименовывает «цветные» картинки (иконки,
    чипы, точки): тот же объект PhotoImage получает пиксели иконки нового цвета,
    поэтому виджеты, которые на него ссылаются, менять не нужно.
    """
    previous = _STATE['theme']
    _STATE['theme'] = theme_id or 'aurora'
    entries = getattr(root, '_ares_all', [])
    images = getattr(root, '_ares_images', {})
    stale = root.__dict__.setdefault('_ares_stale', set())
    for entry in entries:
        image, name = entry
        new_name = remap_name(name, color_map)
        if new_name == name and not ICON_RE.match(name) and asset_path(name, previous) == asset_path(name):
            continue  # общий для всех тем файл (логотип, превью тем) — не меняется
        if new_name.startswith(LAZY_PREFIXES):
            entry[1] = new_name
            stale.add(new_name)
            continue
        try:
            if _fill(root, image, new_name):
                entry[1] = new_name
            elif new_name != name:
                _fill(root, image, name)
        except tk.TclError:
            LOGGER.exception('Unable to reload %s', new_name)
    images.clear()
    for image, name in entries:
        images.setdefault(name, image)
    recompose_slices(root)


def icon_name(name, size, color):
    return f'icon_{name}_{size}_{color.lstrip("#").lower()}.png'


def icon(canvas, name, x, y, color, size=20, anchor='nw'):
    return canvas.create_image(x, y, anchor=anchor, image=photo(canvas, icon_name(name, size, color)))


def nine_slice(widget, name, width, height, border):
    """Compose an anti-aliased PNG at any size: fixed corners, tiled edges and centre."""
    root = widget._root()
    cache = root.__dict__.setdefault('_ares_slices', {})
    width, height = max(1, int(width)), max(1, int(height))
    key = (name, width, height)
    if key in cache:
        return cache[key]
    if len(cache) > 160:
        old = next(iter(cache))
        cache.pop(old)
        root.__dict__.get('_ares_slice_borders', {}).pop(old, None)
    dst = tk.PhotoImage(master=root, width=width, height=height)
    _compose_slice(dst, photo(widget, name), width, height, border)
    cache[key] = dst
    root.__dict__.setdefault('_ares_slice_borders', {})[key] = border
    return dst


def recompose_slices(root):
    """После смены темы 9-slice картинки пересобираются в тех же объектах."""
    cache = root.__dict__.get('_ares_slices', {})
    borders = root.__dict__.get('_ares_slice_borders', {})
    for (name, width, height), dst in list(cache.items()):
        try:
            _compose_slice(dst, photo(root, name), width, height, borders.get((name, width, height), 8))
        except tk.TclError:
            LOGGER.exception('Unable to recompose %s', name)


def _compose_slice(dst, src, width, height, border):
    sw, sh = src.width(), src.height()
    left, top, right, bottom = (border,) * 4 if isinstance(border, int) else border
    left, right = min(left, width // 2), min(right, width - width // 2)
    top, bottom = min(top, height // 2), min(bottom, height - height // 2)

    def copy(fx1, fy1, fx2, fy2, tx1, ty1, tx2, ty2):
        if fx2 > fx1 and fy2 > fy1 and tx2 > tx1 and ty2 > ty1:
            dst.tk.call(dst, 'copy', src, '-from', fx1, fy1, fx2, fy2, '-to', tx1, ty1, tx2, ty2,
                        '-compositingrule', 'set')

    copy(0, 0, left, top, 0, 0, left, top)
    copy(sw - right, 0, sw, top, width - right, 0, width, top)
    copy(0, sh - bottom, left, sh, 0, height - bottom, left, height)
    copy(sw - right, sh - bottom, sw, sh, width - right, height - bottom, width, height)
    copy(left, 0, sw - right, top, left, 0, width - right, top)
    copy(left, sh - bottom, sw - right, sh, left, height - bottom, width - right, height)
    copy(0, top, left, sh - bottom, 0, top, left, height - bottom)
    copy(sw - right, top, sw, sh - bottom, width - right, top, width, height - bottom)
    copy(left, top, sw - right, sh - bottom, left, top, width - right, height - bottom)


# ----------------------------------------------------------------- easing
def ease_out_cubic(t):
    return 1 - (1 - t) ** 3


def ease_out_quint(t):
    return 1 - (1 - t) ** 5


def ease_in_out_cubic(t):
    return 4 * t * t * t if t < .5 else 1 - (-2 * t + 2) ** 3 / 2


def ease_out_back(t, overshoot=1.3):
    c3 = overshoot + 1
    return 1 + c3 * (t - 1) ** 3 + overshoot * (t - 1) ** 2


def ease_in_cubic(t):
    return t * t * t


def _rgb(color):
    color = color.lstrip('#')
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


def lerp_color(a, b, t):
    ra, rb = _rgb(a), _rgb(b)
    return '#%02X%02X%02X' % tuple(round(x + (y - x) * t) for x, y in zip(ra, rb))


def animate(widget, key, duration, update, ease=ease_out_cubic, done=None):
    """Call update(eased progress) every frame; a new call with the same key replaces the old one.

    Progress advances by at most MAX_STEP per frame, so a page that takes a moment
    to lay out still plays its whole transition instead of jumping to the end.
    """
    # Timers live on the root, so a global "after cancel" never orphans widget commands.
    root = widget._root()
    if REDUCED_MOTION:
        duration = 0
    jobs = widget.__dict__.setdefault('_ares_anims', {})
    old = jobs.pop(key, None)
    if old is not None:
        try:
            root.after_cancel(old)
        except tk.TclError:
            pass
    clock = {'elapsed': 0.0, 'last': None}

    def frame():
        jobs.pop(key, None)
        try:
            if not widget.winfo_exists():
                return
        except tk.TclError:
            return
        now = time.perf_counter()
        if clock['last'] is not None:
            clock['elapsed'] += min(now - clock['last'], MAX_STEP)
        clock['last'] = now
        t = 1.0 if duration <= 0 else min(1.0, clock['elapsed'] * 1000 / duration)
        update(ease(t))
        if t < 1:
            jobs[key] = root.after(FRAME_MS, frame)
        elif done:
            done()
    frame()


def stop_animation(widget, key):
    job = widget.__dict__.get('_ares_anims', {}).pop(key, None)
    if job is not None:
        try:
            widget._root().after_cancel(job)
        except tk.TclError:
            pass


def flattened(widget, image, background, target=None):
    """Composite a translucent PNG over a solid colour.

    Tk blends photos with soft alpha pixel by pixel on every redraw; an opaque
    copy is drawn with a plain blit, which keeps pages with many controls fast.
    """
    w, h = image.width(), image.height()
    if target is None:
        target = tk.PhotoImage(master=widget, width=w, height=h)
    target.put(background, to=(0, 0, w, h))
    target.tk.call(target, 'copy', image, '-compositingrule', 'overlay')
    return target


def redraw(widget):
    """ttk image elements ignore changes to their photo, so each animation frame asks for a repaint."""
    try:
        widget.event_generate('<Expose>')
    except tk.TclError:
        pass


def parent_background(master, style=None):
    style = style or ttk.Style(master)
    try:
        parent_style = master.cget('style') or master.winfo_class()
        return style.lookup(parent_style, 'background') or '#0B0B12'
    except tk.TclError:
        return master.cget('background')


BUTTON_FRAMES = 12
_KINDS = {'Accent.TButton': 'accent', 'Ghost.TButton': 'ghost', 'Danger.TButton': 'danger'}


class AnimatedButton(ttk.Button):
    """Keep ttk keyboard/focus/accessibility behaviour; animate only the surface.

    The surface fades through pre-rendered frames on hover and focus and
    sinks into a pressed frame while the mouse button is down.
    """
    def __init__(self, master=None, **kwargs):
        base = kwargs.pop('style', 'TButton')
        super().__init__(master, **kwargs)
        style = ttk.Style(self)
        background = parent_background(master, style)
        kind = _KINDS.get(base, 'button')
        self._frames = [photo(self, f'{kind}_frame_{i}.png') for i in range(BUTTON_FRAMES)]
        self._pressed_frame = photo(self, f'{kind}_pressed.png')
        self._background = background
        self._surface = tk.PhotoImage(master=self, width=self._frames[0].width(), height=self._frames[0].height())
        self._disabled = flattened(self, photo(self, 'button_disabled.png'), background)
        self._show(self._frames[0])
        self._level = 0.0
        self._index = 0
        self._hover = self._focus = self._down = False
        unique = f'AresAnimated{next(_ids)}'
        name = unique + '.' + base
        # The frames are wide for fast 9-slicing; the natural size stays that of a small button.
        style.element_create(unique, 'image', self._surface, ('disabled', self._disabled),
                             border=14, padding=0, sticky='nsew', width=40, height=40)
        style.layout(name, [(unique, {'sticky': 'nsew', 'children': [
            ('Button.padding', {'sticky': 'nsew', 'children': [
                ('Button.label', {'sticky': 'nsew'})]})]})])
        style.configure(name, background=background, borderwidth=0, relief='flat')
        # Clam's active background used to leak through the transparent corners.
        for option in ('background', 'bordercolor', 'lightcolor', 'darkcolor'):
            style.map(name, **{option: [('disabled', background), ('!disabled', background)]})
        self.configure(style=name)
        self.bind('<Enter>', lambda e: self._set(hover=True), add='+')
        self.bind('<Leave>', lambda e: self._set(hover=False, down=False), add='+')
        self.bind('<FocusIn>', lambda e: self._set(focus=True), add='+')
        self.bind('<FocusOut>', lambda e: self._set(focus=False), add='+')
        self.bind('<ButtonPress-1>', lambda e: self._set(down=True), add='+')
        self.bind('<ButtonRelease-1>', lambda e: self._set(down=False), add='+')

    def _show(self, image):
        flattened(self, image, self._background, self._surface)
        redraw(self)

    def restyle(self):
        """Смена темы: новый фон родителя, заново «сплющенные» кадры и стиль кнопки."""
        style = ttk.Style(self)
        background = parent_background(self.master, style)
        self._background = background
        flattened(self, photo(self, 'button_disabled.png'), background, self._disabled)
        name = str(self.cget('style'))
        style.configure(name, background=background)
        for option in ('background', 'bordercolor', 'lightcolor', 'darkcolor'):
            style.map(name, **{option: [('disabled', background), ('!disabled', background)]})
        self._show(self._pressed_frame if self._index == -1 else self._frames[max(0, self._index)])

    def _set(self, hover=None, focus=None, down=None):
        if hover is not None:
            self._hover = hover
        if focus is not None:
            self._focus = focus
        if down is not None:
            self._down = down
        if self._down and not self.instate(['disabled']):
            stop_animation(self, 'hover')
            self._show(self._pressed_frame)
            self._index = -1
            return
        target = 1.0 if (self._hover or self._focus) else 0.0
        start = self._level
        duration = 170 if target > start else 300

        def step(t):
            self._level = start + (target - start) * t
            index = round(self._level * (BUTTON_FRAMES - 1))
            if index != self._index:
                self._index = index
                self._show(self._frames[index])
        animate(self, 'hover', duration, step, ease_out_cubic)


SWITCH_FRAMES = 10


class AnimatedSwitch(ttk.Checkbutton):
    """A ttk.Checkbutton drawn as a toggle switch whose knob glides between states."""
    def __init__(self, master=None, base_style='TCheckbutton', **kwargs):
        super().__init__(master, **kwargs)
        style = ttk.Style(self)
        self._base_style = base_style
        self._frames = [photo(self, f'switch_{i}.png') for i in range(SWITCH_FRAMES)]
        self._background = style.lookup(base_style, 'background') or '#13131C'
        self._surface = tk.PhotoImage(master=self, width=self._frames[0].width(), height=self._frames[0].height())
        self._disabled = [flattened(self, photo(self, f'switch_disabled_{n}.png'), self._background) for n in ('on', 'off')]
        unique = f'AresSwitch{next(_ids)}'
        style.element_create(unique, 'image', self._surface,
                             ('disabled selected', self._disabled[0]),
                             ('disabled', self._disabled[1]), sticky='')
        name = unique + '.' + base_style
        style.layout(name, [('Checkbutton.padding', {'sticky': 'nswe', 'children': [
            (unique, {'side': 'left', 'sticky': ''}),
            ('Checkbutton.label', {'side': 'left', 'sticky': 'nswe'})]})])
        self.configure(style=name)
        self._variable = kwargs.get('variable')
        self._level = 1.0 if self._value() else 0.0
        self._index = -1
        self._paint()
        self._trace = None
        if isinstance(self._variable, tk.Variable):
            self._trace = self._variable.trace_add('write', lambda *a: self._changed())
            self.bind('<Destroy>', self._untrace, add='+')

    def restyle(self):
        """Смена темы: фон из базового стиля и заново «сплющенные» кадры."""
        self._background = ttk.Style(self).lookup(self._base_style, 'background') or self._background
        for image, state in zip(self._disabled, ('on', 'off')):
            flattened(self, photo(self, f'switch_disabled_{state}.png'), self._background, image)
        self._index = -1
        self._paint()

    def _value(self):
        try:
            return bool(self._variable.get()) if isinstance(self._variable, tk.Variable) else self.instate(['selected'])
        except (tk.TclError, ValueError):
            return False

    def _paint(self):
        index = max(0, min(SWITCH_FRAMES - 1, round(self._level * (SWITCH_FRAMES - 1))))
        if index != self._index:
            self._index = index
            flattened(self, self._frames[index], self._background, self._surface)
            redraw(self)

    def _changed(self):
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        target = 1.0 if self._value() else 0.0
        start = self._level

        def step(t):
            self._level = start + (target - start) * t
            self._paint()
        animate(self, 'switch', 260 if self.winfo_viewable() else 0, step, ease_out_back)

    def _untrace(self, event):
        if event.widget is self and self._trace:
            try:
                self._variable.trace_remove('write', self._trace)
            except (tk.TclError, ValueError):
                pass
            self._trace = None


FIELD_FRAMES = 12
_FIELD_ELEMENTS = {'Ares.field': 'field', 'Ares.herofield': 'field_hero'}


def animate_field(widget, dialog=False):
    """Give a themed Entry/Combobox/Spinbox its own surface that glides rest → hover → focus."""
    if getattr(widget, '_ares_field', False):
        return
    style = ttk.Style(widget)
    base = str(widget.cget('style')) or widget.winfo_class()
    layout = style.layout(base)
    if not layout or layout[0][0] not in _FIELD_ELEMENTS:
        return
    prefix = _FIELD_ELEMENTS[layout[0][0]]
    if dialog and prefix == 'field':
        prefix = 'field_dialog'
    frames = [photo(widget, f'{prefix}_anim_{i}.png') for i in range(FIELD_FRAMES)]
    surface = tk.PhotoImage(master=widget, width=frames[0].width(), height=frames[0].height())
    surface.tk.call(surface, 'copy', frames[0], '-compositingrule', 'set')
    unique = f'AresField{next(_ids)}'
    style.element_create(unique, 'image', surface,
                         ('disabled', photo(widget, ('field_hero' if prefix == 'field_hero' else 'field') + '_disabled.png')),
                         border=12, padding=0, sticky='nsew', width=36, height=36)
    name = unique + '.' + base
    style.layout(name, [(unique, layout[0][1])] + list(layout[1:]))
    widget.configure(style=name)
    state = {'level': 0.0, 'index': 0, 'hover': False, 'focus': False}
    widget._ares_field = True
    widget._ares_field_surface = surface
    # Для смены темы: кадры перезагружаются на месте, поверхность нужно лишь перерисовать.
    widget.restyle = lambda: (surface.tk.call(surface, 'copy', frames[state['index']], '-compositingrule', 'set'),
                              redraw(widget))

    def refresh(**changes):
        state.update(changes)
        target = 1.0 if state['focus'] else (.45 if state['hover'] else 0.0)
        start = state['level']

        def step(t):
            state['level'] = start + (target - start) * t
            index = max(0, min(FIELD_FRAMES - 1, round(state['level'] * (FIELD_FRAMES - 1))))
            if index != state['index']:
                state['index'] = index
                surface.tk.call(surface, 'copy', frames[index], '-compositingrule', 'set')
                redraw(widget)
        animate(widget, 'field', 240 if target > start else 320, step, ease_out_cubic)
    widget.bind('<Enter>', lambda e: refresh(hover=True), add='+')
    widget.bind('<Leave>', lambda e: refresh(hover=False), add='+')
    widget.bind('<FocusIn>', lambda e: refresh(focus=True), add='+')
    widget.bind('<FocusOut>', lambda e: refresh(focus=False), add='+')