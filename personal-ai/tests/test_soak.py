import tempfile
import unittest
from pathlib import Path

from personal_ai.soak import DURATION_SECONDS, INTERVAL_SECONDS, _sleep_intervals, restart, sample, start, status


class SoakTests(unittest.TestCase):
    def test_elapsed_time_does_not_imply_pass_without_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "soak.sqlite3"
            self.assertEqual(status(path, 100)["status"], "NOT_STARTED")
            first = start(path, 100)
            self.assertEqual(start(path, 101), first)
            self.assertEqual(sample(path, 100, {"web_ok": 1, "ollama_ok": 1, "worker_loaded": 1, "ingestion_enabled": 1})["samples"], 1)
            after = status(path, 100 + DURATION_SECONDS)
            self.assertEqual(after["status"], "FAILED")
            self.assertGreater(after["max_gap_seconds"], INTERVAL_SECONDS * 2)
            self.assertEqual(sample(path, 100 + DURATION_SECONDS, {"web_ok": 1, "ollama_ok": 1, "worker_loaded": 1, "ingestion_enabled": 1})["status"], "FAILED")

    def test_failure_disqualifies_verified_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "soak.sqlite3"
            start(path, 100)
            sample(path, 100, {"web_ok": 0, "ollama_ok": 1, "worker_loaded": 1, "ingestion_enabled": 1})
            for t in range(100 + INTERVAL_SECONDS, 100 + DURATION_SECONDS + 1, INTERVAL_SECONDS):
                result = sample(path, t, {"web_ok": 1, "ollama_ok": 1, "worker_loaded": 1, "ingestion_enabled": 1})
            self.assertEqual(result["status"], "FAILED")
            self.assertEqual(result["failed_samples"], 1)

    def test_continuous_full_day_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "soak.sqlite3"
            start(path, 100)
            for t in range(100, 100 + DURATION_SECONDS + 1, INTERVAL_SECONDS):
                result = sample(path, t, {"web_ok": 1, "ollama_ok": 1, "worker_loaded": 1, "ingestion_enabled": 1})
            self.assertEqual(result["status"], "PASSED")
            self.assertEqual(result["remaining_seconds"], 0)

    def test_disabled_ingestion_disqualifies_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "soak.sqlite3"
            start(path, 100)
            for t in range(100, 100 + DURATION_SECONDS + 1, INTERVAL_SECONDS):
                result = sample(path, t, {"web_ok": 1, "ollama_ok": 1,
                                          "worker_loaded": 1, "ingestion_enabled": 0})
            self.assertEqual(result["status"], "FAILED")

    def test_long_sampling_gap_is_visible_before_full_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "soak.sqlite3"
            start(path, 100)
            sample(path, 100, {"web_ok": 1, "ollama_ok": 1,
                               "worker_loaded": 1, "ingestion_enabled": 1})
            result = status(path, 100 + INTERVAL_SECONDS * 2 + 1)
            self.assertEqual(result["status"], "RUNNING_WITH_GAPS")
            self.assertEqual(result["failed_samples"], 0)
            self.assertEqual(sample(path, 100 + INTERVAL_SECONDS * 3,
                                    {"web_ok": 1, "ollama_ok": 1,
                                     "worker_loaded": 1, "ingestion_enabled": 1})["status"],
                             "RUNNING_WITH_GAPS")

    def test_sleep_gap_is_reported_but_not_treated_as_awake_outage(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "soak.sqlite3"
            start(path, 100)
            sample(path, 100, {"web_ok": 1, "ollama_ok": 1,
                               "worker_loaded": 1, "ingestion_enabled": 1})
            sample(path, 3100, {"web_ok": 1, "ollama_ok": 1,
                                 "worker_loaded": 1, "ingestion_enabled": 1})
            result = status(path, 3100, sleep_intervals=[(150, 3000)])
            self.assertEqual(result["status"], "RUNNING")
            self.assertEqual(result["max_gap_seconds"], 3000)
            self.assertEqual(result["max_awake_gap_seconds"], 150)
            self.assertEqual(status(path, 3100, sleep_intervals=[])["status"],
                             "RUNNING_WITH_GAPS")

    def test_only_sleep_and_wake_events_are_parsed(self):
        log = ("2026-10-20 10:58:31 +0900 Sleep                Entering Sleep\n"
               "2026-10-20 10:58:33 +0900 Wake Requests        maintenance\n"
               "2026-10-20 11:14:02 +0900 DarkWake             maintenance\n"
               "2026-10-20 11:14:04 +0900 Sleep                Back to Sleep\n"
               "2026-10-20 11:44:03 +0900 Wake                 Wake from Deep Idle\n")
        intervals = _sleep_intervals(log)
        self.assertEqual(len(intervals), 1)
        self.assertEqual(round(intervals[0][1] - intervals[0][0]), 2732)

    def test_restart_preserves_old_failed_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "soak.sqlite3"
            first = start(path, 100)
            sample(path, 100, {"web_ok": 1, "ollama_ok": 1,
                               "worker_loaded": 1, "ingestion_enabled": 0})
            self.assertEqual(status(path, 101)["status"], "RUNNING_WITH_FAILURES")
            second = restart(path, 200)
            self.assertNotEqual(first, second)
            self.assertEqual(status(path, 200)["status"], "RUNNING")


if __name__ == "__main__":
    unittest.main()
