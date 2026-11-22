"""Real async handlers, fake Telegram: never send messages to actual bots."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from telethon.errors import FloodWaitError
from ares_removals import AresRemovals, RemovalsError, parse_batch, join_parts, STAFF, MINE
from telegram_service import TelegramService
from test_command_relay import Client, Event, USER, BOT

MINE_ID = BOT + 1
SAMPLE = """Список на снятие (2):
➤ 2081755. r grief pex user TOTOTOP group set default
➤ 2084076. r grief pex user podvig_zek324354 group set default

➤ /p reset 2081755 2084076"""
EMPTY = "⚡ Список на снятие пуст!"


def batch(*rows):
    return f"Список на снятие ({len(rows)}):\n" + "\n".join(
        f"➤ {identifier}. r grief pex user {name} group set default" for identifier, name in rows
    ) + "\n\n➤ /p reset " + " ".join(str(identifier) for identifier, _ in rows)


class RemovalClient(Client):
    def __init__(self, replies=()):
        super().__init__()
        self.replies = list(replies)
        self.tasks = []
        self.mine_times = []
        self.mine_error = None
        self.ack_error = None
        self.mine_wait = None
        self.history = []
        self.history_only = False
        self.early_reply = False
        self.account_id = 777

    async def get_me(self):
        return SimpleNamespace(id=self.account_id, bot=False)

    async def get_entity(self, name):
        return SimpleNamespace(id=BOT if name == STAFF else MINE_ID, bot=True)

    async def emit_text(self, text, *, mid=None, edited=False, reply_to=None):
        if mid is None:
            self.next_id += 1
            mid = self.next_id
        event = Event(text, sender=BOT, mid=mid, reply_to=reply_to)
        event.message.sender_id, event.message.out = BOT, False
        self.history[:] = [m for m in self.history if m.id != mid]
        self.history.append(event.message)
        if not self.history_only:
            for callback, builder in list(self.handlers):
                is_edit = type(builder).__name__ == "MessageEdited"
                if is_edit == edited:
                    await callback(event)
        return mid

    async def deliver(self, reply, query_id):
        if not self.early_reply:
            await asyncio.sleep(.002)
        if callable(reply):
            await reply(self, query_id)
        elif isinstance(reply, str):
            await self.emit_text(reply)
        elif reply is not None:
            for text in reply:
                await self.emit_text(text)

    async def send_message(self, entity, text, **options):
        if entity.id == MINE_ID and self.mine_error:
            raise self.mine_error
        if entity.id == BOT and text != "/p reset" and self.ack_error:
            raise self.ack_error
        self.next_id += 1
        sent_id = self.next_id
        self.sent.append((entity, text, options, sent_id))
        if entity.id == MINE_ID:
            self.mine_times.append(asyncio.get_running_loop().time())
            if self.mine_wait:
                await self.mine_wait.wait()
        if entity.id == BOT and text == "/p reset" and self.replies:
            reply = self.replies.pop(0)
            if self.early_reply:
                await self.deliver(reply, sent_id)
            else:
                self.tasks.append(asyncio.create_task(self.deliver(reply, sent_id)))
        return SimpleNamespace(id=sent_id)

    async def get_messages(self, entity, *, ids=None, min_id=0, limit=100, reverse=False):
        if ids is not None:
            return [next((m for m in self.history if m.id == identifier), None) for identifier in ids]
        return sorted((m for m in self.history if m.id > min_id), key=lambda m:m.id, reverse=not reverse)[:limit]

    @property
    def staff_texts(self):
        return [text for entity, text, *_ in self.sent if entity.id == BOT]

    @property
    def mine_texts(self):
        return [text for entity, text, *_ in self.sent if entity.id == MINE_ID]

    async def cleanup(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)


class ParserTests(unittest.TestCase):
    def test_exact_user_example_and_only_game_commands(self):
        result = parse_batch(SAMPLE)
        self.assertEqual([r.id for r in result.rows], ["2081755", "2084076"])
        self.assertEqual(result.rows[0].command, "r grief pex user TOTOTOP group set default")
        self.assertEqual(result.rows[1].player, "podvig_zek324354")

    def test_empty_signal_with_and_without_lightning(self):
        for text in [EMPTY, "⚡️ Список на снятие пуст!", "Список на снятие пуст!", "  " + EMPTY + "\n"]:
            self.assertTrue(parse_batch(text).empty)

    def test_incomplete_list_waits_for_full_count(self):
        self.assertIsNone(parse_batch(SAMPLE.split("➤ 2084076")[0]))

    def test_injected_or_other_commands_never_parse(self):
        for text in [SAMPLE.replace("set default", "set admin"), SAMPLE.replace("TOTOTOP", "TOTOTOP;shutdown"), "/p reset 2081755", "Аккаунты (0):\nНики (0):"]:
            self.assertIsNone(parse_batch(text))

    def test_duplicate_ids_and_conflicting_footer_rejected(self):
        for text in [batch((1,"One"),(1,"Two")), SAMPLE.replace("/p reset 2081755 2084076", "/p reset 2084076 9999999"),
                     SAMPLE.replace('/p reset 2081755 2084076','/p reset 2081755 2081755')]:
            with self.assertRaises(RemovalsError):parse_batch(text)

    def test_same_footer_ids_in_another_order_are_valid(self):
        self.assertEqual(parse_batch(SAMPLE.replace('/p reset 2081755 2084076','/p reset 2084076 2081755')),parse_batch(SAMPLE))

    def test_reported_ten_id_list_with_swapped_footer(self):
        ids=(2083796,2084130,2084822,2085279,2085310,2086040,2086049,2086063,2086097,2086449)
        text=batch(*((rid,f'Player{i}') for i,rid in enumerate(ids)))
        text=text.replace('/p reset '+' '.join(map(str,ids)), '/p reset '+' '.join(map(str,ids[:6]+(ids[7],ids[6])+ids[8:])))
        self.assertEqual([row.id for row in parse_batch(text).rows],list(map(str,ids)))

    def test_split_in_middle_of_command_and_on_line_boundary(self):
        at = SAMPLE.index("TOTOTOP") + 3
        self.assertEqual(parse_batch(join_parts([SAMPLE[:at], SAMPLE[at:]])), parse_batch(SAMPLE))
        at = SAMPLE.index("➤ 2084076")
        self.assertEqual(parse_batch(join_parts([SAMPLE[:at].rstrip(), SAMPLE[at:]])), parse_batch(SAMPLE))

    def test_unexpected_multiple_headers_rejected(self):
        with self.assertRaises(RemovalsError):parse_batch(SAMPLE + "\n" + SAMPLE)

    def test_large_confirmation_is_split_without_losing_ids(self):
        ids = [str(2000000 + i) for i in range(2000)]
        commands = list(AresRemovals._ack_commands(ids))
        self.assertGreater(len(commands), 1)
        self.assertTrue(all(len(c) <= 3900 for c in commands))
        self.assertEqual([i for c in commands for i in c.split()[2:]], ids)


class RemovalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="ares-removals-tests-")
        self.path = Path(self.directory.name)
        self.client = RemovalClient()
        self.statuses = []
        self.engine = AresRemovals(self.client, self.path, status=self.statuses.append,
            interval_seconds=.025, response_timeout=.15, send_timeout=.08,
            idle_seconds=.008, poll_seconds=.01, confirmation_retries=1)
        await self.engine.install()

    async def asyncTearDown(self):
        await self.engine.close()
        await self.client.cleanup()
        self.directory.cleanup()

    async def until(self, condition):
        async def wait():
            while not condition():await asyncio.sleep(.001)
        await asyncio.wait_for(wait(), 1)

    async def run_engine(self, *replies):
        self.client.replies.extend(replies)
        self.assertTrue(self.engine.start())
        await asyncio.wait_for(self.engine._task, 1)

    async def test_complete_multiple_batches_exact_routing_and_interval(self):
        await self.run_engine(SAMPLE, batch((2084077,"Player3")), EMPTY)
        self.assertEqual(self.client.staff_texts, ["/p reset", "/p reset 2081755 2084076", "/p reset", "/p reset 2084077", "/p reset"])
        self.assertEqual(self.client.mine_texts, [r.command for r in parse_batch(SAMPLE).rows] + ["r grief pex user Player3 group set default"])
        self.assertTrue(all(b-a >= .025 for a,b in zip(self.client.mine_times, self.client.mine_times[1:])))
        self.assertEqual(self.statuses[-1]["state"], "done")
        self.assertEqual(self.statuses[-1]["sent_count"], 3)
        self.assertFalse(self.engine.busy)
        self.assertEqual(AresRemovals(self.client,self.path).interval, 5)

    async def test_immediate_stop_releases_worker_and_allows_next_start(self):
        self.assertTrue(self.engine.start())
        await self.engine.stop()
        self.assertFalse(self.engine.busy)
        self.assertEqual(self.client.sent,[])
        await self.run_engine(EMPTY)
        self.assertEqual(self.statuses[-1]['state'],'done')

    async def test_reordered_footer_executes_original_rows_and_acknowledges_ids(self):
        text=SAMPLE.replace('/p reset 2081755 2084076','/p reset 2084076 2081755')
        await self.run_engine(text,EMPTY)
        self.assertEqual(self.client.mine_texts,[row.command for row in parse_batch(SAMPLE).rows])
        self.assertIn('/p reset 2081755 2084076',self.client.staff_texts)

    async def test_initial_empty_stops_and_only_click_starts_again(self):
        await self.run_engine(EMPTY)
        await asyncio.sleep(.025)
        self.assertEqual(self.client.staff_texts, ["/p reset"])
        self.assertEqual(self.client.mine_texts, [])
        await self.run_engine(EMPTY)
        self.assertEqual(self.client.staff_texts, ["/p reset", "/p reset"])

    async def test_progress_is_ignored_and_edited_result_used(self):
        async def reply(client, query_id):
            mid = await client.emit_text("⚡ Запрос выполняется!")
            await asyncio.sleep(.005)
            await client.emit_text(SAMPLE, mid=mid, edited=True)
        await self.run_engine(reply, EMPTY)
        self.assertEqual(len(self.client.mine_texts), 2)

    async def test_all_parts_and_duplicate_updates(self):
        async def reply(client, query_id):
            at = SAMPLE.index("➤ 2084076")
            first = await client.emit_text(SAMPLE[:at])
            await client.emit_text(SAMPLE[:at], mid=first)
            await asyncio.sleep(.02)
            await client.emit_text(SAMPLE[at:])
        await self.run_engine(reply, EMPTY)
        self.assertEqual(len(self.client.mine_texts), 2)

    async def test_fast_reply_before_send_returns(self):
        self.client.early_reply = True
        await self.run_engine(SAMPLE, EMPTY)
        self.assertEqual(len(self.client.mine_texts), 2)

    async def test_confirmation_receipt_after_empty_does_not_poison_result(self):
        async def reply(client, query_id):
            await client.emit_text(EMPTY)
            await client.emit_text("⚡ Успешно выполнено!")
        await self.run_engine(SAMPLE, reply)
        self.assertEqual(self.statuses[-1]["state"],"done")
        self.assertEqual(len(self.client.mine_texts),2)

    async def test_bot_error_after_partial_list_prevents_execution(self):
        async def reply(client, query_id):
            await client.emit_text(SAMPLE.split("➤ 2084076")[0])
            await client.emit_text("❌ Ошибка: не удалось получить список")
        await self.run_engine(reply)
        self.assertEqual(self.statuses[-1]["state"],"error")
        self.assertEqual(self.client.mine_texts,[])

    async def test_history_recovers_missing_event_and_progress_edit(self):
        self.client.history_only = True
        async def reply(client, query_id):
            mid = await client.emit_text("⚡ Запрос выполняется!")
            await asyncio.sleep(.015)
            await client.emit_text(SAMPLE, mid=mid, edited=True)
        await self.run_engine(reply, EMPTY)
        self.assertEqual(len(self.client.mine_texts), 2)

    async def test_incomplete_timeout_sends_nothing_and_retry_works(self):
        await self.run_engine(SAMPLE.split("➤ 2084076")[0])
        self.assertEqual(self.client.mine_texts, [])
        self.assertEqual(self.statuses[-1]["state"], "error")
        await self.run_engine(EMPTY)
        self.assertEqual(self.statuses[-1]["state"], "done")

    async def test_bot_error_is_shown_without_commands(self):
        await self.run_engine("⚡ Ошибка: недостаточно прав!")
        self.assertIn("недостаточно прав", self.statuses[-1]["message"])
        self.assertEqual(self.client.mine_texts, [])

    async def test_malformed_list_sends_nothing(self):
        await self.run_engine(SAMPLE.replace("TOTOTOP", "TOTOTOP;op"))
        self.assertEqual(self.client.mine_texts, [])
        self.assertEqual(self.statuses[-1]["state"], "error")

    async def test_stop_between_commands_and_resume_without_duplicate(self):
        self.engine.interval = .3
        self.client.replies = [SAMPLE]
        self.engine.start()
        await self.until(lambda: len(self.client.mine_texts) == 1 and self.engine.uncertain is None)
        await self.engine.stop()
        self.assertEqual(self.statuses[-1]["state"], "stopped")
        self.assertNotIn("/p reset 2081755 2084076", self.client.staff_texts)
        self.engine.interval = .025
        self.engine._last_sent_at = 0
        await self.run_engine(SAMPLE, EMPTY)
        self.assertEqual(self.client.mine_texts, [r.command for r in parse_batch(SAMPLE).rows])
        self.assertIn("/p reset 2081755 2084076", self.client.staff_texts)

    async def test_stop_during_send_requires_visible_delivery_review(self):
        self.client.mine_wait = asyncio.Event()
        self.client.replies = [SAMPLE]
        self.engine.start()
        await self.until(lambda: len(self.client.mine_texts) == 1)
        await self.engine.stop()
        self.assertEqual(self.statuses[-1]["state"], "review")
        self.assertEqual(self.engine.uncertain.id, "2081755")
        before = len(self.client.sent)
        self.assertFalse(self.engine.start())
        self.assertEqual(len(self.client.sent), before)
        self.engine.resolve_uncertain(True)
        self.client.mine_wait = None
        await self.run_engine(SAMPLE, EMPTY)
        self.assertEqual(len(self.client.mine_texts), 2)

    async def test_server_rejection_does_not_ack_unsent_or_require_review(self):
        self.client.mine_error = FloodWaitError(request=None, capture=12)
        await self.run_engine(SAMPLE)
        self.assertIsNone(self.engine.uncertain)
        self.assertEqual(self.client.staff_texts, ["/p reset"])
        self.assertEqual(self.client.mine_texts, [])
        self.client.mine_error = None
        await self.run_engine(SAMPLE, EMPTY)
        self.assertEqual(len(self.client.mine_texts), 2)

    async def test_unknown_network_result_survives_recreation(self):
        self.client.mine_error = ConnectionError("test-only")
        await self.run_engine(SAMPLE)
        self.assertEqual(self.statuses[-1]["state"], "review")
        await self.engine.close()
        self.engine = AresRemovals(self.client,self.path,status=self.statuses.append,
            interval_seconds=.01,response_timeout=.15,send_timeout=.08,idle_seconds=.005,poll_seconds=.01)
        await self.engine.install()
        self.assertFalse(self.engine.start())
        self.assertEqual(self.engine.uncertain.id, "2081755")
        self.engine.resolve_uncertain(False)
        self.client.mine_error = None
        await self.run_engine(SAMPLE, EMPTY)
        self.assertEqual(len(self.client.mine_texts), 2)

    async def test_ack_failure_preserves_confirmed_commands_for_retry(self):
        self.client.ack_error = ConnectionError("test-only")
        await self.run_engine(SAMPLE)
        self.assertEqual(len(self.client.mine_texts), 2)
        self.assertIsNone(self.engine.uncertain)
        self.client.ack_error = None
        await self.run_engine(SAMPLE, EMPTY)
        self.assertEqual(len(self.client.mine_texts), 2)
        self.assertIn("/p reset 2081755 2084076", self.client.staff_texts)

    async def test_repeated_acknowledged_batch_stops_bounded_without_resending(self):
        await self.run_engine(SAMPLE, SAMPLE, SAMPLE)
        self.assertEqual(len(self.client.mine_texts), 2)
        self.assertEqual(self.statuses[-1]["state"], "error")
        self.assertIn("подтверждённые ID", self.statuses[-1]["message"])
        self.assertEqual(self.client.staff_texts.count("/p reset"), 3)

    async def test_completed_ids_remain_protected_after_empty_and_restart(self):
        await self.run_engine(SAMPLE, EMPTY)
        state = json.loads(self.engine.state_path.read_text())
        self.assertEqual(set(state["confirmed"]), {"2081755","2084076"})
        await self.run_engine(SAMPLE, EMPTY)
        self.assertEqual(len(self.client.mine_texts), 2)

    async def test_saved_id_cannot_change_command(self):
        await self.run_engine(SAMPLE, EMPTY)
        await self.run_engine(SAMPLE.replace("TOTOTOP","OtherPlayer"))
        self.assertEqual(self.statuses[-1]["state"], "error")
        self.assertEqual(len(self.client.mine_texts), 2)

    async def test_foreign_outgoing_old_and_wrong_thread_are_ignored(self):
        self.engine.start()
        await self.until(lambda:self.engine._reply and self.engine._reply.sent_id)
        query_id = self.engine._reply.sent_id
        for event in [Event(EMPTY,sender=BOT,mid=query_id-1),Event(EMPTY,sender=BOT,mid=query_id+1,out=True),
                      Event(EMPTY,sender=MINE_ID,mid=query_id+2),Event(EMPTY,sender=BOT,mid=query_id+3,private=False),
                      Event(EMPTY,sender=BOT,mid=query_id+4,reply_to=query_id-10)]:
            await self.engine._on_staff(event)
        self.assertEqual(self.engine._reply.messages, {})
        await self.client.emit_text(EMPTY,mid=query_id+5,reply_to=query_id)
        await self.engine._task
        self.assertEqual(self.statuses[-1]["state"], "done")

    async def test_double_start_keeps_single_workflow(self):
        self.client.replies = [EMPTY]
        self.assertTrue(self.engine.start())
        task = self.engine._task
        self.assertFalse(self.engine.start())
        self.assertIs(self.engine._task,task)
        await task
        self.assertEqual(self.client.staff_texts,["/p reset"])

    async def test_close_removes_only_own_handlers(self):
        other = lambda event:None
        self.client.handlers.append((other,None))
        await self.engine.close()
        self.assertEqual(self.client.handlers,[(other,None)])

    async def test_corrupt_checkpoint_is_not_overwritten_or_used(self):
        await self.engine.close()
        self.engine.state_path.write_text('{broken',encoding='utf-8')
        worker = AresRemovals(self.client,self.path)
        with self.assertRaises(RemovalsError):await worker.install()
        self.assertEqual(self.client.handlers,[])
        self.assertEqual(self.engine.state_path.read_text(),'{broken')

    async def test_separate_account_does_not_use_other_progress(self):
        await self.run_engine(SAMPLE, EMPTY)
        await self.engine.close()
        self.client.account_id = 778
        worker = AresRemovals(self.client,self.path)
        await worker.install()
        self.assertEqual(worker._confirmed,{})
        self.assertNotEqual(worker.state_path,self.engine.state_path)
        await worker.close()

    async def test_checkpoint_failure_before_send_prevents_network_effect(self):
        with patch.object(self.engine,"_save",side_effect=OSError("test-only")):
            await self.run_engine(SAMPLE)
        self.assertEqual(self.client.mine_texts,[])
        self.assertNotIn("/p reset 2081755 2084076",self.client.staff_texts)


class RemovalServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="ares-removal-service-")
        self.client = RemovalClient()
        self.statuses = []
        self.service = TelegramService({"command_relay_enabled":True,"command_relay_user_id":str(USER),
            "command_relay_timeout_seconds":15}, SimpleNamespace(),Path(self.directory.name)/"session",
            lambda k,p:self.statuses.append((k,p)))
        self.service.loop = asyncio.get_running_loop()
        self.service.client,self.service.connected = self.client,True
        await self.service._apply_command_relay()

    async def asyncTearDown(self):
        if self.service._removals:await self.service._removals.close()
        if self.service._command_relay:await self.service._command_relay.close()
        await self.client.cleanup()
        self.directory.cleanup()

    async def until(self,condition):
        async def wait():
            while not condition():await asyncio.sleep(.001)
        await asyncio.wait_for(wait(),1)

    async def test_active_relay_refuses_removals(self):
        relay = self.service._command_relay
        await relay._on_private(Event("/c list 14 Prop1oonat"))
        await self.service._start_removals()
        self.assertIsNone(self.service._removals)
        self.assertIn("/c list",self.statuses[-1][1]["message"])

    async def test_active_removals_refuses_private_command_without_cross_delivery(self):
        await self.service._start_removals()
        self.assertEqual(len(self.client.handlers),5)
        event = Event("/c list 14 Prop1oonat")
        relay = self.service._command_relay
        await relay._on_private(event)
        await relay._notice_task
        self.assertIn("снятия",event.responses[0][0])
        self.assertFalse(relay.busy)
        self.assertNotIn("/c list 14 Prop1oonat",self.client.staff_texts)

    async def test_late_removal_list_is_not_a_command_result(self):
        relay = self.service._command_relay
        await relay._on_private(Event("/c list 14 Prop1oonat"))
        await self.until(lambda:relay._pending and relay._pending.sent_id)
        await relay._on_bot(Event(SAMPLE,sender=BOT,mid=relay._pending.sent_id+1))
        await relay._on_bot(Event(EMPTY,sender=BOT,mid=relay._pending.sent_id+2))
        self.assertEqual(relay._pending.messages,{})

    async def test_pause_stops_running_removals(self):
        await self.service._start_removals()
        self.service.set_paused(True)
        await self.until(lambda:not self.service._removals.busy)
        self.assertEqual(self.service._removals._last_state,"stopped")

    async def test_disconnected_service_cannot_schedule(self):
        self.service.connected=False
        self.assertFalse(self.service.start_removals())
        self.assertEqual(self.statuses[-1][1]["state"],"offline")

    async def test_saved_unfinished_relay_refuses_start_even_if_disabled(self):
        self.service._relay_needs_check=True
        self.service.config["command_relay_enabled"]=False
        await self.service._apply_command_relay()
        await self.service._start_removals()
        self.assertIsNone(self.service._removals)
        self.assertIn("Сбросить",self.statuses[-1][1]["message"])

    async def test_two_start_calls_install_only_one_listener_set(self):
        await asyncio.gather(self.service._start_removals(),self.service._start_removals())
        self.assertEqual(len(self.client.handlers),5)
        self.assertIsNotNone(self.service._removals)
        self.assertTrue(self.service._removals.busy)

    async def test_stop_interrupts_bot_lookup_and_can_start_again(self):
        lookup = asyncio.Event()
        async def hanging(name):
            lookup.set()
            await asyncio.Event().wait()
        with patch.object(self.client,"get_entity",side_effect=hanging):
            self.assertTrue(self.service.start_removals())
            await lookup.wait()
            self.assertTrue(self.service.stop_removals())
            await self.until(lambda:not self.service._removals_starting)
        self.assertIsNone(self.service._removals)
        self.assertEqual(len(self.client.handlers),3)
        await self.service._start_removals()
        self.assertIsNotNone(self.service._removals)


if __name__ == "__main__":unittest.main()
