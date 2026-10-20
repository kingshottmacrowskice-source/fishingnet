"""Server-side normalisation for claim -> source_version *fact* evidence links.

The model (or the user) may propose that a stored version of a source supports,
refutes or gives context for a claim. Only the server decides the anchor offsets,
the quote hash and the payload schema; a proposal never becomes usable evidence
until a human accepts a verification record that used it.

Requirement references: 6.5 (versioned fact evidence payload), 6.6 (records) and
12.4a (``POST /claims/{id}/evidence-links``).
"""
import re

from app.domain.evidence import digest

SCHEMA_VERSION = 'fact_evidence.v1'
RELATIONS = {'support': 'fact_supported_by',
             'refute': 'fact_refuted_by',
             'context': 'fact_context_by'}
RELATION_KINDS = {value: key for key, value in RELATIONS.items()}
DIRECTNESS = ('direct', 'indirect')
INDEPENDENCE = ('confirmed', 'shared_origin', 'unknown')
ARTIFACT_INTEGRITY = ('verified', 'unverified', 'suspected_manipulation')
APPLICABILITY_FIELDS = ('product_version', 'date', 'environment', 'region', 'method')
APPLICABILITY_VALUES = ('matched', 'mismatched', 'unknown')
TRANSPARENCY_FIELDS = ('method', 'version', 'sample', 'code', 'raw_data', 'logs', 'limitations')
MAX_LIMITATIONS = 10
MAX_LIMITATION_CHARS = 300
_CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')


class FactEvidenceError(Exception):
    """A user- or model-supplied fact material failed server-side validation."""

    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def _clean_text(value, label, *, low, high):
    if not isinstance(value, str):
        raise FactEvidenceError('MATERIAL_INVALID', f'{label}必须是文本')
    text = value.strip()
    if _CONTROL.search(text):
        raise FactEvidenceError('MATERIAL_INVALID', f'{label}包含不可见控制字符')
    if not low <= len(text) <= high:
        raise FactEvidenceError('MATERIAL_INVALID', f'{label}长度必须在 {low} 到 {high} 个字符之间')
    return text


def _enum(value, label, allowed):
    if value not in allowed:
        raise FactEvidenceError('MATERIAL_INVALID', f'{label}必须是 {"/".join(allowed)} 之一',
                                {'field': label, 'allowed': list(allowed)})
    return value


def normalize_applicability(value):
    """Keep the five conditions explicit; a missing condition is ``unknown``, never matched."""
    if value is None:
        return {field: 'unknown' for field in APPLICABILITY_FIELDS}
    if not isinstance(value, dict):
        raise FactEvidenceError('MATERIAL_INVALID', '适用条件必须是对象')
    unknown = set(value) - set(APPLICABILITY_FIELDS)
    if unknown:
        raise FactEvidenceError('MATERIAL_INVALID', '适用条件包含未知字段',
                                {'unknown_fields': sorted(unknown)})
    return {field: _enum(value.get(field, 'unknown'), f'适用条件 {field}', APPLICABILITY_VALUES)
            for field in APPLICABILITY_FIELDS}


def normalize_transparency(value):
    """Record what the material discloses; unknown disclosure never counts as verified."""
    if value is None:
        return {field: None for field in TRANSPARENCY_FIELDS}
    if not isinstance(value, dict):
        raise FactEvidenceError('MATERIAL_INVALID', '材料透明度必须是对象')
    unknown = set(value) - set(TRANSPARENCY_FIELDS)
    if unknown:
        raise FactEvidenceError('MATERIAL_INVALID', '材料透明度包含未知字段',
                                {'unknown_fields': sorted(unknown)})
    result = {}
    for field in TRANSPARENCY_FIELDS:
        item = value.get(field)
        if item is not None and not isinstance(item, bool):
            raise FactEvidenceError('MATERIAL_INVALID', f'材料透明度 {field} 必须是布尔值或空')
        result[field] = item
    return result


def normalize_limitations(values):
    if values is None:
        return []
    if not isinstance(values, list):
        raise FactEvidenceError('MATERIAL_INVALID', '限制必须是列表')
    if len(values) > MAX_LIMITATIONS:
        raise FactEvidenceError('MATERIAL_INVALID', f'限制最多 {MAX_LIMITATIONS} 条')
    return [_clean_text(item, '限制条目', low=1, high=MAX_LIMITATION_CHARS) for item in values]


def build_anchor(segments, segment_id, quote):
    """Find the quote in the stored segment and hash it; offsets are never trusted."""
    if not isinstance(segment_id, str) or not segment_id.strip():
        raise FactEvidenceError('MATERIAL_INVALID', '必须提供原文片段')
    segment = next((item for item in segments or [] if item.get('segment_id') == segment_id), None)
    if not segment:
        raise FactEvidenceError('UNKNOWN_SEGMENT', '该原文片段不属于所选来源版本',
                                {'segment_id': segment_id})
    text = segment.get('text') or ''
    start = text.find(quote)
    if start < 0:
        raise FactEvidenceError('QUOTE_NOT_FOUND', '引文必须逐字出现在该原文片段中',
                                {'segment_id': segment_id})
    if text.find(quote, start + 1) >= 0:
        # Ambiguous quotes cannot be located unambiguously; ask for a unique span.
        raise FactEvidenceError('QUOTE_AMBIGUOUS', '引文在该片段中出现多次，请提供更长的唯一片段',
                                {'segment_id': segment_id})
    end = start + len(quote)
    return {'segment_id': segment_id, 'char_start': start, 'char_end': end,
            'quote': quote, 'quote_hash': digest(quote)}


def normalize_link(*, body, segments, source_id):
    """Build the versioned payload for one proposed fact evidence relation."""
    relation = _enum(body.get('relation', 'support'), '材料作用', tuple(RELATIONS))
    segment_id = _clean_text(body.get('segment_id'), '原文片段', low=1, high=60)
    quote = _clean_text(body.get('quote'), '引文', low=2, high=500)
    scope = _clean_text(body.get('proposition_scope'), '命题范围', low=2, high=600)
    directness = _enum(body.get('directness', 'direct'), '直接性', DIRECTNESS)
    independence = _enum(body.get('independence_status', 'unknown'), '独立性', INDEPENDENCE)
    integrity = _enum(body.get('artifact_integrity', 'unverified'), '材料完整性', ARTIFACT_INTEGRITY)
    origin_group = body.get('origin_group_id')
    if origin_group is None:
        origin_group = f'source:{source_id}'
    origin_group = _clean_text(origin_group, '上游来源组', low=1, high=120)
    note = body.get('note')
    if note is not None:
        note = _clean_text(note, '说明', low=1, high=300)
    payload = {
        'schema_version': SCHEMA_VERSION,
        'anchor': build_anchor(segments, segment_id, quote),
        'proposition_scope': scope,
        'directness': directness,
        'origin_group_id': origin_group,
        'independence_status': independence,
        'applicability': normalize_applicability(body.get('applicability')),
        'artifact_integrity': integrity,
        'method_transparency': normalize_transparency(body.get('method_transparency')),
        'limitations': normalize_limitations(body.get('limitations')),
    }
    if note:
        payload['note'] = note
    return RELATIONS[relation], payload
