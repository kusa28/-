"""Conservative markers for obvious ASR loops; never rewrite source text."""

import re

_REPEATED_CHAR = re.compile(r"(.)\1{9,}")
_REPEATED_PHRASE = re.compile(r"(.{2,20}?)\1{4,}")


def suspicious_segments(segments):
    """Return segment ID to reason for text that needs listening review.

    A user correction is treated as reviewed. These heuristics flag uncertainty,
    not proof that the speech is absent from the source audio.
    """
    rows = [dict(segment) for segment in segments]
    reasons = {}
    for row in rows:
        if row.get("corrected_at"):
            continue
        text = row["text"].strip()
        if text.startswith("［文字起こし不確実：") or _REPEATED_CHAR.search(text) or _REPEATED_PHRASE.search(text):
            reasons[row["id"]] = "同じ文字列の過剰な反復"
    run = []
    previous = None
    for row in rows + [None]:
        text = row["text"].strip() if row else None
        if row and not row.get("corrected_at") and text and text == previous:
            run.append(row)
            continue
        if len(run) >= 4:
            for member in run:
                reasons.setdefault(member["id"], "同じ発言の連続反復")
        run = [row] if row and not row.get("corrected_at") and text else []
        previous = text if run else None
    return reasons
