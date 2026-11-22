"""Ares 2.9.9.6 with the «Aurora» interface: same sections and logic, new look and motion."""
from __future__ import annotations

import os
import time
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import ttk, font as tkfont
from ui_assets import (AnimatedButton, AnimatedSwitch, animate_field, icon_name, photo, nine_slice, ASSETS, animate,
                       lerp_color, ease_out_cubic, ease_out_quint, ease_in_cubic)
from ui_widgets import (NavRail, SearchBox, StatusPill, GlowProgress, CountLabel, ActionTile, Toast,
                        DashboardHero, SegmentedTabs, smooth_scroll, tree_hover)
from repunish_ui import RepunishUI
from voice_phrases_ui import VoicePhrasesUI
from templates_lock_ui import TemplateLockUI
from ui_overlays import PopupMenu, install_dialogs, hook_combobox
import ui_widgets
from themes import full_palette
from theme_ui import ThemeUI

# Палитра активной темы. Это один и тот же словарь на всё время работы: при смене темы
# он обновляется на месте (ThemeUI._apply_theme_tokens), а интерфейс перестраивается.
P = full_palette("aurora")
ui_widgets.PALETTE = P
TRANSPARENT_KEY = "#010203"
# Служебные коды истории → понятные подписи (код остаётся в базе без изменений).
ACTION_LABELS = {
    "auto_queued": "Автоответ", "auto_sent": "Отправлено", "draft_queued": "Черновик", "ignored": "Без ответа",
    "unknown": "Нужен ответ", "unknown_filtered": "Нужен ответ · фильтр", "manual_required": "Ручной ответ",
    "close_queued": "Закрытие", "close_preview_only": "Закрытие · без TG", "close_rule_disabled": "Закрытие выкл.",
    "preview_only": "Только просмотр", "learn_waiting": "Ждёт обучения", "learn_filtered": "Не для обучения",
}


