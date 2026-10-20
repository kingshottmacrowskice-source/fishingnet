"""AC32-45 blocking dataset and Phase 2 behaviour checks.

The dataset (``tests/fixtures/ac32-45.json``) holds 48 synthetic cases in six
categories plus 12 controls. Everything here is synthetic and runs against an isolated
PostgreSQL schema; the routed model is always ``httpx.MockTransport``. Passing these
tests does not mean a real external fact check or a real model call happened.
"""
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import app.jobs.worker as worker
from app.ai import extraction as ai_extraction
from app.ai import factcheck as ai_factcheck
from app.domain import verification
from app.domain.policy import PublicationPolicy
from app.domain.evidence import digest
from app.storage.db import execute, one

from m4_support import (
    add_evidence_anchor, add_second_source, add_update, advisory_handler, create_model,
    create_provider, install_transport, latest_record, post_material, review,
    run_fact_check, save_routes, seed, set_budget,
)

DATASET = json.loads(Path('tests/fixtures/ac32-45.json').read_text(encoding='utf8'))
CASES = DATASET['cases']
ROLE_TYPES = {'support': 'fact_supported_by', 'refute': 'fact_refuted_by',
              'context': 'fact_context_by'}
SURFACES = ('feed', 'detail', 'title', 'overview', 'digest', 'search', 'export')


def build_material(spec, index):
    payload = {
        'schema_version': 'fact_evidence.v1',
        'anchor': {'segment_id': 'seg_1', 'char_start': 0, 'char_end': 2,
                   'quote': '离线', 'quote_hash': digest('离线')},
        'proposition_scope': '该版本的离线索引可用',
        'directness': spec['directness'],
        'origin_group_id': spec['origin_group_id'],
        'independence_status': spec['independence_status'],
        'artifact_integrity': spec['artifact_integrity'],
        'applicability': spec['applicability'],
        'method_transparency': {},
        'limitations': spec['limitations'],
    }
    return {'id': 'mat-%02d' % index, 'relation_type': ROLE_TYPES[spec['role']],
            'status': 'proposed', 'payload': payload,
            'superseded': bool(spec['superseded']),
            'retention_ok': spec['retention'] != 'expired'}


def build_claim(case):
    claim = dict(case['claim'])
    claim.update({'id': 'claim-' + case['id'], 'lifecycle_status': 'active',
                  'revision': 1, 'fingerprint': 'f' * 64,
                  'verification_validity': 'current'})
    return claim


