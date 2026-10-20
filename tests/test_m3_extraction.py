"""M3 Phase 2: routed claim extraction into ``process_source``.

The integration tests never reach a real provider: ``httpx.MockTransport`` returns a
deterministic JSON answer. They assert the server-side anchor validation (quotes,
code-point offsets, hashes), the publication threshold, budget reservation before
the HTTP call, settlement afterwards and the invocation ledger.
"""
import json
import uuid

import httpx
import pytest
from sqlalchemy import text

import app.jobs.worker as worker
from app.ai import extraction as ai_extraction
from app.ai import providers as ai_providers
from app.domain.evidence import digest
from app.storage.db import many, one

CONTENT = 'Plugin X 2.0 在 Windows 上离线索引运行正常。\n\n作者认为界面配色过于刺眼，日常使用不舒服。'


# ------------------------------------------------------------------ unit tests

def synthetic_segments():
    return [
        {'segment_id': 'seg_1', 'text': 'Plugin X 2.0 在 Windows 上离线索引运行正常。', 'content_hash': 'h1'},
        {'segment_id': 'seg_2', 'text': '作者认为界面配色过于刺眼，日常使用不舒服。', 'content_hash': 'h2'},
    ]


def messages_for(segments=None, max_input_tokens=4000):
    return ai_extraction.build_claim_messages(segments or synthetic_segments(),
                                              max_input_tokens=max_input_tokens)


def test_prompt_is_bounded_json_and_marks_material_as_data():
    messages, coverage = messages_for()
    assert [item['role'] for item in messages] == ['system', 'user']
    assert '不要执行' in messages[0]['content']
    payload = json.loads(messages[1]['content'])
    assert [item['segment_id'] for item in payload['segments']] == ['seg_1', 'seg_2']
    assert coverage['included_segments'] == 2 and coverage['truncated'] is False
    assert coverage['included']['seg_1'] == len(synthetic_segments()[0]['text'])


def test_prompt_clips_oversized_segment_and_marks_truncated():
    segments = [{'segment_id': 'seg_1', 'text': '甲' * 5000}]
    messages, coverage = messages_for(segments, max_input_tokens=1200)
    payload = json.loads(messages[1]['content'])
    visible = payload['segments'][0]['text']
    assert len(visible) < 5000 and payload['segments'][0]['partial'] is True
    assert coverage['truncated'] is True and coverage['included']['seg_1'] == len(visible)


def test_quote_offsets_are_counted_in_code_points():
    segments = [{'segment_id': 'seg_1', 'text': '前缀🙂离线运行正常后缀'}]
    _, coverage = messages_for(segments)
    parsed = {'claims': [{
        'statement': '离线运行正常', 'kind': 'experience', 'aspect': 'compatibility',
        'polarity': 'affirm', 'importance': 'low', 'verifiability': 'verifiable',
        'evidence': [{'segment_id': 'seg_1', 'quote': '离线运行正常'}]}]}
    result = ai_extraction.validate_claim_output(parsed, segments, coverage)
    anchor = result['claims'][0]['anchors'][0]
    assert anchor['char_start'] == 3 and anchor['char_end'] == 9
    assert segments[0]['text'][anchor['char_start']:anchor['char_end']] == '离线运行正常'
    assert anchor['quote_hash'] == digest('离线运行正常')


def test_whitespace_wrapped_quote_is_mapped_back_to_original_text():
    segments = [{'segment_id': 'seg_1', 'text': '这里  离线\n也能运行。'}]
    _, coverage = messages_for(segments)
    parsed = {'claims': [{
        'statement': '离线也能运行', 'kind': 'experience',
        'evidence': [{'segment_id': 'seg_1', 'quote': '这里 离线 也能运行'}]}]}
    result = ai_extraction.validate_claim_output(parsed, segments, coverage)
    anchor = result['claims'][0]['anchors'][0]
    assert segments[0]['text'][anchor['char_start']:anchor['char_end']] == '这里  离线\n也能运行'
    assert anchor['quote'] == '这里  离线\n也能运行'


