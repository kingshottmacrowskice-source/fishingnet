"""M3 unit tests: pricing, budget math, key sealing and provider protocol shapes.

No database and no real network: provider HTTP is a deterministic httpx MockTransport.
"""
import json
from decimal import Decimal

import httpx
import pytest

from app.ai import budget, providers
from app.config import settings
from app.domain.model_secrets import (mask_hint, open_api_key, seal_api_key,
                                      validate_api_key)
from app.domain.secrets import SecretError

MODEL = {'model_id': 'gpt-test', 'display_name': '测试模型', 'input_price_per_mtok': Decimal('1.0'),
         'output_price_per_mtok': Decimal('2.0'), 'price_currency': 'USD',
         'max_output_tokens': 512, 'supports_structured_output': True}
PROVIDER = {'protocol': 'openai_compatible', 'base_url': 'https://llm.example/v1', 'is_local': False}
MESSAGES = [{'role': 'system', 'content': '只输出 JSON'}, {'role': 'user', 'content': '合成样例'}]


def _transport(handler):
    providers.set_transport_factory(lambda: httpx.MockTransport(handler))


def test_estimate_cost_uses_per_million_prices():
    assert budget.estimate_cost(1000, 500, MODEL) == Decimal('0.00200000')
    assert budget.estimate_cost(0, 0, MODEL) == Decimal('0')
    assert budget.estimate_cost(-5, -5, MODEL) == Decimal('0')


def test_reservation_uses_both_token_ceilings():
    route = {'max_input_tokens': 1000, 'max_output_tokens': 500}
    assert budget.reservation_amount(route, MODEL) == Decimal('0.00200000')
    assert budget.reservation_amount({'max_input_tokens': 0, 'max_output_tokens': 0}, MODEL) == Decimal('0')


def test_normalize_usage_accepts_missing_and_cached_tokens():
    assert budget.normalize_usage(None) == {'input_tokens': 0, 'output_tokens': 0, 'cached_tokens': 0}
    assert budget.normalize_usage({'prompt_tokens': '12', 'completion_tokens': 3,
                                   'prompt_tokens_details': {'cached_tokens': 4}}) == {
        'input_tokens': 12, 'output_tokens': 3, 'cached_tokens': 4}
    assert budget.normalize_usage({'prompt_tokens': -1, 'completion_tokens': 'x'}) == {
        'input_tokens': 0, 'output_tokens': 0, 'cached_tokens': 0}


@pytest.mark.parametrize('value', ['-1', 'NaN', 'Infinity', 'abc', '2000000'])
def test_to_decimal_rejects_invalid_amounts(value):
    with pytest.raises(budget.BudgetError) as exc:
        budget.to_decimal(value)
    assert exc.value.code == 'INVALID_AMOUNT'


def test_to_decimal_quantizes():
    assert budget.to_decimal('0.123456789') == Decimal('0.12345679')


def test_api_key_validation_and_masking(monkeypatch):
    monkeypatch.setattr(settings, 'master_key', 'unit-test-master-key-0123456789')
    token, hint = seal_api_key('sk-test-abcd1234')
    assert hint == '****1234'
    assert 'sk-test-abcd1234' not in token
    assert open_api_key(token) == 'sk-test-abcd1234'
    assert mask_hint('abc') == '****'
    for bad in ('', 'has space', 'line\nbreak', 'x' * 5000):
        with pytest.raises(SecretError):
            validate_api_key(bad)


def test_api_key_seal_requires_master_key(monkeypatch):
    monkeypatch.setattr(settings, 'master_key', '')
    with pytest.raises(SecretError) as exc:
        seal_api_key('sk-test-abcd')
    assert exc.value.code == 'MASTER_KEY_NOT_CONFIGURED'


def test_api_key_cannot_be_opened_with_platform_schema(monkeypatch):
    from app.domain.secrets import open_sealed
    monkeypatch.setattr(settings, 'master_key', 'unit-test-master-key-0123456789')
    token, _ = seal_api_key('sk-test-abcd1234')
    with pytest.raises(SecretError):
        open_sealed(token)


@pytest.mark.parametrize('url,protocol', [
    ('ftp://example.com/v1', 'openai_compatible'),
    ('https://user:pass@example.com/v1', 'openai_compatible'),
    ('https://example.com/v1?token=1', 'openai_compatible'),
    ('', 'openai_compatible'),
    ('https://exa mple.com/v1', 'openai_compatible'),
])
def test_base_url_rejects_unsafe_shapes(url, protocol):
    with pytest.raises(providers.ProviderError) as exc:
        providers.validate_base_url(url, protocol)
    assert exc.value.code == 'INVALID_CONFIG'


def test_base_url_accepts_local_http_and_strips_slash():
    assert providers.validate_base_url('http://127.0.0.1:11434/v1/', 'local') == 'http://127.0.0.1:11434/v1'
    assert providers.validate_base_url('whatever', 'mock') == ''


