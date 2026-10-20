"""Explainable, deterministic fact-check assessment over stored materials.

Nothing here calls a model or the network. ``assess`` turns the *accepted* fact
evidence relations of one claim into a versioned assessment candidate; a human must
still accept it before it can change ``claims.fact_check_status`` or the publication
gate. Tiers follow requirement 6.5: E0 unusable / E1 limited indirect / E2
substantive support / E3 strong support. Counts, extraction confidence, author
identity and source popularity are never inputs.
"""
import json
from datetime import datetime, timedelta, timezone

from app.domain.evidence import digest

SCHEMA_VERSION = 'claim_fact_check.v1'
TIERS = ('E0', 'E1', 'E2', 'E3')
_TIER_ORDER = {tier: index for index, tier in enumerate(TIERS)}
STATUSES = ('unchecked', 'insufficient', 'corroborated', 'conflicted', 'refuted')
REVIEWABLE_STATUSES = STATUSES
HIGH_RECHECK_DAYS = 7
DEFAULT_RECHECK_DAYS = 30


class VerificationError(Exception):
    """The assessment itself cannot be built from the supplied material state."""

    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def _payload(relation):
    return (relation or {}).get('payload') or {}


def _conditions(payload):
    applicability = payload.get('applicability') or {}
    return [applicability.get(field, 'unknown') for field in
            ('product_version', 'date', 'environment', 'region', 'method')]


def _superseded(relation):
    """True when the material's source version is no longer the source's current one.

    A later version of the same source (for example a re-collected Bilibili page or a
    document edit) means the older snapshot cannot confirm a *current* statement. It
    stays usable as limited context (E1) but can never reach E2/E3 on its own, so an
    expired version cannot keep a stored conclusion alive (AC34).
    """
    return bool(relation.get('superseded'))


def _retention_expired(relation):
    if relation.get('retention_ok') is False:
        return True
    return bool(relation.get('retention_expired'))


def material_tier(relation):
    """Return ``(tier, reasons)`` for one stored material.

    A suspected-manipulation artifact, a mismatched key condition and an expired
    retention window are unusable (E0). Only a direct material whose five conditions
    are all matched, whose artifact integrity was verified *and* which still
    represents the source's current version reaches E2; everything readable but
    incomplete or superseded stays E1.
    """
    payload = _payload(relation)
    reasons = []
    if _retention_expired(relation):
        return 'E0', ['MATERIAL_RETENTION_EXPIRED']
    if payload.get('artifact_integrity') == 'suspected_manipulation':
        return 'E0', ['ARTIFACT_SUSPECTED_MANIPULATION']
    conditions = _conditions(payload)
    if 'mismatched' in conditions:
        return 'E0', ['APPLICABILITY_MISMATCH']
    direct = payload.get('directness') == 'direct'
    all_matched = all(value == 'matched' for value in conditions)
    verified = payload.get('artifact_integrity') == 'verified'
    superseded = _superseded(relation)
    if direct and all_matched and verified and not superseded:
        return 'E2', ['DIRECT_APPLICABLE_VERIFIED']
    if direct:
        if not all_matched:
            reasons.append('CONDITION_UNKNOWN_OR_PARTIAL')
        if not verified:
            reasons.append('ARTIFACT_INTEGRITY_UNVERIFIED')
        if superseded:
            reasons.append('SOURCE_VERSION_SUPERSEDED')
        return 'E1', reasons
    reasons.append('INDIRECT_MATERIAL')
    if superseded:
        reasons.append('SOURCE_VERSION_SUPERSEDED')
    return 'E1', reasons


def _strongest(materials):
    """Highest tier plus the requirements that kept it from reaching E3."""
    if not materials:
        return 'E0', ['NO_ACCEPTED_MATERIAL']
    tiers = [material_tier(item)[0] for item in materials]
    strongest = max(tiers, key=lambda tier: _TIER_ORDER[tier])
    reasons = []
    if strongest == 'E2':
        substantive = [item for item, tier in zip(materials, tiers)
                       if tier == 'E2' and _payload(item).get('artifact_integrity') == 'verified']
        groups = {_payload(item).get('origin_group_id') for item in substantive}
        independent = [item for item in substantive
                       if _payload(item).get('independence_status') in ('confirmed', 'unknown')]
        confirmed = any(_payload(item).get('independence_status') == 'confirmed'
                        for item in substantive)
        if len(substantive) < 2:
            reasons.append('E3_REQUIRES_INDEPENDENT_DIRECT_EVIDENCE')
        if len(groups) < 2:
            reasons.append('E3_REQUIRES_DISTINCT_ORIGIN_GROUPS')
        if not confirmed or not independent:
            reasons.append('E3_REQUIRES_CONFIRMED_INDEPENDENCE')
        if not reasons:
            strongest = 'E3'
    return strongest, reasons


