"""Independent, metadata-only, real-time 24-hour application soak monitor.

The monitor never reads recording contents or writes to the application database.
"""

import argparse
from contextlib import closing
from datetime import datetime
import json
import os
from pathlib import Path
import plistlib
import re
import sqlite3
import subprocess
import time
from urllib.request import urlopen

LABEL = "local.personal-ai.soak"
STATE = Path.home() / ".local/state/personal-ai/soak.sqlite3"
DURATION_SECONDS = 24 * 60 * 60
INTERVAL_SECONDS = 300


def _sleep_intervals(log_text):
    """Extract only actual Sleep/Wake events; ignore DarkWake and requests."""
    intervals = []
    sleep_at = None
    pattern = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d [+-]\d{4})[ \t]+(Sleep|Wake)[ \t]{2,}")
    for line in log_text.splitlines():
        match = pattern.match(line)
        if not match:
            continue
        when = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S %z").timestamp()
        if match.group(2) == "Sleep" and sleep_at is None:
            sleep_at = when
        elif match.group(2) == "Wake" and sleep_at is not None and when > sleep_at:
            intervals.append((sleep_at, when))
            sleep_at = None
    return intervals


def _system_sleep_intervals():
    try:
        result = subprocess.run(["pmset", "-g", "log"], capture_output=True,
                                text=True, timeout=15)
        return _sleep_intervals(result.stdout) if result.returncode == 0 else []
    except (OSError, subprocess.TimeoutExpired):
        return []


def _awake_gap(start, end, sleep_intervals):
    covered_until = start
    awake = 0
    for sleep_start, sleep_end in sorted(sleep_intervals):
        if sleep_end <= covered_until or sleep_start >= end:
            continue
        awake += max(0, min(end, sleep_start) - covered_until)
        covered_until = max(covered_until, min(end, sleep_end))
    return awake + max(0, end - covered_until)


def _db(path=STATE):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    db = sqlite3.connect(path, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.executescript("""
    CREATE TABLE IF NOT EXISTS runs (
      id INTEGER PRIMARY KEY, started_at REAL NOT NULL,
      ended_at REAL, status TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS samples (
      id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id),
      sampled_at REAL NOT NULL, web_ok INTEGER NOT NULL,
      ollama_ok INTEGER NOT NULL, worker_loaded INTEGER NOT NULL,
      ingestion_enabled INTEGER,
      web_cpu_percent REAL, web_rss_kb INTEGER,
      ollama_cpu_percent REAL, ollama_rss_kb INTEGER,
      recording_count INTEGER, completed_count INTEGER,
      error_kind TEXT
    );
    CREATE INDEX IF NOT EXISTS samples_run_time ON samples(run_id, sampled_at);
    """)
    columns = {row[1] for row in db.execute("PRAGMA table_info(samples)")}
    if "ingestion_enabled" not in columns:
        db.execute("ALTER TABLE samples ADD COLUMN ingestion_enabled INTEGER")
    db.commit()
    if path.exists():
        path.chmod(0o600)
    return db


def _agent_pid(label):
    result = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{label}"],
                            capture_output=True, text=True, timeout=8)
    if result.returncode:
        return None, False
    match = re.search(r"^\s*pid = (\d+)\s*$", result.stdout, re.MULTILINE)
    return (int(match.group(1)) if match else None), True


def _resource(pid):
    if pid is None:
        return None, None
    result = subprocess.run(["ps", "-p", str(pid), "-o", "%cpu=,rss="],
                            capture_output=True, text=True, timeout=8)
    if result.returncode or not result.stdout.strip():
        return None, None
    fields = result.stdout.split()
    try:
        return float(fields[0].replace(",", ".")), int(fields[1])
    except (ValueError, IndexError):
        return None, None


def _http_ok(url):
    try:
        with urlopen(url, timeout=5) as response:
            return 200 <= response.status < 500
    except (OSError, TimeoutError):
        return False


