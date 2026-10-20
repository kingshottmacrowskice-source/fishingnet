"""M4 Phase 1: fact materials, versioned verification records and human review.

Everything here is synthetic and runs against an isolated PostgreSQL schema with no
model or network call. Passing these tests proves the server-side gate and the
append-only record chain, not a real external fact check.
"""
import uuid

import pytest
from sqlalchemy import text

import app.jobs.worker as worker
from app.domain import fact_evidence, verification
from app.domain.evidence import digest, parse_segments
from app.domain.policy import mode_is_within
from app.storage.db import execute, many, one

CONTENT = 'Plugin X 2.0 在 Windows 上离线索引运行正常。\n\n该版本安装包大小为 42 MB，可以离线安装。'
QUOTE = '离线索引运行正常'
SCOPE = '该版本的离线索引可用'


def segment_list(content=CONTENT):
    return parse_segments(content)


def material(relation='fact_supported_by', *, directness='direct', integrity='verified',
             independence='confirmed', origin='group-a', conditions=None,
             scope=SCOPE, relation_id=None, status='proposed'):
    payload = {
        'schema_version': 'fact_evidence.v1',
        'anchor': {'segment_id': 'seg_1', 'char_start': 0, 'char_end': 2,
                   'quote': '离线', 'quote_hash': digest('离线')},
        'proposition_scope': scope,
        'directness': directness,
        'origin_group_id': origin,
        'independence_status': independence,
        'artifact_integrity': integrity,
        'applicability': {field: 'matched' for field in fact_evidence.APPLICABILITY_FIELDS}
        if conditions is None else conditions,
        'method_transparency': {},
        'limitations': [],
    }
    return {'id': relation_id or str(uuid.uuid4()), 'relation_type': relation,
            'status': status, 'payload': payload}


def claim_row(importance='medium'):
    return {'id': str(uuid.uuid4()), 'statement': 'Plugin X 2.0 在 Windows 上离线索引运行正常。',
            'importance': importance, 'claim_kind': 'factual_assertion',
            'verifiability': 'verifiable', 'lifecycle_status': 'active', 'revision': 1,
            'fingerprint': 'f' * 64}


# ------------------------------------------------------------------ unit tests

def test_normalize_link_computes_anchor_offsets_and_hash():
    segments = segment_list()
    relation_type, payload = fact_evidence.normalize_link(
        body={'segment_id': 'seg_1', 'quote': QUOTE, 'proposition_scope': SCOPE,
              'relation': 'support', 'directness': 'direct',
              'artifact_integrity': 'verified'},
        segments=segments, source_id='source-1')
    assert relation_type == 'fact_supported_by'
    anchor = payload['anchor']
    assert segments[0]['text'][anchor['char_start']:anchor['char_end']] == QUOTE
    assert anchor['quote_hash'] == digest(QUOTE)
    assert payload['origin_group_id'] == 'source:source-1'
    assert payload['applicability'] == {field: 'unknown' for field in fact_evidence.APPLICABILITY_FIELDS}


def test_normalize_link_rejects_unknown_segment_missing_and_ambiguous_quotes():
    segments = segment_list()
    with pytest.raises(fact_evidence.FactEvidenceError) as error:
        fact_evidence.normalize_link(
            body={'segment_id': 'seg_9', 'quote': QUOTE, 'proposition_scope': SCOPE},
            segments=segments, source_id='s')
    assert error.value.code == 'UNKNOWN_SEGMENT'
    with pytest.raises(fact_evidence.FactEvidenceError) as error:
        fact_evidence.normalize_link(
            body={'segment_id': 'seg_1', 'quote': '并不存在的句子', 'proposition_scope': SCOPE},
            segments=segments, source_id='s')
    assert error.value.code == 'QUOTE_NOT_FOUND'
    repeated = [{'segment_id': 'seg_1', 'text': '离线索引；离线索引'}]
    with pytest.raises(fact_evidence.FactEvidenceError) as error:
        fact_evidence.normalize_link(
            body={'segment_id': 'seg_1', 'quote': '离线索引', 'proposition_scope': SCOPE},
            segments=repeated, source_id='s')
    assert error.value.code == 'QUOTE_AMBIGUOUS'


