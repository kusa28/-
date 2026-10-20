import unittest

from personal_ai.web_views import render_dashboard


class WebViewTests(unittest.TestCase):
    def test_untrusted_content_escaped_and_review_forms_protected(self):
        markup = render_dashboard(
            [{"id": 1, "kind": "hypothesis", "text": '<script>alert(1)</script>',
              "source": 'source" onmouseover="bad', "user_confirmed": 0}],
            [{"id": 2, "kind": "value", "statement": '<img src=x onerror=bad()>',
              "recording_id": 3, "start": 1, "end": 2, "status": "PENDING"}],
            [{"period": "daily", "start_date": "2026-10-20", "end_date": "2026-10-21",
              "conclusion": '<b>fake</b>', "sources": [{"id": 1}]}],
            {"values": '<svg onload=bad()>'}, 'token" dangerous')
        self.assertNotIn("<script>", markup)
        self.assertNotIn("<img", markup)
        self.assertNotIn("<svg", markup)
        self.assertNotIn("<b>fake", markup)
        self.assertIn('&lt;script&gt;', markup)
        self.assertIn('value="token&quot; dangerous"', markup)
        self.assertEqual(markup.count('name="csrf"'), 5)
        self.assertIn('name="decision" value="accept"', markup)
        self.assertIn('name="decision" value="reject"', markup)
        self.assertIn("仮説", markup)
        self.assertIn("架空の背景設定", markup)
        self.assertIn('/memory-candidate/clip?id=2', markup)

    def test_empty_sections_and_non_pending_candidates(self):
        markup = render_dashboard([], [{"status": "ACCEPTED", "id": 9}], [], {}, "tok")
        self.assertIn("記憶はまだありません", markup)
        self.assertIn("承認待ちの候補はありません", markup)
        self.assertIn("有効な考察はまだありません", markup)
        self.assertNotIn('name="candidate_id"', markup)

    def test_deliberations_show_hypothesis_evidence_and_revision(self):
        markup = render_dashboard([], [], [], {}, "tok", deliberations=[{
            "id": 12, "purpose": "取り組みやすい時間を検討する",
            "statement": "夕方のほうが取り組みやすい可能性がある",
            "confidence": 0.4, "rationale": "時間帯の異なる記録を比較した",
            "next_check": "別の日も同じ傾向か本人に確認する",
            "supports": [3, 5], "counters": [7], "previous_id": 10,
            "created_at": "2026-10-20 12:00:00",
        }])
        self.assertIn("AIの仮説 #12", markup)
        self.assertIn("本人確認済みの事実ではありません", markup)
        self.assertIn("確信度（モデル自己評価）", markup)
        self.assertIn("<dt>支持する記憶ID</dt><dd>#3, #5</dd>", markup)
        self.assertIn("<dt>反証となる記憶ID</dt><dd>#7</dd>", markup)
        self.assertIn("<dt>改訂元の考察ID</dt><dd>#10</dd>", markup)
        self.assertIn("別の日も同じ傾向か本人に確認する", markup)
        self.assertIn("時間帯の異なる記録を比較した", markup)
        self.assertIn("2026-10-20 12:00:00", markup)
        self.assertNotIn('name="decision"', markup)

    def test_deliberation_output_is_escaped_in_every_field(self):
        attack = '<img src=x onerror="bad()">'
        finding = {field: attack for field in
                   ("id", "purpose", "statement", "confidence", "rationale",
                    "next_check", "previous_id", "created_at")}
        finding["supports"] = [attack]
        finding["counters"] = [attack]
        markup = render_dashboard([], [], [], {}, "tok", deliberations=[finding])
        self.assertNotIn("<img", markup)
        self.assertEqual(markup.count('&lt;img src=x onerror=&quot;bad()&quot;&gt;'), 10)


if __name__ == "__main__":
    unittest.main()
