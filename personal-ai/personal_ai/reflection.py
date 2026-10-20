"""Local period index and bounded evidence-driven review cycle.

The log points to user-confirmed memories instead of copying their private text.
The period index does not call an LLM. The scheduled cycle separately reviews
changed evidence using local Ollama without promoting guesses into facts.
"""

import argparse
from contextlib import closing
from datetime import date, timedelta
import json
import os
from pathlib import Path
import plistlib
import subprocess

from .memory import memory_db

AGENT_LABEL = "local.personal-ai.reflection"


def reflection_db(path=None):
    db = memory_db(path)
    db.executescript("""
    CREATE TABLE IF NOT EXISTS reflections (
      id INTEGER PRIMARY KEY,
      period TEXT NOT NULL CHECK(period IN ('daily','weekly')),
      start_date TEXT NOT NULL,
      end_date TEXT NOT NULL,
      conclusion TEXT NOT NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(period,start_date)
    );
    CREATE TABLE IF NOT EXISTS reflection_sources (
      reflection_id INTEGER NOT NULL REFERENCES reflections(id) ON DELETE CASCADE,
      memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
      PRIMARY KEY(reflection_id,memory_id)
    );
    """)
    return db


def _window(period, on_date):
    if period not in ("daily", "weekly"):
        raise ValueError("期間は daily または weekly を指定してください")
    day = date.fromisoformat(on_date) if isinstance(on_date, str) else on_date
    if not isinstance(day, date):
        raise ValueError("日付は YYYY-MM-DD で指定してください")
    start = day if period == "daily" else day - timedelta(days=day.weekday())
    end = start + timedelta(days=1 if period == "daily" else 7)
    return start.isoformat(), end.isoformat()


def run(period, on_date=None, path=None):
    """Log confirmed evidence in [start,end); return None if there is none.

    A completed period is immutable and repeated calls return the same row. A
    later confirmation can be reviewed in the next period; it never silently
    rewrites an earlier thought log.
    """
    start, end = _window(period, on_date or date.today())
    with closing(reflection_db(path)) as db:
        db.execute("BEGIN IMMEDIATE")
        previous = db.execute("SELECT id FROM reflections WHERE period=? AND start_date=?",
                              (period, start)).fetchone()
        if previous:
            db.commit()
            return get(previous["id"], path)
        sources = db.execute("""
            SELECT id FROM memories
            WHERE active=1 AND user_confirmed=1 AND kind!='hypothesis'
              AND substr(COALESCE(occurred_at,observed_at),1,10)>=?
              AND substr(COALESCE(occurred_at,observed_at),1,10)<?
            ORDER BY id
        """, (start, end)).fetchall()
        if not sources:
            db.commit()
            return None
        # No interpretation is generated without human review. The source
        # links retain the auditable basis without duplicating private text.
        conclusion = "期間内の本人確認済み記憶を確認。新しい推測は追加していません。"
        cur = db.execute("INSERT INTO reflections(period,start_date,end_date,conclusion) VALUES (?,?,?,?)",
                         (period, start, end, conclusion))
        db.executemany("INSERT INTO reflection_sources(reflection_id,memory_id) VALUES (?,?)",
                       [(cur.lastrowid, row["id"]) for row in sources])
        db.commit()
        return get(cur.lastrowid, path)


def get(reflection_id, path=None):
    with closing(reflection_db(path)) as db:
        row = db.execute("SELECT * FROM reflections WHERE id=?", (reflection_id,)).fetchone()
        if not row:
            raise ValueError("振り返りIDが見つかりません")
        result = dict(row)
        result["sources"] = [dict(source) for source in db.execute("""
            SELECT m.id,m.kind,m.text,m.source,m.occurred_at,m.observed_at
            FROM reflection_sources rs JOIN memories m ON m.id=rs.memory_id
            WHERE rs.reflection_id=? AND m.active=1 AND m.user_confirmed=1
            ORDER BY m.id
        """, (reflection_id,))]
        return result


def list_reflections(path=None):
    with closing(reflection_db(path)) as db:
        ids = [row[0] for row in db.execute("SELECT id FROM reflections ORDER BY start_date DESC,id DESC")]
    return [get(rid, path) for rid in ids]


def run_due(today=None, path=None):
    """Catch up after sleep without inventing thoughts on empty days."""
    today = today or date.today()
    created = 0
    for days_ago in range(7, -1, -1):
        day = today - timedelta(days=days_ago)
        created += int(run("daily", day, path) is not None)
    for weeks_ago in range(4, -1, -1):
        day = today - timedelta(weeks=weeks_ago)
        created += int(run("weekly", day, path) is not None)
    return {"periods_with_evidence": created}


def run_cycle(today=None, path=None):
    """One bounded hypothesis review after maintaining the period index."""
    from .deliberation import run as review
    return {**run_due(today, path), "deliberation": review(path)}


def install_agent():
    from .services import deploy_runtime
    runtime = deploy_runtime()
    logs = Path.home() / ".local/state/personal-ai"
    logs.mkdir(parents=True, exist_ok=True)
    logs.chmod(0o700)
    plist = Path.home() / "Library/LaunchAgents" / f"{AGENT_LABEL}.plist"
    plist.parent.mkdir(parents=True, exist_ok=True)
    spec = {"Label": AGENT_LABEL,
            "ProgramArguments": [str(runtime / ".venv/bin/python"), "-m", "personal_ai.reflection", "cycle"],
            "WorkingDirectory": str(runtime), "RunAtLoad": True, "StartInterval": 21600,
            "StandardOutPath": str(logs / "reflection.log"),
            "StandardErrorPath": str(logs / "reflection-error.log")}
    plist.write_bytes(plistlib.dumps(spec))
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", domain, str(plist)], capture_output=True)
    result = subprocess.run(["launchctl", "bootstrap", domain, str(plist)], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"振り返りサービスの起動に失敗: {result.stderr.strip()}")
    return plist


def main():
    parser = argparse.ArgumentParser(description="根拠付きローカル振り返り")
    parser.add_argument("period", choices=("daily", "weekly", "list", "due", "cycle", "install-agent"))
    parser.add_argument("--date", help="YYYY-MM-DD。週次は指定日を含む月曜始まりの週")
    args = parser.parse_args()
    try:
        if args.period == "list":
            for row in list_reflections():
                print(f'#{row["id"]} {row["period"]} {row["start_date"]}: '
                      f'{row["conclusion"]} 根拠 {len(row["sources"])} 件')
        elif args.period == "due":
            print(json.dumps(run_due(), ensure_ascii=False))
        elif args.period == "cycle":
            print(json.dumps(run_cycle(), ensure_ascii=False))
        elif args.period == "install-agent":
            print(json.dumps({"plist": str(install_agent())}, ensure_ascii=False))
        else:
            row = run(args.period, args.date)
            print("対象期間に新情報なし" if row is None else
                  f'#{row["id"]} {row["start_date"]}: {row["conclusion"]} 根拠 {len(row["sources"])} 件')
    except ValueError as exc:
        parser.exit(1, f"エラー: {exc}\n")


if __name__ == "__main__":
    main()
