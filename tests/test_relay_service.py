"""Relay lifecycle inside Ares' real Telegram service, with no network traffic."""
import asyncio
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from telegram_service import TelegramService
from test_command_relay import Client, Event, USER, BOT


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='ares-service-tests-')
        self.statuses = []
        self.client = Client()
        self.service = TelegramService({
            "command_relay_enabled": True, "command_relay_user_id": str(USER),
            "command_relay_timeout_seconds": 15, "command_relay_forward": False,
        }, SimpleNamespace(), Path(self.directory.name)/'session', lambda kind, data: self.statuses.append((kind, data)))
        self.service.loop = asyncio.get_running_loop()
        self.service.client = self.client
        self.service.connected = True
        await self.service._apply_command_relay()
        self.service._command_relay.response_idle_seconds = .01

    async def asyncTearDown(self):
        self.service.connected = False
        if self.service._command_relay:
            await self.service._command_relay.close()
        self.directory.cleanup()

    async def until(self, condition):
        async def wait():
            while not condition():
                await asyncio.sleep(.001)
        await asyncio.wait_for(wait(), 1)

    async def request(self, sender=USER, mid=1):
        relay = self.service._command_relay
        event = Event("/c list 14 Prop1oonat", sender=sender, mid=mid)
        await relay._on_private(event)
        await self.until(lambda: relay._pending is not None and relay._pending.sent_id is not None)
        return relay, event

    async def answer(self, relay, text="Список"):
        relay.response_idle_seconds = .01
        await relay._on_bot(Event(text, sender=BOT, mid=self.client.next_id + 1))
        await relay._request_task

    async def test_installs_into_existing_client_and_routes_one_user(self):
        relay, request = await self.request()
        self.assertEqual(len(self.client.handlers), 3)
        self.assertEqual(self.client.sent[0][0].id, BOT)
        self.assertTrue(self.service.config["command_relay_needs_check"])
        await self.answer(relay)
        self.assertEqual(request.responses[0][0], "Список")
        self.assertFalse(self.service.config["command_relay_needs_check"])
        self.assertEqual(self.statuses[-1][1]["state"], "done")

    async def test_unrelated_settings_do_not_interrupt_request(self):
        relay, request = await self.request()
        self.service.config["auto_threshold"] = 97
        await self.service._apply_command_relay()
        self.assertIs(self.service._command_relay, relay)
        self.assertFalse(relay._request_task.done())
        await self.answer(relay)
        self.assertEqual(len(request.responses), 1)

    async def test_disable_cancels_wait_and_enable_keeps_late_reply_block(self):
        old, request = await self.request()
        self.service.config["command_relay_enabled"] = False
        await self.service._apply_command_relay()
        self.assertIsNone(self.service._command_relay)
        self.assertTrue(old._request_task.cancelled())
        self.assertEqual(self.client.handlers, [])
        self.assertEqual(request.responses, [])
        self.service.config["command_relay_enabled"] = True
        await self.service._apply_command_relay()
        new = self.service._command_relay
        self.assertTrue(new.needs_resync)
        await new._on_bot(Event("Поздний ответ", sender=BOT, mid=102))
        self.assertIsNone(new._pending)
        self.assertTrue(self.service.config["command_relay_needs_check"])

    async def test_change_recipient_removes_old_filter_and_preserves_block(self):
        old, _ = await self.request()
        self.service.config["command_relay_user_id"] = str(USER + 1)
        await self.service._apply_command_relay()
        new = self.service._command_relay
        self.assertEqual(new.allowed_user_id, USER + 1)
        self.assertEqual(len(self.client.handlers), 3)
        self.assertTrue(new.needs_resync)
        await new._on_private(Event("/c list 14 Prop1oonat", sender=USER, mid=2))
        self.assertIsNone(new._request_task)
        new.resume_after_check()
        relay, request = await self.request(sender=USER + 1, mid=3)
        await self.answer(relay, "Для нового человека")
        self.assertEqual(request.responses[0][0], "Для нового человека")

    async def test_global_pause_cancels_request_and_resume_requires_check(self):
        relay, request = await self.request()
        self.service.set_paused(True)
        await self.until(lambda: relay._request_task.done())
        self.assertTrue(relay.needs_resync)
        self.assertEqual(request.responses, [])
        self.service.set_paused(False)
        await asyncio.sleep(.01)
        self.assertTrue(relay.needs_resync)
        self.service.resume_command_relay()
        await self.until(lambda: self.service._command_relay is not relay)
        await self.until(lambda: self.service._command_relay is not None and not self.service._relay_restarting)
        self.assertFalse(self.service._command_relay.needs_resync)
        relay, second = await self.request(mid=2)
        await self.answer(relay)
        self.assertEqual(second.responses[0][0], "Список")

    async def test_saved_uncertain_state_survives_service_recreation(self):
        await self.service._command_relay.close()
        self.service._command_relay = None
        self.service._relay_needs_check = True
        await self.service._apply_command_relay()
        self.assertTrue(self.service._command_relay.needs_resync)
        self.assertEqual(self.statuses[-1][1]["state"], "needs_check")

    async def test_bot_lookup_failure_does_not_disable_ticket_client_and_can_retry(self):
        relay = self.service._command_relay
        await relay.close()
        self.service._command_relay = None
        other = lambda e: None
        self.client.handlers.append((other, None))
        with patch.object(self.client, "get_entity", AsyncMock(side_effect=ValueError("Бот недоступен"))):
            with self.assertLogs("telegram_service", "ERROR"):
                await self.service._apply_command_relay()
        self.assertTrue(self.service.connected)
        self.assertIsNone(self.service._command_relay)
        self.assertEqual(self.client.handlers, [(other, None)])
        self.assertEqual(self.statuses[-1][1]["state"], "error")
        await self.service._apply_command_relay()
        self.assertEqual(len(self.client.handlers), 4)

    async def test_forward_setting_reconfigures_and_sends_to_allowed_peer(self):
        self.service.config["command_relay_forward"] = True
        await self.service._apply_command_relay()
        relay, request = await self.request()
        await self.answer(relay)
        self.assertEqual(self.client.forwarded[0][0], USER)
        self.assertEqual(request.responses, [])

    async def test_normal_main_attaches_relay_and_closes_its_handlers(self):
        class MainClient(Client):
            def __init__(self, *args):
                super().__init__()
                self.disconnected = asyncio.Event()
            async def is_user_authorized(self):
                return True
            async def get_me(self):
                return SimpleNamespace(id=USER+500, bot=False)
            async def get_input_entity(self, value):
                return value
            def on(self, builder):
                def register(callback):
                    self.add_event_handler(callback, builder)
                    return callback
                return register
            async def run_until_disconnected(self):
                await self.disconnected.wait()
            async def disconnect(self):
                self.disconnected.set()

        statuses = []
        service = TelegramService({**self.service.config, "api_id": "1", "api_hash": "test-only",
                                   "source_chat": "@AresMineRobot", "response_chat": "@AresMineRobot"},
                                  SimpleNamespace(), Path(self.directory.name)/'session', lambda k, p: statuses.append((k, p)))
        client = MainClient()
        with patch("telegram_service.TelegramClient", return_value=client), patch.object(service, "_authorize_qr", AsyncMock()):
            task = asyncio.create_task(service._main())
            try:
                await self.until(lambda: service._command_relay is not None)
                self.assertTrue(service.connected)
                self.assertEqual(len(client.handlers), 5)
                request = Event("/c list 14 Prop1oonat")
                relay = service._command_relay
                relay.response_idle_seconds = .01
                await relay._on_private(request)
                await self.until(lambda: relay._pending is not None and relay._pending.sent_id is not None)
                await relay._on_bot(Event("⚡ Запрос выполняется!", sender=BOT, mid=client.next_id + 1))
                self.assertEqual(request.responses, [])
                await relay._on_bot(Event("Полный цикл", sender=BOT, mid=client.next_id + 2))
                await relay._request_task
                self.assertEqual(request.responses[0][0], "Полный цикл")
            finally:
                await client.disconnect()
                await asyncio.wait_for(task, 1)
        self.assertFalse(service.connected)
        self.assertEqual(len(client.handlers), 2)
        self.assertEqual(statuses[-1][1]["state"], "offline")

    async def test_service_keeps_every_part_and_pending_status_until_complete(self):
        relay, request = await self.request()
        first = 'Аккаунты (2):\n➤ One - 192.0.2.1\n➤ Two - 192.0.2'
        second = '.2\nНики (2):\n➤ One\n➤ Two'
        await relay._on_bot(Event(first, sender=BOT, mid=102))
        await asyncio.sleep(.025)
        self.assertTrue(self.service.config['command_relay_needs_check'])
        self.assertTrue(relay.busy)
        self.service.config['auto_threshold'] = 98
        await self.service._apply_command_relay()
        self.assertIs(self.service._command_relay, relay)
        await relay._on_bot(Event(second, sender=BOT, mid=103))
        await relay._request_task
        self.assertEqual([text for text, _ in request.responses], [first, second])
        self.assertFalse(self.service.config['command_relay_needs_check'])
        self.assertEqual(self.statuses[-1][1]['state'], 'done')

    async def test_disabling_during_collection_cancels_all_remaining_parts(self):
        relay, request = await self.request()
        await relay._on_bot(Event('Аккаунты (2):\n➤ One - 192.0.2.1', sender=BOT, mid=102))
        await asyncio.sleep(0)
        self.service.config['command_relay_enabled'] = False
        await self.service._apply_command_relay()
        self.assertTrue(relay._request_task.cancelled())
        self.assertEqual(request.responses, [])
        self.assertTrue(self.service.config['command_relay_needs_check'])
        self.assertEqual(self.client.handlers, [])

    async def test_resume_restarts_even_with_a_live_request(self):
        old, request = await self.request()
        old._blocked = True
        self.service.resume_command_relay()
        await self.until(lambda: self.service._command_relay is not old)
        await self.until(lambda: self.service._command_relay is not None)
        new = self.service._command_relay
        self.assertFalse(new.needs_resync)
        self.assertTrue(old._request_task.cancelled())
        self.assertEqual(len(self.client.handlers), 3)
        self.assertFalse(self.service.config['command_relay_needs_check'])
        relay, second = await self.request(mid=2)
        await self.answer(relay, 'После перезапуска')
        self.assertEqual(second.responses[-1][0], 'После перезапуска')

    async def test_restart_failure_reenables_button_and_preserves_ticket_handler(self):
        old = self.service._command_relay
        other = lambda event: None
        self.client.handlers.append((other, None))
        with patch.object(self.client, 'get_entity', AsyncMock(side_effect=ConnectionError('offline'))):
            with self.assertLogs('telegram_service', 'ERROR'):
                self.assertTrue(self.service.resume_command_relay())
                await self.until(lambda: self.service._command_relay is not old and not self.service._relay_restarting)
        self.assertIsNone(self.service._command_relay)
        self.assertEqual(self.client.handlers, [(other, None)])
        self.assertEqual(self.statuses[-1][1]['state'], 'error')
        self.assertTrue(self.statuses[-1][1]['can_restart'])
        self.assertTrue(self.service.resume_command_relay())
        await self.until(lambda: self.service._command_relay is not None)
        self.assertEqual(len(self.client.handlers), 4)

    async def test_stopped_loop_returns_error_instead_of_silent_button(self):
        original = self.service.loop
        stopped = asyncio.new_event_loop()
        try:
            self.service.loop = stopped
            self.assertFalse(self.service.resume_command_relay())
            self.assertEqual(self.statuses[-1][1]['state'], 'error')
            self.assertTrue(self.statuses[-1][1]['can_restart'])
            self.assertIn('поток', self.statuses[-1][1]['message'].lower())
        finally:
            self.service.loop = original
            stopped.close()

    async def test_status_from_replaced_handler_is_ignored(self):
        old = self.service._command_relay
        self.service.resume_command_relay()
        await self.until(lambda: self.service._command_relay is not old and not self.service._relay_restarting)
        self.assertFalse(self.service.config['command_relay_needs_check'])
        count = len(self.statuses)
        old._blocked = True
        old.publish_status()
        self.assertEqual(len(self.statuses), count)
        self.assertFalse(self.service.config['command_relay_needs_check'])

    async def test_repeated_restart_registers_exactly_one_handler_set(self):
        for _ in range(3):
            old = self.service._command_relay
            self.service.resume_command_relay()
            await self.until(lambda: self.service._command_relay is not old and not self.service._relay_restarting)
            self.assertEqual(len(self.client.handlers), 3)
            self.assertFalse(self.service._command_relay.needs_resync)


if __name__ == "__main__":
    unittest.main(verbosity=2)
