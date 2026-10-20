"""Read-only, source-linked timeline units; not semantic event understanding.

Each unit joins an utterance interval with model speaker turns and overlapping
sound-analysis windows. It never infers identity, location, emotion, or facts
from text. Building from one SQLite snapshot avoids stale copied conclusions.
"""

import argparse
from contextlib import closing
from html import escape
import json
import math

from .app import database
from .speaker_alignment import assign_speaker
from .transcript_quality import suspicious_segments


def _exists(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _valid_interval(row, duration):
    return (all(type(row[key]) in (int, float) and math.isfinite(row[key]) for key in ("start", "end"))
            and 0 <= row["start"] < row["end"] <= duration)


def timeline(recording_id, db_path=None, *, offset=0, limit=50):
    if type(recording_id) is not int or recording_id <= 0:
        raise ValueError("録音IDが不正です")
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("表示範囲が不正です（1～100区間）")
    with closing(database(db_path)) as db:
        db.execute("BEGIN")
        recording = db.execute("SELECT id,duration,status,created_at FROM recordings WHERE id=?", (recording_id,)).fetchone()
        if not recording or recording["status"] != "done":
            raise ValueError("解析済みの録音IDが必要です")
        # Full neighboring text is needed for the existing consecutive-loop
        # detector, even when the displayed page ends in the middle of a run.
        all_segments = [dict(row) for row in db.execute(
            "SELECT * FROM segments WHERE recording_id=? ORDER BY start,id", (recording_id,))]
        suspicious = suspicious_segments(all_segments)
        segments = all_segments[offset:offset+limit]
        start = min((s["start"] for s in segments), default=0)
        end = max((s["end"] for s in segments), default=0)
        turns = [dict(row) for row in db.execute("""SELECT * FROM speaker_turns
            WHERE recording_id=? AND start<? AND end>? ORDER BY start,end,id""",
            (recording_id, end, start))] if _exists(db, "speaker_turns") else []
        sounds = [dict(row) for row in db.execute("""SELECT * FROM environment_sound
            WHERE recording_id=? AND start<? AND end>? ORDER BY start,end,id""",
            (recording_id, end, start))] if _exists(db, "environment_sound") else []
        invalid_turns = sum(not _valid_interval(t, recording["duration"]) for t in turns)
        invalid_windows = sum(not _valid_interval(s, recording["duration"]) for s in sounds)
        turns = [t for t in turns if _valid_interval(t, recording["duration"])]
        sounds = [s for s in sounds if _valid_interval(s, recording["duration"])]
        raw = db.execute("SELECT result_json FROM audio_observations WHERE recording_id=?", (recording_id,)).fetchone()
        acoustic = None
        if raw:
            payload = json.loads(raw[0])
            if not isinstance(payload, dict) or payload.get("kind") != "observation":
                raise ValueError("観測データの種類が不正です")
            measured = {}
            for key in ("duration_seconds", "rms_dbfs", "peak_dbfs", "silence_seconds"):
                value = payload.get(key)
                if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
                    raise ValueError("音響観測値が不正です")
                measured[key] = value
            acoustic = {"kind": "observation", "scope": "whole_recording", "values": measured,
                        "source": {"recording_id": recording_id, "table": "audio_observations"}}
        units = []
        for segment in segments:
            a, b = segment["start"], segment["end"]
            timing_valid = _valid_interval(segment, recording["duration"])
            assigned = (assign_speaker(segment, [t for t in turns if t["exclusive"]],
                                      [t for t in turns if not t["exclusive"]]) if timing_valid else
                        {"speaker_label": "UNKNOWN", "overlap": False, "speaker_source": "unavailable"})
            source = {"recording_id": recording_id, "segment_id": segment["id"], "start": a, "end": b}
            flagged = segment["id"] in suspicious or not timing_valid
            text = None if flagged else segment["text"][:2000]
            overlapping = []
            for sound in sounds:
                if not timing_valid:
                    break
                left, right = max(a, sound["start"]), min(b, sound["end"])
                if left >= right:
                    continue
                if sound["kind"] != "model_prediction":
                    raise ValueError("環境音の推測を観測事実として扱えません")
                overlapping.append({"kind": "model_prediction", "verified": False,
                    "label": sound["identifier"], "confidence": sound["confidence"], "model": sound["model"],
                    "overlap_start": left, "overlap_end": right,
                    "source": {"recording_id": recording_id, "table": "environment_sound", "id": sound["id"],
                               "start": sound["start"], "end": sound["end"]}})
            units.append({"source": source, "source_timing_valid": timing_valid,
                "utterance": {"kind": "human_corrected_transcript" if segment["corrected_at"] else "asr_transcript",
                    "text": text, "uncertain": flagged,
                    "reason": "時刻が録音範囲外または長さ0です" if not timing_valid else suspicious.get(segment["id"]),
                    "corrected_at": segment["corrected_at"],
                    "truncated": not flagged and len(segment["text"]) > 2000},
                "speaker": {**assigned, "scope_recording_id": recording_id, "person_id": None,
                    "source_turn_ids": [t["id"] for t in turns if timing_valid and t["start"] < b and t["end"] > a]},
                "sound_window_predictions": overlapping,
                "interpretations": [], "location": None, "emotion": None})
        return {"version": 1, "recording": {"id": recording_id, "duration": recording["duration"],
                "recorded_at": None, "ingested_at": recording["created_at"]},
                "offset": offset, "total_segments": len(all_segments), "units": units,
                "next_offset": offset+limit if offset+limit < len(all_segments) else None,
                "recording_acoustic_observations": acoustic,
                "excluded_invalid_timing": {"speaker_turns": invalid_turns, "sound_windows": invalid_windows},
                "note": "区間の対応付けです。人物・場所・感情・出来事の意味を確定したものではありません。"}


def render_timeline(result):
    e = lambda value: escape(str(value), quote=True)
    rid = result["recording"]["id"]
    parts = ['<!doctype html><html lang="ja"><meta charset="utf-8"><title>音声の統合タイムライン</title>',
             '<a href="/">録音へ戻る</a><h1>音声の統合タイムライン</h1>',
             '<p>発言・匿名話者・環境音の解析窓を時刻で対応付けています。話者の身元、場所、感情は不明です。'
             '環境音はモデルの候補で、同時刻に実際に鳴ったと確認したものではありません。</p>']
    acoustic = result["recording_acoustic_observations"]
    if acoustic:
        parts.append('<p>録音全体の音響観測（個別発言の測定値ではありません）: ' + e(json.dumps(acoustic["values"], ensure_ascii=False)) + '</p>')
    for unit in result["units"]:
        source, utterance, speaker = unit["source"], unit["utterance"], unit["speaker"]
        label = "話者不明" if speaker["speaker_label"] == "UNKNOWN" else "匿名の推定話者 " + speaker["speaker_label"]
        text = ("文字起こしが不確実です（" + utterance["reason"] + "）。原音を確認してください。"
                if utterance["uncertain"] else utterance["text"])
        parts.append(f'<section><h2>{e(source["start"])}–{e(source["end"])}秒</h2>'
            f'<p>{e(label)}{"（重なりの推定あり）" if speaker["overlap"] else ""}</p><p>{e(text)}</p>'
            f'<p>文字起こし: {"人による訂正あり" if utterance["corrected_at"] else "音声認識結果（未確認）"}'
            f' · 根拠発言 #{e(source["segment_id"])} '
            f'<a href="/transcript/clip?id={e(source["segment_id"])}">原音を確認</a></p>')
        if utterance["truncated"]:
            parts.append('<p>長い発言の一部を表示しています。全文は文字起こし画面で確認してください。</p>')
        if unit["sound_window_predictions"]:
            parts.append('<p>重なる解析窓の環境音候補（未確認）:</p><ul>')
            for prediction in unit["sound_window_predictions"][:20]:
                parts.append(f'<li>{e(prediction["label"])} · モデル出力 {e(prediction["confidence"])} '
                             f'· 根拠の窓 {e(prediction["source"]["start"])}–{e(prediction["source"]["end"])}秒</li>')
            if len(unit["sound_window_predictions"]) > 20:
                parts.append('<li>候補は先頭20件を表示しています</li>')
            parts.append('</ul>')
        parts.append('</section>')
    if result["next_offset"] is not None:
        parts.append(f'<a href="/experience?id={e(rid)}&amp;offset={e(result["next_offset"])}">次の区間へ</a>')
    return "".join(parts) + '</html>'


def main():
    parser = argparse.ArgumentParser(description="録音の時刻付き統合資料をローカル表示")
    parser.add_argument("recording_id", type=int)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    try:
        print(json.dumps(timeline(args.recording_id, offset=args.offset, limit=args.limit), ensure_ascii=False))
    except (ValueError, OSError) as exc:
        parser.exit(1, f"統合資料を取得できません: {type(exc).__name__}\n")


if __name__ == "__main__":
    main()
