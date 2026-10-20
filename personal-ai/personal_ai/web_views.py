"""Escaped, read-only local dashboard markup and explicit review controls."""

from html import escape


PERSONA_LABELS = {
    "values": "価値観", "likes": "好きなこと", "dislikes": "苦手なこと",
    "interests": "興味", "strengths": "得意分野",
    "conversation_style": "会話の特徴", "thinking_style": "考え方",
    "relationship": "ユーザーとの関係", "fictional_background": "架空の背景設定",
}
KIND_LABELS = {
    "episode": "出来事", "semantic": "情報", "value": "価値観・希望",
    "hypothesis": "仮説", "current": "現在の状況", "relationship": "AIとの関係",
}


def _e(value):
    return escape(str(value if value is not None else ""), quote=True)


def render_dashboard(memories, candidates, reflections, persona, csrf_token, deliberations=()):
    """Render already loaded local data; callers enforce CSRF and review action.

    Text from recordings, memories and model output is untrusted. This function
    interpolates no such text into script, CSS or raw HTML.
    """
    sections = ['<!doctype html><html lang="ja"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                '<title>個人AIの記録</title></head><body><a href="/">録音へ戻る</a>'
                '<h1>記憶と振り返り</h1>']
    sections.append('<section><h2>記憶</h2><ul>')
    sections.append('<form method="post" action="/memory/add">'
                    f'<input type="hidden" name="csrf" value="{_e(csrf_token)}">'
                    '<label>種類 <select name="kind">'
                    '<option value="episode">出来事</option><option value="semantic">比較的安定した情報</option>'
                    '<option value="value">価値観・希望</option><option value="current">現在の状況</option>'
                    '<option value="relationship">AIとの関係</option><option value="hypothesis">仮説</option>'
                    '</select></label><textarea name="text" maxlength="4000" required placeholder="自分で確認した内容や仮説を入力"></textarea>'
                    '<button>記憶を追加</button></form><p>本人が直接入力した記憶は本人確認済みとして保存します。仮説は未確認のままです。</p>')
    if not memories:
        sections.append('<li>記憶はまだありません</li>')
    for row in memories:
        kind = row.get("kind", "")
        status = "仮説" if kind == "hypothesis" else (
            "本人確認済み" if row.get("user_confirmed") else "未確認")
        source = str(row.get("source", ""))
        source_link = (f' <a href="/organize#event-{_e(source.removeprefix("organized_event:"))}">根拠の出来事を見る</a>'
                       if source.startswith('organized_event:') and source.removeprefix('organized_event:').isdigit() else '')
        sections.append(f'<li>#{_e(row.get("id"))} [{_e(KIND_LABELS.get(kind, kind))}／{_e(status)}] '
                        f'{_e(row.get("text"))} <small>情報源: {_e(source)}</small>{source_link}'
                        f'<details><summary>根拠・訂正・削除</summary><p>{_e(row.get("evidence"))}</p>'
                        f'<p>出来事日時: {_e(row.get("occurred_at")) or "不明"} / 取得日時: {_e(row.get("observed_at"))}</p>'
                        '<form method="post" action="/memory/edit">'
                        f'<input type="hidden" name="csrf" value="{_e(csrf_token)}">'
                        f'<input type="hidden" name="id" value="{_e(row.get("id"))}">'
                        f'<textarea name="text" maxlength="4000" required>{_e(row.get("text"))}</textarea>'
                        '<label><input type="checkbox" name="confirmed" value="1">本人確認済み（仮説には不可）</label>'
                        '<button>履歴を残して訂正</button></form>'
                        '<form method="post" action="/memory/delete">'
                        f'<input type="hidden" name="csrf" value="{_e(csrf_token)}">'
                        f'<input type="hidden" name="id" value="{_e(row.get("id"))}">'
                        '<label><input type="checkbox" name="confirm_delete" value="1" required>この派生記憶を削除する</label>'
                        '<button>削除（元録音は保持）</button></form></details></li>')
    sections.append('</ul></section><section><h2>承認待ちの記憶候補</h2>'
                    '<p>録音内の発言は話者を確定していません。内容と発言者を確認してから承認してください。</p><ul>')
    pending = [row for row in candidates if row.get("status", "PENDING") == "PENDING"]
    if not pending:
        sections.append('<li>承認待ちの候補はありません</li>')
    for row in pending:
        candidate_id = _e(row.get("id"))
        sections.append(f'<li>#{candidate_id} [{_e(KIND_LABELS.get(row.get("kind"), row.get("kind")))}] '
                        f'{_e(row.get("statement"))} '
                        f'<small>録音 #{_e(row.get("recording_id"))} '
                        f'{_e(row.get("start"))}–{_e(row.get("end"))}秒</small>'
                        f'<audio controls preload="none" src="/memory-candidate/clip?id={candidate_id}"></audio>')
        for decision, label in (("accept", "承認"), ("reject", "却下")):
            sections.append('<form method="post" action="/memory-candidate/review">'
                            f'<input type="hidden" name="csrf" value="{_e(csrf_token)}">'
                            f'<input type="hidden" name="candidate_id" value="{candidate_id}">'
                            f'<input type="hidden" name="decision" value="{decision}">'
                            f'<button type="submit">{label}</button></form>')
        sections.append('</li>')
    sections.append('</ul></section><section><h2>日次・週次の振り返り</h2><ul>')
    if not reflections:
        sections.append('<li>振り返りはまだありません</li>')
    for row in reflections:
        period = "日次" if row.get("period") == "daily" else "週次" if row.get("period") == "weekly" else row.get("period")
        source_ids = ", ".join(f'#{_e(source.get("id"))}' for source in row.get("sources", []))
        sections.append(f'<li>[{_e(period)} {_e(row.get("start_date"))}–{_e(row.get("end_date"))}] '
                        f'{_e(row.get("conclusion"))} <small>根拠となる記憶: {source_ids or "なし"}</small></li>')
    sections.append('</ul></section><section><h2>根拠付きの考察</h2>'
                    '<p>以下はAIの仮説です。本人確認済みの事実ではありません。'
                    '確信度はモデルの自己評価で、正しさの確率を保証するものではありません。</p><ul>')
    if not deliberations:
        sections.append('<li>有効な考察はまだありません</li>')
    for row in deliberations:
        support_ids = ", ".join(f'#{_e(memory_id)}' for memory_id in row.get("supports", []))
        counter_ids = ", ".join(f'#{_e(memory_id)}' for memory_id in row.get("counters", []))
        sections.append(f'<li><p><b>AIの仮説 #{_e(row.get("id"))}</b>: '
                        f'{_e(row.get("statement"))}</p><dl>'
                        f'<dt>考察の目的</dt><dd>{_e(row.get("purpose"))}</dd>'
                        f'<dt>判断理由</dt><dd>{_e(row.get("rationale"))}</dd>'
                        f'<dt>支持する記憶ID</dt><dd>{support_ids or "なし"}</dd>'
                        f'<dt>反証となる記憶ID</dt><dd>{counter_ids or "なし"}</dd>'
                        f'<dt>次に確認すること</dt><dd>{_e(row.get("next_check"))}</dd>'
                        f'<dt>確信度（モデル自己評価）</dt><dd>{_e(row.get("confidence"))}</dd>'
                        f'<dt>作成日時</dt><dd>{_e(row.get("created_at"))}</dd>')
        if row.get("previous_id") is not None:
            sections.append(f'<dt>改訂元の考察ID</dt><dd>#{_e(row.get("previous_id"))}</dd>')
        sections.append('</dl></li>')
    sections.append('</ul></section><section><h2>AIの人格設定</h2>'
                    '<p>以下はAIの設定です。ユーザーの事実やAIの実体験ではありません。</p><dl>')
    for key, label in PERSONA_LABELS.items():
        sections.append(f'<dt>{_e(label)}</dt><dd>{_e(persona.get(key, ""))}</dd>')
    sections.append('</dl></section></body></html>')
    return "".join(sections)
