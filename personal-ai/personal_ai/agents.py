"""Opt-in, read-only coding-agent reviews with durable run records."""

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / ".agent_runs"
SOURCES = ("personal_ai/consolidation.py", "personal_ai/events.py", "personal_ai/organization.py", "personal_ai/memory.py", "personal_ai/reflection.py", "tests/test_consolidation.py", "tests/test_events.py", "PROJECT_PLAN.md", "TASKS.md")


def store():
    STATE.mkdir(mode=0o700, exist_ok=True)
    STATE.chmod(0o700)
    db = sqlite3.connect(STATE / "runs.sqlite3")
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS runs (
      id TEXT PRIMARY KEY, agent TEXT NOT NULL, task TEXT NOT NULL,
      status TEXT NOT NULL, started_at REAL NOT NULL, finished_at REAL,
      exit_code INTEGER, response TEXT, error TEXT)""")
    db.execute("UPDATE runs SET status='interrupted',finished_at=? WHERE status='running'", (time.time(),))
    db.commit()
    return db


def command(agent, task):
    if agent == "claude":
        binary = shutil.which("claude") or str(Path.home() / ".local/bin/claude")
        return [binary, "-p", task, "--output-format", "json", "--tools", ""]
    if agent == "agy":
        settings = Path.home() / ".gemini/antigravity-cli/settings.json"
        try:
            if json.loads(settings.read_text()).get("useG1Credits") is not False:
                raise ValueError("Antigravityの追加クレジット利用を無効にしてください")
        except FileNotFoundError as exc:
            raise ValueError("Antigravityの課金設定が未確認です") from exc
        binary = shutil.which("agy") or str(Path.home() / ".local/bin/agy")
        return [binary, "-p", task, "--output-format", "json", "--mode", "plan", "--sandbox"]
    raise ValueError("agent は claude または agy")


def keep_credits_off():
    settings = Path.home() / ".gemini/antigravity-cli/settings.json"
    data = json.loads(settings.read_text()) if settings.exists() else {}
    data["useG1Credits"] = False
    settings.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    settings.chmod(0o600)


def run(agent, task, timeout=180):
    command(agent, task)
    run_id = uuid.uuid4().hex[:12]
    db = store()
    db.execute("INSERT INTO runs(id,agent,task,status,started_at) VALUES (?,?,?,?,?)", (run_id, agent, task, "running", time.time()))
    db.commit()
    env = {name: os.environ[name] for name in ("HOME", "USER", "SHELL", "LANG", "LC_ALL", "TERM", "TMPDIR", "PATH") if name in os.environ}
    env["PATH"] = str(Path.home() / ".local/bin") + os.pathsep + env.get("PATH", "")
    with tempfile.TemporaryDirectory(prefix="personal-ai-review-") as tmp:
        sandbox = Path(tmp)
        excerpts = []
        for relative in SOURCES:
            source = ROOT / relative
            if source.is_file():
                target = sandbox / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                target.chmod(0o444)
                excerpts.append(f"--- {relative} ---\n{source.read_text()[:18000]}")
        prompt = ("以下はコードレビュー用の一時コピーです。ファイルを編集せず、ネットワークや外部ツールを使わず、日本語で回答してください。"
                  "録音や個人情報は含まれていません。具体的な問題、根拠、修正案を短く示してください。\n\n"
                  + task + "\n\n" + "\n\n".join(excerpts)[:50000])
        try:
            argv = command(agent, prompt)
            completed = subprocess.run(argv, cwd=sandbox, env=env, capture_output=True, text=True, timeout=timeout)
            raw = completed.stdout[-100000:]
            try:
                result = json.loads(raw)
                response = result.get("result", result.get("response", ""))
                inner_error = result.get("error", "")
                status = result.get("status", "")
            except json.JSONDecodeError:
                response = raw
                inner_error = "JSON出力を解析できませんでした"
                status = "ERROR"
            error = (completed.stderr[-4000:] + "\n" + str(inner_error)).strip()
            success = completed.returncode == 0 and status not in ("ERROR", "CANCELED") and bool(response)
            code = completed.returncode
        except subprocess.TimeoutExpired:
            response, error, success, code = "", f"{timeout}秒でタイムアウト", False, None
        except OSError as exc:
            response, error, success, code = "", str(exc), False, None
    if agent == "agy":
        keep_credits_off()
    db.execute("UPDATE runs SET status=?,finished_at=?,exit_code=?,response=?,error=? WHERE id=?", ("done" if success else "failed", time.time(), code, str(response)[:100000], error[:4000], run_id))
    db.commit()
    db.close()
    return run_id, success, response if success else error


def main():
    parser = argparse.ArgumentParser(description="読み取り専用のサブエージェント実行")
    sub = parser.add_subparsers(dest="action", required=True)
    job = sub.add_parser("run")
    job.add_argument("agent", choices=["claude", "agy"])
    job.add_argument("task")
    job.add_argument("--timeout", type=int, default=180)
    sub.add_parser("list")
    retry = sub.add_parser("retry")
    retry.add_argument("id")
    args = parser.parse_args()
    if args.action == "list":
        with closing(store()) as db:
            for row in db.execute("SELECT id,agent,status,started_at FROM runs ORDER BY started_at DESC LIMIT 30"):
                print(dict(row))
        return
    if args.action == "retry":
        with closing(store()) as db:
            row = db.execute("SELECT agent,task,status FROM runs WHERE id=?", (args.id,)).fetchone()
            if not row or row["status"] not in ("failed", "interrupted"):
                parser.error("再試行できる失敗記録がありません")
            agent, task, timeout = row["agent"], row["task"], 180
    else:
        agent, task, timeout = args.agent, args.task, args.timeout
    if timeout < 1 or timeout > 1800:
        parser.error("timeout は1～1800秒")
    run_id, success, output = run(agent, task, timeout)
    print(f"run_id={run_id} status={'done' if success else 'failed'}")
    print(output)
    if not success:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
