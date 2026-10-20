"""Local-only language model connection."""

import json
import os
import urllib.error
import urllib.request


def chat(messages, model=None, *, json_mode=False, max_tokens=512, seed=None, temperature=None, json_schema=None):
    selected_model = model or os.environ.get("PERSONAL_AI_OLLAMA_MODEL", "qwen3:4b-instruct")
    if selected_model.lower().endswith(":cloud"):
        raise ValueError("クラウドモデルは使用できません")
    if not 1 <= max_tokens <= 2048:
        raise ValueError("出力上限は1～2048トークンで指定してください")
    payload = {"model": selected_model, "stream": False, "think": False,
               "options": {"num_predict": max_tokens}, "messages": messages}
    if json_mode or json_schema is not None:
        payload["format"] = json_schema if json_schema is not None else "json"
        payload["options"]["num_ctx"] = 8192
    if seed is not None:
        payload["options"]["seed"] = seed
    if temperature is not None:
        payload["options"]["temperature"] = temperature
    request = urllib.request.Request("http://127.0.0.1:11434/api/chat", json.dumps(payload).encode(), {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.load(response)["message"]["content"]
    except urllib.error.HTTPError as exc:
        try:
            detail = json.load(exc).get("error", str(exc))
        except (ValueError, OSError):
            detail = str(exc)
        raise RuntimeError(f"Ollamaの応答エラー: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError("Ollamaに接続できません。ollama serve とモデル取得を確認してください") from exc
