"""Themed popup menus and dialogs that replace the white native Windows ones.

PopupMenu keeps the small part of the tk.Menu API the app uses (add_command,
add_separator, delete, entrycget, invoke, tk_popup, unpost, grab_release,
winfo_ismapped). install_dialogs() swaps tkinter.messagebox / simpledialog.askstring
for animated dialogs in the app's style and falls back to the native ones
whenever the main window is hidden in the tray.
"""
from __future__ import annotations

import logging
import time
import tkinter as tk
from tkinter import ttk, font as tkfont

from ui_widgets import overlay_key
from ui_assets import (AnimatedButton, photo, icon_name, nine_slice, animate, lerp_color,
                       ease_out_cubic, ease_out_quint, ease_in_cubic)

LOGGER = logging.getLogger(__name__)
KEY = "#010203"
DECORATE = None  # set by install_dialogs: gives dialog buttons the app's icons
MENU_BG = "#171723"
DIALOG_BG = "#15151F"


def _is_dark(color):
    color = color.lstrip("#")
    r, g, b = (int(color[i:i + 2], 16) for i in (0, 2, 4))
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) < 128


def _alive(widget):
    try:
        return bool(widget is not None and widget.winfo_exists())
    except tk.TclError:
        return False


def _overlay_window(master):
    """Borderless always-on-top window whose KEY colour is see-through (rounded corners)."""
    win = tk.Toplevel(master)
    win.withdraw()
    win.overrideredirect(True)
    try:
        key = overlay_key()
        win.attributes("-transparentcolor", key)
    except tk.TclError:
        key = MENU_BG
    win.configure(bg=key)
    try:
        win.attributes("-alpha", 0.0)
        win.attributes("-topmost", True)
    except tk.TclError:
        pass
    return win, key


