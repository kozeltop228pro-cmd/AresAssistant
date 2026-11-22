"""SIMPLE-перебаны в реальном async-обработчике, только подменные боты."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from ares_repunish import AresRepunish, RepunishError, parse_repunish, join_parts, ALLOWED_REASONS, options_from_config, next_run, RepunishSchedule
from telegram_service import TelegramService
from test_removals import RemovalClient, BOT, MINE_ID, STAFF
from test_command_relay import Event, USER

EMPTY='⚡ Список на перебан пуст!'
ACK='Убрано из списка на перебан.'
SAMPLE='''Список на перебан (3):
➤ 2080001. r grief ban Player_____ 1.11 - читы
➤ 2080002. r grief ban OtherPlayer 2.2 - оскорбление
➤ 2080003. r grief ban TeSt_Nick 1.10 - причина
➤ /p repunish 2080003 2080001 2080002'''

def batch(*rows):
    return f'Список на перебан ({len(rows)}):\n'+'\n'.join(f'➤ {rid}. r grief ban {name} {reason} - test' for rid,name,reason in rows)+'\n➤ /p repunish '+' '.join(str(rid) for rid,_,_ in rows)


class RepunishClient(RemovalClient):
    def __init__(self,replies=()):
        super().__init__(replies)
        self.outgoing=[];self.ack_reply=ACK;self.error_after_send=False
        self.mine_failure_once=False

    async def send_message(self,entity,text,**options):
        if entity.id==MINE_ID and self.mine_error:raise self.mine_error
        if entity.id==BOT and text.startswith('/p repunish ') and self.ack_error:raise self.ack_error
        self.next_id+=1;mid=self.next_id
        self.sent.append((entity,text,options,mid))
        if entity.id==MINE_ID:
            self.mine_times.append(asyncio.get_running_loop().time())
            self.outgoing.append(SimpleNamespace(id=mid,out=True,raw_text=text,date=datetime.now(timezone.utc)))
            if self.mine_wait:await self.mine_wait.wait()
            if self.error_after_send:
                self.error_after_send=False;raise ConnectionError('response lost')
        elif text=='/p repunish' and self.replies:
            reply=self.replies.pop(0)
            if self.early_reply:await self.deliver(reply,mid)
            else:self.tasks.append(asyncio.create_task(self.deliver(reply,mid)))
        elif text.startswith('/p repunish ') and self.ack_reply:
            self.tasks.append(asyncio.create_task(self.deliver(self.ack_reply,mid)))
        return SimpleNamespace(id=mid)

    async def get_messages(self,entity,**kwargs):
        if entity.id==MINE_ID:return self.outgoing[-kwargs.get('limit',100):]
        return await super().get_messages(entity,**kwargs)


class ParserTests(unittest.TestCase):
    def test_ready_commands_and_nick_are_preserved(self):
        parsed=parse_repunish(SAMPLE)
        self.assertEqual(parsed.rows[0].command,'r grief ban Player_____ 1.11 - читы')
        self.assertEqual(parsed.rows[0].player,'Player_____')
        self.assertEqual([r.allowed for r in parsed.rows],[True,False,True])

    def test_all_allowed_and_other_reasons(self):
        for reason in ALLOWED_REASONS:self.assertTrue(parse_repunish(batch((1,'Nick',reason))).rows[0].allowed)
        for reason in ('1.111','2.2','3.1'):self.assertFalse(parse_repunish(batch((1,'Nick',reason))).rows[0].allowed)

    def test_raw_ban_and_mute_follow_original_simple_format(self):
        text='Ваши неоформленные наказания (2):\n➤ 10. 12.10.26 15:20 - BAN - One___ - 1.11\n➤ 11. MUTE - Two - 1.6'
        self.assertEqual([r.command for r in parse_repunish(text).rows],['r grief ban One___ 1.11','r grief ban Two 1.6'])

    def test_partial_reply_and_split_command(self):
        at=SAMPLE.index('Player_____')+5
        self.assertIsNone(parse_repunish(SAMPLE[:at]))
        self.assertEqual(parse_repunish(join_parts([SAMPLE[:at],SAMPLE[at:]])),parse_repunish(SAMPLE))
        at=SAMPLE.index('➤ 2080002')
        self.assertEqual(parse_repunish(join_parts([SAMPLE[:at].rstrip(),SAMPLE[at:]])),parse_repunish(SAMPLE))

    def test_wrong_commands_or_foreign_lists_are_not_executed(self):
        for text in (SAMPLE.replace('r grief ban','r grief op'),SAMPLE.replace('Player_____','Player;op'),
                     'Список на снятие (1):\n➤ 1. r grief pex user One group set default','/p repunish 1 2'):
            self.assertIsNone(parse_repunish(text))

    def test_duplicate_id_or_changed_footer_rejected(self):
        for text in (SAMPLE.replace('2080002.','2080001.'),SAMPLE.replace('/p repunish 2080003 2080001 2080002','/p repunish 2080003 2080001 9999')):
            with self.assertRaises(RepunishError):parse_repunish(text)

    def test_empty_variants(self):
        for text in (EMPTY,'⚡️ Список на перебан пуст!','Неоформленные наказания (0):','Ваши неоформленные наказания (0):'):
            self.assertTrue(parse_repunish(text).empty)

    def test_large_cleanup_is_split(self):
        ids=[str(2000000+i) for i in range(2000)]
        commands=list(AresRepunish._ack_commands(ids))
        self.assertGreater(len(commands),1)
        self.assertTrue(all(len(c)<=3900 for c in commands))
        self.assertEqual([rid for c in commands for rid in c.split()[2:]],ids)


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory=tempfile.TemporaryDirectory();self.path=Path(self.directory.name)
        self.client=RepunishClient();self.statuses=[]
        self.worker=self.new_worker()
        await self.worker.install()
    def new_worker(self):
        return AresRepunish(self.client,self.path,status=self.statuses.append,interval_seconds=.02,
            response_timeout=.15,send_timeout=.08,idle_seconds=.005,poll_seconds=.008,burst_rest=.04,batch_pause=0)
    async def asyncTearDown(self):
        await self.worker.close();await self.client.cleanup();self.directory.cleanup()
    async def run_worker(self,*replies):
        self.client.replies.extend(replies);self.assertTrue(self.worker.start())
        await asyncio.wait_for(self.worker._task,2)
    async def until(self,condition):
        async def wait():
            while not condition():await asyncio.sleep(.001)
        await asyncio.wait_for(wait(),1)

    async def test_multiple_batches_filter_ack_all_ids_and_interval(self):
        await self.run_worker(SAMPLE,batch((2080004,'Last','2.7')),EMPTY)
        self.assertEqual(self.client.mine_texts,['r grief ban Player_____ 1.11 - читы','r grief ban TeSt_Nick 1.10 - причина','r grief ban Last 2.7 - test'])
        self.assertEqual(self.client.staff_texts,['/p repunish','/p repunish 2080001 2080002 2080003','/p repunish','/p repunish 2080004','/p repunish'])
        self.assertTrue(all(b-a>=.02 for a,b in zip(self.client.mine_times,self.client.mine_times[1:])))
        self.assertEqual(self.statuses[-1]['state'],'done');self.assertEqual(self.statuses[-1]['skipped_count'],1)

    async def test_fully_filtered_batch_is_closed_and_next_batch_processed(self):
        await self.run_worker(batch((5,'Ignored','2.2')),batch((6,'Allowed','1.11')),EMPTY)
        self.assertIn('/p repunish 5',self.client.staff_texts)
        self.assertEqual(self.client.mine_texts,['r grief ban Allowed 1.11 - test'])

    async def test_initial_empty_never_sends_mine(self):
        await self.run_worker(EMPTY)
        self.assertEqual(self.client.mine_texts,[])
        self.assertEqual(self.client.staff_texts,['/p repunish'])
        self.assertFalse(self.worker.busy)

    async def test_only_one_start_is_accepted(self):
        self.client.replies=[EMPTY]
        self.assertTrue(self.worker.start());self.assertFalse(self.worker.start())
        await self.worker._task
        self.assertEqual(self.client.staff_texts,['/p repunish'])

    async def test_stale_closed_batches_stop_without_duplicate_commands(self):
        await self.run_worker(SAMPLE,SAMPLE,SAMPLE,SAMPLE)
        self.assertEqual(len(self.client.mine_texts),2)
        self.assertEqual(self.statuses[-1]['state'],'error')
        self.assertFalse(self.statuses[-1]['retryable'])

    async def test_immediate_stop_then_restart(self):
        self.worker.start();await self.worker.stop()
        self.assertFalse(self.worker.busy)
        self.assertEqual(self.client.sent,[])
        await self.run_worker(EMPTY)
        self.assertEqual(self.statuses[-1]['state'],'done')

    async def test_progress_edit_and_multipart_are_collected(self):
        async def response(client,query_id):
            mid=await client.emit_text('⚡ Запрос выполняется!')
            await client.emit_text(SAMPLE[:SAMPLE.index('➤ 2080002')],mid=mid,edited=True)
            await client.emit_text(SAMPLE[SAMPLE.index('➤ 2080002'):])
        await self.run_worker(response,EMPTY)
        self.assertEqual(len(self.client.mine_texts),2);self.assertEqual(self.statuses[-1]['state'],'done')

    async def test_poll_history_recovers_lost_events(self):
        self.client.history_only=True
        await self.run_worker(SAMPLE,EMPTY)
        self.assertEqual(len(self.client.mine_texts),2);self.assertEqual(self.statuses[-1]['state'],'done')

    async def test_early_response_before_send_returns(self):
        self.client.early_reply=True
        await self.run_worker(SAMPLE,EMPTY)
        self.assertEqual(len(self.client.mine_texts),2)

    async def test_partial_list_or_bot_error_sends_nothing(self):
        await self.run_worker(SAMPLE[:SAMPLE.index('➤ 2080002')])
        self.assertEqual(self.client.mine_texts,[]);self.assertEqual(self.statuses[-1]['state'],'error')
        self.assertTrue(self.statuses[-1]['retryable'])
        await self.run_worker('Ошибка: нет доступа')
        self.assertEqual(self.client.mine_texts,[])
        self.assertFalse(self.statuses[-1]['retryable'])

    async def test_acknowledgement_required_before_next_query(self):
        self.client.ack_reply=None
        await self.run_worker(SAMPLE,EMPTY)
        self.assertEqual(self.client.staff_texts,['/p repunish','/p repunish 2080001 2080002 2080003'])
        self.assertEqual(self.statuses[-1]['state'],'error')
        self.assertTrue(self.statuses[-1]['retryable'])

    async def test_stop_and_restart_does_not_repeat_sent_commands(self):
        self.client.replies=[SAMPLE]
        self.worker.start();await self.until(lambda:len(self.client.mine_texts)==1)
        await self.worker.stop();self.assertEqual(self.statuses[-1]['state'],'stopped')
        await self.worker.close();self.worker=self.new_worker();await self.worker.install()
        await self.run_worker(EMPTY)
        self.assertEqual(len(self.client.mine_texts),2)
        self.assertEqual(self.client.mine_texts.count('r grief ban Player_____ 1.11 - читы'),1)

    async def test_completed_checkpoint_suppresses_stale_repeated_id(self):
        await self.run_worker(SAMPLE,EMPTY)
        await self.worker.close();self.worker=self.new_worker();await self.worker.install()
        await self.run_worker(SAMPLE,EMPTY)
        self.assertEqual(len(self.client.mine_texts),2)

    async def test_lost_send_receipt_recovers_exact_outgoing_history(self):
        self.client.error_after_send=True
        await self.run_worker(SAMPLE,EMPTY)
        self.assertEqual(len(self.client.mine_texts),2);self.assertIsNone(self.worker.uncertain)

    async def test_unknown_delivery_stays_in_review_until_owner_resolves(self):
        self.client.mine_error=ConnectionError('unknown')
        await self.run_worker(SAMPLE)
        self.assertEqual(self.statuses[-1]['state'],'review')
        self.assertIsNotNone(self.worker.uncertain)
        self.assertNotIn('/p repunish 2080001 2080002 2080003',self.client.staff_texts)
        self.worker.resolve_uncertain(False);self.client.mine_error=None
        await self.run_worker(EMPTY)
        self.assertEqual(len(self.client.mine_texts),2)

    async def test_restart_recovers_saved_uncertain_send_without_duplicate(self):
        self.client.mine_wait=asyncio.Event();self.client.replies=[SAMPLE]
        self.worker.start();await self.until(lambda:len(self.client.mine_texts)==1)
        await self.worker.stop();await self.worker.close()
        self.client.mine_wait=None;self.worker=self.new_worker();await self.worker.install()
        await self.run_worker(EMPTY)
        self.assertEqual(len(self.client.mine_texts),2)

    async def test_old_matching_outgoing_message_does_not_confirm_new_attempt(self):
        self.client.mine_error=ConnectionError('unknown')
        self.client.outgoing=[SimpleNamespace(out=True,raw_text='r grief ban Player_____ 1.11 - читы',date=datetime.now(timezone.utc)-timedelta(days=1))]
        await self.run_worker(SAMPLE)
        self.assertEqual(self.statuses[-1]['state'],'review')

    async def test_changed_command_for_sent_id_is_not_acknowledged(self):
        await self.run_worker(SAMPLE,EMPTY)
        await self.run_worker(SAMPLE.replace('Player_____','Changed'))
        self.assertEqual(self.statuses[-1]['state'],'error');self.assertEqual(len(self.client.mine_texts),2)

    async def test_manual_game_reply_is_not_required_or_classified(self):
        await self.run_worker(SAMPLE,EMPTY)
        self.assertTrue(all(not t.startswith('baninfo') for t in self.client.mine_texts))

    async def test_account_progress_is_separate(self):
        await self.run_worker(SAMPLE,EMPTY);await self.worker.close()
        self.client.account_id+=1;self.worker=self.new_worker();await self.worker.install()
        await self.run_worker(SAMPLE,EMPTY)
        self.assertEqual(len(self.client.mine_texts),4)


class ScheduleTests(unittest.IsolatedAsyncioTestCase):
    def test_options_and_local_daily_time(self):
        self.assertEqual(options_from_config({})['mode'],'manual')
        for values in ({'repunish_delay_seconds':.5},{'repunish_daily_time':'25:00'},{'repunish_schedule':'bad'}):
            with self.assertRaises(ValueError):options_from_config(values)
        at=datetime(2026,10,12,11,30).timestamp()
        self.assertEqual(next_run('5h','12:00',at),at+18000)
        self.assertEqual(next_run('10h','12:00',at),at+36000)
        self.assertEqual(next_run('manual','12:00',at),0)
        self.assertEqual(datetime.fromtimestamp(next_run('daily','12:00',at)),datetime(2026,10,12,12))
        self.assertEqual(datetime.fromtimestamp(next_run('daily','12:00',datetime(2026,10,12,12).timestamp())),datetime(2026,10,13,12))

    async def test_timer_starts_after_cycle_completion_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as d:
            now=[100000];values=options_from_config({'repunish_schedule':'5h'})
            async def start():return True
            schedule=RepunishSchedule(Path(d)/'schedule.json',values,start,lambda:True,lambda i:None,clock=lambda:now[0])
            self.assertEqual(schedule.next_at,118000)
            schedule.started();self.assertEqual(schedule.next_at,0)
            now[0]+=100;schedule.completed();self.assertEqual(schedule.next_at,118100)
            saved=RepunishSchedule(schedule.path,values,start,lambda:True,lambda i:None,clock=lambda:now[0])
            self.assertEqual(saved.next_at,118100)
            saved.update(options_from_config({}));self.assertEqual(saved.next_at,0)

    async def test_overdue_schedule_is_not_sent_immediately_on_connect(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'s.json';p.write_text(json.dumps({'schedule':['5h','12:00'],'next_at':1}))
            schedule=RepunishSchedule(p,options_from_config({'repunish_schedule':'5h'}),lambda:None,lambda:True,lambda i:None,clock=lambda:100)
            self.assertEqual(schedule.next_at,18100)

    async def test_transient_error_retries_only_in_automatic_mode(self):
        with tempfile.TemporaryDirectory() as d:
            schedule=RepunishSchedule(Path(d)/'s.json',options_from_config({}),lambda:None,lambda:True,lambda i:None,clock=lambda:100)
            schedule.failed(True);self.assertEqual(schedule.retry_at,0)
            schedule.update(options_from_config({'repunish_schedule':'5h'}));schedule.failed(True)
            self.assertEqual(schedule.retry_at,220)
            schedule.failed(False);self.assertEqual(schedule.retry_at,0)

    async def test_immediate_schedule_shutdown(self):
        with tempfile.TemporaryDirectory() as d:
            schedule=RepunishSchedule(Path(d)/'s.json',options_from_config({}),lambda:None,lambda:True,lambda i:None)
            schedule.start_loop();await schedule.close()
            self.assertIsNone(schedule._task)

    async def test_saving_same_schedule_rearms_it_after_stop(self):
        with tempfile.TemporaryDirectory() as d:
            options=options_from_config({'repunish_schedule':'5h'})
            schedule=RepunishSchedule(Path(d)/'s.json',options,lambda:None,lambda:True,lambda i:None,clock=lambda:100)
            schedule.failed(False);self.assertEqual(schedule.next_at,0)
            schedule.update(options);self.assertEqual(schedule.next_at,18100)

    async def test_due_timer_waits_for_free_bot_and_survives_connection_error(self):
        with tempfile.TemporaryDirectory() as d:
            now=[100];available=[False];attempts=[];statuses=[]
            async def start():
                attempts.append(now[0])
                if len(attempts)==1:raise ConnectionError('qa')
                schedule.started();available[0]=False
                return True
            schedule=RepunishSchedule(Path(d)/'s.json',options_from_config({'repunish_schedule':'5h'}),start,lambda:available[0],statuses.append,clock=lambda:now[0])
            now[0]=schedule.next_at;schedule.start_loop()
            try:
                await asyncio.sleep(.005);self.assertEqual(attempts,[])
                available[0]=True
                async def wait_for_count(count):
                    while len(attempts)<count:await asyncio.sleep(.01)
                await asyncio.wait_for(wait_for_count(1),2)
                self.assertEqual(schedule.retry_at,now[0]+120)
                self.assertFalse(schedule._task.done())
                now[0]=schedule.retry_at
                await asyncio.wait_for(wait_for_count(2),2)
                self.assertEqual(schedule.next_at,0)
                self.assertEqual(schedule.retry_at,0)
            finally:await schedule.close()


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory=tempfile.TemporaryDirectory();self.client=RepunishClient([EMPTY]);self.messages=[]
        self.service=TelegramService({},SimpleNamespace(),Path(self.directory.name)/'telegram',lambda k,p:self.messages.append((k,p)))
        self.service.loop=asyncio.get_running_loop();self.service.client=self.client;self.service.connected=True
    async def asyncTearDown(self):
        self.service.connected=False
        if self.service._repunish:await self.service._repunish.close()
        if self.service._repunish_schedule:await self.service._repunish_schedule.close()
        await self.client.cleanup();self.directory.cleanup()

    async def test_same_client_and_global_pause(self):
        self.assertTrue(await self.service._start_repunish())
        worker=self.service._repunish;worker.idle=.005;worker.poll=.008
        self.assertIs(worker.client,self.client)
        self.service._paused.set();await self.service._sync_relay_pause()
        self.assertFalse(worker.busy);self.assertEqual(self.messages[-1][1]['state'],'stopped')

    async def test_staff_lock_blocks_other_flows_without_stuck_controls(self):
        await self.service._start_repunish()
        await self.service._start_removals()
        self.assertEqual(self.messages[-1][0],'removals_status')
        self.assertFalse(self.messages[-1][1]['busy'])
        self.assertIsNone(self.service._removals)
        self.assertIn('перебаны',self.messages[-1][1]['message'])
        self.assertIn('перебаны',self.service._staff_busy_reason())

    async def test_repunish_waits_for_active_removals_or_private_request(self):
        self.service._removals_starting=True
        self.assertFalse(await self.service._start_repunish())
        self.service._removals_starting=False
        self.service._command_relay=SimpleNamespace(busy=True,needs_resync=False,publish_status=lambda:None)
        self.assertFalse(await self.service._start_repunish())
        self.assertEqual(self.client.sent,[])

    async def test_concurrent_settings_create_one_scheduler(self):
        self.service.config.update(repunish_schedule='5h')
        await asyncio.gather(self.service._apply_repunish_settings(),self.service._apply_repunish_settings())
        self.assertIsNotNone(self.service._repunish_schedule._task)
        self.assertEqual(self.client.sent,[])

    async def test_stop_during_bot_lookup_and_next_start(self):
        lookup=asyncio.Event()
        original=self.client.get_entity
        async def blocked(name):lookup.set();await asyncio.Event().wait()
        self.client.get_entity=blocked
        task=asyncio.create_task(self.service._start_repunish())
        await lookup.wait()
        self.assertTrue(self.service.stop_repunish())
        self.assertFalse(await task)
        self.assertIsNone(self.service._repunish)
        self.assertFalse(self.service._repunish_starting)
        self.assertEqual(self.client.handlers,[])
        self.client.get_entity=original
        self.assertTrue(await self.service._start_repunish())

    async def test_late_repunish_messages_never_become_private_command_results(self):
        self.service.config.update(command_relay_enabled=True,command_relay_user_id=str(USER))
        await self.service._apply_command_relay()
        relay=self.service._command_relay
        try:
            await relay._on_private(Event('/c list 14 Nick'))
            async def waiting():
                while not (relay._pending and relay._pending.sent_id):await asyncio.sleep(.001)
            await asyncio.wait_for(waiting(),1)
            texts=(SAMPLE,EMPTY,ACK,'➤ 2080001. r grief ban Player 1.11 - читы',
                '➤ 2080001. 12.10.26 15:20 - BAN - Player - 1.11','/p repunish 2080001')
            for offset,text in enumerate(texts,1):
                await relay._on_bot(Event(text,sender=BOT,mid=relay._pending.sent_id+offset))
            self.assertEqual(relay._pending.messages,{})
        finally:await relay.close()
