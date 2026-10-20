"""Apple SoundAnalysis v1 predictions, kept separate from acoustic observations.

This module never sends audio to a network service. A predicted label is not a
fact about the recording's location, the speaker, or anyone's emotional state.
"""

import argparse
from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile

from .app import database


MODEL = "Apple SoundAnalysis v1"
SOURCE = Path(__file__).resolve().parent.parent / "native" / "sound_analysis.swift"
CACHE = Path.home() / ".cache" / "personal-ai"


def _binary():
    """Compile locally into a private cache, keyed by source content."""
    if not SOURCE.is_file():
        raise RuntimeError("SoundAnalysisのSwiftソースがありません")
    digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()[:16]
    CACHE.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = CACHE / f"sound-analysis-{digest}"
    if path.is_file():
        return path
    with tempfile.TemporaryDirectory(prefix="personal-ai-swift-") as directory:
        temporary = Path(directory) / "sound-analysis"
        result = subprocess.run(["xcrun", "swiftc", str(SOURCE), "-o", str(temporary)],
                                capture_output=True, text=True, timeout=120, check=False)
        if result.returncode:
            raise RuntimeError("Swiftのコンパイルに失敗しました: " + result.stderr[-500:])
        try:
            os.link(temporary, path)
        except FileExistsError:
            pass
    return path


def _validate(payload):
    if not isinstance(payload, dict) or payload.get("model") != MODEL:
        raise ValueError("環境音モデルの出力形式が不正です")
    if payload.get("kind", "model_prediction") != "model_prediction":
        raise ValueError("環境音の推測を観測事実として扱えません")
    windows = payload.get("windows")
    if not isinstance(windows, list):
        raise ValueError("環境音の時刻情報が不正です")
    cleaned = []
    for item in windows:
        start, end = float(item["start"]), float(item["end"])
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise ValueError("環境音の時刻情報が不正です")
        predictions = []
        for prediction in item["predictions"]:
            label = prediction["identifier"]
            confidence = float(prediction["confidence"])
            if (not isinstance(label, str) or not label or len(label) > 128 or
                    not math.isfinite(confidence) or not 0 <= confidence <= 1):
                raise ValueError("環境音の推定値が不正です")
            predictions.append({"identifier": label, "confidence": confidence})
        cleaned.append({"start": start, "end": end, "predictions": predictions})
    return {"kind": "model_prediction", "verified": False, "model": MODEL, "windows": cleaned,
            "note": "モデルの候補です。場所・状況・感情を確定しません。"}


def analyze(path, timeout=300):
    """Return timestamped labels for a caller-selected local audio file."""
    source = Path(path).expanduser().resolve(strict=True)
    if not source.is_file():
        raise ValueError("音声ファイルを指定してください")
    result = subprocess.run([str(_binary()), str(source)], capture_output=True,
                            text=True, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError("SoundAnalysisの推論に失敗しました: " + result.stderr[-500:])
    return _validate(json.loads(result.stdout))


def store(recording_id, result, db_path=None):
    """Atomically replace a recording's predictions in the local database."""
    validated = _validate(result)
    with closing(database(db_path)) as db:
        row = db.execute("SELECT id FROM recordings WHERE id=? AND status='done'", (recording_id,)).fetchone()
        if row is None:
            raise ValueError("解析済みの録音IDが必要です")
        db.execute("""CREATE TABLE IF NOT EXISTS environment_sound (
          id INTEGER PRIMARY KEY, recording_id INTEGER NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
          start REAL NOT NULL, end REAL NOT NULL, identifier TEXT NOT NULL,
          confidence REAL NOT NULL, model TEXT NOT NULL, kind TEXT NOT NULL,
          CHECK(start >= 0 AND end > start), CHECK(confidence >= 0 AND confidence <= 1)
        )""")
        records = [(recording_id, window["start"], window["end"], pred["identifier"],
                    pred["confidence"], MODEL, "model_prediction")
                   for window in validated["windows"] for pred in window["predictions"]]
        db.execute("DELETE FROM environment_sound WHERE recording_id=?", (recording_id,))
        db.executemany("""INSERT INTO environment_sound
          (recording_id,start,end,identifier,confidence,model,kind)
          VALUES (?,?,?,?,?,?,?)""", records)
        db.commit()
        return len(records)


def list_predictions(recording_id, db_path=None):
    with closing(database(db_path)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='environment_sound'").fetchone():
            return []
        return [dict(row) | {"verified": False} for row in db.execute(
            "SELECT start,end,identifier,confidence,model,kind FROM environment_sound "
            "WHERE recording_id=? ORDER BY start,end,confidence DESC,id", (recording_id,))]


def main():
    parser = argparse.ArgumentParser(description="Appleのローカル環境音モデル")
    sub = parser.add_subparsers(dest="action", required=True)
    run = sub.add_parser("run")
    run.add_argument("recording_id", type=int)
    show = sub.add_parser("list")
    show.add_argument("recording_id", type=int)
    args = parser.parse_args()
    try:
        if args.action == "list":
            print(json.dumps(list_predictions(args.recording_id), ensure_ascii=False))
            return
        with closing(database()) as db:
            row = db.execute("SELECT source_path FROM recordings WHERE id=? AND status='done'", (args.recording_id,)).fetchone()
            if row is None:
                raise ValueError("解析済みの録音IDが必要です")
            path = row["source_path"]
        count = store(args.recording_id, analyze(path))
        print(json.dumps({"recording_id": args.recording_id, "predictions": count}, ensure_ascii=False))
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        parser.exit(1, f"エラー: {exc}\n")


if __name__ == "__main__":
    main()
