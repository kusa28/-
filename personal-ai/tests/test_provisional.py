from contextlib import closing
from datetime import datetime,timedelta
from http.server import HTTPServer
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import quote,urlencode
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from zoneinfo import ZoneInfo

from personal_ai.app import CSRF_TOKEN,Handler,database
from personal_ai.provisional import reply,search,service_counts,set_recorded_at

class Quiet(Handler):
    def log_message(self,*args): pass

class ProvisionalTests(unittest.TestCase):
    def test_recording_date_search_status_and_grounded_chat_in_browser(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'synthetic.sqlite3'
            with closing(database(path)) as db:
                db.execute("INSERT INTO recordings(id,sha256,source_path,filename,size_bytes,modified_at,duration,status,backend,model) VALUES (1,'synthetic','/never/open.wav','架空録音.m4a',1,'0',10,'done','test','test')")
                db.execute("INSERT INTO segments(recording_id,start,end,text,original_text) VALUES (1,0,2,'図書館で数学を勉強した','図書館で数学を勉強した')")
                db.commit()
            with patch.dict(os.environ,PERSONAL_AI_DB=str(path)):
                server=HTTPServer(('127.0.0.1',0),Quiet)
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                base=f'http://127.0.0.1:{server.server_port}'
                try:
                    for route,word in [('/','ラファエルAI'),('/search?q='+quote('数学'),'図書館で数学'),('/status','処理済み録音: 1件'),('/transcript?id=1','録音日時を保存')]:
                        with urlopen(base+route) as res:
                            self.assertEqual(res.status,200);self.assertIn(word,res.read().decode())
                    self.assertEqual(len(search('数学',path)['segments']),1)
                    self.assertIn('録音日時が確認された録音がまだない',reply('昨日はどんなことを話していた？',path))
                    yesterday=(datetime.now(ZoneInfo('Asia/Tokyo'))-timedelta(days=1)).strftime('%Y-%m-%dT12:00')
                    data=urlencode({'csrf':CSRF_TOKEN,'id':'1','recorded_at':yesterday}).encode()
                    with urlopen(Request(base+'/recording/date',data=data)) as res:
                        self.assertEqual(res.status,200)
                        self.assertIn('本人入力',res.read().decode())
                    with closing(database(path)) as db:
                        self.assertEqual(db.execute('SELECT recorded_at_source FROM recordings WHERE id=1').fetchone()[0],'user')
                    self.assertEqual(service_counts(path)['recordings']['done'],1)
                    memory_page=urlopen(base+'/memory').read().decode()
                    self.assertIn('記憶を追加',memory_page)
                    add_data=urlencode({'csrf':CSRF_TOKEN,'kind':'value','text':'合成テスト: 仕組みを理解したい'}).encode()
                    with urlopen(Request(base+'/memory/add',data=add_data)) as res:
                        self.assertEqual(res.status,200)
                        added=res.read().decode()
                        self.assertIn('合成テスト: 仕組みを理解したい',added)
                        self.assertIn('本人確認済み',added)
                    hypothesis_data=urlencode({'csrf':CSRF_TOKEN,'kind':'hypothesis','text':'合成テストの仮説'}).encode()
                    with urlopen(Request(base+'/memory/add',data=hypothesis_data)) as res:
                        self.assertIn('仮説',res.read().decode())
                    from personal_ai.memory import list_memories
                    saved=list_memories(path)
                    self.assertEqual(saved[0]['kind'],'hypothesis')
                    self.assertFalse(saved[0]['user_confirmed'])
                    self.assertEqual(saved[1]['source'],'本人Web入力')
                    self.assertTrue(saved[1]['user_confirmed'])
                    with self.assertRaises(HTTPError) as err:
                        urlopen(Request(base+'/memory/add',data=urlencode({'csrf':'wrong','kind':'value','text':'no'}).encode()))
                    self.assertEqual(err.exception.code,403);err.exception.close()
                    self.assertFalse(Path('/never/open.wav').exists())
                finally:
                    server.shutdown();server.server_close();thread.join(2)

    def test_date_is_not_inferred_and_missing_context_is_not_invented(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'db.sqlite3'
            with closing(database(path)) as db:
                db.execute("INSERT INTO recordings(id,sha256,source_path,filename,size_bytes,modified_at,duration,status,backend,model) VALUES (1,'synthetic','/never/open.wav','synthetic',1,'0',10,'done','test','test')")
                db.commit()
            self.assertIn('どの録音か分からない',reply('この録音の重要な出来事は？',path))
            self.assertIn('どの出来事や記憶',reply('なぜそう判断した？ 根拠を見せて',path))
            self.assertIn('まだ確認できない',reply('最近よく話している話題は？',path))
            with self.assertRaises(ValueError):set_recorded_at(1,'2099-01-01T01:00',path)
            with closing(database(path)) as db:
                self.assertIsNone(db.execute('SELECT recorded_at FROM recordings WHERE id=1').fetchone()[0])
