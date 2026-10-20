"""Opt-in one-shot folder scan for a macOS LaunchAgent."""

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import plistlib
import stat
import subprocess
import sys
import tempfile
import time

from .app import FORMATS, database, import_recording

CONFIG = Path.home() / ".config/personal-ai/config.json"
AGENT_LABEL = "local.personal-ai.worker"


def _is_dataless(file_stat):
    """macOS marks iCloud placeholders with SF_DATALESS despite a logical size."""
    flag = getattr(stat, "SF_DATALESS", 0)
    return bool(flag and getattr(file_stat, "st_flags", 0) & flag)


def configure(folder, enabled=False, config_path=CONFIG):
    path = Path(folder).expanduser().resolve(strict=True)
    if not path.is_dir():
        raise ValueError("録音フォルダを指定してください")
    config_path = Path(config_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    data = {"watch_folder": str(path), "import_enabled": bool(enabled), "max_files_per_run": 1}
    fd, temporary = tempfile.mkstemp(prefix=".config-", dir=config_path.parent)
    try:
        with os.fdopen(fd, "w") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, config_path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return data


def _state(db_path):
    db = database(db_path)
    db.execute("""CREATE TABLE IF NOT EXISTS worker_failures (
      source_path TEXT PRIMARY KEY, attempts INTEGER NOT NULL,
      next_retry_at REAL NOT NULL, error_type TEXT NOT NULL)""")
    db.commit()
    return db


def run_once(config_path=CONFIG, min_age_seconds=120, now=None, db_path=None):
    config_path = Path(config_path)
    if not config_path.exists():
        return {"status": "not_configured", "processed": 0}
    config = json.loads(config_path.read_text())
    if not config.get("import_enabled"):
        return {"status": "disabled", "processed": 0}
    folder = Path(config["watch_folder"]).expanduser().resolve(strict=True)
    if not folder.is_dir():
        raise ValueError("設定した録音フォルダが見つかりません")
    now = time.time() if now is None else now
    with closing(_state(db_path)) as db:
        done = {(r["source_path"], r["size_bytes"], r["modified_at"]) for r in db.execute("SELECT source_path,size_bytes,modified_at FROM recordings WHERE status='done'")}
        retry_after = {r["source_path"]: r["next_retry_at"] for r in db.execute("SELECT source_path,next_retry_at FROM worker_failures")}
    candidates = []
    skipped_icloud = 0
    for path in folder.iterdir():
        if path.name.startswith(".") or path.suffix.lower() not in FORMATS or path.is_symlink() or not path.is_file():
            continue
        stat = path.stat()
        if _is_dataless(stat):
            skipped_icloud += 1
            continue
        if stat.st_size == 0 or now - stat.st_mtime < min_age_seconds:
            continue
        if (str(path), stat.st_size, str(stat.st_mtime)) in done or retry_after.get(str(path), 0) > now:
            continue
        candidates.append(path)
    candidates.sort(key=lambda path: (path.stat().st_mtime, path.name))
    processed = 0
    errors = 0
    limit = max(1, min(int(config.get("max_files_per_run", 1)), 10))
    for path in candidates[:limit]:
        try:
            _, duplicate = import_recording(path, db_path=db_path)
            processed += int(not duplicate)
            with closing(_state(db_path)) as db:
                db.execute("DELETE FROM worker_failures WHERE source_path=?", (str(path),))
                db.commit()
        except (OSError, RuntimeError, ValueError) as exc:
            errors += 1
            with closing(_state(db_path)) as db:
                prior = db.execute("SELECT attempts FROM worker_failures WHERE source_path=?", (str(path),)).fetchone()
                attempts = (prior["attempts"] if prior else 0) + 1
                delay = min(86400, 60 * 2 ** min(attempts, 10))
                db.execute("INSERT INTO worker_failures(source_path,attempts,next_retry_at,error_type) VALUES (?,?,?,?) ON CONFLICT(source_path) DO UPDATE SET attempts=excluded.attempts,next_retry_at=excluded.next_retry_at,error_type=excluded.error_type",
                           (str(path), attempts, now + delay, type(exc).__name__))
                db.commit()
            print(f"取り込み失敗: {type(exc).__name__}", file=sys.stderr)
    return {"status": "ok", "processed": processed, "errors": errors,
            "examined": min(len(candidates), limit), "skipped_icloud": skipped_icloud}


def install_launch_agent():
    from .services import deploy_runtime
    root = deploy_runtime()
    logs = Path.home() / ".local/state/personal-ai"
    logs.mkdir(parents=True, exist_ok=True)
    logs.chmod(0o700)
    plist = Path.home() / "Library/LaunchAgents" / f"{AGENT_LABEL}.plist"
    plist.parent.mkdir(parents=True, exist_ok=True)
    spec = {"Label": AGENT_LABEL, "ProgramArguments": [str(root / ".venv/bin/python"), "-m", "personal_ai.worker", "run-once"],
            "WorkingDirectory": str(root), "RunAtLoad": True, "StartInterval": 900,
            "EnvironmentVariables": {"PATH": f"{Path.home()}/.local/bin:/opt/homebrew/bin:/usr/bin:/bin"},
            "StandardOutPath": str(logs / "worker.log"), "StandardErrorPath": str(logs / "worker-error.log")}
    with open(plist, "wb") as file:
        plistlib.dump(spec, file)
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", domain, str(plist)], capture_output=True)
    result = subprocess.run(["launchctl", "bootstrap", domain, str(plist)], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"LaunchAgent起動失敗: {result.stderr.strip()}")
    return plist


def remove_launch_agent():
    plist = Path.home() / "Library/LaunchAgents" / f"{AGENT_LABEL}.plist"
    if plist.exists():
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(plist)], capture_output=True)
        plist.unlink()
    return plist


def main():
    parser = argparse.ArgumentParser(description="許可したフォルダを一度だけ検査")
    sub = parser.add_subparsers(dest="action", required=True)
    setup = sub.add_parser("configure")
    setup.add_argument("folder")
    setup.add_argument("--enable", action="store_true")
    sub.add_parser("run-once")
    sub.add_parser("install-agent")
    sub.add_parser("remove-agent")
    args = parser.parse_args()
    try:
        if args.action == "configure":
            result = configure(args.folder, args.enable)
        elif args.action == "run-once":
            result = run_once()
        elif args.action == "install-agent":
            result = {"installed": str(install_launch_agent())}
        else:
            result = {"removed": str(remove_launch_agent())}
        print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        parser.exit(1, f"エラー: {exc}\n")


if __name__ == "__main__":
    main()
