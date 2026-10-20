"""Short-lived QR login sessions (single API process, in-memory).

The challenge key can be exchanged for account cookies, so it is kept only in
this process, never returned to the browser, never logged and never persisted.
A restart loses pending sessions; the UI then shows "已失效，请刷新二维码" and
the user scans again — the stored credential is unaffected.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

TTL_SECONDS = 180
MAX_POLLS = 60
POLL_MIN_INTERVAL = 1.5
START_COOLDOWN = 10.0

TERMINAL = ('confirmed', 'expired', 'error')


class QrSessionError(Exception):
    def __init__(self, code: str, message: str, retry_after: int | None = None):
        self.code = code
        self.message = message
        self.retry_after = retry_after
        super().__init__(message)


@dataclass
class QrSession:
    auth_id: str
    workspace_id: str
    platform: str
    created_at: float
    expires_at: float
    key: str = field(default='', repr=False)
    url: str = ''
    status: str = 'starting'
    message: str = ''
    error_code: str | None = None
    retryable: bool = False
    polls: int = 0
    last_poll_at: float = 0.0

    def view(self) -> dict:
        return {
            'auth_id': self.auth_id,
            'platform': self.platform,
            'status': self.status,
            'message': self.message,
            'error_code': self.error_code,
            'retryable': self.retryable,
            'qr_url': self.url,
            'expires_at': datetime.fromtimestamp(self.expires_at, tz=timezone.utc).isoformat(),
            'polls': self.polls,
            'poll_interval_seconds': 2,
        }


class QrSessionStore:
    def __init__(self, *, ttl: int = TTL_SECONDS, clock=time.time):
        self._ttl = ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._sessions: dict[str, QrSession] = {}
        self._last_start: dict[tuple[str, str], float] = {}

    def _purge(self, now: float):
        for auth_id, session in list(self._sessions.items()):
            if session.expires_at <= now and session.status not in TERMINAL:
                session.status = 'expired'
                session.message = '二维码已过期，请刷新'
            if session.expires_at + 30 <= now:
                self._sessions.pop(auth_id, None)

    def reserve(self, workspace_id, platform: str) -> QrSession:
        now = self._clock()
        with self._lock:
            self._purge(now)
            key = (str(workspace_id), platform)
            for session in self._sessions.values():
                if (session.workspace_id, session.platform) == key and session.status not in TERMINAL:
                    raise QrSessionError('QR_SESSION_ACTIVE', '已有进行中的扫码会话，请先取消或等待其过期')
            last = self._last_start.get(key)
            if last is not None and now - last < START_COOLDOWN:
                raise QrSessionError('QR_RATE_LIMITED', '二维码创建过于频繁，请稍后再试',
                                     retry_after=max(1, int(START_COOLDOWN - (now - last))))
            session = QrSession(auth_id=str(uuid.uuid4()), workspace_id=key[0], platform=platform,
                                created_at=now, expires_at=now + self._ttl)
            self._sessions[session.auth_id] = session
            self._last_start[key] = now
            return session

    def attach(self, session: QrSession, key: str, url: str):
        with self._lock:
            session.key = key
            session.url = url
            session.status = 'waiting_scan'
            session.message = '等待手机扫码'

    def get(self, workspace_id, platform: str, auth_id: str) -> QrSession | None:
        with self._lock:
            self._purge(self._clock())
            session = self._sessions.get(str(auth_id))
            if not session or session.workspace_id != str(workspace_id) or session.platform != platform:
                return None
            return session

    def begin_poll(self, session: QrSession) -> bool:
        """Return False when the client polls faster than allowed (cached status)."""
        with self._lock:
            now = self._clock()
            if session.status in TERMINAL:
                return False
            if now - session.last_poll_at < POLL_MIN_INTERVAL:
                return False
            if session.polls >= MAX_POLLS:
                session.status = 'expired'
                session.message = '扫码轮询次数已达上限，请刷新二维码'
                return False
            session.polls += 1
            session.last_poll_at = now
            return True

    def update(self, session: QrSession, status: str, message: str = '',
               error_code: str | None = None, retryable: bool = False):
        with self._lock:
            session.status = status
            session.message = message
            session.error_code = error_code
            session.retryable = retryable

    def drop(self, workspace_id, platform: str, auth_id: str):
        with self._lock:
            session = self._sessions.get(str(auth_id))
            if session and session.workspace_id == str(workspace_id) and session.platform == platform:
                self._sessions.pop(str(auth_id), None)

    def clear(self, workspace_id, platform: str):
        with self._lock:
            for auth_id, session in list(self._sessions.items()):
                if session.workspace_id == str(workspace_id) and session.platform == platform:
                    self._sessions.pop(auth_id, None)

    def clear_all(self):
        with self._lock:
            self._sessions.clear()
            self._last_start.clear()


qr_sessions = QrSessionStore()
