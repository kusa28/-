"""Serve a bounded local audio excerpt for reviewing a memory candidate."""

from contextlib import closing
from pathlib import Path
import subprocess

from .memory_candidates import candidate_db


def _render_clip(source_path, start_at, end_at, duration):
    source = Path(source_path)
    if not source.is_file() or source.is_symlink():
        raise ValueError("録音原本を読み込めません")
    start = max(0.0, min(float(start_at) - 2.0, float(duration)))
    end = min(float(duration), float(end_at) + 2.0, start + 20.0)
    if end <= start:
        raise ValueError("再生範囲が不正です")
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{start:.3f}",
         "-i", str(source), "-t", f"{end-start:.3f}", "-vn", "-ac", "1",
         "-ar", "16000", "-f", "wav", "pipe:1"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=35, check=False)
    if result.returncode or not result.stdout.startswith(b"RIFF") or len(result.stdout) > 700000:
        raise ValueError("音声の再生用変換に失敗しました")
    return result.stdout


def candidate_clip(candidate_id, path=None):
    with closing(candidate_db(path)) as db:
        row = db.execute("""SELECT c.start,c.end,r.source_path,r.duration
            FROM memory_candidates c JOIN recordings r ON r.id=c.recording_id
            WHERE c.id=? AND c.status='PENDING' AND r.status='done'""", (candidate_id,)).fetchone()
    if row is None:
        raise ValueError("承認待ちの記憶候補が見つかりません")
    return _render_clip(row["source_path"], row["start"], row["end"], row["duration"])


def segment_clip(segment_id, path=None):
    """Play at most 20 seconds for one flagged transcript segment."""
    from .transcript_quality import suspicious_segments
    with closing(candidate_db(path)) as db:
        row = db.execute("""SELECT s.id,s.recording_id,s.start,s.end,s.text,s.corrected_at,
                   r.source_path,r.duration FROM segments s
                   JOIN recordings r ON r.id=s.recording_id
                   WHERE s.id=? AND r.status='done'""", (segment_id,)).fetchone()
        if row is None:
            raise ValueError("発言IDが見つかりません")
        segments = db.execute("""SELECT id,text,corrected_at FROM segments
            WHERE recording_id=? ORDER BY start,id""", (row["recording_id"],)).fetchall()
    if segment_id not in suspicious_segments(segments):
        raise ValueError("要確認の発言だけを再生できます")
    return _render_clip(row["source_path"], row["start"], row["end"], row["duration"])
