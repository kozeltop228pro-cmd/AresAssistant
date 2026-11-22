"""Защита шаблонов от удаления — интерфейс вкладки «Шаблоны».

Где живёт правило. Инвариант «защищённый шаблон не удаляется» обеспечивает
core.KnowledgeBase (столбец templates.is_locked, условие is_locked=0 прямо в DELETE).
Этот модуль только показывает состояние и помогает им управлять, поэтому даже
ошибка здесь не может удалить защищённый шаблон.

Что добавляется к списку шаблонов:
  * столбец-замок слева (#0) — кнопка «защитить / снять защиту» у каждой строки;
  * два режима выбора с РАЗНЫМИ наборами отметок:
      «Удаление» — галочки означают «к удалению»; у защищённых галочка недоступна («—»);
      «Защита»  — галочки означают группу для «Защитить / Снять защиту», отмечать можно любые.
    Отметки одного режима никогда не превращаются в отметки другого: смена режима
    просто показывает другой набор;
  * индикатор «Защищено: N из M», бейдж-кнопка в редакторе, подсказки с причиной;
  * перед удалением — точный прогноз (deletion_preview), после — фактический итог
    (*_report). Если защиту включили, пока был открыт диалог, итог это покажет;
  * короткая вспышка строки при смене защиты и затухание удаляемых строк. Данные при
    этом не ждут анимацию: удаление происходит сразу, анимация лишь провожает строку.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox

from ui_assets import AnimatedButton, photo, icon_name, animate, lerp_color, ease_out_cubic, ease_in_cubic
from ui_overlays import Tooltip, attach_tooltip
from ui_widgets import SegmentedControl

MODE_DELETE, MODE_PROTECT = "delete", "protect"
TEMPLATES = ("шаблон", "шаблона", "шаблонов")
LOCK_REASON = ("Шаблон защищён: его нельзя удалить ни кнопкой, ни клавишей Delete, ни через «Удалить все». "
               "Редактировать можно. Чтобы удалить, сначала снимите защиту (замок слева).")
LOCKED_SUFFIX = " · защищён от удаления"


def plural(count, forms):
    """Русская форма слова для числа: (1 шаблон, 2 шаблона, 5 шаблонов)."""
    n = abs(int(count)) % 100
    if 11 <= n <= 14:
        return forms[2]
    n %= 10
    if n == 1:
        return forms[0]
    if 2 <= n <= 4:
        return forms[1]
    return forms[2]


def will_delete_text(count):
    """«Будет удалён 1 шаблон» / «Будут удалены 3 шаблона» / «Будет удалено 8 шаблонов»."""
    verb = plural(count, ("Будет удалён", "Будут удалены", "Будет удалено"))
    return f"{verb} {count} {plural(count, TEMPLATES)}"


def protected_stay_text(count):
    """«1 защищённый шаблон останется» / «3 защищённых шаблона останутся»."""
    return (f"{count} {plural(count, ('защищённый', 'защищённых', 'защищённых'))} "
            f"{plural(count, TEMPLATES)} {plural(count, ('останется', 'останутся', 'останутся'))}")


class TemplateLockUI:
    # ------------------------------------------------------------ отметки
    # LegacyAresAssistantApp и старые обработчики работают с self.template_checked_ids.
    # Здесь это свойство: оно возвращает набор отметок ТЕКУЩЕГО режима, поэтому
    # «Отметить видимые», «Инвертировать», пробел и клик по галочке автоматически
    # действуют на нужный набор, а присваивание в __init__ попадает в набор удаления.
    def _template_marks(self):
        marks = self.__dict__.get("_tpl_marks")
        if marks is None:
            marks = self.__dict__["_tpl_marks"] = {MODE_DELETE: set(), MODE_PROTECT: set()}
        return marks

    @property
    def template_mode(self):
        return self.__dict__.get("_tpl_mode", MODE_DELETE)

    @property
    def template_checked_ids(self):
        return self._template_marks()[self.template_mode]

    @template_checked_ids.setter
    def template_checked_ids(self, value):
        self._template_marks()[self.template_mode] = set(value)

    def _locked_template_ids(self):
        cache = getattr(self, "template_rows_cache", {}) or {}
        return {tid for tid, row in cache.items() if int(row.get("is_locked") or 0)}

    def _template_is_locked(self, template_id):
        row = (getattr(self, "template_rows_cache", {}) or {}).get(int(template_id))
        return bool(row and int(row.get("is_locked") or 0))

    def _eligible_visible_ids(self):
        """Строки, которые можно отметить в текущем режиме (в «Удалении» — без защищённых)."""
        visible = list(getattr(self, "template_visible_ids", []))
        if self.template_mode == MODE_DELETE:
            locked = self._locked_template_ids()
            return [tid for tid in visible if tid not in locked]
        return visible

    # ------------------------------------------------------------ построение
    def _build_template_protection(self, left, right, actions):
        """Вызывается из ModernUI._build_templates, когда список и редактор уже созданы."""
        p, tree = self.palette, self.templates_tree
        style = ttk.Style(tree)
        # Отдельный стиль списка: в столбце #0 только иконка замка, по центру, без отступа дерева.
        style.layout("Templates.Treeview", style.layout("Treeview"))
        style.layout("Templates.Treeview.Item", [("Treeitem.padding", {"sticky": "nswe", "children": [
            ("Treeitem.image", {"sticky": ""})]})])
        style.configure("Templates.Treeview", indent=0)
        tree.configure(style="Templates.Treeview", show=("tree", "headings"))
        tree.column("#0", width=42, minwidth=42, stretch=False, anchor="center")
        tree.heading("#0", text="", image=photo(tree, icon_name("lock", 17, p["muted"])))
        tree.column("marked", width=40, minwidth=40)
        # Порядок создания тегов задаёт приоритет в ttk (раньше = сильнее): вспышка и
        # затухание ставятся вместо «locked», а не поверх него, поэтому порядок здесь не важен.
        self._locked_row_bg = lerp_color(p["surface"], p["accent"], .075)
        tree.tag_configure("locked", background=self._locked_row_bg)
        tree.tag_configure("ares_flash", background=self._locked_row_bg)
        tree.tag_configure("ares_fade", foreground=p["text_soft"])

        # Строка режима: переключатель «Удаление | Защита» и индикатор защищённых.
        mode_row = ttk.Frame(left, style="Card.TFrame")
        mode_row.pack(fill="x", pady=(0, 8), after=actions)
        self.template_mode_switch = SegmentedControl(
            mode_row, p, self.fnt, [(MODE_DELETE, "Удаление"), (MODE_PROTECT, "Защита")],
            command=self._set_template_mode, current=self.template_mode, bg=p["surface"])
        self.template_mode_switch.pack(side="left")
        attach_tooltip(self.template_mode_switch, p, self.fnt,
                       "Режим выбора: галочки отмечают шаблоны к удалению или к защите. "
                       "У каждого режима свои отметки. Стрелки ← → переключают режим.")
        self.template_lock_count_var = tk.StringVar(value="")
        self.template_lock_count_label = ttk.Label(
            mode_row, textvariable=self.template_lock_count_var, style="Muted.Card.TLabel",
            image=photo(tree, icon_name("lock", 17, p["accent"])), compound="left")
        self.template_lock_count_label.pack(side="right")

        # Подсказка режима фиксированной высоты: смена режима не сдвигает список.
        hint = tk.Frame(left, bg=p["surface"], height=42)
        hint.pack(fill="x", pady=(0, 8), after=mode_row)
        hint.pack_propagate(False)
        self._template_hint_label = ttk.Label(hint, style="Muted.Card.TLabel", justify="left")
        self._template_protect_bar = ttk.Frame(hint, style="Card.TFrame")
        self._template_lock_buttons = []
        for text, glyph, locked in (("Защитить", "lock", True), ("Снять защиту", "unlock", False)):
            button = AnimatedButton(self._template_protect_bar, text=text, style="Secondary.TButton",
                                    command=lambda value=locked: self._lock_marked_templates(value),
                                    image=self._lock_button_images(glyph), compound="left")
            ttk.Style(button).configure(str(button.cget("style")), padding=(12, 10))
            button.configure(width=0)
            button.pack(side="left", padx=(0, 8))
            self._template_lock_buttons.append(button)
        self._template_protect_count = ttk.Label(self._template_protect_bar, style="Muted.Card.TLabel")
        self._template_protect_count.pack(side="left", padx=(4, 0))
        hint.bind("<Configure>", lambda e: self._template_hint_label.configure(wraplength=max(160, e.width - 8)), add="+")

        # Подсказки прямо в списке: замок и недоступная галочка объясняют себя.
        self._template_tip = Tooltip(tree, p, self.fnt)
        tree.bind("<Motion>", self._template_tree_motion, add="+")
        tree.bind("<Leave>", lambda e: self._template_tip.hide(), add="+")
        tree.bind("<ButtonPress>", lambda e: self._template_tip.hide(immediate=True), add="+")
        tree.bind("<Control-l>", self._toggle_selected_templates_lock)
        tree.bind("<Control-L>", self._toggle_selected_templates_lock)

        # Редактор: строка «источник · ID» + бейдж-кнопка защиты на той же линии
        # (всегда на месте, поэтому выбор шаблона не сдвигает поля редактора).
        source = next((w for w in right.winfo_children() if isinstance(w, ttk.Label)
                       and str(w.cget("textvariable")) == str(self.template_source_var)), None)
        row = ttk.Frame(right, style="Card.TFrame")
        if source is not None:
            row.pack(fill="x", pady=(0, 8), after=source)
            source.pack_forget()
        else:
            row.pack(fill="x", pady=(0, 8))
        self.template_lock_button = AnimatedButton(row, text="Защитить", style="Secondary.TButton",
                                                   command=self._toggle_editor_template_lock,
                                                   image=self._lock_button_images("unlock"), compound="left")
        ttk.Style(self.template_lock_button).configure(str(self.template_lock_button.cget("style")), padding=(14, 10))
        self.template_lock_button.configure(width=0)
        self.template_lock_button.pack(side="right", padx=(10, 0))
        label = ttk.Label(row, textvariable=self.template_source_var, style="Muted.Card.TLabel", justify="left")
        label.pack(side="left", fill="x", expand=True)
        row.bind("<Configure>", lambda e: label.configure(
            wraplength=max(120, e.width - self.template_lock_button.winfo_width() - 16)), add="+")
        attach_tooltip(self.template_lock_button, p, self.fnt, self._editor_lock_tip)
        self._sync_template_mode_ui()
        self._sync_template_lock_editor()

    def _restyle_template_protection(self):
        """Смена темы: подсветка защищённых строк — смесь цветов, её считаем заново."""
        tree = getattr(self, "templates_tree", None)
        if tree is None or not hasattr(self, "_locked_row_bg"):
            return
        p = self.palette
        self._locked_row_bg = lerp_color(p["surface"], p["accent"], .075)
        tree.tag_configure("locked", background=self._locked_row_bg)
        tree.tag_configure("ares_flash", background=self._locked_row_bg)
        tree.heading("#0", image=photo(tree, icon_name("lock", 17, p["muted"])))
        self._sync_template_mode_ui()
        self._refresh_templates()
        self._sync_template_lock_editor()

    def _lock_button_images(self, glyph):
        p = self.palette
        normal = self._padded_icon(glyph, 17, p["accent"] if glyph == "lock" else p["text_soft"], gap=8, side="right")
        hover = self._padded_icon(glyph, 17, p["accent"] if glyph == "lock" else p["text"], gap=8, side="right")
        dim = self._padded_icon(glyph, 17, p["muted_dim"], gap=8, side="right")
        return (normal, "disabled", dim, "active", hover, "focus", hover)

    # ------------------------------------------------------------ режимы
    def _set_template_mode(self, mode):
        if mode not in (MODE_DELETE, MODE_PROTECT) or mode == self.template_mode:
            return
        self.__dict__["_tpl_mode"] = mode
        switch = getattr(self, "template_mode_switch", None)
        if switch is not None and switch.current != mode:
            switch.select(mode, notify=False)
        self._sync_template_mode_ui()
        self._refresh_templates()

    def _sync_template_mode_ui(self):
        if not hasattr(self, "_template_hint_label"):
            return
        p, tree = self.palette, self.templates_tree
        if self.template_mode == MODE_DELETE:
            tree.heading("marked", text="✓", image="")
            self._template_protect_bar.pack_forget()
            self._template_hint_label.configure(
                text="Галочки отмечают шаблоны к удалению. Защищённые (с замком) отметить нельзя — "
                     "сначала снимите защиту.")
            self._template_hint_label.pack(fill="both", expand=True)
        else:
            tree.heading("marked", text="", image=photo(tree, icon_name("lock", 17, p["accent"])))
            self._template_hint_label.pack_forget()
            self._template_protect_bar.pack(fill="both", expand=True)

    # ------------------------------------------------------------ список
    def _refresh_templates(self):
        tree = getattr(self, "templates_tree", None)
        if tree is None:
            return super()._refresh_templates()
        top = tree.yview()[0] if tree.get_children() else 0.0
        super()._refresh_templates()
        if not hasattr(self, "_locked_row_bg"):
            return
        p = self.palette
        locked_ids = self._locked_template_ids()
        marks = self._template_marks()
        # Защищённый шаблон не может числиться «к удалению»: отметка снимается сразу,
        # как только защита включена (в том числе с другого устройства через импорт).
        marks[MODE_DELETE].difference_update(locked_ids)
        alive = set(getattr(self, "template_rows_cache", {}))
        for mode_marks in marks.values():
            mode_marks.intersection_update(alive)
        lock_img = photo(tree, icon_name("lock", 17, p["accent"]))
        open_img = photo(tree, icon_name("unlock", 17, p["muted_dim"]))
        deleting = self.template_mode == MODE_DELETE
        current = marks[self.template_mode]
        for template_id in self.template_visible_ids:
            iid = str(template_id)
            locked = template_id in locked_ids
            if deleting and locked:
                mark = "—"
            else:
                mark = "☑" if template_id in current else "☐"
            tags = [tag for tag in (tree.item(iid, "tags") or ()) if tag not in ("locked", "ares_flash", "ares_fade")]
            if locked:
                tags.append("locked")
            tree.item(iid, image=lock_img if locked else open_img, tags=tuple(tags))
            tree.set(iid, "marked", mark)
        total = len(alive)
        shown = len(self.template_visible_ids)
        self.template_lock_count_var.set(f"Защищено: {len(locked_ids)} из {total}")
        summary = self.template_summary_var.get()
        if locked_ids and "защищено" not in summary:
            self.template_summary_var.set(f"{summary} · защищено {len(locked_ids)}")
        if deleting:
            text = (f"К удалению отмечено: {len(current)} · показано: {shown} из {total}" if current
                    else f"Показано: {shown} из {total}")
        else:
            text = (f"К защите отмечено: {len(current)} · показано: {shown} из {total}" if current
                    else f"Режим защиты · показано: {shown} из {total}")
            self._template_protect_count.configure(text=f"Отмечено: {len(current)}" if current else "Отметьте галочками")
        self.template_selection_var.set(text)
        # Пересборка списка не должна прыгать к началу (например, после смены защиты).
        if top:
            tree.yview_moveto(top)

    def _template_tree_press(self, event):
        tree = self.templates_tree
        region = tree.identify_region(event.x, event.y)
        column = tree.identify_column(event.x)
        if region == "heading":
            if column == "#0":
                return "break"
            if column == "#1":
                # Заголовок галочек: отметить/снять все ДОПУСТИМЫЕ видимые строки.
                eligible = self._eligible_visible_ids()
                marks = self.template_checked_ids
                all_checked = bool(eligible) and all(tid in marks for tid in eligible)
                self._mark_visible_templates(not all_checked)
                return "break"
        row_id = tree.identify_row(event.y)
        if region in {"cell", "tree"} and row_id:
            template_id = int(row_id)
            if column == "#0":
                self._set_templates_lock([template_id], not self._template_is_locked(template_id))
                return "break"
            if column == "#1" and self.template_mode == MODE_DELETE and self._template_is_locked(template_id):
                tree.selection_set(row_id)
                tree.focus(row_id)
                self._template_selected()
                self._flash_template_rows([row_id], True)
                self.status_var.set("Шаблон защищён — отметить к удалению нельзя. Снимите защиту замком слева.")
                self._template_tip.hide(immediate=True)
                self._template_tip.show(LOCK_REASON, event.x_root, event.y_root)
                return "break"
        return super()._template_tree_press(event)

    def _template_tree_motion(self, event):
        tree = self.templates_tree
        region = tree.identify_region(event.x, event.y)
        column = tree.identify_column(event.x)
        row_id = tree.identify_row(event.y)
        text = ""
        if region in {"cell", "tree"} and row_id:
            locked = self._template_is_locked(int(row_id))
            if column == "#0":
                text = ("Защищён от удаления. Нажмите, чтобы снять защиту" if locked
                        else "Нажмите, чтобы защитить шаблон от удаления")
            elif column == "#1" and locked and self.template_mode == MODE_DELETE:
                text = LOCK_REASON
        elif region == "heading" and column == "#0":
            text = "Защита от удаления · Ctrl+L — защитить или снять защиту с выделенных"
        elif region == "heading" and column == "#1":
            text = ("Отметить все видимые к удалению (кроме защищённых)" if self.template_mode == MODE_DELETE
                    else "Отметить все видимые для защиты")
        tree.configure(cursor="hand2" if column in ("#0", "#1") and region in {"cell", "tree", "heading"} else "")
        if text:
            self._template_tip.show(text, event.x_root, event.y_root)
        else:
            self._template_tip.hide()

    def _toggle_selected_template_marks(self, event=None):
        selected = [int(item) for item in self.templates_tree.selection()]
        if self.template_mode == MODE_DELETE:
            locked = self._locked_template_ids()
            skipped = [tid for tid in selected if tid in locked]
            selected = [tid for tid in selected if tid not in locked]
            if skipped:
                self.status_var.set(f"Защищённые пропущены: {len(skipped)} — их нельзя отметить к удалению")
        if not selected:
            return "break"
        marks = self.template_checked_ids
        should_mark = any(tid not in marks for tid in selected)
        for tid in selected:
            (marks.add if should_mark else marks.discard)(tid)
        self._refresh_templates()
        return "break"

    def _mark_visible_templates(self, marked):
        eligible = self._eligible_visible_ids()
        if marked:
            self.template_checked_ids.update(eligible)
        else:
            self.template_checked_ids.difference_update(self.template_visible_ids)
        self._refresh_templates()

    def _invert_visible_templates(self):
        marks = self.template_checked_ids
        for tid in self._eligible_visible_ids():
            (marks.discard if tid in marks else marks.add)(tid)
        self._refresh_templates()

    # ------------------------------------------------------------ защита
    def _lock_targets(self):
        """Кому меняем защиту: отметки режима «Защита», иначе выделенные строки."""
        if self.template_mode == MODE_PROTECT and self.template_checked_ids:
            return sorted(self.template_checked_ids)
        selected = sorted({int(item) for item in self.templates_tree.selection()})
        if selected:
            return selected
        if self.template_mode == MODE_DELETE:
            return sorted(self._template_marks()[MODE_DELETE])
        return []

    def _lock_marked_templates(self, locked):
        ids = self._lock_targets()
        if not ids:
            messagebox.showinfo("Защита шаблонов",
                                "Выделите шаблоны в списке или отметьте их галочками в режиме «Защита».")
            return
        self._set_templates_lock(ids, locked)

    def _toggle_selected_templates_lock(self, event=None):
        ids = sorted({int(item) for item in self.templates_tree.selection()})
        if ids:
            locked = self._locked_template_ids()
            self._set_templates_lock(ids, not all(tid in locked for tid in ids))
        return "break"

    def _toggle_editor_template_lock(self):
        template_id = self.template_selected_id
        if not template_id:
            return
        data = self.knowledge.get_template(template_id)
        if data is None:
            return
        self._set_templates_lock([template_id], not int(data.get("is_locked") or 0))

    def _set_templates_lock(self, ids, locked):
        ids = sorted({int(value) for value in ids})
        if not ids:
            return 0
        changed = self.knowledge.set_templates_locked(ids, locked)
        if locked:
            self._template_marks()[MODE_DELETE].difference_update(ids)
        self._refresh_templates()
        self._sync_template_lock_editor()
        self._flash_template_rows([str(tid) for tid in ids], locked)
        same = len(ids) - changed
        if locked:
            message = f"Защищено: {changed} {plural(changed, TEMPLATES)}" if changed else "Эти шаблоны уже защищены"
            if changed and same:
                message += f" · уже были защищены: {same}"
        else:
            message = f"Защита снята: {changed} {plural(changed, TEMPLATES)}" if changed else "Эти шаблоны и так без защиты"
        self.status_var.set(message)
        self._toast(message, "ok" if changed else "info")
        return changed

    def _flash_template_rows(self, iids, locked):
        """Короткая вспышка строк при смене защиты (≈0,5 с), затем обычный вид строки."""
        tree, p = self.templates_tree, self.palette
        # Строки от прерванной прошлой вспышки возвращаем в нормальный вид.
        for iid in tree.tag_has("ares_flash"):
            self._restore_row_tags(iid)
        iids = [iid for iid in iids if tree.exists(iid)]
        if not iids:
            return
        start = lerp_color(p["surface"], p["accent"], .5)
        end = self._locked_row_bg if locked else p["surface"]
        for iid in iids:
            tags = [tag for tag in (tree.item(iid, "tags") or ()) if tag != "locked"] + ["ares_flash"]
            tree.item(iid, tags=tuple(tags))

        def done():
            for iid in iids:
                self._restore_row_tags(iid)
        animate(tree, "lock_flash", 520, lambda t: tree.tag_configure("ares_flash", background=lerp_color(start, end, t)),
                ease_out_cubic, done=done)

    def _restore_row_tags(self, iid):
        tree = self.templates_tree
        if not tree.exists(iid):
            return
        tags = [tag for tag in (tree.item(iid, "tags") or ()) if tag not in ("ares_flash", "locked")]
        if self._template_is_locked(int(iid)):
            tags.append("locked")
        tree.item(iid, tags=tuple(tags))

    def _fade_out_template_rows(self, iids, done):
        """Удалённые строки гаснут ~0,2 с и исчезают при следующей пересборке списка."""
        tree, p = self.templates_tree, self.palette
        iids = [iid for iid in iids if tree.exists(iid)]
        if not iids:
            done()
            return
        tree.tag_configure("ares_fade", foreground=p["text_soft"])
        for iid in iids:
            tree.item(iid, tags=("ares_fade",), image="")
        animate(tree, "row_fade", 200,
                lambda t: tree.tag_configure("ares_fade", foreground=lerp_color(p["text_soft"], p["surface"], t)),
                ease_in_cubic, done=done)

    # ------------------------------------------------------------ редактор
    def _template_selected(self):
        super()._template_selected()
        self._sync_template_lock_editor()

    def new_template(self):
        super().new_template()
        self._sync_template_lock_editor()

    def _sync_template_lock_editor(self):
        button = getattr(self, "template_lock_button", None)
        if button is None:
            return
        data = self.knowledge.get_template(self.template_selected_id) if self.template_selected_id else None
        source = self.template_source_var.get().replace(LOCKED_SUFFIX, "")
        if data is None:
            button.configure(text="Защитить", image=self._lock_button_images("unlock"))
            button.state(["disabled"])
            self.template_source_var.set(source)
            return
        locked = bool(int(data.get("is_locked") or 0))
        button.state(["!disabled"])
        button.configure(text="Защищён" if locked else "Защитить",
                         image=self._lock_button_images("lock" if locked else "unlock"))
        self.template_source_var.set(source + (LOCKED_SUFFIX if locked else ""))

    def _editor_lock_tip(self):
        if not self.template_selected_id:
            return "Сначала сохраните шаблон — потом его можно будет защитить"
        if self._template_is_locked(self.template_selected_id):
            return LOCK_REASON.replace(" (замок слева)", "") + " Нажмите, чтобы снять защиту."
        return "Защитить шаблон: его нельзя будет удалить, пока защита включена. Редактировать можно."

    # ------------------------------------------------------------ контекстное меню
    def _template_context_menu(self, event):
        tree = self.templates_tree
        menu = getattr(self, "_template_popup_menu", None)
        row_id = tree.identify_row(event.y)
        if tree.identify_region(event.x, event.y) not in {"cell", "tree"} or not row_id:
            if menu is not None:
                menu.unpost()
            return "break"
        template_id = int(row_id)
        data = self.knowledge.get_template(template_id)
        if data is None:
            return "break"
        tree.focus_set()
        tree.selection_set(row_id)
        tree.focus(row_id)
        self._template_selected()
        if menu is None:
            menu = self._template_popup_menu = self._popup_menu(tree)
        menu.delete(0, "end")
        locked = bool(int(data.get("is_locked") or 0))
        # ID строки захватывается здесь: последующая смена фокуса не меняет цель команды.
        menu.add_command(label="Удалить", command=lambda tid=template_id: self._delete_template_from_menu(tid),
                         icon="trash", state="disabled" if locked else "normal",
                         hint="защищён" if locked else "")
        menu.add_separator()
        if locked:
            menu.add_command(label="Снять защиту", icon="unlock",
                             command=lambda tid=template_id: self._set_templates_lock([tid], False))
        else:
            menu.add_command(label="Защитить от удаления", icon="lock",
                             command=lambda tid=template_id: self._set_templates_lock([tid], True))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    # ------------------------------------------------------------ удаление
    def _deletion_targets(self):
        """В режиме «Удаление» — отмеченные (если есть), иначе выделенные строки."""
        marks = self._template_marks()[MODE_DELETE]
        if self.template_mode == MODE_DELETE and marks:
            return sorted(marks)
        return sorted({int(item) for item in self.templates_tree.selection()})

    def _delete_template_from_menu(self, template_id):
        template = self.knowledge.get_template(template_id)
        if template is None:
            return
        if int(template.get("is_locked") or 0):
            messagebox.showinfo("Шаблон защищён",
                                f"«{template['name']}» защищён от удаления.\n\n"
                                "Редактировать его можно. Чтобы удалить, сначала снимите защиту.")
            return
        if not messagebox.askyesno(
            "Удаление шаблона",
            f"Удалить шаблон «{template['name']}» вместе с его обучающими примерами?\n\nЭто действие нельзя отменить.",
        ):
            return
        preview = {"deletable": 1, "protected": 0, "missing": 0}
        # Защита перепроверяется внутри delete_templates_report в момент удаления.
        self._finish_template_delete([template_id], self.knowledge.delete_templates_report([template_id]), preview)

    def _delete_marked_templates(self):
        ids = self._deletion_targets()
        if not ids:
            messagebox.showinfo("Шаблоны", "Отметь галочками шаблоны для удаления" if self.template_mode == MODE_DELETE
                                else "Выделите шаблоны в списке или перейдите в режим «Удаление»")
            return
        preview = self.knowledge.deletion_preview(ids)
        deletable, protected = preview["deletable"], preview["protected"]
        if not deletable:
            if protected:
                messagebox.showinfo(
                    "Удалять нечего",
                    f"{'Выбранный шаблон защищён' if protected == 1 else f'Все выбранные шаблоны ({protected}) защищены'}"
                    " — удаление недоступно.\n\nСнимите защиту, если их действительно нужно удалить.")
            else:
                messagebox.showinfo("Удалять нечего", "Выбранные шаблоны уже удалены.")
            self._refresh_all()
            return
        message = f"{will_delete_text(deletable)} вместе с обучающими примерами."
        if protected:
            message += f"\n{protected_stay_text(protected)} без изменений."
        message += "\n\nЭто действие нельзя отменить."
        if not messagebox.askyesno("Удаление шаблонов", message):
            return
        self._finish_template_delete(ids, self.knowledge.delete_templates_report(ids), preview)

    def _delete_all_templates(self):
        preview = self.knowledge.deletion_preview(None)
        deletable, protected = preview["deletable"], preview["protected"]
        if not deletable and not protected:
            messagebox.showinfo("Удалять нечего", "Библиотека шаблонов уже пуста.")
            return
        if not deletable:
            messagebox.showinfo(
                "Удалять нечего",
                f"Все {protected} {plural(protected, TEMPLATES)} защищены — «Удалить все» их не затронет.\n\n"
                "Снимите защиту с тех, что действительно нужно удалить.")
            return
        message = f"{will_delete_text(deletable)} и их примеры."
        if protected:
            message += f"\n{protected_stay_text(protected)} без изменений."
        message += "\nТочные правила, история и настройки останутся.\n\nПродолжить?"
        if not messagebox.askyesno("Удалить все шаблоны", message):
            return
        final = f"Точно удалить {deletable} {plural(deletable, TEMPLATES)}?"
        if protected:
            final += f" Защищённые ({protected}) останутся."
        if not messagebox.askyesno("Последнее подтверждение", final):
            return
        before = sorted(getattr(self, "template_rows_cache", {}) or {})
        self._finish_template_delete(before, self.knowledge.delete_all_templates_report(), preview)

    def _finish_template_delete(self, ids, report, preview):
        """Показывает фактический итог и провожает удалённые строки затуханием."""
        alive = {int(row["id"]) for row in self.knowledge.template_rows()}
        gone = [tid for tid in ids if tid not in alive]
        for marks in self._template_marks().values():
            marks.difference_update(gone)
        if self.template_selected_id and self.template_selected_id not in alive:
            self.new_template()
        deleted, protected = report["deleted"], report["protected"]
        message = f"Удалено: {deleted} {plural(deleted, TEMPLATES)}"
        if protected:
            message += f" · защищённых сохранено: {protected}"
        if deleted < preview.get("deletable", 0):
            message += " · часть шаблонов защитили до подтверждения — они сохранены"
        self.status_var.set(message)
        self._toast(message, "ok" if deleted else "info")
        self._fade_out_template_rows([str(tid) for tid in gone], self._refresh_all)

    def _cleanup_suspicious_templates(self):
        locked = self.knowledge.locked_template_count()
        count = self.knowledge.cleanup_suspicious_templates()
        extra = f"\nЗащищённые шаблоны ({locked}) не проверялись и не изменились." if locked else ""
        if count:
            messagebox.showinfo(
                "Очистка завершена",
                f"Убрано подозрительных или слишком коротких шаблонов: {count}.\n"
                "Их конкретные ситуации сохранены как точные правила." + extra,
            )
        else:
            messagebox.showinfo("Очистка завершена", "Подозрительных шаблонов не найдено." + extra)
        self._refresh_all()