def test_unknown_segment_and_wrong_offset_are_rejected_not_corrected():
    _, coverage = messages_for()
    parsed = {'claims': [
        {'statement': '引用了不存在的片段', 'kind': 'factual_assertion',
         'evidence': [{'segment_id': 'seg_99', 'quote': '离线索引运行正常'}]},
        {'statement': '偏移被模型写错', 'kind': 'factual_assertion',
         'evidence': [{'segment_id': 'seg_1', 'quote': '离线索引运行正常',
                       'char_start': 0, 'char_end': 2}]},
    ]}
    result = ai_extraction.validate_claim_output(parsed, synthetic_segments(), coverage)
    assert result['claims'] == []
    assert {item['reason'] for item in result['rejected']} >= {'UNKNOWN_SEGMENT', 'OFFSET_MISMATCH'}
    assert result['counts']['unverified'] == 2


def test_quote_not_found_and_invalid_enums_are_rejected():
    _, coverage = messages_for()
    parsed = {'claims': [
        {'statement': '原文并不存在的引文', 'kind': 'factual_assertion',
         'evidence': [{'segment_id': 'seg_1', 'quote': '完全不存在的一句话'}]},
        {'statement': '极性值不合法', 'kind': 'opinion', 'polarity': 'maybe',
         'evidence': [{'segment_id': 'seg_1', 'quote': '离线索引运行正常'}]},
        {'statement': '类别值不合法', 'kind': 'gossip',
         'evidence': [{'segment_id': 'seg_1', 'quote': '离线索引运行正常'}]},
    ]}
    result = ai_extraction.validate_claim_output(parsed, synthetic_segments(), coverage)
    reasons = [item['reason'] for item in result['rejected']]
    assert 'QUOTE_NOT_FOUND' in reasons and 'INVALID_POLARITY' in reasons and 'INVALID_KIND' in reasons
    assert result['claims'] == []


def test_claim_limit_and_evidence_limit_are_enforced():
    _, coverage = messages_for()
    claims = []
    for index in range(ai_extraction.MAX_CLAIMS + 2):
        claims.append({'statement': f'主张 {index}', 'kind': 'opinion',
                       'evidence': [{'segment_id': 'seg_1', 'quote': '离线索引运行正常'}]})
    claims[0]['evidence'] = [{'segment_id': 'seg_1', 'quote': '离线索引运行正常'}
                             for _ in range(ai_extraction.MAX_EVIDENCE_PER_CLAIM + 2)]
    result = ai_extraction.validate_claim_output({'claims': claims}, synthetic_segments(), coverage)
    assert result['counts']['accepted'] == ai_extraction.MAX_CLAIMS
    assert any(item['reason'] == 'TOO_MANY_CLAIMS' for item in result['rejected'])
    assert any(item['reason'] == 'EVIDENCE_LIMIT' for item in result['rejected'])


def test_non_object_answer_is_a_hard_failure():
    _, coverage = messages_for()
    with pytest.raises(ai_extraction.ExtractionError) as error:
        ai_extraction.validate_claim_output(['not', 'a', 'dict'], synthetic_segments(), coverage)
    assert error.value.code == 'MODEL_OUTPUT_INVALID'
    with pytest.raises(ai_extraction.ExtractionError) as error:
        ai_extraction.validate_claim_output({'not_claims': []}, synthetic_segments(), coverage)
    assert error.value.code == 'MODEL_OUTPUT_INVALID'


def test_fingerprint_ignores_model_confidence_and_key_order():
    claim = {'statement': '陈述', 'claim_kind': 'opinion', 'aspect': 'general',
             'qualifiers': {'os': 'Windows'}, 'polarity': 'neutral'}
    reordered = {'polarity': 'neutral', 'qualifiers': {'os': 'Windows'},
                 'aspect': 'general', 'claim_kind': 'opinion', 'statement': '陈述'}
    assert ai_extraction.claim_fingerprint(claim) == ai_extraction.claim_fingerprint(reordered)


