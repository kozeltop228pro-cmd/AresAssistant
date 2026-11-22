"""Раздел «Перебаны» в оформлении «Aurora»."""
from __future__ import annotations
from datetime import datetime
import math
import time
import tkinter as tk
from tkinter import ttk, messagebox
import webbrowser

from ui_assets import AnimatedButton
from ui_widgets import StatusPill, GlowProgress, CountLabel, smooth_scroll
from ares_repunish import ALLOWED_REASONS, options_from_config

SCHEDULE_LABELS={'manual':'Только вручную','5h':'Каждые 5 часов','10h':'Каждые 10 часов','daily':'Каждый день'}


class RepunishUI:
    def _build_repunish(self):
        page=self.repunish_tab;p=self.palette
        page.columnconfigure(0,weight=1);page.rowconfigure(2,weight=1)
        self.repunish_status_var=tk.StringVar(value='Готово к ручному запуску')
        self.repunish_sent_var=tk.StringVar(value='0')
        self.repunish_skipped_var=tk.StringVar(value='0')
        self.repunish_batches_var=tk.StringVar(value='0')
        self.repunish_next_var=tk.StringVar(value='Следующий запуск — вручную')
        self.repunish_progress_var=tk.StringVar(value='Очередь ещё не запрашивалась')
        self._repunish_next=0;self._repunish_retry=0;self._repunish_uncertain=None
        self._repunish_rows=[];self._repunish_by_id={};self._repunish_page=0;self._repunish_busy=False
        self._repunish_clock_job=None
        hero=ttk.Frame(page,style='Hero.TFrame',padding=(20,16));hero.grid(row=0,column=0,sticky='ew',pady=(0,12))
        top=ttk.Frame(hero,style='HeroInner.TFrame');top.pack(fill='x')
        ttk.Label(top,text='ARES REPUNISH  /  ОЧЕРЕДЬ ПЕРЕБАНОВ',style='HeroSub.TLabel',foreground=p['accent'],font=self.fnt(8,'bold')).pack(side='left')
        self.repunish_badge=StatusPill(top,p['surface_high'],self.fnt,'●  Ожидание',p['muted'],pulse=(p['accent'],))
        self.repunish_badge.pack(side='right')
        hero_title=ttk.Label(hero,text='Перебаны по твоим правилам',style='HeroTitle.TLabel',font=self.fnt(19,'bold'))
        hero_title.pack(anchor='w',pady=(8,4))
        route=ttk.Label(hero,text='AresStaffRobot → AresMineRobot  ·  через подключённый Telegram-аккаунт',style='HeroSub.TLabel',font=self.fnt(9))
        route.pack(anchor='w')
        row=ttk.Frame(hero,style='HeroInner.TFrame');row.pack(fill='x',pady=(12,0))
        self.repunish_start_button=AnimatedButton(row,text='Запустить перебаны',style='Accent.TButton',command=self.start_repunish)
        self.repunish_start_button.pack(side='left',padx=(0,8))
        self.repunish_stop_button=AnimatedButton(row,text='Остановить',style='Secondary.TButton',command=self.stop_repunish)
        self.repunish_stop_button.pack(side='left');self.repunish_stop_button.state(['disabled'])
        AnimatedButton(row,text='Открыть Staff ↗',style='Ghost.TButton',command=lambda:webbrowser.open('https://t.me/AresStaffRobot')).pack(side='right')
        metrics=ttk.Frame(page);metrics.grid(row=1,column=0,sticky='ew',pady=(0,12))
        metric_widgets=[]
        for i,(title,var,color) in enumerate((('Команд отправлено',self.repunish_sent_var,p['green']),('Пропущено по причине',self.repunish_skipped_var,p['amber']),('Списков получено',self.repunish_batches_var,p['accent']))):
            metrics.columnconfigure(i,weight=1,uniform='repunish')
            card=self._card(metrics,'');card.configure(padding=(18,11))
            card.grid(row=0,column=i,sticky='ew',padx=(0 if i==0 else 6,0 if i==2 else 6));self._hover_card(card)
            label=ttk.Label(card,text=title,style='Muted.Card.TLabel');label.pack(anchor='w')
            number=CountLabel(card,var,style='Number.TLabel',font=self.fnt(23,'bold'),foreground=color);number.pack(anchor='w')
            metric_widgets.append((card,label,number))
        compact=[None]
        def resize_header(event):
            small=event.height<620
            if compact[0]==small:return
            compact[0]=small
            if small:top.pack_forget();route.pack_forget();progress_description.pack_forget()
            else:
                top.pack(fill='x',before=hero_title);route.pack(anchor='w',before=row)
                progress_description.pack(anchor='w',pady=(0,10),before=tabs)
            hero.configure(padding=(16,12) if small else (20,16))
            hero_title.configure(font=self.fnt(16 if small else 19,'bold'))
            hero_title.pack_configure(pady=(0,2) if small else (8,4))
            row.pack_configure(pady=(8 if small else 12,0))
            for card,label,number in metric_widgets:
                card.configure(padding=(16,8) if small else (18,11))
                label.pack_forget();number.pack_forget()
                label.pack(side='left' if small else 'top',anchor='w')
                number.configure(font=self.fnt(19 if small else 23,'bold'))
                number.pack(side='right' if small else 'top',anchor='w')
        pane=tk.PanedWindow(page,orient='horizontal',bd=0,bg=p['background'],sashwidth=14,opaqueresize=True)
        pane.grid(row=2,column=0,sticky='nsew')
        left=self._card(pane,'Очередь и ход выполнения');right=self._card(pane,'Управление перебанами')
        pane.add(left,minsize=340,stretch='always');pane.add(right,minsize=300,stretch='always')
        self._responsive_pane(pane,.59)
        self.repunish_status_label=ttk.Label(left,textvariable=self.repunish_status_var,style='Card.TLabel',wraplength=550)
        self.repunish_status_label.pack(fill='x',pady=(0,8))
        self.repunish_progress=GlowProgress(left,p['surface'],mode='determinate',maximum=100)
        self.repunish_progress.pack(fill='x',pady=(0,6))
        progress_description=ttk.Label(left,textvariable=self.repunish_progress_var,style='Muted.Card.TLabel')
        progress_description.pack(anchor='w',pady=(0,10))
        tabs=ttk.Notebook(left);tabs.pack(fill='both',expand=True)
        queue=ttk.Frame(tabs,style='Card.TFrame',padding=(0,8));journal=ttk.Frame(tabs,style='Card.TFrame',padding=(0,8))
        tabs.add(queue,text='Текущая пачка');tabs.add(journal,text='Журнал')
        pager=ttk.Frame(queue,style='Card.TFrame');pager.pack(side='bottom',fill='x',pady=(8,0))
        self.repunish_page_var=tk.StringVar(value='Нет записей')
        ttk.Label(pager,textvariable=self.repunish_page_var,style='Muted.Card.TLabel',font=self.fnt(8)).pack(side='left')
        self.repunish_next_button=AnimatedButton(pager,text='Дальше',image=self._padded_icon('chevright',17,p['text_soft'],gap=6,side='left'),compound='right',style='Ghost.TButton',command=lambda:self._repunish_turn_page(1));self.repunish_next_button.pack(side='right')
        self.repunish_prev_button=AnimatedButton(pager,text='Назад',image=self._padded_icon('chevleft',17,p['text_soft'],gap=6),compound='left',style='Ghost.TButton',command=lambda:self._repunish_turn_page(-1));self.repunish_prev_button.pack(side='right')
        holder=ttk.Frame(queue,style='Card.TFrame');holder.pack(fill='both',expand=True)
        tree=self.repunish_tree=ttk.Treeview(holder,columns=('id','player','reason','status'),show='headings',selectmode='browse',height=4)
        for col,title,width in (('id','ID',85),('player','Игрок',175),('reason','Причина',70),('status','Состояние',115)):
            tree.heading(col,text=title);tree.column(col,width=width,minwidth=65,stretch=col=='player')
        self._scroll_tree(holder,tree)
        for tag,color in (('sent',p['green']),('skip',p['muted_dim']),('active',p['accent'])):tree.tag_configure(tag,foreground=color)
        tree.bind('<<TreeviewSelect>>',lambda e:self._repunish_selected())
        self.repunish_log=self._text_widget(journal,height=5,readonly=True)
        self.repunish_log.configure(font=(self.ui_font,9),spacing3=4)
        sy=ttk.Scrollbar(journal,command=self.repunish_log.yview);sy.pack(side='right',fill='y')
        self.repunish_log.configure(yscrollcommand=self._autohide(sy,pack={'side':'right','fill':'y','before':self.repunish_log}));self.repunish_log.pack(fill='both',expand=True)
        for tag,color in (('info',p['text_soft']),('success',p['green']),('error',p['red'])):self.repunish_log.tag_configure(tag,foreground=color)
        self._append_repunish_log({'message':'Запусти очередь вручную или сохрани расписание. Перебаны, снятия и /c list работают с ботом по очереди.'})
        actions=ttk.Frame(right,style='Card.TFrame');actions.pack(side='bottom',fill='x',pady=(10,0))
        ttk.Label(actions,textvariable=self.repunish_next_var,style='Muted.Card.TLabel',wraplength=325).pack(fill='x',pady=(0,8))
        self.repunish_save_button=AnimatedButton(actions,text='Сохранить расписание',style='Accent.TButton',command=self.save_repunish_settings)
        self.repunish_save_button.pack(anchor='w')
        self.repunish_review_button=AnimatedButton(actions,text='Проверить отправку',style='Warning.TButton',command=self.review_repunish_send)
        settings_page,preview=self._tab_pages(right,('Расписание','Команда'))
        settings=self._repunish_scroll_form(settings_page)
        ttk.Label(settings,text='Разрешённые причины',style='Section.TLabel',wraplength=325).pack(fill='x')
        ttk.Label(settings,text='1.1   1.6   1.8   1.9   1.10   1.11   2.7',style='Card.TLabel',foreground=p['accent'],font=self.fnt(10,'bold'),wraplength=325).pack(fill='x',pady=(6,6))
        ttk.Label(settings,text='Остальные причины пропускаются. После обработки закрываются все ID пачки, включая пропущенные.',style='Muted.Card.TLabel',wraplength=325).pack(fill='x',pady=(0,12))
        self.repunish_schedule_var=tk.StringVar(value=SCHEDULE_LABELS.get(self.config.get('repunish_schedule','manual'),SCHEDULE_LABELS['manual']))
        ttk.Combobox(settings,textvariable=self.repunish_schedule_var,values=tuple(SCHEDULE_LABELS.values()),state='readonly').pack(fill='x')
        self.repunish_time_var=tk.StringVar(value=self.config.get('repunish_daily_time','12:00'))
        when=ttk.Frame(settings,style='Card.TFrame');when.pack(fill='x',pady=(8,8))
        ttk.Label(when,text='Время на ПК',style='Muted.Card.TLabel').pack(side='left')
        self.repunish_time_entry=ttk.Entry(when,textvariable=self.repunish_time_var,width=8)
        self.repunish_time_entry.pack(side='right')
        delay=ttk.Frame(settings,style='Card.TFrame');delay.pack(fill='x',pady=(0,8))
        ttk.Label(delay,text='Пауза, сек.',style='Muted.Card.TLabel').pack(side='left')
        self.repunish_delay_var=tk.StringVar(value=f"{float(self.config.get('repunish_delay_seconds',2)):g}".replace('.',','))
        ttk.Combobox(delay,textvariable=self.repunish_delay_var,values=('1,5','2','3','4','5'),state='readonly',width=6).pack(side='right')
        self.repunish_notify_var=tk.BooleanVar(value=bool(self.config.get('repunish_notify_start',True)))
        self._checkbutton(settings,'Уведомлять о начале',self.repunish_notify_var).pack(anchor='w',pady=(0,8))
        self.repunish_schedule_var.trace_add('write',lambda *a:self._repunish_schedule_controls())
        self._repunish_schedule_controls()
        ttk.Label(preview,text='Точная строка для выбранной записи',style='Muted.Card.TLabel',wraplength=325).pack(anchor='w',pady=(0,8))
        self.repunish_command_text=self._text_widget(preview,height=5,readonly=True);self.repunish_command_text.pack(fill='both',expand=True)
        self._set_text(self.repunish_command_text,'Выбери запись в очереди слева. Готовая команда бота отправляется без изменения ника и подчёркиваний.')
        ttk.Label(preview,text='Статус «Отправлено» означает доставку команды в Telegram. Ответ MineRobot и baninfo не проверяются.',style='Muted.Card.TLabel',wraplength=325).pack(fill='x',pady=(10,0))
        page.bind('<Destroy>',lambda e:self._cancel_repunish_clock() if e.widget is page else None,add='+')
        self._repunish_clock()
        self._show_repunish_page()
        page.bind('<Configure>',resize_header,add='+')

    def _repunish_scroll_form(self,page):
        # Только форма прокручивается; запуск, сохранение и проверка всегда видны.
        canvas=self._repunish_settings_canvas=tk.Canvas(page,bg=self.palette['surface'],bd=0,highlightthickness=0,width=1,height=1)
        scroll=ttk.Scrollbar(page,orient='vertical',command=canvas.yview)
        canvas.pack(side='left',fill='both',expand=True)
        canvas.configure(yscrollcommand=self._autohide(scroll,pack={'side':'right','fill':'y','before':canvas}),yscrollincrement=0)
        body=ttk.Frame(canvas,style='Card.TFrame');window=canvas.create_window(0,0,window=body,anchor='nw')
        body.bind('<Configure>',lambda e:canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>',lambda e:canvas.itemconfigure(window,width=max(100,e.width-8)))
        def wheel(event):
            if not canvas.winfo_exists() or not canvas.winfo_ismapped():return
            target=self.root.winfo_containing(event.x_root,event.y_root)
            while target is not None and target is not page:target=getattr(target,'master',None)
            if target is page and body.winfo_height()>canvas.winfo_height():
                delta=(-1 if event.num==4 else 1) if event.num in (4,5) else -event.delta/120
                smooth_scroll(canvas,delta,60)
        for event in ('<MouseWheel>','<Button-4>','<Button-5>'):self.root.bind(event,wheel,add='+')
        return body

    def _repunish_schedule_controls(self):
        self.repunish_time_entry.configure(state='normal' if self.repunish_schedule_var.get()==SCHEDULE_LABELS['daily'] else 'disabled')

    def save_repunish_settings(self, quiet=False):
        mode=next((key for key,label in SCHEDULE_LABELS.items() if label==self.repunish_schedule_var.get()),'manual')
        values={'repunish_schedule':mode,'repunish_daily_time':self.repunish_time_var.get().strip(),
            'repunish_notify_start':self.repunish_notify_var.get()}
        try:
            values['repunish_delay_seconds']=float(self.repunish_delay_var.get().replace(',','.'))
            options_from_config(values)
            from config import save_config
            updated={**self.config,**values};save_config(updated);self.config.update(values)
            if self.telegram and self.telegram.connected:self.telegram.update_repunish_settings(values)
            if not quiet:self._toast('Настройки перебанов сохранены')
            return True
        except (ValueError,OSError) as exc:
            self._toast(str(exc),'error');return False

    def start_repunish(self):
        if not self.save_repunish_settings(quiet=True):return
        if not self.telegram or not self.telegram.connected:
            self._update_repunish({'state':'offline','busy':False,'message':'Сначала подключи Telegram кнопкой вверху.'});return
        self._update_repunish({'state':'connecting','busy':True,'message':'Запускаю перебаны…'})
        if self.telegram.start_repunish() is False:
            self._update_repunish({'state':'error','busy':False,'message':'Не удалось запустить перебаны. Подключи Telegram заново.'})

    def stop_repunish(self):
        if self.telegram:
            self.repunish_status_var.set('Останавливаю перебаны…')
            if self.telegram.stop_repunish() is False:self._update_repunish({'state':'offline','busy':False,'message':'Telegram отключён. Очередь сохранена.'})

    def review_repunish_send(self):
        row=self._repunish_uncertain
        if not row or not self.telegram or not self.telegram.connected:return
        sent=messagebox.askyesnocancel('Проверка отправки перебана',f"Проверь свой диалог с @AresMineRobot. Есть ли эта исходящая команда?\n\nID {row['id']}\n{row['command']}\n\nДа — повтор не нужен. Нет — разрешить отправку при следующем запуске.",parent=self.root)
        if sent is not None:self.telegram.resolve_repunish_send(sent)

    def _append_repunish_log(self, entry):
        widget=self.repunish_log;widget.configure(state='normal')
        widget.insert('end',(str(entry.get('time',''))+'  '+str(entry.get('message',''))).strip()+'\n',entry.get('level','info'))
        if int(widget.index('end-1c').split('.')[0])>1200:widget.delete('1.0','201.0')
        widget.configure(state='disabled');widget.see('end')

    def _update_repunish(self,info):
        if 'next_run' in info:self._repunish_next=float(info['next_run'] or 0)
        if 'retry_at' in info:self._repunish_retry=float(info['retry_at'] or 0)
        if info.get('schedule_error'):self._toast(info['schedule_error'],'error')
        if 'state' not in info:return
        state=info['state'];busy=bool(info.get('busy',False));self._repunish_busy=busy
        self.repunish_status_var.set(info.get('message',''))
        self.repunish_start_button.state(['disabled'] if busy else ['!disabled'])
        self.repunish_stop_button.state(['!disabled'] if busy else ['disabled'])
        badge,color=('●  Выполняется',self.palette['accent']) if busy else {
            'done':('●  Завершено',self.palette['green']),'error':('●  Ошибка',self.palette['red']),
            'review':('●  Нужна проверка',self.palette['amber']),'stopped':('●  Остановлено',self.palette['amber']),
            'offline':('●  Нет подключения',self.palette['muted'])}.get(state,('●  Ожидание',self.palette['muted']))
        self.repunish_badge.configure(text=badge,foreground=color)
        for key,var in (('sent_count',self.repunish_sent_var),('skipped_count',self.repunish_skipped_var),('batch_number',self.repunish_batches_var)):
            if key in info:var.set(str(info[key]))
        if 'batch_total' in info:
            total,done=int(info['batch_total']),int(info.get('batch_sent',0))
            self.repunish_progress.configure(value=100*done/total if total else (100 if state=='done' else 0))
            self.repunish_progress_var.set(f'Текущая пачка: {done} / {total}' if total else ('Очередь пуста' if state=='done' else 'Ожидание списка'))
        if 'rows' in info:
            self._repunish_rows=list(info['rows']);self._repunish_by_id={r['id']:r for r in self._repunish_rows};self._repunish_page=0
            self._show_repunish_page()
        if 'row_update' in info:
            update=info['row_update'];row=self._repunish_by_id.get(update['id'])
            if row is not None:
                row['status']=update['status']
                if self.repunish_tree.exists(row['id']):self.repunish_tree.item(row['id'],values=self._repunish_values(row),tags=(self._repunish_tag(row),))
        if 'uncertain' in info:self._repunish_uncertain=info['uncertain']
        if self._repunish_uncertain and not busy:self.repunish_review_button.pack(anchor='w',pady=(8,0))
        else:self.repunish_review_button.pack_forget()
        if info.get('log'):self._append_repunish_log(info['log'])
        self.nav_items[str(self.repunish_tab)].badge='…' if busy else ''
        self.nav_items[str(self.repunish_tab)].draw()

    @staticmethod
    def _repunish_values(row):return tuple(row[k] for k in ('id','player','reason','status'))
    @staticmethod
    def _repunish_tag(row):return 'sent' if row['status']=='Отправлено' else ('active' if row['status']=='Отправляю' else ('skip' if row['status'] in ('Пропущено','Закрыто ранее') else ''))
    def _show_repunish_page(self):
        tree=self.repunish_tree;tree.delete(*tree.get_children())
        count=len(self._repunish_rows);pages=max(1,math.ceil(count/100))
        self._repunish_page=min(self._repunish_page,pages-1)
        start=self._repunish_page*100
        for row in self._repunish_rows[start:start+100]:tree.insert('','end',iid=row['id'],values=self._repunish_values(row),tags=(self._repunish_tag(row),))
        self.repunish_page_var.set(f'{start+1}–{min(start+100,count)} из {count}' if count else 'Нет записей')
        self.repunish_prev_button.state(['!disabled'] if self._repunish_page else ['disabled'])
        self.repunish_next_button.state(['!disabled'] if self._repunish_page+1<pages else ['disabled'])
    def _repunish_turn_page(self,delta):
        self._repunish_page=max(0,self._repunish_page+delta);self._show_repunish_page()
    def _repunish_selected(self):
        ids=self.repunish_tree.selection()
        if ids:self._set_text(self.repunish_command_text,self._repunish_by_id[ids[0]]['command'])
    def _repunish_clock(self):
        due=self._repunish_retry or self._repunish_next
        if due:
            seconds=max(0,int(due-time.time()));hours,rem=divmod(seconds,3600);minutes,seconds=divmod(rem,60)
            at=datetime.fromtimestamp(due).strftime('%d.%m %H:%M')
            self.repunish_next_var.set(f"{'Повтор' if self._repunish_retry else 'Проверка'}: {at}\nЧерез {hours:02d}:{minutes:02d}:{seconds:02d}" if due>time.time() else 'Проверка ожидает свободного бота и подключения')
        else:self.repunish_next_var.set('Следующий запуск — вручную' if self.repunish_schedule_var.get()==SCHEDULE_LABELS['manual'] else ('Следующая проверка — после пустого списка' if self._repunish_busy else 'Сохрани расписание или запусти очередь'))
        self._repunish_clock_job=self.root.after(1000,self._repunish_clock)
    def _cancel_repunish_clock(self):
        if self._repunish_clock_job is not None:self.root.after_cancel(self._repunish_clock_job);self._repunish_clock_job=None
