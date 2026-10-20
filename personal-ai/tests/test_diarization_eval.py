import unittest

from personal_ai.diarization_eval import score


class DiarizationEvaluationTests(unittest.TestCase):
    def test_permuted_labels_and_overlap(self):
        reference = [{"start": 0, "end": 2, "speaker": "A"},
                     {"start": 1, "end": 3, "speaker": "B"}]
        predicted = [{"start": 0, "end": 2, "speaker": "SPEAKER_01"},
                     {"start": 1, "end": 3, "speaker": "SPEAKER_00"}]
        result = score(reference, predicted)
        self.assertEqual(result["der"], 0)
        self.assertEqual(result["overlap_precision"], 1)
        self.assertEqual(result["overlap_recall"], 1)

    def test_boundary_and_missed_overlap(self):
        reference = [{"start": 0, "end": 2, "speaker": "A"},
                     {"start": 1, "end": 2, "speaker": "B"}]
        predicted = [{"start": 0.25, "end": 2, "speaker": "SPEAKER_00"}]
        result = score(reference, predicted)
        self.assertEqual(result["missed_speaker_seconds"], 1.25)
        self.assertAlmostEqual(result["der"], 1.25 / 3)
        self.assertEqual(result["overlap_recall"], 0)
        self.assertIsNone(result["overlap_precision"])

    def test_empty_and_invalid(self):
        self.assertIsNone(score([], [])["der"])
        with self.assertRaises(ValueError):
            score([{"start": 2, "end": 1, "speaker": "A"}], [])


if __name__ == "__main__":
    unittest.main()
