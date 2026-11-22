"""Actual Tk buttons -> TelegramService in its thread -> fake Telegram bots.

Uses isolated APPDATA. No login, network messages, or real punishments.
"""
import asyncio
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
temporary=tempfile.TemporaryDirectory(prefix='ares-repunish-ui-')
os.environ['APPDATA']=temporary.name
os.environ['ARES_ASSISTANT_HOME']=str(ROOT)
sys.path[:0]=[str(ROOT/'app'),str(ROOT/'tests')]
import main
from config import load_config
from PIL import ImageGrab
from telegram_service import TelegramService
from ares_repunish import AresRepunish
from test_repunish import RepunishClient, SAMPLE, EMPTY, batch
from repunish_ui import SCHEDULE_LABELS

for name in ('_startup_services','_maybe_show_whats_new','refresh_microphones','_apply_autostart','_apply_voice_setting'):
    setattr(main.AresAssistantApp,name,lambda self:None)

checks=[];errors=[];worker_errors=[];signals={}
root=main.Tk();root.report_callback_exception=lambda *e:errors.append(e)
app=main.AresAssistantApp(root);thread=None

def check(condition,label):
    if not condition:
        root.update_idletasks()
        ImageGrab.grab(bbox=(0,0,1440,900)).save(Path(sys.argv[1]).with_name('failed_repunish_ui.png'))
    assert condition,label
    checks.append(label)

def pump(seconds=.08):
    end=time.monotonic()+seconds
    while time.monotonic()<end:root.update();time.sleep(.005)

def until(condition,timeout=5):
    end=time.monotonic()+timeout
    while not condition():
        assert not worker_errors,worker_errors
        assert time.monotonic()<end,'Worker or UI did not settle'
        pump(.02)

def quick_worker(*args,**kwargs):
    kwargs.update(response_timeout=.4,send_timeout=.25,idle_seconds=.015,poll_seconds=.025,batch_pause=.01)
    return AresRepunish(*args,**kwargs)