class ModernUI(ThemeUI, TemplateLockUI, VoicePhrasesUI, RepunishUI):
    def _configure_root(self):
        super()._configure_root()
        self.root.title("Ares Assistant  •  2.9.9.6")
        self.root.configure(bg=P["background"])
        self.root.minsize(min(1080,self.root.winfo_screenwidth()-20),
                          min(660,self.root.winfo_screenheight()-60))
        # On Windows iconphoto and iconbitmap fight: together they leave Python's icon on
        # the taskbar and a blank one in the caption. The .ico alone is used there and is
        # refined by _apply_window_icons once the frame window exists.
        self._app_icons = [photo(self.root, f'app_{size}.png') for size in (16,32,48,64,128,256)]
        self._win_icons = []
        if os.name == 'nt':
            try:
                self.root.iconbitmap(default=str(ASSETS / 'app.ico'))
            except tk.TclError:
                self.root.iconphoto(True, *self._app_icons)
        else:
            self.root.iconphoto(True, *self._app_icons)
        self._command_window = None
        self._toast_job = None
        self._toaster = None
        self._history_job = None
        self._history_sort = None
        self._ui_alive = True
        try:
            # The window fades in once the interface is built (see _reveal_window).
            self.root.attributes("-alpha", 0.0)
        except tk.TclError:
            pass

    def fnt(self, size, weight="normal"):
        if weight == "bold":
            return (self.head_font, size, self._head_weight)
        return (self.ui_font, size)

    def _configure_style(self):
        # Тема применяется до первой отрисовки: окно ещё прозрачно, поэтому чужая тема не мелькает.
        self._apply_theme_tokens(self._configured_theme())
        # Живая палитра (тот же словарь P): компоненты видят новые цвета сразу после смены темы.
        self.palette = P
        families = set(tkfont.families(self.root))
        self.ui_font = next((f for f in ("Segoe UI Variable Text", "Segoe UI", "DejaVu Sans") if f in families), "TkDefaultFont")
        head = next((f for f in ("Segoe UI Variable Display Semib", "Segoe UI Semibold") if f in families), None)
        self.head_font, self._head_weight = (head, "normal") if head else (self.ui_font, "bold")
        f = self.ui_font
        style = self.style = ttk.Style(self.root)
        style.theme_use("clam")
        self.root.option_add("*Menu.Font", (f,10))
        for name,value in {"background":P["field"],"foreground":P["text_soft"],"selectBackground":P["selection"],
                           "selectForeground":P["text"],"relief":"flat","highlightThickness":0,"borderWidth":0}.items():
            self.root.option_add("*Listbox."+name,value)
            self.root.option_add("*TCombobox*Listbox."+name,value)
        self.root.option_add("*TCombobox*Listbox.font",(f,10))
        self.root.option_add("*TCombobox*Listbox.activeStyle","none")
        self.root.option_add("*TCombobox*Listbox.selectBorderWidth",0)
        self.root.option_add("*TCombobox*Listbox.selectBackground",P["list_select"])
        self.root.option_add("*TCombobox*Listbox.cursor","hand2")
        style.configure(".",font=(f,10),background=P["background"],foreground=P["text"],lightcolor=P["border_soft"],darkcolor=P["border_soft"],
                        bordercolor=P["border_soft"],fieldbackground=P["field"],troughcolor=P["surface"],
                        selectbackground=P["selection"],selectforeground=P["text"],insertcolor=P["text"],focuscolor=P["accent"])
        style.map('.', background=[], lightcolor=[], darkcolor=[], bordercolor=[])
        for name,bg in [("TFrame",P["background"]),("Card.TFrame",P["surface"]),
                        ("Panel.TFrame",P["surface"]),("PanelHover.TFrame",P["surface"]),("Hero.TFrame",P["surface_high"]),
                        ("Toolbar.TFrame",P["surface"]),("HeroInner.TFrame",P["surface_high"]),("Sidebar.TFrame",P["sidebar"])]:
            style.configure(name,background=bg,borderwidth=0)
        for key,bgstyle in [("panel","Panel.TFrame"),("panel_hover","PanelHover.TFrame"),("hero","Hero.TFrame")]:
            self._element(style,"Ares."+key,"image",photo(self.root,key+".png"),border=18,padding=0,sticky="nsew",width=40,height=40)
            style.layout(bgstyle,[("Ares."+key,{"sticky":"nsew"})])
        head = self.fnt
        labels = {
            "TLabel":(P["background"],P["text"],(f,10)),
            "Card.TLabel":(P["surface"],P["text_soft"],(f,10)),
            "Section.TLabel":(P["surface"],P["text"],head(11,"bold")),
            "Muted.Card.TLabel":(P["surface"],P["muted"],(f,9)),
            "Sub.TLabel":(P["background"],P["muted"],(f,9)),
            "Eyebrow.TLabel":(P["background"],P["accent"],head(8,"bold")),
            "Title.TLabel":(P["background"],P["text"],head(24,"bold")),
            "HeroTitle.TLabel":(P["surface_high"],P["text"],head(21,"bold")),
            "HeroSub.TLabel":(P["surface_high"],P["muted"],(f,10)),
            "Hero.TLabel":(P["surface_high"],P["text"],(f,10)),
            "Pill.TLabel":(P["selection"],P["accent"],head(9,"bold")),
            "Status.TLabel":(P["background"],P["muted"],(f,9)),
            "Number.TLabel":(P["surface"],P["text"],head(28,"bold")),
            "Sidebar.TLabel":(P["sidebar"],P["muted"],(f,9)),
        }
        for name,(bg,fg,font) in labels.items():
            style.configure(name,background=bg,foreground=fg,font=font)
        style.configure("Pill.TLabel",padding=(11,6))
        disabled = photo(self.root,"button_disabled.png")
        for name,kind,fg,hover_fg in [("Accent.TButton","accent",P["on_accent"],P["on_accent"]),
                                      ("Secondary.TButton","button",P["text_soft"],P["text"]),
                                      ("Success.TButton","button",P["green"],P["green"]),
                                      ("Warning.TButton","button",P["amber"],P["amber"]),
                                      ("Danger.TButton","danger",P["red"],P["danger_hover"]),
                                      ("Ghost.TButton","ghost",P["muted"],P["text"]),
                                      ("TButton","button",P["text_soft"],P["text"])]:
            element="Ares."+name
            self._element(style,element,"image",photo(self.root,f"{kind}_frame_0.png"),("disabled",disabled),
                                 ("pressed",photo(self.root,f"{kind}_pressed.png")),("active",photo(self.root,f"{kind}_frame_11.png")),
                                 border=14,padding=0,sticky="nsew",width=40,height=40)
            style.layout(name,[(element,{"sticky":"nsew","children":[("Button.padding",{"sticky":"nsew","children":[("Button.label",{"sticky":"nsew"})]})]})])
            style.configure(name,background=P["surface"],foreground=fg,padding=(16,10),
                            font=head(9,"bold"),anchor="center")
            style.map(name,foreground=[("disabled",P["muted_dim"]),("active",hover_fg),("focus",hover_fg)],
                      background=[('disabled',P['surface']),('!disabled',P['surface'])])
        # Segmented tabs inside cards and on pages.
        self._element(style,"Ares.tab","image",photo(self.root,"tab.png"),("selected",photo(self.root,"tab_selected.png")),
                             ("active",photo(self.root,"tab_hover.png")),border=10,padding=0,sticky="nsew",width=28,height=28)
        tab_layout=[("Ares.tab",{"sticky":"nswe","children":[("Notebook.padding",{"side":"top","sticky":"nswe","children":[("Notebook.label",{"side":"top","sticky":""})]})]})]
        for prefix,bg in (("",P["surface"]),("Page.",P["background"])):
            style.configure(prefix+"TNotebook",background=bg,borderwidth=0,tabmargins=(0,0,0,10),bordercolor=bg,lightcolor=bg,darkcolor=bg)
            style.layout(prefix+"TNotebook.Tab",tab_layout)
            style.configure(prefix+"TNotebook.Tab",background=bg,foreground=P["muted"],font=(f,9),padding=(14,7),borderwidth=0,focusthickness=0)
            style.map(prefix+"TNotebook.Tab",foreground=[("selected",P["text"]),("active",P["text_soft"])],
                      background=[("selected",bg),("!selected",bg)],expand=[("selected",(0,0,0,0))])
        # Notebooks whose tabs are drawn by SegmentedTabs keep only their client area.
        for name,bg in (("Bare.TNotebook",P["surface"]),("BarePage.TNotebook",P["background"])):
            style.configure(name,background=bg,borderwidth=0,tabmargins=0,bordercolor=bg,lightcolor=bg,darkcolor=bg)
            style.layout(name+".Tab",[])
            style.layout(name,[("Notebook.client",{"sticky":"nsew"})])
        style.configure("Workspace.TNotebook",background=P["background"],borderwidth=0,tabmargins=0,bordercolor=P["background"],lightcolor=P["background"],darkcolor=P["background"])
        style.layout("Workspace.TNotebook.Tab",[])
        style.layout("Workspace.TNotebook",[("Notebook.client",{"sticky":"nsew"})])
        # Lists.
        style.layout("Treeview",[("Treeview.treearea",{"sticky":"nswe"})])
        style.layout("Treeview.Item",[("Treeitem.padding",{"sticky":"nswe","children":[
            ("Treeitem.indicator",{"side":"left","sticky":""}),("Treeitem.image",{"side":"left","sticky":""}),
            ("Treeitem.text",{"side":"left","sticky":""})]})])
        style.configure("Treeview",bordercolor=P["surface"],lightcolor=P["surface"],darkcolor=P["surface"],background=P["surface"],
                        fieldbackground=P["surface"],foreground=P["text_soft"],font=(f,9),rowheight=36,borderwidth=0,relief="flat")
        style.map("Treeview",background=[("selected",P["selection"])],foreground=[("selected",P["text"])])
        self._element(style,"Ares.heading","image",photo(self.root,"heading.png"),("active",photo(self.root,"heading_hover.png")),
                             border=4,padding=0,sticky="nsew",width=24,height=24)
        style.layout("Treeview.Heading",[("Ares.heading",{"sticky":"nswe","children":[("Treeheading.padding",{"sticky":"nswe","children":[
            ("Treeheading.image",{"side":"right","sticky":""}),("Treeheading.text",{"sticky":"we"})]})]})])
        style.configure("Treeview.Heading",background=P["surface_high"],foreground=P["muted"],font=head(8,"bold"),padding=(10,9),relief="flat",borderwidth=0)
        style.map("Treeview.Heading",foreground=[("active",P["text_soft"])])
        # Rounded inputs with a focus glow.
        self._element(style,"Ares.field","image",photo(self.root,"field.png"),("disabled",photo(self.root,"field_disabled.png")),
                             ("focus",photo(self.root,"field_focus.png")),("hover",photo(self.root,"field_hover.png")),
                             border=12,padding=0,sticky="nsew",width=36,height=36)
        self._element(style,"Ares.combo.downarrow","image",photo(self.root,"chevron.png"),("disabled",photo(self.root,"chevron_disabled.png")),("alternate",photo(self.root,"chevron_open.png")),
                             ("pressed",photo(self.root,"chevron_hover.png")),("hover",photo(self.root,"chevron_hover.png")),sticky="")
        self._element(style,"Ares.spin.uparrow","image",photo(self.root,"spin_up.png"),("pressed",photo(self.root,"spin_up_hover.png")),
                             ("active",photo(self.root,"spin_up_hover.png")),sticky="")
        self._element(style,"Ares.spin.downarrow","image",photo(self.root,"spin_down.png"),("pressed",photo(self.root,"spin_down_hover.png")),
                             ("active",photo(self.root,"spin_down_hover.png")),sticky="")
        style.layout("TEntry",[("Ares.field",{"sticky":"nswe","children":[("Entry.padding",{"sticky":"nswe","children":[("Entry.textarea",{"sticky":"nswe"})]})]})])
        style.layout("TCombobox",[("Ares.field",{"sticky":"nswe","children":[("Ares.combo.downarrow",{"side":"right","sticky":"ns"}),
            ("Combobox.padding",{"sticky":"nswe","children":[("Combobox.textarea",{"sticky":"nswe"})]})]})])
        style.layout("TSpinbox",[("Ares.field",{"sticky":"nswe","children":[("null",{"side":"right","sticky":"","children":[
            ("Ares.spin.uparrow",{"side":"top","sticky":"e"}),("Ares.spin.downarrow",{"side":"bottom","sticky":"e"})]}),
            ("Spinbox.padding",{"sticky":"nswe","children":[("Spinbox.textarea",{"sticky":"nswe"})]})]})])
        for name,padding in (("TEntry",(12,9)),("TCombobox",(12,9,4,9)),("TSpinbox",(12,8,6,8))):
            style.configure(name,fieldbackground=P["field"],background=P["surface"],foreground=P["text"],
                            insertcolor=P["accent"],insertwidth=2,arrowcolor=P["muted"],bordercolor=P["border"],
                            lightcolor=P["field"],darkcolor=P["field"],padding=padding,relief="flat",
                            selectbackground=P["selection"],selectforeground=P["text"])
            style.map(name,fieldbackground=[("readonly",P["field"]),("disabled",P["surface"])],
                      selectbackground=[("readonly focus",P["field_focus_fill"]),("readonly",P["field"])],selectforeground=[("readonly",P["text"])],
                      foreground=[("readonly",P["text"]),("disabled",P["muted_dim"])])
        # The hero selector sits on the gradient, so it gets its own flattened field.
        self._element(style,"Ares.herofield","image",photo(self.root,"field_hero.png"),("disabled",photo(self.root,"field_hero_disabled.png")),
                             ("focus",photo(self.root,"field_hero_focus.png")),("hover",photo(self.root,"field_hero_hover.png")),
                             border=12,padding=0,sticky="nsew",width=36,height=36)
        style.layout("Hero.TCombobox",[("Ares.herofield",{"sticky":"nswe","children":[("Ares.combo.downarrow",{"side":"right","sticky":"ns"}),
            ("Combobox.padding",{"sticky":"nswe","children":[("Combobox.textarea",{"sticky":"nswe"})]})]})])
        style.configure("Hero.TCombobox",background=P["hero_backdrop"])
        style.configure("ComboboxPopdownFrame",background=P["field"],bordercolor=P["border"],lightcolor=P["border"],darkcolor=P["border"],relief="solid",borderwidth=1)
        style.layout("Palette.TEntry",[("Entry.padding",{"sticky":"nswe","children":[("Entry.textarea",{"sticky":"nswe"})]})])
        style.configure("Palette.TEntry",background=P["palette_surface"],fieldbackground=P["palette_surface"],foreground=P["text"],
                        insertcolor=P["accent"],insertwidth=2,padding=0,selectbackground=P["selection"])
        for name,bg in [("Dark.TCheckbutton",P["background"]),("DarkCard.TCheckbutton",P["surface"])]:
            style.configure(name,background=bg,foreground=P["text_soft"],font=(f,9),padding=(0,5),focusthickness=0)
            style.map(name,background=[("active",bg)],foreground=[("active",P["text"]),("disabled",P["muted_dim"])],
                      indicatorbackground=[("selected",P["accent"]),("!selected",P["field"])],
                      indicatorforeground=[("selected",P["sidebar"])])
        style.configure("TSeparator",background=P["border_soft"])
        # Thin scrollbars without arrows: a rounded thumb that brightens under the pointer.
        self._element(style,"Ares.Vertical.Scrollbar.thumb","image",photo(self.root,"thumb_v.png"),
                             ("pressed",photo(self.root,"thumb_v_pressed.png")),("active",photo(self.root,"thumb_v_hover.png")),
                             border=(4,7,4,7),padding=0,sticky="nsew",width=12,height=24)
        self._element(style,"Ares.Horizontal.Scrollbar.thumb","image",photo(self.root,"thumb_h.png"),
                             ("pressed",photo(self.root,"thumb_h_pressed.png")),("active",photo(self.root,"thumb_h_hover.png")),
                             border=(7,4,7,4),padding=0,sticky="nsew",width=24,height=12)
        style.layout("Vertical.TScrollbar",[("Vertical.Scrollbar.trough",{"sticky":"ns","children":[
            ("Ares.Vertical.Scrollbar.thumb",{"expand":"1","sticky":"nswe"})]})])
        style.layout("Horizontal.TScrollbar",[("Horizontal.Scrollbar.trough",{"sticky":"ew","children":[
            ("Ares.Horizontal.Scrollbar.thumb",{"expand":"1","sticky":"nswe"})]})])
        for prefix,bg in (("",P["surface"]),("Page.",P["background"])):
            for orient in ("Vertical.TScrollbar","Horizontal.TScrollbar"):
                style.configure(prefix+orient,background=bg,troughcolor=bg,borderwidth=0,bordercolor=bg,
                                lightcolor=bg,darkcolor=bg,gripcount=0,arrowsize=12)
        style.configure("TProgressbar",background=P["accent"],troughcolor=P["surface_high"],borderwidth=0,thickness=3)

    # ---------------------------------------------------------------- shell
    def _build_ui(self):
        self.shell=ttk.Frame(self.root)
        self.shell.pack(fill="both",expand=True)
        sidebar=ttk.Frame(self.shell,style="Sidebar.TFrame",width=222)
        sidebar.pack(side="left",fill="y")
        sidebar.pack_propagate(False)
        tk.Frame(self.shell,width=1,bg=P["shell_divider"]).pack(side="left",fill="y")
        brand=tk.Canvas(sidebar,height=64,bg=P["sidebar"],highlightthickness=0)
        brand.pack(fill="x",padx=(20,12),pady=(16,8))
        brand.create_image(0,32,anchor="w",image=photo(brand,"app_40.png"))
        brand.create_text(53,24,text="Ares",anchor="w",fill=P["text"],font=self.fnt(17,"bold"))
        brand.create_text(54,45,text="ASSISTANT  ·  2.9.9.6",anchor="w",fill=P["muted"],font=self.fnt(7))
        SearchBox(sidebar,P,self.fnt,self.open_command_palette).pack(padx=12,pady=(0,14))
        self.nav_rail=NavRail(sidebar,P,self.fnt)
        self.nav_rail.pack(fill="x")
        self.workspace=ttk.Frame(self.shell,padding=(30,0,30,0))
        self.workspace.pack(side="left",fill="both",expand=True)
        self.header=ttk.Frame(self.workspace)
        self.header.pack(fill="x",pady=(24,18))
        self.page_chip=tk.Canvas(self.header,width=46,height=46,bg=P["background"],highlightthickness=0)
        self.page_chip.pack(side="left",anchor="n",padx=(0,16),pady=(4,0))
        self.page_chip.create_image(0,0,anchor="nw",image=photo(self.page_chip,f"chip46_{P['accent'][1:].lower()}.png"))
        self._chip_icon=self.page_chip.create_image(23,23,image=photo(self.page_chip,icon_name("home",24,P["accent"])))
        title_area=self.title_area=ttk.Frame(self.header)
        title_area.pack(side="left",fill="x",expand=True)
        self.eyebrow=ttk.Label(title_area,text="ARES  /  ОСНОВНОЕ",style="Eyebrow.TLabel")
        self.eyebrow.pack(anchor="w")
        self.page_title=ttk.Label(title_area,text="Обзор",style="Title.TLabel")
        self.page_title.pack(anchor="w",pady=(1,2))
        self.page_subtitle=ttk.Label(title_area,text="",style="Sub.TLabel")
        self.page_subtitle.pack(anchor="w")
        actions=ttk.Frame(self.header)
        actions.pack(side="right",padx=(14,0))
        self.pause_button=AnimatedButton(actions,textvariable=self.pause_button_var,style="Secondary.TButton",command=self.toggle_pause,compound="left")
        self.pause_button.pack(side="left",padx=(0,8))
        self.monitor_button=AnimatedButton(actions,textvariable=self.monitor_button_var,style="Accent.TButton",command=self.toggle_monitoring,compound="left")
        self.monitor_button.pack(side="left")
        self.pause_button_var.trace_add("write",lambda *a:self._sync_header_icons())
        self.monitor_button_var.trace_add("write",lambda *a:self._sync_header_icons())
        self._sync_header_icons()
        footer=ttk.Frame(self.workspace)
        footer.pack(side="bottom",fill="x",pady=(10,12))
        self.footer_status=ttk.Label(footer,textvariable=self.status_var,style="Sub.TLabel",wraplength=680)
        self.footer_status.pack(side="left")
        ttk.Label(footer,text="2.9.9.6  ·  Ctrl+K — быстрый поиск",style="Sub.TLabel",font=self.fnt(8)).pack(side="right")
        # Pages slide in from below; the stage clips the travelling notebook.
        self.stage=ttk.Frame(self.workspace)
        self.stage.pack(fill="both",expand=True)
        self.notebook=ttk.Notebook(self.stage,style="Workspace.TNotebook")
        self.notebook.place(x=0,y=0,relwidth=1,relheight=1)
        self._pages=[
            ("dashboard_tab","Обзор","home","Состояние ассистента и последние обращения"),
            ("simulator_tab","Проверка ответа","check","Проверь сообщение перед использованием шаблона"),
            ("training_tab","Обучение","learn","Разбирай неизвестные обращения и уточняй ответы"),
            ("templates_tab","Шаблоны","templates","Ответы, триггеры и правила в одном месте"),
            ("history_tab","История","history","Обработанные обращения и принятые решения"),
            ("removals_tab","Снятия","removals","Очередь снятий через твою сессию Telegram"),
            ("repunish_tab","Перебаны","repunish","Очередь перебанов, расписание и журнал"),
            ("ai_tab","Контекст","context","Как ассистент выбирает подходящий ответ"),
            ("settings_tab","Настройки","settings","Подключение, ответы и личные предпочтения"),
            ("diagnostics_tab","Диагностика","diagnostics","Состояние программы и инструменты обслуживания"),
            ("changes_tab","Изменения","changes","Обновления интерфейса и новые возможности"),
        ]
        self.nav_items={}
        for i,(attr,title,glyph,subtitle) in enumerate(self._pages):
            if i in (0,5):
                self.nav_rail.add_section("ОСНОВНОЕ" if i==0 else "ПРОГРАММА",gap=0 if i==0 else 10)
            tab=ttk.Frame(self.notebook)
            setattr(self,attr,tab)
            self.notebook.add(tab,text=title)
            self.nav_items[str(tab)]=self.nav_rail.add_item(title,glyph,lambda t=tab:self.notebook.select(t))
        bottom=ttk.Frame(sidebar,style="Sidebar.TFrame")
        bottom.pack(side="bottom",fill="x",padx=20,pady=(0,16))
        ttk.Separator(bottom).pack(fill="x",pady=(0,12))
        self.connection_label=StatusPill(bottom,P["sidebar"],self.fnt,"●  Не подключён",P["muted"],pulse=(P["green"],))
        self.connection_label.pack(anchor="w")
        ttk.Label(bottom,text="AresMine · staff tools",style="Sidebar.TLabel",font=self.fnt(8)).pack(anchor="w",pady=(5,0))
        self._build_dashboard()
        self._build_simulator()
        self._build_training()
        self._build_templates()
        self._build_local_ai()
        self._build_history()
        self._build_removals()
        self._build_repunish()
        self._build_settings()
        self._build_diagnostics()
        self._build_changes()
        self._polish(self.notebook)
        self.notebook.bind("<<NotebookTabChanged>>",self._page_changed)
        self.status_var.trace_add("write",self._connection_changed)
        self.mode_var.trace_add("write",self._mode_hint_changed)
        self.root.bind("<Control-k>",lambda e:self.open_command_palette())
        self.root.bind("<Control-K>",lambda e:self.open_command_palette())
        self.root.bind("<Control-Cyrillic_el>",lambda e:self.open_command_palette())
        for i,page in enumerate((page for page in self._pages if page[0]!='repunish_tab'),1):
            self.root.bind(f"<Alt-Key-{i%10}>",lambda e,a=page[0]:self.notebook.select(getattr(self,a)))
        self.root.bind("<Alt-Key-r>",lambda e:self.notebook.select(self.repunish_tab))
        self._install_theme_ui(sidebar)
        self._page_changed()
        self._dress(self.root)
        install_dialogs(self)
        self.root.after(90,self._reveal_window)
        # Атмосфера стартует после раскладки: слоям нужны реальные размеры холстов.
        self.root.after(260,self._start_atmosphere)

    # Every button gets an icon that matches its meaning, brightening on hover.
    BUTTON_ICONS=(("↗","external","right"),("открыть чат","external","right"),("открыть бота","external","right"),
                  ("копир","copy","left"),("экспорт","download","left"),("собрать пакет","download","left"),
                  ("импорт","upload","left"),("перечитать","refresh","left"),("обнов","refresh","left"),
                  ("сброс","refresh","left"),("повтор","refresh","left"),("разрешить повторную","refresh","left"),
                  ("сохран","save","left"),("останов","stop","left"),("тест","play","left"),("запуст","play","left"),
                  ("выполн","play","left"),("попробовать","play","left"),("проверить отправку","warning","left"),
                  ("провер","check","left"),("очист","broom","left"),("удалить программу","trash","left"),
                  ("да, удалить","trash","left"),("удал","trash","left"),("новый шаблон","plus","left"),
                  ("создать шаблон","plus","left"),("обучить","learn","left"),("настроить шаблоны","templates","left"),
                  ("открыть шаблон","templates","left"),("открыть лог","file","left"),("открыть файл","file","left"),
                  ("папка","folder","left"),("что нового","spark","left"),("понятно","check","left"),
                  ("готово","check","left"),("закрыть","error","left"),("дальше","chevright","right"),
                  ("назад","chevleft","left"))
    def _button_icon_colors(self):
        """Цвета иконок кнопок (обычный, наведение) — из палитры активной темы."""
        return {"Accent.TButton":(P["on_accent"],P["on_accent"]),"Danger.TButton":(P["red"],P["danger_hover"]),
                "Warning.TButton":(P["amber"],P["amber"]),"Success.TButton":(P["green"],P["green"]),
                "Ghost.TButton":(P["muted"],P["text"]),"Secondary.TButton":(P["text_soft"],P["text"])}

    def _dress(self,widget):
        """Bring every control under `widget` to the Aurora look; safe to call again."""
        self._decorate_buttons(widget)
        self._dress_controls(widget)

    def _dress_controls(self,widget):
        for child in widget.winfo_children():
            self._dress_controls(child)
        if isinstance(widget,(ttk.Entry,ttk.Spinbox)):  # ttk.Combobox is an Entry too
            if str(widget.cget("style"))!="Palette.TEntry":
                animate_field(widget,dialog=getattr(widget,"_ares_dialog",False))
            if isinstance(widget,ttk.Combobox):
                hook_combobox(widget,P,self.fnt)
        elif isinstance(widget,ttk.Notebook) and widget is not self.notebook and not getattr(widget,"_ares_tabs",None):
            if widget.winfo_manager()!="pack":return
            info=widget.pack_info()
            page=str(widget.cget("style")).startswith("Page.")
            widget.configure(style="BarePage.TNotebook" if page else "Bare.TNotebook")
            bar=SegmentedTabs(widget.master,widget,P,self.fnt,P["background"] if page else P["surface"])
            bar.pack(before=widget,side=info.get("side","top"),anchor="w",pady=(0,10))
            widget._ares_tabs=bar

    def _decorate_buttons(self,widget):
        for child in widget.winfo_children():
            self._decorate_buttons(child)
        if not isinstance(widget,AnimatedButton) or str(widget.cget("image")) or str(widget.cget("textvariable")):
            return
        text=str(widget.cget("text"))
        low=text.lower()
        match=next(((glyph,side) for key,glyph,side in self.BUTTON_ICONS if key in low),None)
        if not match:
            return
        glyph,side=match
        base=str(widget.cget("style")).split(".",1)[-1]
        normal,hover=self._button_icon_colors().get(base,(P["text_soft"],P["text"]))
        icon_side="left" if side=="right" else "right"
        images=[self._padded_icon(glyph,17,color,gap=8,side=icon_side) for color in (normal,hover,P["muted_dim"])]
        clean=" ".join(text.replace("↗","").replace("→","").replace("▶","").split())
        if clean.startswith("+"):clean=clean[1:].strip()
        clean=clean.replace(" Ctrl+Enter","   Ctrl+Enter")
        widget.configure(text=clean,compound=side,
                         image=(images[0],"disabled",images[2],"active",images[1],"focus",images[1]))

    def _sync_header_icons(self):
        paused="Продолж" in self.pause_button_var.get()
        connected="Отключ" in self.monitor_button_var.get()
        self.pause_button.configure(image=self._padded_icon("play" if paused else "pause",17,P["text_soft"]))
        self.monitor_button.configure(image=self._padded_icon("power" if connected else "send",17,P["on_accent"]))

    def _padded_icon(self,glyph,size,color,gap=8,side="right"):
        """An icon with transparent space on one side, so compound buttons breathe."""
        cache=self.root.__dict__.setdefault("_ares_padded",{})
        key=(glyph,size,color,gap,side)
        if key not in cache:
            src=photo(self.root,icon_name(glyph,size,color))
            img=tk.PhotoImage(master=self.root,width=size+gap,height=size)
            img.tk.call(img,"copy",src,"-to",gap if side=="left" else 0,0,"-compositingrule","set")
            cache[key]=img
        return cache[key]

    def _apply_window_chrome(self,caption_only=False):
        """Dark caption in the app's colours. Windows creates the frame window only once Tk maps it.

        caption_only=True (смена темы) — только цвета заголовка, без иконок и без
        принудительной перерисовки окна: её и так сделает главный цикл.
        """
        if os.name!="nt":return
        try:
            import ctypes
            if not caption_only:self.root.update_idletasks()
            handle=ctypes.windll.user32.GetParent(self.root.winfo_id())
            if not handle:return
            def dwm(attribute,value):
                data=ctypes.c_int(value)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(handle,attribute,ctypes.byref(data),4)
            def colorref(color):
                r,g,b=(int(color[i:i+2],16) for i in (1,3,5))
                return r|(g<<8)|(b<<16)
            dark=self._theme_is_dark()
            dwm(20,1 if dark else 0)                   # dark/light title bar (Windows 10 20H1+)
            dwm(35,colorref(P["background"]))          # Windows 11 caption colour
            dwm(34,colorref(P["window_border"]))       # window border
            dwm(36,colorref(P["text_soft"]))           # caption text
            if caption_only:
                ctypes.windll.user32.SetWindowPos(handle,0,0,0,0,0,0x0001|0x0002|0x0004|0x0010|0x0020)
                return
            self._apply_window_icons(handle)
            # SWP_NOSIZE|NOMOVE|NOZORDER|NOACTIVATE|FRAMECHANGED repaints the caption now.
            ctypes.windll.user32.SetWindowPos(handle,0,0,0,0,0,0x0001|0x0002|0x0004|0x0010|0x0020)
        except Exception:
            pass
        try:
            self._apply_taskbar_identity(handle)
        except Exception:
            pass

    def _apply_window_icons(self,handle):
        """Exact-size A logo for the caption, Alt+Tab and the taskbar, straight from app.ico."""
        import ctypes
        from ctypes import wintypes
        user32=ctypes.windll.user32
        user32.LoadImageW.restype=wintypes.HANDLE
        user32.LoadImageW.argtypes=[wintypes.HINSTANCE,wintypes.LPCWSTR,wintypes.UINT,ctypes.c_int,ctypes.c_int,wintypes.UINT]
        user32.SendMessageW.argtypes=[wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM]
        if not self._win_icons:
            for metric in (49,11):                     # SM_CXSMICON, SM_CXICON
                size=user32.GetSystemMetrics(metric)
                # IMAGE_ICON, LR_LOADFROMFILE; Windows reads the PNG frames Pillow writes.
                self._win_icons.append(user32.LoadImageW(None,str(ASSETS/"app.ico"),1,size,size,0x10))
        for kind,icon in enumerate(self._win_icons):   # ICON_SMALL, ICON_BIG
            if icon:user32.SendMessageW(handle,0x80,kind,icon)   # WM_SETICON

    def _apply_taskbar_identity(self,handle):
        """A pinned taskbar button keeps the A logo and relaunches through the EXE, not python.exe."""
        from win32com.propsys import propsys,pscon
        store=propsys.SHGetPropertyStoreForWindow(handle,propsys.IID_IPropertyStore)
        values=[(pscon.PKEY_AppUserModel_ID,"AresMine.AresAssistant")]
        launcher=os.environ.get("ARES_LAUNCHER_EXE","")
        if launcher and os.path.exists(launcher):
            values+=[(pscon.PKEY_AppUserModel_RelaunchCommand,f'"{launcher}"'),
                     (pscon.PKEY_AppUserModel_RelaunchDisplayNameResource,"Ares Assistant"),
                     (pscon.PKEY_AppUserModel_RelaunchIconResource,f"{ASSETS/'app.ico'},0")]
        for key,value in values:
            store.SetValue(key,propsys.PROPVARIANTType(value))
        store.Commit()

    def _reveal_window(self):
        if getattr(self,"_window_revealed",False):
            # Перестройка при смене темы: окно уже видно, обновляем только заголовок окна.
            self._apply_window_chrome()
            return
        self._window_revealed=True
        try:
            if self.root.state()=="withdrawn":
                self.root.attributes("-alpha",1.0)
                return
        except tk.TclError:
            return
        self._apply_window_chrome()
        def fade(t):
            try:self.root.attributes("-alpha",t)
            except tk.TclError:pass
        animate(self.root,"reveal",450,fade,ease_out_cubic)
        # Never leave the window transparent, whatever happens to the animation.
        self.root.after(1500,lambda:fade(1.0))
        self.root.after(140,self._intro_dashboard)

    def _intro_dashboard(self):
        if str(self.notebook.select())!=str(self.dashboard_tab):return
        self.hero.intro()
        for label in self.metric_labels:label.replay()

    def show_window(self):
        try:hidden=self.root.state() in ("withdrawn","iconic")
        except tk.TclError:hidden=False
        if hidden:
            try:self.root.attributes("-alpha",0.0)
            except tk.TclError:pass
        super().show_window()
        self._apply_window_chrome()
        if hidden:
            def fade(t):
                try:self.root.attributes("-alpha",t)
                except tk.TclError:pass
            animate(self.root,"reveal",340,fade,ease_out_cubic)
            self.root.after(1200,lambda:fade(1.0))

    def _page_changed(self,event=None):
        selected=self.notebook.select()
        for index,(attr,title,glyph,subtitle) in enumerate(self._pages):
            item=self.nav_items[str(getattr(self,attr))]
            item.selected=str(getattr(self,attr))==selected
            if item.selected:
                self.nav_rail.select(index)
                self._show_header(title,subtitle,glyph,"ОСНОВНОЕ" if index<5 else "ПРОГРАММА")
        self._slide_page()

    def _show_header(self,title,subtitle,glyph,section):
        self.page_title.configure(text=title)
        self.page_subtitle.configure(text=subtitle)
        self.eyebrow.configure(text=f"ARES  /  {section}")
        self.page_chip.itemconfigure(self._chip_icon,image=photo(self.page_chip,icon_name(glyph,24,P["accent"])))
        def step(t):
            self.page_title.configure(foreground=lerp_color(P["muted_dim"],P["text"],t))
            self.page_subtitle.configure(foreground=lerp_color(P["background"],P["muted"],t))
            self.page_title.pack_configure(padx=(round(12*(1-t)),0))
        animate(self.header,"title",420,step,ease_out_quint)

    def _slide_page(self):
        def step(t):
            self.notebook.place_configure(y=round(26*(1-t)))
        animate(self.stage,"slide",340,step,ease_out_quint)

    def _connection_changed(self,*args):
        status=self.status_var.get().lower()
        connected=bool(self.telegram and self.telegram.connected)
        if self.paused:
            text,color="●  На паузе",P["amber"]
        elif connected:
            text,color="●  Telegram подключён",P["green"]
        elif "ошиб" in status:
            text,color="●  Ошибка подключения",P["red"]
        else:
            text,color="●  Не подключён",P["muted"]
        self.connection_label.configure(text=text,foreground=color)

    def _card(self,parent,title):
        panel=ttk.Frame(parent,style="Panel.TFrame",padding=(18,44 if title else 18,18,18))
        if title:
            ttk.Label(panel,text=title,style="Section.TLabel").place(x=19,y=15,bordermode="ignore")
        return panel

    def _hover_card(self,panel):
        """Light the card border while the pointer is anywhere inside it."""
        def enter(event):
            panel.configure(style="PanelHover.TFrame")
        def leave(event):
            widget=panel.winfo_containing(*panel.winfo_pointerxy())
            while widget is not None and widget is not panel:
                widget=getattr(widget,"master",None)
            if widget is None:
                panel.configure(style="Panel.TFrame")
        panel.bind("<Enter>",enter,add="+")
        panel.bind("<Leave>",leave,add="+")

    def _text_widget(self,parent,height=8,readonly=False):
        widget=tk.Text(parent,height=height,width=1,bg=P["field"],fg=P["text_soft"],
                       insertbackground=P["accent"],insertwidth=2,selectbackground=P["selection"],selectforeground=P["text"],
                       relief="flat",bd=0,highlightthickness=1,highlightbackground=P["text_border"],
                       highlightcolor=P["text_focus"],wrap="word",font=(self.ui_font,10),
                       padx=14,pady=12,spacing1=2,spacing3=4,undo=not readonly)
        if readonly: widget.configure(state="disabled",cursor="arrow")
        return widget

    def _checkbutton(self,parent,text,variable,card=True,command=None):
        return AnimatedSwitch(parent,base_style="DarkCard.TCheckbutton" if card else "Dark.TCheckbutton",
                              text=text,variable=variable,command=command,cursor="hand2")

    def _autohide(self,bar,**grid):
        """Scrollbar command that hides the bar while everything fits."""
        def update(first,last):
            first,last=float(first),float(last)
            if first<=0 and last>=1:
                if bar.winfo_manager():
                    bar.grid_remove() if bar.winfo_manager()=="grid" else bar.pack_forget()
            elif not bar.winfo_manager():
                if "grid" in grid:bar.grid()
                else:bar.pack(**grid.get("pack",{}))
            bar.set(first,last)
        return update

    # ------------------------------------------------------------ dashboard
    def _build_dashboard(self):
        body=self._scroll_page(self.dashboard_tab)
        self.hero=DashboardHero(body,P,self.fnt,"ТВОЙ ПОМОЩНИК В МОДЕРАЦИИ","Меньше рутины.\nБольше внимания игрокам.",
                                motif=self._theme_motif())
        self.hero.pack(fill="x",pady=(0,16))
        combo=ttk.Combobox(self.hero,textvariable=self.mode_var,values=("Черновик","Автоответ","Только обучение"),
                           state="readonly",width=19,style="Hero.TCombobox")
        combo.bind("<<ComboboxSelected>>",lambda e:self._mode_changed())
        self.hero.place_control(combo)
        self.mode_hint=self.hero.hint
        self._mode_hint_changed()
        metrics=ttk.Frame(body)
        metrics.pack(fill="x",pady=(0,16))
        self.metric_values={}
        self.metric_notes={}
        self.metric_labels=[]
        specs=[("templates","Шаблоны ответов","templates",P["accent"]),
               ("examples","Примеры в базе","learn",P["blue"]),
               ("pending","Ждут обучения","history",P["amber"]),
               ("processed","Обработано","check",P["green"])]
        for i,(key,title,glyph,color) in enumerate(specs):
            metrics.columnconfigure(i,weight=1,uniform="metric")
            panel=self._card(metrics,"")
            panel.grid(row=0,column=i,sticky="nsew",padx=(0 if i==0 else 6,0 if i==3 else 6))
            line=ttk.Frame(panel,style="Card.TFrame")
            line.pack(fill="x")
            ttk.Label(line,text=title,style="Muted.Card.TLabel").pack(side="left",anchor="n",pady=(7,0))
            chip=tk.Canvas(line,width=34,height=34,bg=P["surface"],highlightthickness=0)
            chip.pack(side="right")
            chip.create_image(0,0,anchor="nw",image=photo(chip,f"chip34_{color[1:].lower()}.png"))
            chip.create_image(17,17,image=photo(chip,icon_name(glyph,17,color)))
            value=tk.StringVar(value="0")
            note=tk.StringVar(value="")
            self.metric_values[key]=value; self.metric_notes[key]=note
            number=CountLabel(panel,value,style="Number.TLabel")
            number.pack(anchor="w",pady=(0,0))
            self.metric_labels.append(number)
            ttk.Label(panel,textvariable=note,style="Muted.Card.TLabel",font=self.fnt(8)).pack(anchor="w",pady=(2,0))
            self._hover_card(panel)
        content=ttk.Frame(body)
        content.pack(fill="both",expand=True)
        content.columnconfigure(0,weight=3,uniform="release");content.columnconfigure(1,weight=1,minsize=286)
        content.rowconfigure(0,weight=1)
        last=self._card(content,"Последнее обращение")
        last.grid(row=0,column=0,sticky="nsew",padx=(0,14))
        ticket_label=ttk.Label(last,textvariable=self.last_ticket_var,style="Muted.Card.TLabel",wraplength=580)
        ticket_label.pack(fill="x",pady=(2,10))
        last.bind("<Configure>",lambda e:ticket_label.configure(wraplength=max(160,e.width-40)),add="+")
        self.preview_text=self._text_widget(last,height=5,readonly=True)
        self.preview_text.pack(fill="both",expand=True)
        self._set_text(self.preview_text,"Здесь появится последнее обращение\n\nПодключи Telegram, чтобы получать тикеты и видеть выбранные ответы.")
        row=ttk.Frame(last,style="Card.TFrame");row.pack(fill="x",pady=(12,0))
        AnimatedButton(row,text="Повторить обработку",style="Secondary.TButton",command=self.retry_last_ticket).pack(side="left")
        AnimatedButton(row,text="Открыть чат ↗",style="Ghost.TButton",command=self.open_response_chat).pack(side="right")
        quick=self._card(content,"Быстрые действия")
        quick.grid(row=0,column=1,sticky="nsew")
        for title,desc,glyph,color,command in [
            ("Проверить ответ","Тест на своём сообщении","check",P["green"],self.open_simulator),
            ("Открыть шаблоны","Ответы и точные правила","templates",P["accent"],lambda:self.notebook.select(self.templates_tab)),
            ("Разобрать очередь","Обучить на новых тикетах","learn",P["amber"],lambda:self.notebook.select(self.training_tab)),
        ]:
            ActionTile(quick,P,self.fnt,title,desc,glyph,color,command).pack(fill="x",pady=(4,6))

    def _mode_hint_changed(self,*args):
        hints={"Черновик":"Готовит ответ для проверки перед отправкой.",
               "Автоответ":"Отправляет ответы при достаточной уверенности.",
               "Только обучение":"Запоминает подтверждённые ручные ответы."}
        if hasattr(self,"mode_hint"): self.mode_hint.configure(text=hints.get(self.mode_var.get(),""))

    def _refresh_stats(self):
        super()._refresh_stats()
        stats=self.knowledge.stats()
        if not hasattr(self,"metric_values"): return
        for key,var in self.metric_values.items(): var.set(f"{stats.get(key,0):,}".replace(","," "))
        self.metric_notes["templates"].set(f"{stats['templates']-stats['disabled']} включено")
        self.metric_notes["examples"].set(f"{stats['manual']} твоих решений")
        self.metric_notes["pending"].set("Нужен твой ответ" if stats["pending"] else "Очередь пуста")
        self.metric_notes["processed"].set("За всё время")
        nav=self.nav_items.get(str(self.training_tab))
        if nav:
            nav.badge=str(stats["pending"]) if 0<stats["pending"]<100 else ("99+" if stats["pending"] else "")
            nav.draw()

    # ------------------------------------------------------------ simulator
    def _build_simulator(self):
        notice=ttk.Frame(self.simulator_tab,style="Hero.TFrame",padding=(18,12))
        notice.pack(fill="x",pady=(0,14))
        ttk.Label(notice,text="ТЕСТОВЫЙ РЕЖИМ",style="HeroSub.TLabel",foreground=P["accent"],font=self.fnt(8,"bold")).pack(side="left",padx=(0,14))
        ttk.Label(notice,text="Проверка не отправляет сообщения и не меняет базу.",style="HeroSub.TLabel",font=self.fnt(9)).pack(side="left")
        pane=tk.PanedWindow(self.simulator_tab,orient="horizontal",bg=P["background"],bd=0,sashwidth=14,showhandle=False,opaqueresize=True)
        pane.pack(fill="both",expand=True)
        left=self._card(pane,"01  /  Сообщение игрока");right=self._card(pane,"02  /  Результат проверки")
        pane.add(left,minsize=300,stretch="always");pane.add(right,minsize=400,stretch="always")
        ttk.Label(left,text="Вставь вопрос или полный текст тикета.",style="Muted.Card.TLabel").pack(anchor="w",pady=(4,12))
        actions=ttk.Frame(left,style="Card.TFrame");actions.pack(side="bottom",fill="x",pady=(12,0))
        self.simulator_run_button=AnimatedButton(actions,text="Проверить   Ctrl+Enter",style="Accent.TButton",command=self.run_simulator)
        self.simulator_run_button.pack(side="left")
        AnimatedButton(actions,text="Очистить",style="Ghost.TButton",command=self.clear_simulator).pack(side="right")
        ttk.Label(left,textvariable=self.simulator_status_var,style="Muted.Card.TLabel",wraplength=390).pack(side="bottom",fill="x",pady=(10,0))
        self.simulator_input_text=self._text_widget(left,height=14)
        self.simulator_input_text.pack(fill="both",expand=True)
        self.simulator_input_text.bind("<Control-Return>",lambda e:(self.run_simulator(),"break")[1])
        result_heading=ttk.Label(right,textvariable=self.simulator_action_var,style="Section.TLabel",wraplength=480)
        result_heading.pack(fill="x",pady=(4,9))
        self.confidence_var=tk.IntVar(value=0)
        self.confidence_bar=GlowProgress(right,P["surface"],variable=self.confidence_var,maximum=100)
        self.confidence_bar.pack(fill="x",pady=(0,10))
        result_tabs=ttk.Notebook(right);result_tabs.pack(fill="both",expand=True)
        pages=[]
        for title in ("Ответ","Решение","Почему","Кандидаты"):
            page=ttk.Frame(result_tabs,style="Card.TFrame",padding=(0,8));result_tabs.add(page,text=title);pages.append(page)
        answer,summary,reason,candidates=pages
        AnimatedButton(answer,text="Копировать ответ",style="Secondary.TButton",command=lambda:self._copy_text(self.simulator_answer_text)).pack(side="bottom",anchor="e",pady=(10,0))
        self.simulator_answer_text=self._text_widget(answer,height=10,readonly=True)
        self.simulator_answer_text.pack(fill="both",expand=True)
        self.simulator_summary_text=self._text_widget(summary,height=10,readonly=True)
        self.simulator_summary_text.pack(fill="both",expand=True)
        self.simulator_reason_text=self._text_widget(reason,height=10,readonly=True)
        self.simulator_reason_text.pack(fill="both",expand=True)
        self._set_text(self.simulator_answer_text,"Здесь появится ответ\n\nВведи сообщение слева и нажми «Проверить».")
        self._set_text(self.simulator_summary_text,"Результат появится после проверки.")
        self._set_text(self.simulator_reason_text,"Здесь будет объяснение выбранного решения.")
        AnimatedButton(candidates,text="Открыть шаблон",style="Secondary.TButton",command=self.simulator_open_selected_template).pack(side="bottom",anchor="w",pady=(10,0))
        holder=ttk.Frame(candidates,style="Card.TFrame");holder.pack(fill="both",expand=True)
        self.simulator_candidates_tree=ttk.Treeview(holder,columns=("score","name","examples"),show="headings",selectmode="browse")
        for col,title,width in (("score","Близость",85),("name","Шаблон",240),("examples","Примеры",80)):
            self.simulator_candidates_tree.heading(col,text=title)
            self.simulator_candidates_tree.column(col,width=width,minwidth=70,stretch=col=="name")
        self._scroll_tree(holder,self.simulator_candidates_tree)
        self.simulator_candidates_tree.bind("<Double-1>",lambda e:self.simulator_open_selected_template())
        self._responsive_pane(pane,.43)

    def run_simulator(self):
        result=super().run_simulator()
        if self.simulator_busy:
            self.simulator_run_button.state(["disabled"])
            self.confidence_bar.configure(mode="indeterminate")
            self.confidence_bar.start(14)
        return result

    def _handle_simulator_result(self,payload):
        super()._handle_simulator_result(payload)
        self.confidence_bar.stop()
        self.confidence_bar.configure(mode="determinate")
        self.confidence_var.set(int(payload.get("confidence",0) or 0))
        self.simulator_run_button.state(["!disabled"])

    def _handle_event(self,event,payload):
        if event=='repunish_status':
            info=dict(payload or {})
            if info.get('service_id') is not None and info['service_id']!=id(self.telegram):return
            self._update_repunish(info);return
        if event=='repunish_notice':
            if self.tray:self.tray.notify('Перебаны Ares',str(payload))
            return
        if event=="removals_status":
            info=dict(payload or {})
            if info.get("service_id") is not None and info["service_id"]!=id(self.telegram):
                return
            self._update_removals(info)
            return
        if event=="simulator_error":
            self.confidence_bar.stop()
            self.confidence_bar.configure(mode="determinate")
            self.confidence_var.set(0)
            self.simulator_run_button.state(["!disabled"])
        return super()._handle_event(event,payload)

    # ------------------------------------------------------------- removals
    def _build_removals(self):
        page=self.removals_tab
        page.columnconfigure(0,weight=1)
        page.rowconfigure(2,weight=1)
        self.removals_status_var=tk.StringVar(value="Готово к запуску")
        self.removals_progress_var=tk.StringVar(value="Ожидание запуска")
        self.removals_remaining_var=tk.StringVar(value="0")
        self.removals_sent_var=tk.StringVar(value="0")
        self.removals_batches_var=tk.StringVar(value="0")
        self._removals_uncertain=None
        hero=ttk.Frame(page,style="Hero.TFrame",padding=(22,18))
        hero.grid(row=0,column=0,sticky="ew",pady=(0,12))
        top=ttk.Frame(hero,style="HeroInner.TFrame");top.pack(fill="x")
        ttk.Label(top,text="ОЧЕРЕДЬ СНЯТИЙ",style="HeroSub.TLabel",foreground=P["accent"],font=self.fnt(8,"bold")).pack(side="left")
        self.removals_badge=StatusPill(top,P["surface_high"],self.fnt,"●  Ожидание",P["muted"],pulse=(P["accent"],))
        self.removals_badge.pack(side="right")
        ttk.Label(hero,text="Снятия без ручного копирования",style="HeroTitle.TLabel",font=self.fnt(19,"bold")).pack(anchor="w",pady=(6,4))
        route=ttk.Label(hero,text="AresStaffRobot  →  AresMineRobot    ·    5 секунд между командами",style="HeroSub.TLabel")
        route.pack(anchor="w")
        row=ttk.Frame(hero,style="HeroInner.TFrame");row.pack(fill="x",pady=(14,0))
        self.removals_start_button=AnimatedButton(row,text="Выполнить снятия",style="Accent.TButton",command=self.start_removals)
        self.removals_start_button.pack(side="left",padx=(0,8))
        self.removals_stop_button=AnimatedButton(row,text="Остановить",style="Secondary.TButton",command=self.stop_removals)
        self.removals_stop_button.pack(side="left")
        self.removals_stop_button.state(["disabled"])
        AnimatedButton(row,text="Mine ↗",style="Ghost.TButton",command=lambda:webbrowser.open("https://t.me/AresMineRobot")).pack(side="right")
        AnimatedButton(row,text="Staff ↗",style="Ghost.TButton",command=lambda:webbrowser.open("https://t.me/AresStaffRobot")).pack(side="right",padx=(0,5))
        metrics=ttk.Frame(page);metrics.grid(row=1,column=0,sticky="ew",pady=(0,12))
        for i,(title,var,color,note) in enumerate([
            ("В текущем списке",self.removals_remaining_var,P["amber"],"ещё не отправлено"),
            ("Команд отправлено",self.removals_sent_var,P["green"],"в этой очереди"),
            ("Списков получено",self.removals_batches_var,P["accent"],"обработка до пустой очереди")]):
            metrics.columnconfigure(i,weight=1,uniform="removal")
            card=self._card(metrics,"");card.grid(row=0,column=i,sticky="nsew",padx=(0 if i==0 else 6,0 if i==2 else 6))
            ttk.Label(card,text=title,style="Muted.Card.TLabel").pack(anchor="w")
            line=ttk.Frame(card,style="Card.TFrame");line.pack(fill="x",pady=(2,0))
            CountLabel(line,var,style="Number.TLabel",foreground=color,font=self.fnt(24,"bold")).pack(side="left")
            ttk.Label(line,text=note,style="Muted.Card.TLabel",font=self.fnt(8),wraplength=120).pack(side="left",padx=(12,0))
            self._hover_card(card)
        journal=self._card(page,"Ход выполнения")
        journal.grid(row=2,column=0,sticky="nsew")
        self.removals_status_label=ttk.Label(journal,textvariable=self.removals_status_var,style="Card.TLabel",wraplength=760)
        self.removals_status_label.pack(fill="x",pady=(2,6))
        journal.bind("<Configure>",lambda e:self.removals_status_label.configure(wraplength=max(240,e.width-40)),add="+")
        self.removals_progress=GlowProgress(journal,P["surface"],mode="determinate",maximum=100)
        self.removals_progress.pack(fill="x",pady=(0,6))
        tools=ttk.Frame(journal,style="Card.TFrame");tools.pack(fill="x",pady=(0,7))
        ttk.Label(tools,textvariable=self.removals_progress_var,style="Muted.Card.TLabel",font=self.fnt(9)).pack(side="left")
        self.removals_review_button=AnimatedButton(tools,text="Проверить отправку",style="Secondary.TButton",command=self.review_removal_send)
        holder=ttk.Frame(journal,style="Card.TFrame");holder.pack(fill="both",expand=True)
        self.removals_log=self._text_widget(holder,height=4,readonly=True)
        self.removals_log.configure(font=(self.ui_font,9),padx=12,pady=10,spacing3=6)
        scroll=ttk.Scrollbar(holder,command=self.removals_log.yview);scroll.pack(side="right",fill="y")
        self.removals_log.configure(yscrollcommand=self._autohide(scroll,pack={"side":"right","fill":"y","before":self.removals_log}))
        self.removals_log.pack(fill="both",expand=True)
        for name,color in (("time",P["muted"]),("info",P["text_soft"]),("success",P["green"]),("error",P["red"])):
            self.removals_log.tag_configure(name,foreground=color)
        self._append_removals_log({"time":"—","message":"Нажми «Выполнить снятия». Ассистент обработает все списки и остановится, когда очередь станет пустой.","level":"info"})
        ttk.Label(journal,text="Отправленные ID сохраняются. Остановка и общая пауза прекращают очередь.",style="Muted.Card.TLabel",font=self.fnt(8)).pack(anchor="w",pady=(9,0))

    def _append_removals_log(self,entry):
        widget=self.removals_log
        widget.configure(state="normal")
        widget.insert("end",str(entry.get("time",""))+"  ","time")
        widget.insert("end",str(entry.get("message",""))+"\n",entry.get("level","info"))
        if int(widget.index("end-1c").split(".")[0])>1000:
            widget.delete("1.0","201.0")
        widget.configure(state="disabled")
        widget.see("end")

    def _update_removals(self,info):
        busy=bool(info.get("busy",False))
        state=info.get("state","ready")
        self.removals_status_var.set(info.get("message",""))
        self.removals_start_button.state(["disabled"] if busy else ["!disabled"])
        self.removals_stop_button.state(["!disabled"] if busy else ["disabled"])
        label,color=("●  Выполняется",P["accent"]) if busy else {
            "done":("●  Завершено",P["green"]),"error":("●  Ошибка",P["red"]),
            "review":("●  Нужна проверка",P["amber"]),"stopped":("●  Остановлено",P["amber"]),
            "offline":("●  Нет подключения",P["muted"]),"paused":("●  Пауза",P["amber"]),
        }.get(state,("●  Ожидание",P["muted"]))
        self.removals_badge.configure(text=label,foreground=color)
        for key,var in (("remaining",self.removals_remaining_var),("sent_count",self.removals_sent_var),("batch_number",self.removals_batches_var)):
            if key in info:var.set(str(info[key]))
        total,done=info.get("batch_total",0),info.get("batch_sent",0)
        self.removals_progress.stop()
        self.removals_progress.configure(mode="determinate",value=(done/total*100 if total else (100 if state=="done" else 0)))
        if busy and state in ("connecting","requesting"):
            self.removals_progress.configure(mode="indeterminate");self.removals_progress.start(14)
        self.removals_progress_var.set(f"Текущий список: {done} / {total}" if total else ("Очередь пуста" if state=="done" else "Ожидание списка"))
        if "uncertain" in info:self._removals_uncertain=info["uncertain"]
        if self._removals_uncertain and not busy:
            self.removals_review_button.pack(side="right")
        else:self.removals_review_button.pack_forget()
        self.nav_items[str(self.removals_tab)].badge="…" if busy else ""
        self.nav_items[str(self.removals_tab)].draw()
        if info.get("log"):self._append_removals_log(info["log"])

    def start_removals(self):
        if not self.telegram or not self.telegram.connected:
            self._update_removals({"state":"offline","message":"Сначала подключи Telegram кнопкой вверху."})
            return
        self._update_removals({"state":"connecting","busy":True,"message":"Запускаю снятия…"})
        if self.telegram.start_removals() is False:
            self._update_removals({"state":"error","message":"Не удалось запустить снятия. Подключи Telegram заново."})

    def stop_removals(self):
        if self.telegram:
            self.removals_status_var.set("Останавливаю очередь…")
            if self.telegram.stop_removals() is False:
                self._update_removals({"state":"offline","message":"Telegram отключён. Прогресс сохранён."})

    def review_removal_send(self):
        if not self._removals_uncertain or not self.telegram or not self.telegram.connected:
            self.removals_status_var.set("Подключи Telegram и запусти снятия, чтобы проверить сохранённую отправку.")
            return
        from tkinter import messagebox
        row=self._removals_uncertain
        answer=messagebox.askyesnocancel("Проверка отправки снятия",
            f"Проверь свой диалог с @AresMineRobot. Есть ли там эта отправленная команда?\n\nID {row['id']}\n{row['command']}\n\nДа — команда есть, повторять её не нужно.\nНет — команды нет, её можно отправить при следующем запуске.\nОтмена — оставить состояние для проверки.",parent=self.root)
        if answer is not None:self.telegram.resolve_removal_send(answer)

    def stop_monitoring(self):
        super().stop_monitoring()
        self._update_removals({"state":"offline","busy":False,"message":"Telegram отключён. Прогресс снятий сохранён."})

    def clear_simulator(self):
        super().clear_simulator()
        if not self.simulator_busy: self.confidence_var.set(0)

    def _copy_text(self,widget):
        text=widget.get("1.0","end-1c").strip()
        if not text or text=="—" or (widget==getattr(self,"simulator_answer_text",None) and not self.simulator_last_question):
            self._toast("Пока нечего копировать","info")
            return
        self.root.clipboard_clear();self.root.clipboard_append(text)
        self._toast("Скопировано в буфер обмена")

    def _toast(self,text,kind="ok"):
        if self._toast_job:
            self.root.after_cancel(self._toast_job)
        if self._toaster is None:
            self._toaster=Toast(self.root,P,self.fnt)
        try:
            self._toaster.show(text,kind)
        except tk.TclError:
            pass
        self._toast_job=self.root.after(2700,self._dismiss_toast)

    def _dismiss_toast(self):
        if self._toaster is not None:
            try:self._toaster.close()
            except tk.TclError:pass
        self._toast_job=None

    # ------------------------------------------------------------- training
    def _build_training(self):
        pane=tk.PanedWindow(self.training_tab,orient="horizontal",bd=0,bg=P["background"],sashwidth=14,opaqueresize=True)
        pane.pack(fill="both",expand=True)
        left=self._card(pane,"Очередь обучения");right=self._card(pane,"Решение обращения")
        pane.add(left,minsize=285,stretch="always");pane.add(right,minsize=450,stretch="always")
        actions=ttk.Frame(left,style="Card.TFrame");actions.pack(fill="x",pady=(0,12))
        self._action_menu(actions,"Действия ▾",[
            ("Удалить выбранные",self.delete_pending),("Очистить мусор",self.cleanup_pending),
            ("Удалить всю очередь",self.delete_all_pending)]).pack(side="left")
        ttk.Label(left,text="Выбери тикет, чтобы уточнить ответ",style="Muted.Card.TLabel",wraplength=300).pack(side="bottom",fill="x",pady=(10,0))
        holder=ttk.Frame(left,style="Card.TFrame");holder.pack(fill="both",expand=True)
        self.pending_tree=ttk.Treeview(holder,columns=("ticket","question","date"),show="headings",selectmode="extended",height=5)
        for col,title,width in (("ticket","Тикет",70),("question","Сообщение",230),("date","Обновлён",140)):
            self.pending_tree.heading(col,text=title);self.pending_tree.column(col,width=width,minwidth=70,stretch=col=="question")
        self._scroll_tree(holder,self.pending_tree)
        self.pending_tree.bind("<<TreeviewSelect>>",lambda e:self._pending_selected())
        self.pending_tree.bind("<Delete>",lambda e:self.delete_pending())
        pages=self._tab_pages(right,("Решение","Фильтры","Автозакрытие"))
        details,filters,closing=pages
        ttk.Label(details,text="Сообщение игрока",style="Muted.Card.TLabel").pack(anchor="w",pady=(0,5))
        self.pending_question_text=self._text_widget(details,height=3,readonly=True)
        self.pending_question_text.pack(fill="x",pady=(0,10))
        candidates,manual,last=self._tab_pages(details,("Кандидаты","Свой ответ","Сохранённое"))
        actions=ttk.Frame(candidates,style="Card.TFrame");actions.pack(side="bottom",fill="x",pady=(10,0))
        AnimatedButton(actions,text="Обучить на выбранном",style="Accent.TButton",command=self.training_teach_selected).pack(side="left")
        AnimatedButton(actions,text="Открыть шаблон",style="Ghost.TButton",command=self.training_open_selected_template).pack(side="right")
        self.training_candidate_answer_text=self._text_widget(candidates,height=3,readonly=True)
        self.training_candidate_answer_text.pack(side="bottom",fill="x",pady=(8,0))
        holder=ttk.Frame(candidates,style="Card.TFrame");holder.pack(fill="both",expand=True)
        self.training_candidates_tree=ttk.Treeview(holder,columns=("score","name","examples"),show="headings",selectmode="browse",height=3)
        for col,title,width in (("score","Близость",78),("name","Шаблон",230),("examples","Примеры",75)):
            self.training_candidates_tree.heading(col,text=title);self.training_candidates_tree.column(col,width=width,minwidth=70,stretch=col=="name")
        self._scroll_tree(holder,self.training_candidates_tree)
        self.training_candidates_tree.bind("<<TreeviewSelect>>",lambda e:self._training_candidate_selected())
        self.training_candidates_tree.bind("<Double-1>",lambda e:self.training_teach_selected())
        ttk.Label(manual,text="Название шаблона · необязательно",style="Muted.Card.TLabel").pack(anchor="w",pady=(0,5))
        self.training_manual_name_var=tk.StringVar()
        self.training_manual_name_entry=ttk.Entry(manual,textvariable=self.training_manual_name_var)
        self.training_manual_name_entry.pack(fill="x",pady=(0,10))
        actions=ttk.Frame(manual,style="Card.TFrame");actions.pack(side="bottom",fill="x",pady=(10,0))
        AnimatedButton(actions,text="Создать шаблон и обучить",style="Accent.TButton",command=self.training_create_manual_template).pack(side="left")
        AnimatedButton(actions,text="Очистить",style="Ghost.TButton",command=lambda:(self.training_manual_answer_text.delete("1.0","end"),self.training_manual_name_var.set(""))).pack(side="right")
        self.training_manual_answer_text=self._text_widget(manual,height=5);self.training_manual_answer_text.pack(fill="both",expand=True)
        self.learned_answer_text=self._text_widget(last,height=5,readonly=True);self.learned_answer_text.pack(fill="both",expand=True)
        self._set_text(self.learned_answer_text,"Пока новых решений в этой сессии нет.")
        ttk.Label(filters,text="Не учиться на этих фразах · по одной на строку",style="Muted.Card.TLabel").pack(anchor="w",pady=(0,7))
        controls=ttk.Frame(filters,style="Card.TFrame");controls.pack(side="bottom",fill="x",pady=(10,0))
        self._checkbutton(controls,"Отбрасывать служебные сообщения",self.learning_noise_var).pack(anchor="w")
        ttk.Combobox(controls,textvariable=self.learning_policy_var,values=("Умно: неизвестные + исправления","Только неизвестные","Все ручные ответы"),state="readonly").pack(fill="x",pady=(7,10))
        actions=ttk.Frame(controls,style="Card.TFrame");actions.pack(fill="x")
        AnimatedButton(actions,text="Сохранить фильтр",style="Accent.TButton",command=self.save_learning_filters).pack(side="left")
        AnimatedButton(actions,text="Очистить очередь",style="Secondary.TButton",command=self.cleanup_pending).pack(side="right")
        self.learning_exclusions_text=self._text_widget(filters,height=5);self.learning_exclusions_text.pack(fill="both",expand=True)
        ttk.Label(closing,text="Закрывать по точным фразам · по одной на строку",style="Muted.Card.TLabel").pack(anchor="w",pady=(0,7))
        controls=ttk.Frame(closing,style="Card.TFrame");controls.pack(side="bottom",fill="x",pady=(10,0))
        self._checkbutton(controls,"Разрешить правила автозакрытия",self.auto_close_enabled_var).pack(anchor="w")
        ttk.Label(controls,text="Совпадение имеет приоритет над ответами. Команда: t c <номер тикета>.",style="Muted.Card.TLabel",wraplength=500).pack(anchor="w",pady=(5,8))
        AnimatedButton(controls,text="Сохранить фильтры",style="Accent.TButton",command=self.save_learning_filters).pack(anchor="e")
        self.global_close_text=self._text_widget(closing,height=5);self.global_close_text.pack(fill="both",expand=True)
        self._responsive_pane(pane,.37)

    # ------------------------------------------------------------ templates
    def _build_templates(self):
        toolbar=ttk.Frame(self.templates_tab,style="Panel.TFrame",padding=14);toolbar.pack(fill="x",pady=(0,14))
        glass=tk.Canvas(toolbar,width=26,height=26,bg=P["surface"],highlightthickness=0)
        glass.pack(side="left",padx=(4,8))
        glass.create_image(13,13,image=photo(glass,icon_name("search",20,P["muted"])))
        # Packed first so the button keeps its full width when the window is narrow.
        AnimatedButton(toolbar,text="+  Новый шаблон",style="Accent.TButton",command=self.new_template).pack(side="right")
        search=ttk.Entry(toolbar,textvariable=self.template_search_var)
        search.pack(side="left",fill="x",expand=True,padx=(0,10))
        search.bind("<Escape>",lambda e:self.template_search_var.set(""))
        search.bind("<FocusIn>",lambda e:glass.itemconfigure(1,image=photo(glass,icon_name("search",20,P["accent"]))),add="+")
        search.bind("<FocusOut>",lambda e:glass.itemconfigure(1,image=photo(glass,icon_name("search",20,P["muted"]))),add="+")
        combo=ttk.Combobox(toolbar,textvariable=self.template_filter_var,values=("Все","Включённые","Отключённые","Мои","Исходные"),state="readonly",width=14)
        combo.pack(side="left",padx=(0,10));combo.bind("<<ComboboxSelected>>",lambda e:self._refresh_templates())
        self.template_search_var.trace_add("write",self._schedule_template_refresh)
        pane=self.template_split_pane=tk.PanedWindow(self.templates_tab,orient="horizontal",bd=0,bg=P["background"],sashwidth=14,opaqueresize=True)
        pane.pack(fill="both",expand=True)
        left=self._card(pane,"Библиотека ответов");right=self._card(pane,"Редактор шаблона")
        pane.add(left,minsize=285,stretch="always");pane.add(right,minsize=450,stretch="always")
        actions=ttk.Frame(left,style="Card.TFrame");actions.pack(fill="x",pady=(0,12))
        self._action_menu(actions,"Выделить ▾",[
            ("Отметить видимые",lambda:self._mark_visible_templates(True)),
            ("Снять отметки",lambda:self._mark_visible_templates(False)),("Инвертировать",self._invert_visible_templates)]).pack(side="left")
        self._action_menu(actions,"Действия ▾",[
            ("Включить отмеченные",lambda:self._set_marked_templates_enabled(True)),
            ("Выключить отмеченные",lambda:self._set_marked_templates_enabled(False)),
            ("Удалить отмеченные",self._delete_marked_templates),None,
            ("Защитить отмеченные",lambda:self._lock_marked_templates(True)),
            ("Снять защиту с отмеченных",lambda:self._lock_marked_templates(False)),None,
            ("Включить все шаблоны",lambda:self._set_all_templates_enabled(True)),
            ("Выключить все шаблоны",lambda:self._set_all_templates_enabled(False)),
            ("Вернуть базовые шаблоны",self._restore_seed_templates),
            ("Очистить подозрительные",self._cleanup_suspicious_templates),
            ("Удалить все шаблоны",self._delete_all_templates)]).pack(side="right")
        ttk.Label(left,textvariable=self.template_selection_var,style="Muted.Card.TLabel").pack(side="bottom",anchor="w",pady=(10,0))
        holder=ttk.Frame(left,style="Card.TFrame");holder.pack(fill="both",expand=True)
        self.templates_tree=ttk.Treeview(holder,columns=("marked","status","name","priority","mode","examples","manual"),show="headings",selectmode="extended",height=5)
        for col,title,width in (("marked","✓",35),("status","Статус",72),("name","Шаблон",245),("priority","Приор.",57),
                                ("mode","Режим",66),("examples","Прим.",55),("manual","Твоих",55)):
            self.templates_tree.heading(col,text=title)
            self.templates_tree.column(col,width=width,minwidth=width if col!="name" else 175,stretch=col=="name",anchor="w" if col=="name" else "center")
        self._scroll_tree(holder,self.templates_tree)
        self.templates_tree.bind("<<TreeviewSelect>>",lambda e:self._template_selected())
        self.templates_tree.bind("<ButtonPress-1>",self._template_tree_press,add="+")
        self.templates_tree.bind("<B1-Motion>",self._template_tree_drag,add="+")
        self.templates_tree.bind("<ButtonRelease-1>",self._template_tree_release,add="+")
        self.templates_tree.bind("<Double-1>",lambda e:self._toggle_focused_template_enabled())
        self.templates_tree.bind("<space>",self._toggle_selected_template_marks)
        self.templates_tree.bind("<Delete>",lambda e:self._delete_marked_templates())
        self.templates_tree.bind("<Control-a>",self._select_all_visible_templates)
        self.templates_tree.bind("<Button-3>",self._template_context_menu)
        self.templates_tree.tag_configure("enabled",foreground=P["text_soft"])
        self.templates_tree.tag_configure("disabled",foreground=P["muted_dim"])
        self.templates_tree.tag_configure("manual",foreground=P["manual_tag"])
        bottom=ttk.Frame(right,style="Card.TFrame");bottom.pack(side="bottom",fill="x",pady=(12,0))
        self._checkbutton(bottom,"Включён",self.template_enabled_var).pack(side="left")
        AnimatedButton(bottom,text="Сохранить изменения",style="Accent.TButton",command=self.save_template).pack(side="right")
        ttk.Label(right,text="Название",style="Muted.Card.TLabel").pack(anchor="w",pady=(0,5))
        ttk.Entry(right,textvariable=self.template_name_var).pack(fill="x",pady=(0,10))
        meta=ttk.Frame(right,style="Card.TFrame");meta.pack(fill="x",pady=(0,8))
        meta.columnconfigure(0,weight=1)
        ttk.Combobox(meta,textvariable=self.template_match_mode_var,values=("Умный","Только точное совпадение"),state="readonly",width=21).grid(row=0,column=0,sticky="ew",padx=(0,10))
        ttk.Label(meta,text="Приоритет",style="Muted.Card.TLabel").grid(row=0,column=1,padx=(0,8))
        ttk.Spinbox(meta,from_=0,to=100,textvariable=self.template_priority_var,width=4).grid(row=0,column=2)
        ttk.Label(right,textvariable=self.template_source_var,style="Muted.Card.TLabel",wraplength=470).pack(fill="x",pady=(0,8))
        self.template_editor_notebook=ttk.Notebook(right);self.template_editor_notebook.pack(fill="both",expand=True)
        pages=[]
        for title in ("Текст ответа","Правила","Примеры","Автоответы"):
            page=ttk.Frame(self.template_editor_notebook,style="Card.TFrame",padding=(0,9));self.template_editor_notebook.add(page,text=title);pages.append(page)
        answer,rules,examples,auto=pages
        self.template_reply_tab = auto
        self.reply_limit_check = self._checkbutton(auto,'Защита от повторных автоответов',self.template_reply_limit_var)
        self.reply_limit_check.pack(anchor='w',pady=(4,14))
        info=self._text_widget(auto,height=5,readonly=True);info.pack(fill='both',expand=True)
        self._set_text(info,'До 2 автоответов на один тикет.\n\nДля одного TG/VK-клиента этот шаблон используется до 2 раз за последние 24 часа, даже в новых тикетах. Если ID клиента нет в заголовке, работает лимит по номеру тикета.\n\nПосле сообщения «не помогло» следующий ответ остаётся за тобой. Черновики лимит не расходуют.\n\nСними галочку и сохрани шаблон, чтобы отключить эту защиту. Ручной ответ сотрудника и защита от повторной отправки одного сообщения действуют всегда.')
        ttk.Label(answer,text="t a {ticket_id} … @@@",style="Muted.Card.TLabel").pack(anchor="w",pady=(0,7))
        self.template_answer_text=self._text_widget(answer,height=5);self.template_answer_text.pack(fill="both",expand=True)
        rule_pages=self._tab_pages(rules,("Триггеры","Стоп-фразы","Закрытие"))
        for page,attr,description in [
            (rule_pages[0],"template_triggers_text","Фразы помогают найти ответ; решение учитывает весь контекст."),
            (rule_pages[1],"template_negative_text","Совпадение исключает этот шаблон из кандидатов."),
            (rule_pages[2],"template_close_text","Точная фраза закрывает тикет без автоответа.")]:
            ttk.Label(page,text=description+" По одной на строку; символы сохраняются.",style="Muted.Card.TLabel",wraplength=480).pack(anchor="w",pady=(0,8))
            widget=self._text_widget(page,height=5);widget.pack(fill="both",expand=True);setattr(self,attr,widget)
        ttk.Label(examples,text="Подтверждённые ситуации для этого шаблона",style="Muted.Card.TLabel").pack(anchor="w",pady=(0,8))
        self.template_examples_text=self._text_widget(examples,height=5,readonly=True);self.template_examples_text.pack(fill="both",expand=True)
        self._build_template_protection(left,right,actions)
        self._responsive_pane(pane,.38)

    def _tab_pages(self,parent,titles):
        tabs=ttk.Notebook(parent);tabs.pack(fill="both",expand=True)
        pages=[]
        for title in titles:
            page=ttk.Frame(tabs,style="Card.TFrame",padding=(0,9));tabs.add(page,text=title);pages.append(page)
        return pages

    MENU_ICONS={"Удалить выбранные":"trash","Очистить мусор":"broom","Удалить всю очередь":"trash",
                "Отметить видимые":"check_all","Снять отметки":"uncheck","Инвертировать":"invert",
                "Включить отмеченные":"toggle_on","Выключить отмеченные":"toggle_off","Удалить отмеченные":"trash",
                "Включить все шаблоны":"toggle_on","Выключить все шаблоны":"toggle_off",
                "Вернуть базовые шаблоны":"history","Очистить подозрительные":"broom","Удалить все шаблоны":"trash",
                "Удалить":"trash","Защитить отмеченные":"lock","Снять защиту с отмеченных":"unlock",
                "Защитить от удаления":"lock","Снять защиту":"unlock"}

    def _popup_menu(self,parent):
        return PopupMenu(parent,P,self.fnt)

    def _action_menu(self,parent,title,items):
        """A button that drops a themed menu; its chevron flips while the menu is open."""
        menu=self._popup_menu(parent)
        for item in items:
            if item is None:menu.add_separator()
            else:menu.add_command(label=item[0],command=item[1],icon=self.MENU_ICONS.get(item[0]))
        closed=self._padded_icon("chevdown",17,P["text_soft"],gap=6,side="left")
        opened=self._padded_icon("chevup",17,P["accent"],gap=6,side="left")
        def show():
            # A click on the button while the menu is open first closes it through focus loss.
            if menu.winfo_ismapped() or time.monotonic()-menu.closed_at<.25:
                menu.unpost();return
            button.configure(image=opened)
            menu.on_close=lambda:button.winfo_exists() and button.configure(image=closed)
            menu.post(button.winfo_rootx(),button.winfo_rooty()+button.winfo_height()+4)
        button=AnimatedButton(parent,text=title.replace("▾","").strip(),style="Secondary.TButton",command=show,
                              image=closed,compound="right")
        # Menu buttons sit in pairs in narrow columns, so they use tighter padding.
        ttk.Style(button).configure(str(button.cget("style")),padding=(12,10))
        button.configure(width=0)  # clam's minimum width would push the pair past the column edge
        button.menu=menu
        return button

    def _set_templates_sash(self,pane):
        if not pane.winfo_exists(): return
        width=pane.winfo_width()
        if width>100: pane.sash_place(0,int(width*.43),1)

    def _remove_hero(self,page):
        for child in page.winfo_children():
            if isinstance(child,ttk.Frame) and str(child.cget("style"))=="Hero.TFrame":
                child.destroy();break

    def _responsive_pane(self,pane,ratio=.45):
        if hasattr(pane,"_ares_initialized"):return
        pane._ares_initialized=False
        pane._ares_ratio=ratio
        def resize(event):
            if event.width<100:return
            if not pane._ares_initialized:
                pane.sash_place(0,int(event.width*ratio),1)
                pane._ares_initialized=True
        pane.bind("<Configure>",resize,add="+")

    def _polish(self,parent):
        for widget in parent.winfo_children():
            if isinstance(widget,tk.PanedWindow):
                widget.configure(bg=P["background"],sashwidth=14,sashcursor="sb_h_double_arrow")
                # The builders choose pane minima to keep editor controls reachable.
                self._responsive_pane(widget)
            elif isinstance(widget,ttk.Label) and not isinstance(widget,CountLabel):
                txt=str(widget.cget("text"))
                if len(txt)>60 or int(widget.cget("wraplength") or 0)>0:
                    widget.configure(wraplength=430,justify="left")
                    # Wrap to the actual parent, including nested editors on smaller screens.
                    widget.master.bind("<Configure>",lambda e,w=widget:w.configure(wraplength=max(140,e.width-42)) if w.winfo_exists() else None,add="+")
            elif isinstance(widget,ttk.Button): widget.configure(cursor="hand2")
            self._polish(widget)

    def _scroll_tree(self,holder,tree):
        holder.rowconfigure(0,weight=1);holder.columnconfigure(0,weight=1)
        sy=ttk.Scrollbar(holder,orient="vertical",command=tree.yview)
        sx=ttk.Scrollbar(holder,orient="horizontal",command=tree.xview)
        tree.grid(row=0,column=0,sticky="nsew");sy.grid(row=0,column=1,sticky="ns");sx.grid(row=1,column=0,sticky="ew")
        sy.grid_remove();sx.grid_remove()
        tree.configure(yscrollcommand=self._autohide(sy,grid=True),xscrollcommand=self._autohide(sx,grid=True))
        tree_hover(tree,P["hover_row"])

    # -------------------------------------------------------------- history
    def _build_history(self):
        card=self._card(self.history_tab,"");card.pack(fill="both",expand=True)
        toolbar=ttk.Frame(card,style="Card.TFrame");toolbar.pack(fill="x",pady=(0,14))
        glass=tk.Canvas(toolbar,width=26,height=26,bg=P["surface"],highlightthickness=0)
        glass.pack(side="left",padx=(0,8))
        glass.create_image(13,13,image=photo(glass,icon_name("search",20,P["muted"])))
        ttk.Label(toolbar,text="Найти запись",style="Card.TLabel").pack(side="left",padx=(0,12))
        self.history_search_var=tk.StringVar()
        self.history_filter_entry=ttk.Entry(toolbar,textvariable=self.history_search_var,width=25)
        self.history_filter_entry.pack(side="left",fill="x",expand=True)
        self.history_filter_entry.bind("<Escape>",lambda e:self.history_search_var.set(""))
        self.history_search_var.trace_add("write",self._schedule_history)
        AnimatedButton(toolbar,text="Обновить",style="Secondary.TButton",command=self._refresh_history).pack(side="left",padx=(10,0))
        bottom=ttk.Frame(card,style="Card.TFrame");bottom.pack(side="bottom",fill="x",pady=(12,0))
        self.history_count=tk.StringVar(value="Нет записей")
        ttk.Label(bottom,textvariable=self.history_count,style="Muted.Card.TLabel").pack(side="left")
        AnimatedButton(bottom,text="Экспорт базы",style="Ghost.TButton",command=self.export_knowledge).pack(side="right")
        AnimatedButton(bottom,text="Разрешить повторную обработку",style="Secondary.TButton",command=self.unmark_selected_history).pack(side="right",padx=(8,8))
        holder=ttk.Frame(card,style="Card.TFrame");holder.pack(fill="both",expand=True)
        self.history_tree=ttk.Treeview(holder,columns=("ticket","action","confidence","question","date"),show="headings",selectmode="browse")
        for col,title,width in (("ticket","Тикет",85),("action","Действие",125),("confidence","Точность",85),("question","Сообщение",370),("date","Дата",150)):
            self.history_tree.heading(col,text=title,command=lambda c=col:self._sort_history(c))
            self.history_tree.column(col,width=width,minwidth=85,stretch=col=="question")
        self._scroll_tree(holder,self.history_tree)
        self.history_tree.bind("<Double-1>",lambda e:self.show_history_details())
        self.history_tree.bind("<Return>",lambda e:self.show_history_details())

    def _schedule_history(self,*args):
        if self._history_job:self.root.after_cancel(self._history_job)
        self._history_job=self.root.after(160,self._refresh_history)

    def _sort_history(self,column):
        descending=not self._history_sort[1] if self._history_sort and self._history_sort[0]==column else False
        self._history_sort=(column,descending)
        self._refresh_history()

    def _refresh_history(self):
        self._history_job=None
        if not hasattr(self,"history_tree"):return
        selected=self.history_tree.selection()
        # Новые тикеты не должны сбивать прокрутку и фокус, пока модератор читает историю.
        top=self.history_tree.yview()[0] if self.history_tree.get_children() else 0.0
        focused=self.history_tree.focus()
        self.root.after_idle(lambda:self._restore_history_view(top,focused))
        # Detached rows from the previous search still own their tree item IDs.
        for iid in tuple(self.history_data):
            if self.history_tree.exists(iid):self.history_tree.delete(iid)
        super()._refresh_history()
        for iid in self.history_tree.get_children():
            action=str(self.history_tree.set(iid,"action"))
            self.history_tree.set(iid,"action",ACTION_LABELS.get(action,action))
        query=self.history_search_var.get().casefold().strip()
        for iid in self.history_tree.get_children():
            data=self.history_data[iid]
            haystack=" ".join(str(v) for v in self.history_tree.item(iid,"values"))+" "+str(data.get("answer",""))+" "+str(data.get("question",""))
            if query and not all(word in haystack.casefold() for word in query.split()):self.history_tree.detach(iid)
        if self._history_sort:
            col,desc=self._history_sort
            def value(iid):
                text=str(self.history_tree.set(iid,col))
                if col in ("ticket","confidence"):
                    try:return float(text.strip("#% "))
                    except ValueError:return -1
                return text.casefold()
            for n,iid in enumerate(sorted(self.history_tree.get_children(),key=value,reverse=desc)):self.history_tree.move(iid,"",n)
        for i,iid in enumerate(self.history_tree.get_children()):self.history_tree.item(iid,tags=("odd" if i%2 else "even",))
        self.history_tree.tag_configure("odd",background=P["stripe"])
        shown=len(self.history_tree.get_children())
        self.history_count.set(f"Записей: {shown} из {len(self.history_data)}" if query else f"Записей: {shown}  ·  Двойной клик — детали")
        for iid in selected:
            if iid in self.history_tree.get_children():self.history_tree.selection_set(iid)

    def _restore_history_view(self,top,focused):
        tree=getattr(self,"history_tree",None)
        if tree is None or not tree.winfo_exists():return
        if top:tree.yview_moveto(top)
        if focused and tree.exists(focused):tree.focus(focused)

    # -------------------------------------------------------------- context
    def _build_local_ai(self):
        overview=ttk.Frame(self.ai_tab,style="Hero.TFrame",padding=24)
        overview.pack(fill="x",pady=(0,16))
        ttk.Label(overview,text="КАК ДУМАЕТ АССИСТЕНТ",style="HeroSub.TLabel",foreground=P["accent"],font=self.fnt(8,"bold")).pack(anchor="w",pady=(0,6))
        ttk.Label(overview,text="Сначала смысл. Потом ответ.",style="HeroTitle.TLabel").pack(anchor="w")
        ttk.Label(overview,text="Ассистент сопоставляет весь вопрос с подтверждёнными примерами из твоей базы.",style="HeroSub.TLabel").pack(anchor="w",pady=(9,0))
        card=self._card(self.ai_tab,"Как принимается решение")
        card.pack(fill="both",expand=True)
        steps=[("01","Определяет ситуацию","Учитывает действие и соседние слова: «аккаунт забанили» и «потерял доступ» требуют разных ответов.",P["accent"]),
               ("02","Находит подходящие примеры","Сравнивает вопрос с подтверждёнными обращениями и учитывает правила шаблона.",P["blue"]),
               ("03","Проверяет противоречия","Если варианты слишком близки или смысл не совпадает, оставляет решение тебе.",P["amber"]),
               ("04","Применяет твои настройки","Использует выбранный режим, пороги уверенности и отдельные точные правила автозакрытия.",P["green"])]
        for number,title,desc,color in steps:
            row=ttk.Frame(card,style="Card.TFrame",padding=(0,9));row.pack(fill="x")
            badge=tk.Canvas(row,width=46,height=46,bg=P["surface"],highlightthickness=0)
            badge.pack(side="left",anchor="n",padx=(0,18))
            badge.create_image(0,0,anchor="nw",image=photo(badge,f"chip46_{color[1:].lower()}.png"))
            badge.create_text(23,23,text=number,fill=color,font=self.fnt(13,"bold"))
            body=ttk.Frame(row,style="Card.TFrame");body.pack(fill="x",expand=True)
            ttk.Label(body,text=title,style="Section.TLabel").pack(anchor="w",pady=(2,0))
            ttk.Label(body,text=desc,style="Muted.Card.TLabel",wraplength=800).pack(anchor="w",pady=(4,0))
        AnimatedButton(card,text="Попробовать на сообщении   →",style="Accent.TButton",command=self.open_simulator).pack(anchor="w",pady=(12,0))

    # ------------------------------------------------------------- settings
    def _build_settings(self):
        actions=ttk.Frame(self.settings_tab);actions.pack(side="bottom",fill="x",pady=(12,0))
        ttk.Label(actions,text="Изменения применяются после сохранения",style="Sub.TLabel").pack(side="left")
        AnimatedButton(actions,text="Сохранить настройки",style="Accent.TButton",command=self.save_settings).pack(side="right")
        tabs=ttk.Notebook(self.settings_tab,style="Page.TNotebook");tabs.pack(fill="both",expand=True)
        self.settings_sections=tabs
        pages=[]
        for title in ("Telegram","Команды","Ответы","Голос и Windows","Данные"):
            page=ttk.Frame(tabs,padding=(0,4));tabs.add(page,text=title);pages.append(self._scroll_page(page))
        telegram,commands,answers,voice,data=pages
        card=self._card(telegram,"Подключение Telegram");card.pack(fill="x")
        for row,label,var,hidden in [(0,"API ID",self.api_id_var,False),(1,"API Hash",self.api_hash_var,True),
                                     (2,"Чат с тикетами",self.source_chat_var,False),(3,"Чат для ответов",self.response_chat_var,False)]:
            self._form_row(card,row,label,ttk.Entry(card,textvariable=var,show="•" if hidden else ""))
        ttk.Label(card,text="Вход через QR-код",style="Section.TLabel").grid(row=4,column=0,columnspan=2,sticky="w",pady=(22,8))
        ttk.Label(card,text="В Telegram открой: Настройки → Устройства → Подключить устройство. Затем нажми «Подключить Telegram» вверху окна.",style="Muted.Card.TLabel",wraplength=760).grid(row=5,column=0,columnspan=2,sticky="w")
        card=self._card(commands,"Команды через AresStaffRobot");card.pack(fill="x",pady=(0,14))
        self._checkbutton(card,"Перенаправлять /c list из ЛС выбранного человека",self.command_relay_enabled_var).grid(row=0,column=0,columnspan=2,sticky="w",pady=(4,12))
        self._form_row(card,1,"Telegram ID отправителя",ttk.Entry(card,textvariable=self.command_relay_user_var))
        self._form_row(card,2,"Уведомить о задержке, сек.",ttk.Spinbox(card,from_=5,to=60,textvariable=self.command_relay_timeout_var,width=10))
        self._checkbutton(card,"Пересылать оригинал ответа, включая файлы и медиа",self.command_relay_forward_var).grid(row=3,column=0,columnspan=2,sticky="w",pady=(12,8))
        ttk.Label(card,text="После уведомления о задержке ожидание продолжается до 60 секунд. Поздний результат принимается для исходного запроса. Длинные списки отправляются всеми частями по порядку. Обрабатываются только ЛС указанного ID.",style="Muted.Card.TLabel",wraplength=760).grid(row=4,column=0,columnspan=2,sticky="w",pady=(4,0))
        card=self._card(commands,"Статус и восстановление");card.pack(fill="x",pady=(0,14))
        ttk.Label(card,textvariable=self.command_relay_status_var,style="Card.TLabel",wraplength=760).pack(anchor="w",pady=(5,12))
        ttk.Label(card,text="Задержанный ответ принимается автоматически. Если запрос завис, проверь диалог с ботом и нажми «Сбросить и возобновить»: текущий запрос будет отменён, обработчик подключится заново.",style="Muted.Card.TLabel",wraplength=760).pack(anchor="w",pady=(0,16))
        row=ttk.Frame(card,style="Card.TFrame");row.pack(fill="x")
        self.command_relay_resume_button=AnimatedButton(row,text="Сбросить и возобновить",style="Accent.TButton",command=self.resume_command_relay)
        self.command_relay_resume_button.pack(side="left")
        AnimatedButton(row,text="Открыть бота ↗",style="Secondary.TButton",command=lambda:webbrowser.open("https://t.me/AresStaffRobot")).pack(side="left",padx=(10,0))
        card=self._card(commands,"Как пользоваться");card.pack(fill="x")
        ttk.Label(card,text="/c list 14 Prop1oonat",style="Section.TLabel",foreground=P["accent"]).pack(anchor="w",pady=(6,12))
        ttk.Label(card,text="Человек присылает команду тебе в ЛС → Ares запрашивает список у @AresStaffRobot → ответ возвращается в тот же диалог. Одновременно выполняется один запрос. Используется уже подключённая сессия Telegram.",style="Muted.Card.TLabel",wraplength=760).pack(anchor="w")
        ttk.Label(card,text="Общая «Пауза» действует и на команды. Во время запроса не отправляй другие команды этому боту вручную: его ответы без привязки к сообщению невозможно однозначно сопоставить.",style="Muted.Card.TLabel",wraplength=760).pack(anchor="w",pady=(12,0))
        card=self._card(answers,"Уверенность и отправка");card.pack(fill="x",pady=(0,14))
        for row,label,widget in [
            (0,"Порог черновика, %",ttk.Spinbox(card,from_=70,to=99,textvariable=self.draft_threshold_var,width=9)),
            (1,"Порог автоответа, %",ttk.Spinbox(card,from_=85,to=100,textvariable=self.auto_threshold_var,width=9)),
            (2,"Задержка перед отправкой, сек.",ttk.Entry(card,textvariable=self.auto_delay_var,width=10)),
            (3,"Автоответов в минуту",ttk.Spinbox(card,from_=1,to=30,textvariable=self.auto_limit_var,width=9)),
        ]:self._form_row(card,row,label,widget)
        card=self._card(answers,"Обработка обращений");card.pack(fill="x")
        for label,var in [("Обучаться по моим исходящим ответам",self.learning_enabled_var),
                          ("Открывать Telegram после вставки черновика",self.open_telegram_var),
                          ("Разрешить точные правила автозакрытия",self.auto_close_enabled_var)]:
            self._checkbutton(card,label,var).pack(anchor="w",pady=4)
        ttk.Label(card,text="Правила и фильтры обучения доступны в разделе «Обучение». Команда закрытия: t c {ticket_id}.",style="Muted.Card.TLabel",wraplength=760).pack(anchor="w",pady=(10,0))
        card=self._card(voice,"Голосовые команды и запуск");card.pack(fill="x")
        for label,var in [("Голосовые команды",self.voice_enabled_var),("Приветствие при запуске",self.startup_voice_var),
                          ("Требовать слово «Алексей» перед командой",self.wake_word_var),("Запускать вместе с Windows",self.autostart_var)]:
            self._checkbutton(card,label,var).pack(anchor="w",pady=5)
        ttk.Label(card,text="Микрофон",style="Section.TLabel").pack(anchor="w",pady=(20,9))
        mic=ttk.Frame(card,style="Card.TFrame");mic.pack(fill="x")
        self.microphone_combo=ttk.Combobox(mic,textvariable=self.microphone_var,state="readonly")
        self.microphone_combo.pack(side="left",fill="x",expand=True)
        AnimatedButton(mic,text="Обновить",style="Secondary.TButton",command=self.refresh_microphones).pack(side="right",padx=(10,0))
        ttk.Label(card,text="Команды: открыть, скрыть, режим черновик, режим авто, режим обучения, пауза, продолжить.",style="Muted.Card.TLabel",wraplength=750).pack(anchor="w",pady=(16,0))
        self._build_voice_phrases(voice)
        card=self._card(data,"Резервная копия и перенос");card.pack(fill="x",pady=(0,14))
        ttk.Label(card,text="Пакет сохраняет шаблоны, триггеры, стоп-фразы, приоритеты, обучение и настройки. Текущая база используется и после обновления EXE.",style="Card.TLabel",wraplength=800).pack(anchor="w",pady=(5,16))
        row=ttk.Frame(card,style="Card.TFrame");row.pack(fill="x")
        AnimatedButton(row,text="Экспорт обновления",style="Accent.TButton",command=self.export_update_bundle).pack(side="left")
        AnimatedButton(row,text="Импорт обновления",style="Secondary.TButton",command=self.import_update_bundle).pack(side="left",padx=10)
        card=self._card(data,"Ares Assistant 2.9.9.6");card.pack(fill="x")
        ttk.Label(card,text="Оформление «Aurora», команды через AresStaffRobot, быстрый поиск Ctrl+K и защита от повторных автоответов.",style="Muted.Card.TLabel",wraplength=800).pack(anchor="w",pady=(4,14))
        AnimatedButton(card,text="Что нового",style="Secondary.TButton",command=self.show_whats_new).pack(anchor="w")

    def _scroll_page(self,page):
        canvas=tk.Canvas(page,bg=P["background"],highlightthickness=0,bd=0)
        sy=ttk.Scrollbar(page,orient="vertical",command=canvas.yview,style="Page.Vertical.TScrollbar")
        canvas.pack(side="left",fill="both",expand=True)
        canvas.configure(yscrollcommand=self._autohide(sy,pack={"side":"right","fill":"y","before":canvas}))
        body=ttk.Frame(canvas);win=canvas.create_window(0,0,window=body,anchor="nw")
        body.bind("<Configure>",lambda e:canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",lambda e:canvas.itemconfigure(win,width=max(100,e.width)))
        def wheel(event):
            if not canvas.winfo_ismapped():return
            target=self.root.winfo_containing(event.x_root,event.y_root)
            while target is not None and target != page:
                target=getattr(target,"master",None)
            if target==page and body.winfo_height()>canvas.winfo_height():
                delta=(-1 if event.num==4 else 1) if event.num in (4,5) else -event.delta/120
                smooth_scroll(canvas,delta)
        page.bind("<Destroy>",lambda e:None)
        self.root.bind("<MouseWheel>",wheel,add="+")
        self.root.bind("<Button-4>",wheel,add="+");self.root.bind("<Button-5>",wheel,add="+")
        return body

    # ---------------------------------------------------------- diagnostics
    def _build_diagnostics(self):
        row=ttk.Frame(self.diagnostics_tab);row.pack(fill="x",pady=(0,14))
        AnimatedButton(row,text="Запустить диагностику",style="Accent.TButton",command=self.run_diagnostics).pack(side="left")
        AnimatedButton(row,text="Собрать пакет логов",style="Secondary.TButton",command=self.export_diagnostics_bundle).pack(side="left",padx=10)
        AnimatedButton(row,text="Копировать отчёт",style="Ghost.TButton",command=lambda:self._copy_text(self.diagnostics_text)).pack(side="right")
        bottom=self._card(self.diagnostics_tab,"Обслуживание")
        bottom.pack(side="bottom",fill="x",pady=(14,0))
        buttons=ttk.Frame(bottom,style="Card.TFrame");buttons.pack(fill="x")
        import config
        AnimatedButton(buttons,text="Папка данных",style="Secondary.TButton",command=lambda:os.startfile(config.APPDATA_ROOT)).pack(side="left")
        AnimatedButton(buttons,text="Открыть лог",style="Ghost.TButton",command=lambda:os.startfile(config.LOG_DIR/"app.log")).pack(side="left",padx=8)
        AnimatedButton(buttons,text="Сбросить Telegram-сессию",style="Secondary.TButton",command=self.reset_session).pack(side="left")
        AnimatedButton(buttons,text="Удалить программу",style="Danger.TButton",command=self.uninstall_everything).pack(side="right")
        card=self._card(self.diagnostics_tab,"Отчёт о состоянии")
        card.pack(fill="both",expand=True)
        self.diagnostics_text=self._text_widget(card,height=9,readonly=True)
        self.diagnostics_text.pack(fill="both",expand=True)
        self._set_text(self.diagnostics_text,"Нажми «Запустить диагностику», чтобы проверить состояние базы, настройки и подключение.")

    # -------------------------------------------------------- command palette
    def open_command_palette(self):
        if self._command_window and self._command_window.winfo_exists():
            self._command_window.lift();self.command_entry.focus_set();return "break"
        win=self._command_window=tk.Toplevel(self.root)
        win.withdraw()
        win.title("Быстрый поиск")
        win.overrideredirect(True)
        try:
            key=ui_widgets.overlay_key();win.attributes("-transparentcolor",key)
        except tk.TclError:
            key=P["background"]
        win.configure(bg=key)
        w,h=620,476
        self.root.update_idletasks()
        x=self.root.winfo_rootx()+(self.root.winfo_width()-w)//2;y=self.root.winfo_rooty()+86
        shell=tk.Canvas(win,width=w,height=h,bg=key,bd=0,highlightthickness=0)
        shell.pack()
        shell.create_image(0,0,anchor="nw",image=nine_slice(shell,"palette.png",w,h,20))
        shell.create_image(26,42,anchor="w",image=photo(shell,icon_name("search",24,P["accent"])))
        query=tk.StringVar()
        self.command_entry=ttk.Entry(shell,textvariable=query,style="Palette.TEntry",font=(self.ui_font,14))
        shell.create_window(64,42,anchor="w",window=self.command_entry,width=w-64-80,height=34)
        shell.create_image(w-24,42,anchor="e",image=nine_slice(shell,"kbd.png",40,22,6))
        shell.create_text(w-44,42,text="Esc",fill=P["muted"],font=self.fnt(8))
        shell.create_line(18,76,w-18,76,fill=P["palette_line"])
        caption=shell.create_text(26,96,anchor="w",text="РАЗДЕЛЫ",fill=P["muted_dim"],font=self.fnt(8,"bold"))
        row_h,list_h=52,6*52
        lc=tk.Canvas(shell,width=w-24,height=list_h,bg=P["palette_surface"],bd=0,highlightthickness=0,yscrollincrement=0)
        shell.create_window(12,110,anchor="nw",window=lc)
        shell.create_line(18,h-46,w-18,h-46,fill=P["palette_line_soft"])
        hint_x=26
        for keys,label in (("↑ ↓","выбрать"),("Enter","открыть"),("Esc","закрыть")):
            kw=12+len(keys)*7
            shell.create_image(hint_x,h-23,anchor="w",image=nine_slice(shell,"kbd.png",kw,20,6))
            shell.create_text(hint_x+kw/2,h-23,text=keys,fill=P["text_soft"],font=self.fnt(8))
            shell.create_text(hint_x+kw+7,h-23,anchor="w",text=label,fill=P["muted"],font=self.fnt(8))
            hint_x+=kw+7+len(label)*7+22
        shell.create_text(w-26,h-23,anchor="e",text="Ares · быстрый поиск",fill=P["muted_dim"],font=self.fnt(8))
        commands=[]
        state={"index":0,"pill":None,"closing":False}
        page_colors=[P["accent"]]*5+[P["blue"]]*6

        def draw_rows():
            lc.delete("all")
            lw=w-24
            state["pill"]=lc.create_image(4,2,anchor="nw",image=nine_slice(lc,"row_active.png",lw-8,row_h-4,10),
                                          state="normal" if commands else "hidden")
            for i,(title,desc,glyph,color,tag,action) in enumerate(commands):
                cy=i*row_h+row_h/2
                lc.create_image(16,cy,anchor="w",image=photo(lc,f"chip34_{color[1:].lower()}.png"))
                lc.create_image(33,cy,image=photo(lc,icon_name(glyph,17,color)))
                lc.create_text(64,cy-9,anchor="w",text=title,fill=P["text"],font=self.fnt(11,"bold"))
                lc.create_text(64,cy+11,anchor="w",text=desc,fill=P["muted"],font=self.fnt(8))
                lc.create_text(lw-18,cy,anchor="e",text=tag,fill=P["muted_dim"],font=self.fnt(8))
            if not commands:
                lc.create_text(lw/2,list_h/2-10,text="Ничего не найдено",fill=P["text_soft"],font=self.fnt(12,"bold"))
                lc.create_text(lw/2,list_h/2+14,text="Попробуй другое название раздела или шаблона",fill=P["muted"],font=self.fnt(9))
            lc.configure(scrollregion=(0,0,lw,max(list_h,len(commands)*row_h)))
            lc.yview_moveto(0)
            shell.itemconfigure(caption,text=("РАЗДЕЛЫ И ШАБЛОНЫ" if query.get().strip() else "РАЗДЕЛЫ")+(f"  ·  {len(commands)}" if commands else ""))
        def rebuild(*args):
            commands.clear()
            terms=query.get().casefold().split()
            for i,(attr,title,glyph,desc) in enumerate(self._pages):
                if all(t in (title+" "+desc).casefold() for t in terms):
                    commands.append((title,desc,glyph,page_colors[i],"Раздел",lambda a=attr:self.notebook.select(getattr(self,a))))
            commands.extend(self._theme_commands(terms))
            if terms:
                for item in self.template_rows_cache.values():
                    if all(t in str(item["name"]).casefold() for t in terms):
                        commands.append((str(item["name"])[:52],"Открыть шаблон в редакторе","templates",P["green"],"Шаблон",
                                         lambda i=int(item["id"]):self._jump_template(i)))
                    if len(commands)>=20:break
            state["index"]=0
            draw_rows()
        def select(n,animated=True):
            if not commands:return
            n=max(0,min(len(commands)-1,n))
            old=state["index"];state["index"]=n
            y0,y1=lc.coords(state["pill"])[1],n*row_h+2
            if animated and old!=n:
                animate(lc,"pill",200,lambda t:lc.coords(state["pill"],4,y0+(y1-y0)*t),ease_out_quint)
            else:lc.coords(state["pill"],4,y1)
            total=max(list_h,len(commands)*row_h)
            top=lc.canvasy(0)
            if n*row_h<top or (n+1)*row_h>top+list_h:
                goal=n*row_h if n*row_h<top else (n+1)*row_h-list_h
                animate(lc,"scroll",220,lambda t:lc.yview_moveto((top+(goal-top)*t)/total),ease_out_cubic)
        def close(event=None):
            if state["closing"]:return "break"
            state["closing"]=True
            if self._command_window is win:self._command_window=None
            def fade(t):
                try:win.attributes("-alpha",1-t)
                except tk.TclError:pass
            animate(win,"out",130,fade,ease_in_cubic,done=win.destroy)
            try:self.root.focus_set()
            except tk.TclError:pass
            return "break"
        def execute(event=None):
            if commands and 0<=state["index"]<len(commands):
                action=commands[state["index"]][5];close();action()
            return "break"
        def move(delta):
            if commands:select((state["index"]+delta)%len(commands))
            return "break"
        def row_at(event):
            return int(lc.canvasy(event.y)//row_h)
        def hover(event):
            n=row_at(event)
            if 0<=n<len(commands) and n!=state["index"]:select(n)
        def click(event):
            n=row_at(event)
            if 0<=n<len(commands):state["index"]=n;execute()
        def focus_out(event):
            def check():
                if state["closing"] or not win.winfo_exists():return
                try:focused=self.root.focus_get()
                except (KeyError,tk.TclError):focused=None
                if focused is None or not str(focused).startswith(str(win)):close()
            win.after(80,check)
        query.trace_add("write",rebuild);rebuild()
        self.command_entry.bind("<Down>",lambda e:move(1));self.command_entry.bind("<Up>",lambda e:move(-1))
        win.bind("<Escape>",close);win.bind("<Return>",execute);win.bind("<FocusOut>",focus_out)
        lc.bind("<Motion>",hover);lc.bind("<ButtonRelease-1>",click)
        lc.bind("<MouseWheel>",lambda e:(smooth_scroll(lc,-e.delta/120,row_h),"break")[1])
        win.protocol("WM_DELETE_WINDOW",close)
        win.geometry(f"{w}x{h}+{max(0,x)}+{max(0,y-12)}")
        try:win.attributes("-alpha",0.0)
        except tk.TclError:pass
        try:win.transient(self.root);win.attributes("-topmost",True)
        except tk.TclError:pass
        win.deiconify();win.lift()
        def appear(t):
            win.geometry(f"+{max(0,x)}+{max(0,round(y-12*(1-t)))}")
            try:win.attributes("-alpha",t)
            except tk.TclError:pass
        animate(win,"in",220,appear,ease_out_cubic)
        win.focus_force()
        self.command_entry.focus_set()
        return "break"

    def _jump_template(self,template_id):
        self.template_search_var.set("");self.template_filter_var.set("Все")
        self._refresh_templates();self.notebook.select(self.templates_tab)
        iid=str(template_id)
        if self.templates_tree.exists(iid):
            self.templates_tree.selection_set(iid);self.templates_tree.focus(iid);self.templates_tree.see(iid)
            self._template_selected()

    # --------------------------------------------------------------- changes
    def _build_changes(self):
        self.changes_tab.columnconfigure(0,weight=3,uniform="release")
        self.changes_tab.columnconfigure(1,weight=2,uniform="release")
        self.changes_tab.rowconfigure(0,weight=1)
        release=self._card(self.changes_tab,'Версия 2.9.9.6  /  Что нового')
        release.grid(row=0,column=0,sticky='nsew',padx=(0,12))
        holder=ttk.Frame(release,style='Card.TFrame');holder.pack(fill='both',expand=True)
        text=tk.Text(holder,wrap='word',width=1,height=8,bg=P['surface'],fg=P['text_soft'],bd=0,
                     highlightthickness=0,padx=0,pady=4,font=(self.ui_font,10),cursor='arrow',
                     selectbackground=P['selection'],selectforeground=P['text'])
        scroll=ttk.Scrollbar(holder,command=text.yview);scroll.pack(side='right',fill='y')
        text.configure(yscrollcommand=self._autohide(scroll,pack={'side':'right','fill':'y','before':text}));text.pack(fill='both',expand=True)
        text.tag_configure('heading',foreground=P['text'],font=self.fnt(11,'bold'),spacing1=16,spacing3=7)
        text.tag_configure('new',foreground=P['accent'],font=self.fnt(11,'bold'),spacing1=4,spacing3=7)
        text.tag_configure('body',foreground=P['muted'],spacing3=8,lmargin1=0,lmargin2=0)
        text.insert('end','НОВОЕ  Пять тем оформления\n','new')
        text.insert('end','Кнопка с палитрой в шапке или Ctrl+Shift+T открывает «Оформление»: «Аврора», «Небесный экспресс» (аниме-ночь с поездом), «Уютный вечер» (зима), «Цветение» (весна), «Золотой час» (лето) и «Плед и какао» (осень). Тема применяется сразу, без перезапуска, и запоминается. Атмосфера — звёзды, снег, лепестки, золотая пыль или листья — только в свободных местах: выключена, деликатная или выразительная. Если в Windows отключена анимация, частицы остаются неподвижным декором.\n','body')
        text.insert('end','НОВОЕ  Защита шаблонов\n','new')
        text.insert('end','Замок слева у каждого шаблона защищает его от удаления — кнопкой, клавишей Delete, «Удалить все» и очисткой. Перед удалением видно точно, сколько удалится и сколько защищённых останется. Режим «Защита» позволяет защитить или снять защиту сразу с группы. Защищённые шаблоны можно редактировать; защита сохраняется при экспорте и импорте.\n','body')
        text.insert('end','НОВОЕ  Меню и окна в стиле программы\n','new')
        text.insert('end','Свои выпадающие списки с плавным раскрытием и галочкой, поля ввода со свечением в фокусе, вкладки с перетекающей подсветкой. Все кнопки везде одного вида: иконка по смыслу, подсветка при наведении, контурные второстепенные кнопки. Меню «Действия», «Выделить» и правый клик по шаблону — с иконками, плавной подсветкой и анимацией. Подтверждения, ошибки и ввод пароля открываются в собственных окнах с затемнением фона вместо белых окон Windows. Меню в трее — тёмное.\n','body')
        text.insert('end','НОВОЕ  Голос, фразы и оскорбления\n','new')
        text.insert('end','Живой голос William вместо «робота» (рабочая edge-tts встроена в программу). Приветствия и реакции на тикеты с оскорблениями берутся из файла voice_phrases.json: несколько фраз, случайная или одна фиксированная. Тикеты с матом распознаются автоматически, включая обходы вроде «х*й» и транслита. Всё настраивается и проверяется кнопками «Тест» в Настройки → Голос и Windows.\n','body')
        text.insert('end','НОВОЕ  Оформление «Aurora»\n','new')
        text.insert('end','Новая глубокая палитра, градиентные кнопки с мягким свечением и плавной подсветкой, скользящий индикатор в меню, плавные переходы между разделами, анимированные счётчики, светящиеся прогресс-бары и переключатели вместо галочек. Быстрый поиск Ctrl+K, уведомления, поля ввода, вкладки и списки получили новый вид. Логика, настройки и данные не изменились.\n','body')
        for title,body in [
            ('НОВОЕ  Перебаны SIMPLE','Новый раздел «Перебаны» использует уже подключённый Telegram. Получает /p repunish, отправляет команды с разрешёнными причинами и закрывает все ID пачки. Есть ручной запуск, расписание, очередь, предпросмотр команды и журнал.'),
            ('Исправлены ID снятий','Порядок ID в подсказке /p reset больше не вызывает ложную ошибку: сравнивается полный состав ID. Чужие, пропущенные и повторяющиеся записи отклоняются. Мгновенная остановка после запуска освобождает кнопки.'),
            ('Автоматические снятия','Отдельный раздел «Снятия»: запуск, остановка, прогресс и журнал. Ассистент получает /p reset у AresStaffRobot, отправляет команды в AresMineRobot с интервалом 5 секунд и подтверждает ID. Цикл продолжается до пустого списка.'),
            ('Надёжное продолжение','Отправленные команды сохраняются для твоего аккаунта. После остановки их не отправляем повторно. Неполный список ждём целиком. Снятия и /c list используют бота по очереди.'),
            ('01  Работающая кнопка сброса','«Сбросить и возобновить» отменяет текущий запрос и заново подключает команды. Не нужно ждать зависшую задачу. После сбоя кнопка снова доступна, а ошибка видна в статусе.'),
            ('02  Задержка без вечной блокировки','Через настроенные 5–60 секунд приходит уведомление о задержке. Ожидание результата продолжается до 60 секунд. Поздний ответ возвращается к исходному запросу и снимает блокировку.'),
            ('03  Ответ не теряется при пропущенном событии','Дополнительно проверяется диалог бота: только сообщения после отправленной команды и правки её служебных сообщений. Старые ответы не копируются из истории.'),
            ('04  Все части длинного списка','Части /c list передаются по порядку. Задержанное продолжение подхватывается автоматически, уже отправленные части не дублируются. Служебный статус пропускается.'),
            ('05  Согласованный статус','Состояние закрытого обработчика не перезаписывает новую сессию. Сохранение настроек не возвращает старую блокировку. Удаление шаблонов по ПКМ и защита от повторных ответов сохранены.')]:
            text.insert('end',title+'\n','heading');text.insert('end',body+'\n','body')
        text.configure(state='disabled')
        side=self._card(self.changes_tab,'Когда нужен ручной ответ')
        side.grid(row=0,column=1,sticky='nsew')
        ttk.Label(side,text='02',style='Number.TLabel',foreground=P['accent'],font=self.fnt(44,'bold')).pack(anchor='w',pady=(8,0))
        ttk.Label(side,text='автоответа на один тикет',style='Section.TLabel').pack(anchor='w',pady=(0,18))
        for body in [
            'Если вопрос остался нерешённым, ассистент останавливается и добавляет обращение в очередь обучения.',
            'Для одного TG/VK-клиента тот же шаблон используется до двух раз за последние 24 часа, даже в разных тикетах.',
            'Черновики не расходуют лимит. Счётчик сохраняется после перезапуска. Твой ручной ответ останавливает очередь автоответов.']:
            ttk.Label(side,text=body,style='Card.TLabel',wraplength=300).pack(fill='x',pady=(0,16))
        AnimatedButton(side,text='Настроить шаблоны',style='Accent.TButton',command=self._open_reply_settings).pack(anchor='w',pady=(4,0))

    def _open_reply_settings(self):
        self.notebook.select(self.templates_tab)
        self.template_editor_notebook.select(self.template_reply_tab)

    def show_whats_new(self):
        import sys
        from config import save_config
        self.notebook.select(self.changes_tab)
        self.config['last_seen_changelog_version']=sys.modules[self.__class__.__module__].APP_VERSION
        save_config(self.config)
