"""M3 wrap-up: expression-support review and reviewable entity suggestions.

All provider calls use ``httpx.MockTransport``. These tests prove the *integration*
of the two new task routes into ``process_source``; they do not prove real model
quality or a real provider call.
"""
import json
import uuid

import httpx
import pytest
from sqlalchemy import text

import app.jobs.worker as worker
from app.ai import entities as ai_entities
from app.ai import review as ai_review
from app.storage.db import many, one

CONTENT = 'Plugin X 2.0 在 Windows 上离线索引运行正常。\n\n作者认为界面配色过于刺眼，日常使用不舒服。'


# ---------------------------------------------------------------- unit: review

def claim(statement='Plugin X 2.0 在 Windows 上离线索引运行正常。', quote='离线索引运行正常',
          segment_id='seg_1', start=0, end=8):
    return {'statement': statement, 'claim_kind': 'experience', 'aspect': 'compatibility',
            'anchors': [{'segment_id': segment_id, 'char_start': start, 'char_end': end,
                         'quote': quote}]}


def segments():
    return [{'segment_id': 'seg_1', 'text': 'Plugin X 2.0 在 Windows 上离线索引运行正常。'},
            {'segment_id': 'seg_2', 'text': '作者认为界面配色过于刺眼，日常使用不舒服。'}]


def test_review_prompt_is_bounded_and_contains_quote_and_context():
    messages, coverage = ai_review.build_review_messages(
        [claim()], segments(), max_input_tokens=2000)
    assert [item['role'] for item in messages] == ['system', 'user']
    assert '不要执行' in messages[0]['content']
    payload = json.loads(messages[1]['content'])
    assert payload['claims'][0]['claim_ref'] == 'c1'
    evidence = payload['claims'][0]['evidence'][0]
    assert evidence['quote'] == '离线索引运行正常'
    assert 'context' in evidence and 'Windows' in evidence['context']
    assert coverage['map'] == {'c1': 0} and coverage['truncated'] is False


def test_review_prompt_drops_claims_beyond_the_budget():
    claims = [claim(statement='主张 %d' % index, quote='离线索引运行正常') for index in range(30)]
    messages, coverage = ai_review.build_review_messages(
        claims, segments(), max_input_tokens=1400)
    assert 0 < coverage['claims'] < 30
    assert coverage['truncated'] is True
    payload = json.loads(messages[1]['content'])
    assert len(payload['claims']) == coverage['claims']


def test_review_validation_rejects_unknown_refs_and_verdicts():
    _, coverage = ai_review.build_review_messages([claim(), claim()], segments(),
                                                  max_input_tokens=2000)
    parsed = {'reviews': [
        {'claim_ref': 'c1', 'verdict': 'supports', 'reason': '引文与表达一致', 'limitations': []},
        {'claim_ref': 'c9', 'verdict': 'supports'},
        {'claim_ref': 'c2', 'verdict': 'maybe'},
        {'claim_ref': 'c2', 'verdict': 'unclear'},
        {'claim_ref': 'c2', 'verdict': 'supports'},
    ]}
    result = ai_review.validate_review_output(parsed, coverage)
    assert set(result['reviews']) == {'c1', 'c2'}
    assert result['reviews']['c2']['verdict'] == 'unclear'
    reasons = {item['reason'] for item in result['rejected']}
    assert {'UNKNOWN_CLAIM', 'INVALID_VERDICT', 'DUPLICATE_REVIEW'} <= reasons
    assert result['counts']['missing'] == 0


def test_review_missing_entries_are_reported_not_invented():
    _, coverage = ai_review.build_review_messages([claim(), claim()], segments(),
                                                  max_input_tokens=2000)
    result = ai_review.validate_review_output(
        {'reviews': [{'claim_ref': 'c1', 'verdict': 'partially_supports',
                      'limitations': ['仅限 Windows']}]}, coverage)
    assert result['missing'] == ['c2']
    assert result['reviews']['c1']['verdict'] == 'partially_supports'
    assert result['reviews']['c1']['limitations'] == ['仅限 Windows']


