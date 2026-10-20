from contextlib import closing
from http.server import HTTPServer
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from personal_ai.app import CSRF_TOKEN, Handler, database


class QuietHandler(Handler):
    def log_message(self, *_args):
        pass


class ChatWebTests(unittest.TestCase):
    def test_chat_stays_local_and_persists_after_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            db_path = Path(temporary) / "test.sqlite3"
            previous = os.environ.get("PERSONAL_AI_DB")
            os.environ["PERSONAL_AI_DB"] = str(db_path)
            server = HTTPServer(("127.0.0.1", 0), QuietHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                question = '<script>alert("x")</script>'
                with patch("personal_ai.provisional.reply", return_value="その話、聞きたい！") as mocked:
                    bad = urlencode({"csrf": "wrong", "question": question}).encode()
                    with self.assertRaises(HTTPError) as denied:
                        urlopen(Request(base + "/chat", data=bad))
                    denied.exception.close()
                    mocked.assert_not_called()
                    good = urlencode({"csrf": CSRF_TOKEN, "question": question}).encode()
                    with urlopen(Request(base + "/chat", data=good)) as response:
                        self.assertEqual(response.status, 200)
                    mocked.assert_called_once_with(question)
                with urlopen(base + "/chat") as response:
                    body = response.read().decode()
                    self.assertIn("&lt;script&gt;", body)
                    self.assertNotIn("<script>", body)
                with closing(database(db_path)) as db:
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM chat_turns").fetchone()[0], 1)
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
