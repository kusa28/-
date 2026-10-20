"""Conservative, offline suggestions from transcript text.

Transcript text is evidence, never an instruction. Nothing in this module sends
data to a model or promotes a suggestion into a confirmed memory automatically.
"""

from contextlib import closing
import argparse
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import time

from .memory import memory_db


_PATTERNS = (
    ("value", re.compile(r"^(?:私は|僕は|自分は)(.{1,80}?)(?:を|が)大切にして(?:います|いる)[。！!]?\s*$")),
    ("semantic", re.compile(r"^(?:私は|僕は|自分は)(.{1,80}?)が好き(?:です|だ)[。！!]?\s*$")),
    ("current", re.compile(r"^(?:私は|僕は|自分は)明日(.{1,80}?)する予定(?:です|だ)[。！!]?\s*$")),
)
_UNCERTAIN = ("かもしれない", "たぶん", "らしい", "と思う", "？", "?")
AGENT_LABEL = "local.personal-ai.memory-candidates"


def _candidate(text):
    text = text.strip()
    if any(marker in text for marker in _UNCERTAIN):
        return None
    for kind, pattern in _PATTERNS:
        match = pattern.fullmatch(text)
        if match:
            # Store the exact utterance as a proposal. Never rewrite it into a
            # stronger assertion or infer a psychological state from it.
            return kind, text
    return None


def candidate_db(path=None):
    db = memory_db(path)
    db.executescript("""
    CREATE TABLE IF NOT EXISTS memory_candidates (
      id INTEGER PRIMARY KEY,
      recording_id INTEGER NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
      segment_id INTEGER NOT NULL REFERENCES segments(id) ON DELETE CASCADE,
      kind TEXT NOT NULL CHECK(kind IN ('semantic','value','current')),
      statement TEXT NOT NULL,
      evidence TEXT NOT NULL,
      start REAL NOT NULL,
      end REAL NOT NULL,
      status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','ACCEPTED','REJECTED')),
      memory_id INTEGER REFERENCES memories(id) ON DELETE SET NULL,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(segment_id,kind,statement)
    );
    CREATE TABLE IF NOT EXISTS memory_candidate_scans (
      recording_id INTEGER PRIMARY KEY REFERENCES recordings(id) ON DELETE CASCADE,
      scanned_at REAL NOT NULL
    );
    CREATE TRIGGER IF NOT EXISTS invalidate_candidate_scan_after_correction
    AFTER UPDATE OF text ON segments
    WHEN NEW.text != OLD.text
    BEGIN
      DELETE FROM memory_candidate_scans WHERE recording_id=NEW.recording_id;
      UPDATE memories SET active=0,last_checked_at=CURRENT_TIMESTAMP
        WHERE id IN (SELECT memory_id FROM memory_candidates
                     WHERE segment_id=NEW.id AND status='ACCEPTED');
    END;
    """)
    return db


def extract_candidates(recording_id, path=None):
    """Queue explicit first-person statements from a completed recording.

    Repeated calls are idempotent. Speaker identity is not yet known, so even
    first-person candidates require human review before use as memory.
    """
    with closing(candidate_db(path)) as db:
        from .transcript_quality import suspicious_segments
        recording = db.execute("SELECT status FROM recordings WHERE id=?", (recording_id,)).fetchone()
        if not recording or recording["status"] != "done":
            raise ValueError("解析済みの録音IDが必要です")
        db.execute("""UPDATE memory_candidates SET status='REJECTED'
            WHERE recording_id=? AND status='PENDING'
              AND evidence != (SELECT text FROM segments WHERE id=segment_id)""", (recording_id,))
        rows = db.execute("SELECT id,start,end,text,corrected_at FROM segments WHERE recording_id=? ORDER BY start,id", (recording_id,)).fetchall()
        suspicious = suspicious_segments(rows)
        for row in rows:
            if row["id"] in suspicious:
                continue
            proposal = _candidate(row["text"])
            if proposal is None:
                continue
            kind, statement = proposal
            db.execute("""INSERT OR IGNORE INTO memory_candidates
                (recording_id,segment_id,kind,statement,evidence,start,end)
                VALUES (?,?,?,?,?,?,?)""",
                (recording_id, row["id"], kind, statement, row["text"], row["start"], row["end"]))
        db.commit()
        return [dict(row) for row in db.execute(
            "SELECT * FROM memory_candidates WHERE recording_id=? ORDER BY start,id", (recording_id,))]


def list_candidates(path=None, status="PENDING"):
    if status not in ("PENDING", "ACCEPTED", "REJECTED", "ALL"):
        raise ValueError("候補の状態が不正です")
    with closing(candidate_db(path)) as db:
        where = "" if status == "ALL" else "WHERE status=?"
        args = () if status == "ALL" else (status,)
        return [dict(row) for row in db.execute(
            f"SELECT * FROM memory_candidates {where} ORDER BY id", args)]


