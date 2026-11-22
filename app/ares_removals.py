"""Очередь снятий через текущую пользовательскую сессию Telethon.

Сеть работает только в asyncio-потоке TelegramService. Здесь нет Tk и нет
второго входа в Telegram. Подтверждение отправки означает доставку команды
в Telegram, а не проверку изменения группы на игровом сервере.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import re
import time
from typing import Any, Callable

from telethon import events
from telethon.errors import RPCError

LOGGER = logging.getLogger(__name__)
STAFF = "@AresStaffRobot"
MINE = "@AresMineRobot"
HEADER = re.compile(r"Список\s+на\s+снятие\s*\(\s*(\d+)\s*\)\s*:", re.I)
EMPTY = re.compile(r"^\s*(?:⚡\ufe0f?\s*)?Список\s+на\s+снятие\s+пуст\s*!?\s*$", re.I)
COMMAND = re.compile(r"r grief pex user ([A-Za-z0-9_]{1,16}) group set default")
ROW = re.compile(r"^\s*[➤►•]?\s*([1-9]\d{0,15})\.\s+(r grief pex user [A-Za-z0-9_]{1,16} group set default)\s*$")
FOOTER = re.compile(r"^\s*[➤►•]?\s*/p\s+reset((?:\s+[1-9]\d{0,15})+)\s*$", re.I)
PROGRESS = re.compile(r"^\s*(?:⚡\ufe0f?\s*)?Запрос\s+выполняется[!.…\s]*$", re.I)
ERROR = re.compile(r"(?:ошибк|нет\s+(?:прав|доступа)|недостаточно\s+прав|не\s+(?:найден|удалось)|доступ\s+запрещ|слишком\s+часто)", re.I)


class RemovalsError(RuntimeError):
    """Понятная пользователю ошибка; запуск после неё снова доступен."""


@dataclass(frozen=True)
class Removal:
    id: str
    command: str

    @property
    def player(self) -> str:
        return COMMAND.fullmatch(self.command)[1]


@dataclass(frozen=True)
class Batch:
    rows: tuple[Removal, ...] = ()
    empty: bool = False


def parse_batch(text: str) -> Batch | None:
    """None — ещё не весь список. Чужой текст не становится командой.

    Используем ID из строк списка, а не исполняем произвольный текст или
    нижнюю подсказку бота. Проверяем все строки до первого снятия.
    """
    if EMPTY.fullmatch(text):
        return Batch(empty=True)
    header = HEADER.search(text)
    if header is None:
        return None
    count = int(header[1])
    if not 0 < count <= 10000:
        raise RemovalsError("Некорректное количество снятий в ответе бота.")
    if len(HEADER.findall(text)) != 1:
        raise RemovalsError("Пришло несколько списков одновременно. Запусти снятия повторно.")
    rows: list[Removal] = []
    footer_ids = None
    for line in text[header.end():].splitlines():
        if not line.strip():
            continue
        row, footer = ROW.fullmatch(line), FOOTER.fullmatch(line)
        if row:
            rows.append(Removal(row[1], row[2]))
        elif footer:
            if footer_ids is not None:
                raise RemovalsError("В списке обнаружено несколько строк подтверждения.")
            footer_ids = footer[1].split()
        else:
            # Конец последнего сообщения может быть обрезан посередине строки.
            # Полный неподдерживаемый текст не исполняется даже частично.
            return None
    if len(rows) < count:
        return None
    if len(rows) != count or len({row.id for row in rows}) != count:
        raise RemovalsError("Количество строк или ID в списке снятий не совпадает.")
    # StaffRobot может перечислить те же ID в другом порядке.
    # Порядок подсказки не определяет порядок команд; проверяем состав.
    if footer_ids is not None and (len(footer_ids) != len(rows) or set(footer_ids) != {row.id for row in rows}):
        raise RemovalsError("ID в списке и в подтверждении бота различаются. Ничего не отправлено.")
    return Batch(tuple(rows))


def join_parts(parts: list[str]) -> str:
    """Сохраняет разрывы строк, но умеет склеить разрезанную команду."""
    result = ""
    for part in parts:
        if result and not result.endswith("\n") and not part.startswith("\n"):
            if re.match(r"\s*(?:[➤►•]|\d+\.\s+r\s+grief|Список\s+на\s+снятие|/p\s+reset)", part, re.I):
                result += "\n"
        result += part
    return result


@dataclass
class _Reply:
    sent_id: int | None = None
    messages: dict[int, Any] = field(default_factory=dict)
    progress: set[int] = field(default_factory=set)
    early: list[tuple[Any, bool]] = field(default_factory=list)
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    last_change: float = 0
    after_id: int = 0


class AresRemovals:
    def __init__(self, client, state_directory: Path, *, status: Callable[[dict], None] | None = None,
                 enabled: Callable[[], bool] | None = None, interval_seconds=5,
                 response_timeout=60, send_timeout=15, idle_seconds=1,
                 poll_seconds=2, confirmation_retries=2):
        if min(interval_seconds, response_timeout, send_timeout, idle_seconds, poll_seconds) <= 0:
            raise ValueError("Интервалы должны быть больше нуля")
        self.client, self.state_directory = client, Path(state_directory)
        self.status, self.enabled = status, enabled or (lambda: True)
        self.interval = float(interval_seconds)
        self.response_timeout, self.send_timeout = float(response_timeout), float(send_timeout)
        self.idle, self.poll = float(idle_seconds), float(poll_seconds)
        self.confirmation_retries = confirmation_retries
        self.staff = self.mine = None
        self.state_path: Path | None = None
        self._confirmed: dict[str, str] = {}
        self._uncertain: Removal | None = None
        self._last_sent_at = 0.0
        self._next_send_at = 0.0
        self._reply: _Reply | None = None
        self._task: asyncio.Task | None = None
        self._running = False
        self._installed = False
        self._closed = False
        self._seen_ids: set[str] = set()
        self._sent_ids: set[str] = set()
        self._batch = Batch()
        self._batch_number = 0
        self._new_handler = self._on_staff
        self._edit_handler = self._on_edit
        self._last_state, self._last_message = "ready", "Готово к запуску"

    @property
    def busy(self):
        return self._running

    @property
    def uncertain(self):
        return self._uncertain

    def publish_status(self):
        self._report(self._last_state, self._last_message)

    def _report(self, state, message, *, log=False, level="info"):
        self._last_state, self._last_message = state, message
        completed = sum(row.id in self._confirmed for row in self._batch.rows)
        info = {"state": state, "message": message, "busy": self.busy,
                "sent_count": len(self._sent_ids), "batch_number": self._batch_number,
                "batch_total": len(self._batch.rows), "batch_sent": completed,
                "remaining": len(self._batch.rows) - completed,
                "uncertain": None if not self.uncertain else {"id": self.uncertain.id, "command": self.uncertain.command}}
        if log:
            info["log"] = {"time": time.strftime("%H:%M:%S"), "message": message, "level": level}
        if self.status:
            try:
                self.status(info)
            except Exception:
                LOGGER.exception("Unable to publish removals status")

    async def install(self):
        if self._installed:
            return self
        me = await asyncio.wait_for(self.client.get_me(), self.send_timeout)
        if me is None or getattr(me, "bot", False):
            raise RemovalsError("Нужна пользовательская сессия Telegram.")
        self.state_path = self.state_directory / f"removals_{me.id}.json"
        self._load()
        self.staff = await asyncio.wait_for(self.client.get_entity(STAFF), self.send_timeout)
        self.mine = await asyncio.wait_for(self.client.get_entity(MINE), self.send_timeout)
        if not self.staff.bot or not self.mine.bot or self.staff.id == self.mine.id:
            raise RemovalsError("Не удалось определить двух разных ботов снятий.")
        try:
            self.client.add_event_handler(self._new_handler, events.NewMessage(incoming=True, chats=self.staff.id, from_users=self.staff.id))
            self.client.add_event_handler(self._edit_handler, events.MessageEdited(incoming=True, chats=self.staff.id, from_users=self.staff.id))
        except BaseException:
            self.client.remove_event_handler(self._new_handler)
            self.client.remove_event_handler(self._edit_handler)
            raise
        self._installed = True
        self.publish_status()
        return self

    def _load(self):
        if not self.state_path.exists():
            return
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            if state.get("version") != 1 or not isinstance(state.get("confirmed"), dict):
                raise ValueError("Unknown checkpoint")
            for key, command in state["confirmed"].items():
                if not re.fullmatch(r"[1-9]\d{0,15}", key) or not isinstance(command, str) or not COMMAND.fullmatch(command):
                    raise ValueError("Invalid command")
            self._confirmed = dict(state["confirmed"])
            uncertain = state.get("uncertain")
            if uncertain is not None:
                row = Removal(str(uncertain["id"]), uncertain["command"])
                if not ROW.fullmatch(f"{row.id}. {row.command}"):
                    raise ValueError("Invalid uncertain command")
                self._uncertain = row
            self._last_sent_at = float(state.get("last_sent_at", 0))
        except Exception as exc:
            raise RemovalsError("Не удалось прочитать сохранённый прогресс снятий. Проверь файл " + self.state_path.name) from exc

    def _save(self):
        self.state_directory.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        state = {"version": 1, "confirmed": self._confirmed, "last_sent_at": self._last_sent_at,
                 "uncertain": None if not self.uncertain else {"id": self.uncertain.id, "command": self.uncertain.command}}
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.state_path)

    def start(self) -> bool:
        if self.busy:
            return False
        if not self._installed or self._closed:
            raise RemovalsError("Обработчик снятий не подключён.")
        if self.uncertain:
            self._report("review", f"Нужно проверить отправку ID {self.uncertain.id}. Нажми «Проверить отправку».", log=True, level="error")
            return False
        self._seen_ids.clear()
        self._sent_ids.clear()
        self._batch, self._batch_number = Batch(), 0
        # Во время работы используем монотонные часы. Между перезапусками
        # восстанавливаем лишь оставшуюся часть пятисекундного интервала.
        delay = max(0, min(self.interval, self.interval - (time.time() - self._last_sent_at)))
        self._next_send_at = asyncio.get_running_loop().time() + delay
        self._running = True
        self._report("requesting", "Получаю список снятий…", log=True)
        self._task = asyncio.create_task(self._run(), name="ares-removals")
        return True

    async def stop(self):
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            finally:
                # Если task отменён до первого шага, его _run/finally не
                # выполняется. Освобождаем кнопки и резерв бота здесь.
                if self._running:
                    self._reply = None
                    self._running = False
                    self._report("stopped", "Выполнение остановлено. Отправленные ID сохранены.", log=True)

    async def close(self):
        self._closed = True
        self.client.remove_event_handler(self._new_handler)
        self.client.remove_event_handler(self._edit_handler)
        await self.stop()
        self._installed = False

    def resolve_uncertain(self, was_sent: bool):
        """Только после проверки владельцем диалога MineRobot; без автоповтора."""
        if self.busy or not self.uncertain:
            raise RemovalsError("Сейчас нет отправки для проверки.")
        row = self.uncertain
        if was_sent:
            self._confirmed[row.id] = row.command
            self._last_sent_at = time.time()
        else:
            self._confirmed.pop(row.id, None)
        self._uncertain = None
        try:
            self._save()
        except Exception:
            self._uncertain = row
            raise
        self._report("ready", "Проверка сохранена. Нажми «Выполнить снятия» для продолжения.", log=True)

    def _check_enabled(self):
        if self._closed or not self.enabled():
            raise asyncio.CancelledError

    async def _run(self):
        state, message, level = "done", "Список на снятие пуст. Работа завершена.", "success"
        acknowledged: set[str] = set()
        try:
            while True:
                self._check_enabled()
                batch = await self._query()
                # Подтверждённые ID не отправляем повторно, даже если бот не
                # обработал /p reset <ids>. Две повторные проверки ограничены.
                for attempt in range(self.confirmation_retries + 1):
                    if not (acknowledged & {row.id for row in batch.rows}):
                        break
                    if attempt == self.confirmation_retries:
                        raise RemovalsError("Бот ещё показывает подтверждённые ID. Работа остановлена без повторных команд; можно запустить снова.")
                    self._report("confirming", "Проверяю, обработал ли StaffRobot подтверждение…", log=True)
                    await asyncio.sleep(self.poll)
                    batch = await self._query()
                if batch.empty:
                    self._save()
                    message = f"Список на снятие пуст! Отправлено команд: {len(self._sent_ids)}."
                    break
                self._batch = batch
                self._batch_number += 1
                self._seen_ids.update(row.id for row in batch.rows)
                self._report("sending", f"Список {self._batch_number} · {len(batch.rows)} снятий", log=True)
                for row in batch.rows:
                    self._check_enabled()
                    if row.id in self._confirmed:
                        if self._confirmed[row.id] != row.command:
                            raise RemovalsError(f"ID {row.id} теперь содержит другую команду. Проверь список бота.")
                        self._sent_ids.add(row.id)
                        self._report("sending", f"ID {row.id} · уже отправлен, повтор пропущен", log=True)
                        continue
                    delay = max(0, self._next_send_at - asyncio.get_running_loop().time())
                    if delay:
                        self._report("waiting", f"Следующее снятие через {delay:.1f} сек. · {row.player}")
                        await asyncio.sleep(delay)
                    self._check_enabled()
                    self._uncertain = row
                    self._save()  # Запись намерения ДО сети защищает при аварии.
                    self._report("sending", f"Отправляю ID {row.id} · {row.player}")
                    try:
                        await asyncio.wait_for(self.client.send_message(self.mine, row.command, parse_mode=None, link_preview=False), self.send_timeout)
                    except RPCError as exc:
                        # Явный отказ сервера (400/403/420) не является доставкой.
                        if getattr(exc, "code", 0) in (400, 403, 420):
                            self._uncertain = None
                            self._save()
                        raise
                    self._confirmed[row.id] = row.command
                    self._last_sent_at = time.time()
                    self._next_send_at = asyncio.get_running_loop().time() + self.interval
                    self._uncertain = None
                    try:
                        self._save()
                    except Exception:
                        self._uncertain = row
                        raise
                    self._sent_ids.add(row.id)
                    self._report("sending", f"ID {row.id} · {row.player} · команда отправлена", log=True, level="success")
                self._check_enabled()
                ids = [row.id for row in batch.rows if row.id in self._confirmed]
                # В большой партии подтверждение тоже делим по лимиту Telegram.
                for command in self._ack_commands(ids):
                    self._check_enabled()
                    self._report("confirming", "Подтверждаю в StaffRobot: " + command, log=True)
                    await asyncio.wait_for(self.client.send_message(self.staff, command, parse_mode=None, link_preview=False), self.send_timeout)
                acknowledged.update(ids)
        except asyncio.CancelledError:
            state, message, level = "stopped", "Снятия остановлены. Отправленные команды сохранены; запуск продолжит работу.", "info"
        except Exception as exc:
            LOGGER.warning("Removals stopped: %s", type(exc).__name__)
            state, level = "error", "error"
            message = str(exc) if isinstance(exc, RemovalsError) else f"Снятия остановлены: {type(exc).__name__}. Проверь подключение и повтори запуск."
        finally:
            self._reply = None
            self._running = False
            if self.uncertain:
                state, level = "review", "error"
                message = f"Нет подтверждения отправки ID {self.uncertain.id}. Проверь @AresMineRobot и нажми «Проверить отправку»."
            self._report(state, message, log=True, level=level)

    @staticmethod
    def _ack_commands(ids):
        command = "/p reset"
        for identifier in ids:
            if len(command) + len(identifier) + 1 > 3900:
                yield command
                command = "/p reset"
            command += " " + identifier
        if command != "/p reset":
            yield command

    async def _on_edit(self, event):
        await self._on_staff(event, edited=True)

    async def _on_staff(self, event, *, edited=False):
        pending = self._reply
        if pending is None or self._closed or event.out or not event.is_private:
            return
        if event.sender_id != self.staff.id or event.chat_id != self.staff.id:
            return
        if pending.sent_id is None:
            if len(pending.early) < 512:
                pending.early.append((event.message, edited))
        else:
            self._accept(pending, event.message, edited)

    def _accept(self, pending, message, edited=False):
        if message.id <= pending.sent_id or getattr(message, "out", False):
            return
        reply_id = getattr(message, "reply_to_msg_id", None)
        if reply_id and reply_id not in {pending.sent_id, *pending.progress, *pending.messages}:
            return
        if edited and message.id not in pending.progress and message.id not in pending.messages:
            return
        text = message.raw_text or ""
        if PROGRESS.fullmatch(text):
            pending.progress.add(message.id)
            return
        # Ответ /c list или квитанция предыдущего /p reset <ids> не являются
        # новым списком. Допускаем продолжения уже начатого списка.
        if not (HEADER.search(text) or EMPTY.fullmatch(text) or re.match(r"\s*(?:[➤►•]|/p\s+reset)", text) or pending.messages or pending.progress or ERROR.search(text)):
            return
        if not (HEADER.search(text) or EMPTY.fullmatch(text) or re.match(r"\s*(?:[➤►•]|/p\s+reset)", text) or ERROR.search(text)):
            assembled = join_parts([pending.messages[mid].raw_text or "" for mid in sorted(pending.messages)])
            try:
                complete = parse_batch(assembled) is not None
            except RemovalsError:
                return  # Ошибку формата обработает _query, а не event handler.
            if not HEADER.search(assembled) or complete:
                return  # Квитанция старого подтверждения не портит новый ответ.
        old = pending.messages.get(message.id)
        if old is not None and old.raw_text == text:
            return
        pending.messages[message.id] = message
        pending.last_change = asyncio.get_running_loop().time()
        pending.changed.set()

    async def _poll_history(self, pending, budget):
        if not hasattr(self.client, "get_messages") or budget <= 0:
            return
        try:
            messages = await asyncio.wait_for(self.client.get_messages(self.staff, limit=100, min_id=max(pending.sent_id, pending.after_id), reverse=True), min(3, budget))
            for message in messages:
                pending.after_id = max(pending.after_id, message.id)
                if getattr(message, "sender_id", None) == self.staff.id:
                    self._accept(pending, message)
            if pending.progress:
                updates = await asyncio.wait_for(self.client.get_messages(self.staff, ids=sorted(pending.progress)), min(3, budget))
                for message in updates:
                    if message is not None and getattr(message, "sender_id", None) == self.staff.id:
                        self._accept(pending, message, True)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.debug("Unable to read removals history", exc_info=True)

    async def _query(self) -> Batch:
        self._report("requesting", "Запрашиваю /p reset у @AresStaffRobot…", log=True)
        pending = self._reply = _Reply()
        try:
            sent = await asyncio.wait_for(self.client.send_message(self.staff, "/p reset", parse_mode=None, link_preview=False), self.send_timeout)
            pending.sent_id = sent.id
            for message, edited in pending.early:
                self._accept(pending, message, edited)
            pending.early.clear()
            loop = asyncio.get_running_loop()
            deadline, next_poll = loop.time() + self.response_timeout, loop.time() + self.poll
            while True:
                self._check_enabled()
                text = join_parts([pending.messages[mid].raw_text or "" for mid in sorted(pending.messages)])
                batch = parse_batch(text)
                quiet = loop.time() - pending.last_change
                if pending.messages and quiet >= self.idle:
                    if batch is not None:
                        return batch
                    # Не смешиваем явно отказавший бот с неполным списком.
                    if ERROR.search(text):
                        raise RemovalsError("StaffRobot: " + text[:600])
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise RemovalsError(f"StaffRobot не прислал полный список за {self.response_timeout:g} секунд. Ничего из неполного списка не отправлено; повтори запуск.")
                if loop.time() >= next_poll:
                    await self._poll_history(pending, remaining)
                    next_poll = loop.time() + self.poll
                    continue
                pending.changed.clear()
                wait = min(remaining, max(.001, next_poll - loop.time()))
                if batch is not None:
                    wait = min(wait, max(.001, self.idle - quiet))
                try:
                    await asyncio.wait_for(pending.changed.wait(), wait)
                except asyncio.TimeoutError:
                    pass
        finally:
            self._reply = None
