"""M2 Phase 2 integration: keyword sources, scheduler, watermark, page targeting.

Every collector is a double; no test performs a real Bilibili request.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

import app.jobs.scheduler as scheduler
import app.jobs.worker as worker
import app.main as main
from app.collectors import register
from app.collectors.base import Page, RemoteSource
from app.collectors.fake import FakeBilibiliCollector
from app.storage.db import one

KEYWORDS = ['AI']
BVID = 'BV1xx411c7mD'
URL = f'https://www.bilibili.com/video/{BVID}'
BVID_A, BVID_B, BVID_C = 'BV1aa0000001', 'BV1aa0000002', 'BV1aa0000003'


def use(collector):
    register('bilibili', lambda: collector)
    return collector


def items(*specs):
    """Build fake discovery items: (bvid, title, day)."""
    result = []
    for bvid, title, day in specs:
        result.append({'bvid': bvid, 'title': title, 'author_name': '虚构UP主',
                       'published_at': datetime(2026, 1, day, tzinfo=timezone.utc)})
    return result


def create(api, **overrides):
    payload = {'name': 'AI 观察', 'keywords': KEYWORDS, 'languages': ['zh-CN'],
               'network_authorized': True, 'enabled': False}
    payload.update(overrides)
    response = api.post('/api/v1/discovery-sources', json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def request_run(api, source_id):
    return api.post(f'/api/v1/discovery-sources/{source_id}/run')


def run_discovery(api, source_id, limit=20):
    """Queue a manual run and tick until that run reaches a terminal state."""
    response = request_run(api, source_id)
    assert response.status_code == 202, response.text
    run_id = response.json()['run_id']
    for _ in range(limit):
        body = api.get('/api/v1/discovery-runs/' + run_id).json()
        if body['state'] in ('succeeded', 'failed', 'blocked_upstream', 'cancelled'):
            return body
        assert worker.tick() is True
    raise AssertionError('discovery run did not finish')


def drain(api, limit=12):
    """Run every queued job (collection plus the follow-up Mock extraction)."""
    for _ in range(limit):
        if not worker.tick():
            return
    raise AssertionError('queue did not drain')


def count(engine, table, where=''):
    with engine.connect() as conn:
        return one(conn, f'SELECT count(*) n FROM {table} {where}')['n']


def decisions(api, run_id):
    body = api.get('/api/v1/discovery-runs/' + run_id).json()
    return {row['external_id']: row['decision'] for row in body['items']}


def test_create_validates_and_lists(api):
    bad = api.post('/api/v1/discovery-sources', json={'name': '无关键词'})
    assert bad.status_code == 422
    assert bad.json()['error']['code'] == 'DISCOVERY_CONFIG_INVALID'
    assert bad.json()['error']['details']['field'] == 'keywords'

    too_many = api.post('/api/v1/discovery-sources',
                        json={'name': '太多', 'keywords': ['a', 'b', 'c', 'd', 'e', 'f']})
    assert too_many.status_code == 422

    source = create(api)
    assert source['enabled'] is False and source['network_authorized'] is True
    assert source['live_discovery_verified'] is False
    assert source['watermark']['seen_count'] == 0
    duplicate = api.post('/api/v1/discovery-sources',
                         json={'name': source['name'], 'keywords': ['AI']})
    assert duplicate.status_code == 409
    assert duplicate.json()['error']['code'] == 'DISCOVERY_NAME_CONFLICT'

    listing = api.get('/api/v1/discovery-sources').json()
    assert listing['capability']['real_discovery_verified'] is False
    assert listing['items'][0]['id'] == source['id']
    assert listing['items'][0]['run_count'] == 0


def test_manual_run_requires_network_authorization(api):
    collector = use(FakeBilibiliCollector())
    source = create(api, enabled=True, network_authorized=False)
    response = request_run(api, source['id'])
    assert response.status_code == 422
    assert response.json()['error']['code'] == 'NETWORK_NOT_AUTHORIZED'
    assert count(api.engine, 'jobs') == 0
    assert count(api.engine, 'discovery_runs') == 0
    assert collector.calls == []


def test_discovery_enqueues_sources_and_jobs(api):
    collector = use(FakeBilibiliCollector(discovery_items=items(
        (BVID_A, 'AI 一月观察', 3), (BVID_B, 'AI 二月观察', 2), (BVID_C, 'AI 三月观察', 1))))
    source = create(api, enabled=True)
    run = run_discovery(api, source['id'])

    assert run['state'] == 'succeeded'
    assert run['discovered'] == 3 and run['enqueued'] == 3 and run['excluded'] == 0
    assert run['requests_used'] == 1 and run['pages_visited'] == 1
    assert decisions(api, run['id']) == {
        f'{BVID_A}:p1': 'enqueued', f'{BVID_B}:p1': 'enqueued', f'{BVID_C}:p1': 'enqueued'}
    assert 'discover:1' in collector.calls
    assert 'fetch_source' not in collector.calls

    assert count(api.engine, 'sources') == 3
    assert count(api.engine, "jobs WHERE kind='collect_source'") == 3
    assert count(api.engine, 'discovery_run_items') == 3

    detail = api.get('/api/v1/discovery-sources/' + source['id']).json()
    assert detail['watermark']['seen_count'] == 3
    assert detail['watermark']['newest_published_at'].startswith('2026-01-03')
    assert detail['next_run_at'] is not None
    assert detail['last_error_code'] is None

    source_row = api.get('/api/v1/sources').json()['items'][0]
    assert source_row['metadata']['discovery_source_id'] == source['id']
    assert source_row['metadata']['synthetic'] is True
    assert source_row['metadata']['bvid'].startswith('BV1')


def test_second_run_is_incremental_and_adds_nothing(api, monkeypatch):
    monkeypatch.setattr(main, 'MANUAL_RUN_COOLDOWN_SECONDS', 0)
    collector = use(FakeBilibiliCollector(discovery_items=items(
        (BVID_A, 'AI 一月观察', 3), (BVID_B, 'AI 二月观察', 2))))
    source = create(api, enabled=True)
    first = run_discovery(api, source['id'])
    assert first['enqueued'] == 2

    second = run_discovery(api, source['id'])
    assert second['state'] == 'succeeded'
    assert second['enqueued'] == 0 and second['duplicates'] == 2
    assert decisions(api, second['id']) == {
        f'{BVID_A}:p1': 'duplicate', f'{BVID_B}:p1': 'duplicate'}
    assert count(api.engine, 'sources') == 2
    assert count(api.engine, "jobs WHERE kind='collect_source'") == 2
    assert collector.calls.count('discover:1') == 2


def test_stale_source_is_refreshed_not_duplicated(api, monkeypatch):
    monkeypatch.setattr(main, 'MANUAL_RUN_COOLDOWN_SECONDS', 0)
    use(FakeBilibiliCollector(discovery_items=items((BVID_A, 'AI 观察', 3))))
    source = create(api, enabled=True)
    first = run_discovery(api, source['id'])
    assert first['enqueued'] == 1
    drain(api)  # collect the source and finish the follow-up Mock extraction job
    assert count(api.engine, "jobs WHERE kind='collect_source' AND state='succeeded'") == 1

    with api.engine.begin() as conn:
        conn.execute(text("UPDATE sources SET last_checked_at=now()-interval '30 days'"))
    second = run_discovery(api, source['id'])
    assert decisions(api, second['id']) == {f'{BVID_A}:p1': 'refreshed'}
    assert second['enqueued'] == 1
    assert count(api.engine, "jobs WHERE kind='collect_source'") == 2


def test_exclusion_limit_and_keyword_miss_are_recorded(api):
    use(FakeBilibiliCollector(discovery_items=items(
        (BVID_A, 'AI 新闻速览', 5), (BVID_B, 'AI 速成广告', 4),
        (BVID_C, '与关键词无关', 3), ('BV1aa0000004', 'AI 旧闻回顾', 2))))
    source = create(api, exclude_terms=['广告'], max_results_per_run=1)
    run = run_discovery(api, source['id'])
    assert decisions(api, run['id']) == {
        f'{BVID_A}:p1': 'enqueued',
        f'{BVID_B}:p1': 'excluded_term',
        f'{BVID_C}:p1': 'keyword_miss',
        'BV1aa0000004:p1': 'over_limit',
    }
    assert run['discovered'] == 4 and run['matched'] == 2
    assert run['excluded'] == 2 and run['enqueued'] == 1
    assert count(api.engine, 'sources') == 1


def test_explicit_page_policy_targets_one_part(api):
    collector = use(FakeBilibiliCollector(
        discovery_items=items((BVID_A, 'AI 多P 视频', 3)), page_count=2))
    source = create(api, page_policy='explicit', target_pages=[2])
    run = run_discovery(api, source['id'])
    assert decisions(api, run['id']) == {f'{BVID_A}:p2': 'enqueued'}
    source_row = api.get('/api/v1/sources').json()['items'][0]
    assert source_row['external_id'] == f'{BVID_A}:p2'
    assert source_row['metadata']['page'] == 2

    with api.engine.connect() as conn:
        job = one(conn, "SELECT * FROM jobs WHERE kind='collect_source'")
    assert job['input']['page'] == 2
    assert job['input']['languages'] == ['zh-CN']
    drain(api)
    assert api.get('/api/v1/sources/' + str(source_row['id'])).json()['content_status'] == 'ready'
    assert collector.calls.count('fetch_source') == 1


def test_language_requirement_marks_mismatch_without_version(api):
    use(FakeBilibiliCollector(discovery_items=items((BVID_A, 'AI 观察', 3)),
                              subtitle_languages=('en-US',)))
    source = create(api, language_policy='require', languages=['zh-CN'])
    run_discovery(api, source['id'])
    assert worker.tick() is True
    row = api.get('/api/v1/sources').json()['items'][0]
    detail = api.get('/api/v1/sources/' + row['id']).json()
    assert detail['content_status'] == 'missing'
    assert detail['metadata']['subtitle_status'] == 'language_mismatch'
    assert count(api.engine, 'source_versions') == 0


def test_parts_endpoint_locates_one_part(api):
    use(FakeBilibiliCollector(page_count=3))
    imported = api.post('/api/v1/sources/import-url',
                        headers={'Idempotency-Key': 'parts-import-0001'},
                        json={'url': URL, 'rights_confirmed': True}).json()
    drain(api)
    parent = api.get('/api/v1/sources/' + imported['source_ids'][0]).json()
    assert [part['page_no'] for part in parent['metadata']['parts']] == [1, 2, 3]

    missing = api.post(f"/api/v1/sources/{parent['id']}/parts/9/import")
    assert missing.status_code == 404
    assert missing.json()['error']['code'] == 'SOURCE_PART_NOT_FOUND'

    target = api.post(f"/api/v1/sources/{parent['id']}/parts/3/import")
    assert target.status_code == 202, target.text
    body = target.json()
    assert body['created'] is True and body['page'] == 3
    child = api.get('/api/v1/sources/' + body['source_id']).json()
    assert child['external_id'] == f'{BVID}:p3'
    assert child['metadata']['page'] == 3
    drain(api)
    assert api.get('/api/v1/sources/' + body['source_id']).json()['content_status'] == 'ready'


def test_collect_resumes_from_checkpoint_without_metadata_request(api):
    collector = use(FakeBilibiliCollector())
    imported = api.post('/api/v1/sources/import-url',
                        headers={'Idempotency-Key': 'resume-collect-0001'},
                        json={'url': URL, 'rights_confirmed': True}).json()
    checkpoint = RemoteSource(
        platform='bilibili', external_id=f'{BVID}:p1', canonical_url=URL,
        title='恢复用的元数据', author_name='虚构UP主',
        pages=(Page(page_no=1, cid='cid-resume', part_title='第一段', duration_seconds=99),),
        metadata={'schema_version': 'source.v1', 'collector': 'bilibili.v1',
                  'bvid': BVID, 'page': 1}).to_checkpoint()
    with api.engine.begin() as conn:
        conn.execute(text('UPDATE jobs SET checkpoint=CAST(:c AS jsonb) WHERE id=:id'),
                     {'c': main.js({'remote': checkpoint, 'stage': 'fetch_subtitles'}),
                      'id': imported['job_id']})
    assert worker.tick() is True
    assert collector.calls == ['fetch_subtitles', 'fetch_pinned_comments']
    job = api.get('/api/v1/jobs/' + imported['job_id']).json()
    assert job['state'] == 'succeeded'
    assert job['result']['resumed'] is True
    assert job['result']['subtitle_status'] == 'available'


def test_discovery_resumes_from_checkpoint_without_refetching_page_one(api):
    from app.collectors.base import DiscoveryItem

    collector = use(FakeBilibiliCollector(discover_pages=2, discovery_items=items(
        (BVID_A, 'AI 观察', 3))))
    source = create(api, enabled=True)
    first_page = [DiscoveryItem(platform='bilibili', external_id=BVID_A, video_id=BVID_A,
                                title='AI 观察',
                                published_at=datetime(2026, 1, 3, tzinfo=timezone.utc)
                                ).to_checkpoint()]
    with api.engine.begin() as conn:
        workspace = one(conn, 'SELECT workspace_id FROM discovery_sources WHERE id=:id',
                        id=source['id'])['workspace_id']
        run = one(conn, """INSERT INTO discovery_runs(workspace_id,source_id,trigger,state,request_budget)
            VALUES(:ws,:sid,'manual','queued',3) RETURNING *""", ws=workspace, sid=source['id'])
        job = one(conn, """INSERT INTO jobs(workspace_id,kind,input,config_snapshot,idempotency_key,
            checkpoint) VALUES(:ws,'discover_keywords',CAST(:input AS jsonb),
            CAST('{"provider":"none"}' AS jsonb),:key,CAST(:checkpoint AS jsonb)) RETURNING *""",
                  ws=workspace,
                  input=main.js({'discovery_source_id': source['id'], 'run_id': str(run['id'])}),
                  key=f'discover:{source["id"]}:resume-test',
                  checkpoint=main.js({'pages': {'1': first_page}, 'requests': 1,
                                      'stage': 'discover_page_1'}))
        conn.execute(text('UPDATE discovery_runs SET job_id=:jid WHERE id=:rid'),
                     {'jid': job['id'], 'rid': run['id']})
    assert worker.tick() is True
    assert collector.calls == ['discover:2']
    body = api.get('/api/v1/discovery-runs/' + str(run['id'])).json()
    assert body['state'] == 'succeeded'
    assert body['requests_used'] == 2 and body['pages_visited'] == 2
    assert decisions(api, str(run['id']))[f'{BVID_A}:p1'] == 'enqueued'
    assert count(api.engine, 'sources') == 1


def test_fence_loss_during_discovery_writes_nothing(api):
    holder = {}

    def steal(page):
        if holder.get('done'):
            return
        holder['done'] = True
        with api.engine.begin() as conn:
            row = one(conn, "SELECT id FROM jobs WHERE kind='discover_keywords'")
            conn.execute(text("UPDATE jobs SET fence=fence+1,lease_until=now()+interval '5 minutes' WHERE id=:id"),
                         {'id': row['id']})

    use(FakeBilibiliCollector(discovery_items=items((BVID_A, 'AI 观察', 3)),
                              on_discover=steal))
    source = create(api, enabled=True)
    assert request_run(api, source['id']).status_code == 202
    assert worker.tick() is True
    with api.engine.connect() as conn:
        job = one(conn, "SELECT * FROM jobs WHERE kind='discover_keywords'")
        run = one(conn, 'SELECT * FROM discovery_runs')
    assert job['state'] == 'running' and job['fence'] == 2
    assert run['state'] == 'queued'
    assert count(api.engine, 'discovery_run_items') == 0
    assert count(api.engine, 'sources') == 0


def test_blocked_discovery_records_error_and_backoff(api):
    use(FakeBilibiliCollector(fail_discover_page=1, fail_code='RISK_CONTROL'))
    source = create(api, enabled=True)
    response = request_run(api, source['id'])
    assert worker.tick() is True
    run = api.get('/api/v1/discovery-runs/' + response.json()['run_id']).json()
    assert run['state'] == 'blocked_upstream' and run['error_code'] == 'RISK_CONTROL'
    job = api.get('/api/v1/jobs/' + run['job_id']).json()
    assert job['state'] == 'failed' and job['error_code'] == 'RISK_CONTROL'
    assert job['result']['discovery_state'] == 'blocked_upstream'
    detail = api.get('/api/v1/discovery-sources/' + source['id']).json()
    assert detail['failure_streak'] == 1
    assert detail['last_error_code'] == 'RISK_CONTROL'
    now = datetime.now(timezone.utc)
    assert detail['next_run_at'] is not None
    next_run = datetime.fromisoformat(detail['next_run_at'].replace('Z', '+00:00'))
    assert timedelta(minutes=10) < next_run - now < timedelta(minutes=20)
    assert count(api.engine, 'sources') == 0


def test_scheduler_only_runs_authorized_enabled_sources(api):
    due = create(api, name='已授权', enabled=True, network_authorized=True)
    create(api, name='未授权', enabled=True, network_authorized=False)
    create(api, name='已停用', enabled=False, network_authorized=True)

    created = scheduler.run_due()
    assert len(created) == 1
    assert count(api.engine, "jobs WHERE kind='discover_keywords'") == 1
    with api.engine.connect() as conn:
        job = one(conn, "SELECT * FROM jobs WHERE kind='discover_keywords'")
        source = one(conn, 'SELECT * FROM discovery_sources WHERE id=:id', id=due['id'])
    assert job['input']['trigger'] == 'schedule'
    assert source['next_run_at'] > datetime.now(timezone.utc)
    assert scheduler.run_due() == []


def test_patch_delete_and_unknown_ids(api):
    source = create(api)
    missing = api.patch('/api/v1/discovery-sources/' + source['id'],
                        json={'name': source['name']})
    assert missing.status_code == 428
    stale = api.patch('/api/v1/discovery-sources/' + source['id'],
                      headers={'If-Match': str(source['revision'] + 5)},
                      json={'name': source['name']})
    assert stale.status_code == 409
    updated = api.patch('/api/v1/discovery-sources/' + source['id'],
                        headers={'If-Match': str(source['revision'])},
                        json={'name': '新的名称', 'keywords': ['AI', '观察'],
                              'max_results_per_run': 3})
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body['revision'] == source['revision'] + 1
    assert body['keywords'] == ['AI', '观察'] and body['name'] == '新的名称'
    assert body['limits']['max_results_per_run'] == 3

    removed = api.delete('/api/v1/discovery-sources/' + source['id'],
                         headers={'If-Match': str(body['revision'])})
    assert removed.status_code == 200
    assert api.get('/api/v1/discovery-sources/' + source['id']).status_code == 404
    assert api.get('/api/v1/discovery-sources').json()['items'] == []

    assert api.post('/api/v1/discovery-sources/00000000-0000-0000-0000-000000000000/run').status_code == 404
    assert api.get('/api/v1/discovery-runs/00000000-0000-0000-0000-000000000000').status_code == 404


def test_manual_run_cooldown_after_completion(api):
    use(FakeBilibiliCollector(discovery_items=items((BVID_A, 'AI 观察', 3))))
    source = create(api, enabled=True)
    assert request_run(api, source['id']).status_code == 202
    assert worker.tick() is True
    second = request_run(api, source['id'])
    assert second.status_code == 429
    assert second.json()['error']['code'] == 'RATE_LIMITED'


def test_exhausted_discovery_job_closes_run_and_backs_off(api):
    use(FakeBilibiliCollector())
    source = create(api, enabled=True)
    response = request_run(api, source['id'])
    with api.engine.begin() as conn:
        conn.execute(text("""UPDATE jobs SET attempts=3,state='running',
            lease_until=now()-interval '1 minute' WHERE id=:id"""),
                     {'id': response.json()['job_id']})
    assert worker.claim() is None  # the claim sweep recovers abandoned runs first
    run = api.get('/api/v1/discovery-runs/' + response.json()['run_id']).json()
    assert run['state'] == 'failed' and run['error_code'] == 'ATTEMPTS_EXHAUSTED'
    detail = api.get('/api/v1/discovery-sources/' + source['id']).json()
    assert detail['last_error_code'] == 'ATTEMPTS_EXHAUSTED'
    assert detail['failure_streak'] == 1
    assert detail['next_run_at'] is not None


def test_cancelled_discovery_job_closes_run(api):
    source = create(api, enabled=True)
    response = request_run(api, source['id'])
    cancelled = api.post('/api/v1/jobs/' + response.json()['job_id'] + '/cancel')
    assert cancelled.status_code == 202
    assert cancelled.json()['state'] == 'cancelled'
    assert worker.claim() is None
    run = api.get('/api/v1/discovery-runs/' + response.json()['run_id']).json()
    assert run['state'] == 'cancelled'


def test_retrying_a_discovery_job_opens_a_new_run(api):
    use(FakeBilibiliCollector(discovery_items=items((BVID_A, 'AI 观察', 3))))
    source = create(api, enabled=True)
    response = request_run(api, source['id'])
    job_id = response.json()['job_id']
    assert api.post('/api/v1/jobs/' + job_id + '/cancel').status_code == 202
    assert worker.claim() is None  # sweep closes the cancelled run
    assert api.post('/api/v1/jobs/' + job_id + '/retry').status_code == 202
    assert worker.tick() is True
    runs = api.get('/api/v1/discovery-sources/' + source['id'] + '/runs').json()['items']
    assert [run['trigger'] for run in runs] == ['retry', 'manual']
    assert runs[0]['state'] == 'succeeded' and runs[1]['state'] == 'cancelled'
    assert count(api.engine, 'sources') == 1
