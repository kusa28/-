import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from personal_ai.deliberation import list_findings, review_db, run
from personal_ai.memory import add, conversation, forget, memory_db, revise, supersede


def answer(supports, counters=(), previous_id=None):
    return json.dumps({"findings": [{
        "previous_id": previous_id, "purpose": "数学への負担は変わったか",
        "statement": "数学の負担が減った可能性があるが、状況によるかもしれない",
        "rationale": "本人の新旧の発言を比較。一般的な性格までは分からない",
        "next_check": "次の課題でも同じように感じるか本人に確認する",
        "confidence": 0.4, "supports": list(supports), "counters": list(counters),
    }]}, ensure_ascii=False)


class DeliberationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "memory.sqlite3"

    def memory(self, text="数学が以前より分かる", confirmed=True):
        return add("episode", text, "合成テスト", confirmed=confirmed, path=self.path)

    def test_only_confirmed_evidence_and_restart_without_model_call(self):
        unconfirmed = self.memory("未確認の個人情報", confirmed=False)
        add("hypothesis", "既存の推測", "テスト", path=self.path)
        provider = Mock()
        self.assertEqual(run(self.path, provider)["status"], "no_evidence")
        provider.assert_not_called()
        mid = self.memory()
        provider.return_value = answer([mid])
        self.assertEqual(run(self.path, provider)["status"], "completed")
        inputs = json.loads(provider.call_args.args[0][1]["content"])
        self.assertEqual([m["id"] for m in inputs["memories"]], [mid])
        self.assertNotIn(unconfirmed, list_findings(self.path)[0]["supports"])
        self.assertEqual(run(self.path, provider)["status"], "unchanged")
        self.assertEqual(provider.call_count, 1)

    def test_counterevidence_revises_previous_and_enters_conversation(self):
        first = self.memory()
        run(self.path, lambda _: answer([first]))
        previous = list_findings(self.path)[0]["id"]
        counter = self.memory("今回は数学に長い時間がかかった")
        self.assertEqual(list_findings(self.path), [])
        provider = Mock(return_value=answer([first], [counter], previous))
        self.assertEqual(run(self.path, provider)["status"], "completed")
        row = list_findings(self.path)[0]
        self.assertEqual((row["previous_id"], row["counters"]), (previous, [counter]))
        with patch("personal_ai.llm.chat", return_value="確認しながら考えよう") as chat:
            conversation("数学の負担について相談したい", self.path)
        content = chat.call_args.args[0][1]["content"]
        self.assertIn("AIの未確認仮説", content)
        self.assertIn('"counters":[' + str(counter) + ']', content)
        self.assertIn("状況によるかもしれない", content)
        with closing(memory_db(self.path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM memories").fetchone()[0], 2)

    def test_two_hypotheses_revise_after_changed_evidence_and_stop_on_restart(self):
        first = self.memory("数学は小分けにすると始めやすい")
        second = self.memory("英語は朝に取り組みやすい")

        def two_findings(messages):
            previous = json.loads(messages[1]["content"])["previous_hypotheses"]
            return json.dumps({"findings": [
                {**json.loads(answer([first]))["findings"][0],
                 "previous_id": previous[0]["id"] if previous else None},
                {**json.loads(answer([second]))["findings"][0],
                 "previous_id": previous[1]["id"] if previous else None},
            ]}, ensure_ascii=False)

        provider = Mock(side_effect=two_findings)
        self.assertEqual(run(self.path, provider)["findings"], 2)
        original = [item["id"] for item in list_findings(self.path)]
        self.assertEqual(run(self.path, provider)["status"], "unchanged")
        self.assertEqual(provider.call_count, 1)
        self.memory("数学は昨日小分けでも始められなかった")
        self.assertEqual(run(self.path, provider)["findings"], 2)
        self.assertEqual([item["previous_id"] for item in list_findings(self.path)], original)
        self.assertEqual(run(self.path, provider)["status"], "unchanged")
        self.assertEqual(provider.call_count, 2)
        revise(first, "数学は小分けでも始められない日がある", confirmed=True, path=self.path)
        self.assertEqual(list_findings(self.path), [])
        self.assertEqual(run(self.path, provider)["findings"], 2)
        self.assertEqual(provider.call_count, 3)

    def test_revise_supersede_and_direct_sql_invalidate(self):
        for mutation in ("revise", "supersede", "sql"):
            with self.subTest(mutation=mutation):
                mid = self.memory()
                self.assertEqual(run(self.path, lambda _: answer([mid]))["status"], "completed")
                if mutation == "revise":
                    revise(mid, "数学への理解はまだ不明", confirmed=True, path=self.path)
                elif mutation == "supersede":
                    supersede(mid, "episode", "数学への負担が増えた", "本人訂正", "新しい経験",
                              confirmed=True, path=self.path)
                else:
                    with closing(memory_db(self.path)) as db:
                        db.execute("UPDATE memories SET active=0 WHERE id=?", (mid,))
                        db.commit()
                self.assertEqual(list_findings(self.path), [])

    def test_forget_erases_derived_text_and_revisions(self):
        first = self.memory()
        run(self.path, lambda _: answer([first]))
        previous = list_findings(self.path)[0]["id"]
        second = self.memory("別の数学の経験")
        run(self.path, lambda _: answer([first], [second], previous))
        forget(first, self.path)
        with closing(review_db(self.path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM reasoning_findings").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM reasoning_runs").fetchone()[0], 0)

    def test_concurrent_correction_or_deletion_discards_output(self):
        for deletion in (False, True):
            with self.subTest(deletion=deletion):
                mid = self.memory()

                def provider(_):
                    if deletion:
                        forget(mid, self.path)
                    else:
                        revise(mid, "訂正された根拠", confirmed=True, path=self.path)
                    return answer([mid])

                self.assertEqual(run(self.path, provider)["status"], "evidence_changed")
                self.assertEqual(list_findings(self.path), [])

    def test_invalid_output_cooldown_limit_and_private_error_redaction(self):
        self.memory()
        provider = Mock(side_effect=TimeoutError("秘密のモデル応答をログに出さない"))
        for clock in (0, 3601, 7202):
            self.assertEqual(run(self.path, provider, now=clock)["status"], "failed")
            self.assertEqual(run(self.path, provider, now=clock + 1)["status"], "cooldown"
                             if clock < 7202 else "blocked")
        self.assertEqual(run(self.path, provider, now=11000)["status"], "blocked")
        self.assertEqual(provider.call_count, 3)
        with closing(review_db(self.path)) as db:
            errors = [r[0] for r in db.execute("SELECT error_kind FROM reasoning_runs")]
        self.assertEqual(errors, ["TimeoutError"] * 3)

    def test_fabricated_evidence_nonfinite_confidence_and_bad_json_rejected(self):
        mid = self.memory()
        valid = json.loads(answer([mid]))
        invalid_confidence = json.loads(answer([mid]))
        invalid_confidence["findings"][0]["confidence"] = float("nan")
        invalid_schema = {**valid, "extra": "not allowed"}
        from personal_ai.deliberation import _validate
        for raw in (answer([99999]), "not json", json.dumps(invalid_confidence),
                    json.dumps(invalid_schema), answer([mid], [mid]), answer([], []),
                    answer([mid], previous_id=99999)):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    _validate(raw, {mid}, set())

    def test_empty_review_is_completed_and_not_repeated(self):
        self.memory()
        provider = Mock(return_value='{"findings":[]}')
        self.assertEqual(run(self.path, provider)["findings"], 0)
        self.assertEqual(run(self.path, provider)["status"], "unchanged")
        self.assertEqual(list_findings(self.path), [])
        provider.assert_called_once()

    def test_interrupted_run_uses_failure_budget_and_lock_prevents_overlap(self):
        from personal_ai.app import recording_lock
        mid = self.memory()
        with recording_lock("deliberation", self.path):
            self.assertEqual(run(self.path, Mock())["status"], "already_running")
        run(self.path, lambda _: answer([mid]), now=0)
        with closing(review_db(self.path)) as db:
            db.execute("UPDATE reasoning_runs SET status='RUNNING'")
            db.commit()
        self.assertEqual(run(self.path, Mock(), now=10)["status"], "cooldown")
        self.assertEqual(run(self.path, lambda _: answer([mid]), now=3611)["status"], "completed")

    def test_scheduled_cycle_uses_bounded_review(self):
        from personal_ai.reflection import run_cycle
        with patch("personal_ai.deliberation.run", return_value={"status": "unchanged"}) as mocked:
            self.assertEqual(run_cycle(path=self.path)["deliberation"]["status"], "unchanged")
        mocked.assert_called_once_with(self.path)


if __name__ == "__main__":
    unittest.main()
