"""/c list <число> <ник> из ЛС одного человека → AresStaffRobot → его ЛС.

Python 3.10+, Telethon 1.44.0. API_ID/API_HASH остаются в вашем TelegramClient.
Один экземпляр на клиент. Подключать после авторизации, в том же asyncio loop.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, RPCError

LOGGER = logging.getLogger(__name__)
BOT_USERNAME = "@AresStaffRobot"
DEFAULT_ALLOWED_USER_ID = 123456789
# Проверяется вся строка: вторая команда через перенос строки не пройдёт.
COMMAND = re.compile(r"/c[ \t]+list[ \t]+([0-9]{1,10})[ \t]+([A-Za-z0-9_]{1,16})")
PREFIX = re.compile(r"^/c[ \t]+list(?:[ \t]|$)")
BOT_ERROR = re.compile(
    r"^\W*(?:ошибка\b|error\b|нет доступа\b|недостаточно прав\b|"
    r"доступ запрещ[её]н\b|игрок не найден\b|пользователь не найден\b)", re.I
)
# Служебное подтверждение не завершает запрос. Проверяем всю строку,
# чтобы не отбросить готовый результат, содержащий эти слова в тексте.
BOT_PROGRESS = re.compile(r"\W*запрос\s+выполняется\W*", re.I)
ACCOUNT_HEADER = re.compile(r"\bАккаунты\s*\(([0-9]{1,10})\)\s*:", re.I)
NICK_HEADER = re.compile(r"\bНики\s*\(([0-9]{1,10})\)\s*:", re.I)
LIST_BULLET = re.compile(r"[➤►▶›•]")


@dataclass
class _Pending:
    future: asyncio.Future
    sent_id: int | None = None
    early: list[tuple[Any, bool]] = field(default_factory=list)
    progress_ids: set[int] = field(default_factory=set)
    messages: dict[int, Any] = field(default_factory=dict)
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    first_received: float | None = None
    last_received: float = 0
    early_overflow: bool = False
    poll_after: int | None = None
    late: bool = False
    attempted: set[int] = field(default_factory=set)
    failed: set[int] = field(default_factory=set)


class AresCommandRelay:
    def __init__(
        self,
        client: TelegramClient,
        allowed_user_id: int,
        *,
        timeout_seconds: float = 15,
        delivery_timeout: float = 15,
        response_idle_seconds: float = 3,
        collection_timeout: float = 60,
        response_wait_seconds: float = 60,
        history_poll_seconds: float = 1,
        forward: bool = False,
        allow_unthreaded: bool = True,
        enabled: Callable[[], bool] | None = None,
        status: Callable[[dict[str, Any]], None] | None = None,
        initially_blocked: bool = False,
        external_busy: Callable[[], str | None] | None = None,
    ):
        if isinstance(allowed_user_id, bool) or not isinstance(allowed_user_id, int) or allowed_user_id <= 0:
            raise ValueError("allowed_user_id должен быть положительным числовым Telegram ID")
        if min(timeout_seconds, delivery_timeout, response_idle_seconds, collection_timeout,
               response_wait_seconds, history_poll_seconds) <= 0:
            raise ValueError("Таймаут должен быть больше нуля")
        self.client = client
        self.allowed_user_id = allowed_user_id
        self.timeout_seconds = float(timeout_seconds)
        self.delivery_timeout = float(delivery_timeout)
        # Первый результат ждём timeout_seconds. Затем собираем все части,
        # а не закрываем слушатель после первого сообщения.
        self.response_idle_seconds = float(response_idle_seconds)
        self.collection_timeout = float(collection_timeout)
        self.response_wait_seconds = float(response_wait_seconds)
        self.history_poll_seconds = float(history_poll_seconds)
        self.forward = forward
        self.allow_unthreaded = allow_unthreaded
        self.enabled = enabled or (lambda: True)
        self.external_busy = external_busy or (lambda: None)
        self.status = status
        self._bot = None
        self._bot_id = None
        self._pending: _Pending | None = None
        self._deferred_pending: _Pending | None = None
        self._deferred_event = None
        self._request_task: asyncio.Task | None = None
        self._notice_task: asyncio.Task | None = None
        self._late_watch_task: asyncio.Task | None = None
        self.late_watch_seconds = 300.0
        self._seen: OrderedDict[int, None] = OrderedDict()
        self._closing = False
        self._blocked = bool(initially_blocked)
        self._awaiting_bot = False
        self._last_state, self._last_message = "ready", "Готово · ожидание команды в ЛС"
        self._private_handler = self._on_private
        self._bot_handler = self._on_bot
        self._bot_edit_handler = self._on_bot_edited

    @property
    def busy(self) -> bool:
        return self._request_task is not None and not self._request_task.done()

    @property
    def needs_resync(self) -> bool:
        """После таймаута нельзя надёжно опознать будущий ответ без reply_to."""
        return self._blocked

    def _report(self, state: str, message: str) -> None:
        self._last_state, self._last_message = state, message
        if self.status is not None:
            try:
                self.status({"state": state, "message": message,
                             "needs_check": self._blocked or self._awaiting_bot,
                             "busy": self.busy})
            except Exception:
                LOGGER.exception("Unable to publish relay status")

    def publish_status(self) -> None:
        self._ensure_late_watch()
        if self._try_late_recovery():
            return
        if self._blocked:
            self._report("needs_check", "Нужна проверка диалога с ботом перед следующим запросом")
        elif self.external_busy():
            self._report("paused", self.external_busy())
        elif not self.enabled():
            self._report("paused", "Перенаправление команд на паузе")
        elif not self.busy:
            self._report("ready", "Готово · ожидание команды в ЛС")

    async def install(self) -> "AresCommandRelay":
        if getattr(self.client, "_ares_command_relay", None) is not None:
            raise RuntimeError("Обработчик AresCommandRelay уже подключён к этому клиенту")
        # Claim before the first await: simultaneous installation must not
        # register two listeners. Roll back if setup fails or is cancelled.
        self.client._ares_command_relay = self
        try:
            me = await asyncio.wait_for(self.client.get_me(), self.timeout_seconds)
            if me is None or getattr(me, "bot", False):
                raise ValueError("Нужен авторизованный пользовательский Telegram-клиент")
            if me.id == self.allowed_user_id:
                raise ValueError("Укажи ID другого человека, который присылает команды в твои ЛС")
            bot = await asyncio.wait_for(self.client.get_entity(BOT_USERNAME), self.timeout_seconds)
            if not getattr(bot, "bot", False):
                raise ValueError(f"{BOT_USERNAME} не является Telegram-ботом")
            self._bot, self._bot_id = bot, bot.id
            # Фильтр и дополнительные проверки внутри обработчиков защищают от групп,
            # чужих отправителей, исходящих сообщений и петель пересылки.
            self.client.add_event_handler(
                self._private_handler, events.NewMessage(incoming=True, from_users=self.allowed_user_id)
            )
            self.client.add_event_handler(
                self._bot_handler, events.NewMessage(incoming=True, chats=bot.id, from_users=bot.id)
            )
            self.client.add_event_handler(
                self._bot_edit_handler, events.MessageEdited(incoming=True, chats=bot.id, from_users=bot.id)
            )
        except BaseException:
            self.client.remove_event_handler(self._private_handler)
            self.client.remove_event_handler(self._bot_handler)
            self.client.remove_event_handler(self._bot_edit_handler)
            if getattr(self.client, '_ares_command_relay', None) is self:
                del self.client._ares_command_relay
            raise
        self.publish_status()
        return self

    async def _on_private(self, event) -> None:
        if self._closing or not event.is_private or event.out:
            return
        if event.sender_id != self.allowed_user_id or event.chat_id != self.allowed_user_id:
            return
        text = (event.raw_text or "").strip()
        if not PREFIX.match(text):
            return  # Любая другая команда/переписка не относится к этому модулю.
        if event.id in self._seen:
            return
        self._seen[event.id] = None
        if len(self._seen) > 2048:
            self._seen.popitem(last=False)
        if not COMMAND.fullmatch(text):
            self._notice(event, "Формат: /c list <число> <ник>\nПример: /c list 14 Prop1oonat")
        elif not self.enabled():
            self._notice(event, "Перенаправление команд сейчас выключено или на паузе.")
        elif self.external_busy():
            self._notice(event, self.external_busy())
        elif self.busy:
            self._notice(event, "Предыдущий запрос ещё выполняется. Дождись ответа и повтори команду.")
        elif self._blocked:
            self._notice(event, "Предыдущий запрос ещё не завершён. Перенаправление пока приостановлено; поздний ответ будет принят автоматически. Владелец клиента может нажать «Сбросить и возобновить» во вкладке «Команды».")
        else:
            # Не await здесь: это позволяет бот-ответам обрабатываться даже если
            # существующий клиент использует sequential_updates=True.
            self._request_task = asyncio.create_task(self._run(event, text), name="ares-command-relay")
            self._request_task.add_done_callback(self._request_done)

    async def _on_bot_edited(self, event) -> None:
        await self._on_bot(event, edited=True)

    async def _on_bot(self, event, *, edited: bool = False) -> None:
        if self._closing or not event.is_private or event.out:
            return
        if event.sender_id != self._bot_id or event.chat_id != self._bot_id:
            return
        pending = self._pending or self._deferred_pending
        if pending is None or (pending.future.cancelled() and not pending.late):
            return
        if pending.sent_id is None:
            # Telegram update иногда обгоняет завершение send_message().
            # Буфер ограничен и будет проверен по ID отправленной команды.
            if len(pending.early) < 256:
                pending.early.append((event.message, edited))
            else:
                pending.early_overflow = True
        else:
            self._accept(pending, event.message, edited=edited)
            self._try_late_recovery()

    def _accept(self, pending: _Pending, message, *, edited: bool = False) -> None:
        if (pending.future.cancelled() and not pending.late) or message.id <= pending.sent_id:
            return
        # Правка уже собранной части заменяет её, не создавая второй копии.
        # Посторонние старые сообщения не относятся к текущему запросу.
        if edited and message.id not in pending.progress_ids and message.id not in pending.messages:
            return
        reply_id = getattr(message, "reply_to_msg_id", None)
        if (reply_id is not None and reply_id != pending.sent_id
                and reply_id not in pending.progress_ids and reply_id not in pending.messages):
            return
        if reply_id is None and not self.allow_unthreaded:
            return
        text = " ".join((message.raw_text or "").split())
        if re.search(r"Список\s+на\s+(?:снятие|перебан)|неоформленн(?:ые|ых)\s+наказан(?:ия|ий)|убрано\s+из\s+списка\s+на\s+перебан|не\s+находились\s+или\s+не\s+найдены|(?:^|\s)/p\s+(?:reset|repunish)\b|(?:^|\s)[➤►•]?\s*\d+\.\s+r\s+grief\s+(?:pex\s+user|ban)\b|(?:^|\s)[➤►•]?\s*\d+\.\s*(?:\d{1,2}\.\d{1,2}\.\d{2,4}\s+\d{1,2}:\d{2}\s*-\s*)?(?:BAN|MUTE)\s*-", text, re.I):
            return  # В том же боте работают отдельные очереди снятий и перебанов.
        if BOT_PROGRESS.fullmatch(text):
            if message.id in pending.progress_ids:
                return
            pending.progress_ids.add(message.id)
            self._report("waiting", "Бот выполняет запрос · ожидание результата")
            return  # Не пересылаем статус и не перезапускаем общий таймаут.
        previous = pending.messages.get(message.id)
        if (previous is not None and previous.raw_text == message.raw_text
                and previous.entities == message.entities):
            return  # Повтор Telegram update не продлевает сбор и не дублируется.
        pending.messages[message.id] = message
        pending.last_received = asyncio.get_running_loop().time()
        if pending.first_received is None:
            pending.first_received = pending.last_received
        if not pending.future.done():
            pending.future.set_result(message)
        pending.changed.set()
        self._report("waiting", f"Сбор ответа бота · получено частей: {len(pending.messages)}")

    async def _request(self, command: str):
        pending = self._pending = _Pending(asyncio.get_running_loop().create_future())
        self._awaiting_bot = True
        # Сохраняем состояние до сетевого вызова: после перезапуска приложения
        # поздний ответ старой команды не должен стать ответом новой команды.
        self._report("waiting", f"Ожидание {BOT_USERNAME} · до {self.timeout_seconds:g} сек.")
        # Слушатель уже установлен; будущий ответ не потеряется при быстрой сети.
        sent = await self.client.send_message(self._bot, command, parse_mode=None, link_preview=False)
        pending.sent_id = sent.id
        for early, edited in pending.early:
            self._accept(pending, early, edited=edited)
        pending.early.clear()
        return pending

    async def _poll_history(self, pending: _Pending, budget: float) -> None:
        """Страховка от пропущенного update; только сообщения после команды.

        Читаем по возрастанию ID, а не произвольные последние сообщения.
        Служебные сообщения перечитываем по ID: бот мог их отредактировать.
        Ошибка чтения не останавливает основной слушатель событий.
        """
        if pending.sent_id is None or not hasattr(self.client, "get_messages"):
            return
        async def read():
            for _ in range(3):
                messages = await self.client.get_messages(
                    self._bot, limit=100, min_id=pending.poll_after or pending.sent_id, reverse=True)
                for message in sorted(messages, key=lambda item: item.id):
                    pending.poll_after = max(pending.poll_after or pending.sent_id, message.id)
                    if getattr(message, "sender_id", None) == self._bot_id and not getattr(message, "out", False):
                        self._accept(pending, message)
                if len(messages) < 100:
                    break
            unfinished = pending.progress_ids - pending.messages.keys()
            if unfinished:
                messages = await self.client.get_messages(self._bot, ids=sorted(unfinished))
                for message in messages:
                    if (message is not None and getattr(message, "sender_id", None) == self._bot_id
                            and not getattr(message, "out", False)):
                        self._accept(pending, message, edited=True)
        try:
            await asyncio.wait_for(read(), min(3, budget))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOGGER.debug("Bot history check unavailable: %s", type(exc).__name__)

    async def _wait_response(self, event, pending: _Pending) -> None:
        loop = asyncio.get_running_loop()
        start = loop.time()
        soft_deadline = start + self.timeout_seconds
        hard_deadline = start + max(self.timeout_seconds, self.response_wait_seconds)
        next_poll = start + self.history_poll_seconds
        notified = False
        while not pending.future.done():
            now = loop.time()
            if now >= hard_deadline:
                raise asyncio.TimeoutError
            if now >= soft_deadline and not notified:
                notified = True
                self._report("waiting", "Бот задерживается · продолжаю ждать результат")
                await self._reply_safe(event, "Бот пока не прислал готовый ответ. Продолжаю ждать результат; повторно отправлять команду не нужно.")
                continue
            if now >= next_poll:
                await self._poll_history(pending, hard_deadline - now)
                next_poll = loop.time() + self.history_poll_seconds
                continue
            wake = min(hard_deadline, next_poll, soft_deadline if not notified else hard_deadline)
            try:
                # wait_for без shield отменял бы Future на каждом опросе.
                await asyncio.wait_for(asyncio.shield(pending.future), max(.001, wake - now))
            except asyncio.TimeoutError:
                pass

    def _park(self, event, pending: _Pending | None) -> None:
        self._blocked = True
        if pending is not None and pending.sent_id is not None:
            pending.late = True
            self._deferred_pending, self._deferred_event = pending, event
            self._ensure_late_watch()

    def _ensure_late_watch(self) -> None:
        if self._deferred_pending is None or self._closing or not self.enabled():
            return
        if self._late_watch_task is None or self._late_watch_task.done():
            self._late_watch_task = asyncio.create_task(self._watch_late(), name="ares-late-history")
            self._late_watch_task.add_done_callback(self._task_done)

    async def _watch_late(self) -> None:
        pending = self._deferred_pending
        if pending is None:
            return  # Ответ или сброс мог завершиться до первого шага задачи.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.late_watch_seconds
        while self._deferred_pending is pending and not self._closing and loop.time() < deadline:
            await asyncio.sleep(self.history_poll_seconds)
            if self._deferred_pending is not pending or self._closing or not self.enabled():
                return
            await self._poll_history(pending, max(.001, min(3, deadline - loop.time())))
            self._try_late_recovery()

    def _try_late_recovery(self) -> bool:
        pending = self._deferred_pending
        if (pending is None or self._closing or self.busy or not self.enabled()
                or not (pending.messages.keys() - pending.attempted)):
            return False
        event = self._deferred_event
        self._deferred_pending, self._deferred_event = None, None
        pending.first_received = pending.last_received = asyncio.get_running_loop().time()
        self._pending = pending
        self._request_task = asyncio.create_task(self._recover_late(event, pending), name="ares-late-result")
        self._request_task.add_done_callback(self._request_done)
        return True

    async def _recover_late(self, event, pending: _Pending) -> None:
        self._awaiting_bot = True
        try:
            self._report("waiting", "Получен задержанный ответ · сбор всех частей")
            responses, incomplete = await self._collect_response(pending)
            if self._pending is pending:
                self._pending = None
            await self._complete_response(event, pending, responses, incomplete)
        except asyncio.CancelledError:
            self._park(event, pending)
            raise
        except Exception:
            self._park(event, pending)
            LOGGER.exception("Cannot recover late bot response")
            self._report("needs_check", "Не удалось завершить задержанный ответ · сбрось обработчик")
        finally:
            if self._pending is pending:
                self._pending = None
            self._awaiting_bot = False

    @staticmethod
    def _list_incomplete(messages: list[Any]) -> bool:
        """Заголовки Ares помогают дождаться задержавшейся части списка.

        Бот может разрезать текст даже посередине IP/ника, поэтому для проверки
        склеиваем части без дополнительных символов. В ЛС отправляем сами
        оригинальные части отдельно, сохраняя их текст и форматирование.
        Для ответов другого формата используем паузу между сообщениями.
        """
        text = "".join(message.raw_text or "" for message in messages)
        if any(BOT_ERROR.search(message.raw_text or "") for message in messages):
            return False
        accounts = ACCOUNT_HEADER.search(text)
        nicks = NICK_HEADER.search(text)
        if accounts is None and nicks is None:
            return False
        if nicks is None:
            return True  # Ожидаем секцию «Ники», даже если аккаунты уже пришли.
        if len(LIST_BULLET.findall(text[nicks.end():])) < int(nicks[1]):
            return True
        return (accounts is not None
                and len(LIST_BULLET.findall(text[accounts.end():nicks.start()])) < int(accounts[1]))

    async def _collect_response(self, pending: _Pending) -> tuple[list[Any], bool]:
        """Сбор до паузы; известный неполный список ждёт продолжение дольше.

        Служебные статусы и дубли не сбрасывают часы. Общий предел защищает
        от бесконечного потока. Неполный ответ передаём с предупреждением,
        а новый запрос блокируем, чтобы поздние части не смешались с ним.
        """
        loop = asyncio.get_running_loop()
        hard_deadline = pending.first_received + self.collection_timeout
        next_poll = loop.time() + self.history_poll_seconds
        while True:
            messages = [pending.messages[mid] for mid in sorted(pending.messages)]
            incomplete = self._list_incomplete(messages)
            quiet = max(self.timeout_seconds, self.response_idle_seconds) if incomplete else self.response_idle_seconds
            remaining = min(hard_deadline, pending.last_received + quiet) - loop.time()
            if remaining <= 0:
                return messages, incomplete or pending.early_overflow or loop.time() >= hard_deadline
            if loop.time() >= next_poll:
                await self._poll_history(pending, remaining)
                next_poll = loop.time() + self.history_poll_seconds
                continue
            # Между расчётом и clear нет await: поступивший update не теряется.
            pending.changed.clear()
            try:
                await asyncio.wait_for(pending.changed.wait(), min(remaining, max(.001, next_poll - loop.time())))
            except asyncio.TimeoutError:
                pass  # Пересчитываем срок и полноту с последними сообщениями.

    async def _run(self, event, command: str) -> None:
        try:
            if self._closing or not self.enabled():
                self.publish_status()
                return
            try:
                # Ограничивает и отправку команды, и ожидание ответа, включая
                # автоматические ожидания FloodWait внутри Telethon.
                pending = await asyncio.wait_for(self._request(command), max(self.timeout_seconds, self.delivery_timeout))
                await self._wait_response(event, pending)
                responses, incomplete = await self._collect_response(pending)
            except asyncio.TimeoutError:
                self._park(event, self._pending)
                self._awaiting_bot = False
                self._report("needs_check", "Ответ не пришёл · поздний результат принимается; доступен сброс обработчика")
                await self._reply_safe(event, f"{BOT_USERNAME} не ответил вовремя либо Telegram не подтвердил отправку. Если бот пришлёт результат позже, он будет передан в этот диалог. Владелец клиента может нажать «Сбросить и возобновить» во вкладке «Команды».")
                return
            except FloodWaitError as exc:
                self._awaiting_bot = False
                self._report("error", f"Ограничение Telegram · подожди {exc.seconds} сек.")
                await self._reply_safe(event, f"Telegram ограничил отправку. Подожди {exc.seconds} сек. и повтори запрос.")
                return
            except RPCError as exc:
                self._blocked = getattr(exc, "code", 500) >= 500
                self._awaiting_bot = False
                self._report("needs_check" if self._blocked else "error", f"Telegram: {type(exc).__name__}")
                LOGGER.warning("Relay request RPC error: %s", type(exc).__name__)
                await self._reply_safe(event, f"Telegram не выполнил запрос ({type(exc).__name__}). Владелец клиента может проверить доступ к боту.")
                return
            except (ConnectionError, OSError):
                self._blocked = True
                self._report("needs_check", "Сбой связи · результат отправки нужно проверить в Telegram")
                LOGGER.exception("Relay request connection error")
                await self._reply_safe(event, "Не удалось связаться с Telegram. Результат отправки неизвестен; перенаправление приостановлено до проверки владельцем клиента.")
                return
            except Exception:
                self._blocked = True
                self._report("needs_check", "Ошибка запроса · проверь диалог с ботом и журнал")
                LOGGER.exception("Relay request failed")
                await self._reply_safe(event, "Не удалось получить ответ бота. Владелец клиента может посмотреть ошибку в журнале программы.")
                return
            finally:
                pending, self._pending = self._pending, None
                if pending and not pending.future.done():
                    pending.future.cancel()

            await self._complete_response(event, pending, responses, incomplete)
        except asyncio.CancelledError:
            # Отмена сетевого ожидания не отменяет уже принятую ботом команду.
            self._blocked = self._blocked or self._awaiting_bot
            self._awaiting_bot = False
            self.publish_status()
            raise
        except Exception:
            LOGGER.exception("Unexpected relay failure")
            await self._reply_safe(event, "Ошибка обработчика. Владелец клиента может проверить журнал программы.")
        finally:
            self._awaiting_bot = False

    async def _complete_response(self, event, pending: _Pending, responses: list[Any], incomplete: bool) -> None:
        self._awaiting_bot = False
        self._blocked = bool(incomplete)
        if incomplete:
            self._park(event, pending)
        if self._closing or not self.enabled():
            self.publish_status()
            return
        # Уже отправленные части остаются в проверке полноты, но не дублируются
        # при получении задержанного продолжения того же запроса.
        responses = [response for response in responses if response.id not in pending.attempted]
        delivered = 0
        for index, response in enumerate(responses, 1):
            if self._closing or not self.enabled():
                return
            pending.attempted.add(response.id)
            self._report("delivering", f"Отправка в ЛС · часть {index} из {len(responses)}")
            success = await self._deliver_response(event, response)
            delivered += success
            if not success:
                pending.failed.add(response.id)
        if incomplete:
            await self._reply_safe(event, "Полученные части отправлены, но пока пришёл не весь результат. Задержанное продолжение будет передано автоматически. Если бот не завершит запрос, владелец клиента может сбросить обработчик команд.")
            self._report("needs_check", f"Доставлено частей: {delivered} · ожидаю задержанное продолжение; доступен сброс")
        else:
            success = not pending.failed
            total_delivered = len(pending.attempted) - len(pending.failed)
            self._report("done" if success else "error",
                         f"Отправлено частей: {total_delivered} · ожидание следующей команды" if success
                         else f"Доставлено {total_delivered} из {len(pending.messages)} частей · проверь Telegram")

    def _request_done(self, task: asyncio.Task) -> None:
        self._task_done(task)
        if self._request_task is task and not self._closing:
            if not self._try_late_recovery():
                self._report(self._last_state, self._last_message)  # busy уже False.

    async def _deliver_response(self, event, response) -> bool:
        text = response.raw_text or ""
        if text and BOT_ERROR.search(text):
            if len(text) <= 3500:
                return await self._reply_safe(event, "Бот вернул ошибку:\n\n" + text)
            await self._reply_safe(event, "Бот вернул ошибку:")
            return await self._reply_safe(event, text, entities=response.entities)
        if self.forward:
            try:
                peer = await asyncio.wait_for(event.get_input_chat(), self.delivery_timeout)
                if self._closing or not self.enabled():
                    return False
                await asyncio.wait_for(self.client.forward_messages(peer, response), self.delivery_timeout)
                return True
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("Unable to forward bot response")
                await self._reply_safe(event, "Ответ получен, но Telegram не разрешил или не подтвердил его пересылку. Проверь диалог с владельцем клиента.")
                return False
        if text:
            # parse_mode=None: <>, _ и * буквально; entities из оригинала.
            return await self._reply_safe(event, text, entities=response.entities)
        return await self._reply_safe(event, "Бот прислал сообщение без текста. Для файлов/медиа включи пересылку оригинала в настройках «Команды».")

    async def _reply_safe(self, event, text: str, *, entities=None) -> bool:
        if self._closing:
            return False
        try:
            await asyncio.wait_for(event.reply(text, parse_mode=None, formatting_entities=entities, link_preview=False), self.delivery_timeout)
            return True
        except asyncio.CancelledError:
            raise
        except Exception:
            # Если Telegram запретил отправку адресату, доставить ему ещё одну
            # ошибку тем же способом невозможно. Показываем её в журнале клиента.
            LOGGER.exception("Cannot deliver relay response/notice to allowed user %s", self.allowed_user_id)
            return False

    def _notice(self, event, text: str) -> None:
        # Одновременно не копим уведомления «занято» при серии одинаковых запросов.
        if self._notice_task is not None and not self._notice_task.done():
            return
        self._notice_task = asyncio.create_task(self._reply_safe(event, text), name="ares-relay-notice")
        self._notice_task.add_done_callback(self._task_done)

    @staticmethod
    def _task_done(task: asyncio.Task) -> None:
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                LOGGER.error("Relay task failed", exc_info=(type(error), error, error.__traceback__))

    def resume_after_check(self) -> None:
        """Вызывать владельцу ПОСЛЕ проверки, что старый запрос завершён.

        Если бот не ставит reply_to, автоматически отличить поздний старый ответ
        от нового нельзя. Проверка владельцем заменяет ненадёжный автоповтор.
        """
        if self.busy:
            raise RuntimeError("Дождись завершения текущего запроса")
        self._deferred_pending, self._deferred_event = None, None
        if self._late_watch_task is not None and not self._late_watch_task.done():
            self._late_watch_task.cancel()
        self._blocked = False
        self.publish_status()

    async def cancel_pending(self) -> None:
        """Остановить активный запрос при паузе/изменении настроек."""
        tasks = [t for t in (self._request_task, self._notice_task, self._late_watch_task)
                 if t is not None and not t.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self) -> None:
        """Снять только свои хэндлеры; остальные функции клиента остаются доступны."""
        self._closing = True
        self.client.remove_event_handler(self._private_handler)
        self.client.remove_event_handler(self._bot_handler)
        self.client.remove_event_handler(self._bot_edit_handler)
        await self.cancel_pending()
        self._deferred_pending, self._deferred_event = None, None
        if getattr(self.client, "_ares_command_relay", None) is self:
            del self.client._ares_command_relay


async def attach_ares_relay(
    client: TelegramClient,
    allowed_user_id: int = DEFAULT_ALLOWED_USER_ID,
    **options,
) -> AresCommandRelay:
    """Использует уже авторизованный клиент; новая сессия/вход не нужны."""
    return await AresCommandRelay(client, allowed_user_id, **options).install()