def test_tiny_route_limit_stops_before_building_an_over_budget_prompt():
    with pytest.raises(ai_extraction.ExtractionError) as error:
        ai_extraction.build_claim_messages(synthetic_segments(), max_input_tokens=100)
    assert error.value.code == 'ROUTE_INPUT_TOO_SMALL'


# ----------------------------------------------------------- integration tests

def create_provider(api, *, enabled=True):
    response = api.post('/api/v1/model-providers', json={
        'name': 'mimo-' + uuid.uuid4().hex[:6], 'protocol': 'openai_compatible',
        'base_url': 'https://llm.example/v1', 'api_key': 'sk-test-1234', 'enabled': enabled})
    assert response.status_code == 201, response.text
    return response.json()


def create_model(api, provider_id, *, enabled=True):
    response = api.post('/api/v1/models', json={
        'provider_id': provider_id, 'model_id': 'mimo-2.6', 'display_name': 'MIMO 2.6',
        'enabled': enabled, 'input_price_per_mtok': '1.0', 'output_price_per_mtok': '2.0',
        'context_window': 8192, 'max_output_tokens': 1024,
        'supports_structured_output': True})
    assert response.status_code == 201, response.text
    return response.json()


def set_budget(api, daily='10', monthly='50', policy='approved_cloud', if_match='1'):
    response = api.put('/api/v1/settings/budget', headers={'If-Match': if_match}, json={
        'daily_budget': daily, 'monthly_budget': monthly, 'data_policy': policy,
        'budget_currency': 'USD'})
    assert response.status_code == 200, response.text
    return response.json()


def set_route(api, model_id, *, data_policy='approved_cloud', mode='model', enabled=True,
              max_input_tokens=2000, max_output_tokens=400):
    response = api.put('/api/v1/task-routes', headers={'If-Match': '1'}, json={
        'routes': [{'task': 'claim_extraction', 'mode': mode, 'enabled': enabled,
                    'primary_model_id': model_id, 'fallback_model_ids': [],
                    'max_input_tokens': max_input_tokens, 'max_output_tokens': max_output_tokens,
                    'timeout_seconds': 5, 'max_attempts': 1, 'data_policy': data_policy}],
        'confirm_cloud': True})
    assert response.status_code == 200, response.text
    return response.json()


def import_text(api, content=CONTENT, title='模型抽取材料'):
    response = api.post('/api/v1/sources/import',
                        headers={'Idempotency-Key': 'm3-extract-' + uuid.uuid4().hex},
                        json={'title': title, 'content': content, 'rights_confirmed': True})
    assert response.status_code == 202, response.text
    return response.json()


def install_transport(handler):
    calls = []

    def wrapper(request):
        calls.append(request)
        return handler(request)

    ai_providers.set_transport_factory(lambda: httpx.MockTransport(wrapper))
    return calls


def answer(claims, *, prompt_tokens=300, completion_tokens=100):
    body = json.dumps({'schema_version': 'claim_extract.v1', 'claims': claims}, ensure_ascii=False)

    def handler(request):
        return httpx.Response(200, json={
            'id': 'chat-extract-1', 'model': 'mimo-2.6',
            'choices': [{'message': {'content': body}}],
            'usage': {'prompt_tokens': prompt_tokens, 'completion_tokens': completion_tokens}})
    return handler


ANCHORED_CLAIMS = [
    {'statement': 'Plugin X 2.0 在 Windows 上离线索引运行正常。', 'kind': 'experience',
     'aspect': 'compatibility', 'polarity': 'affirm', 'importance': 'medium',
     'verifiability': 'verifiable', 'qualifiers': {'product_version': '2.0', 'os': 'Windows'},
     'evidence': [{'segment_id': 'seg_1', 'quote': '离线索引运行正常'}]},
    {'statement': '作者认为界面配色过于刺眼。', 'kind': 'opinion', 'aspect': 'usability',
     'polarity': 'deny', 'importance': 'low', 'verifiability': 'not_verifiable',
     'evidence': [{'segment_id': 'seg_2', 'quote': '配色过于刺眼'}]},
]


