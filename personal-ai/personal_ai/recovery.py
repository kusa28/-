"""Read-only official quota checks and durable, bounded development recovery."""

import argparse
from contextlib import closing
from datetime import datetime
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import plistlib
import re
import selectors
import shutil
import signal
import subprocess
import time

LABEL = "local.personal-ai.development"
AUTH_ARGS = ["-c", 'forced_login_method="chatgpt"', "-c", 'model_provider="openai"']
EXTERNAL_ERRORS = {"usage_limit", "authentication", "network", "timeout", "interrupted"}


def ensure_schema(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS development_gate (
      id INTEGER PRIMARY KEY CHECK(id=1), kind TEXT NOT NULL,
      next_probe_at REAL NOT NULL, checks INTEGER NOT NULL DEFAULT 0, checked_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS development_recovery (
      task_id TEXT PRIMARY KEY, error_kind TEXT NOT NULL, next_attempt REAL,
      attempts INTEGER NOT NULL DEFAULT 0, snapshot TEXT NOT NULL, patch_path TEXT,
      FOREIGN KEY(task_id) REFERENCES tasks(id)
    );
    """)
    columns = {r[1] for r in db.execute("PRAGMA table_info(runs)")}
    for name, kind in (("child_pid", "INTEGER"), ("snapshot", "TEXT"), ("reset_at", "REAL")):
        if name not in columns:
            db.execute(f"ALTER TABLE runs ADD COLUMN {name} {kind}")


def classify_error(value, now=None):
    """Return only safe categories and timestamps; never retain error text."""
    now = time.time() if now is None else now
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    lower = text.lower()
    if any(x in lower for x in ("unauthorized", "authentication", "login required", "not logged in", "token expired", "401")):
        kind = "authentication"
    elif any(x in lower for x in ("rate limit", "rate_limit", "usage limit", "usage_limit", "usagelimit", "quota", "too many requests", "429", "使用制限", "利用制限")):
        kind = "usage_limit"
    elif any(x in lower for x in ("connection", "network", "dns", "timed out", "temporarily unavailable", "stream disconnected", "502", "503", "504")):
        kind = "network"
    else:
        return None, {}
    times = []
    def walk(item):
        if not isinstance(item, dict):
            return
        for key, val in item.items():
            if key in ("resetsAt", "reset_at", "resets_at", "retryAt", "retry_at"):
                try:
                    timestamp = float(val) if not isinstance(val, str) or val.replace('.', '', 1).isdigit() else datetime.fromisoformat(val.replace('Z', '+00:00')).timestamp() if re.search(r'(Z|[+-]\d\d:\d\d)$', val) else None
                    if timestamp and math.isfinite(timestamp) and now < timestamp < now+366*86400:
                        times.append(timestamp)
                except (ValueError, TypeError, OverflowError):
                    pass
            elif key in ("retry_after", "retryAfterSeconds") and type(val) in (int, float) and math.isfinite(val) and 0 < val < 366*86400:
                times.append(now+val)
            elif isinstance(val, dict):
                walk(val)
    walk(value)
    for stamp in re.findall(r'\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d)?(?:Z|[+-]\d\d:\d\d)', text):
        try:
            parsed = datetime.fromisoformat(stamp.replace('Z', '+00:00')).timestamp()
            if now < parsed < now+366*86400:
                times.append(parsed)
        except ValueError:
            pass
    return kind, {"reset_at": max(times)} if times else {}


def _quota_result(result, now):
    buckets = result.get("rateLimitsByLimitId") or {"legacy": result.get("rateLimits")}
    if not isinstance(buckets, dict) or not buckets:
        return {"kind": "unknown_limit"}
    # Conservative: never treat purchased credits as permission to run after
    # a subscription window is exhausted. No reset/purchase RPC exists here.
    seen, full, resets = False, False, []
    for bucket in buckets.values():
        if not isinstance(bucket, dict):
            return {"kind": "unknown_limit"}
        if bucket.get('rateLimitReachedType'):
            full = True
        for key in ("primary", "secondary"):
            window = bucket.get(key)
            if window is None:
                continue
            used = window.get("usedPercent") if isinstance(window, dict) else None
            if type(used) not in (int, float) or not math.isfinite(used) or not 0 <= used <= 100:
                return {"kind": "unknown_limit"}
            seen = True
            if used >= 100:
                full = True
                reset = window.get("resetsAt")
                if type(reset) in (int, float) and math.isfinite(reset) and reset > now:
                    resets.append(reset)
    if full:
        return {"kind": "usage_limit", **({"reset_at": max(resets)} if resets else {})}
    return {"kind": "available" if seen else "unknown_limit"}


def probe_codex(binary, *, timeout=20, env=None):
    """Only initialize, account/read, account/rateLimits/read; no model turn."""
    from .orchestrator import _safe_environment
    process = subprocess.Popen([binary, "app-server", "--listen", "stdio://", *AUTH_ARGS],
        cwd=str(Path.home()), env=env if env is not None else _safe_environment(),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
    selector, buffer = selectors.DefaultSelector(), b""
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic()+timeout
    def send(item):
        process.stdin.write((json.dumps(item)+'\n').encode()); process.stdin.flush()
    def rpc(identifier, method, params=None):
        nonlocal buffer
        send({"id": identifier, "method": method, **({"params": params} if params is not None else {})})
        while time.monotonic() < deadline:
            while b'\n' in buffer:
                line, buffer = buffer.split(b'\n', 1)
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError('invalid_rpc_response')
                if message.get("id") == identifier:
                    if "error" in message:
                        kind, details = classify_error(message["error"])
                        return {"rpc_error": kind or "probe_unsupported", **details}
                    if not isinstance(message.get('result'), dict):
                        raise ValueError('invalid_rpc_result')
                    return message["result"]
            if not selector.select(max(0, deadline-time.monotonic())):
                raise TimeoutError
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                raise ConnectionError
            buffer += chunk
            if len(buffer) > 1048576:
                raise ValueError("probe_response_too_large")
        raise TimeoutError
    try:
        initialized = rpc(1, "initialize", {"clientInfo": {"name": "personal_ai_recovery", "version": "1.0"}})
        if initialized.get("rpc_error"):
            return {"kind": initialized["rpc_error"]}
        send({"method": "initialized", "params": {}})
        account = rpc(2, "account/read", {"refreshToken": False})
        if account.get("rpc_error"):
            return {"kind": account["rpc_error"]}
        if not account.get("account"):
            return {"kind": "authentication"}
        if not isinstance(account['account'],dict) or account["account"].get("type") != "chatgpt":
            return {"kind": "unsupported_auth"}
        limits = rpc(3, "account/rateLimits/read")
        return {"kind": limits["rpc_error"]} if limits.get("rpc_error") else _quota_result(limits, time.time())
    except (TimeoutError, OSError, ConnectionError):
        return {"kind": "network"}
    except (ValueError, KeyError, TypeError):
        return {"kind": "probe_unsupported"}
    finally:
        selector.close()
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL); process.wait()
        process.stdin.close(); process.stdout.close()


def snapshot(root):
    """Hash code files, HEAD and Git status without logging their contents."""
    def git(*args):
        result = subprocess.run(["git", *args], cwd=root, capture_output=True, check=True)
        return result.stdout
    state = hashlib.sha256(git("status", "--porcelain=v1", "-z"))
    paths = set(git("ls-files", "-z").split(b'\0') + git("ls-files", "--others", "--exclude-standard", "-z").split(b'\0'))
    for raw in sorted(paths - {b''}):
        path = Path(root)/os.fsdecode(raw)
        state.update(raw)
        if path.is_symlink():
            state.update(os.fsencode(os.readlink(path)))
        elif path.is_file():
            with path.open('rb') as source:
                for chunk in iter(lambda: source.read(65536), b''):
                    state.update(chunk)
        else:
            state.update(b'<missing>')
    return json.dumps({"head": git("rev-parse", "HEAD").decode().strip(), "tree_hash": state.hexdigest()}, sort_keys=True)


def pause(db, root, task_id, kind, now, details=None, run_id=None):
    details = details or {}
    old = db.execute("SELECT attempts FROM development_recovery WHERE task_id=?", (task_id,)).fetchone()
    attempts = (old[0] if old else 0)+1
    retry = details.get("reset_at")
    delay = 300 if kind == 'code_error' else min(21600, 1800*2**min(attempts-1,4))
    next_at = max(now+60, retry+60) if type(retry) in (int,float) and math.isfinite(retry) and retry > now else now+delay
    saved = snapshot(root)
    folder = Path(root)/'.development/checkpoints'
    folder.mkdir(parents=True,exist_ok=True); folder.chmod(0o700)
    patch_path = folder/f'run-{run_id or "interrupted"}-{task_id}.patch'
    patch = subprocess.run(['git','diff','--binary','HEAD'],cwd=root,capture_output=True,check=True).stdout
    patch_path.write_bytes(patch); patch_path.chmod(0o600)
    retry_at = None if attempts >= 3 else next_at
    db.execute("""INSERT INTO development_recovery(task_id,error_kind,next_attempt,attempts,snapshot,patch_path)
                  VALUES (?,?,?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET error_kind=excluded.error_kind,
                  next_attempt=excluded.next_attempt,attempts=excluded.attempts,snapshot=excluded.snapshot,patch_path=excluded.patch_path""",
               (task_id, kind, retry_at, attempts, saved, str(patch_path)))
    db.execute("UPDATE tasks SET status='BLOCKED',next_action=?,updated_at=? WHERE id=?",
               (f"{kind}: 正式な利用可否を確認して再開" if retry_at else f"{kind}: 復帰後の実行失敗が3回。要調査",now,task_id))
    if kind != 'code_error':
        db.execute("INSERT OR REPLACE INTO development_gate VALUES (1,?,?,?,?)",('service_retry_exhausted' if retry_at is None else kind,next_at,attempts,now))
    if run_id:
        db.execute("UPDATE runs SET snapshot=?,reset_at=? WHERE id=?",(saved,retry,run_id))
    db.commit()


def prepare(db, root, binary, now, probe):
    for run in db.execute("SELECT * FROM runs WHERE finished_at IS NULL").fetchall():
        if run['child_pid']:
            try:
                os.kill(run['child_pid'],0)
                return {"status":"orphan_still_running"}
            except ProcessLookupError:
                pass
        pause(db,root,run['task_id'],'interrupted',now,run_id=run['id'])
        db.execute("UPDATE runs SET finished_at=?,result='interrupted',error_kind='interrupted' WHERE id=?",(now,run['id']))
        db.commit()
    gate = db.execute("SELECT * FROM development_gate WHERE id=1").fetchone()
    if gate and gate['kind'] == 'service_retry_exhausted':
        return {'status':'blocked','reason':'service_retry_exhausted'}
    if gate and gate['next_probe_at'] > now:
        return {"status":"waiting", "reason":gate['kind'], "next_probe_at":gate['next_probe_at']}
    try:
        result = probe(binary)
    except OSError:
        result = {'kind':'probe_unavailable'}
    if result.get('kind') != 'available':
        kind = result.get('kind','unknown_limit')
        count = gate['checks']+1 if gate else 1
        delay = min(21600,1800*2**min(count-1,4))
        reset = result.get('reset_at')
        next_at = max(now+60,reset+60) if type(reset) in (int,float) and math.isfinite(reset) and reset > now else now+delay
        db.execute("INSERT OR REPLACE INTO development_gate VALUES (1,?,?,?,?)",(kind,next_at,count,now)); db.commit()
        return {"status":"waiting", "reason":kind, "next_probe_at":next_at}
    db.execute("DELETE FROM development_gate")
    for job in db.execute("SELECT * FROM development_recovery WHERE next_attempt<=? ORDER BY next_attempt",(now,)).fetchall():
        if job['snapshot'] != snapshot(root):
            db.execute("UPDATE development_recovery SET next_attempt=NULL,error_kind='worktree_changed' WHERE task_id=?",(job['task_id'],))
            db.execute("UPDATE tasks SET next_action='中断後にGit/ファイルが変化。変更を確認して再開' WHERE id=?",(job['task_id'],)); db.commit()
            return {"status":"worktree_changed","task":job['task_id']}
        db.commit()
        return {"resume_task":job['task_id']}
    db.commit()
    return None


def install(root, binary):
    from .services import deploy_runtime
    runtime = deploy_runtime()
    logs = Path.home()/'.local/state/personal-ai'; logs.mkdir(parents=True,exist_ok=True)
    plist = Path.home()/'Library/LaunchAgents'/f'{LABEL}.plist'
    spec = {"Label":LABEL, "ProgramArguments":[str(runtime/'.venv/bin/python'),'-m','personal_ai.recovery','tick',
            '--root',str(Path(root).resolve()),'--codex',str(Path(binary).resolve())],
            "WorkingDirectory":str(runtime),"RunAtLoad":True,"StartInterval":300,"ThrottleInterval":60,
            "EnvironmentVariables":{"PATH":f"{Path.home()}/.local/bin:/opt/homebrew/bin:/usr/bin:/bin"},
            "StandardOutPath":str(logs/'development.log'),"StandardErrorPath":str(logs/'development-error.log')}
    plist.write_bytes(plistlib.dumps(spec)); plist.chmod(0o600)
    domain=f'gui/{os.getuid()}'
    subprocess.run(['launchctl','bootout',domain,str(plist)],capture_output=True)
    subprocess.run(['launchctl','bootstrap',domain,str(plist)],capture_output=True,check=True)
    return plist


def main():
    from .orchestrator import ROOT, STATE, connect, run_once
    parser=argparse.ArgumentParser(description='Codexの正式な利用枠確認と自動復帰')
    parser.add_argument('action',choices=('tick','probe','status','install','remove','hold-lock'))
    parser.add_argument('--root',default=str(ROOT))
    parser.add_argument('--codex',default=shutil.which('codex'))
    args=parser.parse_args(); root=Path(args.root).resolve(); state=root/'.development/state.sqlite3'
    try:
        if args.action=='hold-lock':
            state.parent.mkdir(parents=True,exist_ok=True)
            with (state.parent/'run.lock').open('a+') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                print(json.dumps({'status':'interactive_lock_acquired','pid':os.getpid()}),flush=True)
                time.sleep(1800)
            return
        if args.action=='status':
            with closing(connect(state)) as db:
                result={'gate':[dict(r) for r in db.execute('SELECT * FROM development_gate')],
                        'waiting_tasks':[dict(r) for r in db.execute('SELECT task_id,error_kind,next_attempt,attempts FROM development_recovery')]}
        elif args.action=='remove':
            p=Path.home()/'Library/LaunchAgents'/f'{LABEL}.plist'
            subprocess.run(['launchctl','bootout',f'gui/{os.getuid()}',str(p)],capture_output=True)
            if p.exists(): p.unlink()
            result={'status':'removed'}
        elif not args.codex:
            result={'status':'codex_unavailable'}
        elif args.action=='install':
            result={'plist':str(install(root,args.codex))}
        elif args.action=='probe':
            result=probe_codex(args.codex)
        else:
            result=run_once(root=root,state=state,codex=args.codex)
        print(json.dumps(result,ensure_ascii=False))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(1,f'開発復帰処理エラー: {type(exc).__name__}\n')


if __name__=='__main__':
    main()
