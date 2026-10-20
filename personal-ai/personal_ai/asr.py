"""Replaceable local speech-recognition backends."""

import json
from pathlib import Path
import subprocess
import tempfile


def transcribe_chunk(path, backend, model):
    if backend == "mlx":
        try:
            import mlx_whisper
        except ImportError as exc:
            raise RuntimeError("mlx-whisper がありません。READMEの準備手順を確認してください") from exc
        result = mlx_whisper.transcribe(str(path), path_or_hf_repo=model, language="ja",
                                        verbose=False, condition_on_previous_text=False)
        rows = []
        for segment in result["segments"]:
            text = segment["text"].strip()
            if (segment.get("no_speech_prob", 0) >= 0.9 and
                    segment.get("compression_ratio", 0) >= 5):
                text = "［文字起こし不確実：反復と無音判定。原音確認が必要］"
            rows.append((float(segment["start"]), float(segment["end"]), text))
        return rows
    if backend == "whisper-cpp":
        if not Path(model).is_file():
            raise RuntimeError("--whisper-model にローカルのggmlモデルを指定してください")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "result"
            subprocess.run(["whisper-cli", "-m", model, "-f", str(path), "-l", "ja", "-oj", "-of", str(output)], check=True, capture_output=True, text=True)
            data = json.loads(output.with_suffix(".json").read_text())
            return [(s["offsets"]["from"] / 1000, s["offsets"]["to"] / 1000, s["text"].strip()) for s in data["transcription"]]
    raise ValueError("不明な音声認識バックエンド")
