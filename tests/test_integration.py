"""Run against isolated schema on the local PostgreSQL instance; no user data changed."""
import json
import secrets
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from app.config import settings
import app.main as main
import app.auth as auth
import app.jobs.worker as worker
from app.storage.db import one


@pytest.fixture
def client(monkeypatch):
    schema='test_'+uuid.uuid4().hex
    base=create_engine(settings.database_url,connect_args={'connect_timeout':5})
    with base.begin() as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA {schema}')
    engine=create_engine(settings.database_url,connect_args={'options':f'-c search_path={schema},public','connect_timeout':5})
    try:
        with engine.begin() as conn:
            with conn.connection.driver_connection.cursor() as cur:
                for migration in sorted(Path('migrations/versions').glob('*.sql')):
                    cur.execute(migration.read_text(encoding='utf8'))
        for module in (main,auth,worker):monkeypatch.setattr(module,'engine',engine)
        monkeypatch.setattr(settings,'setup_token','test-setup')
        main.login_attempts.clear()
        with TestClient(main.app) as c:
            c.engine=engine
            yield c
    finally:
        engine.dispose()
        with base.begin() as conn:conn.exec_driver_sql(f'DROP SCHEMA {schema} CASCADE')
        base.dispose()


def initialize(c):
    r=c.post('/api/v1/auth/setup',json={'setup_token':'test-setup','username':'tester','password':'test-password-12345','workspace_name':'测试知识库'})
    assert r.status_code==200,r.text
    c.headers['X-CSRF-Token']=c.cookies['fn_csrf']
    # These tests target the preserved M1 pipeline, not the default V1 reader.
    with c.engine.begin() as conn:
        conn.exec_driver_sql('UPDATE workspace_settings SET simple_mode=false')


def test_end_to_end_and_idempotency(client):
    c=client
    assert c.get('/api/v1/sources').status_code==401
    initialize(c)
    assert c.post('/api/v1/auth/setup',json={'setup_token':'test-setup','username':'tester','password':'test-password-12345','workspace_name':'测试'}).status_code==409
    assert c.post('/api/v1/demo').status_code==202
    for _ in range(6):assert worker.tick()
    assert not worker.tick()
    feed=c.get('/api/v1/updates');assert feed.status_code==200,feed.text
    items=feed.json()['items'];assert len(items)==2
    assert any('令人困惑' in x['title'] for x in items)
    assert not any('支持离线索引' in x['title'] for x in items)
    for _ in range(10):assert c.post('/api/v1/demo').status_code==202
    assert not worker.tick()
    assert len(c.get('/api/v1/sources').json()['items'])==6
    assert len(c.get('/api/v1/updates').json()['items'])==2
    update=items[0]
    detail=c.get('/api/v1/updates/'+update['id']).json()
    assert detail['evidence']
    for ev in detail['evidence']:
        r=c.get('/api/v1/evidence/'+ev['id']);assert r.status_code==200
        assert r.json()['payload']['quote']
    assert c.put('/api/v1/updates/'+update['id']+'/state',json={'bookmarked':True,'read_revision':update['revision']}).status_code==200
    assert len(c.get('/api/v1/updates?bookmarked=true').json()['items'])==1
    assert '来源' in c.get('/api/v1/digests/today?download=true').text
    queue=c.get('/api/v1/verification-queue').json()['items'];assert len(queue)==6
    assert sum(x['importance']=='high' for x in queue)==3
    with c.engine.connect() as conn:
        assert one(conn,"SELECT count(*) n FROM relations WHERE relation_type='contradicts'")['n']==0
        assert one(conn,"SELECT count(*) n FROM relations WHERE relation_type='qualifies'")['n']>0
    old_cookie=c.cookies['fn_session']
    assert c.post('/api/v1/auth/logout').status_code==200
    c.cookies.set('fn_session',old_cookie)
    assert c.get('/api/v1/updates').status_code==401


