"""Broadcast script (台本) generation: fixed-format output and API flow."""
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.ai import gateway
from app.domain import scripts as script_domain
from app.storage.db import one, execute
from test_m3_ai_flow import create_provider, create_model, route_payload, save_routes


def _workspace_day(client):
    with client.engine.connect() as conn:
        tz = one(conn, 'SELECT timezone FROM workspace_settings')['timezone']
    return datetime.now(ZoneInfo(tz)).date()


def _insert_digest(client, markdown='# 日报\n\n今日总结内容。\n\n## 今日视频\n- **视频**：介绍工具 A\n\n## 今日实体\n- **工具 A**（工具）：离线工具'):
    with client.engine.begin() as conn:
        execute(conn, """INSERT INTO daily_digests(workspace_id,day,summary,markdown,video_count)
            VALUES((SELECT id FROM workspace_settings),:day,:summary,:markdown,1)
            ON CONFLICT(workspace_id,day) DO UPDATE SET markdown=EXCLUDED.markdown""",
                day=_workspace_day(client), summary='今日总结内容。', markdown=markdown)


def test_normalize_expressions():
    assert script_domain.normalize_expressions(['微笑', ' 认真 ', '微笑', '', 3]) == ['微笑', '认真']
    assert script_domain.normalize_expressions([]) == script_domain.DEFAULT_EXPRESSIONS
    assert script_domain.normalize_expressions(None) == script_domain.DEFAULT_EXPRESSIONS


def test_script_output_fixed_format():
    parsed = {'segments': [
        {'expression': '认真', 'text': '今天我们关注工具 A。'},
        {'expression': '不存在表情', 'text': '被纠正为预设表情。'},
        {'expression': '微笑', 'text': '   '},
        {'text': '没有表情字段的片段。'},
    ]}
    segments = script_domain.script_output(parsed, ['微笑', '认真'], '各位观众朋友们，大家好！')
    # Opening is pinned to the front verbatim; bad expressions fall back; blanks dropped.
    assert segments[0]['text'] == '各位观众朋友们，大家好！'
    assert segments[0]['expression'] == '微笑'
    assert segments[1]['expression'] == '认真'
    assert any(s['text'] == '被纠正为预设表情。' and s['expression'] == '微笑' for s in segments)
    assert any(s['text'] == '没有表情字段的片段。' for s in segments)
    assert not any(s['text'] == '' for s in segments)


def test_script_output_rejects_malformed():
    with pytest.raises(ValueError):
        script_domain.script_output(None, ['微笑'], '开场白')
    with pytest.raises(ValueError):
        script_domain.script_output({'segments': []}, ['微笑'], '开场白')


def test_config_default_and_save(api):
    first = api.get('/api/v1/simple/script').json()
    assert first['script'] is None
    assert first['config']['opening'] == script_domain.DEFAULT_OPENING
    assert first['config']['expressions'] == script_domain.DEFAULT_EXPRESSIONS

    saved = api.put('/api/v1/simple/script-config', json={
        'opening': '欢迎收看今天的 FishingNet 播报。',
        'anchor_persona': '你是一名亲切的主播。',
        'expressions': ['微笑', ' 认真 ', '微笑', '严肃'],
    })
    assert saved.status_code == 200, saved.text
    assert saved.json()['config']['expressions'] == ['微笑', '认真', '严肃']

    again = api.get('/api/v1/simple/script').json()
    assert again['config']['opening'] == '欢迎收看今天的 FishingNet 播报。'
    assert again['config']['anchor_persona'] == '你是一名亲切的主播。'
    assert again['config']['expressions'] == ['微笑', '认真', '严肃']


@pytest.fixture
def script_api(api):
    provider = create_provider(api)
    model = create_model(api, provider['id'])
    result = save_routes(api, [route_payload('digest_polish', model['id'],
                                              max_input_tokens=20000, max_output_tokens=3000)])
    assert result.status_code == 200, result.text
    _insert_digest(api)
    return api


def test_generate_requires_digest(api):
    response = api.post('/api/v1/simple/script/generate', json={})
    assert response.status_code == 409
    assert '还没有生成日报' in response.json()['error']['message']


def test_generate_requires_route(api):
    _insert_digest(api)
    response = api.post('/api/v1/simple/script/generate', json={})
    assert response.status_code == 409
    assert '日报润色' in response.json()['error']['message']


def test_generate_script_and_persist(script_api, monkeypatch):
    seen = {}

    def invoke(task, **kwargs):
        seen['task'] = task
        system = kwargs['messages'][0]['content']
        assert '微笑、认真、放松' in system
        assert '开场白：' in system
        return {'parsed': {'segments': [
            {'expression': '微笑', 'text': '各位观众朋友们，大家好！'},
            {'expression': '认真', 'text': '今天我们关注工具 A 的离线能力。'},
            {'expression': '放松', 'text': '感谢收看，我们下期再见。'},
        ]}, 'invocation_id': str(uuid.uuid4())}

    monkeypatch.setattr(gateway, 'invoke_messages', invoke)
    opening = '各位观众朋友们，大家好！'
    response = script_api.post('/api/v1/simple/script/generate', json={
        'opening': opening, 'anchor_persona': '你是一名亲切的主播。',
        'expressions': ['微笑', '认真', '放松'],
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert seen['task'] == 'digest_polish'
    assert body['segments'][0]['text'] == opening
    assert all(s['expression'] in ('微笑', '认真', '放松') for s in body['segments'])

    persisted = script_api.get('/api/v1/simple/script').json()
    assert persisted['script']['segments'] == body['segments']
    assert persisted['script']['opening'] == opening


def test_generate_bad_model_output_is_rejected(script_api, monkeypatch):
    monkeypatch.setattr(gateway, 'invoke_messages',
                        lambda *a, **k: {'parsed': {'segments': []}, 'invocation_id': 'x'})
    response = script_api.post('/api/v1/simple/script/generate', json={})
    assert response.status_code == 502
    assert '格式不符合要求' in response.json()['error']['message']
