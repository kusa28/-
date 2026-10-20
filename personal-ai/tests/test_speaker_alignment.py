from contextlib import closing
from pathlib import Path
import tempfile
import unittest

from personal_ai.app import database
from personal_ai.diarization import store_turns
from personal_ai.speaker_alignment import annotated_transcript


class SpeakerAlignmentTests(unittest.TestCase):
    def test_clear_turn_switch_and_overlap_are_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.sqlite3"
            with closing(database(path)) as db:
                rid = db.execute("""INSERT INTO recordings
                    (sha256,source_path,filename,size_bytes,modified_at,duration,status,backend,model)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    ("e" * 64, "synthetic.wav", "synthetic.wav", 1, "0", 3, "done", "test", "test")).lastrowid
                for start, end, label in ((0, 1, "first"), (1, 2, "switch"), (2, 3, "last")):
                    db.execute("INSERT INTO segments(recording_id,start,end,text,original_text) VALUES (?,?,?,?,?)",
                               (rid, start, end, label, label))
                db.commit()
            store_turns(rid, [
                {"start": 0, "end": 1.8, "speaker": "SPEAKER_00"},
                {"start": 1.4, "end": 3, "speaker": "SPEAKER_01"},
                {"start": 0, "end": 1.5, "speaker": "SPEAKER_00", "exclusive": True},
                {"start": 1.5, "end": 3, "speaker": "SPEAKER_01", "exclusive": True},
            ], path)
            _, rows = annotated_transcript(rid, path)
            self.assertEqual([row["speaker_label"] for row in rows],
                             ["SPEAKER_00", "UNKNOWN", "SPEAKER_01"])
            self.assertEqual([row["overlap"] for row in rows], [False, True, False])
            self.assertTrue(all(row["speaker_source"] == "model_prediction" for row in rows))

    def test_without_diarization_speaker_is_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.sqlite3"
            with closing(database(path)) as db:
                rid = db.execute("""INSERT INTO recordings
                    (sha256,source_path,filename,size_bytes,modified_at,duration,status,backend,model)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    ("f" * 64, "synthetic.wav", "synthetic.wav", 1, "0", 1, "done", "test", "test")).lastrowid
                db.execute("INSERT INTO segments(recording_id,start,end,text,original_text) VALUES (?,?,?,?,?)",
                           (rid, 0, 1, "synthetic", "synthetic"))
                db.commit()
            _, rows = annotated_transcript(rid, path)
            self.assertEqual(rows[0]["speaker_label"], "UNKNOWN")
            self.assertEqual(rows[0]["speaker_source"], "unavailable")


if __name__ == "__main__":
    unittest.main()
