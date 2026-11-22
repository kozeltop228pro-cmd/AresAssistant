"""Перебаны из приложенного SIMPLE-проекта через существующую сессию Ares.

Решение принимает только номер причины. Ответы MineRobot не проверяются,
baninfo не вызывается. Отправлено означает доставлено в Telegram.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import json
import logging
import os
from pathlib import Path
import re
import time

from telethon import events
from telethon.errors import FloodWaitError, RPCError
from ares_removals import AresRemovals, RemovalsError, _Reply, STAFF, MINE, PROGRESS, ERROR

LOGGER = logging.getLogger(__name__)
ALLOWED_REASONS = frozenset(('1.1','1.6','1.8','1.9','1.10','1.11','2.7'))
HEADER = re.compile(r'(?:список\s+на\s+перебан|неоформленн(?:ые|ых)\s+наказан(?:ия|ий))\s*\((\d+)\)\s*:',re.I)
EMPTY = re.compile(r'^\s*(?:⚡\ufe0f?\s*)?(?:список\s+на\s+перебан\s+пуст!?|(?:ваши\s+)?неоформленн(?:ые|ых)\s+наказан(?:ия|ий)\s*\(0\):?)\s*$',re.I)
ROW_ID = r'([1-9]\d{0,15})'
LEGACY = re.compile(r'^\s*[➤►•]?\s*'+ROW_ID+r'\.\s*(r\s+grief\s+ban\s+([A-Za-z0-9_]{1,32})\s+(\d+\.\d+)\b[^\r\n]*)\s*$',re.I)
RAW = re.compile(r'^\s*[➤►•]?\s*'+ROW_ID+r'\.\s*(?:(?:\d{1,2}\.\d{1,2}\.\d{2,4})\s+\d{1,2}:\d{2}\s*-\s*)?(BAN|MUTE)\s*-\s*([A-Za-z0-9_]{1,32})\s*-\s*(\d+\.\d+)\b(?:\s*-\s*[^\r\n]*)?\s*$',re.I)
FOOTER = re.compile(r'^\s*[➤►•]?\s*/p\s+repunish((?:\s+[1-9]\d{0,15})+)\s*$',re.I)
ACK = re.compile(r'убрано\s+из\s+списка\s+на\s+перебан|не\s+находились\s+или\s+не\s+найдены|список\s+на\s+перебан\s+пуст',re.I)


class RepunishError(RemovalsError):
    pass


class RepunishTimeout(RepunishError):
    """Временное молчание бота: сохранённая пачка допускает повтор ожидания."""
    pass


@dataclass(frozen=True)
class RepunishItem:
    id: str
    command: str
    player: str
    reason: str

    @property
    def allowed(self):
        return self.reason in ALLOWED_REASONS


@dataclass(frozen=True)
class RepunishBatch:
    rows: tuple[RepunishItem,...] = ()
    empty: bool = False


def parse_repunish(text):
    """Не отправляем команды из неполной или неподдерживаемой пачки."""
    if EMPTY.fullmatch(text):return RepunishBatch(empty=True)
    header=HEADER.search(text)
    if header is None:return None
    count=int(header[1])
    if len(HEADER.findall(text))!=1 or not 0<=count<=10000:
        raise RepunishError('Некорректный заголовок списка перебанов.')
    rows=[];footer_ids=[]
    for line in text[header.end():].splitlines():
        if not line.strip():continue
        legacy,raw,footer=LEGACY.fullmatch(line),RAW.fullmatch(line),FOOTER.fullmatch(line)
        if legacy:
            rows.append(RepunishItem(legacy[1],legacy[2].strip(),legacy[3],legacy[4]))
        elif raw:
            # Это поведение исходного SIMPLE-проекта для форматов BAN/MUTE.
            rows.append(RepunishItem(raw[1],f'r grief ban {raw[3]} {raw[4]}',raw[3],raw[4]))
        elif footer:
            footer_ids.extend(footer[1].split())
        else:
            return None
    if len(rows)<count:return None
    if len(rows)!=count or len({r.id for r in rows})!=count:
        raise RepunishError('Количество строк или уникальных ID перебанов не совпадает.')
    if footer_ids and (len(footer_ids)!=len(rows) or set(footer_ids)!={r.id for r in rows}):
        raise RepunishError('ID в строках и в подтверждении бота различаются.')
    return RepunishBatch(tuple(rows),empty=count==0)


def join_parts(parts):
    result=''
    for part in parts:
        if result and not result.endswith('\n') and not part.startswith('\n'):
            if re.match(r'\s*(?:[➤►•]|(?:Ваши\s+)?неоформленн|список\s+на\s+перебан|/p\s+repunish)',part,re.I):result+='\n'
        result+=part
    return result


def options_from_config(config):
    mode=str(config.get('repunish_schedule','manual'))
    if mode not in ('manual','5h','10h','daily'):raise ValueError('Выбери расписание перебанов.')
    delay=float(config.get('repunish_delay_seconds',2))
    if delay not in (1.5,2,3,4,5):raise ValueError('Задержка должна быть 1.5, 2, 3, 4 или 5 секунд.')
    daily=str(config.get('repunish_daily_time','12:00')).strip()
    if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',daily):raise ValueError('Укажи время в формате ЧЧ:ММ, например 12:00.')
    return {'mode':mode,'delay':delay,'daily':daily,'notify':bool(config.get('repunish_notify_start',True))}


def next_run(mode,daily,after=None):
    after=time.time() if after is None else after
    if mode=='manual':return 0
    if mode in ('5h','10h'):return after+int(mode[:-1])*3600
    hour,minute=map(int,daily.split(':'))
    current=datetime.fromtimestamp(after)
    candidate=current.replace(hour=hour,minute=minute,second=0,microsecond=0)
    if candidate.timestamp()<=after:candidate+=timedelta(days=1)
    return candidate.timestamp()


def atomic_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp')
    with temporary.open('w',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False)
        stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path)


class AresRepunish(AresRemovals):
    """Использует проверенные события/опрос истории снятий, но свой прогресс."""
    def __init__(self,*args,burst_size=20,burst_rest=20,batch_pause=2,**kwargs):
        kwargs.setdefault('interval_seconds',2)
        super().__init__(*args,**kwargs)
        self.burst_size,self.burst_rest,self.batch_pause=burst_size,burst_rest,batch_pause
        self._batch=RepunishBatch()
        self._handled=set();self._checkpoint_rows=()
        self._attempt_started=0;self._checkpoint_loaded=False
        self._skipped_ids=set();self._cycle_active=False

    async def install(self):
        if self._installed:return self
        me=await asyncio.wait_for(self.client.get_me(),self.send_timeout)
        if me is None or getattr(me,'bot',False):raise RepunishError('Нужна пользовательская сессия Telegram.')
        self.state_path=self.state_directory/f'repunish_{me.id}.json'
        self._load()
        self.staff=await asyncio.wait_for(self.client.get_entity(STAFF),self.send_timeout)
        self.mine=await asyncio.wait_for(self.client.get_entity(MINE),self.send_timeout)
        if not self.staff.bot or not self.mine.bot or self.staff.id==self.mine.id:
            raise RepunishError('Не удалось определить двух разных ботов перебанов.')
        try:
            self.client.add_event_handler(self._new_handler,events.NewMessage(incoming=True,chats=self.staff.id,from_users=self.staff.id))
            self.client.add_event_handler(self._edit_handler,events.MessageEdited(incoming=True,chats=self.staff.id,from_users=self.staff.id))
        except BaseException:
            self.client.remove_event_handler(self._new_handler);self.client.remove_event_handler(self._edit_handler)
            raise
        self._installed=True
        if self._checkpoint_rows or self._cycle_active:
            self._last_message='Незавершённая очередь сохранена. Нажми «Запустить перебаны» для продолжения.'
        self.publish_status()
        return self

    def _load(self):
        self._checkpoint_loaded=True
        if not self.state_path.exists():return
        try:
            data=json.loads(self.state_path.read_text(encoding='utf-8'))
            if data.get('version')!=1:raise ValueError('version')
            self._confirmed=dict(data.get('sent',{}))
            for rid,command in self._confirmed.items():
                row=LEGACY.fullmatch(f'{rid}. {command}')
                if row is None or row[4] not in ALLOWED_REASONS:raise ValueError('sent')
            self._handled=set(data.get('handled',[]))
            if any(not re.fullmatch(ROW_ID,rid) for rid in self._handled):raise ValueError('handled')
            rows=tuple(RepunishItem(**row) for row in data.get('batch',[]))
            for row in rows:
                match=LEGACY.fullmatch(f'{row.id}. {row.command}')
                if match is None or match[3]!=row.player or match[4]!=row.reason:raise ValueError('batch')
            if len({r.id for r in rows})!=len(rows):raise ValueError('duplicate')
            self._checkpoint_rows=rows
            raw=data.get('uncertain')
            if raw:
                row=RepunishItem(**raw)
                match=LEGACY.fullmatch(f'{row.id}. {row.command}')
                if match is None or match[3]!=row.player or match[4]!=row.reason or not row.allowed:raise ValueError('uncertain')
                self._uncertain=row
            self._attempt_started=float(data.get('attempt_started',0))
            self._last_sent_at=float(data.get('last_sent_at',0))
            self._cycle_active=bool(data.get('cycle_active',False))
        except Exception as exc:
            raise RepunishError('Не удалось прочитать прогресс перебанов: '+self.state_path.name) from exc

    def _save(self):
        atomic_json(self.state_path,{'version':1,'sent':self._confirmed,'handled':sorted(self._handled),
            'batch':[asdict(r) for r in self._checkpoint_rows],
            'uncertain':None if self._uncertain is None else asdict(self._uncertain),
            'attempt_started':self._attempt_started,'last_sent_at':self._last_sent_at,'cycle_active':self._cycle_active})

    def _report(self,state,message,*,log=False,level='info',**extra):
        self._last_state,self._last_message=state,message
        done=sum(r.id in self._confirmed or not r.allowed or r.id in self._handled for r in self._batch.rows)
        info={'state':state,'message':message,'busy':self.busy,'sent_count':len(self._sent_ids),
            'skipped_count':len(self._skipped_ids),'batch_number':self._batch_number,
            'batch_total':len(self._batch.rows),'batch_sent':done,'remaining':len(self._batch.rows)-done,
            'uncertain':None if self._uncertain is None else asdict(self._uncertain),**extra}
        if log:info['log']={'time':time.strftime('%H:%M:%S'),'message':message,'level':level}
        if self.status:
            try:self.status(info)
            except Exception:LOGGER.exception('Unable to publish repunish status')

    def start(self):
        if self.busy:return False
        if not self._installed or self._closed:raise RepunishError('Обработчик перебанов не подключён.')
        self._sent_ids.clear();self._skipped_ids.clear();self._batch_number=0
        self._cycle_active=True;self._save()
        delay=max(0,min(self.interval,self.interval-(time.time()-self._last_sent_at)))
        self._next_send_at=asyncio.get_running_loop().time()+delay
        self._running=True
        self._report('requesting','Запускаю перебаны…',log=True)
        self._task=asyncio.create_task(self._run(),name='ares-repunish')
        return True

    def resolve_uncertain(self,was_sent):
        if self.busy or self._uncertain is None:raise RepunishError('Сейчас нет отправки для проверки.')
        row=self._uncertain
        if was_sent:self._confirmed[row.id]=row.command;self._last_sent_at=time.time()
        else:self._confirmed.pop(row.id,None)
        self._uncertain=None
        try:self._save()
        except Exception:self._uncertain=row;raise
        self._report('ready','Проверка сохранена. Нажми «Запустить перебаны» для продолжения.',log=True)

    async def _recover(self):
        """При аварии проверяем только точную собственную исходящую команду."""
        row=self._uncertain
        if row is None:return True
        self._report('recovering',f'Проверяю историю отправки ID {row.id}…')
        messages=await asyncio.wait_for(self.client.get_messages(self.mine,limit=100),self.send_timeout)
        for message in messages:
            if not getattr(message,'out',False) or (message.raw_text or '').strip()!=row.command:continue
            date=getattr(message,'date',None)
            if date is None or date.timestamp()+15<self._attempt_started:continue
            self._confirmed[row.id]=row.command;self._uncertain=None
            self._last_sent_at=max(self._last_sent_at,date.timestamp())
            self._save();self._report('recovering',f'ID {row.id} найден в исходящих. Повтор не нужен.',log=True)
            return True
        return False

    async def _run(self):
        state,message,level='done','Список на перебан пуст!','success'
        retryable=False;sent_since_rest=0;stale_batches=0
        try:
            if self._uncertain and not await self._recover():
                raise RepunishError('Не удалось однозначно определить доставку предыдущей команды.')
            while True:
                self._check_enabled()
                batch=RepunishBatch(self._checkpoint_rows) if self._checkpoint_rows else await self._query()
                if batch.empty:
                    self._cycle_active=False;self._checkpoint_rows=();self._save()
                    message=f'Список на перебан пуст! Отправлено: {len(self._sent_ids)}, пропущено: {len(self._skipped_ids)}.'
                    break
                if all(row.id in self._handled for row in batch.rows):
                    stale_batches+=1
                    if stale_batches>=3:
                        raise RepunishError('StaffRobot трижды вернул уже закрытый список. Проверь его диалог и повтори запуск.')
                else:stale_batches=0
                self._checkpoint_rows=batch.rows;self._batch=batch;self._batch_number+=1;self._save()
                rows=[{**asdict(r),'status':'Отправлено' if r.id in self._confirmed else ('Закрыто ранее' if r.id in self._handled else ('В очереди' if r.allowed else 'Пропущено'))} for r in batch.rows]
                self._report('sending',f'Список {self._batch_number}: {len(batch.rows)} записей',log=True,rows=rows)
                for row in batch.rows:
                    self._check_enabled()
                    if row.id in self._confirmed:
                        if self._confirmed[row.id]!=row.command:raise RepunishError(f'ID {row.id} теперь содержит другую команду. Проверь список бота.')
                        self._sent_ids.add(row.id);continue
                    if row.id in self._handled:continue
                    if not row.allowed:
                        self._skipped_ids.add(row.id)
                        self._report('sending',f'ID {row.id} · причина {row.reason} пропущена',log=True,row_update={'id':row.id,'status':'Пропущено'})
                        continue
                    delay=max(0,self._next_send_at-asyncio.get_running_loop().time())
                    if delay:
                        self._report('waiting',f'Следующая команда через {delay:.1f} сек. · {row.player}')
                        await asyncio.sleep(delay)
                    self._check_enabled()
                    self._uncertain=row;self._attempt_started=time.time();self._save()
                    self._report('sending',f'Отправляю ID {row.id} · {row.player}',row_update={'id':row.id,'status':'Отправляю'})
                    while True:
                        self._check_enabled()
                        try:
                            await asyncio.wait_for(self.client.send_message(self.mine,row.command,parse_mode=None,link_preview=False),self.send_timeout)
                            break
                        except FloodWaitError as exc:
                            self._uncertain=None;self._save()
                            self._report('waiting',f'Telegram просит подождать {exc.seconds+3} сек.',log=True)
                            await asyncio.sleep(exc.seconds+3)
                            self._check_enabled();self._uncertain=row;self._attempt_started=time.time();self._save()
                        except RPCError as exc:
                            if getattr(exc,'code',0) in (400,403,420):self._uncertain=None;self._save()
                            raise
                        except Exception:
                            if await self._recover():break
                            raise
                    self._confirmed[row.id]=row.command;self._last_sent_at=time.time()
                    self._uncertain=None;self._next_send_at=asyncio.get_running_loop().time()+self.interval
                    try:self._save()
                    except Exception:self._uncertain=row;raise
                    self._sent_ids.add(row.id);sent_since_rest+=1
                    if self.burst_size and sent_since_rest>=self.burst_size:
                        self._next_send_at=max(self._next_send_at,asyncio.get_running_loop().time()+self.burst_rest)
                        sent_since_rest=0
                    self._report('sending',f'ID {row.id} · {row.player} · команда отправлена',log=True,level='success',row_update={'id':row.id,'status':'Отправлено'})
                # Как в SIMPLE-проекте: закрываем всю пачку, включая фильтр.
                for command in self._ack_commands([r.id for r in batch.rows]):
                    await self._cleanup(command)
                self._handled.update(r.id for r in batch.rows)
                self._checkpoint_rows=();self._save()
                self._report('confirming','Пачка закрыта. Запрашиваю следующий список.',log=True)
                if self.batch_pause:await asyncio.sleep(self.batch_pause)
        except asyncio.CancelledError:
            state,message,level='stopped','Перебаны остановлены. Очередь и отправленные ID сохранены.','info'
        except Exception as exc:
            state,level='error','error';retryable=isinstance(exc,RepunishTimeout) or not isinstance(exc,RepunishError)
            message=str(exc) if isinstance(exc,RepunishError) else f'Перебаны остановлены: {type(exc).__name__}. Очередь сохранена.'
            LOGGER.warning('Repunish stopped: %s',type(exc).__name__)
        finally:
            self._reply=None;self._running=False
            if self._uncertain:
                state,level='review','error';retryable=False
                message=f'Не подтверждена отправка ID {self._uncertain.id}. Проверь @AresMineRobot и нажми «Проверить отправку».'
            self._report(state,message,log=True,level=level,retryable=retryable)

    @staticmethod
    def _ack_commands(ids):
        command='/p repunish'
        for rid in ids:
            if len(command)+len(rid)+1>3900:
                yield command;command='/p repunish'
            command+=' '+rid
        if command!='/p repunish':yield command

    def _accept(self,pending,message,edited=False):
        if message.id<=pending.sent_id or getattr(message,'out',False):return
        reply_id=getattr(message,'reply_to_msg_id',None)
        if reply_id and reply_id not in {pending.sent_id,*pending.progress,*pending.messages}:return
        if edited and message.id not in pending.progress and message.id not in pending.messages:return
        text=message.raw_text or ''
        if PROGRESS.fullmatch(text):pending.progress.add(message.id);return
        if getattr(pending,'ack',False):
            if not (ACK.search(text) or ERROR.search(text)):return
        elif not (HEADER.search(text) or EMPTY.fullmatch(text) or re.match(r'\s*(?:[➤►•]|/p\s+repunish)',text) or ERROR.search(text)):
            combined=join_parts([pending.messages[mid].raw_text or '' for mid in sorted(pending.messages)])
            try:complete=parse_repunish(combined) is not None
            except RepunishError:return
            if not HEADER.search(combined) or complete:return
        old=pending.messages.get(message.id)
        if old is not None and old.raw_text==text:return
        pending.messages[message.id]=message
        pending.last_change=asyncio.get_running_loop().time();pending.changed.set()

    async def _request(self,command,*,ack=False):
        self._check_enabled()
        self._report('confirming' if ack else 'requesting',('Закрываю пачку: ' if ack else 'Запрашиваю ')+command,log=True)
        pending=self._reply=_Reply();pending.ack=ack
        try:
            sent=await asyncio.wait_for(self.client.send_message(self.staff,command,parse_mode=None,link_preview=False),self.send_timeout)
            pending.sent_id=sent.id
            for message,edited in pending.early:self._accept(pending,message,edited)
            pending.early.clear()
            loop=asyncio.get_running_loop();deadline=loop.time()+(min(30,self.response_timeout) if ack else self.response_timeout)
            next_poll=loop.time()+self.poll
            while True:
                self._check_enabled()
                text=join_parts([pending.messages[mid].raw_text or '' for mid in sorted(pending.messages)])
                if ack:
                    if ACK.search(text):return None
                    result=None
                else:result=parse_repunish(text)
                quiet=loop.time()-pending.last_change
                if pending.messages and quiet>=self.idle:
                    if result is not None:return result
                    if ERROR.search(text):raise RepunishError('StaffRobot: '+text[:600])
                remaining=deadline-loop.time()
                if remaining<=0:
                    raise RepunishTimeout('StaffRobot не подтвердил закрытие пачки; повтори запуск.' if ack else 'StaffRobot не прислал полный список перебанов. Ничего из неполной пачки не отправлено.')
                if loop.time()>=next_poll:
                    await self._poll_history(pending,remaining);next_poll=loop.time()+self.poll;continue
                pending.changed.clear()
                wait=min(remaining,max(.001,next_poll-loop.time()))
                if result is not None:wait=min(wait,max(.001,self.idle-quiet))
                try:await asyncio.wait_for(pending.changed.wait(),wait)
                except asyncio.TimeoutError:pass
        finally:self._reply=None

    async def _query(self):return await self._request('/p repunish')
    async def _cleanup(self,command):await self._request(command,ack=True)


class RepunishSchedule:
    """Один таймер, локальные часы ПК; следующий интервал после пустого списка."""
    def __init__(self,path,options,start,ready,changed,clock=time.time):
        self.path,self.options,self.start,self.ready,self.changed,self.clock=Path(path),dict(options),start,ready,changed,clock
        self.next_at=0;self._task=None;self.retry_at=0
        if self.path.exists():
            try:
                data=json.loads(self.path.read_text(encoding='utf-8'))
                if data.get('schedule')==self.signature:self.next_at=float(data.get('next_at',0))
            except (ValueError,OSError,TypeError):pass
        # Не выполняем просроченную проверку неожиданно сразу при подключении.
        if self.options['mode']=='manual':self.next_at=0
        elif self.next_at<=clock():self.next_at=next_run(self.options['mode'],self.options['daily'],clock())
        self._save();self.publish()

    @property
    def signature(self):return [self.options['mode'],self.options['daily']]

    def _save(self):atomic_json(self.path,{'schedule':self.signature,'next_at':self.next_at})
    def publish(self):self.changed({'next_run':self.next_at,'retry_at':self.retry_at,'schedule':self.options['mode']})
    def start_loop(self):
        if self._task is None or self._task.done():self._task=asyncio.create_task(self._loop(),name='ares-repunish-schedule')
    def update(self,options,busy=False):
        signature=self.signature;self.options=dict(options)
        if self.signature!=signature or (not busy and self.options['mode']!='manual' and not self.next_at and not self.retry_at):
            self.next_at=0 if busy else next_run(self.options['mode'],self.options['daily'],self.clock())
            self.retry_at=0;self._save()
        self.publish()
    def started(self):self.next_at=0;self.retry_at=0;self._save();self.publish()
    def completed(self):
        self.next_at=next_run(self.options['mode'],self.options['daily'],self.clock())
        self.retry_at=0;self._save();self.publish()
    def failed(self,retryable):
        self.next_at=0
        self.retry_at=self.clock()+120 if retryable and self.options['mode']!='manual' else 0
        self._save();self.publish()
    async def _loop(self):
        try:
            while True:
                due=self.retry_at or self.next_at
                if due and due<=self.clock() and self.ready():
                    try:started=await self.start()
                    except asyncio.CancelledError:raise
                    except Exception as exc:
                        LOGGER.warning('Scheduled repunish start failed: %s',type(exc).__name__)
                        self.failed(True)
                        self.changed({'state':'error','busy':False,'message':f'Не удалось запустить перебаны: {type(exc).__name__}. Повтор через 120 секунд.'})
                    else:
                        if not started:self.retry_at=self.clock()+120;self.publish()
                await asyncio.sleep(1)
        except asyncio.CancelledError:pass
        except Exception:
            LOGGER.exception('Repunish scheduler stopped')
            self.changed({'schedule_error':'Не удалось запустить расписание перебанов. Сохрани настройки повторно.'})
    async def close(self):
        task=self._task;self._task=None
        if task is not None:
            task.cancel()
            try:await task
            except asyncio.CancelledError:pass