def test_import_conflict_and_csrf(client):
    c=client;initialize(c)
    body={'title':'授权文本','content':'这是一份真实用户材料。','rights_confirmed':True}
    key='import-idempotent-test'
    for _ in range(10):
        r=c.post('/api/v1/sources/import',json=body,headers={'Idempotency-Key':key})
        assert r.status_code==202,r.text
    assert c.post('/api/v1/sources/import',json={**body,'content':'不同内容'},headers={'Idempotency-Key':key}).status_code==409
    assert len(c.get('/api/v1/sources').json()['items'])==1
    assert worker.tick()
    assert c.get('/api/v1/jobs').json()['items'][0]['state']=='needs_review'
    assert not c.get('/api/v1/updates').json()['items']
    c.headers.pop('X-CSRF-Token')
    assert c.post('/api/v1/demo').status_code==403


def test_delete_invalidates_all_surfaces(client):
    c=client;initialize(c);c.post('/api/v1/demo')
    while worker.tick():pass
    feed=c.get('/api/v1/updates').json()['items'];item=next(x for x in feed if '令人困惑' in x['title'])
    ev=item['evidence'][0]
    source=c.get('/api/v1/sources/'+ev['source_id']).json()
    assert c.delete('/api/v1/sources/'+source['id'],headers={'If-Match':str(source['revision'])}).status_code==200
    assert c.get('/api/v1/evidence/'+ev['id']).status_code==404
    assert c.get('/api/v1/updates/'+item['id']).status_code==404
    assert '令人困惑' not in c.get('/api/v1/digests/today?download=true').text
    assert c.get('/api/v1/claims/'+item['structured_body'][0]['claim_id']).status_code==404


def test_database_rejects_hallucinated_and_cross_workspace_relations(client):
    from sqlalchemy.exc import DBAPIError
    c=client;initialize(c);c.post('/api/v1/demo')
    while worker.tick():pass
    with c.engine.connect() as conn:
        rel=one(conn,"SELECT * FROM relations WHERE relation_type='evidenced_by' LIMIT 1")
    with pytest.raises(DBAPIError):
        with c.engine.begin() as conn:
            conn.execute(text("UPDATE relations SET payload=jsonb_set(payload,'{quote}',:quote) WHERE id=:id"),{'quote':json.dumps('幻觉引用'),'id':rel['id']})
    with pytest.raises(DBAPIError):
        with c.engine.begin() as conn:
            conn.execute(text("UPDATE relations SET status='retired' WHERE id=:id"),{'id':rel['id']})
    with pytest.raises(DBAPIError):
        with c.engine.begin() as conn:
            ws=one(conn,"INSERT INTO workspace_settings(name) VALUES('second') RETURNING id")
            conn.execute(text('UPDATE relations SET workspace_id=:ws WHERE id=:id'),{'ws':ws['id'],'id':rel['id']})


def test_password_change_revokes_other_sessions(client):
    c=client;initialize(c)
    first_token=c.cookies['fn_session']
    assert c.post('/api/v1/auth/login',json={'username':'tester','password':'test-password-12345'}).status_code==200
    c.headers['X-CSRF-Token']=c.cookies['fn_csrf']
    assert len(c.get('/api/v1/auth/sessions').json()['items'])==2
    assert c.post('/api/v1/auth/password',json={'current_password':'test-password-12345','new_password':'new-test-password-12345'}).status_code==200
    assert len(c.get('/api/v1/auth/sessions').json()['items'])==1
    c.cookies.clear();c.cookies.set('fn_session',first_token)
    assert c.get('/api/v1/auth/me').status_code==401


def import_text(c,title='授权文本',content='这是一份真实用户材料。',key='import-test-key'):
    return c.post('/api/v1/sources/import',json={'title':title,'content':content,'format':'text','rights_confirmed':True},headers={'Idempotency-Key':key})


def only_job(c):
    with c.engine.connect() as conn:
        return one(conn,'SELECT * FROM jobs ORDER BY created_at DESC LIMIT 1')