try:
    root.geometry('1440x900+0+0');app.notebook.select(app.repunish_tab);pump(.25)
    check(app.repunish_start_button.winfo_ismapped(),'new-section start visible')
    check(not app.repunish_start_button.instate(['disabled']) and app.repunish_stop_button.instate(['disabled']),'idle controls')
    check(app.repunish_schedule_var.get()==SCHEDULE_LABELS['manual'],'manual by default')
    check(app.repunish_time_entry.instate(['disabled']),'daily time disabled outside daily mode')
    app.repunish_start_button.invoke()
    check('Сначала подключи Telegram' in app.repunish_status_var.get(),'offline start gives actionable status')
    check(not app.repunish_start_button.instate(['disabled']),'offline start remains available')
    app.repunish_schedule_var.set(SCHEDULE_LABELS['daily']);app.repunish_time_var.set('21:35')
    app.repunish_delay_var.set('1,5');app.repunish_notify_var.set(False)
    check(not app.repunish_time_entry.instate(['disabled']),'daily time enabled')
    check(app.save_repunish_settings(),'schedule saves')
    saved=load_config()
    check(saved['repunish_schedule']=='daily' and saved['repunish_daily_time']=='21:35' and saved['repunish_delay_seconds']==1.5,'schedule persisted')
    check(not saved['repunish_notify_start'],'notification preference persisted')
    app.repunish_time_var.set('25:00')
    check(not app.save_repunish_settings(),'invalid daily time rejected')
    check(load_config()['repunish_daily_time']=='21:35','invalid value does not overwrite saved settings')
    app.repunish_time_var.set('21:35');app.repunish_schedule_var.set(SCHEDULE_LABELS['manual'])
    app.save_repunish_settings()
    live=TelegramService(dict(app.config),SimpleNamespace(),Path(temporary.name)/'telegram',app.emit)
    app.telegram=live
    def run():
        async def work():
            live.loop=asyncio.get_running_loop();live.client=RepunishClient();live.connected=True
            signals['stop']=asyncio.Event()
            try:
                await live._apply_repunish_settings();signals['ready']=True
                await signals['stop'].wait()
            finally:
                live.connected=False
                if live._repunish:await live._repunish.close()
                if live._repunish_schedule:await live._repunish_schedule.close()
                await live.client.cleanup()
        try:
            with patch('telegram_service.AresRepunish',new=quick_worker):asyncio.run(work())
        except BaseException as exc:worker_errors.append(exc)
    thread=threading.Thread(target=run,daemon=True);thread.start()
    until(lambda:signals.get('ready',False));pump(.15)
    # Complete workflow from the real start button and delay from saved settings.
    live.loop.call_soon_threadsafe(live.client.replies.extend,[SAMPLE,EMPTY])
    app.repunish_start_button.invoke()
    check(app.repunish_start_button.instate(['disabled']) and not app.repunish_stop_button.instate(['disabled']),'button enters running state')
    until(lambda:live._repunish is not None and not live._repunish.busy and 'Список на перебан пуст!' in app.repunish_status_var.get())
    check(live._repunish.client is live.client,'same Telegram client')
    check(live.client.mine_texts==['r grief ban Player_____ 1.11 - читы','r grief ban TeSt_Nick 1.10 - причина'],'exact allowed commands only')
    check(live.client.mine_times[1]-live.client.mine_times[0]>=1.5,'actual delay from UI is observed')
    check('/p repunish 2080001 2080002 2080003' in live.client.staff_texts,'all batch IDs including filtered are closed')
    check(app.repunish_sent_var.get()=='2' and app.repunish_skipped_var.get()=='1','real counters reach UI')
    check(not app.repunish_start_button.instate(['disabled']) and app.repunish_stop_button.instate(['disabled']),'completion restores controls')
    check('Player_____' in app.repunish_log.get('1.0','end'),'journal has actual commands')
    check(app.repunish_tree.item('2080002','values')[-1]=='Пропущено','filter status in queue')
    app.repunish_tree.selection_set('2080001');pump()
    check('Player_____' in app.repunish_command_text.get('1.0','end'),'selected command preview preserves nickname')
    # Stop while the next command is waiting; restart must skip the first ID.
    live.loop.call_soon_threadsafe(live.client.replies.extend,[batch((2080010,'Resume_A','1.11'),(2080011,'Resume_B','2.7')),EMPTY])
    app.repunish_start_button.invoke()
    until(lambda:len(live.client.mine_texts)==3 and live._repunish.uncertain is None)
    app.repunish_stop_button.invoke()
    until(lambda:not live._repunish.busy and not app.repunish_start_button.instate(['disabled']))
    check(len(live.client.mine_texts)==3,'stop prevents second command')
    app.repunish_start_button.invoke()
    until(lambda:not live._repunish.busy and 'Список на перебан пуст!' in app.repunish_status_var.get())
    check(len(live.client.mine_texts)==4,'restart resumes without duplicate command')
    # Network ambiguity leads to an explicit, working verification button.
    live.loop.call_soon_threadsafe(live.client.replies.extend,[batch((2080012,'Review_Nick','1.11')),EMPTY])
    live.loop.call_soon_threadsafe(setattr,live.client,'mine_error',ConnectionError('qa'))
    app.repunish_start_button.invoke()
    until(lambda:not live._repunish.busy and not app._repunish_busy and app._repunish_uncertain is not None)
    pump(.05)
    check(app.repunish_review_button.winfo_ismapped(),'verification control visible after ambiguous send')
    with patch('repunish_ui.messagebox.askyesnocancel',return_value=False):app.repunish_review_button.invoke()
    until(lambda:app._repunish_uncertain is None)
    live.loop.call_soon_threadsafe(setattr,live.client,'mine_error',None)
    app.repunish_start_button.invoke()
    until(lambda:not live._repunish.busy and 'Список на перебан пуст!' in app.repunish_status_var.get())
    check(len(live.client.mine_texts)==5 and 'Review_Nick' in live.client.mine_texts[-1],'verification permits the resolved retry')
    # Schedule-only events may never unlock the active worker controls.
    app._handle_event('repunish_status',{'service_id':id(live),'state':'sending','busy':True,'message':'QA active'})
    app._handle_event('repunish_status',{'service_id':id(live),'next_run':time.time()+3600,'schedule':'5h'})
    check(app.repunish_start_button.instate(['disabled']),'timer-only status does not unlock active run')
    app._handle_event('repunish_status',{'service_id':id(live)+1,'state':'error','busy':False,'message':'Stale'})
    check(app.repunish_status_var.get()=='QA active','stale service status ignored')
    app._handle_event('repunish_status',{'service_id':id(live),'state':'ready','busy':False,'message':'Готово'})
    # Large queues render at most 100 rows and preserve off-page status updates.
    rows=[{'id':str(3000000+i),'player':f'Player_{i:04d}','reason':'1.11','command':f'r grief ban Player_{i:04d} 1.11','status':'В очереди'} for i in range(501)]
    app._update_repunish({'state':'ready','busy':False,'rows':rows})
    check(len(app.repunish_tree.get_children())==100,'bounded table for large queues')
    app._update_repunish({'state':'ready','row_update':{'id':'3000100','status':'Отправлено'}})
    app.repunish_next_button.invoke();pump()
    check(app.repunish_tree.item('3000100','values')[-1]=='Отправлено','off-page status retained')
    for _ in range(4):app.repunish_next_button.invoke()
    check(len(app.repunish_tree.get_children())==1 and app.repunish_next_button.instate(['disabled']),'last page fits and disables next')
    # Save schedule in a live service and verify the account-scoped timer.
    app.repunish_schedule_var.set(SCHEDULE_LABELS['5h']);app.save_repunish_settings()
    until(lambda:live._repunish_schedule.options['mode']=='5h')
    check(live._repunish_schedule.next_at>time.time(),'live schedule has a future check')
    app._repunish_clock();check('Через' in app.repunish_next_var.get(),'countdown shown')
    root.geometry('1120x700+0+0');pump(.2)
    check(app.repunish_start_button.winfo_rooty()+app.repunish_start_button.winfo_height()<700,'start control fits minimum window')
    app._repunish_settings_canvas.yview_moveto(1);pump(.1)
    check(app.repunish_time_entry.winfo_rooty()+app.repunish_time_entry.winfo_height()<700,'schedule fields fit minimum window')
    check(app.repunish_save_button.winfo_ismapped() and app.repunish_save_button.winfo_rooty()+app.repunish_save_button.winfo_height()<700,'save schedule fits minimum window')
    check(app.repunish_tree.winfo_height()>=50,'queue remains usable at minimum size')
    app.repunish_tree.see('3000500');pump(.08)
    bbox=app.repunish_tree.bbox('3000500')
    check(bbox and app.repunish_tree.winfo_rooty()+bbox[1]+bbox[3]<app.repunish_tree.master.winfo_rooty()+app.repunish_tree.master.winfo_height(),'queue row is visible within its viewport at minimum size')
    check(app.nav_items[str(app.changes_tab)].winfo_rooty()+app.nav_items[str(app.changes_tab)].winfo_height()<700,'eleven nav items fit')
    root.geometry('1080x660+0+0');pump(.2)
    app.repunish_tree.see('3000500');pump(.05)
    bbox=app.repunish_tree.bbox('3000500')
    check(bbox and app.repunish_tree.winfo_rooty()+bbox[1]+bbox[3]<app.repunish_tree.master.winfo_rooty()+app.repunish_tree.master.winfo_height(),'one queue row visible at the original minimum window size')
    # Preview the real window with safe demonstration data.
    root.geometry('1440x900+0+0')
    app._repunish_settings_canvas.yview_moveto(0)
    if app._toast_job:root.after_cancel(app._toast_job);app._dismiss_toast()
    app._handle_event('telegram_status','Telegram подключён')
    demo=[{'id':str(3100101+i),'player':nick,'reason':reason,'command':f'r grief ban {nick} {reason}','status':status} for i,(nick,reason,status) in enumerate((('MorningStar','1.11','Отправлено'),('Cozy_Player','1.10','Отправлено'),('PaperCloud','2.2','Пропущено'),('SilverFox___','2.7','В очереди')))]
    app._update_repunish({'state':'ready','busy':False,'message':'Готово к следующему запуску','rows':demo,'sent_count':2,'skipped_count':1,'batch_number':1,'batch_total':4,'batch_sent':3})
    app.repunish_log.configure(state='normal');app.repunish_log.delete('1.0','end');app.repunish_log.configure(state='disabled')
    app._append_repunish_log({'time':'12:00:00','message':'Пример очереди. Подключи Telegram и запусти перебаны.','level':'info'})
    app.repunish_tree.selection_set('3100101');app._repunish_selected();pump(.25)
    output=Path(sys.argv[1]);output.parent.mkdir(parents=True,exist_ok=True)
    ImageGrab.grab(bbox=(0,0,1440,900)).save(output)
    root.geometry('1120x700+0+0');pump(.15)
    ImageGrab.grab(bbox=(0,0,1120,700)).save(output.with_name(output.stem+'_minimum.png'))
    for page in app.notebook.tabs():app.notebook.select(page);pump(.02)
    check(not errors,'all pages without Tk callback errors')
    print(f'{len(checks)} repunish UI checks passed; preview: {output.name}')
finally:
    if thread and live.loop and live.loop.is_running():live.loop.call_soon_threadsafe(signals['stop'].set);thread.join(3)
    app.telegram=None;app._cancel_repunish_clock();app.knowledge.close()
    for job in root.tk.call('after','info'):root.after_cancel(job)
    root.destroy();temporary.cleanup()