def test_review_rejects_non_object_and_missing_reviews():
    _, coverage = ai_review.build_review_messages([claim()], segments(), max_input_tokens=2000)
    with pytest.raises(ai_review.ReviewError) as error:
        ai_review.validate_review_output(['not', 'a', 'dict'], coverage)
    assert error.value.code == 'MODEL_OUTPUT_INVALID'
    with pytest.raises(ai_review.ReviewError):
        ai_review.validate_review_output({'reviews': 'no'}, coverage)


def test_review_route_limit_too_small_fails_before_any_call():
    with pytest.raises(ai_review.ReviewError) as error:
        ai_review.build_review_messages([claim()], segments(), max_input_tokens=100)
    assert error.value.code == 'ROUTE_INPUT_TOO_SMALL'


# -------------------------------------------------------------- unit: entities

NODE_ID = '11111111-1111-1111-1111-111111111111'


def nodes():
    return [{'id': NODE_ID, 'canonical_name': 'Plugin X', 'entity_type': 'plugin',
             'aliases': ['PX', '插件 X']}]


def test_entity_prompt_only_offers_existing_node_ids():
    messages, coverage = ai_entities.build_entity_messages([claim()], nodes(),
                                                           max_input_tokens=2000)
    payload = json.loads(messages[1]['content'])
    assert payload['entities'][0]['node_id'] == NODE_ID
    assert payload['claims'][0]['claim_ref'] == 'c1'
    assert coverage['node_ids'] == [NODE_ID]
    assert '不得改写或合并已有实体' in messages[0]['content']


def test_entity_prompt_allows_an_empty_entity_library_for_new_entities():
    messages, coverage = ai_entities.build_entity_messages([claim()], [], max_input_tokens=2000)
    payload = json.loads(messages[1]['content'])
    assert payload['entities'] == []
    assert coverage['node_ids'] == []
    assert 'new_entities' in messages[0]['content']


def test_entity_validation_rejects_invented_nodes_and_dedupes():
    _, coverage = ai_entities.build_entity_messages([claim()], nodes(), max_input_tokens=2000)
    parsed = {'links': [
        {'claim_ref': 'c1', 'node_id': NODE_ID, 'role': 'primary'},
        {'claim_ref': 'c1', 'node_id': NODE_ID, 'role': 'secondary'},
        {'claim_ref': 'c1', 'node_id': '22222222-2222-2222-2222-222222222222'},
        {'claim_ref': 'c9', 'node_id': NODE_ID},
        {'claim_ref': 'c1', 'node_id': NODE_ID, 'role': 'maybe'},
    ]}
    result = ai_entities.validate_entity_output(parsed, coverage)
    assert result['links'] == [{'claim_ref': 'c1', 'node_id': NODE_ID, 'role': 'primary'}]
    reasons = {item['reason'] for item in result['rejected']}
    assert {'DUPLICATE_LINK', 'UNKNOWN_NODE', 'UNKNOWN_CLAIM', 'INVALID_ROLE'} <= reasons


def test_entity_validation_enforces_per_claim_limit(monkeypatch):
    other = '22222222-2222-2222-2222-222222222222'
    candidates = nodes() + [{'id': other, 'canonical_name': 'Plugin Y',
                             'entity_type': 'plugin', 'aliases': []}]
    _, coverage = ai_entities.build_entity_messages([claim()], candidates,
                                                    max_input_tokens=2000)
    monkeypatch.setattr(ai_entities, 'MAX_LINKS_PER_CLAIM', 1)
    parsed = {'links': [{'claim_ref': 'c1', 'node_id': NODE_ID, 'role': 'primary'},
                        {'claim_ref': 'c1', 'node_id': other, 'role': 'secondary'}]}
    result = ai_entities.validate_entity_output(parsed, coverage)
    assert len(result['links']) == 1
    assert any(item['reason'] == 'LINK_LIMIT' for item in result['rejected'])