def test_routed_extraction_writes_anchored_claims_and_ledger(api):
    provider = create_provider(api)
    model = create_model(api, provider['id'])
    set_budget(api)
    set_route(api, model['id'])
    calls = install_transport(answer(ANCHORED_CLAIMS))
    imported = import_text(api)

    assert worker.tick() is True

    job = api.get('/api/v1/jobs/' + imported['job_id']).json()
    assert job['state'] == 'succeeded', job
    assert job['result']['extraction'] == 'model'
    assert job['result']['claims'] == 2 and job['result']['validated'] == 2
    assert job['result']['published'] == 2 and job['result']['anchors'] == 2
    assert job['result']['fact_confirmation'] is False
    assert job['result']['invocation_id']

    # One bounded request, authenticated, carrying segment IDs and not a token leak.
    assert len(calls) == 1
    request = calls[0]
    assert request.url.path.endswith('/chat/completions')
    assert request.headers['authorization'] == 'Bearer sk-test-1234'
    body = json.loads(request.content)
    assert body['model'] == 'mimo-2.6'
    prompt = json.loads(body['messages'][1]['content'])
    assert [item['segment_id'] for item in prompt['segments']] == ['seg_1', 'seg_2']
    assert 'sk-test-1234' not in request.content.decode('utf8')

    with api.engine.connect() as conn:
        claims = many(conn, 'SELECT * FROM claims ORDER BY statement')
        assert len(claims) == 2 and all(c['review_status'] == 'validated' for c in claims)
        evidence_rows = many(conn, """SELECT r.*,v.segments FROM relations r
            JOIN source_versions v ON v.id=r.to_id
            WHERE r.relation_type='evidenced_by' AND r.status='accepted'""")
        assert len(evidence_rows) == 2
        original = {segment['segment_id']: segment['text'] for segment in evidence_rows[0]['segments']}
        for row in evidence_rows:
            anchor = row['payload']
            assert original[anchor['segment_id']][anchor['char_start']:anchor['char_end']] == anchor['quote']
            assert digest(anchor['quote']) == anchor['quote_hash']
        invocation = one(conn, 'SELECT * FROM ai_invocations')
        assert invocation['task'] == 'claim_extraction'
        assert invocation['status'] == 'succeeded'
        assert str(invocation['job_id']) == imported['job_id']
        assert invocation['input_tokens'] == 300 and invocation['output_tokens'] == 100
        assert str(invocation['cost_settled']) == '0.00050000'
        assert invocation['cost_status'] == 'estimated'
        assert str(invocation['model_profile_id']) == model['id']
        ledger = one(conn, 'SELECT * FROM ai_budget_ledger WHERE invocation_id=:id', id=invocation['id'])
        assert ledger['status'] == 'settled'
        assert str(ledger['reserved']) == '0.00280000'
        assert str(ledger['settled']) == '0.00050000'

    feed = api.get('/api/v1/updates').json()['items']
    assert len(feed) == 2
    assert all(item['provenance']['provider'] == 'model' for item in feed)
    assert all(item['provenance'].get('demo') is None for item in feed)
    first = feed[0]
    assert first['evidence'][0]['payload']['quote']
    assert api.get('/api/v1/evidence/' + first['evidence'][0]['id']).status_code == 200
    assert api.get('/api/v1/claims/' + first['structured_body'][0]['claim_id']).status_code == 200


def test_demo_fixture_never_reaches_the_configured_model(api):
    provider = create_provider(api)
    model = create_model(api, provider['id'])
    set_budget(api)
    set_route(api, model['id'])
    calls = install_transport(answer(ANCHORED_CLAIMS))
    assert api.post('/api/v1/demo').status_code == 202
    while worker.tick():
        pass
    assert calls == []
    with api.engine.connect() as conn:
        assert one(conn, "SELECT count(*) n FROM ai_invocations")['n'] == 0
        assert one(conn, "SELECT count(*) n FROM claims WHERE provenance->>'provider'='mock'")['n'] == 6