def _counts(materials):
    counts = {tier: 0 for tier in TIERS}
    for item in materials:
        counts[material_tier(item)[0]] += 1
    return counts


def assess(*, claim, materials):
    """Build the assessment candidate for one claim.

    ``materials`` are the claim's stored fact evidence relations that are not
    rejected. Proposed materials participate in the candidate assessment because the
    human accepts the record and those relations together; until then they are only a
    proposal and cannot change ``fact_check_status`` or the publication gate. The
    result always requires human review: ``auto_accept_allowed`` is false.
    """
    materials = list(materials or [])
    accepted = [item for item in materials if item.get('status') == 'accepted']
    proposed = [item for item in materials if item.get('status') == 'proposed']
    support = [item for item in materials if item.get('relation_type') == 'fact_supported_by']
    refute = [item for item in materials if item.get('relation_type') == 'fact_refuted_by']
    context = [item for item in materials if item.get('relation_type') == 'fact_context_by']
    support_tier, support_reasons = _strongest(support)
    refute_tier, refute_reasons = _strongest(refute)
    gaps = []
    if refute_tier in ('E2', 'E3'):
        gaps.append('存在适用的反向材料，需人工判断强弱与范围')
    if support_tier == 'E0' and refute_tier == 'E0':
        status = 'insufficient'
        if materials:
            gaps.append('已有材料但均不可用或不适用')
        else:
            gaps.append('库内没有事实材料；kb_only 未做外部检索')
    elif support_tier in ('E2', 'E3') and refute_tier == 'E0':
        status = 'corroborated'
        gaps.append('支持材料已达 %s；接受本记录后才会提升表达' % support_tier)
    elif support_tier in ('E2', 'E3') and refute_tier in ('E1', 'E2'):
        status = 'conflicted'
    elif refute_tier == 'E3' and support_tier in ('E0', 'E1'):
        status = 'refuted'
        gaps.append('反向材料达 E3；只有在人工接受后才用于纠错表达')
    elif refute_tier == 'E3':
        status = 'conflicted'
        gaps.append('反向材料达 E3，但仍存在适用支持材料')
    elif support_tier == 'E1':
        status = 'insufficient'
        gaps.append('只有有限间接材料，未达到 E2')
    elif refute_tier in ('E1', 'E2'):
        status = 'insufficient'
        gaps.append('存在反向材料但不足以判定反驳')
    else:
        status = 'insufficient'
        gaps.append('未取得可检查的事实材料')
    if proposed:
        gaps.append('接受本记录时会一并接受 %d 条待确认的事实材料' % len(proposed))
    stale_support = [item for item in support if _superseded(item)]
    stale_refute = [item for item in refute if _superseded(item)]
    if stale_support:
        gaps.append('有 %d 条支持材料来自已被新版本取代的来源，不能用于确认当前版本' % len(stale_support))
    if stale_refute:
        gaps.append('有 %d 条反向材料来自已被新版本取代的来源，不能据此判定当前版本' % len(stale_refute))
    balance = None
    if support_tier in ('E1', 'E2', 'E3') and refute_tier in ('E1', 'E2', 'E3'):
        difference = _TIER_ORDER[support_tier] - _TIER_ORDER[refute_tier]
        balance = ('support_stronger' if difference > 0
                   else 'refutation_stronger' if difference < 0 else 'balanced')
        if balance != 'balanced':
            gaps.append('争议双方材料强弱不对称（支持 %s / 反向 %s），不可写成同等充分'
                        % (support_tier, refute_tier))
    assessment = {
        'schema_version': SCHEMA_VERSION,
        'status': status,
        'support_strength': support_tier,
        'refutation_strength': refute_tier,
        'support_counts': _counts(support),
        'refutation_counts': _counts(refute),
        'context_material_count': len(context),
        'material_counts': {'support': len(support), 'refute': len(refute),
                            'context': len(context), 'accepted': len(accepted),
                            'pending': len(proposed)},
        'tier_reasons': {'support': support_reasons, 'refutation': refute_reasons},
        'stale_support_materials': len(stale_support),
        'stale_refutation_materials': len(stale_refute),
        'dispute_balance': balance,
        'unresolved_counterevidence': refute_tier != 'E0',
        'gaps': gaps,
        'limitations': ['仅使用库内已保存材料，未做外部检索',
                        '模型与来源数量不能替代事实证据'],
        'human_review_required': True,
        'auto_accept_allowed': False,
    }
    return assessment