def test_error_contract_and_if_match(client):
    c=client
    r=c.get('/api/v1/sources')
    assert r.status_code==401
    body=r.json()
    assert body['error']['code']=='UNAUTHENTICATED'
    assert body['error']['retryable'] is False
    assert body['request_id'] and r.headers['X-Request-Id']==body['request_id']
    initialize(c)
    # Schema validation keeps the same envelope and never echoes the password.
    r=c.post('/api/v1/auth/setup',json={'setup_token':'test-setup','username':'tester2','password':'zzzz','workspace_name':'测试'})
    assert r.status_code==422 and r.json()['error']['code']=='VALIDATION_ERROR'
    assert r.json()['error']['details'][0]['field']=='password'
    assert 'zzzz' not in r.text
    r=c.patch('/api/v1/auth/profile',json={'display_name':'新名字','timezone':'Asia/Shanghai'})
    assert r.status_code==428 and r.json()['error']['code']=='PRECONDITION_REQUIRED'
    r=c.patch('/api/v1/auth/profile',json={'display_name':'新名字','timezone':'Asia/Shanghai'},headers={'If-Match':'not-a-number'})
    assert r.status_code==422 and r.json()['error']['code']=='INVALID_IF_MATCH'
    r=c.patch('/api/v1/auth/profile',json={'display_name':'新名字','timezone':'Asia/Shanghai'},headers={'If-Match':'999'})
    assert r.status_code==409 and r.json()['error']['code']=='VERSION_CONFLICT'
    r=c.patch('/api/v1/auth/profile',json={'display_name':'新名字','timezone':'Asia/Shanghai'},headers={'If-Match':'"1"'})
    assert r.status_code==200,r.text
    assert c.get('/api/v1/auth/me').json()['display_name']=='新名字'
    r=c.patch('/api/v1/auth/profile',json={'display_name':'另一名字','timezone':'Not/AZone'},headers={'If-Match':'2'})
    assert r.status_code==422 and r.json()['error']['code']=='INVALID_TIMEZONE'


def test_cursor_pagination_filters_and_invalid_cursor(client):
    c=client;initialize(c);c.post('/api/v1/demo')
    while worker.tick():pass
    first=c.get('/api/v1/updates?limit=1').json()
    assert len(first['items'])==1 and first['next_cursor']
    second=c.get('/api/v1/updates?limit=1&cursor='+first['next_cursor']).json()
    assert len(second['items'])==1 and not second['next_cursor']
    assert {first['items'][0]['id'],second['items'][0]['id']}=={x['id'] for x in c.get('/api/v1/updates').json()['items']}
    bad=c.get('/api/v1/updates?cursor=not-a-cursor')
    assert bad.status_code==400 and bad.json()['error']['code']=='INVALID_CURSOR'
    assert 'SELECT' not in bad.text and 'FROM' not in bad.text
    assert c.get('/api/v1/updates?limit=0').status_code==400
    # A filter must survive the cursor and the next page must not repeat items.
    item=first['items'][0]
    assert c.put('/api/v1/updates/'+item['id']+'/state',json={'bookmarked':True}).status_code==200
    saved=c.get('/api/v1/updates?bookmarked=true&limit=1').json()
    assert len(saved['items'])==1 and saved['next_cursor'] is None
    # Source pagination keeps the q filter and returns every source exactly once.
    seen=[];cursor=None
    while True:
        url='/api/v1/sources?limit=2&q=示例'+(('&cursor='+cursor) if cursor else '')
        page=c.get(url).json();seen+= [x['id'] for x in page['items']];cursor=page['next_cursor']
        if not cursor:break
    assert len(seen)==6 and len(set(seen))==6
    queue=[];cursor=None
    while True:
        page=c.get('/api/v1/verification-queue?limit=2'+(('&cursor='+cursor) if cursor else '')).json()
        queue+=[x['id'] for x in page['items']];cursor=page['next_cursor']
        if not cursor:break
    assert len(queue)==6 and len(set(queue))==6


def test_idempotency_key_contract(client):
    c=client;initialize(c)
    body={'title':'授权文本','content':'这是一份真实用户材料。','rights_confirmed':True}
    r=c.post('/api/v1/sources/import',json=body)
    assert r.status_code==400 and r.json()['error']['code']=='IDEMPOTENCY_KEY_REQUIRED'
    r=c.post('/api/v1/sources/import',json=body,headers={'Idempotency-Key':'short'})
    assert r.status_code==400 and r.json()['error']['code']=='INVALID_IDEMPOTENCY_KEY'
    assert import_text(c).status_code==202
    r=c.post('/api/v1/sources/import',json={**body,'content':'不同内容'},headers={'Idempotency-Key':'import-test-key'})
    assert r.status_code==409 and r.json()['error']['code']=='IDEMPOTENCY_KEY_CONFLICT'


