"""Code-only Claude/Antigravity lead briefs for scheduled development.

Every input comes from a fixed allowlist of repository files in agents.py.
Personal recordings, memories and app databases never enter the prompt.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import time

POLICY = '.development/agent-policy.json'
CACHE = '.development/lead-briefs.json'
COMPLEX_TASKS = {'RECORDING_KNOWLEDGE','EXPERIENCE_INTEGRATION','MEMORY_CONSOLIDATE',
                 'RELATIONSHIPS','HYPOTHESIS_LOOP','QUALITY_EVAL','PERSONAL_REASONING',
                 'PERSONA_ADAPT','MODEL_ROUTING'}

def _load(path):
    try: return json.loads(path.read_text())
    except (FileNotFoundError,ValueError): return {}

def prepare(root, task, *, invoke=None, now=None):
    """Run subscription-authenticated code reviews once per task/HEAD.

    Returns advisory prose only. Codex remains the integration and test gate.
    A failed service is cooled down for an hour, never retried in a loop.
    """
    root=Path(root); policy=_load(root/POLICY)
    if policy.get('lead')!='claude' or policy.get('code_only') is not True:
        return {'status':'disabled','brief':''}
    now=time.time() if now is None else now
    head=subprocess.run(['git','rev-parse','HEAD'],cwd=root,capture_output=True,text=True,check=True).stdout.strip()
    key=hashlib.sha256(json.dumps([task['id'],task['purpose'],task['next_action'],head],ensure_ascii=False).encode()).hexdigest()
    cache_path=root/CACHE;cache=_load(cache_path)
    previous=cache.get(key)
    if previous and (previous.get('status')=='ready' or now-previous.get('at',0)<3600):
        return previous
    if invoke is None:
        from .agents import run as invoke
    prompt=(f"開発タスク {task['id']}: {task['purpose']}。次の作業: {task['next_action']}。"
            '提供されたコードと計画だけを調べ、具体的な設計・変更箇所・テスト・失敗時の復旧方法を提案してください。'
            '録音・記憶・秘密情報へアクセスせず、APIキーや追加クレジットを使わないでください。')
    results={}
    for agent in (('claude','agy') if task['id'] in COMPLEX_TASKS else ('claude',)):
        try:
            run_id,ok,answer=invoke(agent,prompt,timeout=180)
        except (OSError,ValueError,RuntimeError) as exc:
            run_id,ok,answer=None,False,type(exc).__name__
        results[agent]={'ok':bool(ok),'run_id':run_id,'brief':str(answer)[:8000] if ok else ''}
    summaries='\n\n'.join(f'{agent}からのコードのみの提案（未検証）:\n{item["brief"]}' for agent,item in results.items() if item['ok'])
    entry={'status':'ready' if summaries else 'unavailable','at':now,'brief':summaries[:12000],
           'agents':{a:{'ok':v['ok'],'run_id':v['run_id']} for a,v in results.items()}}
    cache_path.parent.mkdir(parents=True,exist_ok=True)
    cache[key]=entry
    temp=cache_path.with_suffix('.tmp')
    temp.write_text(json.dumps(cache,ensure_ascii=False))
    temp.chmod(0o600);temp.replace(cache_path);cache_path.chmod(0o600)
    return entry
