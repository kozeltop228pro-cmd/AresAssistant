"""Атмосфера темы: декоративные частицы (звёзды, снег, лепестки, пыль, листья).

Жизненный цикл
--------------
* Atmosphere создаётся темой (ThemeUI) уже ПОСЛЕ построения интерфейса и получает
  слои — холсты и прямоугольник, где частицам можно жить (свободные места:
  правая часть героя на «Обзоре» и пустой низ боковой панели). Рабочие области
  (таблицы, поля, тексты) частиц не получают никогда.
* Все слои обслуживает ОДИН таймер (root.after). Если окно свёрнуто или скрыто в
  трей, а также если холст не виден (открыта другая страница), кадры не
  считаются: таймер переходит в редкий опрос и ничего не рисует.
* stop() отменяет таймер и удаляет все элементы частиц со всех холстов. Тема
  вызывает stop() перед перестройкой интерфейса, поэтому старая атмосфера не
  переживает смену темы и не оставляет «висящих» таймеров.

Плотность
---------
level: "off" — частиц нет (мотив темы на картинке героя остаётся, тема остаётся
красивой); "delicate" — несколько штук; "expressive" — больше. Число частиц
масштабируется площадью слоя, на маленьких экранах уменьшается, сверху ограничено.

Сокращённая анимация
--------------------
reduced=True (в Windows выключено «Показывать анимацию»): частицы расставляются
один раз и не двигаются — остаётся статичный декор, таймер не запускается.

Частицы не перехватывают мышь и фокус: это элементы холста без привязок событий,
а холст боковой панели создаётся с takefocus=0.
"""
from __future__ import annotations

import json
import logging
import math
import random
import time
import tkinter as tk

from ui_assets import asset_path, photo

LOGGER = logging.getLogger(__name__)
FRAME_MS = 40          # 25 кадров в секунду достаточно для медленных частиц
IDLE_MS = 600          # опрос, пока окно скрыто
MAX_PER_LAYER = 36
REFERENCE_AREA = 80_000


def _alive(widget):
    try:
        return bool(widget is not None and widget.winfo_exists())
    except tk.TclError:
        return False


class _Particle:
    __slots__ = ("item", "x", "y", "vx", "vy", "phase", "spin", "frames", "frame", "sway")