def test_entity_new_entities_are_validated_and_deduplicated():
    _, coverage = ai_entities.build_entity_messages([claim()], [], max_input_tokens=2000)
    parsed = {'new_entities': [
        {'name': 'Plugin X', 'entity_type': 'plugin', 'aliases': ['PX'],
         'description': '虚构插件', 'claim_refs': ['c1']},
        {'name': 'Plugin X', 'entity_type': 'plugin', 'aliases': [], 'claim_refs': ['c1']},
        {'name': '', 'entity_type': 'plugin', 'claim_refs': ['c1']},
        {'name': '孤立体', 'entity_type': 'plugin', 'claim_refs': ['c9']},
    ]}
    result = ai_entities.validate_entity_output(parsed, coverage)
    assert result['new_entities'] == [
        {'name': 'Plugin X', 'entity_type': 'plugin', 'aliases': ['PX'],
         'description': '虚构插件', 'claim_refs': ['c1']}]
    reasons = {item['reason'] for item in result['rejected']}
    assert {'DUPLICATE_NEW_ENTITY', 'INVALID_NEW_ENTITY_NAME', 'NEW_ENTITY_NO_CLAIMS'} <= reasons


# --------------------------------------------------------------- integration

ANCHORED_CLAIMS = [
    {'statement': 'Plugin X 2.0 在 Windows 上离线索引运行正常。', 'kind': 'experience',
     'aspect': 'compatibility', 'polarity': 'affirm', 'importance': 'medium',
     'verifiability': 'verifiable', 'qualifiers': {'product_version': '2.0', 'os': 'Windows'},
     'evidence': [{'segment_id': 'seg_1', 'quote': '离线索引运行正常'}]},
    {'statement': '作者认为界面配色过于刺眼。', 'kind': 'opinion', 'aspect': 'usability',
     'polarity': 'deny', 'importance': 'low', 'verifiability': 'not_verifiable',
     'evidence': [{'segment_id': 'seg_2', 'quote': '配色过于刺眼'}]},
]


def create_provider(api):
    response = api.post('/api/v1/model-providers', json={
        'name': 'mimo-' + uuid.uuid4().hex[:6], 'protocol': 'openai_compatible',
        'base_url': 'https://llm.example/v1', 'api_key': 'sk-test-1234', 'enabled': True})
    assert response.status_code == 201, response.text
    return response.json()


def create_model(api, provider_id):
    response = api.post('/api/v1/models', json={
        'provider_id': provider_id, 'model_id': 'mimo-2.6', 'display_name': 'MIMO 2.6',
        'enabled': True, 'input_price_per_mtok': '1.0', 'output_price_per_mtok': '2.0',
        'context_window': 8192, 'max_output_tokens': 1024,
        'supports_structured_output': True})
    assert response.status_code == 201, response.text
    return response.json()


def set_budget(api, daily='10', monthly='50', if_match='1'):
    response = api.put('/api/v1/settings/budget', headers={'If-Match': if_match}, json={
        'daily_budget': daily, 'monthly_budget': monthly, 'data_policy': 'approved_cloud',
        'budget_currency': 'USD'})
    assert response.status_code == 200, response.text
    return response.json()


def save_routes(api, model_id, tasks, *, if_match='1', max_input_tokens=2000):
    routes = [{'task': task, 'mode': 'model', 'enabled': True, 'primary_model_id': model_id,
               'fallback_model_ids': [], 'max_input_tokens': max_input_tokens,
               'max_output_tokens': 400, 'timeout_seconds': 5, 'max_attempts': 1,
               'data_policy': 'approved_cloud'} for task in tasks]
    response = api.put('/api/v1/task-routes', headers={'If-Match': if_match},
                       json={'routes': routes, 'confirm_cloud': True})
    assert response.status_code == 200, response.text
    return response.json()


def create_node(api, name='Plugin X'):
    response = api.post('/api/v1/nodes', json={'kind': 'entity', 'name': name,
                                               'entity_type': 'plugin'})
    assert response.status_code == 201, response.text
    return response.json()


