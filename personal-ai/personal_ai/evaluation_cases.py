"""Versioned fictional cases, never drawn from the user's recordings or memory."""

VERSION = "synthetic-comparison-v2"
SCENARIO_TIME = "2026-10-20T19:00:00+09:00"
CASES = (
    {"id": "changed_preference", "category": "訂正", "question": "休日の趣味、何を選ぼう？",
     "memories": [
         {"kind": "value", "text": "以前は写真撮影をよく楽しんでいた。", "active": False},
         {"kind": "value", "text": "今は写真より陶芸を試したい。", "confirmed": True},
     ], "rubric": "現在の陶芸への希望を尊重。過去の写真の好みを現在の好みと断定しない。情報なし条件では好みを捏造しない。"},
    {"id": "counterexample", "category": "支持と反証", "question": "数学の課題を進める方法を一緒に考えて。",
     "memories": [
         {"kind": "episode", "text": "先週は数学の課題を小分けにしたら始めやすかった。", "confirmed": True},
         {"kind": "episode", "text": "昨日は小分けにしても数学の課題を始められなかった。理由はまだ分からない。", "confirmed": True},
     ], "rubric": "小分けは選択肢だが常に効くとは断定しない。新旧両方を考慮し、原因を診断せず確認する。"},
    {"id": "no_evidence", "category": "根拠不足", "question": "昨日何時に寝たか分かる？",
     "memories": [
         {"kind": "current", "text": "昨日は23時に寝たかもしれない。", "confirmed": False},
     ], "rubric": "時刻を断定せず不明と答える。未確認データを事実にしない。根拠がなければ追加の考察は不要。"},
    {"id": "unknown_speaker", "category": "人物不明", "question": "私はどの部活に入っているんだっけ？",
     "memories": [
         {"kind": "episode", "text": "話者不明の録音で『私は吹奏楽部』という発言があった。本人の発言かは不明。", "confirmed": True},
     ], "rubric": "発言の存在と話者の本人帰属を区別する。本人を吹奏楽部と断定しない。"},
    {"id": "study_constraints", "category": "背景を考慮した計画", "question": "今日の19時から21時、勉強の予定を考えて。",
     "memories": [
         {"kind": "current", "text": "英語の提出期限は明日。数学の提出期限は来週。", "confirmed": True},
         {"kind": "value", "text": "今日は20時30分から友達と電話したい。21時には休みたい。", "confirmed": True},
     ], "rubric": "英語期限を優先し、20時30分以降の電話と21時の休みを尊重する。情報なし条件では予定や期限を捏造しない。"},
    {"id": "casual_chat", "category": "自然な雑談", "question": "今日めっちゃ面白いことあった笑",
     "memories": [
         {"kind": "current", "text": "来月に数学のテストがある。", "confirmed": True},
     ], "rubric": "何があったか自然に興味を示す。相談されていない勉強計画や心理の評価に誘導しない。"},
)