def _counts(app_db):
    if not Path(app_db).exists():
        return None, None
    try:
        db = sqlite3.connect(f"file:{Path(app_db).resolve()}?mode=ro", uri=True, timeout=3)
        try:
            total, done = db.execute("SELECT COUNT(*), SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) FROM recordings").fetchone()
            return total, done or 0
        finally:
            db.close()
    except (sqlite3.Error, OSError):
        return None, None


def probe(app_db=None):
    from .app import DEFAULT_DB
    from .worker import CONFIG
    web_pid, _ = _agent_pid("local.personal-ai.web")
    ollama_pid, _ = _agent_pid("local.personal-ai.ollama")
    _, worker_loaded = _agent_pid("local.personal-ai.worker")
    web_cpu, web_rss = _resource(web_pid)
    ollama_cpu, ollama_rss = _resource(ollama_pid)
    recordings, completed = _counts(app_db or DEFAULT_DB)
    try:
        ingestion_enabled = int(bool(json.loads(CONFIG.read_text()).get("import_enabled")))
    except (OSError, ValueError, AttributeError):
        ingestion_enabled = 0
    return {
        "web_ok": int(_http_ok("http://127.0.0.1:8765/")),
        "ollama_ok": int(_http_ok("http://127.0.0.1:11434/api/version")),
        "worker_loaded": int(worker_loaded),
        "ingestion_enabled": ingestion_enabled,
        "web_cpu_percent": web_cpu, "web_rss_kb": web_rss,
        "ollama_cpu_percent": ollama_cpu, "ollama_rss_kb": ollama_rss,
        "recording_count": recordings, "completed_count": completed,
        "error_kind": None,
    }


def start(path=STATE, now=None):
    now = time.time() if now is None else now
    with closing(_db(path)) as db:
        active = db.execute("SELECT id FROM runs WHERE status='RUNNING' ORDER BY id DESC LIMIT 1").fetchone()
        if active:
            return active["id"]
        cursor = db.execute("INSERT INTO runs(started_at,status) VALUES (?, 'RUNNING')", (now,))
        return cursor.lastrowid


def restart(path=STATE, now=None):
    now = time.time() if now is None else now
    with closing(_db(path)) as db:
        db.execute("UPDATE runs SET status='ABORTED',ended_at=? WHERE status='RUNNING'", (now,))
    return start(path, now)


def pause(path=STATE, now=None):
    """Save the current sample history and stop the wall-clock trial."""
    now = time.time() if now is None else now
    with closing(_db(path)) as db:
        row = db.execute("SELECT id FROM runs WHERE status='RUNNING' ORDER BY id DESC LIMIT 1").fetchone()
        if row:
            db.execute("UPDATE runs SET status='PAUSED',ended_at=? WHERE id=?", (now, row['id']))
            return row['id']
    return None


def sample(path=STATE, now=None, snapshot=None):
    now = time.time() if now is None else now
    with closing(_db(path)) as db:
        run = db.execute("SELECT id FROM runs WHERE status='RUNNING' ORDER BY id DESC LIMIT 1").fetchone()
        if not run:
            return {"status": "NOT_STARTED"}
        previous = db.execute("SELECT sampled_at FROM samples WHERE run_id=? ORDER BY sampled_at DESC LIMIT 1", (run["id"],)).fetchone()
        if previous and now - previous["sampled_at"] < 30:
            return status(path, now)
    if snapshot is None:
        try:
            snapshot = probe()
        except Exception as exc:
            snapshot = {"web_ok": 0, "ollama_ok": 0, "worker_loaded": 0, "error_kind": type(exc).__name__}
    keys = ("web_ok", "ollama_ok", "worker_loaded", "ingestion_enabled", "web_cpu_percent", "web_rss_kb",
            "ollama_cpu_percent", "ollama_rss_kb", "recording_count", "completed_count", "error_kind")
    with closing(_db(path)) as db:
        db.execute("INSERT INTO samples(run_id,sampled_at," + ",".join(keys) + ") VALUES (?,? ," + ",".join("?" for _ in keys) + ")",
                   (run["id"], now, *(snapshot.get(key) for key in keys)))
    return status(path, now)


