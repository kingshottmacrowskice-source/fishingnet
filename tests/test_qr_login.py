"""QR login state machine, credential sealing and session hygiene.

Every collector is a double; no test performs a real Bilibili request.
"""
import pytest

from app.collectors import register
from app.collectors.fake import FakeBilibiliCollector
from app.domain.qr_sessions import qr_sessions
from app.storage.db import one


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    fake_clock = Clock()
    monkeypatch.setattr(qr_sessions, '_clock', fake_clock)
    return fake_clock


def use(collector):
    register('bilibili', lambda: collector)
    return collector


def start(api):
    response = api.post('/api/v1/platforms/bilibili/qr')
    assert response.status_code == 201, response.text
    return response.json()


def poll(api, auth_id):
    return api.get('/api/v1/platforms/bilibili/qr/' + auth_id)


def test_qr_start_exposes_url_but_not_challenge_key(api, clock):
    fake = use(FakeBilibiliCollector())
    body = start(api)
    assert body['status'] == 'waiting_scan'
    assert body['qr_url'].startswith('https://example.test/')
    assert body['polls'] == 0
    assert 'synthetic-qrcode-key' not in str(body)
    assert fake.calls == ['start_qr_login']

    second = api.post('/api/v1/platforms/bilibili/qr')
    assert second.status_code == 409
    assert second.json()['error']['code'] == 'QR_SESSION_ACTIVE'


def test_qr_poll_transitions_and_seals_credential(api, clock):
    fake = use(FakeBilibiliCollector(qr_polls_before_confirm=2))
    auth_id = start(api)['auth_id']

    first = poll(api, auth_id)
    assert first.status_code == 200
    assert first.json()['status'] == 'waiting_scan'

    # Polling faster than the interval must not hit upstream again.
    cached = poll(api, auth_id)
    assert cached.json()['status'] == 'waiting_scan'
    assert fake.qr_polls == 1

    clock.advance(2)
    assert poll(api, auth_id).json()['status'] == 'scanned'
    clock.advance(2)
    confirmed = poll(api, auth_id)
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body['status'] == 'connected' and body['credential_configured'] is True
    assert 'fake-qr-session-value' not in confirmed.text

    with api.engine.connect() as conn:
        row = one(conn, 'SELECT * FROM platform_connections')
        assert row['status'] == 'connected'
        assert row['credential_sealed']
        assert 'fake-qr-session-value' not in row['credential_sealed']
        assert one(conn, "SELECT count(*) n FROM audit_events WHERE action='platform_qr_connected'")['n'] == 1

    # The session is consumed once the credential is stored.
    assert poll(api, auth_id).status_code == 410


def test_qr_cancel_and_disconnect_remove_pending_sessions(api, clock):
    use(FakeBilibiliCollector())
    auth_id = start(api)['auth_id']
    cancelled = api.post('/api/v1/platforms/bilibili/qr/' + auth_id + '/cancel')
    assert cancelled.status_code == 200
    assert cancelled.json()['status'] == 'cancelled'
    assert poll(api, auth_id).status_code == 410

    clock.advance(11)
    auth_id = start(api)['auth_id']
    assert api.delete('/api/v1/platforms/bilibili/credential').status_code == 200
    assert poll(api, auth_id).status_code == 410


def test_qr_start_is_cooldown_limited(api, clock):
    use(FakeBilibiliCollector())
    auth_id = start(api)['auth_id']
    api.post('/api/v1/platforms/bilibili/qr/' + auth_id + '/cancel')
    blocked = api.post('/api/v1/platforms/bilibili/qr')
    assert blocked.status_code == 429
    assert blocked.json()['error']['code'] == 'QR_RATE_LIMITED'
    assert blocked.json()['error']['details']['retry_after_seconds'] >= 1
    clock.advance(11)
    assert api.post('/api/v1/platforms/bilibili/qr').status_code == 201


def test_qr_expiry_is_reported_then_session_disappears(api, clock):
    use(FakeBilibiliCollector())
    auth_id = start(api)['auth_id']
    clock.advance(181)
    expired = poll(api, auth_id)
    assert expired.status_code == 200
    assert expired.json()['status'] == 'expired'
    clock.advance(40)
    assert poll(api, auth_id).status_code == 410


def test_qr_upstream_errors_stay_visible(api, clock):
    use(FakeBilibiliCollector(qr='risk'))
    auth_id = start(api)['auth_id']
    body = poll(api, auth_id).json()
    assert body['status'] == 'error'
    assert body['error_code'] == 'RISK_CONTROL'
    assert body['retryable'] is False

    clock.advance(11)
    use(FakeBilibiliCollector(qr='no_credential'))
    auth_id = start(api)['auth_id']
    body = poll(api, auth_id).json()
    assert body['status'] == 'error'
    assert body['error_code'] == 'QR_NO_CREDENTIAL'


def test_qr_failure_on_start_leaves_no_session(api, clock):
    use(FakeBilibiliCollector(fail_at='start_qr_login', fail_code='RISK_CONTROL'))
    failed = api.post('/api/v1/platforms/bilibili/qr')
    assert failed.status_code == 422
    assert failed.json()['error']['code'] == 'RISK_CONTROL'
    clock.advance(11)
    use(FakeBilibiliCollector())
    assert api.post('/api/v1/platforms/bilibili/qr').status_code == 201


def test_qr_capability_is_advertised_but_unverified(api, clock):
    body = api.get('/api/v1/capabilities').json()
    assert body['collectors']['bilibili']['qr_login'] is True
    assert body['live_qr_login_verified'] is False
    platforms = api.get('/api/v1/platforms').json()
    bilibili = next(item for item in platforms['items'] if item['platform'] == 'bilibili')
    assert bilibili['collector']['qr_login'] is True
