from __future__ import annotations

import asyncio
import logging
import queue
import re
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Optional

from telethon import TelegramClient, events, functions
from telethon.errors import PasswordHashInvalidError, SessionPasswordNeededError, RPCError
from reply_guard import source_key
from core import template_key
from ares_command_relay import attach_ares_relay, AresCommandRelay, DEFAULT_ALLOWED_USER_ID
from ares_removals import AresRemovals, RemovalsError
from ares_repunish import AresRepunish, RepunishSchedule, options_from_config

LOGGER = logging.getLogger(__name__)
Emit = Callable[[str, Any], None]


class TelegramService:
    def __init__(self, config: dict[str, Any], knowledge_base: Any, session_path: Path, emit: Emit):
        self.config = dict(config)
        self.knowledge_base = knowledge_base
        self.session_path = session_path
        self.emit = emit
        self.thread: Optional[threading.Thread] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.client: Optional[TelegramClient] = None
        self._stop_requested = threading.Event()
        self._paused = threading.Event()
        self._auth_input: queue.Queue[tuple[str, str]] = queue.Queue()
        self._auto_timestamps: deque[float] = deque()
        self._program_texts: deque[tuple[str, float]] = deque()
        self.connected = False
        self._send_lock = None
        self._command_relay: Optional[AresCommandRelay] = None
        self._relay_lock: Optional[asyncio.Lock] = None
        self._relay_settings = None
        self._relay_needs_check = bool(self.config.get("command_relay_needs_check", False))
        self._relay_generation = 0
        self._relay_restarting = False
        self._last_relay_status: dict[str, Any] = {}
        self._removals: Optional[AresRemovals] = None
        self._removals_starting = False
        self._removals_start_task: Optional[asyncio.Task] = None
        self._repunish = None
        self._repunish_starting = False
        self._repunish_start_task = None
        self._repunish_schedule = None
        self._repunish_settings_lock = None
        self._repunish_notified = False

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self._stop_requested.clear()
        self.thread = threading.Thread(target=self._thread_main, name="TelegramService", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self._stop_requested.set()
        try:
            self._auth_input.put_nowait(("cancel", ""))
        except Exception:
            pass
        if self.loop and self.client:
            try:
                asyncio.run_coroutine_threadsafe(self.client.disconnect(), self.loop)
            except Exception:
                LOGGER.exception("Unable to request Telegram disconnect")

    def submit_auth(self, kind: str, value: str) -> None:
        self._auth_input.put((kind, value))

    def set_paused(self, paused: bool) -> None:
        if paused:
            self._paused.set()
        else:
            self._paused.clear()
        if self.loop and self.connected:
            self._schedule_relay(self._sync_relay_pause())

    def _emit_relay(self, info: dict[str, Any]) -> None:
        self._relay_needs_check = bool(info.get("needs_check", self._relay_needs_check))
        self.config["command_relay_needs_check"] = self._relay_needs_check
        self._last_relay_status = {**info, "needs_check": self._relay_needs_check}
        self.emit("command_relay_status", {**self._last_relay_status,
                  "service_id": id(self), "can_restart": not self._relay_restarting})

    def _emit_current_relay(self, generation: int, info: dict[str, Any]) -> None:
        if generation == self._relay_generation:
            self._emit_relay(info)

    def _relay_signature(self):
        return (bool(self.config.get("command_relay_enabled", False)),
                str(self.config.get("command_relay_user_id", DEFAULT_ALLOWED_USER_ID)).strip(),
                str(self.config.get("command_relay_timeout_seconds", 15)),
                bool(self.config.get("command_relay_forward", False)))

    def _schedule_relay(self, coroutine) -> bool:
        if not self.loop or self.loop.is_closed() or not self.loop.is_running():
            coroutine.close()
            self._emit_relay({"state": "error", "message": "Поток Telegram остановлен. Отключи и снова подключи Telegram."})
            return False
        try:
            future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        except RuntimeError:
            coroutine.close()
            self._emit_relay({"state": "error", "message": "Не удалось передать команду потоку Telegram. Подключи Telegram заново."})
            return False
        def complete(done):
            try:
                done.result()
            except Exception as exc:
                LOGGER.exception("Command relay control failed")
                self._emit_relay({"state": "error", "message": f"Не удалось перезапустить команды: {type(exc).__name__}. Кнопку можно нажать повторно."})
        future.add_done_callback(complete)
        return True

    def update_command_relay(self) -> None:
        """Вызывать после изменения self.config из UI; операции идут в loop клиента."""
        if self.connected:
            self._schedule_relay(self._apply_command_relay())
        else:
            self._emit_relay({"state": "offline", "message": "Подключи Telegram для обработки команд" if self.config.get("command_relay_enabled") else "Перенаправление выключено"})

    async def _apply_command_relay(self, *, force: bool = False) -> None:
        if self._relay_lock is None:
            self._relay_lock = asyncio.Lock()
        async with self._relay_lock:
            desired = self._relay_signature()
            if not force and self._command_relay is not None and desired == self._relay_settings:
                return  # Сохранение других настроек не обрывает текущий запрос.
            self._relay_generation += 1
            generation = self._relay_generation
            if self._command_relay is not None:
                old = self._command_relay
                self._command_relay = None
                try:
                    await asyncio.wait_for(old.close(), 5)
                except asyncio.TimeoutError:
                    LOGGER.warning("Old relay cancellation exceeded its deadline")
                    # close снимает хэндлеры до первого await. Освобождаем
                    # регистрацию, даже если сеть задержала отмену задачи.
                    if getattr(self.client, "_ares_command_relay", None) is old:
                        del self.client._ares_command_relay
                self._relay_needs_check |= old.needs_resync
            if force:
                self._relay_needs_check = False
                self.config["command_relay_needs_check"] = False
            self._relay_settings = desired
            if not desired[0]:
                self._emit_relay({"state": "disabled", "message": "Перенаправление команд выключено"})
                return
            if not self.connected or self._stop_requested.is_set():
                return
            self._emit_relay({"state": "connecting", "message": "Подключение обработчика к @AresStaffRobot…"})
            try:
                user_id, timeout = int(desired[1]), float(desired[2])
                if not 5 <= timeout <= 60:
                    raise ValueError("Таймаут команд должен быть от 5 до 60 секунд")
                self._command_relay = await asyncio.wait_for(attach_ares_relay(
                    self.client, user_id, timeout_seconds=timeout, forward=desired[3],
                    initially_blocked=self._relay_needs_check,
                    external_busy=self._staff_busy_reason,
                    status=lambda info: self._emit_current_relay(generation, info),
                    enabled=lambda settings=desired: (
                        self._relay_signature() == settings and not self.paused and not self._stop_requested.is_set()
                    ),
                ), 15)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOGGER.exception("Unable to install Ares command relay")
                self._emit_relay({"state": "error", "message": f"Не удалось подключить команды: {str(exc) or type(exc).__name__}. Можно повторить сброс."})

    async def _sync_relay_pause(self) -> None:
        if self.paused:
            if self._repunish_start_task is not None:self._repunish_start_task.cancel()
            if self._repunish is not None:await self._repunish.stop()
        if self.paused and self._removals is not None:
            await self._removals.stop()
        if self._relay_lock is None:
            return
        async with self._relay_lock:
            if self._command_relay is not None:
                if self.paused:
                    await self._command_relay.cancel_pending()
                self._command_relay.publish_status()

    def resume_command_relay(self) -> bool:
        async def operation():
            self._relay_restarting = True
            self._emit_relay({"state": "restarting", "message": "Сбрасываю текущий запрос и перезапускаю команды…"})
            try:
                await asyncio.wait_for(self._apply_command_relay(force=True), 20)
            finally:
                self._relay_restarting = False
                self._emit_relay(self._last_relay_status)
        if not self.connected:
            self._emit_relay({"state": "offline", "message": "Для сброса команд сначала подключи Telegram."})
            return False
        return self._schedule_relay(operation())

    def _staff_busy_reason(self) -> str | None:
        if self._repunish_starting or (self._repunish is not None and self._repunish.busy):
            return "Сейчас выполняются перебаны. Дождись завершения и повтори /c list."
        if self._removals_starting or (self._removals is not None and self._removals.busy):
            return "Сейчас выполняются снятия. Дождись завершения и повтори /c list."
        return None

    def _emit_removals(self, info: dict[str, Any]) -> None:
        self.emit("removals_status", {**info, "service_id": id(self)})
        if self.connected and not info.get("busy") and self._command_relay is not None:
            self._command_relay.publish_status()

    def _schedule_removals(self, coroutine) -> bool:
        if not self.connected or not self.loop or self.loop.is_closed() or not self.loop.is_running():
            coroutine.close()
            self._emit_removals({"state": "offline", "busy": False, "message": "Сначала подключи Telegram кнопкой вверху."})
            return False
        try:
            future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        except RuntimeError:
            coroutine.close()
            self._emit_removals({"state": "error", "busy": False, "message": "Поток Telegram остановлен. Подключи Telegram заново."})
            return False
        def complete(done):
            try:
                done.result()
            except Exception as exc:
                LOGGER.exception("Removals control failed")
                self._emit_removals({"state": "error", "busy": bool(self._staff_busy_reason()),
                    "message": str(exc) if isinstance(exc, RemovalsError) else f"Не удалось запустить снятия: {type(exc).__name__}. Можно повторить запуск."})
        future.add_done_callback(complete)
        return True

    def start_removals(self) -> bool:
        return self._schedule_removals(self._start_removals())

    async def _start_removals(self) -> None:
        if self._relay_lock is None:
            self._relay_lock = asyncio.Lock()
        # Relay и снятия резервируют StaffRobot в одном потоке. До первого
        # сетевого await выставляем флаг; ЛС не создаст параллельный запрос.
        async with self._relay_lock:
            if self._staff_busy_reason():
                if self._removals and self._removals.busy:
                    self._removals.publish_status()
                else:
                    self._emit_removals({"state":"busy","busy":False,"message":"Сейчас выполняются перебаны. Дождись завершения."})
                return
            if self.paused or self._stop_requested.is_set():
                self._emit_removals({"state": "paused", "busy": False, "message": "Сними общую паузу перед запуском снятий."})
                return
            if (self._command_relay is not None and self._command_relay.busy) or self._relay_restarting:
                self._emit_removals({"state": "busy", "busy": False, "message": "Сейчас выполняется /c list. Дождись его завершения."})
                return
            if self._relay_needs_check or (self._command_relay is not None and self._command_relay.needs_resync):
                self._emit_removals({"state": "busy", "busy": False, "message": "Сначала заверши старый /c list или нажми «Сбросить и возобновить» в Настройки → Команды."})
                return
            self._removals_starting = True
            self._removals_start_task = asyncio.current_task()
            self._emit_removals({"state": "connecting", "busy": True, "message": "Подключаю ботов снятий…"})
            try:
                if self._removals is None:
                    worker = AresRemovals(self.client, self.session_path.parent,
                        status=self._emit_removals,
                        enabled=lambda: self.connected and not self.paused and not self._stop_requested.is_set())
                    await worker.install()
                    self._removals = worker
                self._removals.start()
            except asyncio.CancelledError:
                self._emit_removals({"state": "stopped", "busy": False, "message": "Подключение снятий остановлено. Можно запустить снова."})
            finally:
                self._removals_starting = False
                self._removals_start_task = None
                if self._command_relay is not None:
                    self._command_relay.publish_status()

    def stop_removals(self) -> bool:
        async def operation():
            if self._removals_start_task is not None:
                self._removals_start_task.cancel()
            # Этот lock также ждёт завершения первоначального подключения.
            if self._relay_lock is None:
                return
            async with self._relay_lock:
                if self._removals is not None:
                    await self._removals.stop()
        return self._schedule_removals(operation())

    def resolve_removal_send(self, was_sent: bool) -> bool:
        async def operation():
            if self._removals is not None:
                self._removals.resolve_uncertain(was_sent)
        return self._schedule_removals(operation())

    def _emit_repunish(self, info):
        info=dict(info)
        scheduler=self._repunish_schedule
        if scheduler is not None:
            try:
                if info.get('state')=='done':scheduler.completed()
                elif info.get('state') in ('stopped','error','review'):scheduler.failed(bool(info.get('retryable')))
            except Exception:
                LOGGER.exception('Unable to save repunish schedule')
                info['schedule_error']='Не удалось сохранить таймер. Ручной запуск доступен.'
            info.update(next_run=scheduler.next_at,retry_at=scheduler.retry_at,schedule=scheduler.options['mode'])
        self.emit('repunish_status',{**info,'service_id':id(self)})
        if info.get('rows') and not self._repunish_notified:
            allowed=sum(row['reason'] in ('1.1','1.6','1.8','1.9','1.10','1.11','2.7') for row in info['rows'])
            if allowed and bool(self.config.get('repunish_notify_start',True)):
                self.emit('repunish_notice',f'Перебаны запущены: {allowed} команд в первой пачке.')
            self._repunish_notified=True
        if self.connected and not info.get('busy') and self._command_relay is not None:
            self._command_relay.publish_status()

    def _schedule_repunish(self, coroutine):
        if not self.connected or not self.loop or self.loop.is_closed() or not self.loop.is_running():
            coroutine.close()
            self._emit_repunish({'state':'offline','busy':False,'message':'Сначала подключи Telegram кнопкой вверху.'})
            return False
        try:future=asyncio.run_coroutine_threadsafe(coroutine,self.loop)
        except RuntimeError:
            coroutine.close();self._emit_repunish({'state':'error','busy':False,'message':'Поток Telegram остановлен. Подключи Telegram заново.'});return False
        def complete(done):
            try:done.result()
            except Exception as exc:
                LOGGER.exception('Repunish control failed')
                self._emit_repunish({'state':'error','busy':bool(self._repunish and self._repunish.busy),
                    'message':str(exc) if isinstance(exc,(RemovalsError,ValueError)) else f'Ошибка перебанов: {type(exc).__name__}. Можно повторить запуск.'})
        future.add_done_callback(complete)
        return True

    def _repunish_ready(self):
        return (self.connected and not self.paused and not self._stop_requested.is_set()
            and not self._staff_busy_reason() and not self._relay_restarting and not self._relay_needs_check
            and not (self._command_relay and (self._command_relay.busy or self._command_relay.needs_resync)))

    async def _apply_repunish_settings(self):
        if self._repunish_settings_lock is None:self._repunish_settings_lock=asyncio.Lock()
        async with self._repunish_settings_lock:
            await self._apply_repunish_settings_locked()

    async def _apply_repunish_settings_locked(self):
        options=options_from_config(self.config)
        if self._repunish is not None:self._repunish.interval=options['delay']
        if self._repunish_schedule is None:
            me=await asyncio.wait_for(self.client.get_me(),15)
            if me is None or getattr(me,'bot',False):raise RemovalsError('Нужна пользовательская сессия Telegram.')
            options=options_from_config(self.config)
            self._repunish_schedule=RepunishSchedule(
                self.session_path.parent/f'repunish_schedule_{me.id}.json',options,
                self._start_repunish,self._repunish_ready,
                lambda info:self.emit('repunish_status',{**info,'service_id':id(self)}))
            self._repunish_schedule.start_loop()
        else:
            self._repunish_schedule.update(options,busy=bool(self._repunish and self._repunish.busy))
            self._repunish_schedule.start_loop()

    def update_repunish_settings(self, values):
        options_from_config({**self.config,**values})
        self.config.update(values)
        return self._schedule_repunish(self._apply_repunish_settings())

    def start_repunish(self):return self._schedule_repunish(self._start_repunish())

    async def _start_repunish(self):
        if self._relay_lock is None:self._relay_lock=asyncio.Lock()
        async with self._relay_lock:
            if self._repunish and self._repunish.busy:
                self._repunish.publish_status();return False
            if not self._repunish_ready():
                if self.paused:message='Сними общую паузу перед запуском перебанов.'
                elif self._staff_busy_reason():message='Сейчас выполняются снятия. Дождись завершения.'
                elif self._relay_needs_check or (self._command_relay and self._command_relay.needs_resync):
                    message='Сначала проверь старый /c list или сбрось его в Настройки → Команды.'
                else:message='Сейчас выполняется /c list. Дождись завершения.'
                self._emit_repunish({'state':'busy','busy':False,'message':message});return False
            self._repunish_starting=True;self._repunish_start_task=asyncio.current_task()
            self._repunish_notified=False
            self._emit_repunish({'state':'connecting','busy':True,'message':'Подключаю обработчик перебанов…'})
            worker=None
            try:
                if self._repunish is None:
                    worker=AresRepunish(self.client,self.session_path.parent,status=self._emit_repunish,
                        interval_seconds=options_from_config(self.config)['delay'],
                        enabled=lambda:self.connected and not self.paused and not self._stop_requested.is_set())
                    await worker.install();self._repunish=worker
                started=self._repunish.start()
                if started and self._repunish_schedule:self._repunish_schedule.started()
                return started
            except asyncio.CancelledError:
                if worker is not None and self._repunish is None:await worker.close()
                self._emit_repunish({'state':'stopped','busy':False,'message':'Подключение перебанов остановлено.'})
                return False
            except Exception:
                if worker is not None and self._repunish is None:await worker.close()
                raise
            finally:
                self._repunish_starting=False;self._repunish_start_task=None

    def stop_repunish(self):
        async def operation():
            if self._repunish_start_task is not None:self._repunish_start_task.cancel()
            if self._relay_lock is None:return
            async with self._relay_lock:
                if self._repunish is not None:await self._repunish.stop()
                elif self._repunish_schedule:self._repunish_schedule.failed(False)
        return self._schedule_repunish(operation())

    def resolve_repunish_send(self, was_sent):
        async def operation():
            if self._repunish is not None:self._repunish.resolve_uncertain(was_sent)
        return self._schedule_repunish(operation())

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._main())
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            LOGGER.exception("Telegram worker crashed")
            self.emit("telegram_error", str(exc))
        finally:
            self.connected = False
            self.emit("telegram_status", "Отключено")

    async def _wait_auth_value(self, expected_kind: str) -> str:
        while not self._stop_requested.is_set():
            try:
                kind, value = await asyncio.to_thread(self._auth_input.get, True, 0.5)
            except queue.Empty:
                continue
            if kind == "cancel":
                raise asyncio.CancelledError
            if kind == expected_kind:
                return value.strip()
        raise asyncio.CancelledError

    @staticmethod
    def _chat_value(raw: str) -> str | int:
        value = raw.strip()
        if value.lstrip("-").isdigit():
            return int(value)
        return value

    async def _authorize_qr(self) -> None:
        assert self.client is not None
        await self.client.connect()
        if await self.client.is_user_authorized():
            return

        while not self._stop_requested.is_set():
            qr_login = await self.client.qr_login()
            self.emit("qr_login", {"url": qr_login.url})
            try:
                await qr_login.wait(timeout=55)
            except asyncio.TimeoutError:
                self.emit("telegram_notice", "QR-код истёк — показываю новый")
                continue
            except SessionPasswordNeededError:
                for attempt in range(1, 4):
                    self.emit("login_password_needed", {"attempt": attempt})
                    password = await self._wait_auth_value("password")
                    if not password:
                        raise RuntimeError("Вход отменён: пароль двухэтапной защиты не введён")
                    try:
                        await self.client.sign_in(password=password)
                        break
                    except PasswordHashInvalidError:
                        self.emit("telegram_notice", "Неверный пароль двухэтапной защиты")
                else:
                    raise RuntimeError("Не удалось подтвердить пароль двухэтапной защиты")
            if await self.client.is_user_authorized():
                self.emit("qr_authorized", None)
                return
        raise asyncio.CancelledError

    async def _main(self) -> None:
        self.loop = asyncio.get_running_loop()
        self._send_lock = asyncio.Lock()
        self._relay_lock = asyncio.Lock()
        try:
            api_id = int(str(self.config.get("api_id", "")).strip())
        except ValueError as exc:
            raise RuntimeError("API ID должен состоять только из цифр") from exc
        api_hash = str(self.config.get("api_hash", "")).strip()
        if not api_id or not api_hash:
            raise RuntimeError("Не заполнены API ID или API Hash")

        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        self.client = TelegramClient(str(self.session_path), api_id, api_hash)
        await self._authorize_qr()
        if not await self.client.is_user_authorized():
            raise RuntimeError("Telegram не подтвердил авторизацию")

        source_value = self._chat_value(str(self.config.get("source_chat", "")))
        response_value = self._chat_value(str(self.config.get("response_chat", "")))
        if not source_value or not response_value:
            raise RuntimeError("Не указан чат с тикетами или чат для ответов")

        source_entity = await self.client.get_input_entity(source_value)
        response_entity = await self.client.get_input_entity(response_value)

        @self.client.on(events.NewMessage(chats=source_entity, incoming=True))
        async def incoming_handler(event: Any) -> None:
            try:
                if self._paused.is_set():
                    return
                text = event.raw_text or ""
                if text.strip():
                    LOGGER.info("Incoming ticket message: %s", " ".join(text.split())[:1000])
                    self.emit("telegram_ticket", text)
            except Exception as exc:
                LOGGER.exception("Incoming handler failed")
                self.emit("telegram_error", f"Ошибка чтения тикета: {exc}")

        @self.client.on(events.NewMessage(chats=response_entity, outgoing=True))
        async def outgoing_handler(event: Any) -> None:
            try:
                text = (event.raw_text or "").strip()
                now = time.monotonic()
                while self._program_texts and self._program_texts[0][1] < now:
                    self._program_texts.popleft()
                for index, (saved_text, _) in enumerate(self._program_texts):
                    if saved_text == text:
                        del self._program_texts[index]
                        return
                # Manual ownership applies even when learning or monitoring is paused.
                manual = re.match(r"^\s*t\s+[ac]\s+(\d{3,12})(?:\s|$)", text, re.I)
                if manual:
                    self.knowledge_base.reply_guard.manual_takeover(source_key(self.config), manual.group(1))
                if self._paused.is_set():
                    return
                if not bool(self.config.get("learning_enabled", True)):
                    return
                learned = self.knowledge_base.learn_from_manual(
                    text,
                    learning_exclusions=(
                        list(self.config.get("learning_exclusions", []) or [])
                        + list(self.config.get("global_close_phrases", []) or [])
                    ),
                    only_corrections=bool(self.config.get("learning_only_corrections", True)),
                    filter_noise=bool(self.config.get("learning_noise_filter", True)),
                    learning_policy=str(self.config.get("learning_policy", "unknown_or_corrections") or "unknown_or_corrections"),
                )
                if learned:
                    LOGGER.info(
                        "Manual answer observed ticket=%s skipped=%s reason=%s",
                        learned.get("ticket_id", ""),
                        learned.get("skipped", False),
                        learned.get("reason", ""),
                    )
                    self.emit("learned", learned)
                elif re.match(r"^\s*t\s+a\s+\d{3,12}\s+", text, re.IGNORECASE) and text.endswith("@@@"):
                    self.emit("telegram_notice", "Ответ отправлен, но обучение не сохранено: программа не видела исходный тикет с этим ID")
            except Exception as exc:
                LOGGER.exception("Outgoing learning handler failed")
                self.emit("telegram_error", f"Ошибка автоматического обучения: {exc}")

        self.connected = True
        self.emit("telegram_status", "Telegram подключён")
        try:
            await self._apply_command_relay()
            try:await self._apply_repunish_settings()
            except Exception as exc:
                LOGGER.exception('Repunish scheduler setup failed')
                self._emit_repunish({'state':'error','busy':False,'message':f'Не удалось подключить расписание перебанов: {type(exc).__name__}. Ручной запуск доступен.'})
            await self.client.run_until_disconnected()
        finally:
            self.connected = False
            if self._removals_start_task is not None:
                self._removals_start_task.cancel()
            if self._repunish_start_task is not None:self._repunish_start_task.cancel()
            if self._repunish_schedule is not None:
                await self._repunish_schedule.close();self._repunish_schedule=None
            async with self._relay_lock:
                if self._repunish is not None:
                    await self._repunish.close();self._repunish=None
                self._emit_repunish({'state':'offline','busy':False,'message':'Telegram отключён. Прогресс перебанов сохранён.'})
                if self._removals is not None:
                    await self._removals.close()
                    self._removals = None
                self._emit_removals({"state": "offline", "busy": False, "message": "Telegram отключён. Прогресс снятий сохранён."})
                if self._command_relay is not None:
                    await self._command_relay.close()
                    self._command_relay = None
            self._emit_relay({"state": "offline", "message": "Telegram отключён"})

    def _submit(self, coroutine: Any, action_name: str, ticket_id: str = "", context=None) -> None:
        if self._paused.is_set():
            try:
                coroutine.close()
            except Exception:
                pass
            self.emit("telegram_action_skipped", {"action": action_name, "ticket_id": ticket_id, "reason": "paused"})
            return
        if not self.loop or not self.client or not self.connected:
            try:
                coroutine.close()
            except Exception:
                pass
            self.emit("telegram_error", "Telegram ещё не подключён")
            return
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)

        def complete(done: Any) -> None:
            try:
                completed = done.result()
                if isinstance(completed, dict) and completed.get('reason'):
                    self.emit("reply_handoff", {"context": context, "reason": completed['reason']})
                    return
                if completed is False:
                    self.emit("telegram_action_skipped", {"action": action_name, "ticket_id": ticket_id, "reason": "paused"})
                    return
                self.emit("telegram_action_done", {"action": action_name, "ticket_id": ticket_id, "context": context})
            except Exception as exc:
                LOGGER.exception("Telegram action failed: %s", action_name)
                self.emit("telegram_error", f"Не удалось выполнить действие «{action_name}»: {exc}")

        future.add_done_callback(complete)

    def save_draft(self, text: str, ticket_id: str, context=None) -> None:
        async def operation() -> bool:
            if self._paused.is_set():
                return False
            assert self.client is not None
            peer = await self.client.get_input_entity(self._chat_value(str(self.config["response_chat"])))
            if self._paused.is_set():
                return False
            await self.client(functions.messages.SaveDraftRequest(peer=peer, message=text, no_webpage=True))
            LOGGER.info("Draft saved ticket=%s", ticket_id)
            return True

        self._submit(operation(), "draft", ticket_id, context)

    def send_message(self, text: str, ticket_id: str, context=None) -> None:
        async def operation():
            if not context:
                return {'reason': 'Нет контекста обращения для проверки повторных ответов'}
            if self._send_lock is None:
                self._send_lock = asyncio.Lock()
            async with self._send_lock:
                if self._paused.is_set():
                    return False
                assert self.client is not None
                limit = max(1, int(self.config.get("auto_limit_per_minute", 6)))
                delay = max(0.0, float(self.config.get("auto_delay_seconds", 1.5)))
                now = time.monotonic()
                while self._auto_timestamps and now - self._auto_timestamps[0] >= 60.0:
                    self._auto_timestamps.popleft()
                if len(self._auto_timestamps) >= limit:
                    wait_for = max(0.5, 60.0 - (now - self._auto_timestamps[0]))
                    self.emit("telegram_notice", f"Ограничение автоответов: ожидание {int(wait_for)} сек.")
                    await asyncio.sleep(wait_for)
                if delay:
                    await asyncio.sleep(delay)
                if self._paused.is_set() or self._stop_requested.is_set():
                    return False
                if context['source'] != source_key(self.config):
                    return {'reason': 'Чаты изменились после постановки ответа в очередь'}
                template = self.knowledge_base.get_template(context['template_id']) if context.get('template_id') else None
                if context.get('template_id') and (not template or not template['enabled']):
                    return {'reason': 'Шаблон выключен или удалён после постановки в очередь'}
                if template and template_key(template['answer_template']) != context['template_key']:
                    return {'reason': 'Текст шаблона изменился — проверь ответ вручную'}
                enabled = bool(template.get('reply_limit_enabled', True)) if template else True
                guard = self.knowledge_base.reply_guard
                token, reason = guard.reserve(context, context['template_key'], enabled)
                if not token:
                    return {'reason': reason}
                # Mark before send so the outgoing event cannot teach our own reply.
                marker = (text.strip(), time.monotonic() + 300.0)
                self._program_texts.append(marker)
                try:
                    await self.client.send_message(self._chat_value(str(self.config["response_chat"])), text, link_preview=False)
                except RPCError as exc:
                    guard.finish(token, 'uncertain' if getattr(exc, 'code', 500) >= 500 else 'failed')
                    if marker in self._program_texts:
                        self._program_texts.remove(marker)
                    raise
                except (ValueError, TypeError):
                    guard.finish(token, 'failed')
                    if marker in self._program_texts:
                        self._program_texts.remove(marker)
                    raise
                except BaseException:
                    # Transport loss may happen after Telegram accepted the message.
                    guard.finish(token, 'uncertain')
                    self.emit('telegram_notice', f'Тикет #{ticket_id}: результат отправки неизвестен, проверь Telegram; повтор заблокирован')
                    raise
                guard.finish(token, 'sent')
                LOGGER.info("Auto reply sent ticket=%s", ticket_id)
                self._auto_timestamps.append(time.monotonic())
                try:
                    peer = await self.client.get_input_entity(self._chat_value(str(self.config["response_chat"])))
                    await self.client(functions.messages.SaveDraftRequest(peer=peer, message="", no_webpage=True))
                except Exception:
                    LOGGER.debug("Unable to clear response draft", exc_info=True)
                return True

        self._submit(operation(), "auto", ticket_id, context)

    def close_ticket(self, ticket_id: str) -> None:
        """Close a ticket by sending the AresMine command ``t c <ticket_id>``.

        This is intentionally fixed: no inline buttons and no configurable command template.
        """
        async def operation() -> bool:
            if self._paused.is_set():
                return False
            assert self.client is not None
            peer = await self.client.get_input_entity(self._chat_value(str(self.config["response_chat"])))
            clean_id = str(ticket_id or "").strip().lstrip("#")
            if not clean_id or not clean_id.isdigit():
                raise RuntimeError(f"Некорректный номер тикета для закрытия: {ticket_id!r}")
            command = f"t c {clean_id}"
            # Mark as program-generated before send so outgoing monitoring never treats it as training.
            self._program_texts.append((command, time.monotonic() + 30.0))
            await self.client.send_message(peer, command, link_preview=False)
            LOGGER.info("Ticket close command sent ticket=%s command=%r", clean_id, command)
            return True

        self._submit(operation(), "close", str(ticket_id))
