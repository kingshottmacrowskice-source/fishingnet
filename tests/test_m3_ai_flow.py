"""M3 integration: providers, models, routing, budget and the invocation ledger.

No test reaches a real provider: ``httpx.MockTransport`` stands in for the network.
"""
import json
import threading
import uuid
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import text

from app.ai import budget as ai_budget
from app.ai import providers as ai_providers
from app.storage.db import one


def count(engine, table, where=''):
    with engine.connect() as conn:
        return one(conn, f'SELECT count(*) n FROM {table} {where}')['n']


def create_provider(api, **overrides):
    payload = {'name': 'p-' + uuid.uuid4().hex[:8], 'protocol': 'mock', 'base_url': '',
               'enabled': True}
    payload.update(overrides)
    response = api.post('/api/v1/model-providers', json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def create_model(api, provider_id, **overrides):
    payload = {'provider_id': provider_id, 'model_id': 'model-' + uuid.uuid4().hex[:8],
               'display_name': '测试模型', 'enabled': True,
               'input_price_per_mtok': '1.0', 'output_price_per_mtok': '2.0',
               'context_window': 4096, 'max_output_tokens': 512}
    payload.update(overrides)
    response = api.post('/api/v1/models', json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def route_payload(task, model_id, **overrides):
    payload = {'task': task, 'mode': 'model', 'enabled': True, 'primary_model_id': model_id,
               'fallback_model_ids': [], 'max_input_tokens': 1000, 'max_output_tokens': 500,
               'timeout_seconds': 5, 'max_attempts': 1, 'data_policy': 'local_only'}
    payload.update(overrides)
    return payload


def save_routes(api, routes, confirm_cloud=False, if_match='1'):
    return api.put('/api/v1/task-routes', headers={'If-Match': if_match},
                   json={'routes': routes, 'confirm_cloud': confirm_cloud})


def set_budget(api, daily='10', monthly='50', policy='approved_cloud', if_match='1', currency='USD'):
    return api.put('/api/v1/settings/budget', headers={'If-Match': if_match},
                   json={'daily_budget': daily, 'monthly_budget': monthly,
                         'data_policy': policy, 'budget_currency': currency})


def mock_transport(handler):
    ai_providers.set_transport_factory(lambda: httpx.MockTransport(handler))


def chat_ok(prompt_tokens=100, completion_tokens=50):
    def handler(request: httpx.Request):
        return httpx.Response(200, json={
            'id': 'chat-test', 'model': 'gpt-test',
            'choices': [{'message': {'content': '{"claims":[]}'}}],
            'usage': {'prompt_tokens': prompt_tokens, 'completion_tokens': completion_tokens}})
    return handler


def test_provider_crud_masks_secret_and_enforces_if_match(api):
    response = api.post('/api/v1/model-providers', json={
        'name': 'cloud-a', 'protocol': 'openai_compatible', 'base_url': 'https://llm.example/v1',
        'api_key': 'sk-live-secret-9876', 'enabled': True})
    assert response.status_code == 201, response.text
    provider = response.json()
    assert provider['configured'] is True
    assert provider['credential_hint'] == '****9876'
    assert 'sk-live-secret-9876' not in response.text
    assert 'credential_sealed' not in provider
    assert provider['is_cloud'] is True

    listing = api.get('/api/v1/model-providers')
    assert 'sk-live-secret-9876' not in listing.text

    missing = api.patch('/api/v1/model-providers/' + provider['id'], json={'enabled': False})
    assert missing.status_code == 428 and missing.json()['error']['code'] == 'PRECONDITION_REQUIRED'
    stale = api.patch('/api/v1/model-providers/' + provider['id'],
                      headers={'If-Match': '9'}, json={'enabled': False})
    assert stale.status_code == 409

    updated = api.patch('/api/v1/model-providers/' + provider['id'],
                        headers={'If-Match': '1'}, json={'enabled': False, 'name': 'cloud-b'})
    assert updated.status_code == 200, updated.text
    assert updated.json()['revision'] == 2 and updated.json()['enabled'] is False

    # A provider with models cannot be deleted; removing the model then allows it.
    model = create_model(api, provider['id'])
    blocked = api.delete('/api/v1/model-providers/' + provider['id'], headers={'If-Match': '2'})
    assert blocked.status_code == 409 and blocked.json()['error']['code'] == 'PROVIDER_IN_USE'
    assert api.delete('/api/v1/models/' + model['id'], headers={'If-Match': '1'}).status_code == 200
    removed = api.delete('/api/v1/model-providers/' + provider['id'], headers={'If-Match': '2'})
    assert removed.status_code == 200
    assert '撤权' in removed.json()['note']


def test_provider_secret_replace_and_clear(api):
    provider = create_provider(api, api_key='sk-first-1111')
    replaced = api.put('/api/v1/model-providers/' + provider['id'] + '/secret',
                       json={'api_key': 'sk-second-2222'})
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()['credential_hint'] == '****2222'
    assert 'sk-second-2222' not in replaced.text
    cleared = api.put('/api/v1/model-providers/' + provider['id'] + '/secret', json={'clear': True})
    assert cleared.status_code == 200
    assert cleared.json()['configured'] is False
    with api.engine.connect() as conn:
        row = one(conn, 'SELECT credential_sealed FROM provider_connections WHERE id=:id',
                  id=provider['id'])
    assert row['credential_sealed'] is None


def test_model_validation_currency_limits_and_duplicates(api):
    provider = create_provider(api)
    created = create_model(api, provider['id'])
    assert created['price_currency'] == 'USD'
    assert created['enabled'] is True

    wrong_currency = api.post('/api/v1/models', json={
        'provider_id': provider['id'], 'model_id': 'cny-model', 'price_currency': 'CNY'})
    assert wrong_currency.status_code == 422
    assert wrong_currency.json()['error']['code'] == 'CURRENCY_MISMATCH'

    bad_limits = api.post('/api/v1/models', json={
        'provider_id': provider['id'], 'model_id': 'bad-limits',
        'context_window': 1024, 'max_output_tokens': 2048})
    assert bad_limits.status_code == 422

    duplicate = api.post('/api/v1/models', json={
        'provider_id': provider['id'], 'model_id': created['model_id']})
    assert duplicate.status_code == 409 and duplicate.json()['error']['code'] == 'MODEL_ALREADY_EXISTS'

    negative = api.post('/api/v1/models', json={
        'provider_id': provider['id'], 'model_id': 'neg', 'input_price_per_mtok': '-1'})
    assert negative.status_code == 422
    assert negative.json()['error']['code'] == 'INVALID_MODEL_PRICE'


def test_task_routes_are_atomic_and_revisioned(api):
    provider = create_provider(api)
    model = create_model(api, provider['id'])

    defaults = api.get('/api/v1/task-routes').json()
    assert len(defaults['items']) == 8
    assert defaults['revision'] == 1
    assert all(item['mode'] == 'mock' and item['configured'] is False for item in defaults['items'])

    missing_if_match = api.put('/api/v1/task-routes', json={'routes': [route_payload('screening', model['id'])]})
    assert missing_if_match.status_code == 428

    unknown_model = save_routes(api, [route_payload('screening', str(uuid.uuid4()))])
    assert unknown_model.status_code == 422
    assert count(api.engine, 'task_routes') == 0

    unknown_task = save_routes(api, [route_payload('not_a_task', model['id'])])
    assert unknown_task.status_code == 422

    saved = save_routes(api, [route_payload('screening', model['id']),
                              route_payload('claim_extraction', model['id'])])
    assert saved.status_code == 200, saved.text
    assert saved.json()['revision'] == 2
    stored = {item['task']: item for item in saved.json()['items']}
    assert stored['screening']['configured'] is True and stored['screening']['ready'] is True
    assert stored['fact_check']['configured'] is False

    stale = save_routes(api, [route_payload('screening', model['id'])], if_match='1')
    assert stale.status_code == 409
    assert stale.json()['error']['code'] == 'REVISION_CONFLICT'


def test_cloud_route_requires_explicit_confirmation(api):
    provider = create_provider(api, protocol='openai_compatible', name='cloud-confirm',
                               base_url='https://llm.example/v1', api_key='sk-x-1234')
    model = create_model(api, provider['id'])
    route = route_payload('claim_extraction', model['id'], data_policy='approved_cloud')
    unconfirmed = save_routes(api, [route])
    assert unconfirmed.status_code == 422
    assert unconfirmed.json()['error']['code'] == 'ROUTE_CLOUD_CONFIRM_REQUIRED'
    confirmed = save_routes(api, [route], confirm_cloud=True)
    assert confirmed.status_code == 200, confirmed.text


def test_disabled_route_blocks_call_without_side_effect(api):
    provider = create_provider(api)
    model = create_model(api, provider['id'], input_price_per_mtok='0', output_price_per_mtok='0')
    disabled = route_payload('claim_extraction', model['id'], enabled=False)
    assert save_routes(api, [disabled]).status_code == 200

    confirmed = api.post('/api/v1/ai/test-call',
                         json={'task': 'claim_extraction', 'confirm_spend': True})
    assert confirmed.status_code == 409
    assert confirmed.json()['error']['code'] == 'TASK_ROUTE_DISABLED'
    assert count(api.engine, 'ai_invocations') == 0

    no_confirm = api.post('/api/v1/ai/test-call', json={'task': 'claim_extraction'})
    assert no_confirm.status_code == 422
    assert no_confirm.json()['error']['code'] == 'CALL_CONFIRM_REQUIRED'


def test_mock_route_call_is_synthetic_and_writes_ledger(api):
    provider = create_provider(api)
    model = create_model(api, provider['id'], input_price_per_mtok='0', output_price_per_mtok='0')
    assert save_routes(api, [route_payload('claim_extraction', model['id'])]).status_code == 200

    response = api.post('/api/v1/ai/test-call',
                        json={'task': 'claim_extraction', 'confirm_spend': True})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['status'] == 'succeeded'
    assert body['parsed']['synthetic'] is True
    assert body['cost']['status'] == 'none'
    assert body['cost']['amount'] == '0'
    assert '未写入知识库' in body['note']

    invocation = api.get('/api/v1/ai/invocations/' + body['invocation_id']).json()
    assert invocation['status'] == 'succeeded'
    assert invocation['cost_status'] == 'none'
    assert invocation['task_label'] == '主张抽取'
    assert count(api.engine, 'claims') == 0

    listing = api.get('/api/v1/ai/invocations?task=claim_extraction&status=succeeded')
    assert listing.status_code == 200 and len(listing.json()['items']) == 1
    assert api.get('/api/v1/ai/invocations?task=bogus').status_code == 400

    summary = api.get('/api/v1/costs/summary').json()
    assert summary['totals']['calls'] == 1 and Decimal(summary['totals']['settled']) == 0
    assert Decimal(summary['budget']['day_spent']) == 0


def test_zero_budget_blocks_cloud_call_and_records_blocked(api):
    provider = create_provider(api, protocol='openai_compatible', name='cloud-budget',
                               base_url='https://llm.example/v1', api_key='sk-x-1234')
    model = create_model(api, provider['id'])
    assert save_routes(api, [route_payload('claim_extraction', model['id'],
                                           data_policy='approved_cloud')],
                       confirm_cloud=True).status_code == 200
    assert set_budget(api, daily='0', monthly='0', policy='approved_cloud').status_code == 200

    def no_network(request):
        raise AssertionError('zero budget must not reach the provider')
    mock_transport(no_network)

    blocked = api.post('/api/v1/ai/test-call',
                       json={'task': 'claim_extraction', 'confirm_spend': True})
    assert blocked.status_code == 409
    assert blocked.json()['error']['code'] == 'BUDGET_EXHAUSTED'
    assert Decimal(blocked.json()['error']['details']['daily_budget']) == 0

    with api.engine.connect() as conn:
        row = one(conn, """SELECT status,error_code FROM ai_invocations
            WHERE workspace_id=(SELECT id FROM workspace_settings LIMIT 1)""")
    assert row['status'] == 'blocked' and row['error_code'] == 'BUDGET_EXHAUSTED'
    assert count(api.engine, 'ai_budget_ledger') == 0


def test_cloud_policy_blocks_before_budget(api):
    provider = create_provider(api, protocol='openai_compatible', name='cloud-policy',
                               base_url='https://llm.example/v1', api_key='sk-x-1234')
    model = create_model(api, provider['id'])
    assert save_routes(api, [route_payload('claim_extraction', model['id'])]).status_code == 200

    refused = api.post('/api/v1/ai/test-call',
                       json={'task': 'claim_extraction', 'confirm_spend': True})
    assert refused.status_code == 409
    assert refused.json()['error']['code'] == 'DATA_POLICY_BLOCKED'
    assert count(api.engine, 'ai_invocations') == 0


def test_successful_cloud_call_reserves_and_settles_actual_cost(api):
    provider = create_provider(api, protocol='openai_compatible', name='cloud-cost',
                               base_url='https://llm.example/v1', api_key='sk-x-1234')
    model = create_model(api, provider['id'], input_price_per_mtok='1.0',
                         output_price_per_mtok='2.0', max_output_tokens=500)
    assert save_routes(api, [route_payload('claim_extraction', model['id'],
                                           data_policy='approved_cloud')],
                       confirm_cloud=True).status_code == 200
    assert set_budget(api).status_code == 200
    mock_transport(chat_ok(100, 50))

    response = api.post('/api/v1/ai/test-call',
                        json={'task': 'claim_extraction', 'confirm_spend': True})
    assert response.status_code == 200, response.text
    body = response.json()
    # (100 * 1 + 50 * 2) / 1e6
    assert body['cost']['amount'] == '0.00020000'
    assert body['cost']['status'] == 'estimated'
    assert body['usage'] == {'input_tokens': 100, 'output_tokens': 50, 'cached_tokens': 0}

    with api.engine.connect() as conn:
        ledger = one(conn, """SELECT reserved,settled,status FROM ai_budget_ledger
            WHERE invocation_id=:id""", id=body['invocation_id'])
    assert ledger['status'] == 'settled'
    assert Decimal(str(ledger['reserved'])) == Decimal('0.00200000')
    assert Decimal(str(ledger['settled'])) == Decimal('0.00020000')

    settings = api.get('/api/v1/settings').json()
    assert Decimal(settings['budget']['day_spent']) == Decimal('0.00020000')
    summary = api.get('/api/v1/costs/summary').json()
    assert summary['totals']['settled'] == '0.00020000'
    assert summary['groups'][0]['model_label'] == '测试模型'


def test_unknown_usage_keeps_worst_case_reservation(api):
    provider = create_provider(api, protocol='openai_compatible', name='cloud-unknown',
                               base_url='https://llm.example/v1', api_key='sk-x-1234')
    model = create_model(api, provider['id'])
    assert save_routes(api, [route_payload('claim_extraction', model['id'],
                                           data_policy='approved_cloud')],
                       confirm_cloud=True).status_code == 200
    assert set_budget(api).status_code == 200
    mock_transport(lambda request: httpx.Response(200, json={
        'id': 'no-usage', 'choices': [{'message': {'content': '{"ok":true}'}}]}))

    body = api.post('/api/v1/ai/test-call',
                    json={'task': 'claim_extraction', 'confirm_spend': True}).json()
    assert body['cost']['status'] == 'unknown'
    assert body['cost']['amount'] == '0.00200000'
    summary = api.get('/api/v1/costs/summary').json()
    assert summary['totals']['unknown'] == '0.00200000'


def test_fallback_model_used_when_primary_fails(api):
    primary_provider = create_provider(api, protocol='openai_compatible', name='cloud-primary',
                                       base_url='https://llm.example/v1', api_key='sk-x-1234')
    primary = create_model(api, primary_provider['id'])
    fallback_provider = create_provider(api, protocol='mock', name='mock-fallback')
    fallback = create_model(api, fallback_provider['id'], input_price_per_mtok='0',
                            output_price_per_mtok='0', display_name='备用模型')
    route = route_payload('claim_extraction', primary['id'], data_policy='approved_cloud')
    route['fallback_model_ids'] = [fallback['id']]
    assert save_routes(api, [route], confirm_cloud=True).status_code == 200
    assert set_budget(api).status_code == 200

    def handler(request: httpx.Request):
        return httpx.Response(503, text='upstream down')
    mock_transport(handler)

    body = api.post('/api/v1/ai/test-call',
                    json={'task': 'claim_extraction', 'confirm_spend': True}).json()
    assert body['status'] == 'succeeded'
    assert body['model'] == '备用模型'
    assert body['parsed']['synthetic'] is True
    assert body['attempts'][0]['error_code'] == 'UPSTREAM_FAILED'
    assert body['cost']['amount'] == '0'
    detail = api.get('/api/v1/ai/invocations/' + body['invocation_id']).json()
    assert detail['status'] == 'succeeded'
    assert detail['ledger']['status'] == 'settled'
    assert Decimal(str(detail['ledger']['settled'])) == 0


def test_budget_reservation_is_atomic_under_concurrency(api):
    assert set_budget(api, daily='0.0005', monthly='1').status_code == 200
    workspace = api.get('/api/v1/auth/me').json()['workspace_id']
    barrier = threading.Barrier(2)
    results = []

    def attempt():
        barrier.wait()
        try:
            with api.engine.begin() as conn:
                ai_budget.reserve(conn, workspace_id=workspace, invocation_id=None,
                                  task='claim_extraction', amount=Decimal('0.0005'),
                                  currency='USD', reason='concurrency-test')
            results.append('reserved')
        except ai_budget.BudgetError as exc:
            results.append(exc.code)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert sorted(results) == ['BUDGET_EXHAUSTED', 'reserved']
    assert count(api.engine, 'ai_budget_ledger') == 1


def test_settings_budget_revision_and_currency_change_guard(api):
    assert set_budget(api, daily='5', monthly='20', policy='local_only').status_code == 200
    settings = api.get('/api/v1/settings').json()
    assert settings['revision'] == 2
    assert settings['data_policy'] == 'local_only'
    assert Decimal(settings['daily_budget']) == 5

    stale = set_budget(api, daily='6', if_match='1')
    assert stale.status_code == 409

    with api.engine.begin() as conn:
        conn.execute(text("""INSERT INTO ai_budget_ledger(workspace_id,day,month_start,reserved,
            currency,reason) VALUES(:ws,current_date,date_trunc('month',current_date)::date,1,'USD','seed')"""),
            {'ws': api.get('/api/v1/auth/me').json()['workspace_id']})
    blocked = api.put('/api/v1/settings/budget', headers={'If-Match': '2'},
                      json={'daily_budget': '7', 'monthly_budget': '20', 'budget_currency': 'CNY'})
    assert blocked.status_code == 409
    assert blocked.json()['error']['code'] == 'CURRENCY_CHANGE_BLOCKED'


def test_provider_test_endpoint_uses_policy_and_fixed_prompt(api):
    provider = create_provider(api, protocol='openai_compatible', name='cloud-test',
                               base_url='https://llm.example/v1', api_key='sk-x-1234')
    create_model(api, provider['id'])
    refused = api.post('/api/v1/model-providers/' + provider['id'] + '/test')
    assert refused.status_code == 409
    assert refused.json()['error']['code'] == 'DATA_POLICY_BLOCKED'

    mock_provider = create_provider(api, protocol='mock', name='mock-test')
    create_model(api, mock_provider['id'])
    result = api.post('/api/v1/model-providers/' + mock_provider['id'] + '/test')
    assert result.status_code == 200, result.text
    assert result.json()['provider_status'] == 'ok'
    assert '不发起网络请求' in result.json()['note']
    listed = {item['id']: item for item in api.get('/api/v1/model-providers').json()['items']}
    assert listed[mock_provider['id']]['status'] == 'ok'


def test_model_delete_blocked_while_routed(api):
    provider = create_provider(api)
    model = create_model(api, provider['id'])
    assert save_routes(api, [route_payload('screening', model['id'])]).status_code == 200
    blocked = api.delete('/api/v1/models/' + model['id'], headers={'If-Match': '1'})
    assert blocked.status_code == 409
    assert blocked.json()['error']['code'] == 'MODEL_IN_USE'
    assert 'screening' in blocked.json()['error']['details']['tasks']


def test_model_with_invocation_history_cannot_be_deleted(api):
    provider = create_provider(api)
    model = create_model(api, provider['id'], input_price_per_mtok='0', output_price_per_mtok='0')
    assert save_routes(api, [route_payload('claim_extraction', model['id'])]).status_code == 200
    call = api.post('/api/v1/ai/test-call',
                    json={'task': 'claim_extraction', 'confirm_spend': True})
    assert call.status_code == 200, call.text
    # Move the route off the model so "still routed" is no longer the blocking reason.
    assert save_routes(api, [route_payload('claim_extraction', model['id'], mode='mock',
                                           enabled=False)], if_match='2').status_code == 200
    blocked = api.delete('/api/v1/models/' + model['id'], headers={'If-Match': '1'})
    assert blocked.status_code == 409
    assert blocked.json()['error']['code'] == 'MODEL_HAS_HISTORY'
    assert blocked.json()['error']['details']['invocations'] == 1


def test_currency_change_blocked_by_existing_models(api):
    provider = create_provider(api)
    create_model(api, provider['id'])
    blocked = api.put('/api/v1/settings/budget', headers={'If-Match': '1'},
                      json={'daily_budget': '5', 'monthly_budget': '10', 'budget_currency': 'CNY'})
    assert blocked.status_code == 409
    assert blocked.json()['error']['code'] == 'CURRENCY_CHANGE_BLOCKED'
    assert blocked.json()['error']['details']['models'] == 1