class PopupMenu:
    """Rounded menu with icons, a gliding highlight and keyboard navigation."""
    ITEM_H, SEP_H, PAD = 34, 13, 6
    _open = None

    def __init__(self, master, palette, fnt, on_close=None):
        self.master, self.p, self.fnt = master, palette, fnt
        self.entries = []
        self.on_close = on_close
        self.win = None
        self.closed_at = 0.0
        self._rows = []
        self._active = None

    # tk.Menu compatible surface ------------------------------------------
    def add_command(self, label="", command=None, icon=None, danger=None, state="normal", hint="", **_ignored):
        """state="disabled" shows the item dimmed with a short reason (hint) and never runs it."""
        if danger is None:
            danger = str(label).strip().lower().startswith("удалить")
        disabled = str(state) == "disabled"
        self.entries.append({"kind": "command", "label": str(label), "command": command, "icon": icon,
                             "danger": danger and not disabled, "disabled": disabled, "hint": str(hint or "")})

    def add_separator(self, **_ignored):
        self.entries.append({"kind": "separator", "label": ""})

    def delete(self, first, last=None):
        start = 0 if first in ("end",) else int(first)
        stop = len(self.entries) if last in ("end",) else (start + 1 if last is None else int(last) + 1)
        del self.entries[start:stop]

    def entrycget(self, index, option):
        return self.entries[int(index)].get(option, "")

    def invoke(self, index):
        entry = self.entries[int(index)]
        if entry["kind"] == "command" and entry["command"] and not entry.get("disabled"):
            return entry["command"]()

    def tk_popup(self, x, y, entry=None):
        self.post(x, y)

    def grab_release(self):
        pass

    def unpost(self):
        self.close(immediate=True)

    def winfo_ismapped(self):
        try:
            return bool(_alive(self.win) and self.win.winfo_ismapped())
        except tk.TclError:
            return False

    # drawing ---------------------------------------------------------------
    def post(self, x, y):
        if PopupMenu._open is not None and PopupMenu._open is not self:
            PopupMenu._open.close(immediate=True)
        self.close(immediate=True)
        PopupMenu._open = self
        font = tkfont.Font(font=self.fnt(10))
        hint_font = tkfont.Font(font=self.fnt(8))
        text_w = max([font.measure(e["label"]) + (hint_font.measure(e.get("hint", "")) + 18 if e.get("hint") else 0)
                      for e in self.entries] + [60])
        w = max(220, text_w + 82)
        h = self.PAD * 2 + sum(self.ITEM_H if e["kind"] == "command" else self.SEP_H for e in self.entries)
        win, key = _overlay_window(self.master)
        self.win = win
        canvas = self.canvas = tk.Canvas(win, width=w, height=h, bg=key, bd=0, highlightthickness=0, takefocus=1)
        canvas.pack()
        canvas.create_image(0, 0, anchor="nw", image=nine_slice(canvas, "menu.png", w, h, 14))
        self._pill = canvas.create_image(6, -100, anchor="nw", image=nine_slice(canvas, "menu_hover.png", w - 12, self.ITEM_H - 2, 9), state="hidden")
        self._rows = []
        y_pos = self.PAD
        for index, entry in enumerate(self.entries):
            if entry["kind"] == "separator":
                canvas.create_line(14, y_pos + self.SEP_H / 2, w - 14, y_pos + self.SEP_H / 2, fill=self.p["separator"])
                y_pos += self.SEP_H
                continue
            cy = y_pos + self.ITEM_H / 2
            disabled = entry.get("disabled", False)
            if disabled:
                color, glyph_color = self.p["muted_dim"], self.p["muted_dim"]
            else:
                color = self.p["danger_text"] if entry["danger"] else self.p["text_soft"]
                glyph_color = self.p["red"] if entry["danger"] else self.p["muted"]
            icon_id = canvas.create_image(17, cy, anchor="w", image=photo(canvas, icon_name(entry["icon"], 17, glyph_color))) if entry["icon"] else None
            text_id = canvas.create_text(44 if entry["icon"] else 18, cy, anchor="w", text=entry["label"], fill=self.p["menu_bg"], font=font)
            if entry.get("hint"):
                # The reason sits on the right in a smaller, quieter type (e.g. why deletion is unavailable).
                canvas.create_text(w - 16, cy, anchor="e", text=entry["hint"], fill=self.p["muted_dim"], font=hint_font)
            self._rows.append({"index": index, "top": y_pos, "text": text_id, "icon": icon_id, "color": color,
                               "danger": entry["danger"], "disabled": disabled})
            y_pos += self.ITEM_H
        self._active = None
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        x = max(4, min(int(x), sw - w - 4))
        y = int(y) if int(y) + h < sh - 44 else max(4, int(y) - h - 44)
        win.geometry(f"{w}x{h}+{x}+{y - 6}")
        try:
            win.transient(self.master.winfo_toplevel())
        except tk.TclError:
            pass
        win.deiconify()
        win.lift()

        def appear(t):
            win.geometry(f"+{x}+{round(y - 6 * (1 - t))}")
            try:
                win.attributes("-alpha", t)
            except tk.TclError:
                pass
        animate(win, "in", 170, appear, ease_out_cubic)
        for order, row in enumerate(self._rows):  # items surface one after another
            def fade(t, row=row):
                target = (self.p["danger_active"] if row["danger"] else self.p["text"]) if row is self._active else row["color"]
                canvas.itemconfigure(row["text"], fill=lerp_color(self.p["menu_bg"], target, t))
            canvas._root().after(order * 22, lambda f=fade, r=row: animate(canvas, f"row{r['index']}", 220, f, ease_out_cubic))
        canvas.bind("<Motion>", self._motion)
        canvas.bind("<Leave>", lambda e: self._highlight(None))
        canvas.bind("<ButtonRelease-1>", self._click)
        for seq, delta in (("<Down>", 1), ("<Up>", -1), ("<Tab>", 1)):
            canvas.bind(seq, lambda e, d=delta: self._step(d))
        canvas.bind("<Return>", lambda e: self._run(self._active))
        canvas.bind("<space>", lambda e: self._run(self._active))
        canvas.bind("<Escape>", lambda e: self.close())
        canvas.bind("<FocusOut>", self._focus_out)
        win.focus_force()
        canvas.focus_set()

    def _row_at(self, y):
        for row in self._rows:
            if row["top"] <= y < row["top"] + self.ITEM_H:
                return row
        return None

    def _motion(self, event):
        row = self._row_at(event.y)
        if row is not None and row.get("disabled"):
            row = None  # a disabled item gets no highlight and no hand cursor
        self.canvas.configure(cursor="hand2" if row else "")
        self._highlight(row)

    def _highlight(self, row):
        if row is self._active:
            return
        canvas = self.canvas
        previous, self._active = self._active, row
        if previous is not None:
            canvas.itemconfigure(previous["text"], fill=previous["color"])
        if row is None:
            canvas.itemconfigure(self._pill, state="hidden")
            return
        w = int(canvas.cget("width"))
        canvas.itemconfigure(self._pill, image=nine_slice(canvas, "menu_hover_danger.png" if row["danger"] else "menu_hover.png",
                                                          w - 12, self.ITEM_H - 2, 9), state="normal")
        canvas.itemconfigure(row["text"], fill=self.p["danger_active"] if row["danger"] else self.p["text"])
        y1 = row["top"] + 1
        if previous is None:
            canvas.coords(self._pill, 6, y1)
        else:
            y0 = canvas.coords(self._pill)[1]
            animate(canvas, "pill", 150, lambda t: canvas.coords(self._pill, 6, y0 + (y1 - y0) * t), ease_out_quint)

    def _step(self, delta):
        rows = [row for row in self._rows if not row.get("disabled")]
        if not rows:
            return "break"
        current = rows.index(self._active) if self._active in rows else (-1 if delta > 0 else 0)
        self._highlight(rows[(current + delta) % len(rows)])
        return "break"

    def _click(self, event):
        self._run(self._row_at(event.y))

    def _run(self, row):
        if row is None or row.get("disabled"):
            return "break"
        command = self.entries[row["index"]]["command"]
        self.close()
        if command:
            self.master.after_idle(command)
        return "break"

    def _focus_out(self, event):
        def check():
            if not _alive(self.win):
                return
            try:
                focused = self.win.focus_get()
            except (KeyError, tk.TclError):
                focused = None
            if focused is None or not str(focused).startswith(str(self.win)):
                self.close()
        self.master._root().after(60, check)

    def close(self, immediate=False):
        win, self.win = self.win, None
        if PopupMenu._open is self:
            PopupMenu._open = None
        if not _alive(win):
            return
        self.closed_at = time.monotonic()
        if self.on_close:
            try:
                self.on_close()
            except tk.TclError:
                pass
        if immediate:
            win.destroy()
            return

        def fade(t):
            try:
                win.attributes("-alpha", 1 - t)
            except tk.TclError:
                pass
        animate(win, "out", 110, fade, ease_in_cubic, done=win.destroy)


