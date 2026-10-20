from contextlib import closing
from http.server import HTTPServer
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request,urlopen
from personal_ai.app import Handler,CSRF_TOKEN,database
from personal_ai.memory import add,list_memories

class Quiet(Handler):
    def log_message(self,*args): pass

class MemoryEditTests(unittest.TestCase):
    def test_edit_history_delete_retains_recording_and_csrf(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'db.sqlite3'
            with patch.dict(os.environ,PERSONAL_AI_DB=str(path)):
                mid=add('episode','合成の出来事','合成',path=path)
                with closing(database(path)) as db:
                    db.execute("INSERT INTO recordings(id,sha256,source_path,filename,size_bytes,modified_at,duration,status,error,backend,model,created_at) VALUES (1,'hash','/never/open.wav','synthetic',1,'0',10,'done',NULL,'test','test',CURRENT_TIMESTAMP)")
                    db.commit()
                server=HTTPServer(('127.0.0.1',0),Quiet)
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                base=f'http://127.0.0.1:{server.server_port}'
                def post(route,**values):
                    return urlopen(Request(base+route,data=urlencode(values).encode()))
                try:
                    with self.assertRaises(HTTPError) as err:
                        post('/memory/edit',id=mid,text='bad',csrf='wrong')
                    self.assertEqual(err.exception.code,403);err.exception.close()
                    with post('/memory/edit',id=mid,text='<訂正>',confirmed='1',csrf=CSRF_TOKEN) as res:
                        self.assertIn('&lt;訂正&gt;',res.read().decode())
                    with closing(database(path)) as db:
                        self.assertEqual(db.execute('SELECT count(*) FROM memory_revisions').fetchone()[0],1)
                    with post('/memory/delete',id=mid,confirm_delete='1',csrf=CSRF_TOKEN): pass
                    self.assertEqual(list_memories(path),[])
                    with closing(database(path)) as db:
                        self.assertEqual(db.execute('SELECT count(*) FROM recordings').fetchone()[0],1)
                finally:
                    server.shutdown();server.server_close();thread.join(2)
