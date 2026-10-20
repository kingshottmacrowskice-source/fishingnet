"""Shared helpers for the AC32-45 dataset tests (isolated schema, synthetic data)."""
import json
import uuid

import httpx

from app.domain import fact_evidence, verification  # noqa: F401  (re-exported for tests)
from app.domain.evidence import digest, parse_segments
from app.storage.db import execute, many, one

CONTENT = 'Plugin X 2.0 在 Windows 上离线索引运行正常。\n\n该版本安装包大小为 42 MB，可以离线安装。'
QUOTE = '离线索引运行正常'
SCOPE = '该版本的离线索引可用'


def seed(api, *, statement='Plugin X 2.0 在 Windows 上离线索引运行正常。', content=CONTENT,
         importance='medium', claim_kind='factual_assertion', verifiability='verifiable',
         with_node=True):
    with api.engine.begin() as conn:
        ws = one(conn, 'SELECT id FROM workspace_settings LIMIT 1')['id']
        source = one(conn, """INSERT INTO sources(workspace_id,platform,external_id,title,rights_policy,
            metadata) VALUES(:ws,'manual',:eid,'事实材料来源','{}'::jsonb,
            '{"schema_version":"source.v1"}'::jsonb) RETURNING *""",
                     ws=ws, eid=str(uuid.uuid4()))
        segments = parse_segments(content)
        version = one(conn, """INSERT INTO source_versions(workspace_id,source_id,version_no,raw_hash,
            normalized_hash,segments) VALUES(:ws,:sid,1,:raw,:norm,CAST(:seg AS jsonb)) RETURNING *""",
                      ws=ws, sid=source['id'], raw=digest(content), norm=digest(str(segments)),
                      seg=json.dumps(segments, ensure_ascii=False))
        execute(conn, 'UPDATE sources SET current_version_id=:v WHERE id=:id', v=version['id'],
                id=source['id'])
        claim = one(conn, """INSERT INTO claims(workspace_id,origin_version_id,statement,claim_kind,
            aspect,qualifiers,polarity,importance,verifiability,review_status,fingerprint,provenance)
            VALUES(:ws,:vid,:st,:kind,'compatibility',CAST(:qual AS jsonb),'affirm',:imp,:ver,
            'candidate',:fp,'{}'::jsonb) RETURNING *""",
                    ws=ws, vid=version['id'], st=statement, kind=claim_kind,
                    qual=json.dumps({'product_version': '2.0'}), imp=importance,
                    ver=verifiability, fp=digest(str(uuid.uuid4())))
        node = None
        if with_node:
            node = one(conn, """INSERT INTO nodes(workspace_id,node_kind,canonical_name,entity_type)
                VALUES(:ws,'entity','Plugin X','plugin') RETURNING *""", ws=ws)
            execute(conn, """INSERT INTO relations(workspace_id,from_type,from_id,relation_type,to_type,
                to_id,payload,status,asserted_by,dedup_key) VALUES(:ws,'claim',:cid,'about','node',
                :nid,'{}'::jsonb,'accepted','user',:key)""",
                    ws=ws, cid=claim['id'], nid=node['id'],
                    key=digest(str(claim['id']) + str(node['id'])))
    return {'claim': claim, 'version': version, 'source': source, 'segments': segments,
            'node': node, 'workspace_id': ws}


def add_second_source(api, seeded, *, content=None, title='独立测试来源'):
    """Create an independent source+version in the same workspace (synthetic)."""
    content = content or CONTENT
    with api.engine.begin() as conn:
        source = one(conn, """INSERT INTO sources(workspace_id,platform,external_id,title,rights_policy,
            metadata) VALUES(:ws,'manual',:eid,:title,'{}'::jsonb,
            '{"schema_version":"source.v1"}'::jsonb) RETURNING *""",
                     ws=seeded['workspace_id'], eid=str(uuid.uuid4()), title=title)
        segments = parse_segments(content)
        version = one(conn, """INSERT INTO source_versions(workspace_id,source_id,version_no,raw_hash,
            normalized_hash,segments) VALUES(:ws,:sid,1,:raw,:norm,CAST(:seg AS jsonb)) RETURNING *""",
                      ws=seeded['workspace_id'], sid=source['id'], raw=digest(content),
                      norm=digest(str(segments)), seg=json.dumps(segments, ensure_ascii=False))
        execute(conn, 'UPDATE sources SET current_version_id=:v WHERE id=:id',
                v=version['id'], id=source['id'])
    return {'source': source, 'version': version, 'segments': segments}


def add_evidence_anchor(api, seeded):
    with api.engine.begin() as conn:
        anchor = {'segment_id': 'seg_1', 'char_start': 0, 'char_end': 3,
                  'quote': seeded['segments'][0]['text'][:3],
                  'quote_hash': digest(seeded['segments'][0]['text'][:3])}
        relation = one(conn, """INSERT INTO relations(workspace_id,from_type,from_id,relation_type,
            to_type,to_id,payload,status,asserted_by,dedup_key)
            VALUES(:ws,'claim',:cid,'evidenced_by','source_version',:vid,CAST(:payload AS jsonb),
            'accepted','user',:key) RETURNING *""",
                       ws=seeded['workspace_id'], cid=seeded['claim']['id'],
                       vid=seeded['version']['id'], payload=json.dumps(anchor),
                       key=digest(str(seeded['claim']['id']) + ':evidenced_by'))
    return relation


