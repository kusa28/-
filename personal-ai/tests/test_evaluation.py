import json
from pathlib import Path
import tempfile
import unittest
from contextlib import closing
from unittest.mock import Mock, patch

from personal_ai.evaluation import (
    CASES, _check, connect, export_review, rate, run_once, start, summary,
)


def fake_provider(messages, model=None, **kwargs):
    if "検討器" in messages[0]["content"]:
        return '{"findings":[]}'
    return json.dumps({"answer": "必要な情報を確認して、一緒に考えよう。", "claims": []}, ensure_ascii=False)


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "quality.sqlite3"
        self.eid = start(path=self.path, model_digest="fake-test-digest")

    def test_three_conditions_resume_without_repeating_completed_jobs(self):
        provider = Mock(side_effect=fake_provider)
        ids = []
        for _ in range(3):
            result = run_once(self.eid, self.path, provider=provider)
            self.assertEqual(result["status"], "completed")
            ids.append(result["job_id"])
        self.assertEqual(len(set(ids)), 3)
        with closing(connect(self.path)) as db:
            rows = db.execute("SELECT condition,context_json FROM evaluation_jobs WHERE id IN (?,?,?)", ids).fetchall()
        by_condition = {r["condition"]: json.loads(r["context_json"]) for r in rows}
        self.assertEqual(by_condition["none"]["memories"], [])
        self.assertEqual(by_condition["memory"]["memories"], by_condition["deliberation"]["memories"])
        self.assertEqual(by_condition["memory"]["memories"][0]["text"], "今は写真より陶芸を試したい。")
        memory = by_condition["memory"]["memories"][0]
        self.assertIsNone(memory["occurred_at"])
        self.assertEqual(memory["observed_at"], "2026-10-20T19:00:00+09:00")
        self.assertEqual(summary(self.eid, self.path)["completed"], 3)

    def test_suite_is_synthetic_and_no_personal_db_is_opened(self):
        with patch("personal_ai.app.database", wraps=__import__("personal_ai.app", fromlist=["database"]).database) as opened:
            for _ in range(3):
                run_once(self.eid, self.path, provider=fake_provider)
        self.assertTrue(opened.call_count)
        for call in opened.call_args_list:
            self.assertIsNotNone(call.args[0])
            self.assertIn("personal-ai-synthetic-", str(call.args[0]))
        report = summary(self.eid, self.path)
        self.assertTrue(report["synthetic_only"])
        self.assertEqual(report["quality_improvement"], "not_established")
        self.assertEqual(report["human_rated"], 0)

    def test_fabricated_citations_and_hypothesis_fact_are_flags_not_quality_grades(self):
        context = {"memories": [{"id": 1}], "hypotheses": [{"id": 2}]}
        raw = json.dumps({"answer": "根拠なしの主張", "claims": [{"text": "主張", "kind": "fact",
                          "memory_ids": [999], "hypothesis_ids": [2]}]})
        _, checks = _check(raw, context)
        self.assertEqual(checks["invalid_references"], 1)
        self.assertEqual(checks["facts_without_memory_reference"], 1)
        self.assertEqual(checks["hypotheses_labeled_fact"], 1)
        self.assertEqual(checks["semantic_correctness"], "unrated")
        with self.assertRaises(ValueError):
            _check('{"answer":"x","claims":[{"kind":"fact"}]}', context)

    def test_failed_output_has_cooldown_redacted_error_and_bounded_attempts(self):
        provider = Mock(side_effect=TimeoutError("private text must not be logged"))
        with closing(connect(self.path)) as db:
            db.execute("UPDATE evaluation_jobs SET status='BLOCKED' WHERE id!=1")
            db.commit()
        for clock in (100, 3701, 7302):
            self.assertEqual(run_once(self.eid, self.path, provider=provider, now=clock)["status"], "failed")
        self.assertEqual(provider.call_count, 3)
        self.assertEqual(run_once(self.eid, self.path, provider=provider, now=11000)["status"], "no_runnable_job")
        with closing(connect(self.path)) as db:
            job = db.execute("SELECT * FROM evaluation_jobs WHERE id=1").fetchone()
        self.assertEqual((job["attempts"], job["status"], job["error_kind"]), (3, "BLOCKED", "TimeoutError"))

    def test_restart_recovers_running_job_and_lock_excludes_concurrent_execution(self):
        from personal_ai.app import recording_lock
        with recording_lock("quality-evaluation", self.path):
            self.assertEqual(run_once(self.eid, self.path, provider=Mock())["status"], "already_running")
        with closing(connect(self.path)) as db:
            db.execute("UPDATE evaluation_jobs SET status='RUNNING',attempts=1 WHERE id=1")
            db.commit()
        result = run_once(self.eid, self.path, provider=fake_provider, now=100)
        self.assertEqual(result["job_id"], 2)
        with closing(connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT status,error_kind FROM evaluation_jobs WHERE id=1").fetchone()[:],
                             ("FAILED", "interrupted"))

    def test_changed_suite_or_model_is_not_silently_compared(self):
        with patch("personal_ai.evaluation._suite_hash", return_value="new"):
            with self.assertRaises(ValueError):
                run_once(self.eid, self.path, provider=fake_provider)
        with patch("personal_ai.evaluation._model_digest", return_value="changed"), patch("personal_ai.llm.chat") as chat:
            result = run_once(self.eid, self.path)
        self.assertEqual(result["status"], "failed")
        chat.assert_not_called()
        self.assertEqual(summary(self.eid, self.path)["blocked"], len(CASES)*3)

    def test_actual_adapter_keeps_answer_settings_identical(self):
        with patch("personal_ai.evaluation._model_digest", return_value="fake-test-digest"), \
                patch("personal_ai.llm.chat", side_effect=fake_provider) as chat:
            self.assertEqual(run_once(self.eid, self.path)["status"], "completed")
            self.assertEqual(run_once(self.eid, self.path)["status"], "completed")
        self.assertEqual(chat.call_args_list[0].kwargs, chat.call_args_list[1].kwargs)
        self.assertEqual(chat.call_args.kwargs["seed"], 20261020)
        self.assertEqual(chat.call_args.kwargs["temperature"], 0)

    def test_manual_review_is_escaped_condition_hidden_and_never_auto_grades(self):
        provider = lambda *a, **k: '{"answer":"<script>bad()</script>","claims":[]}'
        job = run_once(self.eid, self.path, provider=provider)
        destination = export_review(self.eid, self.path)
        markup = destination.read_text()
        self.assertIn("&lt;script&gt;", markup)
        self.assertNotIn("<script>", markup)
        self.assertNotIn('condition', markup)
        self.assertIn("評価用の架空経験", markup)
        with self.assertRaises(FileExistsError):
            export_review(self.eid, self.path)
        rate(job["job_id"], 1, 2, 3, self.path)
        report = summary(self.eid, self.path)
        self.assertEqual(report["human_rated"], 1)
        self.assertFalse(report["human_comparison_complete"])
        self.assertEqual(report["quality_improvement"], "not_established")
        with self.assertRaises(ValueError):
            rate(job["job_id"], 5, 0, 0, self.path)
        with self.assertRaises(ValueError):
            rate(9999, 0, 0, 0, self.path)

    def test_review_uses_persisted_case_snapshot_after_future_fixture_change(self):
        run_once(self.eid, self.path, provider=fake_provider)
        with patch("personal_ai.evaluation.CASES", ({"id": "different_future_case"},)):
            markup = export_review(self.eid, self.path).read_text()
        self.assertIn("休日の趣味、何を選ぼう？", markup)

    def test_all_cases_execute_and_do_not_imply_quality_improvement(self):
        for _ in range(len(CASES)*3):
            self.assertEqual(run_once(self.eid, self.path, provider=fake_provider)["status"], "completed")
        report = run_once(self.eid, self.path, provider=fake_provider)
        self.assertEqual(report["status"], "no_runnable_job")
        self.assertEqual(report["completed"], 18)
        self.assertFalse(report["human_comparison_complete"])


if __name__ == "__main__":
    unittest.main()
