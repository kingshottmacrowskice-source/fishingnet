"""Model provider transports for M3.

Supported protocols are the three the requirements list as MVP plus a local Mock
provider: ``openai`` (native Chat Completions), ``openai_compatible`` (any endpoint
that speaks the same JSON shape), ``local`` (the same shape on a local endpoint)
and ``mock`` (no network at all). Anthropic/Gemini native protocols are explicitly
not implemented.

The transport never retries by itself, never follows redirects, and never puts the
API key anywhere except the ``Authorization`` header of one request. Responses are
bounded and parsed defensively; an unrecognised body becomes ``INVALID_RESPONSE``
instead of a silent success.
"""

from __future__ import annotations

import json
import urllib.parse
from typing import Callable

import httpx

PROTOCOLS = ('openai', 'openai_compatible', 'local', 'mock')
CLOUD_PROTOCOLS = ('openai', 'openai_compatible')
MAX_BASE_URL_LENGTH = 500
DEFAULT_TIMEOUT = 30
MAX_RESPONSE_BYTES = 2 * 1024 * 1024

# Tests install a deterministic ``httpx.MockTransport`` here; the API never sets it.
TRANSPORT_FACTORY: Callable[[], httpx.BaseTransport] | None = None


class ProviderError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False, detail: str = ''):
        self.code = code
        self.message = message
        self.retryable = retryable
        self.detail = detail
        super().__init__(message)


def set_transport_factory(factory) -> None:
    global TRANSPORT_FACTORY
    TRANSPORT_FACTORY = factory


def reset_transport_factory() -> None:
    global TRANSPORT_FACTORY
    TRANSPORT_FACTORY = None


def validate_base_url(raw: str, protocol: str) -> str:
    """Only plain http(s) endpoints without embedded credentials are accepted."""
    url = (raw or '').strip()
    if protocol == 'mock':
        return ''
    if not url:
        raise ProviderError('INVALID_CONFIG', '请填写供应商端点（例如 https://api.openai.com/v1）')
    if len(url) > MAX_BASE_URL_LENGTH:
        raise ProviderError('INVALID_CONFIG', '供应商端点过长')
    if any(character.isspace() for character in url) or '@' in url:
        raise ProviderError('INVALID_CONFIG', '供应商端点不能含空白或账号信息')
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        raise ProviderError('INVALID_CONFIG', '供应商端点必须是 http(s) URL')
    if parsed.query or parsed.fragment:
        raise ProviderError('INVALID_CONFIG', '供应商端点不能含查询参数或片段；请只填写基础路径')
    return url.rstrip('/')


def is_cloud(protocol: str) -> bool:
    return protocol in CLOUD_PROTOCOLS


def _client(timeout: int) -> httpx.Client:
    transport = TRANSPORT_FACTORY() if TRANSPORT_FACTORY else None
    return httpx.Client(timeout=httpx.Timeout(timeout, connect=min(timeout, 10)),
                        follow_redirects=False, transport=transport)


def _authorization(api_key: str) -> dict:
    return {'Authorization': f'Bearer {api_key}'} if api_key else {}


def _error_from_status(status: int, body: str) -> ProviderError:
    snippet = (body or '').strip()[:300]
    if 300 <= status < 400:
        return ProviderError('UPSTREAM_REJECTED', '供应商返回重定向；为避免把密钥发往其他主机，已停止', False, snippet)
    if status in (401, 403):
        return ProviderError('AUTH_FAILED', '供应商拒绝认证；请检查 API 密钥或该密钥的模型权限', False, snippet)
    if status == 429:
        return ProviderError('RATE_LIMITED', '供应商限流；请稍后重试或更换模型', True, snippet)
    if status == 404:
        return ProviderError('ENDPOINT_NOT_FOUND', '供应商端点或模型不存在；请检查端点与模型 ID', False, snippet)
    if status >= 500:
        return ProviderError('UPSTREAM_FAILED', f'供应商返回 HTTP {status}', True, snippet)
    return ProviderError('UPSTREAM_REJECTED', f'供应商拒绝了请求（HTTP {status}）', False, snippet)


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get('text'), str):
                parts.append(item['text'])
            elif isinstance(item, str):
                parts.append(item)
        return ''.join(parts)
    return ''