def test_call_chat_builds_request_and_parses_usage():
    seen = {}

    def handler(request: httpx.Request):
        seen['url'] = str(request.url)
        seen['auth'] = request.headers.get('authorization')
        seen['body'] = json.loads(request.content)
        return httpx.Response(200, json={'id': 'chat-1', 'model': 'gpt-test',
                                         'choices': [{'message': {'content': '{"claims":[]}'}}],
                                         'usage': {'prompt_tokens': 100, 'completion_tokens': 50}})

    _transport(handler)
    result = providers.call_chat(PROVIDER, MODEL, MESSAGES, api_key='sk-secret-123',
                                 timeout=5, structured=True, max_tokens=128)
    assert seen['url'] == 'https://llm.example/v1/chat/completions'
    assert seen['auth'] == 'Bearer sk-secret-123'
    assert seen['body']['model'] == 'gpt-test'
    assert seen['body']['max_tokens'] == 128
    assert seen['body']['response_format'] == {'type': 'json_object'}
    assert 'sk-secret-123' not in json.dumps(seen['body'])
    assert result['content'] == '{"claims":[]}'
    assert result['provider_request_id'] == 'chat-1'


def test_call_chat_omits_response_format_when_model_does_not_support_it():
    seen = {}

    def handler(request: httpx.Request):
        seen['body'] = json.loads(request.content)
        return httpx.Response(200, json={'choices': [{'message': {'content': 'ok'}}]})

    _transport(handler)
    providers.call_chat(PROVIDER, {**MODEL, 'supports_structured_output': False}, MESSAGES,
                        api_key='k', timeout=5, structured=True)
    assert 'response_format' not in seen['body']


def test_call_chat_does_not_follow_redirects():
    _transport(lambda request: httpx.Response(302, headers={'location': 'https://evil.example/'}))
    with pytest.raises(providers.ProviderError) as exc:
        providers.call_chat(PROVIDER, MODEL, MESSAGES, api_key='k', timeout=5, structured=False)
    assert exc.value.code == 'UPSTREAM_REJECTED'


@pytest.mark.parametrize('status,code,retryable', [
    (401, 'AUTH_FAILED', False), (403, 'AUTH_FAILED', False),
    (404, 'ENDPOINT_NOT_FOUND', False), (429, 'RATE_LIMITED', True),
    (500, 'UPSTREAM_FAILED', True), (400, 'UPSTREAM_REJECTED', False),
])
def test_provider_error_mapping(status, code, retryable):
    _transport(lambda request: httpx.Response(status, text='{"error":"nope"}'))
    with pytest.raises(providers.ProviderError) as exc:
        providers.call_chat(PROVIDER, MODEL, MESSAGES, api_key='sk-secret-123', timeout=5, structured=False)
    assert exc.value.code == code
    assert exc.value.retryable is retryable
    assert 'sk-secret-123' not in exc.value.message


def test_provider_timeout_is_retryable_network_error():
    def handler(request: httpx.Request):
        raise httpx.ReadTimeout('timed out', request=request)

    _transport(handler)
    with pytest.raises(providers.ProviderError) as exc:
        providers.call_chat(PROVIDER, MODEL, MESSAGES, api_key='sk-secret-123', timeout=5, structured=False)
    assert exc.value.code == 'NETWORK_FAILED'
    assert exc.value.retryable is True
    assert 'sk-secret-123' not in exc.value.message


@pytest.mark.parametrize('body', [
    'not json', json.dumps({'choices': []}), json.dumps({'choices': [{'message': {'content': '  '}}]}),
    json.dumps({'choices': [{}]}),
])
def test_invalid_provider_payloads_are_rejected(body):
    _transport(lambda request: httpx.Response(200, text=body))
    with pytest.raises(providers.ProviderError) as exc:
        providers.call_chat(PROVIDER, MODEL, MESSAGES, api_key='k', timeout=5, structured=False)
    assert exc.value.code == 'INVALID_RESPONSE'


def test_mock_provider_makes_no_request_and_is_marked_synthetic():
    def handler(request: httpx.Request):
        raise AssertionError('mock provider must not touch the transport')

    _transport(handler)
    result = providers.call_chat({'protocol': 'mock', 'base_url': '', 'is_local': True}, MODEL,
                                 MESSAGES, api_key='', timeout=5, structured=True)
    payload = json.loads(result['content'])
    assert payload['synthetic'] is True
    assert 'Mock' in payload['note']
    assert result['provider_request_id'] == 'mock-local'


def test_probe_prompt_contains_no_user_material():
    seen = {}

    def handler(request: httpx.Request):
        seen['body'] = json.loads(request.content)
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"ok":true}'}}]})

    _transport(handler)
    providers.probe(PROVIDER, MODEL, api_key='k', timeout=5)
    text = json.dumps(seen['body'], ensure_ascii=False)
    assert '连接测试' in text
    assert 'Plugin X' not in text


def test_protocol_list_excludes_unimplemented_native_protocols():
    assert set(providers.PROTOCOLS) == {'openai', 'openai_compatible', 'local', 'mock'}
    assert 'anthropic' not in providers.PROTOCOLS and 'gemini' not in providers.PROTOCOLS
