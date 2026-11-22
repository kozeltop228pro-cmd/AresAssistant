import sys,tempfile,unittest,sqlite3,time,asyncio
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from core import KnowledgeBase,template_key,parse_ticket
from reply_guard import customer_from_header,latest_player_text,source_key
from telegram_service import TelegramService

class GuardTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name);self.k=KnowledgeBase(self.path/'k.db',self.path/'none');self.g=self.k.reply_guard
  self.t=self.k.create_template('QA','t a {ticket_id} Проверочный ответ. @@@',True,'тестовая ситуация номер один','',match_mode='exact')
  self.key=template_key(self.k.get_template(self.t)['answer_template'])
 def tearDown(self):self.k.close();self.temp.cleanup()
 def ctx(self,q,t='123456',sender='TG-42',source='src|dst'):
  c=self.g.ingest(source,t,f'TICKET #{t}\nSOURCE: {sender}\nQUESTION: {q}',q);c.update(template_id=self.t,template_key=self.key);return c
 def sent(self,c,enabled=True):
  token,why=self.g.reserve(c,self.key,enabled);self.assertTrue(token,why);self.g.finish(token,'sent');self.k.mark_processed(c['ticket'],'auto');return token
 def test_cap_followups_restart(self):
  self.sent(self.ctx('первый вопрос'));c=self.ctx('уточнение второе');self.assertTrue(c['followup']);self.sent(c)
  self.k.close();self.k=KnowledgeBase(self.path/'k.db',self.path/'none');self.g=self.k.reply_guard
  self.assertFalse(self.g.reserve(self.ctx('третье уточнение'),self.key)[0]);self.assertEqual(self.g.totals('src|dst','123456'),{'sent':2})
 def test_template_flag_saved_updated_imported(self):
  d=self.k.get_template(self.t);self.k.update_template(self.t,d['name'],d['answer_template'],True,d['triggers'],'',match_mode='exact',reply_limit_enabled=False)
  self.assertFalse(self.k.get_template(self.t)['reply_limit_enabled']);other=KnowledgeBase(self.path/'other.db',self.path/'none')
  try:
   other.merge_from_database(self.path/'k.db');r=next(x for x in other.template_rows() if x['name']=='QA');self.assertFalse(r['reply_limit_enabled'])
  finally:other.close()
 def test_disabled_budget_not_duplicates_or_manual(self):
  for i in range(3):self.sent(self.ctx('вопрос '+str(i)),False)
  self.assertFalse(self.g.reserve(self.ctx('вопрос 2'),self.key,False)[0])
  self.g.manual_takeover('src|dst','123456');self.assertFalse(self.g.reserve(self.ctx('вопрос 4'),self.key,False)[0])
 def test_customer_across_tickets_and_platforms(self):
  self.sent(self.ctx('один','100001'));self.sent(self.ctx('два','100002'))
  self.assertFalse(self.g.reserve(self.ctx('три','100003'),self.key)[0])
  self.assertTrue(self.g.reserve(self.ctx('три','100004',sender='VK-42'),self.key)[0])
  self.assertTrue(self.g.reserve(self.ctx('три','100005',source='other|dst'),self.key)[0])
 def test_customer_window_expires_and_different_template(self):
  self.sent(self.ctx('один','100001'));self.sent(self.ctx('два','100002'))
  self.assertTrue(self.g.reserve(self.ctx('три','100003'),'another-template')[0])
  with self.g.db:self.g.db.execute('UPDATE reply_attempts SET created_at=?',(time.time()-86401,))
  self.assertTrue(self.g.reserve(self.ctx('четыре','100004'),self.key)[0])
  self.sent(self.ctx('снова','100001'));self.assertFalse(self.g.reserve(self.ctx('третий','100001'),self.key)[0])
 def test_nohelp_manual_early(self):
  self.sent(self.ctx('не получается войти'));c=self.ctx('уже пробовал, не помогло');self.assertIn('ручной',self.g.check(c,self.key))
  self.assertFalse(self.g.reserve(self.ctx('другое уточнение'),self.key)[0]);self.assertTrue(self.g.reserve(self.ctx('другое уточнение'),self.key,False)[0])
 def test_failed_and_draft_do_not_spend_slots(self):
  c=self.ctx('draft');self.g.mark_handled(c,'draft');self.assertEqual(self.g.totals('src|dst','123456'),{})
  c=self.ctx('error');token,_=self.g.reserve(c,self.key);self.g.finish(token,'failed');self.sent(c);self.sent(self.ctx('ещё вопрос'))
  self.assertFalse(self.g.reserve(self.ctx('третий'),self.key)[0])
 def test_concurrent_reservations(self):
  contexts=[self.ctx(str(i),str(200000+i)) for i in range(12)]
  with ThreadPoolExecutor(12) as pool:results=list(pool.map(lambda c:self.g.reserve(c,self.key),contexts))
  self.assertEqual(sum(bool(x[0]) for x in results),2)
 def test_uncertain_retains_slot(self):
  c=self.ctx('timeout');token,_=self.g.reserve(c,self.key);self.g.finish(token,'uncertain')
  self.assertFalse(self.g.reserve(c,self.key)[0]);self.sent(self.ctx('второй'));self.assertFalse(self.g.reserve(self.ctx('третий'),self.key)[0])
 def test_strict_header_metadata(self):
  self.assertEqual(customer_from_header('TICKET #123456\nSOURCE: VK-00042\nQUESTION: ok','123456'),'VK:42')
  self.assertEqual(customer_from_header('[#123456] TG-42\nИгрок: ok','123456'),'TG:42')
  self.assertEqual(customer_from_header('TICKET #123456\nQUESTION: SOURCE: TG-42','123456'),'')
  self.assertEqual(customer_from_header('TICKET #123456\nSOURCE: TG-42\nSOURCE: VK-42\nQUESTION: ok','123456'),'')
  self.assertEqual(latest_player_text('[12] Игрок: старое не помогло\n[13] Агент: ответ\n[14] Игрок: новый вопрос','fallback'),'новый вопрос')
 def test_legacy_migration_one_confirmed_send(self):
  self.k.observe_ticket('123456','old');self.k.add_history('123456','old','auto_queued',100);self.k.mark_processed('123456','auto')
  c=self.ctx('follow-up');self.assertTrue(c['followup']);self.assertEqual(self.g.totals('src|dst','123456'),{'sent':1})
  self.sent(c);self.assertFalse(self.g.reserve(self.ctx('third'),self.key)[0])
 def test_old_database_adds_default_flag(self):
  self.k.close();db=sqlite3.connect(self.path/'k.db');db.execute('ALTER TABLE templates DROP COLUMN reply_limit_enabled');db.commit();db.close()
  self.k=KnowledgeBase(self.path/'k.db',self.path/'none');self.g=self.k.reply_guard;self.assertTrue(self.k.get_template(self.t)['reply_limit_enabled'])
 def test_service_serializes_and_checks_after_delay(self):
  config={'source_chat':'src','response_chat':'dst','auto_delay_seconds':0,'auto_limit_per_minute':60}
  events=[];s=TelegramService(config,self.k,self.path/'fake',lambda *v:events.append(v));calls=[];s.connected=True
  class Client:
   async def send_message(self,*a,**kw):calls.append(a);await asyncio.sleep(.01)
   async def get_input_entity(self,p):return p
   async def __call__(self,r):return True
  s.client=Client();queued=[];s._submit=lambda coro,*a:queued.append(coro)
  for i in range(3):s.send_message('text',str(300000+i),self.ctx('q'+str(i),str(300000+i)))
  async def run():return await asyncio.gather(*queued)
  results=asyncio.run(run());self.assertEqual(len(calls),2);self.assertIn('reason',results[2])
  c=self.ctx('manual','400001',sender='TG-555');queued.clear();s.send_message('text','400001',c);self.g.manual_takeover('src|dst','400001')
  result=asyncio.run(queued[0]);self.assertIn('сотрудник',result['reason']);self.assertEqual(len(calls),2)
 def test_service_definite_failure_release_uncertain_keep(self):
  config={'source_chat':'src','response_chat':'dst','auto_delay_seconds':0};s=TelegramService(config,self.k,self.path/'fake',lambda *v:None)
  for error,expected,tid in [(ValueError('bad peer'),'failed','600001'),(ConnectionError('lost'),'uncertain','600002')]:
   class Client:
    async def send_message(self,*a,**kw):raise error
   s.client=Client();queued=[];s._submit=lambda coro,*a:queued.append(coro);s.send_message('text',tid,self.ctx('send',tid))
   with self.assertRaises(type(error)):asyncio.run(queued[0])
   self.assertEqual(self.g.totals('src|dst',tid),{expected:1})
 def test_manual_takeover_with_learning_disabled_and_pause(self):
  config={'api_id':1,'api_hash':'fake','source_chat':'src','response_chat':'dst','learning_enabled':False}
  s=TelegramService(config,self.k,self.path/'fake',lambda *v:None);s.set_paused(True)
  class Client:
   def __init__(self,*a):self.handlers=[]
   async def is_user_authorized(self):return True
   async def get_me(self):return SimpleNamespace(id=777,bot=False)
   async def get_input_entity(self,p):return p
   def on(self,*a):
    def register(f):self.handlers.append(f);return f
    return register
   async def run_until_disconnected(self):await self.handlers[1](SimpleNamespace(raw_text='t a 123456 Ответ сотрудника @@@'))
  async def authorized():pass
  s._authorize_qr=authorized
  with patch('telegram_service.TelegramClient',Client):asyncio.run(s._main())
  self.assertIn('сотрудник',self.g.check(self.ctx('question'),self.key,False))

if __name__=='__main__':unittest.main(verbosity=2)