def scan_pending(path=None, limit=3):
    """Scan completed recordings once; proposals always require human review."""
    with closing(candidate_db(path)) as db:
        ids = [row[0] for row in db.execute("""SELECT r.id FROM recordings r
            LEFT JOIN memory_candidate_scans s ON s.recording_id=r.id
            WHERE r.status='done' AND s.recording_id IS NULL ORDER BY r.id LIMIT ?""", (limit,))]
    total = 0
    for recording_id in ids:
        total += scan_recording(recording_id, path)
    return {"scanned": len(ids), "candidates": total}


def scan_recording(recording_id, path=None):
    """Scan one imported recording and record completion for periodic retries."""
    with closing(candidate_db(path)) as db:
        before = db.execute("SELECT COUNT(*) FROM memory_candidates WHERE recording_id=?", (recording_id,)).fetchone()[0]
    extract_candidates(recording_id, path)
    with closing(candidate_db(path)) as db:
        after = db.execute("SELECT COUNT(*) FROM memory_candidates WHERE recording_id=?", (recording_id,)).fetchone()[0]
        db.execute("INSERT OR REPLACE INTO memory_candidate_scans(recording_id,scanned_at) VALUES (?,?)",
                   (recording_id, time.time()))
        db.commit()
    return after - before


def install_agent():
    from .services import deploy_runtime
    runtime = deploy_runtime()
    logs = Path.home() / ".local/state/personal-ai"
    logs.mkdir(parents=True, exist_ok=True)
    logs.chmod(0o700)
    plist = Path.home() / "Library/LaunchAgents" / f"{AGENT_LABEL}.plist"
    plist.parent.mkdir(parents=True, exist_ok=True)
    spec = {"Label": AGENT_LABEL,
            "ProgramArguments": [str(runtime / ".venv/bin/python"), "-m", "personal_ai.memory_candidates", "scan"],
            "WorkingDirectory": str(runtime), "RunAtLoad": True, "StartInterval": 3600,
            "StandardOutPath": str(logs / "memory-candidates.log"),
            "StandardErrorPath": str(logs / "memory-candidates-error.log")}
    plist.write_bytes(plistlib.dumps(spec))
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", domain, str(plist)], capture_output=True)
    result = subprocess.run(["launchctl", "bootstrap", domain, str(plist)], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"候補生成サービスの起動に失敗: {result.stderr.strip()}")
    return plist


def review_candidate(candidate_id, accept, path=None):
    """Record explicit human approval/rejection and return the resulting ID.

    This method must only be called following a user's review action. It does
    not attempt speaker identification; the reviewer confirms the content.
    """
    with closing(candidate_db(path)) as db:
        row = db.execute("SELECT * FROM memory_candidates WHERE id=?", (candidate_id,)).fetchone()
        if not row or row["status"] != "PENDING":
            raise ValueError("未確認の記憶候補IDが必要です")
        current = db.execute("SELECT text FROM segments WHERE id=?", (row["segment_id"],)).fetchone()
        if not current or current["text"] != row["evidence"]:
            raise ValueError("文字起こしが訂正されています。候補を再生成して確認してください")
        if not accept:
            db.execute("UPDATE memory_candidates SET status='REJECTED' WHERE id=?", (candidate_id,))
            db.commit()
            return None
        source = f'録音#{row["recording_id"]} 発言#{row["segment_id"]} [{row["start"]:.2f}-{row["end"]:.2f}秒]'
        cur = db.execute("""INSERT INTO memories(kind,text,source,evidence,confidence,user_confirmed)
            VALUES (?,?,?,?,?,1)""", (row["kind"], row["statement"], source, row["evidence"], 1.0))
        db.execute("UPDATE memory_candidates SET status='ACCEPTED',memory_id=? WHERE id=?", (cur.lastrowid, candidate_id))
        db.commit()
        return cur.lastrowid


def main():
    parser = argparse.ArgumentParser(description="録音からの記憶候補を本人が確認")
    sub = parser.add_subparsers(dest="action", required=True)
    extract = sub.add_parser("extract")
    extract.add_argument("recording_id", type=int)
    sub.add_parser("list")
    sub.add_parser("scan")
    sub.add_parser("install-agent")
    for action in ("accept", "reject"):
        review = sub.add_parser(action)
        review.add_argument("candidate_id", type=int)
    args = parser.parse_args()
    try:
        if args.action == "extract":
            result = extract_candidates(args.recording_id)
        elif args.action == "list":
            result = list_candidates()
        elif args.action == "scan":
            result = scan_pending()
        elif args.action == "install-agent":
            result = {"plist": str(install_agent())}
        else:
            result = {"memory_id": review_candidate(args.candidate_id, args.action == "accept")}
        print(json.dumps(result, ensure_ascii=False))
    except ValueError as exc:
        parser.exit(1, f"エラー: {exc}\n")


if __name__ == "__main__":
    main()