def test_revoke_other_sessions_rotates_current(client):
    c=client;initialize(c)
    first_token=c.cookies['fn_session']
    assert c.post('/api/v1/auth/login',json={'username':'tester','password':'test-password-12345'}).status_code==200
    c.headers['X-CSRF-Token']=c.cookies['fn_csrf']
    assert len(c.get('/api/v1/auth/sessions').json()['items'])==2
    r=c.post('/api/v1/auth/sessions/revoke-others')
    assert r.status_code==200,r.text
    rotated=c.cookies['fn_session']
    sessions=c.get('/api/v1/auth/sessions').json()['items']
    assert len(sessions)==1 and sessions[0]['current']
    c.cookies.set('fn_session',first_token)
    assert c.get('/api/v1/auth/me').status_code==401
    c.cookies.set('fn_session',rotated)
    assert c.get('/api/v1/auth/sessions').status_code==200


def test_job_cancel_retry_and_detail(client):
    c=client;initialize(c)
    assert import_text(c).status_code==202
    job=only_job(c)
    detail=c.get('/api/v1/jobs/'+str(job['id']))
    assert detail.status_code==200 and detail.json()['attempts_history']==[]
    r=c.post('/api/v1/jobs/'+str(job['id'])+'/cancel');assert r.status_code==202 and r.json()['state']=='cancelled'
    r=c.post('/api/v1/jobs/'+str(job['id'])+'/cancel');assert r.status_code==202 and r.json()['already_requested'] is True
    r=c.post('/api/v1/jobs/'+str(job['id'])+'/retry');assert r.status_code==202 and r.json()['state']=='queued'
    r=c.post('/api/v1/jobs/'+str(job['id'])+'/retry');assert r.status_code==409 and r.json()['error']['code']=='JOB_STATE_CONFLICT'
    assert worker.tick()
    assert only_job(c)['state']=='needs_review'
    r=c.post('/api/v1/jobs/'+str(job['id'])+'/cancel');assert r.status_code==409
    r=c.post('/api/v1/jobs/'+str(job['id'])+'/retry');assert r.status_code==409


def test_cancel_locked_job_returns_job_busy(client):
    c=client;initialize(c)
    assert import_text(c).status_code==202
    job=only_job(c)
    holder=c.engine.connect()
    transaction=holder.begin()
    holder.execute(text('SELECT id FROM jobs WHERE id=:id FOR UPDATE'),{'id':job['id']})
    try:
        r=c.post('/api/v1/jobs/'+str(job['id'])+'/cancel')
        assert r.status_code==409 and r.json()['error']['code']=='JOB_BUSY'
        assert r.json()['error']['retryable'] is True
    finally:
        transaction.rollback();holder.close()


def test_cancel_requested_stops_before_processing(client):
    c=client;initialize(c)
    assert import_text(c).status_code==202
    original=worker.process
    worker.process=lambda job:None
    try:
        assert worker.tick()
    finally:
        worker.process=original
    job=only_job(c)
    assert job['state']=='running'
    r=c.post('/api/v1/jobs/'+str(job['id'])+'/cancel')
    assert r.status_code==202 and r.json()['state']=='cancel_requested'
    worker.process(job)
    with c.engine.connect() as conn:
        current=one(conn,'SELECT * FROM jobs WHERE id=:id',id=job['id'])
        assert current['state']=='cancelled'
        assert one(conn,'SELECT count(*) n FROM claims')['n']==0
        assert one(conn,"SELECT count(*) n FROM job_attempts WHERE state='cancelled'")['n']==1


def test_stale_fence_does_not_write(client):
    c=client;initialize(c)
    assert import_text(c).status_code==202
    original=worker.process
    worker.process=lambda job:None
    try:
        assert worker.tick()
    finally:
        worker.process=original
    stale=only_job(c)
    with c.engine.begin() as conn:
        conn.execute(text("UPDATE jobs SET fence=fence+1,attempts=attempts+1,lease_until=now()+interval '2 minutes' WHERE id=:id"),{'id':stale['id']})
    worker.process(stale)
    with c.engine.connect() as conn:
        assert one(conn,'SELECT count(*) n FROM claims')['n']==0
        assert one(conn,'SELECT count(*) n FROM updates')['n']==0
        assert one(conn,'SELECT state FROM jobs WHERE id=:id',id=stale['id'])['state']=='running'


