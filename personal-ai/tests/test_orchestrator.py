import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from personal_ai.orchestrator import connect, select_task, run_once, _run_process


class OrchestratorTests(unittest.TestCase):
    def test_state_survives_reopen_and_respects_dependencies(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "state.sqlite3"
            db = connect(state)
            self.assertEqual(select_task(db)["id"], "ORCH")
            db.execute("UPDATE tasks SET status='COMPLETED',test_status='PASS' WHERE id='ORCH'")
            db.commit()
            db.close()
            db = connect(state)
            self.assertEqual(select_task(db)["id"], "PROVISIONAL_RELEASE")
            self.assertEqual(db.execute("SELECT test_status FROM tasks WHERE id='ORCH'").fetchone()[0], "PASS")
            db.close()

    def test_deadline_kills_silent_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = _run_process([sys.executable, "-c", "import time; time.sleep(3)"],
                                  Path(temporary), __import__("time").monotonic() + 0.1, {})
            self.assertEqual(result[2], "timeout")

    def test_usage_limit_event_is_classified_without_logging_message(self):
        with tempfile.TemporaryDirectory() as temporary:
            script = "print('{\"type\":\"error\",\"message\":\"rate limit reached\"}')"
            result = _run_process([sys.executable, "-c", script], Path(temporary),
                                  __import__("time").monotonic() + 5, {})
            self.assertEqual(result[2], "usage_limit")

    def test_restart_uses_saved_session_and_checkpoints(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
            (root / "PROGRESS.md").write_text("start\n")
            (root / "tests").mkdir()
            (root / "tests/test_smoke.py").write_text("import unittest\nclass Smoke(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "initial"], cwd=root, check=True)
            fake = root / "fake-codex"
            fake.write_text("#!/bin/sh\nprintf '%s\\n' '{\"type\":\"thread.started\",\"thread_id\":\"test-session\"}'\necho work >> PROGRESS.md\n")
            fake.chmod(0o700)
            subprocess.run(["git", "add", "fake-codex"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "fake"], cwd=root, check=True)
            state = root / ".development/state.sqlite3"
            (root / ".gitignore").write_text(".development/\n")
            subprocess.run(["git", "add", ".gitignore"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "ignore"], cwd=root, check=True)
            first = run_once(root=root, state=state, codex=str(fake), timeout_seconds=5, probe=lambda _: {"kind":"available"})
            self.assertEqual(first["status"], "TESTING", first)
            db = connect(state)
            self.assertEqual(db.execute("SELECT session_id FROM tasks WHERE id='ORCH'").fetchone()[0], "test-session")
            db.close()
            self.assertIn("codex checkpoint", subprocess.check_output(["git", "log", "-1", "--format=%s"], cwd=root, text=True))
            second = run_once(root=root, state=state, codex=str(fake), timeout_seconds=5, probe=lambda _: {"kind":"available"})
            self.assertEqual(second["status"], "TESTING")
            self.assertIn("work\nwork", (root / "PROGRESS.md").read_text())


if __name__ == "__main__":
    unittest.main()
