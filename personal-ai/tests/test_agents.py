import os
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import subprocess

from personal_ai import agents


class AgentTests(unittest.TestCase):
    def test_records_result_and_strips_api_keys(self):
        class Completed:
            returncode = 0
            stdout = '{"status":"SUCCESS","response":"問題なし"}'
            stderr = ""

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(agents, "STATE", Path(tmp)), patch.dict(os.environ, {"ANTHROPIC_API_KEY": "secret"}):
                def fake_run(argv, **kwargs):
                    self.assertNotIn("ANTHROPIC_API_KEY", kwargs["env"])
                    self.assertNotEqual(kwargs["cwd"], agents.ROOT)
                    return Completed()

                with patch("personal_ai.agents.subprocess.run", side_effect=fake_run):
                    run_id, ok, answer = agents.run("claude", "test")
                self.assertTrue(ok)
                self.assertEqual(answer, "問題なし")
                with closing(agents.store()) as db:
                    row = db.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
                    self.assertEqual(row["status"], "done")

    def test_timeout_is_saved_for_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(agents, "STATE", Path(tmp)):
                with patch("personal_ai.agents.subprocess.run", side_effect=subprocess.TimeoutExpired("claude", 1)):
                    run_id, ok, output = agents.run("claude", "test", timeout=1)
                self.assertFalse(ok)
                self.assertIn("タイムアウト", output)
                with closing(agents.store()) as db:
                    row = db.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
                    self.assertEqual(row["status"], "failed")


if __name__ == "__main__":
    unittest.main()
