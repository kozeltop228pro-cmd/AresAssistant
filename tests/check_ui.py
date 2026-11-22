"""Run in a disposable APPDATA directory, with a display and no Telegram login."""
import json
import asyncio
import threading
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
temporary = tempfile.TemporaryDirectory(prefix="ares-ui-qa-")
os.environ["APPDATA"] = temporary.name
os.environ["ARES_ASSISTANT_HOME"] = str(ROOT)
sys.path.insert(0, str(ROOT / "app"))
import main
from config import CONFIG_PATH, load_config
from PIL import ImageGrab
from telegram_service import TelegramService
sys.path.insert(0, str(ROOT / 'tests'))
from test_command_relay import Client, Event, USER, BOT
from test_removals import RemovalClient, SAMPLE, EMPTY, batch

main.AresAssistantApp._startup_services = lambda self: None
main.AresAssistantApp._maybe_show_whats_new = lambda self: None
main.AresAssistantApp.refresh_microphones = lambda self: None
main.AresAssistantApp._apply_autostart = lambda self: None
main.AresAssistantApp._apply_voice_setting = lambda self: None

checks = []
def check(condition, label):
    assert condition, label
    checks.append(label)

def pump(seconds=.12):
    stop = time.monotonic() + seconds
    while time.monotonic() < stop:
        root.update()
        time.sleep(.01)

