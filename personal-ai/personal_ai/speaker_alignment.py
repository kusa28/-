"""Join transcript timestamps to anonymous speaker turns conservatively."""

from .app import get_transcript
from .diarization import list_turns


def _shared(start, end, turn):
    return max(0.0, min(end, turn["end"]) - max(start, turn["start"]))


def _has_overlap(start, end, turns):
    relevant = [turn for turn in turns if _shared(start, end, turn) > 0]
    for index, first in enumerate(relevant):
        for second in relevant[index + 1:]:
            if first["speaker_label"] != second["speaker_label"] and \
                    max(start, first["start"], second["start"]) < \
                    min(end, first["end"], second["end"]):
                return True
    return False


def assign_speaker(segment, exclusive_turns, regular_turns):
    start, end = float(segment["start"]), float(segment["end"])
    duration = end - start
    if duration <= 0:
        return {"speaker_label": "UNKNOWN", "overlap": False,
                "speaker_source": "model_prediction" if exclusive_turns else "unavailable"}
    coverage = {}
    for turn in exclusive_turns:
        amount = _shared(start, end, turn)
        if amount:
            label = turn["speaker_label"]
            coverage[label] = coverage.get(label, 0.0) + amount
    ranked = sorted(coverage.items(), key=lambda item: item[1], reverse=True)
    chosen = "UNKNOWN"
    if ranked:
        best_label, best = ranked[0]
        runner = ranked[1][1] if len(ranked) > 1 else 0.0
        if best >= 0.6 * duration and best - runner >= 0.2 * duration:
            chosen = best_label
    return {"speaker_label": chosen,
            "overlap": _has_overlap(start, end, regular_turns),
            "speaker_source": "model_prediction" if exclusive_turns else "unavailable"}


def annotated_transcript(recording_id, db_path=None):
    recording, segments = get_transcript(recording_id, db_path)
    regular = list_turns(recording_id, db_path)
    exclusive = list_turns(recording_id, db_path, exclusive=True)
    return recording, [segment | assign_speaker(segment, exclusive, regular)
                       for segment in segments]