def call_chat(provider: dict, model: dict, messages: list[dict], *, api_key: str,
              timeout: int, structured: bool, max_tokens: int | None = None) -> dict:
    """One non-streaming Chat Completions request. Raises ``ProviderError``."""
    protocol = provider['protocol']
    if protocol == 'mock':
        return _mock_response(messages)
    if protocol not in PROTOCOLS:
        raise ProviderError('UNSUPPORTED_PROTOCOL', '不支持的供应商协议')
    url = f"{str(provider['base_url']).rstrip('/')}/chat/completions"
    body = {
        'model': model['model_id'],
        'messages': messages,
        'temperature': 0,
        'max_tokens': int(max_tokens or model.get('max_output_tokens') or 1024),
    }
    if structured and model.get('supports_structured_output'):
        body['response_format'] = {'type': 'json_object'}
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json',
               'User-Agent': 'FishingNet/0.1 local-single-user', **_authorization(api_key)}
    try:
        with _client(timeout) as client:
            response = client.post(url, json=body, headers=headers)
    except httpx.TimeoutException:
        raise ProviderError('NETWORK_FAILED', '连接供应商超时', True)
    except httpx.RequestError as exc:
        # Never echo the exception text: it can contain the request URL and headers.
        raise ProviderError('NETWORK_FAILED', f'无法连接供应商（{type(exc).__name__}）', True)
    raw = response.text
    if len(raw.encode('utf-8', 'ignore')) > MAX_RESPONSE_BYTES:
        raise ProviderError('INVALID_RESPONSE', '供应商响应过大，已停止解析')
    if response.status_code >= 300:
        raise _error_from_status(response.status_code, raw)
    try:
        payload = json.loads(raw)
    except ValueError:
        raise ProviderError('INVALID_RESPONSE', '供应商返回的不是 JSON')
    if not isinstance(payload, dict):
        raise ProviderError('INVALID_RESPONSE', '供应商返回结构无效')
    choices = payload.get('choices')
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ProviderError('INVALID_RESPONSE', '供应商响应缺少 choices')
    message = choices[0].get('message') or {}
    text = _content_text(message.get('content') if isinstance(message, dict) else '')
    if not text.strip():
        raise ProviderError('INVALID_RESPONSE', '供应商返回了空内容')
    return {'content': text.strip(), 'usage': payload.get('usage'),
            'provider_request_id': str(payload.get('id') or response.headers.get('x-request-id') or ''),
            'model': str(payload.get('model') or model['model_id'])}


def _mock_response(messages: list[dict]) -> dict:
    content = json.dumps({
        'synthetic': True,
        'note': 'Mock 提供器不发起网络请求，这不是真实模型输出，也不代表已完成 AI 分析。',
        'message_count': len(messages),
    }, ensure_ascii=False)
    return {'content': content, 'usage': {'prompt_tokens': 0, 'completion_tokens': 0},
            'provider_request_id': 'mock-local', 'model': 'mock-echo'}


def probe(provider: dict, model: dict, *, api_key: str, timeout: int,
          max_tokens: int = 128) -> dict:
    """A tiny fixed prompt used only by the explicit ``test connection`` action.

    It contains no user material, so testing a cloud provider does not send imported
    content anywhere.
    """
    messages = [
        {'role': 'system', 'content': '你是连接测试。只输出 JSON 对象 {"ok": true}。'},
        {'role': 'user', 'content': '连接测试：请返回 {"ok": true}。'},
    ]
    result = call_chat(provider, model, messages, api_key=api_key, timeout=timeout,
                       structured=False, max_tokens=max_tokens)
    return result