def import_text(api, content=CONTENT):
    response = api.post('/api/v1/sources/import',
                        headers={'Idempotency-Key': 'm3-wrap-' + uuid.uuid4().hex},
                        json={'title': 'M3 收尾材料', 'content': content,
                              'rights_confirmed': True})
    assert response.status_code == 202, response.text
    return response.json()


def install_transport(handler):
    calls = []

    def wrapper(request):
        calls.append(request)
        return handler(request)

    from app.ai import providers as ai_providers
    ai_providers.set_transport_factory(lambda: httpx.MockTransport(wrapper))
    return calls


def routed_handler(*, claims=None, reviews=None, links=None, review_status=200,
                   entity_status=200):
    def handler(request):
        body = json.loads(request.content)
        system = body['messages'][0]['content']
        if '主张抽取器' in system:
            content = json.dumps({'schema_version': 'claim_extract.v1',
                                  'claims': claims or []}, ensure_ascii=False)
        elif '表达复核器' in system:
            if review_status != 200:
                return httpx.Response(review_status, json={'error': {'message': 'upstream down'}})
            content = json.dumps({'schema_version': 'expression_review.v1',
                                  'reviews': reviews or []}, ensure_ascii=False)
        elif '实体消歧' in system:
            if entity_status != 200:
                return httpx.Response(entity_status, json={'error': {'message': 'upstream down'}})
            content = json.dumps({'schema_version': 'entity_link.v1',
                                  'links': links or []}, ensure_ascii=False)
        else:  # pragma: no cover - guard against a new unhandled task
            raise AssertionError('unexpected task prompt: ' + system[:80])
        return httpx.Response(200, json={
            'id': 'chat-wrap-1', 'model': 'mimo-2.6',
            'choices': [{'message': {'content': content}}],
            'usage': {'prompt_tokens': 300, 'completion_tokens': 100}})
    return handler


def test_review_supports_publishes_and_records_verdict(api):
    provider = create_provider(api)
    model = create_model(api, provider['id'])
    set_budget(api)
    save_routes(api, model['id'], ['claim_extraction', 'evidence_review'])
    reviews = [{'claim_ref': 'c1', 'verdict': 'supports', 'reason': '引文直接支持',
                'limitations': []},
               {'claim_ref': 'c2', 'verdict': 'supports', 'reason': '引文直接支持',
                'limitations': []}]
    calls = install_transport(routed_handler(claims=ANCHORED_CLAIMS, reviews=reviews))
    imported = import_text(api)
    assert worker.tick() is True

    job = api.get('/api/v1/jobs/' + imported['job_id']).json()
    assert job['state'] == 'succeeded', job
    assert job['result']['published'] == 2
    assert job['result']['semantic_review'] == 'performed'
    assert job['result']['expression_review'] == 'performed'
    assert job['result']['expression_review_detail']['supports'] == 2
    assert job['result']['fact_confirmation'] is False
    assert len(calls) == 2

    with api.engine.connect() as conn:
        claims = many(conn, 'SELECT * FROM claims ORDER BY statement')
        assert len(claims) == 2 and all(item['review_status'] == 'validated' for item in claims)
        assert all(item['fact_check_status'] == 'insufficient' for item in claims)
        reviews_rows = many(conn, 'SELECT * FROM claim_expression_reviews ORDER BY verdict')
        assert len(reviews_rows) == 2
        assert all(item['verdict'] == 'supports' for item in reviews_rows)
        assert all(item['policy_version'] for item in reviews_rows)
        invocations = many(conn, 'SELECT * FROM ai_invocations ORDER BY task')
        assert [item['task'] for item in invocations] == ['claim_extraction', 'evidence_review']
        assert all(item['status'] == 'succeeded' for item in invocations)
        assert one(conn, "SELECT count(*) n FROM updates")['n'] == 2

    feed = api.get('/api/v1/updates').json()['items']
    assert len(feed) == 2
    assert all(item['structured_body'][0]['expression_review'] == 'supports' for item in feed)
    claim_id = feed[0]['structured_body'][0]['claim_id']
    detail = api.get('/api/v1/claims/' + claim_id).json()
    assert detail['expression_review']['verdict'] == 'supports'
    assert len(detail['expression_reviews']) == 1


