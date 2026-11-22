"""Meaningful integration scenarios using real Telethon imports and a fake network."""
import asyncio
import logging
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from telethon.errors import FloodWaitError, RPCError
from ares_command_relay import attach_ares_relay, AresCommandRelay, DEFAULT_ALLOWED_USER_ID

USER = DEFAULT_ALLOWED_USER_ID
BOT = 980000
# Та же структура, что в примере пользователя; IP — адрес для документации.
ACCOUNT_LIST = """Аккаунты (5):
➤ mama_ptica5 - 192.0.2.10
➤ mama_ptica7 - 192.0.2.10
➤ mama_ptica_9 - 192.0.2.10
➤ mama_ptica10 - 192.0.2.10
➤ mama_ptica11 - 192.0.2.10
Ники (5):
➤ mama_ptica5
➤ mama_ptica7
➤ mama_ptica_9
➤ mama_ptica10
➤ mama_ptica11"""

class Event:
    def __init__(self, text, *, sender=USER, chat=None, private=True, out=False, mid=1, reply_to=None, entities=None):
        self.raw_text = text
        self.sender_id = sender
        self.chat_id = sender if chat is None else chat
        self.is_private = private
        self.out = out
        self.id = mid
        self.message = SimpleNamespace(id=mid, raw_text=text, entities=entities, reply_to_msg_id=reply_to)
        self.responses = []
        self.error = None
    async def reply(self, text, **options):
        if self.error:
            raise self.error
        self.responses.append((text, options))
    async def get_input_chat(self):
        return self.chat_id

class Client:
    def __init__(self):
        self.handlers = []
        self.sent = []
        self.forwarded = []
        self.next_id = 100
        self.sending = asyncio.Event()
        self.send_error = None
        self.early = None
    async def get_me(self):
        await asyncio.sleep(0)
        return SimpleNamespace(id=777, bot=False)
    async def get_entity(self, username):
        return SimpleNamespace(id=BOT, bot=True)
    def add_event_handler(self, callback, builder):
        self.handlers.append((callback, builder))
    def remove_event_handler(self, callback):
        self.handlers[:] = [(f,b) for f,b in self.handlers if f != callback]
    async def send_message(self, entity, text, **options):
        if self.send_error:
            raise self.send_error
        self.next_id += 1
        self.sent.append((entity, text, options, self.next_id))
        if self.early:
            await self._ares_command_relay._on_bot(self.early)
        self.sending.set()
        return SimpleNamespace(id=self.next_id)
    async def forward_messages(self, peer, response):
        self.forwarded.append((peer, response))

class RelayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = Client()
        self.relay = await attach_ares_relay(
            self.client, timeout_seconds=.05, delivery_timeout=.05, response_idle_seconds=.01,
            response_wait_seconds=.05, history_poll_seconds=.01)
    async def asyncTearDown(self):
        await self.relay.close()
    async def start_request(self, event=None):
        event = event or Event('/c list 14 Prop1oonat')
        await self.relay._on_private(event)
        await asyncio.wait_for(self.client.sending.wait(), .2)
        return event
    async def finish(self, text='Список для Prop1oonat', *, reply_to=None, mid=None, entities=None):
        event = Event(text, sender=BOT, mid=mid or self.client.next_id+1, reply_to=reply_to, entities=entities)
        await self.relay._on_bot(event)
        await self.relay._request_task
        return event
    async def notice_done(self):
        if self.relay._notice_task:
            await self.relay._notice_task

    async def test_copy_command_and_exact_text_entities(self):
        request = await self.start_request()
        result = await self.finish('Ник: _Prop1oonat_ <тест> *literal*', entities=['fake-entity'])
        self.assertEqual(self.client.sent[0][1], '/c list 14 Prop1oonat')
        text, options = request.responses[0]
        self.assertEqual(text, result.raw_text)
        self.assertIsNone(options['parse_mode'])
        self.assertEqual(options['formatting_entities'], ['fake-entity'])
        self.assertFalse(self.relay.busy)

    async def test_reject_other_users_groups_and_outgoing(self):
        for event in [Event('/c list 14 Prop1oonat', sender=USER+1), Event('/c list 14 Prop1oonat', private=False, chat=-123), Event('/c list 14 Prop1oonat', out=True), Event('/p ok')]:
            await self.relay._on_private(event)
        self.assertEqual(self.client.sent, [])
        self.assertIsNone(self.relay._request_task)

    async def test_invalid_format_does_not_send_command(self):
        for index, text in enumerate(['/c list 14', '/c list 14 name\n/p ok', '/c list 14 a;b', '/c list xx Prop1oonat'], 1):
            event=Event(text, mid=index)
            await self.relay._on_private(event)
            await self.notice_done()
            self.assertIn('Формат:', event.responses[0][0])
        self.assertEqual(self.client.sent, [])

    async def test_fast_response_before_send_returns(self):
        self.client.early = Event('Мгновенный список', sender=BOT, mid=102)
        request=await self.start_request()
        await self.relay._request_task
        self.assertEqual(request.responses[0][0], 'Мгновенный список')

    async def test_wrong_reply_to_old_messages_other_bot_ignored(self):
        request=await self.start_request()
        for event in [Event('чужой', sender=12345, mid=200), Event('старый', sender=BOT, mid=90), Event('другая команда', sender=BOT, mid=102, reply_to=999)]:
            await self.relay._on_bot(event)
        self.assertFalse(self.relay._pending.future.done())
        await self.finish('нужный ответ', reply_to=self.client.next_id)
        self.assertEqual(request.responses[0][0], 'нужный ответ')

    async def test_no_parallel_commands_and_replay(self):
        first=await self.start_request()
        await self.relay._on_private(first)
        second=Event('/c list 15 Another_name', mid=2)
        await self.relay._on_private(second)
        await self.notice_done()
        self.assertEqual(len(self.client.sent), 1)
        self.assertIn('ещё выполняется', second.responses[0][0])
        await self.finish()

    async def test_later_valid_request_after_success(self):
        await self.start_request()
        await self.finish()
        event=Event('/c list 15 Another_name', mid=2)
        await self.relay._on_private(event)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await self.finish('второй список')
        self.assertEqual(len(self.client.sent), 2)
        self.assertEqual(event.responses[0][0], 'второй список')

    async def test_timeout_late_reply_is_never_next_result(self):
        first=await self.start_request()
        await self.relay._request_task
        self.assertIn('не ответил', first.responses[0][0])
        self.assertTrue(self.relay.needs_resync)
        self.assertIsNone(self.relay._pending)
        await self.relay._on_bot(Event('запоздавший результат', sender=BOT, mid=102))
        await self.relay._request_task
        self.assertEqual(first.responses[-1][0], 'запоздавший результат')
        self.assertFalse(self.relay.needs_resync)
        second=Event('/c list 15 Another_name', mid=2)
        await self.relay._on_private(second)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await self.finish('новый результат', mid=104)
        self.assertEqual(len(self.client.sent), 2)
        self.assertEqual(second.responses[-1][0], 'новый результат')
        self.assertFalse(self.relay.needs_resync)

    async def test_forward_preserves_original_message(self):
        self.relay.forward=True
        request=await self.start_request()
        await self.relay._on_bot(Event('⚡ Запрос выполняется!', sender=BOT, mid=102))
        self.assertEqual(self.client.forwarded, [])
        result=await self.finish('список', mid=103)
        self.assertEqual(self.client.forwarded, [(USER, result.message)])
        self.assertEqual(request.responses, [])

    async def test_error_from_bot_is_returned(self):
        request=await self.start_request()
        await self.finish('❌ Ошибка: игрок не найден')
        self.assertIn('Бот вернул ошибку:', request.responses[0][0])
        self.assertIn('игрок не найден', request.responses[0][0])

    async def test_flood_wait_not_silently_retried(self):
        self.client.send_error=FloodWaitError(request=None, capture=10)
        event=Event('/c list 14 Prop1oonat')
        await self.relay._on_private(event)
        await self.relay._request_task
        self.assertIn('10 сек.', event.responses[0][0])
        self.assertFalse(self.relay.needs_resync)

    async def test_network_failure_blocks_uncertain_retries(self):
        self.client.send_error=ConnectionError('offline')
        event=Event('/c list 14 Prop1oonat')
        with self.assertLogs('ares_command_relay', 'ERROR'):
            await self.relay._on_private(event)
            await self.relay._request_task
        self.assertTrue(self.relay.needs_resync)
        self.assertIn('связаться', event.responses[0][0])

    async def test_telegram_rpc_failure_notifies_user(self):
        self.client.send_error=RPCError(request=None, message='CHAT_WRITE_FORBIDDEN', code=403)
        event=Event('/c list 14 Prop1oonat')
        with self.assertLogs('ares_command_relay', 'WARNING'):
            await self.relay._on_private(event)
            await self.relay._request_task
        self.assertFalse(self.relay.needs_resync)
        self.assertIn('Telegram не выполнил', event.responses[0][0])

    async def test_paused_only_notifies_allowed_person(self):
        self.relay.enabled=lambda:False
        event=Event('/c list 14 Prop1oonat')
        await self.relay._on_private(event)
        await self.notice_done()
        self.assertIn('на паузе', event.responses[0][0])
        self.assertEqual(self.client.sent, [])

    async def test_close_cancels_wait_and_preserves_other_handlers(self):
        other=lambda e:None
        self.client.handlers.append((other,None))
        await self.start_request()
        await self.relay.close()
        self.assertEqual(self.client.handlers, [(other,None)])
        self.assertTrue(self.relay._request_task.cancelled())
        self.assertIsNone(self.relay._pending)
        self.assertFalse(hasattr(self.client, '_ares_command_relay'))

    async def test_duplicate_install_and_invalid_id(self):
        with self.assertRaises(RuntimeError):
            await attach_ares_relay(self.client)
        with self.assertRaises(ValueError):
            AresCommandRelay(self.client,0)
        client=Client()
        with self.assertRaises(ValueError):
            await attach_ares_relay(client,allowed_user_id=777)
        self.assertFalse(hasattr(client, '_ares_command_relay'))
        self.assertEqual(client.handlers, [])

    async def test_concurrent_install_one_listener_pair(self):
        client=Client()
        results=await asyncio.gather(attach_ares_relay(client),attach_ares_relay(client),return_exceptions=True)
        relays=[r for r in results if isinstance(r,AresCommandRelay)]
        self.assertEqual(len(relays),1)
        self.assertEqual(len(client.handlers),3)
        await relays[0].close()

    async def test_strict_threaded_mode(self):
        self.relay.allow_unthreaded=False
        request=await self.start_request()
        await self.relay._on_bot(Event('без привязки',sender=BOT,mid=102))
        self.assertFalse(self.relay._pending.future.done())
        await self.finish('точный ответ',reply_to=self.client.next_id)
        self.assertEqual(request.responses[0][0],'точный ответ')

    async def test_delivery_failure_is_logged(self):
        request=await self.start_request()
        request.error=RPCError(request=None,message='USER_IS_BLOCKED',code=403)
        with self.assertLogs('ares_command_relay','ERROR'):
            await self.finish('список')
        self.assertFalse(self.relay.busy)

    async def test_empty_media_gets_notice_in_copy_mode(self):
        request=await self.start_request()
        await self.finish('')
        self.assertIn('без текста', request.responses[0][0])

    async def test_progress_then_account_list_copies_only_complete_result(self):
        request = await self.start_request(Event('/c list 14 mama_ptica11'))
        await self.relay._on_bot(Event('⚡ Запрос выполняется!', sender=BOT, mid=102))
        await asyncio.sleep(0)
        self.assertEqual(request.responses, [])
        self.assertFalse(self.relay._pending.future.done())
        self.assertTrue(self.relay.busy)
        await self.finish(ACCOUNT_LIST, mid=103, entities=['formatting'])
        self.assertEqual(request.responses, [(ACCOUNT_LIST, {
            'parse_mode': None, 'formatting_entities': ['formatting'], 'link_preview': False})])

    async def test_progress_whitespace_emoji_case_and_repeats_are_ignored(self):
        request = await self.start_request()
        for mid, text in enumerate(['⚡ Запрос выполняется!', '  ⚡️ ЗАПРОС   ВЫПОЛНЯЕТСЯ...\u00a0', 'Запрос\nвыполняется'], 102):
            await self.relay._on_bot(Event(text, sender=BOT, mid=mid))
        self.assertEqual(request.responses, [])
        self.assertFalse(self.relay._pending.future.done())
        await self.finish('Аккаунты (0):\nНики (0):', mid=110)
        self.assertEqual(request.responses[0][0], 'Аккаунты (0):\nНики (0):')

    async def test_progress_only_times_out_but_late_result_recovers_automatically(self):
        request = await self.start_request()
        task = self.relay._request_task
        await self.relay._on_bot(Event('⚡ Запрос выполняется!', sender=BOT, mid=102))
        await task
        self.assertIn('не ответил', request.responses[0][0])
        self.assertTrue(self.relay.needs_resync)
        self.assertIs(self.relay._request_task, task)
        await self.relay._on_bot(Event(ACCOUNT_LIST, sender=BOT, mid=103))
        await self.relay._request_task
        self.assertEqual(len(request.responses), 2)
        self.assertEqual(request.responses[-1][0], ACCOUNT_LIST)
        self.assertFalse(self.relay.needs_resync)

    async def test_edit_of_progress_message_delivers_result_once(self):
        request = await self.start_request()
        await self.relay._on_bot(Event('⚡ Запрос выполняется!', sender=BOT, mid=102))
        await self.relay._on_bot_edited(Event('⚡ Запрос выполняется...', sender=BOT, mid=102))
        self.assertEqual(request.responses, [])
        edited = Event(ACCOUNT_LIST, sender=BOT, mid=102)
        await self.relay._on_bot_edited(edited)
        await self.relay._request_task
        await self.relay._on_bot_edited(edited)
        self.assertEqual(len(request.responses), 1)
        self.assertEqual(request.responses[0][0], ACCOUNT_LIST)

    async def test_progress_and_result_before_send_returns(self):
        original = self.client.send_message
        async def sending(*args, **kwargs):
            sent = await original(*args, **kwargs)
            await self.relay._on_bot(Event('⚡ Запрос выполняется!', sender=BOT, mid=sent.id + 1))
            await self.relay._on_bot(Event(ACCOUNT_LIST, sender=BOT, mid=sent.id + 2))
            return sent
        self.client.send_message = sending
        request = await self.start_request()
        await self.relay._request_task
        self.assertEqual(len(request.responses), 1)
        self.assertEqual(request.responses[0][0], ACCOUNT_LIST)

    async def test_progress_and_edited_result_before_send_returns(self):
        original = self.client.send_message
        async def sending(*args, **kwargs):
            sent = await original(*args, **kwargs)
            await self.relay._on_bot(Event('⚡ Запрос выполняется!', sender=BOT, mid=sent.id + 1))
            await self.relay._on_bot_edited(Event(ACCOUNT_LIST, sender=BOT, mid=sent.id + 1))
            return sent
        self.client.send_message = sending
        request = await self.start_request()
        await self.relay._request_task
        self.assertEqual(request.responses[0][0], ACCOUNT_LIST)

    async def test_bot_error_after_progress_is_delivered(self):
        request = await self.start_request()
        await self.relay._on_bot(Event('⚡ Запрос выполняется!', sender=BOT, mid=102))
        await self.finish('❌ Ошибка: игрок не найден', mid=103)
        self.assertEqual(len(request.responses), 1)
        self.assertIn('игрок не найден', request.responses[0][0])

    async def test_progress_phrase_inside_complete_result_is_not_discarded(self):
        request = await self.start_request()
        result = 'Аккаунты (0):\nНики (0):\nПримечание: запрос выполняется!'
        await self.finish(result)
        self.assertEqual(request.responses[0][0], result)

    async def test_unrelated_edit_cannot_become_result(self):
        request = await self.start_request()
        await self.relay._on_bot(Event('⚡ Запрос выполняется!', sender=BOT, mid=102))
        for edited in [Event('Посторонняя правка', sender=BOT, mid=103), Event('Чужой бот', sender=1234, mid=102), Event('Группа', sender=BOT, chat=-123, private=False, mid=102), Event('Исходящее', sender=BOT, mid=102, out=True)]:
            await self.relay._on_bot_edited(edited)
        self.assertFalse(self.relay._pending.future.done())
        await self.finish(ACCOUNT_LIST, mid=104)
        self.assertEqual(request.responses[0][0], ACCOUNT_LIST)

    async def test_result_can_reply_to_this_requests_progress_message(self):
        self.relay.allow_unthreaded = False
        request = await self.start_request()
        await self.relay._on_bot(Event('⚡ Запрос выполняется!', sender=BOT, mid=102, reply_to=self.client.next_id))
        await self.finish(ACCOUNT_LIST, mid=103, reply_to=102)
        self.assertEqual(request.responses[0][0], ACCOUNT_LIST)

    async def test_realistic_164_account_list_split_mid_line_delivers_every_part(self):
        names = [f'Player_{i:09d}' for i in range(164)]
        full = ('Аккаунты (164):\n' + '\n'.join(f'➤ {name} - 192.0.2.123' for name in names)
                + '\nНики (164):\n' + '\n'.join(f'➤ {name}' for name in names))
        parts = [full[start:start + 4096] for start in range(0, len(full), 4096)]
        self.assertGreater(len(parts), 2)
        request = await self.start_request(Event('/c list 14 trenchan5897'))
        await self.relay._on_bot(Event('⚡ Запрос выполняется!', sender=BOT, mid=102))
        for index, part in enumerate(parts):
            await self.relay._on_bot(Event(part, sender=BOT, mid=103 + index, entities=[f'entity-{index}']))
        await self.relay._request_task
        self.assertEqual([text for text, _ in request.responses], parts)
        self.assertEqual(''.join(text for text, _ in request.responses), full)
        self.assertEqual([options['formatting_entities'] for _, options in request.responses],
                         [[f'entity-{i}'] for i in range(len(parts))])
        self.assertFalse(self.relay.needs_resync)

    async def test_incomplete_list_waits_for_continuation_beyond_quiet_period(self):
        self.relay.timeout_seconds = .2
        request = await self.start_request()
        first = 'Аккаунты (2):\n➤ One - 192.0.2.1\n➤ Two - 192.0.2'
        second = '.2\nНики (2):\n➤ One\n➤ Two'
        await self.relay._on_bot(Event(first, sender=BOT, mid=102))
        await asyncio.sleep(.025)  # Longer than .01 seconds of ordinary quiet.
        self.assertTrue(self.relay.busy)
        self.assertEqual(request.responses, [])
        await self.finish(second, mid=103)
        self.assertEqual([text for text, _ in request.responses], [first, second])
        self.assertFalse(self.relay.needs_resync)

    async def test_missing_nicks_waits_even_when_account_section_is_complete(self):
        self.relay.timeout_seconds = .2
        request = await self.start_request()
        await self.relay._on_bot(Event('Аккаунты (1):\n➤ One - 192.0.2.1', sender=BOT, mid=102))
        await asyncio.sleep(.025)
        self.assertTrue(self.relay.busy)
        await self.finish('\nНики (1):\n➤ One', mid=103)
        self.assertEqual(len(request.responses), 2)

    async def test_generic_multipart_resets_quiet_period_after_each_new_part(self):
        self.relay.response_idle_seconds = .04
        request = await self.start_request()
        for index in range(3):
            await self.relay._on_bot(Event(f'Часть {index + 1}', sender=BOT, mid=102 + index))
            if index < 2:
                await asyncio.sleep(.025)
                self.assertTrue(self.relay.busy)
        await self.relay._request_task
        self.assertEqual([text for text, _ in request.responses], ['Часть 1', 'Часть 2', 'Часть 3'])
        self.assertFalse(self.relay.needs_resync)

    async def test_forward_mode_forwards_all_parts_in_original_id_order(self):
        self.relay.forward = True
        request = await self.start_request()
        events = [Event(f'Часть {i}', sender=BOT, mid=102 + i) for i in range(3)]
        for index in (0, 2, 1):  # Concurrent updates may arrive out of order.
            await self.relay._on_bot(events[index])
        await self.relay._request_task
        self.assertEqual(self.client.forwarded, [(USER, event.message) for event in events])
        self.assertEqual(request.responses, [])

    async def test_replayed_parts_are_not_duplicated(self):
        request = await self.start_request()
        for event in [Event('Часть 1', sender=BOT, mid=102), Event('Часть 2', sender=BOT, mid=103)]:
            await self.relay._on_bot(event)
            await self.relay._on_bot(event)
        await self.relay._request_task
        self.assertEqual([text for text, _ in request.responses], ['Часть 1', 'Часть 2'])

    async def test_edit_of_collected_part_replaces_it_instead_of_duplicating(self):
        request = await self.start_request()
        await self.relay._on_bot(Event('Часть 1', sender=BOT, mid=102))
        await self.relay._on_bot_edited(Event('Исправленная часть 1', sender=BOT, mid=102, entities=['edited']))
        await self.finish('Часть 2', mid=103)
        self.assertEqual([text for text, _ in request.responses], ['Исправленная часть 1', 'Часть 2'])
        self.assertEqual(request.responses[0][1]['formatting_entities'], ['edited'])

    async def test_progress_edit_as_first_part_followed_by_new_messages(self):
        request = await self.start_request()
        await self.relay._on_bot(Event('⚡ Запрос выполняется!', sender=BOT, mid=102))
        await self.relay._on_bot_edited(Event('Первая часть', sender=BOT, mid=102))
        await self.finish('Вторая часть', mid=103)
        self.assertEqual([text for text, _ in request.responses], ['Первая часть', 'Вторая часть'])

    async def test_all_40_early_parts_before_send_confirmation_are_kept(self):
        original = self.client.send_message
        async def sending(*args, **kwargs):
            sent = await original(*args, **kwargs)
            for index in range(40):
                await self.relay._on_bot(Event(f'Часть {index}', sender=BOT, mid=sent.id + 1 + index))
            return sent
        self.client.send_message = sending
        request = await self.start_request()
        await self.relay._request_task
        self.assertEqual([text for text, _ in request.responses], [f'Часть {index}' for index in range(40)])
        self.assertFalse(self.relay.needs_resync)

    async def test_new_command_remains_busy_while_remaining_parts_are_collected(self):
        request = await self.start_request()
        await self.relay._on_bot(Event('Часть 1', sender=BOT, mid=102))
        other = Event('/c list 15 Another_name', mid=2)
        await self.relay._on_private(other)
        await self.notice_done()
        self.assertEqual(len(self.client.sent), 1)
        self.assertIn('ещё выполняется', other.responses[0][0])
        await self.finish('Часть 2', mid=103)
        self.assertEqual([text for text, _ in request.responses], ['Часть 1', 'Часть 2'])

    async def test_continuation_can_reply_to_previous_part_in_strict_mode(self):
        self.relay.allow_unthreaded = False
        request = await self.start_request()
        await self.relay._on_bot(Event('Часть 1', sender=BOT, mid=102, reply_to=self.client.next_id))
        await self.finish('Часть 2', mid=103, reply_to=102)
        self.assertEqual([text for text, _ in request.responses], ['Часть 1', 'Часть 2'])

    async def test_unrelated_events_during_collection_do_not_enter_result(self):
        request = await self.start_request()
        await self.relay._on_bot(Event('Часть 1', sender=BOT, mid=102))
        for event in [Event('Старый', sender=BOT, mid=90), Event('Чужой', sender=999, mid=103),
                      Event('Другая команда', sender=BOT, mid=104, reply_to=888)]:
            await self.relay._on_bot(event)
        await self.relay._on_bot_edited(Event('Посторонняя правка', sender=BOT, mid=105))
        await self.finish('Часть 2', mid=106)
        self.assertEqual([text for text, _ in request.responses], ['Часть 1', 'Часть 2'])

    async def test_incomplete_list_is_delivered_with_notice_and_blocks_late_parts(self):
        self.relay.timeout_seconds = .025
        request = await self.start_request()
        first = 'Аккаунты (2):\n➤ One - 192.0.2.1'
        await self.relay._on_bot(Event(first, sender=BOT, mid=102))
        await self.relay._request_task
        self.assertEqual(request.responses[0][0], first)
        self.assertIn('не весь результат', request.responses[1][0])
        self.assertTrue(self.relay.needs_resync)
        await self.relay._on_bot(Event('Поздняя часть', sender=BOT, mid=103))
        self.assertEqual(len(request.responses), 2)
        next_request = Event('/c list 15 Another_name', mid=2)
        await self.relay._on_private(next_request)
        await self.notice_done()
        self.assertEqual(len(self.client.sent), 1)

    async def test_continuous_parts_have_a_hard_time_limit(self):
        self.relay.collection_timeout = .04
        self.relay.response_idle_seconds = .03
        request = await self.start_request()
        for index in range(8):
            await self.relay._on_bot(Event(f'Часть {index}', sender=BOT, mid=102 + index))
            await asyncio.sleep(.01)
        await asyncio.wait_for(self.relay._request_task, .3)
        self.assertTrue(self.relay.needs_resync)
        self.assertEqual(request.responses[0][0], 'Часть 0')
        self.assertIn('не весь результат', request.responses[-1][0])

    async def test_cancel_during_multipart_collection_preserves_uncertain_state(self):
        await self.start_request()
        await self.relay._on_bot(Event('Аккаунты (2):\n➤ One - 192.0.2.1', sender=BOT, mid=102))
        await asyncio.sleep(0)
        await self.relay.cancel_pending()
        self.assertTrue(self.relay.needs_resync)
        self.assertTrue(self.relay._request_task.cancelled())
        self.assertIsNone(self.relay._pending)

    async def test_bot_error_after_partial_list_ends_collection_and_notifies(self):
        request = await self.start_request()
        await self.relay._on_bot(Event('Аккаунты (2):\n➤ One - 192.0.2.1', sender=BOT, mid=102))
        await self.finish('❌ Ошибка: список недоступен', mid=103)
        self.assertEqual(len(request.responses), 2)
        self.assertIn('Бот вернул ошибку', request.responses[1][0])
        self.assertFalse(self.relay.needs_resync)

    async def test_failure_of_one_part_still_attempts_the_remaining_parts(self):
        request = await self.start_request()
        original = request.reply
        async def reply(text, **options):
            if text == 'Часть 2':
                raise RPCError(request=None, message='CHAT_WRITE_FORBIDDEN', code=403)
            await original(text, **options)
        request.reply = reply
        statuses = []
        self.relay.status = statuses.append
        with self.assertLogs('ares_command_relay', 'ERROR'):
            for index in range(3):
                await self.relay._on_bot(Event(f'Часть {index + 1}', sender=BOT, mid=102 + index))
            await self.relay._request_task
        self.assertEqual([text for text, _ in request.responses], ['Часть 1', 'Часть 3'])
        self.assertEqual(statuses[-1]['state'], 'error')
        self.assertIn('2 из 3', statuses[-1]['message'])

    async def test_slow_bot_result_is_still_delivered_after_soft_timeout(self):
        self.relay.response_wait_seconds = .2
        request = await self.start_request()
        await self.relay._on_bot(Event('⚡ Запрос выполняется!', sender=BOT, mid=102))
        await asyncio.sleep(.075)
        self.assertFalse(self.relay.needs_resync)
        self.assertTrue(self.relay.busy)
        await self.finish(ACCOUNT_LIST, mid=103)
        self.assertEqual(request.responses[-1][0], ACCOUNT_LIST)
        self.assertFalse(self.relay.needs_resync)

    async def test_missing_update_is_recovered_from_bot_history(self):
        self.relay.history_poll_seconds = .005
        result = Event(ACCOUNT_LIST, sender=BOT, mid=102).message
        result.sender_id, result.out = BOT, False
        async def history(*args, **options):
            return [result]
        self.client.get_messages = history
        request = await self.start_request()
        await self.relay._request_task
        self.assertEqual(request.responses[-1][0], ACCOUNT_LIST)
        self.assertFalse(self.relay.needs_resync)

    async def test_history_poll_recovers_edit_of_known_progress_message(self):
        result = Event(ACCOUNT_LIST, sender=BOT, mid=102).message
        result.sender_id, result.out = BOT, False
        async def history(*args, **options):
            return [result] if options.get('ids') else []
        self.client.get_messages = history
        request = await self.start_request()
        await self.relay._on_bot(Event('⚡ Запрос выполняется!', sender=BOT, mid=102))
        await self.relay._request_task
        self.assertEqual(request.responses[-1][0], ACCOUNT_LIST)

    async def test_history_is_filtered_to_this_bot_incoming_after_command(self):
        good = Event('нужный ответ', sender=BOT, mid=106).message
        records = [Event('старый ответ', sender=BOT, mid=90).message,
                   Event('исходящее', sender=USER, mid=103, out=True).message,
                   Event('другой отправитель', sender=123, mid=104).message,
                   Event('чужой запрос', sender=BOT, mid=105, reply_to=888).message, good]
        for index, message in enumerate(records):
            message.sender_id = [BOT, USER, 123, BOT, BOT][index]
            message.out = index == 1
        calls = []
        async def history(*args, **options):
            calls.append(options)
            return records
        self.client.get_messages = history
        request = await self.start_request()
        await self.relay._request_task
        self.assertEqual([text for text, _ in request.responses], ['нужный ответ'])
        self.assertEqual(calls[0]['min_id'], self.client.next_id)
        self.assertTrue(calls[0]['reverse'])

    async def test_history_failure_does_not_break_event_delivery(self):
        async def history(*args, **options):
            raise ConnectionError('offline history')
        self.client.get_messages = history
        request = await self.start_request()
        await asyncio.sleep(.02)
        await self.finish(ACCOUNT_LIST, mid=102)
        self.assertEqual(request.responses[-1][0], ACCOUNT_LIST)
        self.assertFalse(self.relay.needs_resync)

    async def test_late_continuation_is_delivered_without_repeating_earlier_part(self):
        self.relay.timeout_seconds = .02
        first = 'Аккаунты (1):\n➤ One - 192.0.2.1'
        second = '\nНики (1):\n➤ One'
        request = await self.start_request()
        await self.relay._on_bot(Event(first, sender=BOT, mid=102))
        await self.relay._request_task
        self.assertTrue(self.relay.needs_resync)
        await self.relay._on_bot(Event(second, sender=BOT, mid=103))
        await self.relay._request_task
        texts = [text for text, _ in request.responses]
        self.assertEqual(texts.count(first), 1)
        self.assertEqual(texts.count(second), 1)
        self.assertFalse(self.relay.needs_resync)
        await self.relay._on_bot(Event(second, sender=BOT, mid=103))
        self.assertEqual(len(request.responses), len(texts))

    async def test_late_progress_edit_after_hard_timeout_recovers_result(self):
        request = await self.start_request()
        await self.relay._on_bot(Event('⚡ Запрос выполняется!', sender=BOT, mid=102))
        await self.relay._request_task
        await self.relay._on_bot_edited(Event(ACCOUNT_LIST, sender=BOT, mid=102))
        await self.relay._request_task
        self.assertEqual(request.responses[-1][0], ACCOUNT_LIST)
        self.assertFalse(self.relay.needs_resync)

    async def test_finished_status_reports_not_busy(self):
        statuses = []
        self.relay.status = statuses.append
        await self.start_request()
        await self.finish(ACCOUNT_LIST)
        await asyncio.sleep(0)
        self.assertFalse(statuses[-1]['busy'])
        self.assertFalse(statuses[-1]['needs_check'])

    async def test_lost_update_after_hard_timeout_is_recovered_from_history(self):
        records = []
        async def history(*args, **options):
            return list(records)
        self.client.get_messages = history
        self.relay.late_watch_seconds = .3
        request = await self.start_request()
        await self.relay._request_task
        self.assertTrue(self.relay.needs_resync)
        result = Event(ACCOUNT_LIST, sender=BOT, mid=102).message
        result.sender_id, result.out = BOT, False
        records.append(result)
        async def delivered():
            while request.responses[-1][0] != ACCOUNT_LIST:
                await asyncio.sleep(.001)
        await asyncio.wait_for(delivered(), .4)
        self.assertFalse(self.relay.needs_resync)

    async def test_failed_early_part_is_not_reported_as_full_delivery_after_late_result(self):
        self.relay.timeout_seconds = .02
        request = await self.start_request()
        first = 'Аккаунты (1):\n➤ One - 192.0.2.1'
        second = '\nНики (1):\n➤ One'
        original = request.reply
        async def reply(text, **options):
            if text == first:
                raise RPCError(request=None, message='USER_IS_BLOCKED', code=403)
            await original(text, **options)
        request.reply = reply
        statuses = []
        self.relay.status = statuses.append
        with self.assertLogs('ares_command_relay', 'ERROR'):
            await self.relay._on_bot(Event(first, sender=BOT, mid=102))
            await self.relay._request_task
        await self.relay._on_bot(Event(second, sender=BOT, mid=103))
        await self.relay._request_task
        self.assertEqual(request.responses[-1][0], second)
        self.assertFalse(self.relay.needs_resync)
        self.assertEqual(statuses[-1]['state'], 'error')
        self.assertIn('1 из 2', statuses[-1]['message'])

if __name__ == '__main__':
    unittest.main(verbosity=2)