def test_expired_lease_recovers_with_new_fence(client):
    c=client;initialize(c)
    assert import_text(c).status_code==202
    job=only_job(c)
    with c.engine.begin() as conn:
        conn.execute(text("UPDATE jobs SET state='running',stage='extract',attempts=1,fence=1,lease_until=now()-interval '1 second' WHERE id=:id"),{'id':job['id']})
        conn.execute(text("INSERT INTO job_attempts(workspace_id,job_id,fence,state) VALUES(:ws,:id,1,'running')"),{'ws':job['workspace_id'],'id':job['id']})
    assert worker.tick()
    with c.engine.connect() as conn:
        current=one(conn,'SELECT * FROM jobs WHERE id=:id',id=job['id'])
        assert current['state']=='needs_review' and current['attempts']==2 and current['fence']==2
        fences=[r['fence'] for r in conn.execute(text('SELECT fence FROM job_attempts WHERE job_id=:id ORDER BY created_at,fence'),{'id':job['id']}).mappings()]
        assert fences==[1,2]


def test_expired_lease_does_not_duplicate_published_update(client):
    c=client;initialize(c);c.post('/api/v1/demo')
    while worker.tick():pass
    assert len(c.get('/api/v1/updates').json()['items'])==2
    with c.engine.connect() as conn:
        job=one(conn,"SELECT * FROM jobs WHERE (result->>'published')::int>0 LIMIT 1")
    assert job
    with c.engine.begin() as conn:
        conn.execute(text("UPDATE jobs SET state='running',stage='extract',attempts=1,lease_until=now()-interval '1 second' WHERE id=:id"),{'id':job['id']})
    assert worker.tick()
    with c.engine.connect() as conn:
        assert one(conn,'SELECT count(*) n FROM updates')['n']==2
        done=one(conn,'SELECT * FROM jobs WHERE id=:id',id=job['id'])
        assert done['state']=='succeeded' and done['result']['published']==0


def test_attempts_exhausted_becomes_failed(client):
    c=client;initialize(c)
    assert import_text(c).status_code==202
    job=only_job(c)
    with c.engine.begin() as conn:
        conn.execute(text("UPDATE jobs SET state='running',attempts=3,lease_until=now()-interval '1 second' WHERE id=:id"),{'id':job['id']})
    assert not worker.tick()
    with c.engine.connect() as conn:
        current=one(conn,'SELECT * FROM jobs WHERE id=:id',id=job['id'])
        assert current['state']=='failed' and current['error_code']=='ATTEMPTS_EXHAUSTED'


def test_concurrent_claim_processes_each_job_once(client):
    c=client;initialize(c)
    assert import_text(c,'材料甲','甲材料内容。','concurrent-key-a').status_code==202
    assert import_text(c,'材料乙','乙材料内容。','concurrent-key-b').status_code==202
    errors=[]
    barrier=threading.Barrier(4)
    def run():
        barrier.wait()
        try: worker.tick()
        except Exception as exc: errors.append(exc)
    threads=[threading.Thread(target=run) for _ in range(4)]
    for t in threads:t.start()
    for t in threads:t.join(timeout=30)
    assert not errors
    with c.engine.connect() as conn:
        jobs=conn.execute(text('SELECT attempts,state FROM jobs')).mappings().all()
        assert len(jobs)==2 and all(j['attempts']==1 for j in jobs)
        assert {j['state'] for j in jobs}<={'needs_review','succeeded'}
        rows=conn.execute(text('SELECT job_id,fence,state FROM job_attempts')).mappings().all()
        assert len(rows)==2
        by_job={}
        for row in rows:by_job.setdefault(row['job_id'],[]).append(row)
        assert all(len(v)==1 for v in by_job.values())


def test_worker_exception_marks_job_failed(client,monkeypatch):
    c=client;initialize(c)
    assert import_text(c).status_code==202
    def boom(self,segments,**kwargs):
        raise RuntimeError('synthetic failure')
    monkeypatch.setattr(worker.AIGateway,'extract',boom)
    with pytest.raises(RuntimeError):
        worker.tick()
    job=only_job(c)
    assert job['state']=='failed' and job['error_code']=='RuntimeError'
    assert job['lease_until'] is None


