import tempfile
import unittest
from pathlib import Path
import sqlite3
from contextlib import closing

from personal_ai.backup import backup
from personal_ai.memory import add


class BackupTests(unittest.TestCase):
    def test_backup_is_consistent_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "source.sqlite3"
            add("semantic", "合成テスト", "test", path=db)
            copy = Path(tmp) / "copy.sqlite3"
            backup(copy, db)
            with closing(sqlite3.connect(copy)) as saved:
                self.assertEqual(saved.execute("SELECT text FROM memories").fetchone()[0], "合成テスト")
            with self.assertRaises(FileExistsError):
                backup(copy, db)


if __name__ == "__main__":
    unittest.main()
