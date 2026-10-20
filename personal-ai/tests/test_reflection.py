import tempfile
import unittest
from datetime import date
from pathlib import Path

from personal_ai.memory import add, forget
from personal_ai.reflection import list_reflections, run, run_due


class ReflectionTests(unittest.TestCase):
    def test_confirmed_only_idempotent_and_empty_period(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.sqlite3"
            self.assertIsNone(run("daily", "2026-10-20", path))
            add("semantic", "未確認の内容", "テスト", occurred_at="2026-10-20", path=path)
            add("hypothesis", "推測の内容", "テスト", occurred_at="2026-10-20", path=path)
            self.assertIsNone(run("daily", "2026-10-20", path))
            mid = add("episode", "確認済みの出来事", "本人確認テスト",
                      occurred_at="2026-10-20", confirmed=True, path=path)
            first = run("daily", "2026-10-20", path)
            self.assertEqual([s["id"] for s in first["sources"]], [mid])
            self.assertEqual(run("daily", "2026-10-20", path)["id"], first["id"])
            self.assertEqual(len(list_reflections(path)), 1)
            self.assertNotIn("確認済みの出来事", first["conclusion"])

    def test_week_boundary_and_forget_removes_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.sqlite3"
            mid = add("value", "睡眠を大切にする", "本人確認テスト",
                      occurred_at="2026-10-19", confirmed=True, path=path)
            self.assertIsNone(run("weekly", "2026-10-18", path))
            row = run("weekly", "2026-10-19", path)
            self.assertEqual((row["start_date"], row["end_date"]), ("2026-10-19", "2026-10-26"))
            self.assertEqual(len(row["sources"]), 1)
            forget(mid, path)
            self.assertEqual(list_reflections(path)[0]["sources"], [])

    def test_due_catches_up_after_sleep_without_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.sqlite3"
            add("episode", "確認済みの出来事", "本人", occurred_at="2026-10-18",
                confirmed=True, path=path)
            run_due(date(2026, 10, 20), path)
            count = len(list_reflections(path))
            self.assertGreaterEqual(count, 2)
            run_due(date(2026, 10, 20), path)
            self.assertEqual(len(list_reflections(path)), count)


if __name__ == "__main__":
    unittest.main()
