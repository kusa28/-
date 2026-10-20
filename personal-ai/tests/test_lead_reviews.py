from pathlib import Path
import json
import subprocess
import tempfile
import unittest
from personal_ai.lead_reviews import prepare
from personal_ai.orchestrator import _codex_command

class LeadTests(unittest.TestCase):
    def test_both_agents_once_for_complex_task_and_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            subprocess.run(['git','init','-q'],cwd=root,check=True)
            subprocess.run(['git','-c','user.email=test@example.invalid','-c','user.name=Test','commit','--allow-empty','-qm','init'],cwd=root,check=True)
            (root/'.development').mkdir()
            (root/'.development/agent-policy.json').write_text(json.dumps({'lead':'claude','code_only':True}))
            calls=[]
            def fake(agent,task,timeout):
                calls.append((agent,timeout));return agent,True,'コードのみの指摘'
            task={'id':'RECORDING_KNOWLEDGE','purpose':'合成データ整理','next_action':'根拠試験'}
            first=prepare(root,task,invoke=fake,now=100)
            second=prepare(root,task,invoke=fake,now=101)
            self.assertEqual(calls,[('claude',180),('agy',180)])
            self.assertEqual(first,second)
            self.assertIn('claudeから',first['brief'])
            self.assertIn('code_review_data',_codex_command({'id':'X','purpose':'P','completion':'C','next_action':'N','session_id':None},'codex',root,first['brief'])[-1])

    def test_unavailable_cools_down_without_busy_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            subprocess.run(['git','init','-q'],cwd=root,check=True)
            subprocess.run(['git','-c','user.email=test@example.invalid','-c','user.name=Test','commit','--allow-empty','-qm','init'],cwd=root,check=True)
            (root/'.development').mkdir();(root/'.development/agent-policy.json').write_text(json.dumps({'lead':'claude','code_only':True}))
            calls=[]
            def unavailable(agent,task,timeout): calls.append(agent);return agent,False,'quota'
            task={'id':'ORCH','purpose':'fixture','next_action':'fixture'}
            self.assertEqual(prepare(root,task,invoke=unavailable,now=1)['status'],'unavailable')
            self.assertEqual(prepare(root,task,invoke=unavailable,now=2)['status'],'unavailable')
            self.assertEqual(calls,['claude'])
