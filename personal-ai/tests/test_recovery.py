from contextlib import closing
import fcntl
import json
import os
import shlex
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock

from personal_ai.orchestrator import connect, run_once, _run_process
from personal_ai.recovery import classify_error, _quota_result, snapshot


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.state=self.root/'.development/state.sqlite3'
        def git(*args): subprocess.run(['git',*args],cwd=self.root,check=True,capture_output=True)
        git('init','-q'); git('config','user.name','Test'); git('config','user.email','test@example.invalid')
        (self.root/'.gitignore').write_text('.development/\n')
        (self.root/'PROGRESS.md').write_text('initial\n')
        (self.root/'tests').mkdir()
        (self.root/'tests/test_smoke.py').write_text('import unittest\nclass Smoke(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n')
        self.binary=self.root/'fake-codex'
        script=self.root/'fake-agent.py'
        self.binary.write_text('#!/bin/sh\nexec '+shlex.quote(sys.executable)+' '+shlex.quote(str(script))+' "$@"\n')
        script.write_text('''import json,sys,pathlib,time
p=pathlib.Path('.development/calls')
n=int(p.read_text())+1 if p.exists() else 1
p.write_text(str(n))
print(json.dumps({'type':'thread.started','thread_id':'test-session'}),flush=True)
with open('PROGRESS.md','a') as f: f.write('partial work\\n')
if n==1:
 print(json.dumps({'type':'turn.failed','error':{'message':'usage limit with secret detail','reset_at':time.time()+600}}),flush=True)
 sys.exit(1)
if 'resume' not in sys.argv or 'test-session' not in sys.argv: sys.exit(3)
print(json.dumps({'type':'turn.completed'}),flush=True)
''')
        self.binary.chmod(0o700)
        git('add','-A'); git('commit','-qm','fixture')
        self.now=time.time(); self.probe=Mock(return_value={'kind':'available'})

    def execute(self, now=None, probe=None):
        return run_once(root=self.root,state=self.state,codex=str(self.binary),timeout_seconds=5,
                        probe=probe or self.probe,now=self.now if now is None else now)

    def test_limit_saves_session_dirty_work_and_resumes_after_verified_reset(self):
        self.assertEqual(self.execute()['status'],'BLOCKED')
        with closing(connect(self.state)) as db:
            task=db.execute("SELECT * FROM tasks WHERE id='ORCH'").fetchone()
            job=db.execute('SELECT * FROM development_recovery').fetchone()
            self.assertEqual(task['session_id'],'test-session')
            self.assertEqual(task['failures'],0)
            self.assertEqual(job['snapshot'],snapshot(self.root))
            self.assertIn('partial work',Path(job['patch_path']).read_text())
            retry=job['next_attempt']
        self.probe.reset_mock()
        self.assertEqual(self.execute(now=self.now+100)['status'],'waiting')
        self.probe.assert_not_called()
        self.assertEqual(self.execute(now=retry+1)['status'],'TESTING')
        with closing(connect(self.state)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM development_recovery').fetchone()[0],0)
        self.assertEqual((self.root/'.development/calls').read_text(),'2')

    def test_user_changes_after_interruption_are_not_overwritten(self):
        self.execute()
        with (self.root/'PROGRESS.md').open('a') as f: f.write('user edit\n')
        self.assertEqual(self.execute(now=self.now+1000)['status'],'worktree_changed')
        self.assertEqual((self.root/'.development/calls').read_text(),'1')

    def test_auth_network_and_credit_gates_never_launch_inference(self):
        for kind in ('authentication','unsupported_auth','network','unknown_limit'):
            with closing(connect(self.state)) as db:
                db.execute('DELETE FROM development_gate'); db.commit()
            result=self.execute(probe=lambda _:{'kind':kind})
            self.assertEqual(result['reason'],kind)
        self.assertFalse((self.root/'.development/calls').exists())
        self.assertEqual(_quota_result({'rateLimits':{'primary':{'usedPercent':100,'resetsAt':self.now+600},'credits':{'hasCredits':True}}},self.now)['kind'],'usage_limit')

    def test_unknown_reset_uses_bounded_backoff_without_inference(self):
        probe=Mock(return_value={'kind':'usage_limit'})
        first=self.execute(probe=probe)
        self.assertEqual(first['next_probe_at'],self.now+1800)
        self.execute(now=self.now+100,probe=probe)
        probe.assert_called_once()
        second=self.execute(now=self.now+1801,probe=probe)
        self.assertEqual(second['next_probe_at'],self.now+1801+3600)

    def test_error_kinds_reset_timestamp_and_no_stderr_text_is_retained(self):
        kind,details=classify_error({'error':{'message':'429 rate limit','reset_at':self.now+1000}},now=self.now)
        self.assertEqual(kind,'usage_limit'); self.assertEqual(details['reset_at'],self.now+1000)
        self.assertEqual(classify_error('401 unauthorized')[0],'authentication')
        self.assertEqual(classify_error('network connection failed')[0],'network')
        self.assertIsNone(classify_error('test assertion failure')[0])
        result=_run_process([sys.executable,'-c',"import sys; sys.stderr.write('rate limit secret text')"],self.root,time.monotonic()+5,{})
        self.assertEqual(result[2],'usage_limit')
        self.assertNotIn('secret',str(result))

    def test_interrupted_manager_recovers_without_live_orphan_collision(self):
        with closing(connect(self.state)) as db:
            db.execute("INSERT INTO runs(task_id,started_at,child_pid) VALUES ('ORCH',?,?)",(self.now,os.getpid()))
            db.commit()
        self.assertEqual(self.execute()['status'],'orphan_still_running')
        with closing(connect(self.state)) as db:
            db.execute('UPDATE runs SET child_pid=NULL'); db.commit()
        result=self.execute()
        self.assertEqual((result['status'],result['reason']),('waiting','interrupted'))

    def test_two_separate_manager_processes_resume_the_same_saved_session(self):
        project=str(Path(__file__).resolve().parent.parent)
        script="""import sys,json
from pathlib import Path
from personal_ai.orchestrator import run_once
print(json.dumps(run_once(root=Path(sys.argv[1]),state=Path(sys.argv[2]),codex=sys.argv[3],now=float(sys.argv[4]),timeout_seconds=5,probe=lambda _:{'kind':'available'})))
"""
        command=[sys.executable,'-c',script,str(self.root),str(self.state),str(self.binary)]
        first=subprocess.run(command+[str(self.now)],cwd=project,capture_output=True,text=True,check=True)
        self.assertEqual(json.loads(first.stdout)['status'],'BLOCKED')
        second=subprocess.run(command+[str(self.now+1000)],cwd=project,capture_output=True,text=True,check=True)
        self.assertEqual(json.loads(second.stdout)['status'],'TESTING')

    def test_child_inherits_manager_lock_until_it_exits(self):
        lock_path=self.root/'inherited.lock'
        script="""import fcntl,subprocess,sys
f=open(sys.argv[1],'a+')
fcntl.flock(f,fcntl.LOCK_EX)
p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(0.5)'],pass_fds=(f.fileno(),),stdout=subprocess.DEVNULL)
print(p.pid,flush=True)
"""
        child=int(subprocess.check_output([sys.executable,'-c',script,str(lock_path)],text=True))
        with lock_path.open('a+') as handle:
            with self.assertRaises(BlockingIOError): fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            deadline = time.monotonic() + 3
            while True:
                try:
                    fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        self.fail("子プロセスの終了後も排他ロックが解放されない")
                    time.sleep(0.05)

    def test_code_error_preserves_owned_changes_for_bounded_fixing(self):
        script=self.root/'fake-agent.py'
        script.write_text(script.read_text().replace('usage limit with secret detail','test failed'))
        subprocess.run(['git','add','-A'],cwd=self.root,check=True,capture_output=True)
        subprocess.run(['git','commit','-qm','code error fixture'],cwd=self.root,check=True,capture_output=True)
        self.assertEqual(self.execute()['status'],'FAILED')
        with closing(connect(self.state)) as db:
            task=db.execute("SELECT failures FROM tasks WHERE id='ORCH'").fetchone()
            self.assertEqual(task[0],1)
        self.assertEqual(self.execute(now=self.now+1000)['status'],'TESTING')

    def test_official_probe_uses_only_read_methods_and_rejects_api_key_auth(self):
        from personal_ai.recovery import probe_codex
        script=self.root/'fake-rpc.py'
        script.write_text('''import json,sys,os,pathlib
for line in sys.stdin:
 m=json.loads(line)
 p=pathlib.Path(os.environ['METHOD_LOG'])
 with p.open('a') as f: f.write(m['method']+'\\n')
 if 'id' not in m: continue
 result={} if m['method']=='initialize' else {'account':{'type':os.environ.get('AUTH_KIND','chatgpt'),'email':'secret'}} if m['method']=='account/read' else {'rateLimits':{'primary':{'usedPercent':10,'resetsAt':2000000000}}}
 print(json.dumps({'id':m['id'],'result':result}),flush=True)
''')
        self.binary.write_text('#!/bin/sh\nexec '+shlex.quote(sys.executable)+' '+shlex.quote(str(script))+'\n')
        log=self.root/'methods'
        result=probe_codex(str(self.binary),env={'METHOD_LOG':str(log)})
        self.assertEqual(result,{'kind':'available'})
        self.assertEqual(log.read_text().splitlines(),['initialize','initialized','account/read','account/rateLimits/read'])
        self.assertNotIn('secret',str(result))
        result=probe_codex(str(self.binary),env={'METHOD_LOG':str(log),'AUTH_KIND':'apiKey'})
        self.assertEqual(result,{'kind':'unsupported_auth'})

    def test_repeated_service_failures_stop_after_three_actual_attempts(self):
        script=self.root/'fake-agent.py'
        script.write_text(script.read_text().replace('if n==1:', 'if n<=3:'))
        subprocess.run(['git','add','-A'],cwd=self.root,check=True,capture_output=True)
        subprocess.run(['git','commit','-qm','persistent limit fixture'],cwd=self.root,check=True,capture_output=True)
        for delta in (0,5000,15000):
            self.assertEqual(self.execute(now=self.now+delta)['status'],'BLOCKED')
        self.assertEqual(self.execute(now=self.now+50000)['reason'],'service_retry_exhausted')
        self.assertEqual((self.root/'.development/calls').read_text(),'3')

    def test_interactive_lock_prevents_quota_requests_and_execution(self):
        self.state.parent.mkdir(parents=True,exist_ok=True)
        with (self.state.parent/'run.lock').open('a+') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            self.assertEqual(self.execute()['status'],'already_running')
        self.probe.assert_not_called()

    def test_secondary_transport_message_does_not_hide_usage_limit(self):
        script="import sys,json; print(json.dumps({'type':'error','message':'usage limit'}),flush=True); sys.stderr.write('connection closed\\n')"
        result=_run_process([sys.executable,'-c',script],self.root,time.monotonic()+5,{})
        self.assertEqual(result[2],'usage_limit')


if __name__=='__main__': unittest.main()
