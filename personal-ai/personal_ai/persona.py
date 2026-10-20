"""Editable local AI persona, separate from facts learned about the user."""

import argparse
import json
import os
from pathlib import Path
import tempfile


DEFAULT_PATH = Path.home() / ".local/share/personal-ai/persona.json"
FIELDS = ("values", "likes", "dislikes", "interests", "strengths", "conversation_style", "thinking_style", "relationship")
DEFAULT = {
    "version": 1,
    "values": "率直さと相手の自主性を大切にする",
    "likes": "仕組みを一緒に考えること",
    "dislikes": "根拠のない断定や過剰なお世辞",
    "interests": "技術と日々の小さな発見",
    "strengths": "情報の整理と検証",
    "conversation_style": "普段は自然に雑談する。相談されたときに考え、求められていない助言は押し付けない",
    "thinking_style": "事実と推測を分け、間違いを認めて修正する",
    "relationship": "気楽に話せる相手として、会話の積み重ねを大切にする",
    "fictional_background": "",
}


def _path(path=None):
    return Path(path or os.environ.get("PERSONAL_AI_PERSONA", DEFAULT_PATH)).expanduser()


def load(path=None):
    target = _path(path)
    if not target.exists():
        return DEFAULT.copy()
    data = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("人格設定の形式またはバージョンが不正です")
    if set(data) != set(DEFAULT) or any(not isinstance(data[key], str) for key in DEFAULT if key != "version"):
        raise ValueError("人格設定の項目が不正です")
    return data


def save(data, path=None):
    target = _path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Restrict access before writing; replacement is atomic even after a crash.
    fd, temporary = tempfile.mkstemp(prefix=".persona-", suffix=".json", dir=target.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def set_field(field, value, path=None):
    if field not in (*FIELDS, "fictional_background"):
        raise ValueError("設定項目が不正です")
    if not isinstance(value, str) or len(value) > 2000:
        raise ValueError("設定は2000文字以下の文字列にしてください")
    data = load(path)
    data[field] = value.strip()
    save(data, path)
    return data


def system_prompt(path=None):
    data = load(path)
    lines = ["あなたは個人向けAIです。以下は編集可能なAI人格設定で、ユーザーについての事実ではありません。"]
    labels = {
        "values": "価値観", "likes": "好きなこと", "dislikes": "苦手なこと", "interests": "興味",
        "strengths": "得意分野", "conversation_style": "会話の特徴", "thinking_style": "考え方", "relationship": "ユーザーとの関係",
    }
    for key, label in labels.items():
        lines.append(f"{label}: {data[key]}")
    if data["fictional_background"]:
        lines.append(f"架空の背景設定（実体験ではない）: {data['fictional_background']}")
    lines.append("架空の背景を現実に体験したと偽らない。人格設定をユーザーの発言や事実として扱わない。")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="ローカルAI人格設定")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("show")
    setter = sub.add_parser("set")
    setter.add_argument("field", choices=(*FIELDS, "fictional_background"))
    setter.add_argument("value")
    args = parser.parse_args()
    try:
        data = load() if args.action == "show" else set_field(args.field, args.value)
        print(json.dumps(data, ensure_ascii=False, indent=2))
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        parser.exit(1, f"エラー: {exc}\n")


if __name__ == "__main__":
    main()
