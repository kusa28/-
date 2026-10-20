import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from personal_ai.memory import add, conversation, forget, list_memories, memory_db, recall, revise, supersede


class MemoryTests(unittest.TestCase):
    def test_time_change_keeps_old_claim_as_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "memory.sqlite3"
            old = add("semantic", "数学が苦手", "本人の過去の発言", occurred_at="2025-04-01", path=db_path)
            new = supersede(old, "semantic", "数学の理解が進んだ", "本人の新しい発言",
                            "時間とともに変化", occurred_at="2026-10-20", confirmed=True, path=db_path)
            self.assertEqual([row["id"] for row in list_memories(db_path)], [new])
            self.assertEqual(len(list_memories(db_path, include_inactive=True)), 2)
            with closing(memory_db(db_path)) as db:
                link = db.execute("SELECT relation,reason FROM memory_links WHERE old_memory_id=?", (old,)).fetchone()
                self.assertEqual((link["relation"], link["reason"]), ("supersedes", "時間とともに変化"))

    def test_hypothesis_is_labeled_and_revision_is_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "memory.sqlite3"
            mid = add("hypothesis", "朝は読書が好きかもしれない", "手動テスト", confidence=0.4, path=db_path)
            with self.assertRaisesRegex(ValueError, "仮説"):
                revise(mid, "朝は読書が好き", confirmed=True, path=db_path)
            revise(mid, "夕方は読書が好きかもしれない", path=db_path)
            rows = list_memories(db_path)
            self.assertEqual(rows[0]["text"], "夕方は読書が好きかもしれない")
            with closing(memory_db(db_path)) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM memory_revisions").fetchone()[0], 1)
            with patch("personal_ai.llm.chat", return_value="そうかもね") as mocked:
                self.assertEqual(conversation("今日は何しよう？", db_path), "そうかもね")
                self.assertIn("仮説", mocked.call_args.args[0][1]["content"])
                self.assertIn("架空の背景を現実に体験したと偽らない", mocked.call_args.args[0][0]["content"])
            self.assertIn("仮説として", recall("読書が好きだっけ？", db_path))
            forget(mid, db_path)
            self.assertEqual(list_memories(db_path), [])


if __name__ == "__main__":
    unittest.main()
