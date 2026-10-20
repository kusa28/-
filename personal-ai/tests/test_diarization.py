from contextlib import closing
from pathlib import Path
import tempfile
import unittest

from personal_ai.app import database
from personal_ai.diarization import list_turns, store_turns


class DiarizationTests(unittest.TestCase):
    def test_anonymous_turns_preserve_overlap_and_exclusive_view(self):
        with tempfile.TemporaryDirectory() as temporary:
            db_path = Path(temporary) / "test.sqlite3"
            with closing(database(db_path)) as db:
                db.execute("""INSERT INTO recordings(id,sha256,source_path,filename,size_bytes,modified_at,duration,status,backend,model)
                    VALUES (1,'synthetic','synthetic.wav','synthetic.wav',1,'0',10,'done','test','test')""")
                db.commit()
            rows = [
                {"start": 0, "end": 2, "speaker": "SPEAKER_00"},
                {"start": 1, "end": 3, "speaker": "SPEAKER_01"},
                {"start": 0, "end": 1.5, "speaker": "SPEAKER_00", "exclusive": True},
                {"start": 1.5, "end": 3, "speaker": "SPEAKER_01", "exclusive": True},
            ]
            self.assertEqual(store_turns(1, rows, db_path), 4)
            regular = list_turns(1, db_path)
            self.assertEqual([row["overlap"] for row in regular], [1, 1])
            self.assertEqual(len(list_turns(1, db_path, exclusive=True)), 2)
            self.assertEqual(store_turns(1, rows, db_path), 4)
            self.assertEqual(len(list_turns(1, db_path)), 2)
            with self.assertRaisesRegex(ValueError, "匿名"):
                store_turns(1, [{"start": 0, "end": 1, "speaker": "本人"}], db_path)


if __name__ == "__main__":
    unittest.main()
