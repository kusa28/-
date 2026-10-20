from contextlib import closing
from http.server import HTTPServer
import os
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from personal_ai.app import CSRF_TOKEN, Handler, database
from personal_ai.memory import list_memories
from personal_ai.memory_candidates import extract_candidates


class QuietHandler(Handler):
    def log_message(self, *_args):
        pass


class MemoryWebTests(unittest.TestCase):
    def test_review_requires_csrf_and_persists_confirmation(self):
        with tempfile.TemporaryDirectory() as temporary:
            db_path = Path(temporary) / "test.sqlite3"
            with closing(database(db_path)) as db:
                db.execute("""INSERT INTO recordings(id,sha256,source_path,filename,size_bytes,modified_at,duration,status,backend,model)
                    VALUES (1,'synthetic','synthetic.wav','synthetic.wav',1,'0',10,'done','test','test')""")
                db.execute("INSERT INTO segments(recording_id,start,end,text,original_text) VALUES (1,0,2,'私は読書が好きです。','私は読書が好きです。')")
                db.commit()
            candidate_id = extract_candidates(1, db_path)[0]["id"]
            previous = os.environ.get("PERSONAL_AI_DB")
            os.environ["PERSONAL_AI_DB"] = str(db_path)
            server = HTTPServer(("127.0.0.1", 0), QuietHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with urlopen(base + "/memory") as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn("承認待ち", response.read().decode())
                bad = urlencode({"candidate_id": candidate_id, "decision": "accept", "csrf": "wrong"}).encode()
                with self.assertRaises(HTTPError) as denied:
                    urlopen(Request(base + "/memory-candidate/review", data=bad))
                self.assertEqual(denied.exception.code, 403)
                denied.exception.close()
                self.assertEqual(list_memories(db_path), [])
                good = urlencode({"candidate_id": candidate_id, "decision": "accept", "csrf": CSRF_TOKEN}).encode()
                with urlopen(Request(base + "/memory-candidate/review", data=good)) as response:
                    self.assertEqual(response.status, 200)  # urllib follows the 303 redirect.
                self.assertEqual(list_memories(db_path)[0]["user_confirmed"], 1)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
                if previous is None:
                    os.environ.pop("PERSONAL_AI_DB", None)
                else:
                    os.environ["PERSONAL_AI_DB"] = previous


if __name__ == "__main__":
    unittest.main()
