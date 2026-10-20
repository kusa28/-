import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from personal_ai.worker import _is_dataless, configure, run_once


class WorkerTests(unittest.TestCase):
    def test_dataless_icloud_placeholder_is_not_opened(self):
        with patch("personal_ai.worker.stat.SF_DATALESS", 0x40000000, create=True):
            self.assertTrue(_is_dataless(SimpleNamespace(st_flags=0x40000060, st_size=90000000)))
            self.assertFalse(_is_dataless(SimpleNamespace(st_flags=0x60, st_size=90000000)))

    def test_requires_explicit_enable_and_skips_new_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "audio"
            folder.mkdir()
            old = folder / "old.wav"
            recent = folder / "recent.wav"
            old.write_bytes(b"old")
            recent.write_bytes(b"new")
            old_time = time.time() - 300
            import os
            os.utime(old, (old_time, old_time))
            config = Path(tmp) / "config.json"
            db_path = Path(tmp) / "worker.sqlite3"
            configure(folder, config_path=config)
            with patch("personal_ai.worker.import_recording") as mocked:
                self.assertEqual(run_once(config, db_path=db_path)["status"], "disabled")
                mocked.assert_not_called()
            configure(folder, enabled=True, config_path=config)
            with patch("personal_ai.worker.import_recording", return_value=(1, False)) as mocked:
                result = run_once(config, db_path=db_path)
                self.assertEqual(result["processed"], 1)
                mocked.assert_called_once_with(old.resolve(), db_path=db_path)


if __name__ == "__main__":
    unittest.main()
