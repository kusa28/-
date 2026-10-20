import unittest

from personal_ai.transcript_quality import suspicious_segments


class TranscriptQualityTests(unittest.TestCase):
    def test_flags_repeated_phrase_and_consecutive_identical_segments(self):
        rows = [{"id": 1, "text": "今日は楽しかった", "corrected_at": None},
                {"id": 2, "text": "そうそう" * 8, "corrected_at": None}]
        rows += [{"id": i, "text": "同じ短い発言", "corrected_at": None}
                 for i in range(3, 7)]
        flags = suspicious_segments(rows)
        self.assertNotIn(1, flags)
        self.assertEqual(set(flags), {2, 3, 4, 5, 6})

    def test_correction_breaks_repeated_run(self):
        rows = [{"id": i, "text": "同じ短い発言", "corrected_at": None}
                for i in range(1, 4)]
        rows.append({"id": 4, "text": "同じ短い発言", "corrected_at": "2026-10-20"})
        self.assertEqual(suspicious_segments(rows), {})


if __name__ == "__main__":
    unittest.main()