class Atmosphere:
    def __init__(self, root, theme, level="delicate", reduced=False):
        self.root = root
        self.theme = theme
        self.level = level
        self.reduced = reduced
        self.layers = []
        self._job = None
        self._last = None
        self._streak = None
        self._next_streak = time.monotonic() + 4
        self.manifest = self._load_manifest()
        self._rng = random.Random(1729)

    # ------------------------------------------------------------ данные
    def _load_manifest(self):
        path = asset_path("particles.json")
        if path is None:
            return {"kind": "", "sprites": [], "streak": ""}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {"kind": "", "sprites": []}
        except (OSError, ValueError):
            LOGGER.exception("Bad particles manifest")
            return {"kind": "", "sprites": []}

    @property
    def kind(self):
        return self.manifest.get("kind", "")

    def _count(self, region, factor):
        if self.level == "off" or not self.manifest.get("sprites"):
            return 0
        base = float(self.theme.get(self.level, 0) or 0)
        x1, y1, x2, y2 = region
        area = max(0, x2 - x1) * max(0, y2 - y1)
        count = base * factor * area / REFERENCE_AREA
        try:
            if self.root.winfo_screenwidth() < 1400:
                count *= .7   # небольшие экраны — меньше частиц
        except tk.TclError:
            pass
        return max(0, min(MAX_PER_LAYER, round(count)))

    # ------------------------------------------------------------ слои
    def add_layer(self, canvas, region, factor=1.0, below=None):
        """region() → (x1, y1, x2, y2) в координатах холста; пересчитывается на лету."""
        layer = {"canvas": canvas, "region": region, "factor": factor, "below": below, "particles": [], "size": None}
        self.layers.append(layer)
        self._populate(layer)
        return layer

    def _populate(self, layer):
        canvas = layer["canvas"]
        for particle in layer["particles"]:
            try:
                canvas.delete(particle.item)
            except tk.TclError:
                pass
        layer["particles"] = []
        if not _alive(canvas):
            return
        region = layer["region"]()
        layer["size"] = region
        sprites = self.manifest.get("sprites") or []
        for _ in range(self._count(region, layer["factor"])):
            frames = [photo(canvas, name) for name in self._rng.choice(sprites)]
            p = _Particle()
            p.frames, p.frame = frames, self._rng.randrange(len(frames))
            x1, y1, x2, y2 = region
            p.x, p.y = self._rng.uniform(x1, x2), self._rng.uniform(y1, y2)
            p.phase, p.spin, p.sway = self._rng.uniform(0, 6.28), self._rng.uniform(.6, 1.6), self._rng.uniform(6, 16)
            p.vx, p.vy = self._velocity()
            p.item = canvas.create_image(round(p.x), round(p.y), image=frames[p.frame], tags=("atmo",))
            if layer["below"] is not None:
                try:
                    canvas.tag_lower(p.item, layer["below"])
                except tk.TclError:
                    pass
            layer["particles"].append(p)

    def _velocity(self):
        speed = float(self.theme.get("speed", 1.0) or 1.0)
        r = self._rng
        kind = self.kind
        if kind == "snow":
            return r.uniform(-4, 4) * speed, r.uniform(12, 26) * speed
        if kind in ("petals", "leaves"):
            return r.uniform(-14, -5) * speed, r.uniform(14, 26) * speed
        if kind == "sparks":
            return r.uniform(-3, 3) * speed, -r.uniform(6, 14) * speed
        if kind == "dust":
            return r.uniform(-5, 5) * speed, r.uniform(-4, 2) * speed
        return 0.0, 0.0  # stars twinkle in place

    # ------------------------------------------------------------ цикл
    def start(self):
        if self.reduced or self.level == "off" or not self.layers:
            return
        self._last = time.monotonic()
        self._schedule(FRAME_MS)

    def _schedule(self, delay):
        try:
            self._job = self.root.after(delay, self._tick)
        except tk.TclError:
            self._job = None

    def _window_hidden(self):
        try:
            return self.root.state() in ("withdrawn", "iconic") or not self.root.winfo_viewable()
        except tk.TclError:
            return True

    def _tick(self):
        self._job = None
        now = time.monotonic()
        dt = min(.1, now - (self._last or now))
        self._last = now
        if self._window_hidden():
            self._schedule(IDLE_MS)  # пауза, пока окно не видно
            return
        self.layers = [layer for layer in self.layers if _alive(layer["canvas"])]
        if not self.layers:
            return
        for layer in self.layers:
            canvas = layer["canvas"]
            try:
                if not canvas.winfo_viewable():
                    continue
            except tk.TclError:
                continue
            region = layer["region"]()
            if region != layer["size"]:
                self._populate(layer)
                continue
            self._step(layer, region, dt, now)
        if self.kind == "stars":
            self._shooting_star(now)
        self._schedule(FRAME_MS)

    def _step(self, layer, region, dt, now):
        canvas = layer["canvas"]
        x1, y1, x2, y2 = region
        kind = self.kind
        for p in layer["particles"]:
            if kind == "stars":
                # Мерцание: медленная смена кадров с собственной фазой у каждой звезды.
                level = (math.sin(now * p.spin + p.phase) + 1) / 2
                frame = min(len(p.frames) - 1, int(level * len(p.frames)))
                if frame != p.frame:
                    p.frame = frame
                    canvas.itemconfigure(p.item, image=p.frames[frame])
                continue
            p.x += p.vx * dt + (math.sin(now * p.spin + p.phase) * p.sway * dt if kind in ("snow", "petals", "leaves") else 0)
            p.y += p.vy * dt
            if kind == "dust":
                p.vx += self._rng.uniform(-1.5, 1.5) * dt
                p.vy += self._rng.uniform(-1.5, 1.5) * dt
            out = p.y > y2 + 10 or p.y < y1 - 10 or p.x < x1 - 14 or p.x > x2 + 14
            if out:
                if kind == "sparks":
                    p.x, p.y = self._rng.uniform(x1, x2), y2 + 6
                elif kind == "dust":
                    p.x, p.y = self._rng.uniform(x1, x2), self._rng.uniform(y1, y2)
                    p.vx, p.vy = self._velocity()
                else:
                    p.x, p.y = self._rng.uniform(x1, x2 + 30), y1 - 8
            if len(p.frames) > 1:
                frame = int((now * p.spin * 2 + p.phase) % len(p.frames))
                if frame != p.frame:
                    p.frame = frame
                    canvas.itemconfigure(p.item, image=p.frames[frame])
            canvas.coords(p.item, round(p.x), round(p.y))

    def _shooting_star(self, now):
        """Редкая падающая звезда в одном из слоёв (раз в 7–12 секунд, ~0,9 с полёта)."""
        name = self.manifest.get("streak")
        layer = next((l for l in self.layers if l["factor"] >= 1 and _alive(l["canvas"])), None)
        if not name or layer is None:
            return
        canvas = layer["canvas"]
        if self._streak is None and now >= self._next_streak and canvas.winfo_viewable():
            x1, y1, x2, y2 = layer["region"]()
            item = canvas.create_image(0, 0, image=photo(canvas, name), anchor="center", tags=("atmo",))
            start = (self._rng.uniform(x1 + 40, x2 - 120), self._rng.uniform(y1 + 6, y1 + (y2 - y1) * .35))
            self._streak = {"item": item, "canvas": canvas, "t0": now, "start": start}
        if self._streak is not None:
            s = self._streak
            t = (now - s["t0"]) / .9
            if t >= 1 or not _alive(s["canvas"]):
                try:
                    s["canvas"].delete(s["item"])
                except tk.TclError:
                    pass
                self._streak = None
                self._next_streak = now + self._rng.uniform(7, 12)
                return
            x, y = s["start"]
            s["canvas"].coords(s["item"], round(x + 110 * t), round(y + 36 * t))

    def stop(self):
        """Отменяет таймер и убирает все частицы — вызывается при смене темы и уровня."""
        if self._job is not None:
            try:
                self.root.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None
        for layer in self.layers:
            canvas = layer["canvas"]
            if _alive(canvas):
                try:
                    canvas.delete("atmo")
                except tk.TclError:
                    pass
        self.layers = []
        self._streak = None
