"""Sealing of model provider API keys at rest.

Keys use the same deployment ``MASTER_KEY`` as platform credentials but a
different derivation label and schema, so a platform cookie can never be
mistaken for a model key. Only the sealed value and a masked hint (``****`` plus
the last four characters) are stored; the plaintext key is never returned by the
API, written to logs, included in errors, or placed in an invocation snapshot.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings
from app.domain.secrets import SecretError

SCHEMA_VERSION = 'model-credential.v1'
ALLOWED_KEYS = ('api_key',)
MAX_KEY_LENGTH = 4096
# Printable ASCII without whitespace: an HTTP header value must not contain CR/LF.
_KEY_PATTERN = re.compile(r'^[\x21-\x7e]+$')


def _fernet() -> Fernet:
    master = (settings.master_key or '').strip()
    if len(master) < 16:
        raise SecretError('MASTER_KEY_NOT_CONFIGURED',
                          '部署端主密钥未配置，无法安全保存模型密钥；请在 .env 设置 MASTER_KEY 后重启服务')
    derived = hashlib.sha256(f'fishingnet.model-credential.v1|{master}'.encode('utf-8')).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def validate_api_key(raw) -> str:
    key = (raw or '').strip()
    if not key:
        raise SecretError('CREDENTIAL_REQUIRED', '请填写模型供应商 API 密钥')
    if len(key) > MAX_KEY_LENGTH:
        raise SecretError('CREDENTIAL_TOO_LONG', '模型密钥过长，请检查是否粘贴了完整文件内容')
    if not _KEY_PATTERN.match(key):
        raise SecretError('INVALID_CREDENTIAL', '模型密钥只能包含可见 ASCII 字符，不能含空格或换行')
    return key


def seal_api_key(raw) -> tuple[str, str]:
    """Return ``(sealed_token, masked_hint)``; the plaintext is not retained."""
    key = validate_api_key(raw)
    payload = {'schema_version': SCHEMA_VERSION, 'values': {'api_key': key}}
    token = _fernet().encrypt(json.dumps(payload, ensure_ascii=False).encode('utf-8')).decode('ascii')
    return token, mask_hint(key)


def open_api_key(token: str) -> str:
    if not token:
        return ''
    try:
        payload = json.loads(_fernet().decrypt(token.encode('ascii')))
    except (InvalidToken, ValueError, UnicodeError):
        raise SecretError('CREDENTIAL_UNREADABLE',
                          '已保存的模型密钥无法解密（主密钥可能已更换）；请重新填写密钥')
    values = payload.get('values') if isinstance(payload, dict) else None
    if not isinstance(values, dict) or payload.get('schema_version') != SCHEMA_VERSION:
        raise SecretError('CREDENTIAL_UNREADABLE', '已保存的模型密钥结构无效；请重新填写密钥')
    key = values.get('api_key')
    if not isinstance(key, str) or not key:
        raise SecretError('CREDENTIAL_UNREADABLE', '已保存的模型密钥缺少内容；请重新填写密钥')
    return key


def mask_hint(key: str) -> str:
    key = key or ''
    if len(key) <= 4:
        return '****'
    return '****' + key[-4:]
