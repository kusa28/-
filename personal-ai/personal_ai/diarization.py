"""Anonymous speaker turns with an optional, local pyannote backend."""

import argparse
from contextlib import closing
import json
import math
import os

from .app import database

MODEL = "pyannote/speaker-diarization-community-1"


def _turns(annotation, exclusive=False):
    rows = []
    for interval, _, speaker in annotation.itertracks(yield_label=True):
        start, end = float(interval.start), float(interval.end)
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            continue
        rows.append({"start": round(start, 3), "end": round(end, 3),
                     "speaker": str(speaker), "exclusive": bool(exclusive)})
    return sorted(rows, key=lambda row: (row["start"], row["end"], row["speaker"]))


def run_local(path, token):
    """Download the gated model using the user's HF access, then infer locally.

    No recording is uploaded. The caller must already have accepted the model
    terms in Hugging Face; this function never handles consent for them.
    """
    if not token:
        raise ValueError("Hugging Faceの本人認証が必要です")
    os.environ["PYANNOTE_METRICS_ENABLED"] = "0"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    try:
        from pyannote.audio import Pipeline
    except ImportError as exc:
        raise RuntimeError("pyannote.audio をインストールしてください") from exc
    pipeline = Pipeline.from_pretrained(MODEL, token=token)
    result = pipeline(str(path))
    regular = _turns(result.speaker_diarization)
    exclusive = _turns(result.exclusive_speaker_diarization, exclusive=True)
    return regular + exclusive


def store_turns(recording_id, rows, db_path=None, model=MODEL):
    with closing(database(db_path)) as db:
        recording = db.execute("SELECT id FROM recordings WHERE id=? AND status='done'", (recording_id,)).fetchone()
        if not recording:
            raise ValueError("解析済みの録音IDが必要です")
        db.execute("""CREATE TABLE IF NOT EXISTS speaker_turns (
          id INTEGER PRIMARY KEY, recording_id INTEGER NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
          start REAL NOT NULL, end REAL NOT NULL, speaker_label TEXT NOT NULL,
          exclusive INTEGER NOT NULL, overlap INTEGER NOT NULL, model TEXT NOT NULL,
          CHECK(start >= 0 AND end > start), CHECK(exclusive IN (0,1)), CHECK(overlap IN (0,1))
        )""")
        validated = []
        for row in rows:
            start, end = float(row["start"]), float(row["end"])
            label = str(row["speaker"])
            exclusive = bool(row.get("exclusive", False))
            if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
                raise ValueError("話者の時刻が不正です")
            if not label.startswith("SPEAKER_") or len(label) > 32:
                raise ValueError("匿名話者ラベルが不正です")
            validated.append((start, end, label, exclusive))
        records = []
        for start, end, label, exclusive in validated:
            overlap = int(not exclusive and any(
                not other_ex and other_label != label and other_start < end and start < other_end
                for other_start, other_end, other_label, other_ex in validated
            ))
            records.append((recording_id, start, end, label, int(exclusive), overlap, model))
        db.execute("DELETE FROM speaker_turns WHERE recording_id=?", (recording_id,))
        db.executemany("""INSERT INTO speaker_turns
          (recording_id,start,end,speaker_label,exclusive,overlap,model)
          VALUES (?,?,?,?,?,?,?)""", records)
        db.commit()
        return len(records)


def list_turns(recording_id, db_path=None, exclusive=False):
    with closing(database(db_path)) as db:
        exists = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='speaker_turns'").fetchone()
        if not exists:
            return []
        return [dict(row) for row in db.execute(
            "SELECT start,end,speaker_label,exclusive,overlap,model FROM speaker_turns "
            "WHERE recording_id=? AND exclusive=? ORDER BY start,end,id",
            (recording_id, int(exclusive)))]


def main():
    parser = argparse.ArgumentParser(description="匿名話者のローカル分離")
    sub = parser.add_subparsers(dest="action", required=True)
    infer = sub.add_parser("run")
    infer.add_argument("recording_id", type=int)
    show = sub.add_parser("list")
    show.add_argument("recording_id", type=int)
    show.add_argument("--exclusive", action="store_true")
    args = parser.parse_args()
    try:
        if args.action == "list":
            print(json.dumps(list_turns(args.recording_id, exclusive=args.exclusive), ensure_ascii=False))
            return
        token = os.environ.get("HF_TOKEN")
        if not token:
            raise ValueError("HF_TOKEN環境変数が必要です。トークンをコマンド引数に入れないでください")
        with closing(database()) as db:
            row = db.execute("SELECT source_path FROM recordings WHERE id=? AND status='done'", (args.recording_id,)).fetchone()
            if not row:
                raise ValueError("解析済みの録音IDが必要です")
            path = row["source_path"]
        count = store_turns(args.recording_id, run_local(path, token))
        print(json.dumps({"recording_id": args.recording_id, "turns": count}, ensure_ascii=False))
    except (OSError, RuntimeError, ValueError) as exc:
        parser.exit(1, f"エラー: {exc}\n")


if __name__ == "__main__":
    main()