# ------------------------------------------------------------------ tooltip
class Tooltip:
    """A small rounded hint that fades in next to the pointer.

    One instance serves a whole widget (e.g. a Treeview whose cells explain
    themselves). show() is debounced: the hint appears after DELAY ms of rest and
    moves with the pointer; hide() cancels a pending show and fades the hint out.
    """
    DELAY, PAD_X, PAD_Y, MAX_W = 420, 12, 8, 320

    def __init__(self, master, palette, fnt):
        self.master, self.p, self.fnt = master, palette, fnt
        self.win = None
        self.text = ""
        self._job = None

    def show(self, text, x_root, y_root):
        if not text:
            self.hide()
            return
        if text == self.text and _alive(self.win):
            self.win.geometry(f"+{x_root + 14}+{y_root + 20}")
            return
        self.hide(immediate=True)
        self.text = text
        root = self.master._root()
        self._job = root.after(self.DELAY, lambda: self._open(text, x_root, y_root))

    def _open(self, text, x_root, y_root):
        self._job = None
        if not _alive(self.master) or not self.master.winfo_viewable():
            return
        win, key = _overlay_window(self.master)
        self.win = win
        font = tkfont.Font(font=self.fnt(9))
        probe = tk.Canvas(win)
        tid = probe.create_text(0, 0, anchor="nw", text=text, font=font, width=self.MAX_W)
        x1, y1, x2, y2 = probe.bbox(tid)
        probe.destroy()
        w, h = int(x2 - x1) + self.PAD_X * 2, int(y2 - y1) + self.PAD_Y * 2
        canvas = tk.Canvas(win, width=w, height=h, bg=key, bd=0, highlightthickness=0)
        canvas.pack()
        canvas.create_image(0, 0, anchor="nw", image=nine_slice(canvas, "menu.png", w, h, 10))
        canvas.create_text(self.PAD_X, self.PAD_Y, anchor="nw", text=text, font=font, width=self.MAX_W,
                           fill=self.p["text_soft"])
        sw = win.winfo_screenwidth()
        x = max(4, min(x_root + 14, sw - w - 4))
        win.geometry(f"{w}x{h}+{x}+{y_root + 20}")
        win.deiconify()

        def appear(t):
            try:
                win.attributes("-alpha", t)
            except tk.TclError:
                pass
        animate(win, "in", 140, appear, ease_out_cubic)

    def hide(self, immediate=False):
        if self._job is not None:
            try:
                self.master._root().after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None
        self.text = ""
        win, self.win = self.win, None
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
        animate(win, "out", 100, fade, ease_in_cubic, done=win.destroy)