def add_update(api, seeded, relation, *, mode='attributed_unverified', title=None):
    with api.engine.begin() as conn:
        body = [{'text': seeded['claim']['statement'], 'claim_id': str(seeded['claim']['id']),
                 'relation_id': str(relation['id']), 'publication_mode': mode,
                 'verification_record_ids': []}]
        update = one(conn, """INSERT INTO updates(workspace_id,update_type,title,summary,change_reason,
            event_key,state,first_published_at,input_fingerprint,structured_body,provenance)
            VALUES(:ws,'new_fact',:title,'摘要','测试',:key,'published',now(),:fp,
            CAST(:body AS jsonb),'{}'::jsonb) RETURNING *""",
                     ws=seeded['workspace_id'], title=title or '来源观点',
                     key='update-' + str(uuid.uuid4()),
                     fp=digest(str(body)), body=json.dumps(body, ensure_ascii=False))
    return update


def evidence_body(version_id, *, quote=QUOTE, relation='support', directness='direct',
                  integrity='verified', independence='confirmed', origin='group-a',
                  conditions='unknown', segment='seg_1', scope=SCOPE, note=None):
    if conditions == 'matched':
        applicability = {field: 'matched' for field in fact_evidence.APPLICABILITY_FIELDS}
    elif conditions == 'unknown':
        applicability = {field: 'unknown' for field in fact_evidence.APPLICABILITY_FIELDS}
    else:
        applicability = conditions
    body = {'source_version_id': str(version_id), 'segment_id': segment, 'quote': quote,
            'relation': relation, 'proposition_scope': scope, 'directness': directness,
            'artifact_integrity': integrity, 'independence_status': independence,
            'origin_group_id': origin, 'applicability': applicability}
    if note is not None:
        body['note'] = note
    return body


def post_material(api, claim_id, version_id, **kwargs):
    return api.post('/api/v1/claims/%s/evidence-links' % claim_id,
                    json=evidence_body(version_id, **kwargs))


def run_fact_check(api, claim_id):
    response = api.post('/api/v1/claims/%s/fact-checks' % claim_id, json={'reason': '人工发起核查'})
    assert response.status_code == 202, response.text
    assert worker_tick() is True
    job = api.get('/api/v1/jobs/' + response.json()['job_id']).json()
    return job, response.json()


def worker_tick():
    import app.jobs.worker as worker
    return worker.tick()


def latest_record(api, claim_id):
    response = api.get('/api/v1/claims/%s/fact-checks' % claim_id)
    assert response.status_code == 200, response.text
    return response.json()['items'][0]


def review(api, record, decision='accept', reason='已核对材料范围、条件与缺口', fingerprint=None):
    return api.post('/api/v1/verification-records/%s/review' % record['id'],
                    json={'decision': decision, 'reason': reason,
                          'expected_input_fingerprint': fingerprint or record['input_fingerprint']})


# ---------------------------------------------------------------- model routing

def create_provider(api):
    response = api.post('/api/v1/model-providers', json={
        'name': 'mimo-' + uuid.uuid4().hex[:6], 'protocol': 'openai_compatible',
        'base_url': 'https://llm.example/v1', 'api_key': 'sk-test-1234', 'enabled': True})
    assert response.status_code == 201, response.text
    return response.json()


def create_model(api, provider_id):
    response = api.post('/api/v1/models', json={
        'provider_id': provider_id, 'model_id': 'mimo-2.6', 'display_name': 'MIMO 2.6',
        'enabled': True, 'input_price_per_mtok': '1.0', 'output_price_per_mtok': '2.0',
        'context_window': 8192, 'max_output_tokens': 1024,
        'supports_structured_output': True})
    assert response.status_code == 201, response.text
    return response.json()


def set_budget(api, daily='10', monthly='50', if_match='1'):
    response = api.put('/api/v1/settings/budget', headers={'If-Match': if_match}, json={
        'daily_budget': daily, 'monthly_budget': monthly, 'data_policy': 'approved_cloud',
        'budget_currency': 'USD'})
    assert response.status_code == 200, response.text
    return response.json()


def save_routes(api, model_id, tasks, *, if_match='1', max_input_tokens=2000):
    routes = [{'task': task, 'mode': 'model', 'enabled': True, 'primary_model_id': model_id,
               'fallback_model_ids': [], 'max_input_tokens': max_input_tokens,
               'max_output_tokens': 400, 'timeout_seconds': 5, 'max_attempts': 1,
               'data_policy': 'approved_cloud'} for task in tasks]
    response = api.put('/api/v1/task-routes', headers={'If-Match': if_match},
                       json={'routes': routes, 'confirm_cloud': True})
    assert response.status_code == 200, response.text
    return response.json()


def install_transport(handler):
    from app.ai import providers as ai_providers
    calls = []

    def wrapper(request):
        calls.append(request)
        return handler(request)

    ai_providers.set_transport_factory(lambda: httpx.MockTransport(wrapper))
    return calls


def advisory_handler(payload, *, status=200):
    """Return a transport handler that answers the fact-check advisor prompt."""
    def handler(request):
        body = json.loads(request.content)
        system = body['messages'][0]['content']
        if '事实材料评审建议器' not in system:
            raise AssertionError('unexpected task prompt: ' + system[:80])
        if status != 200:
            return httpx.Response(status, json={'error': {'message': 'upstream down'}})
        content = json.dumps(payload, ensure_ascii=False) if not isinstance(payload, str) else payload
        return httpx.Response(200, json={
            'id': 'chat-fact-1', 'model': 'mimo-2.6',
            'choices': [{'message': {'content': content}}],
            'usage': {'prompt_tokens': 300, 'completion_tokens': 100}})
    return handler