def test_topic_management_cycle_and_feed_filter(client):
    c=client;initialize(c);c.post('/api/v1/demo')
    while worker.tick():pass
    topics=c.get('/api/v1/nodes?kind=topic').json()['items']
    entities=c.get('/api/v1/nodes?kind=entity').json()['items']
    plugin=next(x for x in entities if x['canonical_name']=='Plugin X')
    a=c.post('/api/v1/nodes',json={'kind':'topic','name':'AI 插件','description':'合成测试主题'}).json()
    b=c.post('/api/v1/nodes',json={'kind':'topic','name':'插件性能'}).json()
    assert c.post('/api/v1/topic-links',json={'child_id':b['id'],'parent_id':a['id']}).status_code==201
    assert c.post('/api/v1/topic-links',json={'child_id':plugin['id'],'parent_id':a['id']}).status_code==201
    # Repeat is idempotent, not a duplicate relation.
    assert c.post('/api/v1/topic-links',json={'child_id':plugin['id'],'parent_id':a['id']}).status_code==201
    # A parent must be a topic; entity-to-entity links are rejected.
    assert c.post('/api/v1/topic-links',json={'child_id':plugin['id'],'parent_id':plugin['id']}).status_code==422
    # A -> B already exists, so B -> A would form a cycle.
    r=c.post('/api/v1/topic-links',json={'child_id':a['id'],'parent_id':b['id']})
    assert r.status_code==409 and r.json()['error']['code']=='TOPIC_CYCLE'
    detail=c.get('/api/v1/nodes/'+a['id']).json()
    assert {x['id'] for x in detail['children']}=={b['id']}
    assert {x['id'] for x in detail['entities']}=={plugin['id']}
    assert len(detail['updates'])==2
    entity_detail=c.get('/api/v1/nodes/'+plugin['id']).json()
    assert a['id'] in {x['id'] for x in entity_detail['topics']}
    # Topic filter follows member entities; time filters and invalid ranges are explicit.
    assert len(c.get('/api/v1/updates?topic_id='+a['id']).json()['items'])==2
    assert len(c.get('/api/v1/updates?topic_id='+b['id']).json()['items'])==0
    assert len(c.get('/api/v1/updates?entity_id='+plugin['id']).json()['items'])==2
    assert len(c.get('/api/v1/updates?since=2999-01-01').json()['items'])==0
    assert c.get('/api/v1/updates?since=not-a-time').status_code==400
    assert c.get('/api/v1/updates?since=2030-01-01&until=2020-01-01').status_code==400
    # Node edits require If-Match and advance revision.
    r=c.patch('/api/v1/nodes/'+a['id'],json={'name':'AI 插件（改名）'})
    assert r.status_code==428
    r=c.patch('/api/v1/nodes/'+a['id'],json={'name':'AI 插件（改名）'},headers={'If-Match':'99'})
    assert r.status_code==409 and r.json()['error']['code']=='VERSION_CONFLICT'
    r=c.patch('/api/v1/nodes/'+a['id'],json={'name':'AI 插件（改名）'},headers={'If-Match':'1'})
    assert r.status_code==200 and r.json()['canonical_name']=='AI 插件（改名）'
    # Removing the entity link removes it from the topic feed but keeps the entity.
    link=next(x for x in detail['entities'] if x['id']==plugin['id'])
    assert c.delete('/api/v1/topic-links/'+link['relation_id']).status_code==200
    assert len(c.get('/api/v1/updates?topic_id='+a['id']).json()['items'])==0
    assert c.delete('/api/v1/topic-links/'+link['relation_id']).status_code==404
    assert c.get('/api/v1/nodes/'+plugin['id']).status_code==200


def test_delete_source_is_idempotent_and_requires_if_match(client):
    c=client;initialize(c);c.post('/api/v1/demo')
    while worker.tick():pass
    item=c.get('/api/v1/updates').json()['items'][0]
    source=c.get('/api/v1/sources/'+item['evidence'][0]['source_id']).json()
    assert c.delete('/api/v1/sources/'+source['id']).status_code==428
    assert c.delete('/api/v1/sources/'+source['id'],headers={'If-Match':'999'}).status_code==409
    assert c.delete('/api/v1/sources/'+source['id'],headers={'If-Match':str(source['revision'])}).status_code==200
    again=c.delete('/api/v1/sources/'+source['id'],headers={'If-Match':str(source['revision'])})
    assert again.status_code==200 and again.json()['already_deleted'] is True
