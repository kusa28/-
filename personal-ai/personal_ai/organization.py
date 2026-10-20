"""Local recording knowledge dashboard and a bounded background cycle."""
import argparse
from contextlib import closing
from html import escape
import json
import os
from pathlib import Path
import plistlib
import subprocess
import fcntl

LABEL='local.personal-ai.organization'

def dashboard(query='',path=None,csrf=''):
    from .app import database
    from .events import list_events
    from .consolidation import list_relations
    def e(value): return escape(str(value if value is not None else ''),quote=True)
    events=list_events(path,query=query or None)
    relations=list_relations(path)
    with closing(database(path)) as db:
        recordings=[dict(r) for r in db.execute('SELECT id,filename,duration,status,created_at,modified_at FROM recordings ORDER BY created_at DESC,id DESC LIMIT 200')]
    parts=['<!doctype html><html lang="ja"><meta charset="utf-8"><title>出来事と録音整理</title>'
           '<a href="/">録音</a> · <a href="/memory">記憶・仮説・日週考察</a><h1>出来事と録音整理</h1>'
           '<p>AIによる未確認の抽出です。話者の身元、感情、場所を確定しません。原文は保持されます。</p>'
           '<form><label>人物表記・話題・出来事を検索 <input name="q" maxlength="200" value="'+e(query)+'"></label><button>検索</button></form><h2>出来事候補</h2>']
    if not events: parts.append('<p>候補なし。処理待ち、根拠不足、または検索条件をご確認ください。</p>')
    for item in events:
        parts.append(f'<article id="event-{e(item["id"])}"><h3>#{e(item["id"])} [{e(item["category"])} / {e(item["epistemic"])}] {e(item["summary"])}</h3>'
                     f'<p>発言内の日時表現: {e(item.get("occurred_at")) or "不明"} / 録音 #{e(item["recording_id"])} / モデル確信度 {e(item["confidence"])}（正確さの確率ではありません）</p>'
                     f'<p>人物表記: {e(item.get("participants"))} / 話題: {e(item.get("topics"))} / 場所の発言表記: {e(item.get("location")) or "不明"}</p><ul>')
        for source in item.get('evidence',[]):
            parts.append(f'<li><a href="/experience?id={e(item["recording_id"])}">発言 #{e(source["segment_id"])} ({e(source["start"])}–{e(source["end"])}秒)</a> '
                         f'<audio controls preload="none" src="/transcript/clip?id={e(source["segment_id"])}"></audio> {e(source["quote"])}</li>')
        parts.append(f'<form method="post" action="/event/save-memory"><input type="hidden" name="csrf" value="{e(csrf)}"><input type="hidden" name="id" value="{e(item["id"])}"><button>未確認の長期記憶へ保存</button></form>')
        parts.append('</ul><p>関連候補（同一出来事の確定ではありません）: '+e(item.get('connections',[]))+'</p>')
        parts.append(f'<form method="post" action="/event/edit"><input type="hidden" name="csrf" value="{e(csrf)}"><input type="hidden" name="id" value="{e(item["id"])}"><textarea name="text" maxlength="2000" required>{e(item["summary"])}</textarea><button>履歴を残して訂正</button></form>'
                     f'<form method="post" action="/event/delete"><input type="hidden" name="csrf" value="{e(csrf)}"><input type="hidden" name="id" value="{e(item["id"])}"><label><input type="checkbox" name="confirm_delete" value="1" required>派生候補を削除</label><button>削除（原本は保持）</button></form></article>')
    parts.append('<h2>記憶同士の整理・変化の候補</h2><p>別々の記憶と根拠を保持し、自動的に上書き統合しません。</p><ul>')
    for relation in relations: parts.append('<li>'+e(relation)+'</li>')
    parts.append('</ul><h2>取り込み日ごとの録音（録音日時とは異なります）</h2>')
    day=None
    for row in recordings:
        if row['created_at'][:10]!=day:
            day=row['created_at'][:10]; parts.append('<h3>'+e(day)+'</h3>')
        duration = '不明' if row['duration'] is None else f'{row["duration"]:.0f}秒'
        parts.append(f'<p><a href="/experience?id={row["id"]}">#{row["id"]} {e(row["filename"])}</a> — {e(row["status"])} / {duration}。録音日時: 未確定</p>')
    return ''.join(parts)+'</html>'

def run_cycle(path=None):
    from .app import DEFAULT_DB
    from .events import run_once
    from .consolidation import run
    from .reflection import run_cycle as reflect
    lock_path=Path(path or os.environ.get('PERSONAL_AI_DB',DEFAULT_DB)).with_suffix('.organization.lock')
    lock_path.parent.mkdir(parents=True,exist_ok=True)
    with lock_path.open('a+') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: return {'status':'already_running'}
        result={}
        # Independent steps continue when a local model is temporarily unavailable.
        for name,action in [('events',lambda:run_once(path)),('memory',lambda:run(path,semantic=True)),('reflection',lambda:reflect(path=path))]:
            try: result[name]=action()
            except Exception as exc: result[name]={'status':'failed','error_type':type(exc).__name__}
        return result

def install_agent():
    from .services import deploy_runtime
    runtime=deploy_runtime(); logs=Path.home()/'.local/state/personal-ai';logs.mkdir(parents=True,exist_ok=True)
    p=Path.home()/'Library/LaunchAgents'/f'{LABEL}.plist'
    p.write_bytes(plistlib.dumps({'Label':LABEL,'ProgramArguments':[str(runtime/'.venv/bin/python'),'-m','personal_ai.organization','cycle'], 'WorkingDirectory':str(runtime),'RunAtLoad':True,'StartInterval':1800,'StandardOutPath':str(logs/'organization.log'),'StandardErrorPath':str(logs/'organization-error.log')}))
    domain=f'gui/{os.getuid()}'
    subprocess.run(['launchctl','bootout',domain,str(p)],capture_output=True)
    subprocess.run(['launchctl','bootstrap',domain,str(p)],capture_output=True,check=True)
    return str(p)

def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['cycle','install-agent']);a=p.parse_args()
    result=install_agent() if a.action=='install-agent' else run_cycle()
    # Never print extracted private content or model responses to service logs.
    if isinstance(result,dict):
        result={k:({'status':v.get('status','finished'),'error_type':v.get('error_type',v.get('error_class',v.get('error_kind')))} if isinstance(v,dict) else 'finished') for k,v in result.items()}
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__': main()