def test_review_unsupported_holds_the_claim_as_candidate(api):
    provider = create_provider(api)
    model = create_model(api, provider['id'])
    set_budget(api)
    save_routes(api, model['id'], ['claim_extraction', 'evidence_review'])
    install_transport(routed_handler(
        claims=[ANCHORED_CLAIMS[0]],
        reviews=[{'claim_ref': 'c1', 'verdict': 'unsupported',
                  'reason': '引文只描述版本号，不说明离线索引', 'limitations': ['缺少功能描述']}]))
    imported = import_text(api)
    assert worker.tick() is True

    job = api.get('/api/v1/jobs/' + imported['job_id']).json()
    assert job['state'] == 'needs_review'
    assert job['error_code'] == 'EXPRESSION_REVIEW_HOLD'
    assert job['result']['published'] == 0
    assert job['result']['held_expression'] == 1
    assert job['result']['candidates'] == 1
    with api.engine.connect() as conn:
        claim_row = one(conn, 'SELECT * FROM claims')
        assert claim_row['review_status'] == 'candidate'
        assert claim_row['fact_check_status'] == 'unchecked'
        assert one(conn, 'SELECT count(*) n FROM updates')['n'] == 0
        assert one(conn, 'SELECT count(*) n FROM verification_records')['n'] == 0
        review_row = one(conn, 'SELECT * FROM claim_expression_reviews')
        assert review_row['verdict'] == 'unsupported'
        assert one(conn, "SELECT count(*) n FROM relations WHERE relation_type='evidenced_by'")['n'] == 1
    assert api.get('/api/v1/updates').json()['items'] == []
    queue = api.get('/api/v1/verification-queue').json()['items']
    assert queue and queue[0]['expression_verdict'] == 'unsupported'


def test_review_upstream_failure_holds_claims_without_publishing(api):
    provider = create_provider(api)
    model = create_model(api, provider['id'])
    set_budget(api)
    save_routes(api, model['id'], ['claim_extraction', 'evidence_review'])
    calls = install_transport(routed_handler(claims=[ANCHORED_CLAIMS[0]], review_status=503))
    imported = import_text(api)
    assert worker.tick() is True

    job = api.get('/api/v1/jobs/' + imported['job_id']).json()
    assert job['state'] == 'needs_review'
    assert job['error_code'] == 'EXPRESSION_REVIEW_UNAVAILABLE'
    assert job['result']['published'] == 0
    assert job['result']['candidates'] == 1
    assert len(calls) == 2
    with api.engine.connect() as conn:
        assert one(conn, 'SELECT count(*) n FROM updates')['n'] == 0
        statuses = {item['task']: item['status'] for item in
                    many(conn, 'SELECT * FROM ai_invocations')}
        assert statuses == {'claim_extraction': 'succeeded', 'evidence_review': 'failed'}


def test_review_blocked_by_budget_does_not_publish(api):
    provider = create_provider(api)
    model = create_model(api, provider['id'])
    # Worst-case extraction reservation is (2000*1 + 400*2)/1e6 = 0.0028.
    # It settles at 0.0005, leaving 0.0023, which is one token-short of the same
    # review reservation. The review must be refused before any HTTP request.
    set_budget(api, daily='0.0028', monthly='50')
    save_routes(api, model['id'], ['claim_extraction', 'evidence_review'])
    calls = install_transport(routed_handler(
        claims=[ANCHORED_CLAIMS[0]],
        reviews=[{'claim_ref': 'c1', 'verdict': 'supports'}]))
    imported = import_text(api)
    assert worker.tick() is True

    job = api.get('/api/v1/jobs/' + imported['job_id']).json()
    assert job['state'] == 'needs_review'
    assert job['error_code'] == 'EXPRESSION_REVIEW_UNAVAILABLE'
    assert job['result']['expression_review_detail']['error'] == 'BUDGET_EXHAUSTED'
    assert job['result']['published'] == 0
    assert len(calls) == 1
    with api.engine.connect() as conn:
        blocked = one(conn, "SELECT * FROM ai_invocations WHERE status='blocked'")
        assert blocked['task'] == 'evidence_review'
        assert blocked['error_code'] == 'BUDGET_EXHAUSTED'
        assert one(conn, 'SELECT count(*) n FROM updates')['n'] == 0