def test_normalize_link_validates_enums_and_control_characters():
    segments = segment_list()
    with pytest.raises(fact_evidence.FactEvidenceError):
        fact_evidence.normalize_link(
            body={'segment_id': 'seg_1', 'quote': QUOTE, 'proposition_scope': SCOPE,
                  'applicability': {'product_version': 'likely'}},
            segments=segments, source_id='s')
    with pytest.raises(fact_evidence.FactEvidenceError):
        fact_evidence.normalize_link(
            body={'segment_id': 'seg_1', 'quote': QUOTE, 'proposition_scope': '范围\x00截断'},
            segments=segments, source_id='s')
    with pytest.raises(fact_evidence.FactEvidenceError):
        fact_evidence.normalize_link(
            body={'segment_id': 'seg_1', 'quote': QUOTE, 'proposition_scope': SCOPE,
                  'limitations': ['x'] * 11},
            segments=segments, source_id='s')


def test_material_tier_requires_matched_conditions_and_verified_artifact():
    assert verification.material_tier(material()) == ('E2', ['DIRECT_APPLICABLE_VERIFIED'])
    unknown = {field: 'matched' for field in fact_evidence.APPLICABILITY_FIELDS}
    unknown['environment'] = 'unknown'
    tier, reasons = verification.material_tier(material(conditions=unknown))
    assert tier == 'E1' and 'CONDITION_UNKNOWN_OR_PARTIAL' in reasons
    assert verification.material_tier(material(directness='indirect'))[0] == 'E1'
    mismatched = {field: 'matched' for field in fact_evidence.APPLICABILITY_FIELDS}
    mismatched['date'] = 'mismatched'
    assert verification.material_tier(material(conditions=mismatched))[0] == 'E0'
    assert verification.material_tier(material(integrity='suspected_manipulation'))[0] == 'E0'
    assert verification.material_tier(material(integrity='unverified'))[0] == 'E1'


def test_e3_needs_two_distinct_independent_origin_groups():
    one_only = material(origin='group-a', relation_id=str(uuid.uuid4()))
    second_same = material(origin='group-a', relation_id=str(uuid.uuid4()))
    assessment = verification.assess(claim=claim_row(),
                                     materials=[one_only, second_same])
    assert assessment['support_strength'] == 'E2'
    assert 'E3_REQUIRES_DISTINCT_ORIGIN_GROUPS' in assessment['tier_reasons']['support']
    second = material(origin='group-b', relation_id=str(uuid.uuid4()))
    assessment = verification.assess(claim=claim_row(), materials=[one_only, second])
    assert assessment['support_strength'] == 'E3'
    shared = [material(origin='group-a', independence='shared_origin'),
              material(origin='group-b', independence='shared_origin')]
    assessment = verification.assess(claim=claim_row(), materials=shared)
    assert assessment['support_strength'] == 'E2'
    assert 'E3_REQUIRES_CONFIRMED_INDEPENDENCE' in assessment['tier_reasons']['support']


def test_assess_status_matrix_is_explainable():
    empty = verification.assess(claim=claim_row(), materials=[])
    assert empty['status'] == 'insufficient' and empty['support_strength'] == 'E0'
    assert empty['gaps'] and empty['human_review_required'] is True
    assert empty['auto_accept_allowed'] is False
    corroborated = verification.assess(claim=claim_row(), materials=[material()])
    assert corroborated['status'] == 'corroborated'
    assert corroborated['unresolved_counterevidence'] is False
    weak = material(conditions={field: 'matched' for field in fact_evidence.APPLICABILITY_FIELDS}
                    | {'environment': 'unknown'})
    assert verification.assess(claim=claim_row(), materials=[weak])['status'] == 'insufficient'
    conflicted = verification.assess(
        claim=claim_row(),
        materials=[material(), material('fact_refuted_by', conditions={
            field: 'matched' for field in fact_evidence.APPLICABILITY_FIELDS} | {'date': 'unknown'})])
    assert conflicted['status'] == 'conflicted'
    assert conflicted['unresolved_counterevidence'] is True
    refuted = verification.assess(
        claim=claim_row(),
        materials=[material('fact_refuted_by', origin='group-a'),
                   material('fact_refuted_by', origin='group-b')])
    assert refuted['status'] == 'refuted'


