from contextlib import closing
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from personal_ai.app import database, correct_segment
from personal_ai.events import run_once, list_events, event_db, revise_event, forget_event, approve_event


class EventTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'synthetic.sqlite3'
        with closing(database(self.path)) as db:
            self.rids=[]; self.sids=[]
            for i in range(2):
                rid=db.execute("INSERT INTO recordings(sha256,source_path,filename,size_bytes,modified_at,duration,status,backend,model) VALUES (?,?,?,?,?,?,?,?,?)",(str(i)*64,'/never-open.wav','synthetic',1,'0',3,'done','test','test')).lastrowid
                sid=db.execute('INSERT INTO segments(recording_id,start,end,text,original_text) VALUES (?,?,?,?,?)',(rid,0,2,'昨日、図書館で友達と数学を勉強した','昨日、図書館で友達と数学を勉強した')).lastrowid
                self.rids.append(rid); self.sids.append(sid)
            db.commit()

    def answer(self, sid):
        return json.dumps({'events':[{'category':'study','summary':'友達と数学を勉強したとの発言','epistemic':'reported_event','occurred_at':'昨日','participants':['友達'],'topics':['数学','勉強'],'location':'図書館','confidence':0.7,'evidence':[{'segment_id':sid,'quote':'昨日、図書館で友達と数学を勉強した'}]}]},ensure_ascii=False)

    def test_links_review_and_delete_leave_originals(self):
        with patch('personal_ai.events.chat',side_effect=[self.answer(sid) for sid in self.sids]) as model:
            self.assertEqual(run_once(self.path)['status'],'done')
            self.assertEqual(run_once(self.path)['status'],'done')
            self.assertEqual(run_once(self.path)['status'],'idle')
            self.assertEqual(model.call_count,2)
        rows=list_events(self.path)
        self.assertEqual(len(rows),2); self.assertEqual(rows[0]['connections'][0]['relation'],'possibly_related')
        self.assertEqual(rows[0]['occurred_at'],'昨日'); self.assertEqual(rows[0]['verified'],0)
        revise_event(rows[0]['id'],'本人が訂正した説明',self.path,verified=True)
        self.assertEqual(list_events(self.path)[0]['verified'],1)
        forget_event(rows[0]['id'],self.path)
        self.assertEqual(run_once(self.path)['status'],'idle')
        with closing(event_db(self.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM recordings').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT count(*) FROM segments').fetchone()[0],2)

    def test_changed_and_deleted_sources_invalidate(self):
        with patch('personal_ai.events.chat',return_value=self.answer(self.sids[0])):
            run_once(self.path)
        correct_segment(self.sids[0],'図書館には行っていない',self.path)
        self.assertEqual(list_events(self.path),[])
        self.assertEqual(len(list_events(self.path,include_inactive=True)),1)
        with closing(event_db(self.path)) as db:
            db.execute('DELETE FROM segments WHERE id=?',(self.sids[0],)); db.commit()
        self.assertEqual(list_events(self.path),[])

    def test_invalid_quotes_retry_bounded_and_redacted(self):
        # Block both independent batches after three attempts, without text logs.
        with patch('personal_ai.events.chat',return_value='private invalid reply') as model:
            for now in (0,0,700,700,2000,2000):
                result=run_once(self.path,now=now)
            self.assertEqual(result['status'],'blocked')
            self.assertEqual(run_once(self.path,now=9000)['status'],'idle')
            self.assertEqual(model.call_count,6)
        with closing(event_db(self.path)) as db:
            self.assertEqual(set(r[0] for r in db.execute('SELECT error_class FROM event_runs')),{'JSONDecodeError'})

    def test_edit_during_inference_discards_result(self):
        def answer(*a,**kw):
            correct_segment(self.sids[0],'訂正済み',self.path)
            return self.answer(self.sids[0])
        with patch('personal_ai.events.chat',side_effect=answer):
            self.assertEqual(run_once(self.path)['status'],'stale')
        self.assertEqual(list_events(self.path),[])

    def test_hallucinated_evidence_rejected(self):
        payload=json.loads(self.answer(self.sids[0])); payload['events'][0]['participants']=['実在しない人物']
        with patch('personal_ai.events.chat',return_value=json.dumps(payload)):
            self.assertEqual(run_once(self.path)['status'],'failed')
        self.assertEqual(list_events(self.path),[])

    def test_long_recordings_advance_bounded_batches(self):
        with closing(database(self.path)) as db:
            db.execute('DELETE FROM segments')
            db.execute('UPDATE recordings SET duration=100')
            for i in range(25):
                text=f'数学の問題{i}を考えた'
                db.execute('INSERT INTO segments(recording_id,start,end,text,original_text) VALUES (?,?,?,?,?)',(self.rids[0],i,i+0.5,text,text))
            db.commit()
        with patch('personal_ai.events.chat',return_value='{"events":[]}') as model:
            for i in range(3):
                self.assertEqual(run_once(self.path)['status'],'done')
            self.assertEqual(run_once(self.path)['status'],'idle')
            self.assertEqual(model.call_count,3)
            sizes=[len(json.loads(c.args[0][1]['content'])) for c in model.call_args_list]
            self.assertEqual(sizes,[12,12,1])

    def test_episodic_bridge_is_unconfirmed_and_invalidates(self):
        with patch('personal_ai.events.chat',return_value=self.answer(self.sids[0])):
            run_once(self.path)
        eid=list_events(self.path)[0]['id']
        mid=approve_event(eid,self.path)
        self.assertEqual(approve_event(eid,self.path),mid)
        with closing(event_db(self.path)) as db:
            row=db.execute('SELECT * FROM memories WHERE id=?',(mid,)).fetchone()
            self.assertEqual(row['kind'],'episode'); self.assertEqual(row['user_confirmed'],0)
        correct_segment(self.sids[0],'訂正',self.path)
        with closing(event_db(self.path)) as db:
            self.assertEqual(db.execute('SELECT active FROM memories WHERE id=?',(mid,)).fetchone()[0],0)

