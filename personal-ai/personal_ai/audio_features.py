"""Local, read-only acoustic observations. No emotion or location inference."""

from array import array
import math
from pathlib import Path
import shutil
import subprocess
import sys


SAMPLE_RATE = 16000
WINDOW_SECONDS = 0.1
SILENCE_DBFS = -40.0
MIN_SILENCE_SECONDS = 0.5


def _dbfs(amplitude):
    return 20 * math.log10(amplitude) if amplitude > 0 else None


def _silence_range(start, end, minimum):
    if end - start >= minimum - 1e-9:
        return {"start": round(start, 3), "end": round(end, 3)}
    return None


def _speech_rate(segments):
    """Characters per timestamped speech second; this is not actual phoneme rate."""
    if not segments:
        return None
    intervals = []
    characters = 0
    for item in segments:
        start, end = float(item["start"]), float(item["end"])
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start:
            raise ValueError("発話の時刻が不正です")
        if end == start:
            continue
        intervals.append((start, end))
        characters += sum(not ch.isspace() for ch in str(item.get("text", "")))
    if not intervals:
        return None
    intervals.sort()
    merged = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    seconds = sum(end - start for start, end in merged)
    return round(characters / seconds, 3) if seconds else None


def analyze_audio(path, segments=None, silence_dbfs=SILENCE_DBFS,
                  min_silence_seconds=MIN_SILENCE_SECONDS):
    """Decode in bounded chunks and return measurements only.

    ``segments`` may be timestamped ASR rows. Its text is used only to count
    characters and is never returned or sent to a remote service.
    """
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError("音声ファイルを指定してください")
    if not shutil.which("ffmpeg"):
        raise RuntimeError("FFmpeg が必要です")
    if not math.isfinite(silence_dbfs) or silence_dbfs > 0:
        raise ValueError("無音のしきい値が不正です")
    if not math.isfinite(min_silence_seconds) or min_silence_seconds <= 0:
        raise ValueError("最小無音時間が不正です")

    command = ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path),
               "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "pipe:1"]
    process = subprocess.Popen(command, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    window_samples = int(SAMPLE_RATE * WINDOW_SECONDS)
    samples = 0
    squares = 0
    peak = 0
    quiet_start = None
    quiet_ranges = []
    pending = b""
    try:
        while True:
            block = process.stdout.read(window_samples * 2)
            if not block:
                break
            pending += block
            while len(pending) >= window_samples * 2:
                raw, pending = pending[:window_samples * 2], pending[window_samples * 2:]
                values = array("h")
                values.frombytes(raw)
                if sys.byteorder != "little":
                    values.byteswap()
                start = samples / SAMPLE_RATE
                window_peak = max(abs(value) for value in values)
                window_squares = sum(value * value for value in values)
                samples += len(values)
                squares += window_squares
                peak = max(peak, window_peak)
                window_rms = math.sqrt(window_squares / len(values)) / 32768
                is_quiet = window_rms == 0 or _dbfs(window_rms) <= silence_dbfs
                if is_quiet and quiet_start is None:
                    quiet_start = start
                elif not is_quiet and quiet_start is not None:
                    interval = _silence_range(quiet_start, start, min_silence_seconds)
                    if interval:
                        quiet_ranges.append(interval)
                    quiet_start = None
        if pending:
            if len(pending) % 2:
                raise RuntimeError("音声データが途中で途切れました")
            values = array("h")
            values.frombytes(pending)
            if sys.byteorder != "little":
                values.byteswap()
            start = samples / SAMPLE_RATE
            window_squares = sum(value * value for value in values)
            samples += len(values)
            squares += window_squares
            peak = max(peak, max(abs(value) for value in values))
            window_rms = math.sqrt(window_squares / len(values)) / 32768
            is_quiet = window_rms == 0 or _dbfs(window_rms) <= silence_dbfs
            if is_quiet and quiet_start is None:
                quiet_start = start
            elif not is_quiet and quiet_start is not None:
                interval = _silence_range(quiet_start, start, min_silence_seconds)
                if interval:
                    quiet_ranges.append(interval)
                quiet_start = None
        stderr = process.stderr.read(4096)
        if process.wait() != 0:
            raise RuntimeError("FFmpeg の音声解析に失敗しました: " + stderr.decode(errors="replace")[:300])
        if not samples:
            raise ValueError("音声サンプルがありません")
        if quiet_start is not None:
            interval = _silence_range(quiet_start, samples / SAMPLE_RATE, min_silence_seconds)
            if interval:
                quiet_ranges.append(interval)
        duration = samples / SAMPLE_RATE
        return {
            "kind": "observation",
            "duration_seconds": round(duration, 3),
            "rms_dbfs": None if squares == 0 else round(_dbfs(math.sqrt(squares / samples) / 32768), 2),
            "peak_dbfs": None if peak == 0 else round(_dbfs(peak / 32768), 2),
            "silence_threshold_dbfs": silence_dbfs,
            "silence_intervals": quiet_ranges,
            "silence_seconds": round(sum(x["end"] - x["start"] for x in quiet_ranges), 3),
            "asr_characters_per_speech_second": _speech_rate(segments),
            "notes": ["音量と無音は観測値です。感情や心理状態を示しません。",
                      "話速は文字起こしの文字数を発話時刻で割った代理値です。"],
        }
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdout.close()
        process.stderr.close()
