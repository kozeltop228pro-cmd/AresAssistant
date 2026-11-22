"""Durable automatic-reply budget and relay-header identity parsing.

No network lookups: a relay bot's Telegram sender is never the ticket owner.
Reservations are transactional. Uncertain sends retain their slot across restarts.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path

NO_HELP = re.compile(r"\b(?:не\s+помог(?:ло|ает|ли)|уже\s+пробовал[аи]?|вс[её]\s+равно\s+не\s+работает|по[ -]прежнему\s+не\s+работает)\b", re.I)


def source_key(config):
    return str(config.get('source_chat','')).strip().casefold()+'|'+str(config.get('response_chat','')).strip().casefold()


def fingerprint(question):
    return hashlib.sha256(' '.join(question.casefold().split()).encode('utf-8')).hexdigest()


def customer_from_header(raw, ticket_id):
    """Accept SOURCE metadata or a ticket header, never quoted player text."""
    found=set()
    for line in raw.replace('\r','').split('\n'):
        if re.search(r'\bQUESTION\s*:|(?:^|\])\s*Игрок\s*:|^\s*>',line,re.I):
            break
        is_source=re.match(r'^\s*SOURCE\s*:\s*(?:VK|TG)\s*[-:]\s*\d+\s*$',line,re.I)
        is_header=bool(re.search(r'\[#?'+re.escape(str(ticket_id))+r'\]|\bTICKET\s*#?'+re.escape(str(ticket_id))+r'\b',line,re.I))
        if is_source or is_header:
            for platform,identifier in re.findall(r'\b(TG|VK)\s*[-:]\s*(\d{1,20})\b',line,re.I):
                if int(identifier)>0:found.add(platform.upper()+':'+str(int(identifier)))
    return next(iter(found)) if len(found)==1 else ''


def latest_player_text(raw, fallback):
    blocks=re.split(r'(?:^|\n)\s*(?:\[[^\]\n]+\]\s*)?Игрок\s*:\s*',raw,flags=re.I)
    if len(blocks)>1:
        return re.split(r'\n\s*\[[^\]\n]+\]\s*[^:\n]{1,40}:',blocks[-1],maxsplit=1)[0].strip()
    return fallback


class ReplyGuard:
    def __init__(self,path:Path):
        self.lock=threading.RLock()
        self.db=sqlite3.connect(path,check_same_thread=False,timeout=20)
        self.db.row_factory=sqlite3.Row
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS reply_tickets(
                source TEXT NOT NULL,ticket TEXT NOT NULL,sender TEXT NOT NULL DEFAULT '',
                fingerprint TEXT NOT NULL,question TEXT NOT NULL,seen_at REAL NOT NULL,
                PRIMARY KEY(source,ticket));
            CREATE TABLE IF NOT EXISTS reply_messages(
                source TEXT NOT NULL,ticket TEXT NOT NULL,fingerprint TEXT NOT NULL,
                state TEXT NOT NULL,PRIMARY KEY(source,ticket,fingerprint));
            CREATE TABLE IF NOT EXISTS reply_attempts(
                token TEXT PRIMARY KEY,source TEXT NOT NULL,ticket TEXT NOT NULL,
                sender TEXT NOT NULL,template_key TEXT NOT NULL,fingerprint TEXT NOT NULL,
                state TEXT NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL,
                UNIQUE(source,ticket,fingerprint));
            CREATE INDEX IF NOT EXISTS reply_attempt_customer ON reply_attempts(source,sender,template_key,created_at);
            CREATE TABLE IF NOT EXISTS reply_handoffs(
                source TEXT NOT NULL,ticket TEXT NOT NULL,kind TEXT NOT NULL,reason TEXT NOT NULL,
                PRIMARY KEY(source,ticket));
        ''')
        self.db.commit()

    def close(self):
        with self.lock:self.db.close()

    def ingest(self,source,ticket,raw,question):
        fp=fingerprint(question);sender=customer_from_header(raw,ticket)
        with self.lock,self.db:
            old=self.db.execute('SELECT * FROM reply_tickets WHERE source=? AND ticket=?',(source,ticket)).fetchone()
            processed=self.db.execute('SELECT action FROM processed WHERE ticket_id=?',(ticket,)).fetchone()
            previous=self.db.execute('SELECT question FROM observed WHERE ticket_id=?',(ticket,)).fetchone()
            # Old versions retained only the final successful action. Seed exactly
            # one confirmed send for that ticket, never infer success from queued rows.
            if old is None and processed and processed['action']=='auto':
                history=self.db.execute("SELECT question FROM history WHERE ticket_id=? AND action='auto_queued' ORDER BY id DESC LIMIT 1",(ticket,)).fetchone()
                old_question=str(history['question']) if history else (str(previous['question']) if previous else '')
                if old_question:
                    old_fp=fingerprint(old_question)
                    self.db.execute('INSERT OR IGNORE INTO reply_attempts VALUES(?,?,?,?,?,?,?,?,?)',
                        ('legacy-'+uuid.uuid4().hex,source,ticket,'','',old_fp,'sent',0.,0.))
                    self.db.execute('INSERT OR IGNORE INTO reply_messages VALUES(?,?,?,?)',(source,ticket,old_fp,'sent'))
            if not sender and old:sender=old['sender']
            old_fp=old['fingerprint'] if old else (fingerprint(previous['question']) if previous else '')
            if old is None and processed and processed['action']=='draft' and old_fp:
                self.db.execute('INSERT OR IGNORE INTO reply_messages VALUES(?,?,?,?)',(source,ticket,old_fp,'draft'))
            self.db.execute('INSERT OR REPLACE INTO reply_tickets VALUES(?,?,?,?,?,?)',(source,ticket,sender,fp,question,time.time()))
            row=self.db.execute('SELECT state FROM reply_messages WHERE source=? AND ticket=? AND fingerprint=?',(source,ticket,fp)).fetchone()
            duplicate=bool(row and row['state'] in ('sent','draft','reserved','uncertain','handoff'))
            followup=bool(processed and processed['action'] in ('auto','draft') and old_fp and not duplicate)
        return {'source':source,'ticket':ticket,'sender':sender,'fingerprint':fp,'question':question,
                'latest_question':latest_player_text(raw,question),'followup':followup,'duplicate':duplicate}

    def mark_handled(self,context,state):
        if not context:return
        with self.lock,self.db:
            self.db.execute('INSERT OR REPLACE INTO reply_messages VALUES(?,?,?,?)',
                (context['source'],context['ticket'],context['fingerprint'],state))

    def manual_takeover(self,source,ticket):
        with self.lock,self.db:
            self.db.execute('INSERT OR REPLACE INTO reply_handoffs VALUES(?,?,?,?)',
                (source,ticket,'manual','На обращение уже ответил сотрудник'))

    def _policy(self,context,template_key,enabled,now):
        source,ticket=context['source'],context['ticket']
        takeover=self.db.execute('SELECT * FROM reply_handoffs WHERE source=? AND ticket=?',(source,ticket)).fetchone()
        if takeover and (takeover['kind']=='manual' or enabled):return takeover['reason']
        if not enabled:return ''
        rows=list(self.db.execute("SELECT state FROM reply_attempts WHERE source=? AND ticket=? AND state IN ('sent','reserved','uncertain')",(source,ticket)))
        sender=context.get('sender','')
        customer=list(self.db.execute("SELECT state FROM reply_attempts WHERE source=? AND sender=? AND template_key=? AND created_at>=? AND state IN ('sent','reserved','uncertain')",
            (source,sender,template_key,now-86400))) if sender and template_key else []
        prior_sent=any(row['state']=='sent' for row in rows+customer)
        if prior_sent and NO_HELP.search(context.get('latest_question',context.get('question',''))):
            reason='Клиент сообщил, что ответ не помог — требуется ручной ответ'
            self.db.execute('INSERT OR REPLACE INTO reply_handoffs VALUES(?,?,?,?)',(source,ticket,'feedback',reason))
            return reason
        if len(rows)>=2:return 'Лимит: уже 2 автоответа на этот тикет (включая неподтверждённые отправки)'
        if len(customer)>=2:return 'Лимит: этот шаблон уже использован для клиента 2 раза за последние 24 часа'
        return ''

    def check(self,context,template_key,enabled=True):
        with self.lock,self.db:return self._policy(context,template_key,enabled,time.time())

    def reserve(self,context,template_key,enabled=True):
        """Return (token, reason). Serialized SQLite check-and-reserve across threads."""
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                old=self.db.execute('SELECT state FROM reply_attempts WHERE source=? AND ticket=? AND fingerprint=?',
                    (context['source'],context['ticket'],context['fingerprint'])).fetchone()
                if old and old['state']!='failed':
                    self.db.commit();return None,'Повтор одного сообщения: ответ уже отправлен или ожидает подтверждения'
                reason=self._policy(context,template_key,enabled,time.time())
                if reason:self.db.commit();return None,reason
                if old:self.db.execute('DELETE FROM reply_attempts WHERE source=? AND ticket=? AND fingerprint=?',
                    (context['source'],context['ticket'],context['fingerprint']))
                token=uuid.uuid4().hex;now=time.time()
                self.db.execute('INSERT INTO reply_attempts VALUES(?,?,?,?,?,?,?,?,?)',
                    (token,context['source'],context['ticket'],context.get('sender',''),template_key,context['fingerprint'],'reserved',now,now))
                self.db.execute('INSERT OR REPLACE INTO reply_messages VALUES(?,?,?,?)',
                    (context['source'],context['ticket'],context['fingerprint'],'reserved'))
                self.db.commit();return token,''
            except BaseException:
                self.db.rollback();raise

    def finish(self,token,state):
        assert state in ('sent','failed','uncertain')
        with self.lock,self.db:
            row=self.db.execute('SELECT * FROM reply_attempts WHERE token=?',(token,)).fetchone()
            if not row:return
            self.db.execute('UPDATE reply_attempts SET state=?,updated_at=? WHERE token=?',(state,time.time(),token))
            self.db.execute('INSERT OR REPLACE INTO reply_messages VALUES(?,?,?,?)',(row['source'],row['ticket'],row['fingerprint'],state))

    def totals(self,source,ticket):
        with self.lock:
            return {row['state']:row['n'] for row in self.db.execute('SELECT state,COUNT(*) AS n FROM reply_attempts WHERE source=? AND ticket=? GROUP BY state',(source,ticket))}
