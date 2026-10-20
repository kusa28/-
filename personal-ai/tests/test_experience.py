from contextlib import closing
import json
from pathlib import Path
import tempfile
import unittest

from personal_ai.app import database, correct_segment
from personal_ai.diarization import store_turns
from personal_ai.environment_sound import store, MODEL
from personal_ai.experience import timeline, render_timeline


class ExperienceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "synthetic.sqlite3"
        with closing(database(self.path)) as db:
            self.rid = db.execute("""INSERT INTO recordings
                (sha256,source_path,filename,size_bytes,modified_at,duration,status,backend,model)
                VALUES (?,?,?,?,?,?,?,?,?)""", ('a'*64, '/never-open-original.wav', 'synthetic', 1, '0', 3, 'done', 'test', 'test')).lastrowid
            self.ids = []
            for start, end, text in ((0,1,'私は図書委員'),(1,2,'そうそう'*12),(2,3,'<script>bad()</script>')):
                self.ids.append(db.execute("INSERT INTO segments(recording_id,start,end,text,original_text) VALUES (?,?,?,?,?)",
                                          (self.rid,start,end,text,text)).lastrowid)
            db.commit()

    def test_join_preserves_sources_uncertainty_and_unknown_identity(self):
        store_turns(self.rid, [
            {'start':0,'end':1.5,'speaker':'SPEAKER_00','exclusive':True},
            {'start':1.5,'end':3,'speaker':'SPEAKER_01','exclusive':True},
            {'start':0,'end':1.8,'speaker':'SPEAKER_00'},
            {'start':1.4,'end':3,'speaker':'SPEAKER_01'},
        ],self.path)
        store(self.rid, {'model':MODEL,'windows':[
            {'start':0.5,'end':1.5,'predictions':[{'identifier':'rain','confidence':0.7}]},
            {'start':2,'end':3,'predictions':[{'identifier':'crowd','confidence':0.5}]},
        ]},self.path)
        result = timeline(self.rid,self.path)
        first,middle,last = result['units']
        self.assertEqual(first['speaker']['speaker_label'],'SPEAKER_00')
        self.assertEqual(middle['speaker']['speaker_label'],'UNKNOWN')
        self.assertTrue(middle['speaker']['overlap'])
        self.assertIsNone(first['speaker']['person_id'])
        self.assertEqual(first['source']['segment_id'],self.ids[0])
        sound = first['sound_window_predictions'][0]
        self.assertEqual((sound['overlap_start'],sound['overlap_end']),(0.5,1))
        self.assertEqual((sound['source']['start'],sound['source']['end']),(0.5,1.5))
        self.assertEqual(sound['kind'],'model_prediction')
        self.assertFalse(sound['verified'])
        self.assertIsNone(middle['utterance']['text'])
        self.assertTrue(middle['utterance']['uncertain'])
        self.assertIsNone(result['recording']['recorded_at'])
        self.assertIsNone(last['location'])
        self.assertEqual(last['interpretations'],[])

    def test_correction_deletion_and_prediction_replacement_are_immediate(self):
        self.assertIsNone(timeline(self.rid,self.path)['units'][1]['utterance']['text'])
        correct_segment(self.ids[1],'訂正された発言',self.path)
        result=timeline(self.rid,self.path)
        self.assertEqual(result['units'][1]['utterance']['kind'],'human_corrected_transcript')
        self.assertEqual(result['units'][1]['utterance']['text'],'訂正された発言')
        with closing(database(self.path)) as db:
            db.execute('DELETE FROM segments WHERE id=?',(self.ids[1],)); db.commit()
        self.assertEqual(timeline(self.rid,self.path)['total_segments'],2)
        store(self.rid,{'model':MODEL,'windows':[{'start':0,'end':1,'predictions':[{'identifier':'music','confidence':0.4}]}]},self.path)
        self.assertEqual(len(timeline(self.rid,self.path)['units'][0]['sound_window_predictions']),1)
        store(self.rid,{'model':MODEL,'windows':[]},self.path)
        self.assertEqual(timeline(self.rid,self.path)['units'][0]['sound_window_predictions'],[])

    def test_recording_measurements_are_not_attributed_to_individual_utterances(self):
        observation={'kind':'observation','duration_seconds':3,'rms_dbfs':-20,'peak_dbfs':-10,'silence_seconds':0.2,'emotion':'happy'}
        with closing(database(self.path)) as db:
            db.execute('INSERT INTO audio_observations(recording_id,result_json) VALUES (?,?)',(self.rid,json.dumps(observation))); db.commit()
        result=timeline(self.rid,self.path)
        self.assertEqual(result['recording_acoustic_observations']['scope'],'whole_recording')
        self.assertNotIn('emotion',result['recording_acoustic_observations']['values'])
        self.assertIsNone(result['units'][0]['emotion'])

    def test_page_boundary_still_flags_consecutive_repetition(self):
        with closing(database(self.path)) as db:
            db.execute('DELETE FROM segments')
            for start in range(4):
                db.execute('INSERT INTO segments(recording_id,start,end,text,original_text) VALUES (?,?,?,?,?)',
                           (self.rid,start*0.5,start*0.5+0.5,'同じ発言','同じ発言'))
            db.commit()
        result=timeline(self.rid,self.path,offset=1,limit=1)
        self.assertTrue(result['units'][0]['utterance']['uncertain'])
        self.assertEqual(result['next_offset'],2)
        self.assertEqual(timeline(self.rid,self.path,offset=4)['units'],[])

    def test_unknown_sources_and_html_escaping_and_bounds(self):
        result=timeline(self.rid,self.path)
        self.assertEqual(result['units'][0]['speaker']['speaker_label'],'UNKNOWN')
        self.assertEqual(result['units'][0]['sound_window_predictions'],[])
        self.assertIsNone(result['recording_acoustic_observations'])
        markup=render_timeline(result)
        self.assertNotIn('<script>',markup)
        self.assertIn('&lt;script&gt;',markup)
        self.assertNotIn('そうそう',markup)
        self.assertIn('/transcript/clip?id=',markup)
        self.assertNotIn('/never-open-original.wav',json.dumps(result))
        for kwargs in ({'limit':101},{'offset':-1},{'limit':0}):
            with self.assertRaises(ValueError):
                timeline(self.rid,self.path,**kwargs)
        with self.assertRaises(ValueError):
            timeline(999,self.path)

    def test_invalid_timing_does_not_produce_correspondence(self):
        with closing(database(self.path)) as db:
            db.execute('UPDATE segments SET end=4 WHERE id=?',(self.ids[2],)); db.commit()
        store_turns(self.rid,[{'start':2,'end':4,'speaker':'SPEAKER_00','exclusive':True}],self.path)
        store(self.rid,{'model':MODEL,'windows':[{'start':2,'end':4,'predictions':[{'identifier':'rain','confidence':0.5}]}]},self.path)
        result=timeline(self.rid,self.path)
        unit=result['units'][2]
        self.assertFalse(unit['source_timing_valid'])
        self.assertIsNone(unit['utterance']['text'])
        self.assertEqual(unit['speaker']['speaker_label'],'UNKNOWN')
        self.assertEqual(unit['sound_window_predictions'],[])
        self.assertEqual(result['excluded_invalid_timing'],{'speaker_turns':1,'sound_windows':1})

    def test_http_route_shows_synthetic_timeline_and_rejects_invalid_offset(self):
        import threading
        from http.server import HTTPServer
        from unittest.mock import patch
        import urllib.request
        import urllib.error
        from personal_ai.app import Handler
        with patch.dict('os.environ',{'PERSONAL_AI_DB':str(self.path)}):
            server=HTTPServer(('127.0.0.1',0),Handler)
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            try:
                base=f'http://127.0.0.1:{server.server_port}'
                with urllib.request.urlopen(base+f'/experience?id={self.rid}') as response:
                    self.assertIn('音声の統合タイムライン',response.read().decode())
                    self.assertEqual(response.status,200)
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(base+f'/experience?id={self.rid}&offset=-1')
                self.assertEqual(error.exception.code,400)
                error.exception.close()
            finally:
                server.shutdown(); server.server_close(); thread.join()


if __name__=='__main__':
    unittest.main()