def test_review_partial_support_publishes_with_limitations(api):
    provider = create_provider(api)
    model = create_model(api, provider['id'])
    set_budget(api)
    save_routes(api, model['id'], ['claim_extraction', 'evidence_review'])
    install_transport(routed_handler(
        claims=ANCHORED_CLAIMS,
        reviews=[{'claim_ref': 'c1', 'verdict': 'partially_supports',
                  'reason': '仅说明该版本在 Windows 上可用', 'limitations': ['不能推广到其他系统']},
                 {'claim_ref': 'c2', 'verdict': 'supports', 'reason': '直接表达', 'limitations': []}]))
    imported = import_text(api)
    assert worker.tick() is True
    job = api.get('/api/v1/jobs/' + imported['job_id']).json()
    assert job['state'] == 'succeeded' and job['result']['published'] == 2
    feed = api.get('/api/v1/updates').json()['items']
    partial = [item for item in feed if item['title'].startswith('来源观点') and
               'Plugin X' in item['title']]
    assert partial
    body = partial[0]['structured_body'][0]
    assert body['expression_review'] == 'partially_supports'
    assert body['expression_limitations'] == ['不能推广到其他系统']


def test_review_missing_all_verdicts_holds_anchored_claims(api):
    provider = create_provider(api)
    model = create_model(api, provider['id'])
    set_budget(api)
    save_routes(api, model['id'], ['claim_extraction', 'evidence_review'])
    install_transport(routed_handler(claims=ANCHORED_CLAIMS, reviews=[]))
    imported = import_text(api)
    assert worker.tick() is True
    job = api.get('/api/v1/jobs/' + imported['job_id']).json()
    # An empty reviews array is structurally valid but every claim is missing a
    # verdict, so nothing may be published.
    assert job['state'] == 'needs_review'
    assert job['error_code'] == 'EXPRESSION_REVIEW_HOLD'
    assert job['result']['published'] == 0
    with api.engine.connect() as conn:
        assert one(conn, "SELECT count(*) n FROM claims WHERE review_status='candidate'")['n'] == 2
        assert one(conn, 'SELECT count(*) n FROM claim_expression_reviews')['n'] == 0


def test_entity_link_is_proposed_and_hidden_until_accepted(api):
    provider = create_provider(api)
    model = create_model(api, provider['id'])
    node = create_node(api)
    set_budget(api)
    save_routes(api, model['id'], ['claim_extraction', 'entity_disambiguation'])
    install_transport(routed_handler(
        claims=[ANCHORED_CLAIMS[0]],
        links=[{'claim_ref': 'c1', 'node_id': node['id'], 'role': 'primary'}]))
    imported = import_text(api)
    assert worker.tick() is True

    job = api.get('/api/v1/jobs/' + imported['job_id']).json()
    assert job['state'] == 'succeeded', job
    assert job['result']['published'] == 1
    assert job['result']['entity_disambiguation'] == 'performed'
    assert job['result']['entity_links_proposed'] == 1
    with api.engine.connect() as conn:
        link = one(conn, "SELECT * FROM relations WHERE relation_type='about' AND asserted_by='model'")
        assert link['status'] == 'proposed'
        assert link['payload']['role'] == 'primary'
        claim_row = one(conn, 'SELECT * FROM claims')
        assert claim_row['fact_check_status'] == 'insufficient'

    # Proposed links do not put the claim under the entity, and the entity-filtered
    # feed is empty because no update->entity relation exists yet.
    detail = api.get('/api/v1/nodes/' + node['id']).json()
    assert detail['claims'] == []
    assert api.get('/api/v1/updates?entity_id=' + node['id']).json()['items'] == []
    claim_detail = api.get('/api/v1/claims/' + str(claim_row['id'])).json()
    assert len(claim_detail['about_nodes']) == 1
    assert claim_detail['about_nodes'][0]['status'] == 'proposed'
    assert claim_detail['about_nodes'][0]['canonical_name'] == 'Plugin X'

    accepted = api.post('/api/v1/relations/' + str(link['id']) + '/review',
                        json={'action': 'accept', 'reason': '同一条目'})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()['status'] == 'accepted'
    assert accepted.json()['updates_linked'] == 1

    detail = api.get('/api/v1/nodes/' + node['id']).json()
    assert len(detail['claims']) == 1
    feed = api.get('/api/v1/updates?entity_id=' + node['id']).json()['items']
    assert len(feed) == 1
    claim_detail = api.get('/api/v1/claims/' + str(detail['claims'][0]['id'])).json()
    assert claim_detail['about_nodes'][0]['status'] == 'accepted'
    # Entity assignment is not fact confirmation.
    assert claim_detail['fact_check_status'] == 'insufficient'