def accept_gate(claim, assessment):
    """Reason codes that block a human acceptance of this assessment (empty = allowed).

    The gate never turns "the human clicked accept" into a fact: it only prevents a
    record from claiming more support than the stored material provides.
    """
    status = assessment.get('status')
    support = assessment.get('support_strength', 'E0')
    refute = assessment.get('refutation_strength', 'E0')
    reasons = []
    if status not in STATUSES:
        return ['UNKNOWN_STATUS']
    if status == 'corroborated':
        if support not in ('E2', 'E3'):
            reasons.append('EVIDENCE_TIER_INSUFFICIENT')
        if claim.get('importance') == 'high' and support != 'E3':
            reasons.append('IMPORTANT_FACT_REQUIRES_E3')
    elif status == 'refuted':
        if refute != 'E3':
            reasons.append('REFUTATION_TIER_INSUFFICIENT')
        if support in ('E2', 'E3'):
            reasons.append('CONFLICTING_SUPPORT_PRESENT')
    elif status == 'conflicted':
        if support not in ('E1', 'E2', 'E3') or refute not in ('E1', 'E2', 'E3'):
            reasons.append('DISPUTE_WITHOUT_MATERIAL')
    return reasons


def valid_until(claim, now=None):
    """Re-check horizon: volatile/high-impact claims expire sooner than ordinary ones."""
    now = now or datetime.now(timezone.utc)
    days = HIGH_RECHECK_DAYS if claim.get('importance') == 'high' else DEFAULT_RECHECK_DAYS
    return now + timedelta(days=days)


def snapshot_hash(*, claim_id, materials):
    """Stable hash of the material state used by one assessment.

    Stored inside ``evidence_snapshot`` so a review is rejected when the relations
    changed after the record was produced, even though ``verification_records`` is
    append-only and ``input_fingerprint`` must stay equal to the claim fingerprint.
    """
    ordered = sorted(({'id': str(item.get('id')),
                       'relation_type': item.get('relation_type'),
                       'status': item.get('status'),
                       'superseded': _superseded(item),
                       'retention_expired': _retention_expired(item),
                       'payload': item.get('payload')}
                      for item in materials
                      if item.get('status') in ('accepted', 'proposed')),
                     key=lambda item: item['id'])
    return digest(json.dumps({'claim_id': str(claim_id), 'materials': ordered},
                             sort_keys=True, ensure_ascii=False, default=str))


def build_snapshot(*, claim, materials):
    """Evidence snapshot stored in a verification record (no raw source text).

    ``material_ids`` are the relations that acceptance would make usable: both already
    accepted ones and the proposals the record relies on.
    """
    usable = [item for item in materials if item.get('status') in ('accepted', 'proposed')]
    return {
        'schema_version': 'verification_snapshot.v1',
        'snapshot_hash': snapshot_hash(claim_id=claim['id'], materials=materials),
        'material_ids': [str(item['id']) for item in usable],
        'proposed_material_ids': [str(item['id']) for item in usable
                                  if item.get('status') == 'proposed'],
        'already_accepted_ids': [str(item['id']) for item in usable
                                 if item.get('status') == 'accepted'],
        'materials': [{'id': str(item['id']),
                       'relation_type': item.get('relation_type'),
                       'status': item.get('status'),
                       'source_version_id': str(item.get('to_id') or item.get('source_version_id')),
                       'tier': material_tier(item)[0],
                       'superseded': _superseded(item),
                       'retention_expired': _retention_expired(item),
                       'proposition_scope': _payload(item).get('proposition_scope'),
                       'directness': _payload(item).get('directness'),
                       'independence_status': _payload(item).get('independence_status'),
                       'origin_group_id': _payload(item).get('origin_group_id'),
                       'artifact_integrity': _payload(item).get('artifact_integrity'),
                       'applicability': _payload(item).get('applicability'),
                       'limitations': _payload(item).get('limitations') or [],
                       'anchor': _payload(item).get('anchor')}
                      for item in usable],
    }