class EventEditRegressionTests(unittest.TestCase):
    def test_model_version_refresh_keeps_existing_event_from_same_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'events.sqlite3'
            with closing(database(path)) as db:
                rid=db.execute("INSERT INTO recordings(sha256,source_path,filename,size_bytes,modified_at,duration,status,backend,model) VALUES ('v','/never/open.wav','synthetic',1,'0',10,'done','test','test')").lastrowid
                sid=db.execute("INSERT INTO segments(recording_id,start,end,text,original_text) VALUES (?,0,2,'昨日数学を勉強した','昨日数学を勉強した')",(rid,)).lastrowid
                db.commit()
            answer=json.dumps({'events':[{'category':'study','summary':'数学を勉強したとの発言',
                'epistemic':'reported_event','occurred_at':'昨日','participants':[],
                'topics':['数学'],'location':None,'confidence':0.5,
                'evidence':[{'segment_id':sid,'quote':'昨日数学を勉強した'}]}]},ensure_ascii=False)
            with patch('personal_ai.events.chat',return_value=answer):
                self.assertEqual(run_once(path)['processed'],1)
                with patch('personal_ai.events.VERSION','future-version'):
                    self.assertEqual(run_once(path)['processed'],0)
            self.assertEqual(len(list_events(path)),1)

    def test_deleted_event_stays_deleted_and_other_event_not_duplicated_after_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'events.sqlite3'
            with closing(database(path)) as db:
                rid=db.execute("INSERT INTO recordings(sha256,source_path,filename,size_bytes,modified_at,duration,status,backend,model) VALUES ('x','/never/open.wav','synthetic',1,'0',10,'done','test','test')").lastrowid
                a=db.execute("INSERT INTO segments(recording_id,start,end,text,original_text) VALUES (?,0,2,'昨日数学を勉強した','昨日数学を勉強した')",(rid,)).lastrowid
                b=db.execute("INSERT INTO segments(recording_id,start,end,text,original_text) VALUES (?,2,4,'今日英語を勉強した','今日英語を勉強した')",(rid,)).lastrowid
                db.commit()
            def reply():
                return json.dumps({'events':[{'category':'study','summary':f'{sid}の勉強','epistemic':'reported_event','occurred_at':None,'participants':[],'topics':[],'location':None,'confidence':0.5,'evidence':[{'segment_id':sid,'quote':quote}]} for sid,quote in [(a,'昨日数学を勉強した'),(b,'今日英語を勉強した')]]},ensure_ascii=False)
            with patch('personal_ai.events.chat',return_value=reply()):
                self.assertEqual(run_once(path)['processed'],2)
            rows=list_events(path)
            forget_event(next(r['id'] for r in rows if r['evidence'][0]['segment_id']==a),path)
            with closing(event_db(path)) as db:
                db.execute('UPDATE segments SET text=? WHERE id=?',('今日英語を少し勉強した',b));db.commit()
            self.assertEqual(list_events(path),[])
            updated=json.loads(reply())
            updated['events'][1]['evidence'][0]['quote']='今日英語を少し勉強した'
            with patch('personal_ai.events.chat',return_value=json.dumps(updated,ensure_ascii=False)):
                self.assertEqual(run_once(path)['processed'],1)
            current=list_events(path)
            self.assertEqual(len(current),1)
            self.assertEqual(current[0]['evidence'][0]['segment_id'],b)
            self.assertEqual(run_once(path)['status'],'idle')