def test_entity_reject_keeps_the_link_out_of_views(api):
    provider = create_provider(api)
    model = create_model(api, provider['id'])
    node = create_node(api)
    set_budget(api)
    save_routes(api, model['id'], ['claim_extraction', 'entity_disambiguation'])
    install_transport(routed_handler(
        claims=[ANCHORED_CLAIMS[0]],
        links=[{'claim_ref': 'c1', 'node_id': node['id'], 'role': 'primary'}]))
    imported = import_text(api)
    assert worker.tick() is True
    with api.engine.connect() as conn:
        link = one(conn, "SELECT * FROM relations WHERE relation_type='about' AND asserted_by='model'")
    rejected = api.post('/api/v1/relations/' + str(link['id']) + '/review',
                        json={'action': 'reject', 'reason': '讨论的是另一个产品'})
    assert rejected.status_code == 200 and rejected.json()['status'] == 'rejected'
    assert rejected.json()['updates_linked'] == 0
    assert api.get('/api/v1/nodes/' + node['id']).json()['claims'] == []
    assert api.get('/api/v1/updates?entity_id=' + node['id']).json()['items'] == []
    again = api.post('/api/v1/relations/' + str(link['id']) + '/review',
                     json={'action': 'accept'})
    assert again.status_code == 409


def test_entity_invented_node_is_rejected_but_claim_still_publishes(api):
    provider = create_provider(api)
    model = create_model(api, provider['id'])
    create_node(api)
    set_budget(api)
    save_routes(api, model['id'], ['claim_extraction', 'entity_disambiguation'])
    install_transport(routed_handler(
        claims=[ANCHORED_CLAIMS[0]],
        links=[{'claim_ref': 'c1', 'node_id': str(uuid.uuid4()), 'role': 'primary'}]))
    imported = import_text(api)
    assert worker.tick() is True
    job = api.get('/api/v1/jobs/' + imported['job_id']).json()
    assert job['state'] == 'succeeded' and job['result']['published'] == 1
    assert job['result']['entity_links_proposed'] == 0
    assert job['result']['entity_disambiguation_detail']['rejected'] == 1
    with api.engine.connect() as conn:
        assert one(conn, "SELECT count(*) n FROM relations WHERE asserted_by='model'")['n'] == 0


def test_entity_upstream_failure_does_not_block_publication(api):
    provider = create_provider(api)
    model = create_model(api, provider['id'])
    create_node(api)
    set_budget(api)
    save_routes(api, model['id'], ['claim_extraction', 'entity_disambiguation'])
    calls = install_transport(routed_handler(claims=[ANCHORED_CLAIMS[0]], entity_status=503))
    imported = import_text(api)
    assert worker.tick() is True
    job = api.get('/api/v1/jobs/' + imported['job_id']).json()
    assert job['state'] == 'succeeded' and job['result']['published'] == 1
    assert job['result']['entity_disambiguation'] == 'unavailable'
    assert job['result']['entity_disambiguation_detail']['error'] == 'UPSTREAM_FAILED'
    assert len(calls) == 2