def test_no_route_keeps_real_material_at_needs_review(api):
    calls = install_transport(answer(ANCHORED_CLAIMS))
    imported = import_text(api)
    assert worker.tick() is True
    job = api.get('/api/v1/jobs/' + imported['job_id']).json()
    assert job['state'] == 'needs_review'
    with api.engine.connect() as conn:
        assert one(conn, 'SELECT count(*) n FROM claims')['n'] == 0
        assert one(conn, 'SELECT count(*) n FROM ai_invocations')['n'] == 0
    assert calls == []


def test_model_route_disabled_still_uses_local_path(api):
    provider = create_provider(api)
    model = create_model(api, provider['id'])
    set_route(api, model['id'], enabled=False)
    calls = install_transport(answer(ANCHORED_CLAIMS))
    imported = import_text(api)
    assert worker.tick() is True
    job = api.get('/api/v1/jobs/' + imported['job_id']).json()
    assert job['state'] == 'needs_review'
    assert calls == []


def test_unverifiable_model_claims_are_candidates_never_published(api):
    provider = create_provider(api)
    model = create_model(api, provider['id'])
    set_budget(api)
    set_route(api, model['id'])
    bad = [{'statement': '模型编造的主张', 'kind': 'factual_assertion', 'importance': 'high',
            'evidence': [{'segment_id': 'seg_9', 'quote': '从未出现过的原文'}]},
           {'statement': '偏移写错的主张', 'kind': 'opinion',
            'evidence': [{'segment_id': 'seg_1', 'quote': '离线索引运行正常',
                          'char_start': 0, 'char_end': 1}]}]
    install_transport(answer(bad))
    imported = import_text(api)
    assert worker.tick() is True
    job = api.get('/api/v1/jobs/' + imported['job_id']).json()
    assert job['state'] == 'needs_review' and job['error_code'] == 'MODEL_OUTPUT_UNVERIFIED'
    assert job['result']['validated'] == 0 and job['result']['candidates'] == 2
    with api.engine.connect() as conn:
        claims = many(conn, 'SELECT * FROM claims')
        assert len(claims) == 2 and all(c['review_status'] == 'candidate' for c in claims)
        assert one(conn, "SELECT count(*) n FROM relations WHERE relation_type='evidenced_by'")['n'] == 0
        assert one(conn, 'SELECT count(*) n FROM updates')['n'] == 0
        assert one(conn, 'SELECT count(*) n FROM verification_records')['n'] == 0
    assert api.get('/api/v1/updates').json()['items'] == []
    assert api.get('/api/v1/verification-queue').json()['items']


def test_partial_validation_publishes_only_anchored_claims(api):
    provider = create_provider(api)
    model = create_model(api, provider['id'])
    set_budget(api)
    set_route(api, model['id'])
    mixed = [ANCHORED_CLAIMS[0],
             {'statement': '无法定位的主张', 'kind': 'prediction',
              'evidence': [{'segment_id': 'seg_2', 'quote': '这句话不在原文里'}]}]
    install_transport(answer(mixed))
    imported = import_text(api)
    assert worker.tick() is True
    job = api.get('/api/v1/jobs/' + imported['job_id']).json()
    assert job['state'] == 'succeeded'
    assert job['result']['validated'] == 1 and job['result']['candidates'] == 1
    with api.engine.connect() as conn:
        assert one(conn, "SELECT count(*) n FROM claims WHERE review_status='validated'")['n'] == 1
        assert one(conn, "SELECT count(*) n FROM claims WHERE review_status='candidate'")['n'] == 1
        assert one(conn, 'SELECT count(*) n FROM updates')['n'] == 1


