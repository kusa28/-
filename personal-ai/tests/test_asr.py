import sys
import types
import unittest
from unittest.mock import patch

from personal_ai.asr import transcribe_chunk
from personal_ai.transcript_quality import suspicious_segments


class AsrTests(unittest.TestCase):
    def test_high_no_speech_and_repetition_is_marked_uncertain(self):
        fake = types.SimpleNamespace(transcribe=lambda *args, **kwargs: {
            "segments": [
                {"start": 0, "end": 30, "text": "ありがとう" * 30,
                 "no_speech_prob": 0.98, "compression_ratio": 33.45},
                {"start": 30, "end": 32, "text": "今日は楽しかった",
                 "no_speech_prob": 0.1, "compression_ratio": 1.1},
            ]})
        with patch.dict(sys.modules, {"mlx_whisper": fake}):
            rows = transcribe_chunk("synthetic.wav", "mlx", "test-model")
        self.assertIn("不確実", rows[0][2])
        self.assertEqual(rows[1][2], "今日は楽しかった")
        self.assertIn(1, suspicious_segments([
            {"id": 1, "text": rows[0][2], "corrected_at": None},
            {"id": 2, "text": rows[1][2], "corrected_at": None},
        ]))

    def test_high_no_speech_alone_does_not_discard_text(self):
        fake = types.SimpleNamespace(transcribe=lambda *args, **kwargs: {
            "segments": [{"start": 0, "end": 2, "text": "小さい声",
                          "no_speech_prob": 0.95, "compression_ratio": 1.2}]})
        with patch.dict(sys.modules, {"mlx_whisper": fake}):
            self.assertEqual(transcribe_chunk("synthetic.wav", "mlx", "test-model")[0][2], "小さい声")


if __name__ == "__main__":
    unittest.main()
