from contextlib import closing
import subprocess
import tempfile
import unittest
from pathlib import Path

from personal_ai.app import database
from personal_ai.environment_sound import MODEL, _validate, analyze, list_predictions, store


class EnvironmentSoundTests(unittest.TestCase):
    def test_prediction_is_not_observation(self):
        result = _validate({"model": MODEL, "windows": [{"start": 0, "end": 3,
            "predictions": [{"identifier": "music", "confidence": 0.7}]}]})
        self.assertEqual(result["kind"], "model_prediction")
        self.assertIs(result["verified"], False)
        self.assertIn("確定しません", result["note"])

    def test_rejects_claim_that_prediction_is_observed_fact(self):
        with self.assertRaises(ValueError):
            _validate({"model": MODEL, "kind": "observation", "windows": []})

    def test_rejects_bad_confidence(self):
        with self.assertRaises(ValueError):
            _validate({"model": MODEL, "windows": [{"start": 0, "end": 3,
                "predictions": [{"identifier": "music", "confidence": 1.5}]}]})

    def test_native_synthetic_tone(self):
        with tempfile.TemporaryDirectory() as directory:
            wav = Path(directory) / "tone.wav"
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                            "sine=frequency=440:duration=12", "-y", str(wav)], check=True)
            result = analyze(wav)
            self.assertEqual(result["model"], MODEL)
            self.assertTrue(result["windows"])
            self.assertGreaterEqual(result["windows"][0]["start"], 0)
            self.assertLessEqual(result["windows"][0]["predictions"][0]["confidence"], 1)
            self.assertIs(result["verified"], False)

    def test_publicly_distributed_system_sound_remains_unverified(self):
        # macOS ships this sample to users; its semantic class is not inferred
        # from a filename or from the classifier's confidence.
        sample = Path("/System/Library/Sounds/Glass.aiff")
        if not sample.is_file():
            self.skipTest("macOS system sound is unavailable")
        with tempfile.TemporaryDirectory() as directory:
            wav = Path(directory) / "public-sample.wav"
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-stream_loop", "8",
                            "-i", str(sample), "-t", "12", "-y", str(wav)], check=True)
            result = analyze(wav)
            self.assertTrue(result["windows"])
            self.assertIs(result["verified"], False)
            self.assertEqual(result["kind"], "model_prediction")
            self.assertNotIn("location", result)

    def test_store_replaces_predictions(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "test.sqlite3"
            with closing(database(db_path)) as db:
                db.execute("""INSERT INTO recordings
                  (id,sha256,source_path,filename,size_bytes,modified_at,duration,status,backend,model)
                  VALUES (1,'synthetic','synthetic.wav','synthetic.wav',1,'0',10,'done','test','test')""")
                db.commit()
            result = {"model": MODEL, "windows": [{"start": 0, "end": 3,
                      "predictions": [{"identifier": "music", "confidence": 0.7}]}]}
            self.assertEqual(store(1, result, db_path), 1)
            self.assertEqual(store(1, result, db_path), 1)
            rows = list_predictions(1, db_path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["kind"], "model_prediction")
            self.assertIs(rows[0]["verified"], False)


if __name__ == "__main__":
    unittest.main()