def test_accept_gate_blocks_unsupported_or_important_claims():
    assert verification.accept_gate(claim_row(), {'status': 'corroborated', 'support_strength': 'E2',
                                                  'refutation_strength': 'E0'}) == []
    assert 'IMPORTANT_FACT_REQUIRES_E3' in verification.accept_gate(
        claim_row('high'), {'status': 'corroborated', 'support_strength': 'E2',
                            'refutation_strength': 'E0'})
    assert 'EVIDENCE_TIER_INSUFFICIENT' in verification.accept_gate(
        claim_row(), {'status': 'corroborated', 'support_strength': 'E1',
                      'refutation_strength': 'E0'})
    assert 'REFUTATION_TIER_INSUFFICIENT' in verification.accept_gate(
        claim_row(), {'status': 'refuted', 'support_strength': 'E0',
                      'refutation_strength': 'E1'})
    assert 'CONFLICTING_SUPPORT_PRESENT' in verification.accept_gate(
        claim_row(), {'status': 'refuted', 'support_strength': 'E2',
                      'refutation_strength': 'E3'})
    assert verification.accept_gate(claim_row(), {'status': 'insufficient',
                                                  'support_strength': 'E0',
                                                  'refutation_strength': 'E0'}) == []


def test_snapshot_hash_tracks_the_material_state():
    first = material(relation_id=str(uuid.uuid4()))
    claim = claim_row()
    base = verification.build_snapshot(claim=claim, materials=[first])
    assert base['snapshot_hash'] == verification.build_snapshot(claim=claim, materials=[first])['snapshot_hash']
    changed = material(relation_id=first['id'], status='accepted')
    assert verification.build_snapshot(claim=claim, materials=[changed])['snapshot_hash'] != base['snapshot_hash']
    assert base['material_ids'] == [first['id']]


def test_mode_strength_never_upgrades_a_stored_sentence():
    assert mode_is_within('attributed_unverified', 'scoped_fact') is True
    assert mode_is_within('opinion', 'attributed_unverified') is True
    assert mode_is_within('scoped_fact', 'attributed_unverified') is False
    assert mode_is_within('conditional_experience', 'opinion') is True
    assert mode_is_within('scoped_fact', 'unknown-mode') is False


