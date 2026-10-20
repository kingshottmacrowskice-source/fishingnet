"""Sealing of platform credentials at rest.

Only the provider's session cookie (``SESSDATA``) is stored; it is encrypted with
a key derived from the deployment ``MASTER_KEY`` so the database alone is not
enough to reuse it. Plaintext is never logged, returned by the API, or included
in error messages.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

SCHEMA_VERSION = 'platform-credential.v1'
ALLOWED_COOKIE_KEYS = ('SESSDATA',)
_SESSDATA_PATTERN = re.compile(r'^[A-Za-z0-9%*_\-\.~+/=]{8,1024}$')


class SecretError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _fernet() -> Fernet:
    master = (settings.master_key or '').strip()
    if len(master) < 16:
        raise SecretError('MASTER_KEY_NOT_CONFIGURED',
                          '部署端主密钥未配置，无法安全保存平台凭证；请在 .env 设置 MASTER_KEY 后重启服务')
    derived = hashlib.sha256(f'fishingnet.platform-credential.v1|{master}'.encode('utf-8')).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def seal(values: dict) -> str:
    payload = {'schema_version': SCHEMA_VERSION,
               'values': {key: str(value) for key, value in values.items()}}
    return _fernet().encrypt(json.dumps(payload, ensure_ascii=False).encode('utf-8')).decode('ascii')


def open_sealed(token: str) -> dict:
    if not token:
        return {}
    try:
        payload = json.loads(_fernet().decrypt(token.encode('ascii')))
    except (InvalidToken, ValueError, UnicodeError):
        raise SecretError('CREDENTIAL_UNREADABLE',
                          '已保存的平台凭证无法解密（主密钥可能已更换）；请重新提供凭证')
    values = payload.get('values') if isinstance(payload, dict) else None
    if not isinstance(values, dict):
        raise SecretError('CREDENTIAL_UNREADABLE', '已保存的平台凭证结构无效；请重新提供凭证')
    return {key: str(value) for key, value in values.items() if key in ALLOWED_COOKIE_KEYS}


def parse_cookie_input(raw: str) -> dict:
    """Accept either a ``Cookie:`` header or a bare SESSDATA value.

    Unknown cookies are deliberately discarded: the collector only needs the
    session cookie for read-only caption access.
    """
    text = (raw or '').strip()
    if not text:
        raise SecretError('CREDENTIAL_REQUIRED', '请粘贴 B站 Cookie 或 SESSDATA 值')
    if len(text) > 4096:
        raise SecretError('CREDENTIAL_TOO_LONG', '凭证内容过长，只应粘贴 Cookie 或 SESSDATA')
    values = {}
    for part in text.split(';'):
        name, _, value = part.strip().partition('=')
        name = name.strip()
        value = value.strip().strip('"')
        if name.lower() == 'cookie':
            continue
        if name in ALLOWED_COOKIE_KEYS and value:
            values[name] = value
    if not values:
        candidate = text.split(';')[0].strip()
        # A bare SESSDATA value never contains '='; rejecting it avoids storing
        # malformed headers such as "SESSDATA=" as if they were the value.
        if '=' not in candidate and _SESSDATA_PATTERN.match(candidate):
            values['SESSDATA'] = candidate
    if not values.get('SESSDATA'):
        raise SecretError('INVALID_CREDENTIAL',
                          '没有识别到 SESSDATA。请在浏览器开发者工具中复制完整 Cookie，或只粘贴 SESSDATA 的值')
    if not _SESSDATA_PATTERN.match(values['SESSDATA']):
        raise SecretError('INVALID_CREDENTIAL', 'SESSDATA 格式不正确，请重新复制')
    return values
