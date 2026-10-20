from datetime import datetime, timezone

POLICY_VERSION = 'publication.v1.3.0'
SURFACES = {'title', 'feed', 'detail', 'overview', 'digest', 'search', 'export'}
# Stronger claims must never be shown where the policy only allows a weaker mode.
# A stored update may keep a weaker mode after a record is accepted; it may not
# upgrade itself without being regenerated.
MODE_STRENGTH = {'opinion': 1, 'conditional_experience': 1, 'attributed_unverified': 1,
                 'dispute': 2, 'correction': 2, 'scoped_fact': 3}


def mode_is_within(recorded, allowed):
    """True when a stored publication mode does not claim more than ``allowed``."""
    if recorded == allowed:
        return True
    if recorded not in MODE_STRENGTH or allowed not in MODE_STRENGTH:
        return False
    return MODE_STRENGTH[recorded] <= MODE_STRENGTH[allowed]


class PublicationPolicy:
    """Fail closed. Extraction confidence, author identity and counts are never inputs."""
    @staticmethod
    def evaluate(surface, claim, record=None, evidence_valid=False):
        if surface not in SURFACES:
            raise ValueError('UNKNOWN_SURFACE')
        def result(action, mode, reason):
            return {'action': action, 'publication_mode': mode, 'reason_codes': [reason], 'policy_version': POLICY_VERSION}
        if not evidence_valid or claim.get('lifecycle_status') != 'active':
            return result('retract', None, 'EXPRESSION_EVIDENCE_UNAVAILABLE')
        kind = claim.get('claim_kind')
        # Pure opinions / bounded experiences must be separately classified, never model-authorized.
        if kind == 'opinion' and claim.get('verifiability') == 'not_verifiable':
            return result('allow', 'opinion', 'ATTRIBUTION_REQUIRED')
        current = record and claim.get('verification_validity') == 'current' and record.get('review_status') == 'accepted'
        if current:
            expiry = record.get('valid_until')
            if isinstance(expiry, str):
                expiry = datetime.fromisoformat(expiry)
            current = bool(expiry and expiry > datetime.now(timezone.utc))
        assessment = record.get('assessment', {}) if current else {}
        status = assessment.get('status', 'unchecked')
        high = claim.get('importance') == 'high'
        human = bool(current and record.get('checker_type') == 'human' and record.get('reviewer_id'))
        if status == 'corroborated' and assessment.get('support_strength') in ('E2', 'E3') and not assessment.get('unresolved_counterevidence', True):
            # Only a human-accepted, current record may lift a sentence to scoped_fact.
            # A legacy rule/model record that was never reviewed cannot inherit that
            # qualification (AC45), even for a non-high-importance claim.
            if human and (not high or (assessment.get('support_strength') == 'E3' and human)):
                return result('allow', 'scoped_fact', 'CURRENT_ACCEPTED_EVIDENCE')
        if status == 'refuted':
            return result('hold', None, 'CORRECTION_REQUIRES_REVIEW')
        if status == 'conflicted':
            return result('hold', None, 'DISPUTE_REQUIRES_REVIEW')
        if high and not (human and assessment.get('allow_attributed_unverified')):
            return result('hold', None, 'IMPORTANT_FACT_REVIEW_REQUIRED')
        if surface == 'overview':
            return result('hold', None, 'NO_VERIFIED_OVERVIEW')
        if kind == 'experience':
            return result('allow', 'conditional_experience', 'UNVERIFIED_BOUNDED_EXPERIENCE')
        return result('allow', 'attributed_unverified', 'FACT_NOT_VERIFIED')
