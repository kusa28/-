"""Score anonymous diarization against consented, manually timed turns.

Inputs are in-memory turn dictionaries; this module never opens audio or a DB.
"""

from itertools import permutations
import math


def _validate(turns):
    result = []
    for turn in turns:
        start, end = float(turn["start"]), float(turn["end"])
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise ValueError("invalid turn time")
        result.append((start, end, str(turn["speaker"])))
    return result


def score(reference, predicted):
    """Return permutation-invariant, overlap-aware duration errors in seconds.

    False alarm, missed speech and speaker confusion are measured by speaker
    time. Overlap precision/recall use wall-clock duration with >=2 speakers.
    No collar is applied at boundaries.
    """
    ref, pred = _validate(reference), _validate(predicted)
    labels_ref = sorted({label for _, _, label in ref})
    labels_pred = sorted({label for _, _, label in pred})
    if len(labels_pred) > 8:
        raise ValueError("too many predicted speakers for exact matching")
    boundaries = sorted({time for start, end, _ in ref + pred for time in (start, end)})
    slices = []
    for start, end in zip(boundaries, boundaries[1:]):
        if start == end:
            continue
        active_ref = {label for left, right, label in ref if left < end and start < right}
        active_pred = {label for left, right, label in pred if left < end and start < right}
        slices.append((end - start, active_ref, active_pred))
    # Anonymous labels are local to a recording. Choose the global assignment
    # maximizing correctly attributed speaker time, including overlap.
    best_mapping = {}
    best_correct = -1.0
    targets = labels_ref + [None] * max(0, len(labels_pred) - len(labels_ref))
    for assigned in permutations(targets, len(labels_pred)):
        mapping = dict(zip(labels_pred, assigned))
        correct = sum(duration * len(ref_set & {mapping[label] for label in pred_set})
                      for duration, ref_set, pred_set in slices)
        if correct > best_correct:
            best_correct, best_mapping = correct, mapping
    reference_time = missed = false_alarm = confusion = 0.0
    overlap_reference = overlap_prediction = overlap_correct = 0.0
    for duration, ref_set, pred_set in slices:
        reference_time += duration * len(ref_set)
        missed += duration * max(0, len(ref_set) - len(pred_set))
        false_alarm += duration * max(0, len(pred_set) - len(ref_set))
        confusion += duration * (min(len(ref_set), len(pred_set)) -
                               len(ref_set & {best_mapping[label] for label in pred_set}))
        ref_overlap, pred_overlap = len(ref_set) >= 2, len(pred_set) >= 2
        overlap_reference += duration * ref_overlap
        overlap_prediction += duration * pred_overlap
        overlap_correct += duration * (ref_overlap and pred_overlap)
    return {
        "reference_speaker_seconds": round(reference_time, 6),
        "missed_speaker_seconds": round(missed, 6),
        "false_alarm_speaker_seconds": round(false_alarm, 6),
        "confusion_speaker_seconds": round(confusion, 6),
        "der": (missed + false_alarm + confusion) / reference_time if reference_time else None,
        "overlap_precision": overlap_correct / overlap_prediction if overlap_prediction else None,
        "overlap_recall": overlap_correct / overlap_reference if overlap_reference else None,
    }
