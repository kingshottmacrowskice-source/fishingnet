"""M4 Phase 2: model-proposed entity creation and entity-grouped updates.

Everything here is synthetic and the model is ``httpx.MockTransport``. The tests prove
that a routed ``entity_disambiguation`` call can (a) propose brand-new entities that the
server auto-creates as provisional nodes, and (b) group one source's claims into one
update per entity. Links to *existing* entities still stay ``proposed`` for human
acceptance, and a claim with no resolved entity keeps the one-update-per-claim shape.
"""
import json
import uuid

import httpx

import app.jobs.worker as worker
from app.storage.db import many, one

from m4_support import (
    create_model, create_provider, install_transport, save_routes, set_budget,
)

CONTENT = ('Plugin X 2.0 在 Windows 上离线索引运行正常。\n\n'
           'Plugin Y 的批量导入最多支持 20 个文件。')

CLAIMS = [
    {'statement': 'Plugin X 2.0 在 Windows 上离线索引运行正常。', 'kind': 'experience',
     'aspect': 'compatibility', 'polarity': 'affirm', 'importance': 'medium',
     'verifiability': 'verifiable', 'qualifiers': {'product_version': '2.0'},
     'evidence': [{'segment_id': 'seg_1', 'quote': '离线索引运行正常'}]},
    {'statement': 'Plugin Y 的批量导入最多支持 20 个文件。', 'kind': 'experience',
     'aspect': 'import', 'polarity': 'affirm', 'importance': 'medium',
     'verifiability': 'verifiable', 'qualifiers': {},
     'evidence': [{'segment_id': 'seg_2', 'quote': '批量导入最多支持 20 个文件'}]},
]


def entity_handler(*, new_entities=None, links=None):
    def handler(request):
        body = json.loads(request.content)
        system = body['messages'][0]['content']
        if '主张抽取器' in system:
            content = json.dumps({'schema_version': 'claim_extract.v1',
                                  'claims': CLAIMS}, ensure_ascii=False)
        elif '实体消歧' in system:
            content = json.dumps({'schema_version': 'entity_link.v1',
                                  'links': links or [],
                                  'new_entities': new_entities or []}, ensure_ascii=False)
        else:  # pragma: no cover
            raise AssertionError('unexpected task prompt: ' + system[:80])
        return httpx.Response(200, json={
            'id': 'chat-ent-1', 'model': 'mimo-2.6',
            'choices': [{'message': {'content': content}}],
            'usage': {'prompt_tokens': 300, 'completion_tokens': 100}})
    return handler


def import_text(api, content=CONTENT):
    response = api.post('/api/v1/sources/import',
                        headers={'Idempotency-Key': 'm4-ent-' + uuid.uuid4().hex},
                        json={'title': '桌宠文档', 'content': content,
                              'rights_confirmed': True})
    assert response.status_code == 202, response.text
    assert worker.tick() is True
    return response.json()


def route_and_run(api, handler):
    provider = create_provider(api)
    model = create_model(api, provider['id'])
    set_budget(api)
    save_routes(api, model['id'], ['claim_extraction', 'entity_disambiguation'])
    install_transport(handler)
    queued = import_text(api)
    job = api.get('/api/v1/jobs/' + queued['job_id']).json()
    return job


def test_new_entities_are_auto_created_and_group_updates(api):
    job = route_and_run(api, entity_handler(new_entities=[
        {'name': 'Plugin X', 'entity_type': 'plugin', 'aliases': ['PX'],
         'description': '测试插件', 'claim_refs': ['c1']},
        {'name': 'Plugin Y', 'entity_type': 'plugin', 'aliases': ['PY'],
         'description': '测试插件', 'claim_refs': ['c2']},
    ]))
    assert job['state'] == 'succeeded', job
    assert job['result']['entities_created'] == 2
    assert job['result']['published'] == 2
    with api.engine.connect() as conn:
        nodes = many(conn, "SELECT * FROM nodes WHERE node_kind='entity' ORDER BY canonical_name")
        assert [node['canonical_name'] for node in nodes] == ['Plugin X', 'Plugin Y']
        for node in nodes:
            assert node['status'] == 'provisional'
            assert node['metadata']['asserted_by'] == 'model'
            assert node['metadata']['auto_created'] is True
        accepted_about = many(conn, """SELECT * FROM relations WHERE relation_type='about'
            AND asserted_by='model' AND status='accepted'""")
        assert len(accepted_about) == 2
        updates = many(conn, 'SELECT * FROM updates ORDER BY title')
        assert [row['title'] for row in updates] == ['Plugin X', 'Plugin Y']
        assert len(updates[0]['structured_body']) == 1
    x_node = nodes[0]['id']
    feed = api.get('/api/v1/updates', params={'entity_id': x_node}).json()
    assert len(feed['items']) == 1
    assert feed['items'][0]['provenance']['entity_name'] == 'Plugin X'


def test_single_entity_groups_multiple_claims_into_one_update(api):
    job = route_and_run(api, entity_handler(new_entities=[
        {'name': 'Plugin X', 'entity_type': 'plugin', 'aliases': [],
         'description': '测试插件', 'claim_refs': ['c1', 'c2']},
    ]))
    assert job['state'] == 'succeeded', job
    assert job['result']['entities_created'] == 1
    assert job['result']['published'] == 1
    with api.engine.connect() as conn:
        updates = many(conn, 'SELECT * FROM updates')
        assert len(updates) == 1
        assert updates[0]['title'] == 'Plugin X'
        assert len(updates[0]['structured_body']) == 2
        assert one(conn, "SELECT count(*) AS n FROM nodes WHERE node_kind='entity'")['n'] == 1


def test_duplicate_new_entity_reuses_an_existing_node(api):
    with api.engine.begin() as conn:
        ws = one(conn, 'SELECT id FROM workspace_settings LIMIT 1')['id']
        one(conn, """INSERT INTO nodes(workspace_id,node_kind,canonical_name,entity_type)
            VALUES(:ws,'entity','Plugin X','plugin') RETURNING id""", ws=ws)
    job = route_and_run(api, entity_handler(new_entities=[
        {'name': 'Plugin X', 'entity_type': 'plugin', 'aliases': [],
         'description': '重复', 'claim_refs': ['c1']},
    ]))
    assert job['result']['entities_created'] == 0
    with api.engine.connect() as conn:
        assert one(conn, "SELECT count(*) AS n FROM nodes WHERE canonical_name='Plugin X'")['n'] == 1


def test_no_entity_still_publishes_one_update_per_claim(api):
    job = route_and_run(api, entity_handler())
    assert job['state'] == 'succeeded'
    assert job['result']['entities_created'] == 0
    assert job['result']['published'] == 2
    with api.engine.connect() as conn:
        assert one(conn, 'SELECT count(*) AS n FROM updates')['n'] == 2
        assert one(conn, "SELECT count(*) AS n FROM nodes WHERE node_kind='entity'")['n'] == 0