def test_zero_budget_blocks_before_the_request_then_retry_succeeds(api):
    provider = create_provider(api)
    model = create_model(api, provider['id'])
    # Cloud sending is allowed, but the budget is still zero: the call must be refused
    # before any network request and recorded as blocked.
    set_budget(api, daily='0', monthly='0')
    set_route(api, model['id'])
    calls = install_transport(answer(ANCHORED_CLAIMS))
    imported = import_text(api)
    assert worker.tick() is True
    job = api.get('/api/v1/jobs/' + imported['job_id']).json()
    assert job['state'] == 'blocked_budget' and job['error_code'] == 'BUDGET_EXHAUSTED', job
    assert calls == []
    with api.engine.connect() as conn:
        assert one(conn, 'SELECT count(*) n FROM claims')['n'] == 0
        blocked = one(conn, "SELECT * FROM ai_invocations WHERE status='blocked'")
        assert blocked and blocked['error_code'] == 'BUDGET_EXHAUSTED'
        assert one(conn, 'SELECT count(*) n FROM ai_budget_ledger')['n'] == 0

    set_budget(api, if_match='2')
    retried = api.post('/api/v1/jobs/' + imported['job_id'] + '/retry')
    assert retried.status_code == 202 and retried.json()['state'] == 'queued', retried.text
    assert worker.tick() is True
    job = api.get('/api/v1/jobs/' + imported['job_id']).json()
    assert job['state'] == 'succeeded' and job['result']['published'] == 2
    assert len(calls) == 1


def test_local_only_policy_refuses_cloud_model_without_sending(api):
    provider = create_provider(api)
    model = create_model(api, provider['id'])
    set_route(api, model['id'], data_policy='local_only')
    calls = install_transport(answer(ANCHORED_CLAIMS))
    imported = import_text(api)
    assert worker.tick() is True
    job = api.get('/api/v1/jobs/' + imported['job_id']).json()
    assert job['state'] == 'failed' and job['error_code'] == 'DATA_POLICY_BLOCKED'
    assert calls == []
    with api.engine.connect() as conn:
        assert one(conn, 'SELECT count(*) n FROM claims')['n'] == 0


def test_invalid_json_answer_writes_nothing(api):
    provider = create_provider(api)
    model = create_model(api, provider['id'])
    set_budget(api)
    set_route(api, model['id'])

    def handler(request):
        return httpx.Response(200, json={
            'id': 'chat-bad', 'choices': [{'message': {'content': '这不是 JSON'}}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 5}})

    install_transport(handler)
    imported = import_text(api)
    assert worker.tick() is True
    job = api.get('/api/v1/jobs/' + imported['job_id']).json()
    assert job['state'] == 'needs_review' and job['error_code'] == 'MODEL_OUTPUT_INVALID'
    with api.engine.connect() as conn:
        assert one(conn, 'SELECT count(*) n FROM claims')['n'] == 0
        assert one(conn, 'SELECT count(*) n FROM updates')['n'] == 0
        invocation = one(conn, 'SELECT * FROM ai_invocations')
        assert invocation['status'] == 'succeeded'
        assert invocation['cost_status'] == 'estimated'


def test_empty_claim_list_is_a_valid_but_unpublished_outcome(api):
    provider = create_provider(api)
    model = create_model(api, provider['id'])
    set_budget(api)
    set_route(api, model['id'])
    install_transport(answer([]))
    imported = import_text(api)
    assert worker.tick() is True
    job = api.get('/api/v1/jobs/' + imported['job_id']).json()
    assert job['state'] == 'needs_review' and job['result']['claims'] == 0
    with api.engine.connect() as conn:
        assert one(conn, 'SELECT count(*) n FROM claims')['n'] == 0


def test_fence_loss_before_write_leaves_no_claims(api):
    provider = create_provider(api)
    model = create_model(api, provider['id'])
    set_budget(api)
    set_route(api, model['id'])
    install_transport(answer(ANCHORED_CLAIMS))
    imported = import_text(api)

    original = worker._store_model_claims

    def steal_then_write(job, version, result, validated, **kwargs):
        with api.engine.begin() as conn:
            conn.execute(text('UPDATE jobs SET fence=fence+1 WHERE id=:id'), {'id': job['id']})
        return original(job, version, result, validated, **kwargs)

    worker._store_model_claims = steal_then_write
    try:
        assert worker.tick() is True
    finally:
        worker._store_model_claims = original
    with api.engine.connect() as conn:
        assert one(conn, 'SELECT count(*) n FROM claims')['n'] == 0
        assert one(conn, 'SELECT count(*) n FROM updates')['n'] == 0
        assert one(conn, 'SELECT state FROM jobs WHERE id=:id', id=imported['job_id'])['state'] == 'running'
