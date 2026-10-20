import tempfile
import unittest
from contextlib import closing
from pathlib import Path
import sqlite3
from unittest.mock import patch
import wave

from personal_ai.app import ask, chunk_windows, correct_segment, database, get_transcript, import_recording, recording_lock
from personal_ai.memory_candidates import list_candidates, scan_pending


class ImportTests(unittest.TestCase):
    def test_ask_excludes_obvious_asr_loop_without_deleting_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.sqlite3"
            with closing(database(db_path)) as db:
                rid = db.execute("""INSERT INTO recordings
                    (sha256,source_path,filename,size_bytes,modified_at,duration,status,backend,model)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    ("loop", "synthetic.wav", "synthetic.wav", 1, "0", 20, "done", "test", "test")).lastrowid
                for start, text in ((0, "今日は公園に行きました。"), (5, "ありがとう" * 20)):
                    db.execute("INSERT INTO segments(recording_id,start,end,text,original_text) VALUES (?,?,?,?,?)",
                               (rid, start, start + 5, text, text))
                db.commit()
            with patch("personal_ai.app.local_chat", return_value="回答") as chat:
                self.assertEqual(ask(rid, "何をした？", db_path), "回答")
            context = chat.call_args.args[0][1]["content"]
            self.assertIn("公園", context)
            self.assertNotIn("ありがとう", context)
            self.assertEqual(len(get_transcript(rid, db_path)[1]), 2)

    def test_ask_refuses_recording_dominated_by_asr_loops(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.sqlite3"
            with closing(database(db_path)) as db:
                rid = db.execute("""INSERT INTO recordings
                    (sha256,source_path,filename,size_bytes,modified_at,duration,status,backend,model)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    ("mostly-loop", "synthetic.wav", "synthetic.wav", 1, "0", 20, "done", "test", "test")).lastrowid
                for index in range(5):
                    text = "今日は公園に行きました。" if index == 0 else "ありがとう" * 20
                    db.execute("INSERT INTO segments(recording_id,start,end,text,original_text) VALUES (?,?,?,?,?)",
                               (rid, index * 2, index * 2 + 2, text, text))
                db.commit()
            with patch("personal_ai.app.local_chat") as chat:
                with self.assertRaisesRegex(ValueError, "根拠にできません"):
                    ask(rid, "何をした？", db_path)
                chat.assert_not_called()

    def test_chunk_boundaries_overlap_without_short_tail(self):
        self.assertEqual(list(chunk_windows(600.5)), [(0, 0, 600.5, 600.5)])
        self.assertEqual(list(chunk_windows(610)), [(0, 0, 605, 600), (600, 595, 610, 610)])

    def test_duplicate_lock_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.sqlite3"
            with recording_lock("abc", db):
                with self.assertRaisesRegex(RuntimeError, "別のプロセス"):
                    with recording_lock("abc", db):
                        pass

    def test_persists_segments_and_skips_duplicate_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            with wave.open(str(audio), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(b"\0\0" * 16000)
            db = Path(tmp) / "test.sqlite3"
            with patch("personal_ai.app.transcribe_chunk", return_value=[(0.1, 0.8, "こんにちは")]) as transcribe:
                rid, skipped = import_recording(audio, db_path=db)
                self.assertFalse(skipped)
                again, skipped = import_recording(audio, db_path=db)
                self.assertTrue(skipped)
                self.assertEqual(rid, again)
                transcribe.assert_called_once()
            recording, segments = get_transcript(rid, db)
            self.assertEqual(recording["status"], "done")
            self.assertEqual(segments[0]["text"], "こんにちは")
            self.assertAlmostEqual(segments[0]["start"], 0.1)
            correct_segment(segments[0]["id"], "おはよう", db)
            _, corrected = get_transcript(rid, db)
            self.assertEqual(corrected[0]["text"], "おはよう")
            self.assertEqual(corrected[0]["original_text"], "こんにちは")
            with self.assertRaisesRegex(ValueError, "クラウドモデル"):
                ask(rid, "内容は？", db, model="remote:cloud")

    def test_import_scans_candidates_without_automatic_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            with wave.open(str(audio), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(b"\0\0" * 16000)
            db_path = Path(tmp) / "test.sqlite3"
            with patch("personal_ai.app.transcribe_chunk", return_value=[(0, 0.9, "私は読書が好きです。")]):
                rid, duplicate = import_recording(audio, db_path=db_path)
                self.assertFalse(duplicate)
                self.assertEqual(len(list_candidates(db_path)), 1)
                with closing(database(db_path)) as db:
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM memories").fetchone()[0], 0)
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM memory_candidate_scans").fetchone()[0], 1)
                rid_again, duplicate = import_recording(audio, db_path=db_path)
                self.assertEqual(rid_again, rid)
                self.assertTrue(duplicate)
                self.assertEqual(len(list_candidates(db_path)), 1)

    def test_candidate_scan_failure_does_not_erase_completed_transcript(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            with wave.open(str(audio), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(b"\0\0" * 16000)
            db_path = Path(tmp) / "test.sqlite3"
            with patch("personal_ai.app.transcribe_chunk", return_value=[(0, 0.9, "私は読書が好きです。")]), \
                 patch("personal_ai.memory_candidates.scan_recording", side_effect=sqlite3.OperationalError("synthetic")):
                rid, duplicate = import_recording(audio, db_path=db_path)
            self.assertFalse(duplicate)
            recording, segments = get_transcript(rid, db_path)
            self.assertEqual(recording["status"], "done")
            self.assertEqual(len(segments), 1)
            self.assertEqual(scan_pending(db_path), {"scanned": 1, "candidates": 1})


if __name__ == "__main__":
    unittest.main()
