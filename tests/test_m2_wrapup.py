"""M2 wrap-up integration: part batches, credential/risk recovery, digest, retention.

All collectors are deterministic doubles; no test performs a real Bilibili request.
"""
from datetime import datetime, timezone

from sqlalchemy import text

import app.jobs.worker as worker
import app.main as main
from app.collectors import register
from app.collectors.fake import FakeBilibiliCollector
from app.storage.db import one

BVID = 'BV1xx411c7mD'
URL = f'https://www.bilibili.com/video/{BVID}'


def use(collector):
    register('bilibili', lambda: collector)
    return collector


def drain(limit=20):
    for _ in range(limit):
        if not worker.tick():
            return
    raise AssertionError('queue did not drain')


def count(engine, table, where=''):
    with engine.connect() as conn:
        return one(conn, f'SELECT count(*) n FROM {table} {where}')['n']


def import_video(api, key):
    response = api.post('/api/v1/sources/import-url', headers={'Idempotency-Key': key},
                        json={'url': URL, 'rights_confirmed': True})
    assert response.status_code == 202, response.text
    return response.json()


def create_discovery(api, **overrides):
    payload = {'name': 'AI 观察', 'keywords': ['AI'], 'languages': ['zh-CN'],
               'network_authorized': True, 'enabled': False}
    payload.update(overrides)
    response = api.post('/api/v1/discovery-sources', json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def run_discovery(api, source_id):
    response = api.post(f'/api/v1/discovery-sources/{source_id}/run')
    assert response.status_code == 202, response.text
    run_id = response.json()['run_id']
    for _ in range(20):
        body = api.get('/api/v1/discovery-runs/' + run_id).json()
        if body['state'] in ('succeeded', 'failed', 'blocked_upstream', 'cancelled'):
            return body
        assert worker.tick() is True
    raise AssertionError('discovery run did not finish')


def test_part_batch_requires_confirmation_and_is_all_or_nothing(api):
    use(FakeBilibiliCollector(page_count=3))
    imported = import_video(api, 'm2-wrap-batch-0001')
    drain()
    parent = api.get('/api/v1/sources/' + imported['source_ids'][0]).json()
    assert [part['page_no'] for part in parent['metadata']['parts']] == [1, 2, 3]
    sources_before = count(api.engine, 'sources')
    jobs_before = count(api.engine, "jobs WHERE kind='collect_source'")

    unconfirmed = api.post(f"/api/v1/sources/{parent['id']}/parts/import",
                           json={'page_nos': [2, 3]})
    assert unconfirmed.status_code == 422
    assert unconfirmed.json()['error']['code'] == 'PART_BATCH_CONFIRM_REQUIRED'
    assert count(api.engine, 'sources') == sources_before
    assert count(api.engine, "jobs WHERE kind='collect_source'") == jobs_before

    missing = api.post(f"/api/v1/sources/{parent['id']}/parts/import",
                       json={'page_nos': [2, 9], 'confirmed': True})
    assert missing.status_code == 404
    assert missing.json()['error']['code'] == 'SOURCE_PART_NOT_FOUND'
    assert missing.json()['error']['details']['missing_pages'] == [9]
    assert count(api.engine, 'sources') == sources_before
    assert count(api.engine, "jobs WHERE kind='collect_source'") == jobs_before

    too_many = api.post(f"/api/v1/sources/{parent['id']}/parts/import",
                        json={'page_nos': list(range(1, 22)), 'confirmed': True})
    assert too_many.status_code == 422
    assert too_many.json()['error']['code'] == 'PART_BATCH_TOO_LARGE'

    queued = api.post(f"/api/v1/sources/{parent['id']}/parts/import",
                      json={'page_nos': [3, 2, 3], 'confirmed': True})
    assert queued.status_code == 202, queued.text
    body = queued.json()
    assert body['queued'] == 2
    assert [item['page'] for item in body['items']] == [2, 3]
    assert body['estimated_upstream_requests'] == 6
    assert all(item['created'] is True for item in body['items'])
    assert count(api.engine, 'sources') == sources_before + 2
    assert count(api.engine, "jobs WHERE kind='collect_source'") == jobs_before + 2

    drain()
    for item in body['items']:
        detail = api.get('/api/v1/sources/' + item['source_id']).json()
        assert detail['content_status'] == 'ready'


def test_expired_credential_marks_connection_and_recovery_requeues(api):
    use(FakeBilibiliCollector(fail_at='fetch_source', fail_code='CREDENTIAL_EXPIRED'))
    saved = api.put('/api/v1/platforms/bilibili/credential',
                    json={'cookie': 'SESSDATA=expiry-fake-value'})
    assert saved.status_code == 200
    imported = import_video(api, 'm2-wrap-expired-0001')
    assert worker.tick() is True

    job = api.get('/api/v1/jobs/' + imported['job_id']).json()
    assert job['state'] == 'failed' and job['error_code'] == 'CREDENTIAL_EXPIRED'
    platforms = api.get('/api/v1/platforms').json()
    bili = next(item for item in platforms['items'] if item['platform'] == 'bilibili')
    assert bili['connection']['status'] == 'expired'
    assert bili['connection']['last_error_code'] == 'CREDENTIAL_EXPIRED'
    assert bili['recovery']['failed_jobs'] == 1
    assert bili['recovery']['recoverable'] is False

    blocked = api.post('/api/v1/platforms/bilibili/recover')
    assert blocked.status_code == 409
    assert blocked.json()['error']['code'] == 'CREDENTIAL_EXPIRED'
    assert count(api.engine, "jobs WHERE state='queued'") == 0

    replaced = api.put('/api/v1/platforms/bilibili/credential',
                       json={'cookie': 'SESSDATA=replacement-fake-value'})
    assert replaced.status_code == 200
    platforms = api.get('/api/v1/platforms').json()
    bili = next(item for item in platforms['items'] if item['platform'] == 'bilibili')
    assert bili['recovery']['recoverable'] is True

    recovered = api.post('/api/v1/platforms/bilibili/recover')
    assert recovered.status_code == 202, recovered.text
    assert recovered.json()['requeued'] == 1
    requeued = api.get('/api/v1/jobs/' + imported['job_id']).json()
    assert requeued['state'] == 'queued' and requeued['attempts'] == 0
    assert requeued['checkpoint'] == {}

    use(FakeBilibiliCollector())
    assert worker.tick() is True
    job = api.get('/api/v1/jobs/' + imported['job_id']).json()
    assert job['state'] == 'succeeded'
    source = api.get('/api/v1/sources/' + imported['source_ids'][0]).json()
    assert source['content_status'] == 'ready'
    with api.engine.connect() as conn:
        connection = one(conn, "SELECT * FROM platform_connections WHERE platform='bilibili'")
    assert connection['last_success_at'] is not None
    assert connection['cooldown_until'] is None


def test_risk_control_cooldown_blocks_recovery_until_it_expires(api):
    use(FakeBilibiliCollector(fail_at='fetch_source', fail_code='RISK_CONTROL'))
    assert api.put('/api/v1/platforms/bilibili/credential',
                   json={'cookie': 'SESSDATA=risk-fake-value'}).status_code == 200
    imported = import_video(api, 'm2-wrap-risk-0001')
    assert worker.tick() is True

    platforms = api.get('/api/v1/platforms').json()
    bili = next(item for item in platforms['items'] if item['platform'] == 'bilibili')
    assert bili['connection']['status'] == 'risk_control'
    assert bili['connection']['cooldown_until'] is not None
    assert bili['recovery']['recoverable'] is False
    assert bili['recovery']['retry_after_seconds'] > 0

    early = api.post('/api/v1/platforms/bilibili/recover')
    assert early.status_code == 429
    assert early.json()['error']['code'] == 'RISK_COOLDOWN'
    assert early.json()['error']['details']['retry_after_seconds'] > 0
    assert api.get('/api/v1/jobs/' + imported['job_id']).json()['state'] == 'failed'

    with api.engine.begin() as conn:
        conn.execute(text("UPDATE platform_connections SET cooldown_until=now()-interval '1 minute'"))
    platforms = api.get('/api/v1/platforms').json()
    bili = next(item for item in platforms['items'] if item['platform'] == 'bilibili')
    assert bili['recovery']['recoverable'] is True
    recovered = api.post('/api/v1/platforms/bilibili/recover')
    assert recovered.status_code == 202
    assert recovered.json()['requeued'] == 1
    assert api.get('/api/v1/jobs/' + imported['job_id']).json()['state'] == 'queued'


def test_digest_includes_discovery_runs_and_collected_sources(api):
    use(FakeBilibiliCollector(discovery_items=[{
        'bvid': 'BV1aa0000101', 'title': 'AI 日报联动观察',
        'author_name': '虚构UP主',
        'published_at': datetime(2026, 1, 5, tzinfo=timezone.utc)}]))
    source = create_discovery(api, enabled=True)
    run = run_discovery(api, source['id'])
    assert run['state'] == 'succeeded' and run['enqueued'] == 1
    drain()

    digest = api.get('/api/v1/digests/today').json()
    discovery = digest['discovery']
    assert discovery['totals']['runs'] >= 1
    assert discovery['totals']['enqueued'] >= 1
    assert any(row['source_name'] == source['name'] for row in discovery['runs'])
    assert any(item['external_id'] == 'BV1aa0000101:p1' for item in discovery['decisions'])
    # The test double replaces the metadata title with its synthetic marker; the
    # report must still list the collected source truthfully, never as real B站 data.
    assert discovery['collected_sources']
    assert any(row['content_status'] == 'ready' for row in discovery['collected_sources'])
    assert all('合成示例' in row['title'] for row in discovery['collected_sources'])
    assert any(item['title'].startswith('AI 日报联动观察') for item in discovery['decisions'])
    assert '关键词发现（本日）' in digest['markdown']
    assert '关键词来源采集（本日）' in digest['markdown']
    assert '入队只表示已排队采集' in digest['markdown']

    download = api.get('/api/v1/digests/today?download=true')
    assert download.status_code == 200
    assert 'text/markdown' in download.headers['content-type']
    assert '关键词发现（本日）' in download.text


def test_discovery_item_retention_keeps_run_ledger(api):
    use(FakeBilibiliCollector(discovery_items=[{
        'bvid': 'BV1aa0000102', 'title': 'AI 保留策略观察',
        'published_at': datetime(2026, 1, 4, tzinfo=timezone.utc)}]))
    source = create_discovery(api, enabled=True)
    run = run_discovery(api, source['id'])
    assert count(api.engine, 'discovery_run_items') == 1

    # Fresh rows survive.
    assert worker.cleanup_discovery_items(days=30) == 0
    assert count(api.engine, 'discovery_run_items') == 1

    with api.engine.begin() as conn:
        conn.execute(text("UPDATE discovery_runs SET finished_at=now()-interval '40 days' WHERE id=:id"),
                     {'id': run['id']})
    assert worker.cleanup_discovery_items(days=30) == 1
    assert count(api.engine, 'discovery_run_items') == 0
    # The run ledger and source watermark stay; only the detailed title rows expire.
    assert count(api.engine, 'discovery_runs') == 1
    detail = api.get('/api/v1/discovery-sources/' + source['id']).json()
    assert detail['watermark']['seen_count'] == 1
    assert detail['watermark']['last_run_id'] == run['id']
