import contextlib
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import wave

from personal_ai.app import database, main


class ObserveCliTests(unittest.TestCase):
    def test_observation_is_saved_without_emotion_claim(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wav = root / "synthetic.wav"
            with wave.open(str(wav), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(16000)
                audio.writeframes(struct.pack("<h", 0) * 16000)
            db_path = root / "test.sqlite3"
            with contextlib.closing(database(db_path)) as db:
                db.execute("""INSERT INTO recordings(id,sha256,source_path,filename,size_bytes,modified_at,duration,status,backend,model)
                  VALUES (1,'synthetic',?,?,?,?,?,'done','mlx','test')""",
                  (str(wav), wav.name, wav.stat().st_size, "0", 1.0))
                db.execute("INSERT INTO segments(recording_id,start,end,text,original_text) VALUES (1,0,1,'テスト','テスト')")
                db.commit()
            old_db, old_argv = os.environ.get("PERSONAL_AI_DB"), sys.argv
            try:
                os.environ["PERSONAL_AI_DB"] = str(db_path)
                sys.argv = ["personal_ai", "observe", "1"]
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    main()
            finally:
                sys.argv = old_argv
                if old_db is None:
                    os.environ.pop("PERSONAL_AI_DB", None)
                else:
                    os.environ["PERSONAL_AI_DB"] = old_db
            result = json.loads(output.getvalue())
            self.assertEqual(result["kind"], "observation")
            self.assertNotIn("emotion", result)
            with contextlib.closing(database(db_path)) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM audio_observations").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
