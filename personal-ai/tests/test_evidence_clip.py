from contextlib import closing
from http.server import HTTPServer
import math
import os
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from urllib.request import urlopen
import wave

from personal_ai.app import Handler, database
from personal_ai.evidence_clip import candidate_clip, segment_clip
from personal_ai.memory_candidates import extract_candidates, review_candidate


class EvidenceClipTests(unittest.TestCase):
    def test_flagged_transcript_excerpt_is_local_and_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.sqlite3"
            source = Path(tmp) / "synthetic.wav"
            with wave.open(str(source), "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(16000)
                out.writeframes(b"\0\0" * 16000 * 4)
            with closing(database(path)) as db:
                rid = db.execute("""INSERT INTO recordings
                    (sha256,source_path,filename,size_bytes,modified_at,duration,status,backend,model)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    ("flagged", str(source), source.name, source.stat().st_size,
                     "0", 4, "done", "test", "test")).lastrowid
                ids = []
                for index in range(4):
                    text = "同じ言葉"
                    ids.append(db.execute("INSERT INTO segments(recording_id,start,end,text,original_text) VALUES (?,?,?,?,?)",
                                          (rid, index, index + 1, text, text)).lastrowid)
                db.commit()
            self.assertTrue(segment_clip(ids[0], path).startswith(b"RIFF"))
            self.assertLess(len(segment_clip(ids[0], path)), 700000)
            with self.assertRaises(ValueError):
                segment_clip(99999, path)

    def test_only_pending_candidate_can_play_bounded_local_excerpt(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.sqlite3"
            source = Path(tmp) / "synthetic.wav"
            with wave.open(str(source), "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(16000)
                frames = (int(1000 * math.sin(2 * math.pi * 440 * i / 16000))
                          for i in range(16000 * 4))
                out.writeframes(b"".join(struct.pack("<h", sample) for sample in frames))
            with closing(database(path)) as db:
                rid = db.execute("""INSERT INTO recordings
                    (sha256,source_path,filename,size_bytes,modified_at,duration,status,backend,model)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    ("d" * 64, str(source), source.name, source.stat().st_size,
                     "0", 4, "done", "test", "test")).lastrowid
                db.execute("INSERT INTO segments(recording_id,start,end,text,original_text) VALUES (?,?,?,?,?)",
                           (rid, 1, 2, "私は読書が好きです。", "私は読書が好きです。"))
                db.commit()
            candidate = extract_candidates(rid, path)[0]
            audio = candidate_clip(candidate["id"], path)
            self.assertTrue(audio.startswith(b"RIFF"))
            self.assertLess(len(audio), 200000)
            previous = os.environ.get("PERSONAL_AI_DB")
            os.environ["PERSONAL_AI_DB"] = str(path)
            server = HTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_port}/memory-candidate/clip?id={candidate['id']}") as response:
                    self.assertEqual(response.headers["Content-Type"], "audio/wav")
                    self.assertTrue(response.read().startswith(b"RIFF"))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
                if previous is None:
                    os.environ.pop("PERSONAL_AI_DB", None)
                else:
                    os.environ["PERSONAL_AI_DB"] = previous
            review_candidate(candidate["id"], False, path)
            with self.assertRaises(ValueError):
                candidate_clip(candidate["id"], path)
            with self.assertRaises(ValueError):
                candidate_clip(99999, path)


if __name__ == "__main__":
    unittest.main()
