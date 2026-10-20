import math
from pathlib import Path
import tempfile
import unittest
import wave

from personal_ai.audio_features import analyze_audio, _speech_rate


class AudioFeatureTests(unittest.TestCase):
    def test_observations_and_silence(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "synthetic.wav"
            rate = 16000
            samples = [0] * rate
            samples += [int(6000 * math.sin(2 * math.pi * 440 * i / rate)) for i in range(rate)]
            samples += [0] * rate
            with wave.open(str(path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(rate)
                from array import array
                output.writeframes(array("h", samples).tobytes())
            before = path.read_bytes()
            result = analyze_audio(path, [{"start": 1, "end": 2, "text": "テストです"}])
            self.assertEqual(before, path.read_bytes())
            self.assertEqual(result["kind"], "observation")
            self.assertAlmostEqual(result["duration_seconds"], 3, places=2)
            self.assertAlmostEqual(result["asr_characters_per_speech_second"], 5)
            self.assertEqual(len(result["silence_intervals"]), 2)
            self.assertAlmostEqual(result["silence_seconds"], 2, delta=0.2)
            self.assertLess(result["rms_dbfs"], 0)
            self.assertLess(result["peak_dbfs"], 0)

    def test_rate_uses_union_of_overlapping_segments(self):
        self.assertEqual(_speech_rate([
            {"start": 0, "end": 2, "text": "四文字だ"},
            {"start": 1, "end": 3, "text": "二字"},
        ]), 2.0)
        self.assertIsNone(_speech_rate([]))


if __name__ == "__main__":
    unittest.main()