def status(path=STATE, now=None, sleep_intervals=None):
    now = time.time() if now is None else now
    if sleep_intervals is None:
        sleep_intervals = _system_sleep_intervals() if Path(path) == STATE else []
    with closing(_db(path)) as db:
        run = db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        if not run:
            return {"status": "NOT_STARTED", "elapsed_seconds": 0, "samples": 0}
        row = db.execute("""SELECT COUNT(*) AS n, MIN(sampled_at) AS first_at,
          MAX(sampled_at) AS last_at,
          SUM(CASE WHEN web_ok=0 OR ollama_ok=0 OR worker_loaded=0 OR ingestion_enabled IS NOT 1 OR error_kind IS NOT NULL THEN 1 ELSE 0 END) AS failures
          FROM samples WHERE run_id=?""", (run["id"],)).fetchone()
        gaps = [r[0] for r in db.execute("SELECT sampled_at FROM samples WHERE run_id=? ORDER BY sampled_at", (run["id"],))]
        effective_now = run["ended_at"] if run["status"] == "PAUSED" else now
        boundaries = [run["started_at"], *gaps, effective_now]
        max_gap = max((b - a for a, b in zip(boundaries, boundaries[1:])), default=0)
        max_awake_gap = max((_awake_gap(a, b, sleep_intervals)
                             for a, b in zip(boundaries, boundaries[1:])), default=0)
        elapsed = max(0, effective_now - run["started_at"])
        verified = (elapsed >= DURATION_SECONDS and row["n"] >= 2 and
                    row["failures"] == 0 and max_awake_gap <= INTERVAL_SECONDS * 2)
        outcome = ("PAUSED" if run["status"] == "PAUSED" else
                   "ABORTED" if run["status"] == "ABORTED" else
                   "PASSED" if verified else "FAILED" if elapsed >= DURATION_SECONDS else
                   "RUNNING_WITH_FAILURES" if row["failures"] else
                   "RUNNING_WITH_GAPS" if max_awake_gap > INTERVAL_SECONDS * 2 else run["status"])
        return {"status": outcome,
                "elapsed_seconds": round(elapsed), "remaining_seconds": max(0, round(DURATION_SECONDS - elapsed)),
                "samples": row["n"], "failed_samples": row["failures"] or 0,
                "max_gap_seconds": round(max_gap),
                "max_awake_gap_seconds": round(max_awake_gap), "run_id": run["id"]}


def install():
    from .services import deploy_runtime
    runtime = deploy_runtime()
    logs = Path.home() / ".local/state/personal-ai"
    logs.mkdir(parents=True, exist_ok=True)
    logs.chmod(0o700)
    plist = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
    plist.parent.mkdir(parents=True, exist_ok=True)
    spec = {"Label": LABEL,
            "ProgramArguments": [str(runtime / ".venv/bin/python"), "-m", "personal_ai.soak", "sample"],
            "WorkingDirectory": str(runtime), "RunAtLoad": True,
            "StartInterval": INTERVAL_SECONDS,
            "StandardOutPath": str(logs / "soak.log"),
            "StandardErrorPath": str(logs / "soak-error.log")}
    with open(plist, "wb") as file:
        plistlib.dump(spec, file)
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", domain, str(plist)], capture_output=True)
    result = subprocess.run(["launchctl", "bootstrap", domain, str(plist)], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"監視器の起動に失敗: {result.stderr.strip()}")
    return plist


def remove():
    plist = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
    if plist.exists():
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(plist)], capture_output=True)
        plist.unlink()
    return plist


def main():
    parser = argparse.ArgumentParser(description="24時間実時間のローカル稼働監視")
    parser.add_argument("action", choices=["start", "restart", "sample", "status", "pause", "install", "remove"])
    args = parser.parse_args()
    result = ({"run_id": start()} if args.action == "start" else
              {"run_id": restart()} if args.action == "restart" else
              sample() if args.action == "sample" else status() if args.action == "status" else
              {"paused_run_id": pause()} if args.action == "pause" else
              {"plist": str(install() if args.action == "install" else remove())})
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
