"""M2 Phase 1: Bilibili single-link collection against an isolated schema.

All collectors are doubles; no test performs a real Bilibili request.
"""
import pytest
from sqlalchemy import text

import app.jobs.worker as worker
from app.collectors import register
from app.collectors.fake import FakeBilibiliCollector
from app.storage.db import one

BVID = 'BV1xx411c7mD'
URL = f'https://www.bilibili.com/video/{BVID}'
HEADERS = {'Idempotency-Key': 'bilibili-import-key-0001'}


def use(collector):
    register('bilibili', lambda: collector)
    return collector


def collect_job(engine):
    with engine.connect() as conn:
        return one(conn, "SELECT * FROM jobs WHERE kind='collect_source' ORDER BY created_at DESC LIMIT 1")


def test_url_import_validation(api):
    response = api.post('/api/v1/sources/import-url', headers=HEADERS,
                        json={'url': URL, 'rights_confirmed': False})
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'RIGHTS_CONFIRMATION_REQUIRED'

    response = api.post('/api/v1/sources/import-url', headers={'Idempotency-Key': 'bilibili-bad-0002'},
                        json={'url': 'https://b23.tv/abc', 'rights_confirmed': True})
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'UNSUPPORTED_LOCATOR'

    response = api.post('/api/v1/sources/import-url',
                        json={'url': URL, 'rights_confirmed': True})
    assert response.status_code == 400
    assert response.json()['error']['code'] == 'IDEMPOTENCY_KEY_REQUIRED'


def test_import_creates_pending_source_without_network(api):
    collector = use(FakeBilibiliCollector())
    response = api.post('/api/v1/sources/import-url', headers=HEADERS,
                        json={'url': URL, 'rights_confirmed': True})
    assert response.status_code == 202, response.text
    body = response.json()
    assert body['collection'] == 'queued' and body['existed'] is False
    source = api.get('/api/v1/sources').json()['items'][0]
    assert source['content_status'] == 'pending'
    assert source['availability'] == 'unknown'
    assert source['platform'] == 'bilibili'
    assert source['external_id'] == f'{BVID}:p1'
    assert source['metadata']['subtitle_status'] == 'unknown'
    # Import itself must not contact the platform; only the worker does.
    assert collector.calls == []
    assert collect_job(api.engine)['state'] == 'queued'


def test_collect_stores_caption_and_never_claims_ai_analysis(api):
    use(FakeBilibiliCollector())
    body = api.post('/api/v1/sources/import-url', headers=HEADERS,
                    json={'url': URL, 'rights_confirmed': True}).json()
    source_id = body['source_ids'][0]
    assert worker.tick() is True

    job = api.get('/api/v1/jobs/' + body['job_id']).json()
    assert job['state'] == 'succeeded'
    assert job['result']['subtitle_status'] == 'available'
    assert job['result']['ai_analyzed'] is False

    detail = api.get('/api/v1/sources/' + source_id).json()
    assert detail['content_status'] == 'ready'
    assert detail['availability'] == 'available'
    assert detail['metadata']['subtitle_status'] == 'available'
    assert detail['metadata']['collector'] == 'bilibili.fake.v1'
    assert detail['title'] == '【合成示例】测试替身返回，非真实B站内容：单链接采集'
    assert detail['last_checked_at'] is not None

    with api.engine.connect() as conn:
        version = one(conn, 'SELECT * FROM source_versions')
        assert version['content_origin'] == 'platform_caption'
        assert version['extractor_version'] == 'bilibili.subtitle.v1'
        assert version['coverage']['source'] == 'platform_caption'
        assert version['segments'][0]['segment_id'] == 'seg_1'
        assert one(conn, "SELECT count(*) n FROM claims")['n'] == 0
        assert one(conn, "SELECT count(*) n FROM updates")['n'] == 0

    # The follow-up extraction job must stop at "needs_review" for real material.
    assert worker.tick() is True
    with api.engine.connect() as conn:
        follow = one(conn, "SELECT * FROM jobs WHERE kind='process_source'")
        assert follow['state'] == 'needs_review'
        assert one(conn, "SELECT count(*) n FROM claims")['n'] == 0
    assert api.get('/api/v1/updates').json()['items'] == []