# ----------------------------------------------------------- integration helpers

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
                      seg=__import__('json').dumps(segments, ensure_ascii=False))
        execute(conn, 'UPDATE sources SET current_version_id=:v WHERE id=:id', v=version['id'],
                id=source['id'])
        claim = one(conn, """INSERT INTO claims(workspace_id,origin_version_id,statement,claim_kind,
            aspect,qualifiers,polarity,importance,verifiability,review_status,fingerprint,provenance)
            VALUES(:ws,:vid,:st,:kind,'compatibility',CAST(:qual AS jsonb),'affirm',:imp,:ver,
            'candidate',:fp,'{}'::jsonb) RETURNING *""",
                    ws=ws, vid=version['id'], st=statement, kind=claim_kind,
                    qual=__import__('json').dumps({'product_version': '2.0'}), imp=importance,
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


def add_evidence_anchor(api, seeded):
    """Attach the claim's expression anchor so an update can reference it."""
    with api.engine.begin() as conn:
        anchor = {'segment_id': 'seg_1', 'char_start': 0, 'char_end': 3,
                  'quote': seeded['segments'][0]['text'][:3],
                  'quote_hash': digest(seeded['segments'][0]['text'][:3])}
        relation = one(conn, """INSERT INTO relations(workspace_id,from_type,from_id,relation_type,
            to_type,to_id,payload,status,asserted_by,dedup_key)
            VALUES(:ws,'claim',:cid,'evidenced_by','source_version',:vid,CAST(:payload AS jsonb),
            'accepted','user',:key) RETURNING *""",
                       ws=seeded['workspace_id'], cid=seeded['claim']['id'],
                       vid=seeded['version']['id'], payload=__import__('json').dumps(anchor),
                       key=digest(str(seeded['claim']['id']) + ':evidenced_by'))
    return relation


def add_update(api, seeded, relation, *, mode='attributed_unverified'):
    with api.engine.begin() as conn:
        body = [{'text': seeded['claim']['statement'], 'claim_id': str(seeded['claim']['id']),
                 'relation_id': str(relation['id']), 'publication_mode': mode,
                 'verification_record_ids': []}]
        update = one(conn, """INSERT INTO updates(workspace_id,update_type,title,summary,change_reason,
            event_key,state,first_published_at,input_fingerprint,structured_body,provenance)
            VALUES(:ws,'new_fact','来源观点','摘要','测试',:key,'published',now(),:fp,
            CAST(:body AS jsonb),'{}'::jsonb) RETURNING *""",
                     ws=seeded['workspace_id'], key='update-' + str(uuid.uuid4()),
                     fp=digest(str(body)), body=__import__('json').dumps(body, ensure_ascii=False))
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
    assert worker.tick() is True
    job = api.get('/api/v1/jobs/' + response.json()['job_id']).json()
    return job, response.json()


def review(api, record, decision='accept', reason='已核对材料范围、条件与缺口', fingerprint=None):
    return api.post('/api/v1/verification-records/%s/review' % record['id'],
                    json={'decision': decision, 'reason': reason,
                          'expected_input_fingerprint': fingerprint or record['input_fingerprint']})


# --------------------------------------------------------- integration: material

def test_evidence_link_requires_anchor_and_role_explanation(api):
    seeded = seed(api)
    version_id = seeded['version']['id']
    missing_note = post_material(api, seeded['claim']['id'], version_id)
    assert missing_note.status_code == 422
    assert missing_note.json()['error']['code'] == 'MATERIAL_ROLE_EXPLANATION_REQUIRED'
    created = post_material(api, seeded['claim']['id'], version_id,
                            note='同一版本包含可检查的测试记录，因此也作为事实材料')
    assert created.status_code == 201, created.text
    record = created.json()
    assert record['status'] == 'proposed' and record['relation_type'] == 'fact_supported_by'
    anchor = record['payload']['anchor']
    text = seeded['segments'][0]['text']
    assert text[anchor['char_start']:anchor['char_end']] == QUOTE
    assert anchor['quote_hash'] == digest(QUOTE)
    duplicate = post_material(api, seeded['claim']['id'], version_id,
                              note='同一版本包含可检查的测试记录，因此也作为事实材料')
    assert duplicate.status_code == 201
    assert duplicate.json()['deduplicated'] is True and duplicate.json()['id'] == record['id']
    unknown_quote = post_material(api, seeded['claim']['id'], version_id,
                                  quote='并不存在的句子', note='说明说明说明说明说明')
    assert unknown_quote.status_code == 422
    assert unknown_quote.json()['error']['code'] == 'QUOTE_NOT_FOUND'
    missing_version = api.post('/api/v1/claims/%s/evidence-links' % seeded['claim']['id'],
                               json=evidence_body(uuid.uuid4(), note='说明说明说明说明说明'))
    assert missing_version.status_code == 404
    detail = api.get('/api/v1/claims/%s' % seeded['claim']['id']).json()
    assert len(detail['fact_evidence']) == 1
    assert detail['fact_evidence'][0]['role'] == 'support'
    assert detail['fact_evidence'][0]['tier'] == 'E1'
    assert detail['fact_evidence'][0]['status'] == 'proposed'


def test_evidence_link_limit_is_enforced(api):
    lines = [f'材料第{index}条说明可用于核查' for index in range(1, 22)]
    seeded = seed(api, content='\n\n'.join(lines))
    version_id = seeded['version']['id']
    statuses = []
    for index in range(1, 22):
        response = post_material(api, seeded['claim']['id'], version_id,
                                 quote=f'材料第{index}条说明', segment=f'seg_{index}',
                                 note='同版本材料说明说明说明')
        statuses.append(response.status_code)
    assert statuses.count(201) == 20
    assert statuses[-1] == 422
    assert response.json()['error']['code'] == 'EVIDENCE_LIMIT_REACHED'


# ------------------------------------------------------ integration: fact check

def test_fact_check_without_materials_stays_pending_and_appends_versions(api):
    seeded = seed(api)
    claim_id = seeded['claim']['id']
    job, queued = run_fact_check(api, claim_id)
    assert queued['retrieval_mode'] == 'kb_only'
    assert job['state'] == 'succeeded', job
    assert job['result']['fact_confirmation'] is False
    assert job['result']['human_review'] == 'pending'
    assert job['result']['status'] == 'insufficient'
    records = api.get('/api/v1/claims/%s/fact-checks' % claim_id).json()
    assert len(records['items']) == 1
    record = records['items'][0]
    assert record['review_status'] == 'pending' and record['checker_type'] == 'rule'
    assert record['evidence_snapshot']['material_ids'] == []
    assert record['retrieval_manifest']['mode'] == 'kb_only'
    assert record['retrieval_manifest']['external_reads'] == []
    assert record['retrieval_manifest']['model_route'] == 'not_wired_in_this_phase'
    assert record['assessment']['status'] == 'insufficient'
    detail = api.get('/api/v1/claims/%s' % claim_id).json()
    assert detail['fact_check_status'] == 'unchecked'
    assert detail['pending_verification_records'][0]['id'] == record['id']
    accepted = review(api, record)
    assert accepted.status_code == 200, accepted.text
    payload = accepted.json()
    assert payload['record']['review_status'] == 'accepted'
    assert payload['record']['checker_type'] == 'human'
    assert payload['claim']['fact_check_status'] == 'insufficient'
    assert payload['claim']['verification_validity'] == 'current'
    detail = api.get('/api/v1/claims/%s' % claim_id).json()
    assert detail['fact_check_status'] == 'insufficient'
    assert detail['current_verification']['version_no'] == 2
    assert detail['current_verification']['review_status'] == 'accepted'
    again = review(api, record)
    assert again.status_code == 409
    assert again.json()['error']['code'] == 'VERIFICATION_ALREADY_REVIEWED'


def test_accepting_a_corroborated_record_links_materials(api):
    seeded = seed(api)
    claim_id = seeded['claim']['id']
    created = post_material(api, claim_id, seeded['version']['id'], conditions='matched',
                            note='同一版本包含可检查的测试记录，因此也作为事实材料')
    assert created.status_code == 201, created.text
    job, _ = run_fact_check(api, claim_id)
    assert job['result']['status'] == 'corroborated'
    record = api.get('/api/v1/claims/%s/fact-checks' % claim_id).json()['items'][0]
    assert record['assessment']['support_strength'] == 'E2'
    assert record['assessment']['human_review_required'] is True
    assert record['assessment']['auto_accept_allowed'] is False
    accepted = review(api, record)
    assert accepted.status_code == 200, accepted.text
    detail = api.get('/api/v1/claims/%s' % claim_id).json()
    assert detail['fact_check_status'] == 'corroborated'
    assert detail['verification_validity'] == 'current'
    material_row = detail['fact_evidence'][0]
    assert material_row['status'] == 'accepted'
    assert material_row['payload']['assessment_record_id'] == accepted.json()['record']['id']
    with api.engine.connect() as conn:
        audit = many(conn, "SELECT * FROM audit_events WHERE action='verification.review'")
        assert len(audit) == 1
        assert audit[0]['details']['decision'] == 'accept'


def test_rejecting_a_record_keeps_claim_and_materials_proposed(api):
    seeded = seed(api)
    claim_id = seeded['claim']['id']
    post_material(api, claim_id, seeded['version']['id'],
                  note='同一版本包含可检查的测试记录，因此也作为事实材料')
    run_fact_check(api, claim_id)
    record = api.get('/api/v1/claims/%s/fact-checks' % claim_id).json()['items'][0]
    rejected = review(api, record, decision='reject', reason='材料范围与主张范围不一致')
    assert rejected.status_code == 200, rejected.text
    detail = api.get('/api/v1/claims/%s' % claim_id).json()
    assert detail['fact_check_status'] == 'unchecked'
    assert detail['current_verification_id'] is None
    assert detail['fact_evidence'][0]['status'] == 'proposed'
    records = api.get('/api/v1/claims/%s/fact-checks' % claim_id).json()['items']
    assert [item['version_no'] for item in records] == [2, 1]
    assert records[0]['review_status'] == 'rejected'


def test_review_rejects_stale_input_and_wrong_fingerprint(api):
    seeded = seed(api)
    claim_id = seeded['claim']['id']
    post_material(api, claim_id, seeded['version']['id'], conditions='matched',
                  note='同一版本包含可检查的测试记录，因此也作为事实材料')
    run_fact_check(api, claim_id)
    record = api.get('/api/v1/claims/%s/fact-checks' % claim_id).json()['items'][0]
    wrong = review(api, record, fingerprint='0' * 64)
    assert wrong.status_code == 409
    assert wrong.json()['error']['code'] == 'STALE_VERIFICATION_INPUT'
    post_material(api, claim_id, seeded['version']['id'], relation='refute',
                  quote='该版本安装包大小为 42 MB', segment='seg_2', integrity='unverified',
                  conditions='matched', note='第二条材料说明说明说明说明')
    stale = review(api, record)
    assert stale.status_code == 409
    assert stale.json()['error']['code'] == 'STALE_VERIFICATION_INPUT'
    refreshed = api.post('/api/v1/claims/%s/fact-checks' % claim_id).json()
    assert refreshed['deduplicated'] is False
    assert worker.tick() is True
    latest = api.get('/api/v1/claims/%s/fact-checks' % claim_id).json()['items'][0]
    accepted = review(api, latest)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()['claim']['fact_check_status'] == 'conflicted'


def test_important_fact_requires_e3_before_acceptance(api):
    seeded = seed(api, importance='high')
    claim_id = seeded['claim']['id']
    post_material(api, claim_id, seeded['version']['id'], conditions='matched',
                  note='同一版本包含可检查的测试记录，因此也作为事实材料')
    run_fact_check(api, claim_id)
    record = api.get('/api/v1/claims/%s/fact-checks' % claim_id).json()['items'][0]
    assert record['gate'] == ['IMPORTANT_FACT_REQUIRES_E3']
    blocked = review(api, record)
    assert blocked.status_code == 422
    error = blocked.json()['error']
    assert error['code'] == 'PUBLICATION_GATE_BLOCKED'
    assert 'IMPORTANT_FACT_REQUIRES_E3' in error['details']['reason_codes']
    detail = api.get('/api/v1/claims/%s' % claim_id).json()
    assert detail['fact_check_status'] == 'unchecked'


def test_gate_blocks_a_corroborated_record_with_e1_material(api):
    seeded = seed(api)
    claim_id = seeded['claim']['id']
    post_material(api, claim_id, seeded['version']['id'],
                  note='同一版本包含可检查的测试记录，因此也作为事实材料')
    run_fact_check(api, claim_id)
    record = api.get('/api/v1/claims/%s/fact-checks' % claim_id).json()['items'][0]
    with api.engine.begin() as conn:
        forged = one(conn, """INSERT INTO verification_records(workspace_id,record_kind,claim_id,
            version_no,policy_version,input_fingerprint,target_revision,trigger,checker_type,
            retrieval_manifest,evidence_snapshot,applicability,assessment,review_status)
            VALUES(:ws,'claim_fact_check',:cid,:no,'test',:fp,1,'manual','rule',
            '{}'::jsonb,'{}'::jsonb,'{}'::jsonb,CAST(:assessment AS jsonb),'pending') RETURNING *""",
                     ws=seeded['workspace_id'], cid=claim_id, no=9,
                     fp=seeded['claim']['fingerprint'],
                     assessment=__import__('json').dumps({
                         'status': 'corroborated', 'support_strength': 'E1',
                         'refutation_strength': 'E0', 'unresolved_counterevidence': False}))
    blocked = review(api, forged, fingerprint=seeded['claim']['fingerprint'])
    assert blocked.status_code == 422
    assert 'EVIDENCE_TIER_INSUFFICIENT' in blocked.json()['error']['details']['reason_codes']


# ------------------------------------------------- integration: publication gate

def test_publication_gate_hides_scoped_fact_until_record_is_accepted(api):
    seeded = seed(api)
    claim_id = seeded['claim']['id']
    anchor = add_evidence_anchor(api, seeded)
    add_update(api, seeded, anchor, mode='scoped_fact')
    assert api.get('/api/v1/updates').json()['items'] == []
    post_material(api, claim_id, seeded['version']['id'], conditions='matched',
                  note='同一版本包含可检查的测试记录，因此也作为事实材料')
    run_fact_check(api, claim_id)
    record = api.get('/api/v1/claims/%s/fact-checks' % claim_id).json()['items'][0]
    accepted = review(api, record)
    assert accepted.status_code == 200, accepted.text
    feed = api.get('/api/v1/updates').json()['items']
    assert len(feed) == 1
    assert feed[0]['structured_body'][0]['publication_mode'] == 'scoped_fact'
    preview = api.post('/api/v1/updates/%s/publication-preview' % feed[0]['id'],
                       json={'surface': 'feed', 'expected_revision': 1}).json()
    assert preview['blocked'] is False
    assert preview['sentences'][0]['publication_mode'] == 'scoped_fact'
    assert preview['sentences'][0]['mode_still_allowed'] is True


def test_accepted_insufficient_record_keeps_attributed_sentence_visible(api):
    seeded = seed(api)
    claim_id = seeded['claim']['id']
    anchor = add_evidence_anchor(api, seeded)
    add_update(api, seeded, anchor, mode='attributed_unverified')
    run_fact_check(api, claim_id)
    record = api.get('/api/v1/claims/%s/fact-checks' % claim_id).json()['items'][0]
    assert review(api, record).status_code == 200
    feed = api.get('/api/v1/updates').json()['items']
    assert len(feed) == 1
    preview = api.post('/api/v1/updates/%s/publication-preview' % feed[0]['id'],
                       json={'surface': 'feed'}).json()
    assert preview['blocked'] is False
    assert preview['sentences'][0]['publication_mode'] == 'attributed_unverified'
    stale = api.post('/api/v1/updates/%s/publication-preview' % feed[0]['id'],
                     json={'surface': 'feed', 'expected_revision': 99})
    assert stale.status_code == 409


def test_scoped_fact_is_not_upgraded_by_material_acceptance_alone(api):
    """Accepting material for one claim does not rewrite unrelated sentences."""
    seeded = seed(api)
    other = seed(api, statement='该版本安装包大小为 42 MB，可以离线安装。')
    anchor = add_evidence_anchor(api, other)
    add_update(api, other, anchor, mode='attributed_unverified')
    post_material(api, seeded['claim']['id'], seeded['version']['id'],
                  note='同一版本包含可检查的测试记录，因此也作为事实材料')
    run_fact_check(api, seeded['claim']['id'])
    record = api.get('/api/v1/claims/%s/fact-checks' % seeded['claim']['id']).json()['items'][0]
    assert review(api, record).status_code == 200
    feed = api.get('/api/v1/updates').json()['items']
    assert len(feed) == 1
    assert feed[0]['structured_body'][0]['publication_mode'] == 'attributed_unverified'


# ------------------------------------------------------- integration: operations

def test_verification_queue_filters_and_dedupes_jobs(api):
    seeded = seed(api)
    claim_id = seeded['claim']['id']
    responses = [api.post('/api/v1/claims/%s/fact-checks' % claim_id) for _ in range(2)]
    assert responses[0].status_code == 202
    assert responses[1].json()['deduplicated'] is True
    assert responses[1].json()['job_id'] == responses[0].json()['job_id']
    assert worker.tick() is True
    pending = api.get('/api/v1/verification-queue?review=pending').json()['items']
    assert [str(item['id']) for item in pending] == [str(claim_id)]
    assert pending[0]['pending_reviews'] == 1
    assert pending[0]['latest_review_status'] == 'pending'
    assert api.get('/api/v1/verification-queue?status=corroborated').json()['items'] == []
    invalid = api.get('/api/v1/verification-queue?status=validated')
    assert invalid.status_code == 400


def test_capabilities_and_reliability_are_honest(api):
    seeded = seed(api)
    caps = api.get('/api/v1/verification-capabilities').json()
    assert caps['retrieval']['mode'] == 'kb_only'
    assert caps['retrieval']['external_read_enabled'] is False
    assert caps['records']['model_route'].startswith('fact_check 模型路由可作为可选建议接入')
    assert caps['fact_check_route']['mode'] == 'advisory_only'
    assert caps['fact_check_route']['changes_tier_or_status'] is False
    assert caps['review']['auto_accept_allowed'] is False
    assert caps['live_fact_check_verified'] is False
    reliability = api.get('/api/v1/sources/%s/reliability' % seeded['source']['id']).json()
    assert reliability['reliability_validity'] == 'unknown'
    assert reliability['dimensions']['official_identity'] == 'unknown'
    assert api.get('/api/v1/sources/%s/reliability' % uuid.uuid4()).status_code == 404
    capabilities = api.get('/api/v1/capabilities').json()
    assert capabilities['fact_verification']['human_review_required'] is True
    assert capabilities['fact_verification']['live_fact_check_verified'] is False
    assert capabilities['external_verification'] == 'kb_only'


def test_fact_check_for_unavailable_claim_fails_without_writing(api):
    assert api.post('/api/v1/claims/%s/fact-checks' % uuid.uuid4()).status_code == 404
    seeded = seed(api)
    with api.engine.begin() as conn:
        job = one(conn, """INSERT INTO jobs(workspace_id,kind,input,config_snapshot,idempotency_key)
            VALUES(:ws,'fact_check',CAST(:input AS jsonb),'{}'::jsonb,:key) RETURNING id""",
                  ws=seeded['workspace_id'],
                  input=__import__('json').dumps({'claim_id': str(uuid.uuid4()), 'trigger': 'manual'}),
                  key='missing-claim-' + uuid.uuid4().hex)
    assert worker.tick() is True
    finished = api.get('/api/v1/jobs/' + str(job['id'])).json()
    assert finished['state'] == 'failed' and finished['error_code'] == 'CLAIM_UNAVAILABLE'
    with api.engine.connect() as conn:
        assert one(conn, 'SELECT count(*) AS n FROM verification_records')['n'] == 0


def test_verification_records_are_append_only(api):
    seeded = seed(api)
    run_fact_check(api, seeded['claim']['id'])
    record = api.get('/api/v1/claims/%s/fact-checks' % seeded['claim']['id']).json()['items'][0]
    with pytest.raises(Exception):
        with api.engine.begin() as conn:
            conn.execute(text("UPDATE verification_records SET review_status='accepted' WHERE id=:id"),
                         {'id': record['id']})
    with pytest.raises(Exception):
        with api.engine.begin() as conn:
            conn.execute(text('DELETE FROM verification_records WHERE id=:id'), {'id': record['id']})
