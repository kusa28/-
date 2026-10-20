import json
from contextlib import closing
import tempfile
import unittest
from pathlib import Path

from personal_ai import consolidation as c, memory, deliberation


class ConsolidationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'test.sqlite3'
        self.a = memory.add('value', '架空人物は 数学 が好き', 'synthetic:a', confirmed=True, path=self.path)
        self.b = memory.add('value', '架空人物は   数学 が好き', 'synthetic:b', confirmed=True, path=self.path)

    def response(self, relation='change_candidate'):
        return json.dumps({'relations': [{'left_id': self.a, 'right_id': self.b,
            'relation': relation, 'reason': '架空の資料の時期・背景を本人に確認する必要がある', 'confidence': 0.4}]})

    def test_exact_does_not_merge_or_confirm(self):
        result = c.run(self.path)
        self.assertEqual(result['relations'], 1)
        item = c.list_relations(self.path)[0]
        self.assertEqual(item['relation'], 'same_claim')
        self.assertFalse(item['verified'])
        self.assertEqual(len(memory.list_memories(self.path)), 2)
        self.assertEqual(c.run(self.path)['status'], 'unchanged')
        self.assertEqual(len(c.inspect(self.a, self.path)['relations']), 1)

    def test_edit_and_delete_invalidate_without_destroying_claims(self):
        c.run(self.path)
        memory.revise(self.b, '数学が以前より分かるようになった', path=self.path)
        self.assertEqual(c.list_relations(self.path), [])
        self.assertEqual(c.history(self.path)[0]['status'], 'STALE')
        self.assertEqual(len(c.inspect(self.b, self.path)['revisions']), 1)
        self.assertEqual(c.run(self.path)['relations'], 0)
        memory.forget(self.a, self.path)
        self.assertEqual(c.history(self.path), [])
        self.assertEqual(len(memory.list_memories(self.path)), 1)

    def test_unrelated_new_information_keeps_existing_pair_current(self):
        c.run(self.path)
        memory.add('current', '架空の新しい出来事', 'synthetic:c', path=self.path)
        current_before_rerun = c.list_relations(self.path)
        self.assertEqual(len(current_before_rerun), 1)
        self.assertEqual((current_before_rerun[0]['left_id'], current_before_rerun[0]['right_id']),
                         tuple(sorted((self.a, self.b))))
        self.assertEqual(c.run(self.path)['relations'], 1)
        current_after_rerun = c.list_relations(self.path)
        self.assertEqual(len(current_after_rerun), 1)
        self.assertTrue(current_after_rerun[0]['current'])

    def test_legacy_batch_relation_is_migrated_only_for_matching_snapshot(self):
        c.run(self.path)
        with closing(c.organization_db(self.path)) as db:
            db.execute('DROP TABLE consolidation_relation_sources')
        self.assertEqual(len(c.list_relations(self.path)), 1)
        memory.revise(self.b, '訂正後の別内容', path=self.path)
        with closing(c.organization_db(self.path)) as db:
            db.execute('DROP TABLE consolidation_relation_sources')
        self.assertEqual(c.list_relations(self.path), [])

    def test_edit_stales_only_relations_using_that_memory(self):
        other = memory.add('value', '別の架空人物は理科が好き', 'synthetic:other', path=self.path)
        third = memory.add('value', '別の架空人物は理科が好き', 'synthetic:other-duplicate', path=self.path)
        c.run(self.path)
        self.assertEqual(len(c.list_relations(self.path)), 2)
        memory.revise(self.b, 'この主張を訂正した', path=self.path)
        current = c.list_relations(self.path)
        self.assertEqual(len(current), 1)
        self.assertEqual((current[0]['left_id'], current[0]['right_id']), tuple(sorted((other, third))))

    def test_optional_semantic_is_only_suggestion(self):
        result = c.run(self.path, lambda _: self.response(), semantic=True)
        self.assertEqual(result['relations'], 2)
        self.assertTrue(all(not x['verified'] for x in c.list_relations(self.path)))
        self.assertEqual(len(memory.list_memories(self.path)), 2)

    def test_failed_retry_is_bounded_and_logs_no_response(self):
        provider = lambda _: 'PRIVATE INVALID OUTPUT'
        self.assertEqual(c.run(self.path, provider, semantic=True, now=0)['status'], 'failed')
        self.assertEqual(c.run(self.path, provider, semantic=True, now=1)['status'], 'cooldown')
        for now in (3601, 7202):
            self.assertEqual(c.run(self.path, provider, semantic=True, now=now)['status'], 'failed')
        self.assertEqual(c.run(self.path, provider, semantic=True, now=10803)['status'], 'blocked')
        self.assertNotIn('PRIVATE', str(c.history(self.path)))

    def test_concurrent_edit_during_inference_rejected(self):
        def provider(_):
            memory.revise(self.a, '訂正した架空の発言', path=self.path)
            return self.response()
        self.assertEqual(c.run(self.path, provider, semantic=True)['status'], 'evidence_changed')
        self.assertEqual(c.list_relations(self.path), [])

    def test_forged_sources_and_nan_rejected(self):
        for payload in (
            {'left_id': self.a, 'right_id': 999, 'relation': 'related', 'reason': 'x', 'confidence': .4},
            {'left_id': self.a, 'right_id': self.b, 'relation': 'related', 'reason': 'x', 'confidence': float('nan')},
        ):
            with self.assertRaises(ValueError):
                c._validate(json.dumps({'relations': [payload]}), {self.a, self.b})

    def test_organization_and_existing_hypothesis_revision_integration(self):
        def thought(previous=None):
            return json.dumps({'findings': [{'previous_id': previous, 'purpose': '架空の興味を検討',
                'statement': '数学への関心が変化した可能性', 'rationale': '資料の範囲だけの仮説',
                'next_check': '本人に確認', 'confidence': .4, 'supports': [self.a], 'counters': []}]})
        c.run(self.path)
        deliberation.run(self.path, lambda _: thought())
        first = deliberation.list_findings(self.path)[0]['id']
        memory.add('current', '架空人物は理科にも興味を持ったと述べた', 'synthetic:c', confirmed=True, path=self.path)
        c.run(self.path)
        deliberation.run(self.path, lambda _: thought(first))
        self.assertEqual(deliberation.list_findings(self.path)[0]['previous_id'], first)
        self.assertEqual(len(memory.list_memories(self.path)), 3)


if __name__ == '__main__':
    unittest.main()
