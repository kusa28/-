from contextlib import closing
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from personal_ai.app import database
from personal_ai.events import run_once,list_events,revise_event,forget_event,approve_event
from personal_ai.organization import dashboard,run_cycle
from personal_ai.memory import revise as revise_memory
from personal_ai.reflection import list_reflections

class OrganizationTests(unittest.TestCase):
    def test_confirmed_event_reaches_daily_and_weekly_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'synthetic.sqlite3'
            with closing(database(path)) as db:
                rid=db.execute("INSERT INTO recordings(sha256,source_path,filename,size_bytes,modified_at,duration,status,backend,model) VALUES ('synthetic','/never/open.wav','synthetic',1,'0',10,'done','test','test')").lastrowid
                sid=db.execute("INSERT INTO segments(recording_id,start,end,text,original_text) VALUES (?,0,2,?,?)",(rid,'昨日、友達と数学を勉強した','昨日、友達と数学を勉強した')).lastrowid
                db.commit()
            answer=json.dumps({'events':[{'category':'study','summary':'友達と数学を勉強したという発言','epistemic':'reported_event','occurred_at':'昨日','participants':['友達'],'topics':['数学'],'location':None,'confidence':0.7,'evidence':[{'segment_id':sid,'quote':'昨日、友達と数学を勉強した'}]}]},ensure_ascii=False)
            with patch('personal_ai.events.chat',return_value=answer):
                run_once(path)
            mid=approve_event(list_events(path)[0]['id'],path)
            with patch('personal_ai.events.chat'),patch('personal_ai.deliberation.run',return_value={'status':'idle'}):
                run_cycle(path)
            self.assertEqual(list_reflections(path),[])
            revise_memory(mid,'本人が確認した架空の出来事',confirmed=True,path=path)
            with patch('personal_ai.events.chat'),patch('personal_ai.deliberation.run',return_value={'status':'idle'}):
                run_cycle(path)
            periods={row['period']:row for row in list_reflections(path)}
            self.assertEqual(set(periods),{'daily','weekly'})
            self.assertTrue(all([source['id'] for source in row['sources']]==[mid] for row in periods.values()))

    def test_local_end_to_end_two_recordings_review_search_and_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'synthetic.sqlite3';sids=[]
            with closing(database(path)) as db:
                for n in range(2):
                    rid=db.execute("INSERT INTO recordings(sha256,source_path,filename,size_bytes,modified_at,duration,status,backend,model) VALUES (?,?,'synthetic',1,'0',10,'done','test','test')",(str(n),'/never/open.wav')).lastrowid
                    sids.append(db.execute("INSERT INTO segments(recording_id,start,end,text,original_text) VALUES (?,0,2,?,?)",(rid,'昨日、友達と数学を勉強した','昨日、友達と数学を勉強した')).lastrowid)
                db.commit()
            def answer(sid):
                return json.dumps({'events':[{'category':'study','summary':'友達と数学を勉強したという発言','epistemic':'reported_event','occurred_at':'昨日','participants':['友達'],'topics':['数学','勉強'],'location':None,'confidence':0.7,'evidence':[{'segment_id':sid,'quote':'昨日、友達と数学を勉強した'}]}]},ensure_ascii=False)
            with patch('personal_ai.events.chat',side_effect=[answer(s) for s in sids]):
                run_once(path);run_once(path)
            rows=list_events(path)
            self.assertEqual(len(rows),2);self.assertTrue(rows[0]['connections'])
            markup=dashboard('数学',path,'token')
            self.assertIn('発言 #',markup);self.assertIn('possibly_related',markup)
            self.assertNotIn('/never/open.wav',markup)
            revise_event(rows[0]['id'],'<訂正>',path)
            self.assertIn('&lt;訂正&gt;',dashboard('',path,'token'))
            forget_event(rows[0]['id'],path)
            self.assertEqual(len(list_events(path)),1)
            with closing(database(path)) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM recordings').fetchone()[0],2)
            with patch('personal_ai.events.chat') as no_call:
                result=run_cycle(path)
                self.assertEqual(result['events']['status'],'idle');no_call.assert_not_called()

    def test_independent_steps_survive_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'db.sqlite3'
            with patch('personal_ai.events.run_once',side_effect=RuntimeError('private secret')),patch('personal_ai.consolidation.run',return_value={'status':'unchanged'}),patch('personal_ai.reflection.run_cycle',return_value={'status':'idle'}):
                result=run_cycle(path)
            self.assertEqual(result['events']['error_type'],'RuntimeError')
            self.assertNotIn('private',str(result));self.assertEqual(result['memory']['status'],'unchanged')