def accepted_record(assessment):
    """A current, human-accepted record wrapping this assessment."""
    return {'review_status': 'accepted', 'checker_type': 'human',
            'reviewer_id': str(uuid.uuid4()),
            'valid_until': (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            'assessment': assessment}


@pytest.mark.parametrize('case', CASES, ids=[case['id'] for case in CASES])
def test_ac_cases_are_blocked_by_the_deterministic_gate(case):
    materials = [build_material(spec, index + 1)
                 for index, spec in enumerate(case['materials'])]
    claim = build_claim(case)
    assessment = verification.assess(claim=claim, materials=materials)
    expect = case['expect']
    assert assessment['status'] == expect['status'], case['title']
    assert assessment['support_strength'] == expect['support_strength'], case['title']
    assert assessment['refutation_strength'] == expect['refutation_strength'], case['title']
    assert sorted(verification.accept_gate(claim, assessment)) == sorted(expect['gate']), case['title']
    assert assessment['auto_accept_allowed'] is False
    assert assessment['human_review_required'] is True
    record = accepted_record(assessment)
    for surface in SURFACES:
        expected = expect['policy'][surface]
        decision = PublicationPolicy.evaluate(surface, claim, record, True)
        assert [decision['action'], decision['publication_mode']] == expected, (
            case['id'], surface, decision)
        if expected[1] is not None:
            assert expected[1] not in expect['must_not_show'], (case['id'], surface)


def test_dataset_has_the_required_case_mix():
    categories = {}
    for case in CASES:
        categories.setdefault(case['category'], []).append(case['id'])
    assert len(categories['false_feature_advertising']) == 8
    assert len(categories['truncated_quote']) == 8
    assert len(categories['expired_version']) == 8
    assert len(categories['circular_repost']) == 8
    assert len(categories['forged_review']) == 8
    assert len(categories['mixed_criticism']) == 8
    controls = sum(len(categories[name]) for name in
                   ('true_feature', 'applicable_refutation',
                    'low_reliability_strong_evidence', 'asymmetric_dispute'))
    assert sum(len(items) for items in categories.values()) == 60
    assert controls == 12
    referenced = {ac for case in CASES for ac in case['ac']}
    referenced |= set(DATASET['ac_coverage'])
    for number in range(32, 46):
        assert 'AC%d' % number in referenced, 'AC%d is not represented' % number


# --------------------------------------------------------------- AC33 expression

@pytest.mark.parametrize('case', [case for case in CASES if case.get('expression')],
                         ids=[case['id'] for case in CASES if case.get('expression')])
def test_truncated_quote_cases_are_caught_or_allowed(case):
    expression = case['expression']
    text = expression['text']
    parsed = {'claims': [{'statement': case['claim']['statement'], 'kind': 'factual_assertion',
                          'aspect': 'compatibility', 'polarity': 'affirm',
                          'importance': 'medium', 'verifiability': 'verifiable',
                          'evidence': [{'segment_id': 'seg_1', 'quote': expression['quote']}]}]}
    result = ai_extraction.validate_claim_output(
        parsed, [{'segment_id': 'seg_1', 'text': text}],
        {'included': {'seg_1': len(text)}, 'truncated': False})
    expected = expression['expect']
    if expected['result'] == 'accepted':
        assert len(result['claims']) == 1
        assert result['unverified'] == []
        assert result['claims'][0]['anchors'][0]['quote'] == expression['quote']
    else:
        assert result['claims'] == []
        assert result['unverified'][0]['reason'] == expected['reason']
        assert result['unverified'][0]['anchors'][0]['context_flags'] == ['TRUNCATED_LIMITATION']


def test_truncated_quote_detection_is_conservative():
    assert ai_extraction.truncated_limitation('支持离线，正式版不支持。', 0, 4) is True
    assert ai_extraction.truncated_limitation('支持离线。正式版不支持。', 0, 4) is False
    assert ai_extraction.truncated_limitation('支持离线，用户反馈良好。', 0, 4) is False


# ----------------------------------------------------------------- AC37 pairing

def test_true_and_false_criticism_are_assessed_independently():
    pairs = {}
    for case in CASES:
        if case.get('pair'):
            pairs.setdefault(case['pair'], []).append(case)
    assert len(pairs) == 4
    for group in pairs.values():
        assert len(group) == 2
        statuses = []
        for case in group:
            materials = [build_material(spec, index + 1)
                         for index, spec in enumerate(case['materials'])]
            statuses.append(verification.assess(
                claim=build_claim(case), materials=materials)['status'])
        assert sorted(statuses) == ['corroborated', 'refuted'], statuses


# ------------------------------------------------------------ deterministic units

def test_duplicate_reposts_never_upgrade_the_tier():
    single = [{'id': 'a', 'relation_type': 'fact_supported_by', 'status': 'proposed',
               'payload': _payload('G1', 'shared_origin')}]
    many = [{'id': 'm%d' % index, 'relation_type': 'fact_supported_by',
             'status': 'proposed', 'payload': _payload('G1', 'shared_origin')}
            for index in range(10)]
    assert verification.assess(claim={'id': 'c', 'importance': 'medium'}, materials=single)[
        'support_strength'] == 'E2'
    assert verification.assess(claim={'id': 'c', 'importance': 'medium'}, materials=many)[
        'support_strength'] == 'E2'


def _payload(origin, independence):
    return {'schema_version': 'fact_evidence.v1',
            'anchor': {'segment_id': 'seg_1', 'char_start': 0, 'char_end': 1,
                       'quote': '离', 'quote_hash': digest('离')},
            'proposition_scope': '范围', 'directness': 'direct',
            'origin_group_id': origin, 'independence_status': independence,
            'artifact_integrity': 'verified',
            'applicability': {field: 'matched' for field in
                              ('product_version', 'date', 'environment', 'region', 'method')},
            'method_transparency': {}, 'limitations': []}


def test_extraction_confidence_and_counts_are_not_truth_inputs():
    base_claim = {'id': 'c', 'importance': 'medium'}
    confident = dict(base_claim, extraction_confidence=1.0)
    materials = [{'id': 'a', 'relation_type': 'fact_supported_by', 'status': 'proposed',
                  'payload': _payload('G1', 'unknown')}]
    first = verification.assess(claim=base_claim, materials=materials)
    second = verification.assess(claim=confident, materials=materials)
    assert first == second
    assert first['support_strength'] == 'E2'


def test_asymmetric_dispute_keeps_strength_and_gap():
    materials = [
        {'id': 'support', 'relation_type': 'fact_supported_by', 'status': 'proposed',
         'payload': _payload('G1', 'unknown')},
        {'id': 'refute', 'relation_type': 'fact_refuted_by', 'status': 'proposed',
         'payload': dict(_payload('G2', 'unknown'), directness='indirect')},
    ]
    assessment = verification.assess(claim={'id': 'c', 'importance': 'medium'},
                                     materials=materials)
    assert assessment['status'] == 'conflicted'
    assert assessment['dispute_balance'] == 'support_stronger'
    assert any('不对称' in gap for gap in assessment['gaps'])


def test_legacy_rule_record_cannot_lift_a_sentence_to_scoped_fact():
    claim = {'id': 'c', 'importance': 'medium', 'claim_kind': 'factual_assertion',
             'verifiability': 'verifiable', 'lifecycle_status': 'active',
             'verification_validity': 'current'}
    assessment = {'status': 'corroborated', 'support_strength': 'E2',
                  'unresolved_counterevidence': False}
    legacy = {'review_status': 'accepted', 'checker_type': 'rule', 'assessment': assessment}
    human = {'review_status': 'accepted', 'checker_type': 'human',
             'reviewer_id': str(uuid.uuid4()), 'assessment': assessment,
             'valid_until': (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}
    assert PublicationPolicy.evaluate('feed', claim, legacy, True)['publication_mode'] == \
        'attributed_unverified'
    assert PublicationPolicy.evaluate('feed', claim, human, True)['publication_mode'] == \
        'scoped_fact'


def test_model_cannot_claim_external_reads():
    coverage = {'map': {'m1': 0}}
    with pytest.raises(ai_factcheck.FactCheckError) as error:
        ai_factcheck.validate_advisory(
            {'verdict': 'supports', 'reason': '我查阅了官网',
             'external_reads': ['https://example.com/doc']}, coverage)
    assert error.value.code == 'MODEL_CLAIMED_EXTERNAL_READS'
    with pytest.raises(ai_factcheck.FactCheckError):
        ai_factcheck.validate_advisory({'verdict': 'certain', 'reason': '确定'}, coverage)
    accepted = ai_factcheck.validate_advisory(
        {'verdict': 'supports', 'reason': '材料匹配', 'material_refs': ['m1', 'm9'],
         'confidence': 0.99}, coverage)
    assert accepted['verdict'] == 'supports' and accepted['confidence_ignored'] is True
    assert accepted['rejected'][0]['reason'] == 'UNKNOWN_MATERIAL'


# ------------------------------------------------------- AC34 superseded version

def test_new_source_version_caps_the_old_material_and_blocks_stale_review(api):
    seeded = seed(api)
    claim_id = seeded['claim']['id']
    created = post_material(api, claim_id, seeded['version']['id'], conditions='matched',
                            note='同一版本包含可检查的测试记录，因此也作为事实材料')
    assert created.status_code == 201, created.text
    job, _ = run_fact_check(api, claim_id)
    assert job['result']['status'] == 'corroborated'
    record = latest_record(api, claim_id)
    assert record['assessment']['support_strength'] == 'E2'

    with api.engine.begin() as conn:
        newer = one(conn, """INSERT INTO source_versions(workspace_id,source_id,version_no,raw_hash,
            normalized_hash,segments) VALUES(:ws,:sid,2,:raw,:norm,CAST(:seg AS jsonb)) RETURNING *""",
                    ws=seeded['workspace_id'], sid=seeded['source']['id'],
                    raw=digest('v2'), norm=digest('v2'),
                    seg=json.dumps([{'segment_id': 'seg_1', 'text': 'v2 更新说明'}], ensure_ascii=False))
        execute(conn, 'UPDATE sources SET current_version_id=:v WHERE id=:id',
                v=newer['id'], id=seeded['source']['id'])

    stale = review(api, record)
    assert stale.status_code == 409
    assert stale.json()['error']['code'] == 'STALE_VERIFICATION_INPUT'
    job, _ = run_fact_check(api, claim_id)
    assert job['result']['status'] == 'insufficient'
    assert job['result']['support_strength'] == 'E1'
    assert job['result']['stale_support_materials'] == 1
    refreshed = latest_record(api, claim_id)
    assert refreshed['evidence_snapshot']['materials'][0]['superseded'] is True
    assert refreshed['evidence_snapshot']['materials'][0]['tier'] == 'E1'


# --------------------------------------------------------- AC42/AC43 publication

def test_accepting_a_conflict_record_withdraws_the_old_certainty_everywhere(api):
    seeded = seed(api)
    claim_id = seeded['claim']['id']
    relation = add_evidence_anchor(api, seeded)
    assert post_material(api, claim_id, seeded['version']['id'], conditions='matched',
                         note='同一版本包含可检查的测试记录，因此也作为事实材料').status_code == 201
    run_fact_check(api, claim_id)
    accepted = review(api, latest_record(api, claim_id))
    assert accepted.status_code == 200, accepted.text
    accepted_id = accepted.json()['record']['id']
    update = add_update(api, seeded, relation, mode='scoped_fact')
    assert api.get('/api/v1/updates').json()['items'], 'accepted fact should be visible'

    refute = post_material(api, claim_id, seeded['version']['id'], relation='refute',
                           directness='indirect', quote='离线索引运行正常',
                           note='同一版本包含可检查的测试记录，因此也作为事实材料')
    assert refute.status_code == 201, refute.text
    run_fact_check(api, claim_id)
    conflict = latest_record(api, claim_id)
    assert conflict['assessment']['status'] == 'conflicted'
    accepted_conflict = review(api, conflict)
    assert accepted_conflict.status_code == 200, accepted_conflict.text

    detail = api.get('/api/v1/claims/%s' % claim_id).json()
    assert detail['fact_check_status'] == 'conflicted'
    assert api.get('/api/v1/updates').json()['items'] == []
    assert api.get('/api/v1/updates/%s' % update['id']).status_code == 404
    assert api.get('/api/v1/digests/today').json()['items'] == []
    markdown = api.get('/api/v1/digests/today', params={'download': 'true'})
    assert accepted_id not in markdown.text
    history = api.get('/api/v1/claims/%s/fact-checks' % claim_id).json()['items']
    assert len(history) == 4
    old = api.get('/api/v1/verification-records/%s' % accepted_id)
    assert old.status_code == 200 and old.json()['review_status'] == 'accepted'
    assert history[0]['supersedes_id'] == conflict['id']
    assert history[1]['id'] == conflict['id']


def test_important_fact_is_hidden_from_every_surface_while_pending(api):
    seeded = seed(api, importance='high')
    claim_id = seeded['claim']['id']
    relation = add_evidence_anchor(api, seeded)
    assert post_material(api, claim_id, seeded['version']['id'], conditions='matched',
                         note='同一版本包含可检查的测试记录，因此也作为事实材料').status_code == 201
    second = add_second_source(api, seeded)
    assert post_material(api, claim_id, second['version']['id'], conditions='matched',
                         origin='group-b').status_code == 201
    job, _ = run_fact_check(api, claim_id)
    assert job['result']['status'] == 'corroborated'
    assert job['result']['support_strength'] == 'E3'
    update = add_update(api, seeded, relation, mode='scoped_fact')
    assert api.get('/api/v1/updates').json()['items'] == []
    assert api.get('/api/v1/updates/%s' % update['id']).status_code == 404
    assert api.get('/api/v1/digests/today').json()['items'] == []
    for surface in ('title', 'feed', 'detail', 'overview', 'digest', 'search', 'export'):
        preview = api.post('/api/v1/updates/%s/publication-preview' % update['id'],
                           json={'surface': surface})
        assert preview.status_code == 200, preview.text
        body = preview.json()
        assert body['blocked'] is True, (surface, body)
        assert all(sentence['action'] == 'hold' for sentence in body['sentences']), (surface, body)
    accepted = review(api, latest_record(api, claim_id))
    assert accepted.status_code == 200, accepted.text
    assert api.get('/api/v1/updates').json()['items']


# --------------------------------------------------- AC44 fact-check model route

def _fact_claim(api, *, conditions='matched', importance='medium'):
    seeded = seed(api, importance=importance, statement='Plugin X 2.0 支持离线索引。')
    created = post_material(api, seeded['claim']['id'], seeded['version']['id'],
                            conditions=conditions, quote='离线索引运行正常',
                            note='同一版本包含可检查的测试记录，因此也作为事实材料')
    assert created.status_code == 201, created.text
    return seeded


def test_fact_check_route_is_advisory_and_never_changes_the_grade(api):
    provider = create_provider(api)
    model = create_model(api, provider['id'])
    set_budget(api)
    save_routes(api, model['id'], ['fact_check'])
    install_transport(advisory_handler({
        'schema_version': 'fact_check_advisory.v1', 'verdict': 'refutes',
        'reason': '材料范围与主张不一致', 'limitations': ['材料只覆盖安装包'],
        'material_refs': ['m1']}))
    seeded = _fact_claim(api)
    claim_id = seeded['claim']['id']
    job, _ = run_fact_check(api, claim_id)
    assert job['state'] == 'succeeded', job
    assert job['result']['model_advisory'] == 'performed'
    assert job['result']['model_verdict'] == 'refutes'
    assert job['result']['model_agreement'] == 'disagrees'
    assert job['result']['status'] == 'corroborated'
    record = latest_record(api, claim_id)
    assert record['checker_type'] == 'rule' and record['review_status'] == 'pending'
    assert record['assessment']['status'] == 'corroborated'
    assert record['assessment']['support_strength'] == 'E2'
    assert record['retrieval_manifest']['mode'] == 'kb_only'
    assert record['retrieval_manifest']['external_reads'] == []
    run = record['model_runs'][0]
    assert run['status'] == 'performed' and run['verdict'] == 'refutes'
    assert run['agreement'] == 'disagrees' and run['confidence_ignored'] is False
    invocations = api.get('/api/v1/ai/invocations', params={'task': 'fact_check'}).json()
    assert invocations['items'][0]['status'] == 'succeeded'
    assert invocations['items'][0]['cost_status'] == 'estimated'
    accepted = review(api, record)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()['claim']['fact_check_status'] == 'corroborated'


def test_fact_check_route_budget_exhaustion_keeps_the_human_gate(api):
    provider = create_provider(api)
    model = create_model(api, provider['id'])
    set_budget(api, daily='0', monthly='0')
    save_routes(api, model['id'], ['fact_check'])
    calls = install_transport(advisory_handler({'verdict': 'supports', 'reason': '支持'}))
    seeded = _fact_claim(api, conditions='unknown')
    claim_id = seeded['claim']['id']
    job, _ = run_fact_check(api, claim_id)
    assert job['state'] == 'succeeded', job
    assert job['result']['model_advisory'] == 'unavailable'
    assert calls == []
    record = latest_record(api, claim_id)
    assert record['model_runs'][0]['status'] == 'unavailable'
    assert record['model_runs'][0]['error_code'] == 'BUDGET_EXHAUSTED'
    assert record['assessment']['status'] == 'insufficient'
    assert record['review_status'] == 'pending'
    blocked = api.get('/api/v1/ai/invocations', params={'task': 'fact_check'}).json()
    assert blocked['items'][0]['status'] == 'blocked'


def test_fact_check_route_rejects_claimed_external_reads(api):
    provider = create_provider(api)
    model = create_model(api, provider['id'])
    set_budget(api)
    save_routes(api, model['id'], ['fact_check'])
    install_transport(advisory_handler({
        'verdict': 'supports', 'reason': '我查阅了官方网站',
        'external_reads': ['https://example.com']}))
    seeded = _fact_claim(api)
    claim_id = seeded['claim']['id']
    job, _ = run_fact_check(api, claim_id)
    assert job['result']['model_advisory'] == 'invalid'
    record = latest_record(api, claim_id)
    assert record['model_runs'][0]['error_code'] == 'MODEL_CLAIMED_EXTERNAL_READS'
    assert record['retrieval_manifest']['external_reads'] == []
    assert record['assessment']['status'] == 'corroborated'


def test_fact_check_route_failure_does_not_hold_back_the_rule_record(api):
    provider = create_provider(api)
    model = create_model(api, provider['id'])
    set_budget(api)
    save_routes(api, model['id'], ['fact_check'])
    install_transport(advisory_handler({'verdict': 'supports'}, status=503))
    seeded = _fact_claim(api, conditions='unknown')
    job, _ = run_fact_check(api, seeded['claim']['id'])
    assert job['state'] == 'succeeded'
    assert job['result']['model_advisory'] == 'unavailable'
    record = latest_record(api, seeded['claim']['id'])
    assert record['model_runs'][0]['status'] == 'unavailable'
    assert record['assessment']['status'] == 'insufficient'


# ---------------------------------------------------------------- AC45 freshness

def test_stale_record_cannot_overwrite_a_newer_revision(api):
    seeded = seed(api)
    claim_id = seeded['claim']['id']
    run_fact_check(api, claim_id)
    record = latest_record(api, claim_id)
    with api.engine.begin() as conn:
        execute(conn, 'UPDATE claims SET revision=revision+1 WHERE id=:id', id=claim_id)
    stale = review(api, record)
    assert stale.status_code == 409
    assert stale.json()['error']['code'] == 'STALE_VERIFICATION_INPUT'
    with api.engine.connect() as conn:
        assert one(conn, 'SELECT count(*) AS n FROM verification_records')['n'] == 1


def test_fact_check_route_skips_a_claim_without_materials(api):
    provider = create_provider(api)
    model = create_model(api, provider['id'])
    set_budget(api)
    save_routes(api, model['id'], ['fact_check'])
    calls = install_transport(advisory_handler({'verdict': 'insufficient', 'reason': '无材料'}))
    seeded = seed(api)
    job, _ = run_fact_check(api, seeded['claim']['id'])
    assert job['state'] == 'succeeded'
    assert job['result']['model_advisory'] == 'skipped'
    assert calls == []
    record = latest_record(api, seeded['claim']['id'])
    assert record['model_runs'][0]['error_code'] == 'NO_FACT_MATERIALS'
    assert record['assessment']['status'] == 'insufficient'
    assert api.get('/api/v1/ai/invocations', params={'task': 'fact_check'}).json()['items'] == []


def test_verification_queue_surfaces_the_advisory_without_accepting_it(api):
    seeded = _fact_claim(api)
    run_fact_check(api, seeded['claim']['id'])
    queue = api.get('/api/v1/verification-queue').json()
    item = next(row for row in queue['items'] if row['id'] == str(seeded['claim']['id']))
    assert item['latest_review_status'] == 'pending'
    assert item['fact_check_status'] == 'unchecked'
