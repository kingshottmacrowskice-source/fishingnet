import secrets
from datetime import datetime, timezone
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import HTTPException, Request, Response
from app.config import settings
from app.domain.evidence import digest
from app.storage.db import engine, one, execute

hasher = PasswordHasher()
DUMMY_HASH = hasher.hash(secrets.token_urlsafe(24))


def verify_password(encoded, password):
    try:
        return hasher.verify(encoded, password)
    except VerificationError:
        return False


def new_session(conn, user, request: Request, response: Response):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    execute(conn, '''INSERT INTO auth_sessions(workspace_id,user_id,token_hash,csrf_hash,device,idle_expires_at,absolute_expires_at)
        VALUES (:ws,:uid,:token,:csrf,:device,now()+interval '30 minutes',now()+interval '24 hours')''',
        ws=user['workspace_id'], uid=user['id'], token=digest(token), csrf=digest(csrf), device=request.headers.get('user-agent', 'unknown')[:160])
    response.set_cookie('fn_session', token, httponly=True, secure=settings.cookie_secure, samesite='strict', max_age=86400, path='/')
    response.set_cookie('fn_csrf', csrf, httponly=False, secure=settings.cookie_secure, samesite='strict', max_age=86400, path='/')


def current_user(request: Request):
    token = request.cookies.get('fn_session', '')
    with engine.begin() as conn:
        user = one(conn, '''SELECT u.*,s.id AS session_id,s.csrf_hash FROM auth_sessions s JOIN users u ON u.id=s.user_id
          WHERE s.token_hash=:token AND s.revoked_at IS NULL AND s.idle_expires_at>now() AND s.absolute_expires_at>now()''', token=digest(token))
        if not user:
            raise HTTPException(401, '请先登录')
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            csrf = request.headers.get('x-csrf-token', '')
            if not secrets.compare_digest(digest(csrf), user['csrf_hash']):
                raise HTTPException(403, 'CSRF 校验失败，请刷新页面')
        execute(conn, "UPDATE auth_sessions SET last_seen_at=now(),idle_expires_at=LEAST(now()+interval '30 minutes',absolute_expires_at) WHERE id=:id", id=user['session_id'])
    return user