def test_missing_subtitles_are_marked_not_invented(api):
    use(FakeBilibiliCollector(subtitles='missing'))
    body = api.post('/api/v1/sources/import-url', headers=HEADERS,
                    json={'url': URL, 'rights_confirmed': True}).json()
    assert worker.tick() is True
    job = api.get('/api/v1/jobs/' + body['job_id']).json()
    assert job['state'] == 'succeeded'
    assert job['result']['missing_subtitles'] is True
    source = api.get('/api/v1/sources/' + body['source_ids'][0]).json()
    assert source['content_status'] == 'missing'
    assert source['metadata']['subtitle_status'] == 'missing'
    with api.engine.connect() as conn:
        assert one(conn, 'SELECT count(*) n FROM source_versions')['n'] == 0
        assert one(conn, "SELECT count(*) n FROM jobs WHERE kind='process_source'")['n'] == 0


def test_login_required_blocks_credentials_then_recovers(api):
    use(FakeBilibiliCollector(subtitles='credential_gated'))
    body = api.post('/api/v1/sources/import-url', headers=HEADERS,
                    json={'url': URL, 'rights_confirmed': True}).json()
    assert worker.tick() is True
    job = api.get('/api/v1/jobs/' + body['job_id']).json()
    assert job['state'] == 'blocked_credentials'
    assert job['error_code'] == 'CREDENTIAL_REQUIRED'
    source = api.get('/api/v1/sources/' + body['source_ids'][0]).json()
    assert source['content_status'] == 'permission_required'
    assert source['metadata']['subtitle_status'] == 'login_required'

    saved = api.put('/api/v1/platforms/bilibili/credential',
                    json={'cookie': 'SESSDATA=fake-cookie-value', 'alias': '本机测试'})
    assert saved.status_code == 200

    refreshed = api.post('/api/v1/sources/' + body['source_ids'][0] + '/refresh',
                         headers={'If-Match': str(source['revision'])})
    assert refreshed.status_code == 202, refreshed.text
    assert worker.tick() is True
    source = api.get('/api/v1/sources/' + body['source_ids'][0]).json()
    assert source['content_status'] == 'ready'
    assert source['metadata']['subtitle_status'] == 'available'


def test_collector_failure_is_explicit_and_creates_no_content(api):
    use(FakeBilibiliCollector(fail_at='fetch_source', fail_code='RISK_CONTROL'))
    body = api.post('/api/v1/sources/import-url', headers=HEADERS,
                    json={'url': URL, 'rights_confirmed': True}).json()
    assert worker.tick() is True
    job = api.get('/api/v1/jobs/' + body['job_id']).json()
    assert job['state'] == 'failed'
    assert job['error_code'] == 'RISK_CONTROL'
    assert job['result']['retryable'] is False
    source = api.get('/api/v1/sources/' + body['source_ids'][0]).json()
    assert source['content_status'] == 'failed'
    assert source['metadata']['last_error_code'] == 'RISK_CONTROL'
    with api.engine.connect() as conn:
        assert one(conn, 'SELECT count(*) n FROM source_versions')['n'] == 0
        assert one(conn, 'SELECT count(*) n FROM claims')['n'] == 0


def test_stale_fence_during_network_stage_prevents_writes(api):
    holder = {}

    def steal_fence(stage):
        if stage != 'fetch_source' or holder.get('done'):
            return
        holder['done'] = True
        with api.engine.begin() as conn:
            row = one(conn, "SELECT id FROM jobs WHERE kind='collect_source'")
            conn.execute(text("UPDATE jobs SET fence=fence+1, lease_until=now()+interval '5 minutes' WHERE id=:id"),
                         {'id': row['id']})

    use(FakeBilibiliCollector(on_fetch=steal_fence))
    body = api.post('/api/v1/sources/import-url', headers=HEADERS,
                    json={'url': URL, 'rights_confirmed': True}).json()
    assert worker.tick() is True
    with api.engine.connect() as conn:
        job = one(conn, 'SELECT * FROM jobs WHERE id=:id', id=body['job_id'])
        assert job['fence'] == 2 and job['state'] == 'running'
        assert one(conn, 'SELECT count(*) n FROM source_versions')['n'] == 0
        source = one(conn, 'SELECT * FROM sources WHERE id=:id', id=body['source_ids'][0])
        assert source['content_status'] == 'pending'
        assert source['metadata']['subtitle_status'] == 'unknown'