def attach_tooltip(widget, palette, fnt, text):
    """Hover hint for a single widget; text may be a callable evaluated on each hover."""
    tip = Tooltip(widget, palette, fnt)

    def enter(event):
        value = text() if callable(text) else text
        tip.show(value, event.x_root, event.y_root)
    widget.bind("<Enter>", enter, add="+")
    widget.bind("<Motion>", enter, add="+")
    widget.bind("<Leave>", lambda e: tip.hide(), add="+")
    widget.bind("<ButtonPress>", lambda e: tip.hide(immediate=True), add="+")
    widget.bind("<Destroy>", lambda e: tip.hide(immediate=True) if e.widget is widget else None, add="+")
    return tip


# ------------------------------------------------------------------ dialogs
KINDS = {"info": ("info", "accent"), "warning": ("warning", "amber"), "error": ("error", "red"),
         "question": ("question", "accent"), "danger": ("warning", "red"), "key": ("key", "accent")}


class Dialog:
    """Modal dialog with a dimmed backdrop; returns the value of the pressed button."""
    WIDTH = 480

    def __init__(self, root, palette, fnt, kind, title, message, buttons, cancel=None, entry=None):
        self.root, self.p, self.fnt = root, palette, fnt
        self.result = cancel
        self.cancel = cancel
        self.entry_var = None
        self._closing = False
        # Затемнение под диалогом: на светлых темах мягче, чтобы окно не «проваливалось» в черноту.
        self._veil = .42 if _is_dark(palette.get("background", "#000000")) else .26
        glyph, color_key = KINDS.get(kind, KINDS["info"])
        color = palette[color_key]
        self.shade = self._make_shade()
        win, key = _overlay_window(root)
        self.win = win
        w = self.WIDTH
        canvas = tk.Canvas(win, width=w, height=200, bg=key, bd=0, highlightthickness=0)
        canvas.pack()
        bg = canvas.create_image(0, 0, anchor="nw")
        canvas.create_image(26, 26, anchor="nw", image=photo(canvas, f"chip46_{color.lstrip('#').lower()}.png"))
        canvas.create_image(49, 49, image=photo(canvas, icon_name(glyph, 24, color)))
        title_id = canvas.create_text(88, 34, anchor="nw", text=title or "Ares Assistant", fill=palette["text"],
                                      font=fnt(13, "bold"), width=w - 112)
        y = max(78, canvas.bbox(title_id)[3] + 10)
        message = str(message or "")
        text_x = 88
        if len(message) > 520 or message.count("\n") > 12:
            holder = tk.Frame(canvas, bg=palette["dialog_bg"])
            box = tk.Text(holder, wrap="word", width=1, height=13, bg=palette["dialog_text_bg"], fg=palette["text_soft"], bd=0,
                          highlightthickness=1, highlightbackground=palette["dialog_text_border"],
                          highlightcolor=palette["dialog_text_border"], insertbackground=palette["text"],
                          font=fnt(10), padx=12, pady=10, spacing3=3, selectbackground=palette["selection"],
                          insertwidth=0, cursor="arrow")
            box.insert("1.0", message)
            box.configure(state="disabled")
            bar = ttk.Scrollbar(holder, command=box.yview)
            box.configure(yscrollcommand=bar.set)
            bar.pack(side="right", fill="y")
            box.pack(fill="both", expand=True)
            text_x = 26
            canvas.create_window(text_x, y, anchor="nw", window=holder, width=w - 52, height=270)
            y += 270 + 18
        elif message:
            msg_id = canvas.create_text(text_x, y - 4, anchor="nw", text=message, fill=palette["text_soft"],
                                        font=fnt(10), width=w - text_x - 26)
            y = canvas.bbox(msg_id)[3] + 18
        if entry is not None:
            self.entry_var = tk.StringVar(value=entry.get("initial", "") or "")
            field = ttk.Entry(canvas, textvariable=self.entry_var, show=entry.get("show") or "", font=fnt(11))
            field._ares_dialog = True  # its animated frame is flattened onto the dialog colour
            canvas.create_window(text_x, y, anchor="nw", window=field, width=w - text_x - 26, height=38)
            y += 38 + 18
            self._field = field
        row = tk.Frame(canvas, bg=palette["dialog_bg"])
        self._buttons = []
        for label, value, style in buttons:  # the first (primary) button ends up on the right
            button = AnimatedButton(row, text=label, style=style, command=lambda v=value: self.close(v), cursor="hand2")
            button.pack(side="right", padx=(8, 0))
            self._buttons.append(button)
        if DECORATE:
            DECORATE(canvas)
        canvas.create_window(w - 24, y + 2, anchor="ne", window=row)
        canvas.update_idletasks()
        h = y + row.winfo_reqheight() + 26
        canvas.configure(height=h)
        canvas.itemconfigure(bg, image=nine_slice(canvas, "dialog.png", w, h, 22))
        self._position(w, h)
        primary = self._buttons[0]
        win.bind("<Escape>", lambda e: self.close(self.cancel))
        win.bind("<Return>", lambda e: self.close(buttons[0][1] if entry is None else buttons[0][1]))
        try:
            win.transient(root)
        except tk.TclError:
            pass
        win.deiconify()
        win.lift()
        x, y0 = self._xy

        def appear(t):
            win.geometry(f"+{x}+{round(y0 + 14 * (1 - t))}")
            try:
                win.attributes("-alpha", t)
            except tk.TclError:
                pass
        animate(win, "in", 230, appear, ease_out_quint)
        try:
            win.grab_set()
        except tk.TclError:
            pass
        win.focus_force()
        (self._field if entry is not None else primary).focus_set()

    def _make_shade(self):
        """A dark veil over the main window while the dialog is open."""
        try:
            if not self.root.winfo_viewable():
                return None
            shade = tk.Toplevel(self.root)
            shade.withdraw()
            shade.overrideredirect(True)
            shade.configure(bg=self.p.get("shade", "#000000"))
            shade.attributes("-alpha", 0.0)
            shade.attributes("-topmost", True)  # the dialog, created next, stays above it
            shade.geometry(f"{self.root.winfo_width()}x{self.root.winfo_height()}+{self.root.winfo_rootx()}+{self.root.winfo_rooty()}")
            shade.transient(self.root)
            shade.deiconify()
            animate(shade, "in", 220, lambda t: shade.attributes("-alpha", self._veil * t), ease_out_cubic)
            return shade
        except tk.TclError:
            return None

    def _position(self, w, h):
        root = self.root
        try:
            if root.winfo_viewable():
                x = root.winfo_rootx() + (root.winfo_width() - w) // 2
                y = root.winfo_rooty() + max(40, (root.winfo_height() - h) // 2 - 30)
            else:
                raise tk.TclError
        except tk.TclError:
            x = (self.win.winfo_screenwidth() - w) // 2
            y = (self.win.winfo_screenheight() - h) // 3
        self._xy = (max(0, x), max(0, y))
        self.win.geometry(f"{w}x{h}+{self._xy[0]}+{self._xy[1] + 14}")

    def close(self, value):
        if self._closing:
            return
        self._closing = True
        self.result = value
        if self.entry_var is not None and value is not None:
            self.result = self.entry_var.get()
        win, shade = self.win, self.shade
        try:
            win.grab_release()
        except tk.TclError:
            pass

        def fade(t):
            for target, top in ((win, 1.0), (shade, self._veil)):
                if _alive(target):
                    try:
                        target.attributes("-alpha", top * (1 - t))
                    except tk.TclError:
                        pass

        def finish():
            for target in (shade, win):
                if _alive(target):
                    target.destroy()
        animate(win, "out", 140, fade, ease_in_cubic, done=finish)

    def wait(self):
        self.root.wait_window(self.win)
        return self.result


def install_dialogs(app):
    """Route tkinter.messagebox / simpledialog.askstring through themed dialogs."""
    import tkinter.messagebox as messagebox
    import tkinter.simpledialog as simpledialog
    global DECORATE
    DECORATE = getattr(app, "_dress", None)
    if getattr(messagebox, "_ares_themed", False):
        return
    native = {name: getattr(messagebox, name) for name in
              ("showinfo", "showwarning", "showerror", "askyesno", "askyesnocancel", "askokcancel", "askquestion", "askretrycancel")}
    native_askstring = simpledialog.askstring
    yes_style = "Accent.TButton"

    def usable():
        try:
            return bool(app.root.winfo_exists() and app.root.winfo_viewable() and not app.shutting_down)
        except (tk.TclError, AttributeError):
            return False

    def destructive(title, message):
        words = (str(title) + " " + str(message)).lower()
        return any(w in words for w in ("удал", "очист", "сброс", "нельзя отменить"))

    def run(name, kind, title, message, buttons, cancel, **opts):
        if not usable():
            return native[name](title, message, **opts)
        try:
            return Dialog(app.root, app.palette, app.fnt, kind, title, message, buttons, cancel).wait()
        except Exception:
            LOGGER.exception("Themed dialog failed, using native")
            return native[name](title, message, **opts)

    def showinfo(title=None, message=None, **opts):
        return run("showinfo", "info", title, message, [("Понятно", "ok", yes_style)], "ok", **opts)

    def showwarning(title=None, message=None, **opts):
        return run("showwarning", "warning", title, message, [("Понятно", "ok", yes_style)], "ok", **opts)

    def showerror(title=None, message=None, **opts):
        return run("showerror", "error", title, message, [("Закрыть", "ok", "Secondary.TButton")], "ok", **opts)

    def askyesno(title=None, message=None, **opts):
        danger = destructive(title, message)
        return run("askyesno", "danger" if danger else "question", title, message,
                   [("Да, удалить" if danger and "удал" in (str(title) + str(message)).lower() else "Да", True,
                     "Danger.TButton" if danger else yes_style), ("Нет", False, "Ghost.TButton")], False, **opts)

    def askyesnocancel(title=None, message=None, **opts):
        return run("askyesnocancel", "question", title, message,
                   [("Да", True, yes_style), ("Нет", False, "Secondary.TButton"), ("Отмена", None, "Ghost.TButton")], None, **opts)

    def askokcancel(title=None, message=None, **opts):
        return run("askokcancel", "question", title, message, [("ОК", True, yes_style), ("Отмена", False, "Ghost.TButton")], False, **opts)

    def askquestion(title=None, message=None, **opts):
        return run("askquestion", "question", title, message, [("Да", "yes", yes_style), ("Нет", "no", "Ghost.TButton")], "no", **opts)

    def askretrycancel(title=None, message=None, **opts):
        return run("askretrycancel", "warning", title, message, [("Повторить", True, yes_style), ("Отмена", False, "Ghost.TButton")], False, **opts)

    def askstring(title, prompt, **kw):
        if not usable():
            return native_askstring(title, prompt, **kw)
        try:
            kind = "key" if kw.get("show") else "question"
            entry = {"show": kw.get("show"), "initial": kw.get("initialvalue", "")}
            return Dialog(app.root, app.palette, app.fnt, kind, title, prompt,
                          [("Готово", True, yes_style), ("Отмена", None, "Ghost.TButton")], None, entry=entry).wait()
        except Exception:
            LOGGER.exception("Themed input dialog failed, using native")
            return native_askstring(title, prompt, **kw)

    for fn in (showinfo, showwarning, showerror, askyesno, askyesnocancel, askokcancel, askquestion, askretrycancel):
        setattr(messagebox, fn.__name__, fn)
    simpledialog.askstring = askstring
    messagebox._ares_themed = True


class Dropdown:
    """Replaces a ttk.Combobox popdown: unfolds downwards, gliding highlight, check on the current value."""
    ROW_H, PAD, MAX_ROWS = 32, 6, 9

    def __init__(self, combo, palette, fnt):
        self.combo, self.p, self.fnt = combo, palette, fnt
        self.win = None
        self.closed_at = 0.0
        self._rows = []
        self._active = None

    def is_open(self):
        return _alive(self.win)

    def toggle(self):
        if self.is_open() or time.monotonic() - self.closed_at < .25:
            self.close()
        else:
            self.open()
        return "break"

    def open(self):
        combo = self.combo
        if combo.instate(["disabled"]):
            return
        command = str(combo.cget("postcommand") or "")
        if command:
            combo.tk.eval(command)
        values = [str(v) for v in combo.tk.splitlist(combo.cget("values"))]
        if not values:
            return
        if PopupMenu._open is not None:
            PopupMenu._open.close(immediate=True)
        PopupMenu._open = self
        current = combo.get()
        font = tkfont.Font(font=self.fnt(10))
        w = max(combo.winfo_width(), max(font.measure(v) for v in values) + 64)
        rows = min(len(values), self.MAX_ROWS)
        list_h = rows * self.ROW_H
        h = list_h + self.PAD * 2
        win, key = _overlay_window(combo)
        self.win = win
        shell = tk.Canvas(win, width=w, height=h, bg=key, bd=0, highlightthickness=0)
        shell.pack()
        shell.create_image(0, 0, anchor="nw", image=nine_slice(shell, "menu.png", w, h, 14))
        canvas = self.canvas = tk.Canvas(shell, width=w - 4, height=list_h, bg=MENU_BG, bd=0, highlightthickness=0,
                                         takefocus=1, yscrollincrement=0)
        shell.create_window(2, self.PAD, anchor="nw", window=canvas)
        self._pill = canvas.create_image(4, -100, anchor="nw", image=nine_slice(canvas, "menu_hover.png", w - 12, self.ROW_H - 2, 9), state="hidden")
        self._rows = []
        selected = None
        for index, value in enumerate(values):
            top = index * self.ROW_H
            chosen = value == current
            text = canvas.create_text(16, top + self.ROW_H / 2, anchor="w", text=value, font=font,
                                      fill=self.p["accent"] if chosen else self.p["text_soft"])
            row = {"index": index, "value": value, "top": top, "text": text, "color": self.p["accent"] if chosen else self.p["text_soft"]}
            if chosen:
                canvas.create_image(w - 18, top + self.ROW_H / 2, anchor="e", image=photo(canvas, icon_name("check_all", 17, self.p["accent"])))
                selected = row
            self._rows.append(row)
        canvas.configure(scrollregion=(0, 0, w - 4, len(values) * self.ROW_H))
        if selected is not None and selected["top"] + self.ROW_H > list_h:
            canvas.yview_moveto(max(0, selected["top"] - list_h / 2) / (len(values) * self.ROW_H))
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        x = max(4, min(combo.winfo_rootx(), sw - w - 4))
        y = combo.winfo_rooty() + combo.winfo_height() + 4
        if y + h > sh - 44:
            y = max(4, combo.winfo_rooty() - h - 4)
        win.geometry(f"{w}x{max(1, h // 3)}+{x}+{y}")
        try:
            win.transient(combo.winfo_toplevel())
        except tk.TclError:
            pass
        win.deiconify()
        win.lift()
        combo.state(["alternate"])

        def unfold(t):  # the window grows downwards while fading in
            win.geometry(f"{w}x{max(1, round(h * (.35 + .65 * t)))}+{x}+{y}")
            try:
                win.attributes("-alpha", min(1.0, t * 1.3))
            except tk.TclError:
                pass
        animate(win, "in", 210, unfold, ease_out_quint)
        canvas.bind("<Motion>", self._motion)
        canvas.bind("<Leave>", lambda e: self._highlight(None))
        canvas.bind("<ButtonRelease-1>", lambda e: self._choose(self._row_at(e.y)))
        canvas.bind("<MouseWheel>", lambda e: (self._scroll(-e.delta / 120), "break")[1])
        for seq, delta in (("<Down>", 1), ("<Up>", -1)):
            canvas.bind(seq, lambda e, d=delta: self._step(d))
        canvas.bind("<Return>", lambda e: self._choose(self._active))
        canvas.bind("<Escape>", lambda e: self.close())
        canvas.bind("<FocusOut>", self._focus_out)
        win.focus_force()
        canvas.focus_set()
        if selected is not None:
            self._highlight(selected)

    def _scroll(self, notches):
        from ui_widgets import smooth_scroll
        smooth_scroll(self.canvas, notches, self.ROW_H)

    def _row_at(self, y):
        y = self.canvas.canvasy(y)
        for row in self._rows:
            if row["top"] <= y < row["top"] + self.ROW_H:
                return row
        return None

    def _motion(self, event):
        row = self._row_at(event.y)
        self.canvas.configure(cursor="hand2" if row else "")
        if row is not None:
            self._highlight(row)

    def _highlight(self, row):
        if row is self._active:
            return
        canvas = self.canvas
        previous, self._active = self._active, row
        if previous is not None:
            canvas.itemconfigure(previous["text"], fill=previous["color"])
        if row is None:
            canvas.itemconfigure(self._pill, state="hidden")
            return
        canvas.itemconfigure(row["text"], fill=self.p["text"] if row["color"] != self.p["accent"] else self.p["accent_hover"])
        y1 = row["top"] + 1
        if previous is None or canvas.itemcget(self._pill, "state") == "hidden":
            canvas.coords(self._pill, 4, y1)
            canvas.itemconfigure(self._pill, state="normal")
        else:
            y0 = canvas.coords(self._pill)[1]
            animate(canvas, "pill", 140, lambda t: canvas.coords(self._pill, 4, y0 + (y1 - y0) * t), ease_out_quint)
        view_top, view_h = canvas.canvasy(0), int(canvas.cget("height"))
        if row["top"] < view_top or row["top"] + self.ROW_H > view_top + view_h:
            total = len(self._rows) * self.ROW_H
            goal = row["top"] if row["top"] < view_top else row["top"] + self.ROW_H - view_h
            canvas.yview_moveto(max(0, goal) / total)

    def _step(self, delta):
        if not self._rows:
            return "break"
        current = self._rows.index(self._active) if self._active in self._rows else -1
        self._highlight(self._rows[max(0, min(len(self._rows) - 1, current + delta))])
        return "break"

    def _choose(self, row):
        if row is None:
            return "break"
        combo = self.combo
        self.close()
        if combo.get() != row["value"]:
            combo.set(row["value"])
            combo.event_generate("<<ComboboxSelected>>")
        try:
            combo.focus_set()
        except tk.TclError:
            pass
        return "break"

    def _focus_out(self, event):
        def check():
            if not _alive(self.win):
                return
            try:
                focused = self.win.focus_get()
            except (KeyError, tk.TclError):
                focused = None
            if focused is None or not str(focused).startswith(str(self.win)):
                self.close()
        self.combo._root().after(60, check)

    def close(self, immediate=False):
        win, self.win = self.win, None
        if PopupMenu._open is self:
            PopupMenu._open = None
        try:
            self.combo.state(["!alternate"])
        except tk.TclError:
            pass
        if not _alive(win):
            return
        self.closed_at = time.monotonic()
        if immediate:
            win.destroy()
            return

        def fade(t):
            try:
                win.attributes("-alpha", 1 - t)
            except tk.TclError:
                pass
        animate(win, "out", 110, fade, ease_in_cubic, done=win.destroy)


def hook_combobox(combo, palette, fnt):
    """Open the themed Dropdown instead of the native popdown (typing still works in editable fields)."""
    if getattr(combo, "_ares_dropdown", None):
        return
    dropdown = combo._ares_dropdown = Dropdown(combo, palette, fnt)

    def press(event):
        if combo.instate(["disabled"]):
            return "break"
        if not combo.instate(["readonly"]) and "textarea" in str(combo.identify(event.x, event.y)):
            return None
        combo.focus_set()
        return dropdown.toggle()

    def key(event):
        if not dropdown.is_open():
            dropdown.open()
        return "break"
    combo.bind("<ButtonPress-1>", press)
    combo.bind("<Double-Button-1>", lambda e: "break" if combo.instate(["readonly"]) else None)
    for sequence in ("<Down>", "<Alt-Down>", "<space>"):
        combo.bind(sequence, lambda e, k=key: k(e) if (combo.instate(["readonly"]) or e.keysym == "Down") else None)