root = main.Tk()
callback_errors = []
root.report_callback_exception = lambda *error: callback_errors.append(error)
app = main.AresAssistantApp(root)
cleanup_worker = None
try:
    root.geometry("1440x900+0+0")
    pump(.3)
    check(main.APP_VERSION == "2.9.9.6" and "2.9.9.6" in root.title(), "version")
    check(len(app.notebook.tabs()) == 11, "all previous sections and repunish available")
    check([app.settings_sections.tab(p, "text") for p in app.settings_sections.tabs()] ==
          ["Telegram", "Команды", "Ответы", "Голос и Windows", "Данные"], "settings tab order")
    check(app.command_relay_enabled_var.get(), "relay enabled by default")
    check(app.command_relay_user_var.get() == "123456789", "approved receiver preset")
    check(app.command_relay_timeout_var.get() == "15", "15 second timeout")
    check(not app.command_relay_forward_var.get(), "copy mode default")
    app.notebook.select(app.settings_tab)
    app.settings_sections.select(1)
    pump()
    check(app.command_relay_resume_button.winfo_ismapped(), "resume visible at 1440x900")
    app.command_relay_user_var.set("0")
    check(not app.save_settings(show_message=False), "zero ID rejected")
    app.command_relay_user_var.set("@somebody")
    check(not app.save_settings(show_message=False), "username rejected as ID")
    app.command_relay_user_var.set("123456789")
    app.command_relay_timeout_var.set("2")
    check(not app.save_settings(show_message=False), "timeout range validated")
    app.command_relay_timeout_var.set("15")
    app.command_relay_forward_var.set(True)
    check(app.save_settings(show_message=False), "valid relay settings save")
    saved = load_config()
    check(saved["command_relay_forward"] and saved["command_relay_user_id"] == "123456789", "relay settings persist")
    app._handle_event("command_relay_status", {"message": "Таймаут · нужна проверка", "needs_check": True})
    check(app.command_relay_status_var.get() == "Таймаут · нужна проверка", "worker status reaches UI")
    check(load_config()["command_relay_needs_check"], "uncertain request state persists")
    service = SimpleNamespace(connected=True, config=dict(app.config), update_command_relay=Mock(), resume_command_relay=Mock())
    app.telegram = service
    app.command_relay_resume_button.invoke()
    check(service.resume_command_relay.call_count == 1, "real button invokes reset without hidden confirmation")
    check(app.command_relay_resume_button.instate(['disabled']), "button shows reset in progress")
    app._handle_event('command_relay_status', {'message':'Готово', 'needs_check':False,
                      'service_id':id(service), 'can_restart':True})
    check(not app.command_relay_resume_button.instate(['disabled']), 'ready status enables reset button')
    app._handle_event('command_relay_status', {'message':'Старый сбой', 'needs_check':True,
                      'service_id':id(service)+1, 'can_restart':False})
    check(app.command_relay_status_var.get() == 'Готово' and not load_config()['command_relay_needs_check'],
          'stale service status cannot restore a cleared block')
    service.config['command_relay_needs_check'] = True
    app.command_relay_enabled_var.set(False)
    check(app.save_settings(show_message=False), "disable setting saves")
    check(service.update_command_relay.call_count == 1 and not service.config["command_relay_enabled"], "saving reconfigures live service")
    check(service.config['command_relay_needs_check'], 'UI saves do not overwrite worker request state')
    app._handle_event("command_relay_status", {"message": "Выключено", "needs_check": True})
    check(load_config()["command_relay_needs_check"], "disabling does not discard uncertain state")
    app._handle_event("command_relay_status", {"message": "Готово", "needs_check": False})
    check(not load_config()["command_relay_needs_check"], "checked reset persists")
    app.telegram = None
    app.command_relay_enabled_var.set(True)
    app.command_relay_forward_var.set(False)
    app.save_settings(show_message=False)
    # Real Tk button → real service scheduling → a separate asyncio thread.
    # The Telegram network is replaced with Client; credentials are not used.
    ready = threading.Event()
    worker_errors = []
    live = TelegramService({**app.config, 'api_id':'1', 'api_hash':'qa-only',
                            'command_relay_needs_check':False},
                           SimpleNamespace(), Path(temporary.name)/'session', app.emit)
    app.telegram = live
    signals = {}
    def run_worker():
        async def run():
            live.loop = asyncio.get_running_loop()
            live.client, live.connected = RemovalClient(), True
            signals['stop'] = asyncio.Event()
            try:
                await live._apply_command_relay()
                relay = live._command_relay
                relay.timeout_seconds = relay.response_wait_seconds = .02
                relay.response_idle_seconds = relay.history_poll_seconds = .01
                ready.set()
                await signals['stop'].wait()
            finally:
                live.connected = False
                if live._removals:
                    await live._removals.close()
                if live._command_relay:
                    await live._command_relay.close()
                await live.client.cleanup()
        try:
            asyncio.run(run())
        except BaseException as error:
            worker_errors.append(error)
            ready.set()
    worker = threading.Thread(target=run_worker, daemon=True)
    worker.start()
    def stop_worker():
        if live.loop and live.loop.is_running() and 'stop' in signals:
            live.loop.call_soon_threadsafe(signals['stop'].set)
        worker.join(3)
    cleanup_worker = stop_worker
    def pump_until(condition,timeout=3):
        end = time.monotonic() + timeout
        while not condition():
            assert not worker_errors, worker_errors
            assert time.monotonic() < end, 'UI/worker state did not settle'
            pump(.02)
    pump_until(ready.is_set)
    old = live._command_relay
    request = Event('/c list 14 modernhoster')
    asyncio.run_coroutine_threadsafe(old._on_private(request), live.loop).result(timeout=1)
    pump_until(lambda: old.needs_resync and not old.busy)
    check(not app.command_relay_resume_button.instate(['disabled']), 'timed-out real worker leaves reset available')
    app.command_relay_resume_button.invoke()
    pump_until(lambda: live._command_relay is not None and live._command_relay is not old
               and not live._relay_restarting and not app.command_relay_resume_button.instate(['disabled']))
    check(not live._command_relay.needs_resync and not load_config()['command_relay_needs_check'],
          'button resets real worker and persistent block')
    check(len(live.client.handlers) == 3, 'reset creates one handler set in real worker')
    second = Event('/c list 14 modernhoster', mid=2)
    async def answer_after_reset():
        relay = live._command_relay
        relay.response_idle_seconds = .01
        await relay._on_private(second)
        while relay._pending is None or relay._pending.sent_id is None:
            await asyncio.sleep(0)
        await relay._on_bot(Event('Аккаунты (0):\nНики (0):', sender=BOT, mid=live.client.next_id+1))
        await relay._request_task
    completed = asyncio.run_coroutine_threadsafe(answer_after_reset(), live.loop)
    pump_until(completed.done)
    completed.result(timeout=1)
    pump(.12)
    check(second.responses[-1][0] == 'Аккаунты (0):\nНики (0):', 'command after GUI reset delivers its result')
    check(not app.command_relay_resume_button.instate(['disabled']), 'finished real request keeps reset usable')
    # Real new-section button, same authorized-client architecture; fake bots.
    app.notebook.select(app.removals_tab)
    app._handle_event('telegram_status','Telegram подключён')
    pump(.1)
    check(app.removals_start_button.winfo_ismapped(), 'removals launch visible')
    live.loop.call_soon_threadsafe(live.client.replies.extend,[SAMPLE,EMPTY])
    app.removals_start_button.invoke()
    check(app.removals_start_button.instate(['disabled']) and not app.removals_stop_button.instate(['disabled']), 'removals controls switch to running')
    pump_until(lambda: live._removals is not None and not live._removals.busy
               and 'Список на снятие пуст!' in app.removals_status_var.get(),10)
    check(len(live.client.mine_texts)==2, 'UI launch sends both commands')
    check(live.client.mine_times[1]-live.client.mine_times[0]>=5, 'real configured five seconds between sends')
    check('/p reset 2081755 2084076' in live.client.staff_texts, 'UI workflow confirms exact IDs')
    check(app.removals_sent_var.get()=='2' and app.removals_batches_var.get()=='1', 'removals counters reflect worker')
    check(not app.removals_start_button.instate(['disabled']) and app.removals_stop_button.instate(['disabled']), 'empty list re-enables launch')
    check('TOTOTOP' in app.removals_log.get('1.0','end') and 'подтверж' in app.removals_log.get('1.0','end').lower(), 'actual journal shows sends and confirmation')
    app._handle_event('removals_status',{'service_id':id(live)+1,'state':'error','busy':True,'message':'Stale'})
    check('Stale' not in app.removals_status_var.get() and not app.removals_start_button.instate(['disabled']), 'stale removals service cannot disable button')
    new_batch=batch((2085000,'NextPlayer'),(2085001,'NextPlayer2'))
    live.loop.call_soon_threadsafe(live.client.replies.extend,[new_batch])
    app.removals_start_button.invoke()
    pump_until(lambda:len(live.client.mine_texts)==3 and live._removals.uncertain is None,8)
    app.removals_stop_button.invoke()
    pump_until(lambda:not live._removals.busy and not app.removals_start_button.instate(['disabled']))
    check(len(live.client.mine_texts)==3 and 'остановлены' in app.removals_status_var.get(), 'real stop button interrupts next command')
    live.loop.call_soon_threadsafe(live.client.replies.extend,[new_batch,EMPTY])
    app.removals_start_button.invoke()
    pump_until(lambda:not live._removals.busy and 'Список на снятие пуст!' in app.removals_status_var.get(),10)
    check(len(live.client.mine_texts)==4, 'UI restart skips already sent command')
    check(live._removals.state_path.exists(), 'UI workflow persists account progress')
    stop_worker()
    cleanup_worker = None
    check(not worker_errors, 'separate worker finished without errors')
    app.telegram = None
    root.geometry("1120x700+0+0")
    app.notebook.select(app.settings_tab)
    pump(.2)
    check(app.command_relay_resume_button.winfo_ismapped(), "resume available at minimum window size")
    check(app.command_relay_resume_button.winfo_rooty() + app.command_relay_resume_button.winfo_height() < 700, "resume is not clipped")
    app.notebook.select(app.removals_tab)
    pump(.15)
    check(app.removals_start_button.winfo_ismapped() and app.removals_stop_button.winfo_ismapped(), 'removals controls at minimum window size')
    check(app.removals_start_button.winfo_rooty()+app.removals_start_button.winfo_height()<700, 'removals launch is not clipped')
    check(app.removals_log.winfo_height()>=60, 'removals journal has usable space at minimum size')
    check(app.nav_items[str(app.changes_tab)].winfo_rooty()+app.nav_items[str(app.changes_tab)].winfo_height()<700, 'eleven navigation sections fit')
    # Make a local preview of the actual application window, using test data.
    root.geometry("1440x900+0+0")
    pump(.2)
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent / "qa_299/preview_2995.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    ImageGrab.grab(bbox=(0, 0, 1440, 900)).save(output)
    # Exercise the real right-click binding against a disposable database.
    app.notebook.select(app.templates_tab)
    first = app.knowledge.create_template('QA контекст A', 't a {ticket_id} Сохрани этот проверочный шаблон. @@@', True, 'Проверочный вопрос первого шаблона', '', match_mode='exact')
    target = app.knowledge.create_template('QA контекст B', 't a {ticket_id} Удали этот проверочный шаблон. @@@', True, 'Проверочный вопрос второго шаблона', '', match_mode='exact')
    app.template_search_var.set('QA контекст')
    app.template_checked_ids.add(first)
    app._refresh_all()
    pump(.2)
    tree = app.templates_tree
    tree.selection_set(str(first))
    tree.focus(str(first))
    app._template_selected()
    tree.see(str(target))
    pump()
    x, y, width, height = tree.bbox(str(target), 'name')
    px, py = x + min(20, width // 2), y + height // 2
    def right_click():
        tree.event_generate('<Button-3>', x=px, y=py, rootx=tree.winfo_rootx()+px, rooty=tree.winfo_rooty()+py)
        pump()
    right_click()
    menu = app._template_popup_menu
    check(menu.entrycget(0, 'label') == 'Удалить' and menu.winfo_ismapped(), 'right-click opens delete menu')
    check(tree.selection() == (str(target),) and app.template_selected_id == target, 'right-click selects row under cursor')
    ImageGrab.grab(bbox=(0, 0, 1440, 900)).save(output.with_name(output.stem+'_menu.png'))
    menu.unpost()
    with patch.object(main.messagebox, 'askyesno', return_value=False) as confirmation:
        menu.invoke(0)
    check(app.knowledge.get_template(target) is not None, 'cancel keeps template')
    check('QA контекст B' in confirmation.call_args.args[1], 'confirmation names clicked template')
    # Changing focus after opening must not change the menu's captured target.
    tree.selection_set(str(first)); tree.focus(str(first)); app._template_selected()
    with patch.object(main.messagebox, 'askyesno', return_value=True):
        menu.invoke(0)
    check(app.knowledge.get_template(target) is None, 'confirmed deletion removes clicked template')
    check(app.knowledge.get_template(first) is not None and first in app.template_checked_ids, 'other marked template survives')
    pump(.6)  # the deleted row fades out before the list is rebuilt
    check(not tree.exists(str(target)), 'deleted row removed from list')
    # A protected template: its «Удалить» item is disabled and deletion is refused at the data layer.
    app.knowledge.set_templates_locked([first], True); app._refresh_templates()
    x, y, width, height = tree.bbox(str(first), 'name')
    tree.event_generate('<Button-3>', x=x + 20, y=y + height // 2, rootx=tree.winfo_rootx()+x+20, rooty=tree.winfo_rooty()+y+height//2)
    pump()
    check(menu.entrycget(0, 'disabled') is True, 'delete item disabled for protected template')
    menu.invoke(0); menu.unpost(); pump(.2)
    check(app.knowledge.delete_templates([first]) == 0 and app.knowledge.get_template(first) is not None, 'protected template survives delete')
    app.knowledge.set_templates_locked([first], False); app._refresh_templates()
    tree.event_generate('<Button-3>', x=px, y=5, rootx=tree.winfo_rootx()+px, rooty=tree.winfo_rooty()+5)
    pump()
    check(not menu.winfo_ismapped(), 'right-click on heading does not open row menu')
    app.template_search_var.set('')
    app.template_checked_ids.clear()
    app._refresh_all()
    for page in app.notebook.tabs():
        app.notebook.select(page)
        pump(.03)
    check(not callback_errors, "all sections switch without Tk callback errors")
    print(f"{len(checks)} UI checks passed; Tk {root.tk.call('info', 'patchlevel')}; preview: {output.name}")
finally:
    if cleanup_worker:
        cleanup_worker()
    app.telegram = None
    app.knowledge.close()
    root.destroy()
    temporary.cleanup()