def test_import_is_idempotent_and_rejects_key_reuse(api):
    use(FakeBilibiliCollector())
    for _ in range(4):
        response = api.post('/api/v1/sources/import-url', headers=HEADERS,
                            json={'url': URL, 'rights_confirmed': True})
        assert response.status_code == 202, response.text
    with api.engine.connect() as conn:
        assert one(conn, 'SELECT count(*) n FROM sources')['n'] == 1
        assert one(conn, "SELECT count(*) n FROM jobs WHERE kind='collect_source'")['n'] == 1
    conflict = api.post('/api/v1/sources/import-url', headers=HEADERS,
                        json={'url': f'{URL}?p=2', 'rights_confirmed': True})
    assert conflict.status_code == 409
    assert conflict.json()['error']['code'] == 'IDEMPOTENCY_KEY_CONFLICT'


def test_credentials_are_sealed_and_not_echoed(api):
    response = api.put('/api/v1/platforms/bilibili/credential',
                       json={'cookie': 'SESSDATA=secret-value-123456; bili_jct=ignored'})
    assert response.status_code == 200, response.text
    assert 'secret-value-123456' not in response.text
    assert response.json()['status'] == 'unverified'
    with api.engine.connect() as conn:
        row = one(conn, 'SELECT * FROM platform_connections')
        assert 'secret-value-123456' not in (row['credential_sealed'] or '')
        assert row['status'] == 'unverified'
        assert one(conn, "SELECT count(*) n FROM audit_events WHERE action='platform_credential_set'")['n'] == 1

    platforms = api.get('/api/v1/platforms').json()
    bilibili = next(item for item in platforms['items'] if item['platform'] == 'bilibili')
    assert bilibili['connection']['credential_configured'] is True
    assert platforms['live_collection_verified'] is False

    removed = api.delete('/api/v1/platforms/bilibili/credential')
    assert removed.status_code == 200
    with api.engine.connect() as conn:
        row = one(conn, 'SELECT * FROM platform_connections')
        assert row['credential_sealed'] is None
        assert row['status'] == 'unconfigured'


def test_check_without_credential_makes_no_network_call(api):
    collector = use(FakeBilibiliCollector())
    response = api.post('/api/v1/platforms/bilibili/check')
    assert response.status_code == 200
    body = response.json()
    assert body['status'] == 'not_configured' and body['live_check'] is False
    assert collector.calls == []
    assert worker.tick() is False


def test_check_with_credential_is_live_and_rate_limited(api):
    collector = use(FakeBilibiliCollector(account='connected'))
    assert api.put('/api/v1/platforms/bilibili/credential',
                   json={'cookie': 'SESSDATA=another-cookie-value'}).status_code == 200
    response = api.post('/api/v1/platforms/bilibili/check')
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['status'] == 'connected' and body['live_check'] is True
    assert body['verification'] == 'live'
    assert collector.calls == ['check_account']
    with api.engine.connect() as conn:
        row = one(conn, 'SELECT * FROM platform_connections')
        assert row['status'] == 'connected' and row['last_checked_at'] is not None
    second = api.post('/api/v1/platforms/bilibili/check')
    assert second.status_code == 429
    assert second.json()['error']['code'] == 'RATE_LIMITED'


def test_refresh_requires_if_match(api):
    use(FakeBilibiliCollector())
    body = api.post('/api/v1/sources/import-url', headers=HEADERS,
                    json={'url': URL, 'rights_confirmed': True}).json()
    assert worker.tick() is True
    source = api.get('/api/v1/sources/' + body['source_ids'][0]).json()
    missing = api.post('/api/v1/sources/' + body['source_ids'][0] + '/refresh')
    assert missing.status_code == 428
    ok = api.post('/api/v1/sources/' + body['source_ids'][0] + '/refresh',
                  headers={'If-Match': str(source['revision'])})
    assert ok.status_code == 202
    assert ok.json()['state'] == 'queued'


def test_capabilities_reports_unverified_collection(api):
    body = api.get('/api/v1/capabilities').json()
    assert body['live_collection_verified'] is False
    assert body['live_keyword_discovery_verified'] is False
    assert body['live_qr_login_verified'] is False
    assert '未验证' in body['keyword_discovery']
    assert body['collectors']['schema'] == 'collector.v1'
    assert '未执行验证' in body['platforms']['bilibili']


def test_manual_import_still_works_alongside_bilibili(api):
    response = api.post('/api/v1/sources/import',
                        headers={'Idempotency-Key': 'manual-still-works'},
                        json={'title': '手工材料', 'content': '这是一份用户自有材料。', 'rights_confirmed': True})
    assert response.status_code == 202
    assert worker.tick() is True
    with api.engine.connect() as conn:
        assert one(conn, 'SELECT count(*) n FROM sources')['n'] == 1
        assert one(conn, "SELECT count(*) n FROM jobs WHERE kind='process_source'")['n'] == 1
