"""M1 offline extractor + M3 model gateway.

``AIGateway.extract`` stays the deterministic synthetic-fixture extractor: it never
calls a network and is the only path that writes demo claims. The M3 gateway below
is separate: it resolves a task route, reserves a worst-case budget amount, calls
one provider protocol outside any database transaction, settles the actual cost and
records an invocation ledger row. A blocked call (no route, no budget, policy) is
recorded as ``blocked`` and never silently falls back to a fake success.
"""

from __future__ import annotations

import json
import re
import time
from decimal import Decimal

from app.storage.db import engine, one, many, execute
from app.domain.evidence import digest
from app.domain.secrets import SecretError
from app.domain.model_secrets import open_api_key
from app.ai import budget, providers

TASKS = (
    ('screening', '初筛'),
    ('claim_extraction', '主张抽取'),
    ('entity_disambiguation', '实体消歧'),
    ('claim_comparison', '主张比较'),
    ('evidence_review', '表达证据复核'),
    ('fact_check', '事实核查'),
    ('update_generation', '动态生成'),
    ('digest_polish', '日报润色'),
)
TASK_KEYS = tuple(key for key, _ in TASKS)
TASK_LABELS = dict(TASKS)
MAX_FALLBACKS = 3
RETRY_DELAY_SECONDS = 0.5
CONNECTION_TEST_TASK = 'connection_test'
PROBE_LIMITS = {'max_input_tokens': 256, 'max_output_tokens': 128}


class GatewayError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False, details=None):
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}
        super().__init__(message)


class AIGateway:
    def extract(self, segments, *, demo=False):
        if not demo:
            return []  # Real content is retained for a configured extractor / human review.
        candidates = []
        for seg in segments:
            text = seg['text']
            # Exact synthetic fixture grammar only. No arbitrary instructions execute.
            if not text.startswith('【合成示例】Plugin X '):
                continue
            opinion = '我认为' in text
            experience = '我的测试环境' in text
            version = re.search(r'Plugin X (\d+\.\d+)', text)
            quote = text
            candidates.append({
                'statement': text, 'claim_kind': 'opinion' if opinion else ('experience' if experience else 'factual_assertion'),
                'aspect': 'experience' if opinion else 'functionality',
                'qualifiers': {'product_version': version.group(1) if version else 'unknown', 'scope': 'synthetic_fixture'},
                'polarity': 'deny' if '不能' in text or '缓慢' in text else 'affirm',
                'importance': 'low' if opinion else 'high', 'verifiability': 'not_verifiable' if opinion else 'verifiable',
                'entity': 'Plugin X', 'anchor': {'segment_id': seg['segment_id'], 'char_start': 0, 'char_end': len(quote), 'quote': quote, 'quote_hash': digest(quote)},
            })
        return candidates


# ------------------------------------------------------------------ routing

def default_route(task: str) -> dict:
    return {'task': task, 'label': TASK_LABELS.get(task, task), 'mode': 'mock', 'enabled': False,
            'primary_model_id': None, 'fallback_model_ids': [], 'primary_model_label': None,
            'max_input_tokens': 8000, 'max_output_tokens': 1024, 'timeout_seconds': 30,
            'max_attempts': 1, 'data_policy': 'local_only', 'revision': 0, 'configured': False}


def _model_label(row) -> str:
    if not row:
        return ''
    provider = row.get('provider_name') or row.get('provider_label') or ''
    model = row.get('display_name') or row.get('model_id') or ''
    return f'{provider} / {model}' if provider else model


def route_list(conn, workspace_id) -> list[dict]:
    """Canonical 8 tasks in a stable order, with stored rows merged over defaults."""
    rows = many(conn, """SELECT r.*, m.display_name AS primary_model_display,
          m.model_id AS primary_model_code, p.name AS primary_provider_name
      FROM task_routes r
      LEFT JOIN model_profiles m ON m.workspace_id=r.workspace_id AND m.id=r.primary_model_id
      LEFT JOIN provider_connections p ON p.workspace_id=m.workspace_id AND p.id=m.provider_id
      WHERE r.workspace_id=:ws""", ws=workspace_id)
    models = many(conn, """SELECT m.id,m.display_name,m.model_id,p.name AS provider_name,
          p.enabled AS provider_enabled, m.enabled AS model_enabled
      FROM model_profiles m JOIN provider_connections p
        ON p.workspace_id=m.workspace_id AND p.id=m.provider_id
      WHERE m.workspace_id=:ws""", ws=workspace_id)
    by_id = {str(item['id']): item for item in models}
    stored = {row['task']: row for row in rows}
    result = []
    for task in TASK_KEYS:
        base = default_route(task)
        row = stored.get(task)
        if not row:
            result.append(base)
            continue
        fallbacks = [str(value) for value in (row.get('fallback_model_ids') or [])]
        base.update({
            'mode': row['mode'], 'enabled': row['enabled'],
            'primary_model_id': str(row['primary_model_id']) if row['primary_model_id'] else None,
            'fallback_model_ids': fallbacks,
            'primary_model_label': _model_label({'provider_name': row['primary_provider_name'],
                                                 'display_name': row['primary_model_display'],
                                                 'model_id': row['primary_model_code']}) or None,
            'fallback_model_labels': [by_id[key]['display_name'] if key in by_id else '（已删除模型）'
                                      for key in fallbacks],
            'max_input_tokens': row['max_input_tokens'], 'max_output_tokens': row['max_output_tokens'],
            'timeout_seconds': row['timeout_seconds'], 'max_attempts': row['max_attempts'],
            'data_policy': row['data_policy'], 'revision': row['revision'], 'configured': True,
        })
        # A disabled or missing model is reported, not silently treated as ready.
        primary_ready = bool(row['primary_model_id']
                             and by_id.get(str(row['primary_model_id']), {}).get('model_enabled')
                             and by_id.get(str(row['primary_model_id']), {}).get('provider_enabled'))
        fallbacks_ready = all(by_id.get(key, {}).get('model_enabled')
                              and by_id.get(key, {}).get('provider_enabled') for key in fallbacks)
        if row['mode'] == 'model':
            base['ready'] = bool(row['enabled'] and primary_ready and fallbacks_ready)
        else:
            # rule/mock modes deliberately call no model; they are ready when enabled.
            base['ready'] = bool(row['enabled'])
        result.append(base)
    return result


def _candidate_rows(conn, workspace_id, model_ids):
    ids = list(dict.fromkeys(model_ids))
    if not ids:
        return []
    placeholders = ','.join(f':m{index}' for index in range(len(ids)))
    params = {f'm{index}': value for index, value in enumerate(ids)}
    rows = many(conn, f"""SELECT m.*, p.name AS provider_name, p.protocol, p.base_url, p.is_local,
          p.credential_sealed, p.enabled AS provider_enabled, p.status AS provider_status
      FROM model_profiles m JOIN provider_connections p
        ON p.workspace_id=m.workspace_id AND p.id=m.provider_id
      WHERE m.workspace_id=:ws AND m.id IN ({placeholders})""",
                ws=workspace_id, **params)
    by_id = {str(row['id']): row for row in rows}
    return [by_id[key] for key in ids if key in by_id]


def _plan(conn, workspace_id, task):
    workspace = one(conn, 'SELECT * FROM workspace_settings WHERE id=:ws', ws=workspace_id)
    if not workspace:
        raise GatewayError('WORKSPACE_NOT_FOUND', '工作区不存在')
    route = one(conn, 'SELECT * FROM task_routes WHERE workspace_id=:ws AND task=:task',
                ws=workspace_id, task=task)
    if not route:
        raise GatewayError('TASK_ROUTE_NOT_CONFIGURED',
                           f'任务「{TASK_LABELS.get(task, task)}」尚未配置模型路由')
    if not route['enabled'] or route['mode'] != 'model':
        raise GatewayError('TASK_ROUTE_DISABLED',
                           f'任务「{TASK_LABELS.get(task, task)}」未启用模型路由；当前模式为 {route["mode"]}')
    ids = [str(route['primary_model_id'])] if route['primary_model_id'] else []
    ids += [str(value) for value in (route['fallback_model_ids'] or [])]
    candidates = _candidate_rows(conn, workspace_id, ids)
    usable, skipped = [], []
    cloud_blocked = False
    for candidate in candidates:
        if not candidate['enabled']:
            skipped.append({'model_profile_id': str(candidate['id']), 'reason': 'MODEL_DISABLED'})
            continue
        if not candidate['provider_enabled']:
            skipped.append({'model_profile_id': str(candidate['id']), 'reason': 'PROVIDER_DISABLED'})
            continue
        cloud = providers.is_cloud(candidate['protocol']) and not candidate['is_local']
        if cloud:
            approved = (workspace['data_policy'] == 'approved_cloud'
                        and route['data_policy'] == 'approved_cloud')
            if not approved:
                cloud_blocked = True
                skipped.append({'model_profile_id': str(candidate['id']), 'reason': 'DATA_POLICY_BLOCKED'})
                continue
        if candidate['price_currency'] != workspace['budget_currency']:
            skipped.append({'model_profile_id': str(candidate['id']), 'reason': 'CURRENCY_MISMATCH'})
            continue
        usable.append(candidate)
    if not usable:
        if cloud_blocked:
            raise GatewayError('DATA_POLICY_BLOCKED',
                               '该路由只包含云端模型，但工作区或任务的数据策略未设为“允许发送云端”')
        raise GatewayError('MODEL_NOT_CONFIGURED', '路由中的模型均未启用或不可用',
                           details={'skipped': skipped})
    return route, usable, workspace, skipped


def _snapshot(route, candidates) -> dict:
    return {
        'schema_version': 'ai-route-snapshot.v1',
        'task': route['task'], 'mode': route['mode'], 'enabled': route['enabled'],
        'route_revision': route['revision'],
        'limits': {'max_input_tokens': route['max_input_tokens'],
                   'max_output_tokens': route['max_output_tokens'],
                   'timeout_seconds': route['timeout_seconds'],
                   'max_attempts': route['max_attempts']},
        'data_policy': route['data_policy'],
        'candidates': [{'model_profile_id': str(item['id']),
                        'provider_id': str(item['provider_id']),
                        'provider_name': item['provider_name'],
                        'protocol': item['protocol'],
                        'model_id': item['model_id'],
                        'display_name': item['display_name'],
                        'tier': item['tier'],
                        'is_local': item['is_local'],
                        'price_currency': item['price_currency'],
                        'input_price_per_mtok': str(item['input_price_per_mtok']),
                        'output_price_per_mtok': str(item['output_price_per_mtok'])} for item in candidates],
    }


# ------------------------------------------------------------------ prompts

SYSTEM_PROMPT = ('你是 FishingNet 的受控信息抽取器。只输出一个 JSON 对象，不输出解释、'
                 '不输出 Markdown。只能引用请求中提供的片段 ID，不得编造引用或外部链接。')


def build_messages(task: str, text: str) -> list[dict]:
    """Build a bounded prompt. The caller supplies synthetic or already-authorized text."""
    clipped = (text or '')[:12000]
    instruction = {
        'claim_extraction': '抽取原子主张。输出 {"claims":[{"statement":"","kind":"factual_assertion|opinion|prediction|experience","aspect":"","evidence":[{"segment_id":"seg_1","quote":""}]}]}。没有可抽取内容时返回 {"claims":[]}。',
        'screening': '判断材料是否包含可核查主张。输出 {"relevant":true,"reason":""}。',
        'fact_check': '基于提供的材料判断证据是否充分。输出 {"status":"unchecked|insufficient|corroborated|conflicted|refuted","gaps":[]}。不得凭记忆声称已查证。',
        'digest_polish': '只润色既有动态文本，不新增事实。输出 {"text":""}。',
    }.get(task, '按任务要求输出 JSON 对象。')
    return [{'role': 'system', 'content': SYSTEM_PROMPT + ' ' + instruction},
            {'role': 'user', 'content': clipped}]


def synthetic_sample(task: str) -> str:
    return (f'【合成示例】这是用于「{TASK_LABELS.get(task, task)}」路由连通性检查的虚构文本，'
            '不包含任何真实来源或个人信息。Plugin X 2.0 在虚构的测试环境中运行正常。')


# -------------------------------------------------------------- invocation

def _insert_invocation(conn, *, workspace_id, task, snapshot, fingerprint, summary,
                       provider_id, model_profile_id, provider_name, model_label,
                       currency, job_id=None) -> dict:
    return one(conn, """INSERT INTO ai_invocations(workspace_id,task,status,job_id,provider_id,
          model_profile_id,provider_name,model_label,request_fingerprint,input_summary,config_snapshot,
          currency) VALUES(:ws,:task,'running',:job,:provider,:model,:pname,:mlabel,:fp,
          CAST(:summary AS jsonb),CAST(:snapshot AS jsonb),:currency) RETURNING *""",
               ws=workspace_id, task=task, job=job_id, provider=provider_id, model=model_profile_id,
               pname=provider_name, mlabel=model_label, fp=fingerprint,
               summary=json.dumps(summary, ensure_ascii=False),
               snapshot=json.dumps(snapshot, ensure_ascii=False, default=str), currency=currency)


def _blocked_invocation(*, workspace_id, task, snapshot, fingerprint, summary,
                        provider_id, model_profile_id, provider_name, model_label,
                        currency, code, message, candidates=None) -> str | None:
    """Best-effort audit row for a call that was refused before any network I/O."""
    try:
        with engine.begin() as conn:
            row = _insert_invocation(conn, workspace_id=workspace_id, task=task, snapshot=snapshot,
                                     fingerprint=fingerprint, summary=summary,
                                     provider_id=provider_id, model_profile_id=model_profile_id,
                                     provider_name=provider_name, model_label=model_label,
                                     currency=currency)
            execute(conn, """UPDATE ai_invocations SET status='blocked',error_code=:code,
                error_message=:message,finished_at=now(),attempt_log=CAST(:log AS jsonb)
                WHERE id=:id""", id=row['id'], code=code, message=message,
                    log=json.dumps(candidates or [], ensure_ascii=False))
            return str(row['id'])
    except Exception:
        return None


def _candidate_reservation(route, candidate) -> Decimal:
    """Mock is free by definition; every other protocol reserves its worst case."""
    if candidate['protocol'] == 'mock':
        return Decimal('0')
    return budget.reservation_amount(route, candidate)


def invoke(task: str, *, workspace_id, text: str, job_id=None, synthetic: bool = False) -> dict:
    """Run one routed model call for a built-in task prompt."""
    if task not in TASK_KEYS:
        raise GatewayError('UNKNOWN_TASK', f'未知任务类型：{task}')
    messages = build_messages(task, text)
    summary = {'input_kind': 'synthetic' if synthetic else 'provided',
               'input_chars': len(text or ''), 'message_count': len(messages)}
    return invoke_messages(task, workspace_id=workspace_id, messages=messages,
                           summary=summary, job_id=job_id)


def invoke_messages(task: str, *, workspace_id, messages: list[dict], summary=None,
                    job_id=None) -> dict:
    """Run one routed model call with budget reservation, fallbacks and settlement.

    ``messages`` is supplied by the caller so a task can carry its own bounded
    prompt (claim extraction sends segment IDs). The order is always: reserve in a
    short transaction, call the provider with no transaction open, then settle in a
    short transaction and update the invocation ledger.
    """
    if task not in TASK_KEYS:
        raise GatewayError('UNKNOWN_TASK', f'未知任务类型：{task}')
    with engine.connect() as conn:
        route, candidates, workspace, skipped = _plan(conn, workspace_id, task)
    snapshot = _snapshot(route, candidates)
    fingerprint = digest(json.dumps({'task': task, 'messages': [m['content'] for m in messages]},
                                    ensure_ascii=False, sort_keys=True))
    summary = dict(summary or {})
    summary.setdefault('input_kind', 'provided')
    summary.setdefault('input_chars', sum(len(str(m.get('content') or '')) for m in messages))
    summary.setdefault('message_count', len(messages))
    summary['skipped_candidates'] = skipped
    currency = workspace['budget_currency']
    primary = candidates[0]
    reservation = max(_candidate_reservation(route, item) for item in candidates)
    invocation_id = None
    ledger_id = None
    try:
        if reservation > 0:
            with engine.begin() as conn:
                ledger = budget.reserve(conn, workspace_id=workspace_id, invocation_id=None,
                                        task=task, amount=reservation, currency=currency,
                                        reason='model-call-worst-case')
                ledger_id = ledger['id']
        with engine.begin() as conn:
            invocation = _insert_invocation(conn, workspace_id=workspace_id, task=task,
                                            snapshot=snapshot, fingerprint=fingerprint, summary=summary,
                                            provider_id=primary['provider_id'],
                                            model_profile_id=primary['id'],
                                            provider_name=primary['provider_name'],
                                            model_label=primary['display_name'],
                                            currency=currency, job_id=job_id)
            invocation_id = str(invocation['id'])
            if ledger_id:
                execute(conn, 'UPDATE ai_budget_ledger SET invocation_id=:inv WHERE id=:id',
                        inv=invocation['id'], id=ledger_id)
    except budget.BudgetError as exc:
        blocked_id = _blocked_invocation(workspace_id=workspace_id, task=task, snapshot=snapshot,
                                         fingerprint=fingerprint, summary=summary,
                                         provider_id=primary['provider_id'], model_profile_id=primary['id'],
                                         provider_name=primary['provider_name'], model_label=primary['display_name'],
                                         currency=currency, code=exc.code, message=exc.message)
        details = dict(exc.details or {})
        if blocked_id:
            details['invocation_id'] = blocked_id
        raise GatewayError(exc.code, exc.message, False, details)

    attempt_log = []
    last_error = None
    for candidate in candidates:
        provider = {'id': candidate['provider_id'], 'name': candidate['provider_name'],
                    'protocol': candidate['protocol'], 'base_url': candidate['base_url'],
                    'is_local': candidate['is_local']}
        try:
            api_key = open_api_key(candidate['credential_sealed']) if candidate['credential_sealed'] else ''
        except SecretError as exc:
            attempt_log.append({'model_profile_id': str(candidate['id']),
                                'model': candidate['display_name'], 'error_code': exc.code})
            last_error = GatewayError(exc.code, exc.message)
            continue
        for attempt in range(1, int(route['max_attempts']) + 1):
            started = time.monotonic()
            try:
                result = providers.call_chat(provider, candidate, messages, api_key=api_key,
                                             timeout=int(route['timeout_seconds']), structured=True,
                                             max_tokens=min(int(route['max_output_tokens']),
                                                            int(candidate['max_output_tokens'])))
            except providers.ProviderError as exc:
                latency = int((time.monotonic() - started) * 1000)
                attempt_log.append({'model_profile_id': str(candidate['id']),
                                    'model': candidate['display_name'],
                                    'provider': candidate['provider_name'],
                                    'attempt': attempt, 'error_code': exc.code,
                                    'retryable': exc.retryable, 'latency_ms': latency})
                last_error = GatewayError(exc.code, exc.message, exc.retryable)
                if exc.retryable and attempt < int(route['max_attempts']):
                    time.sleep(RETRY_DELAY_SECONDS * attempt)
                    continue
                break
            latency = int((time.monotonic() - started) * 1000)
            usage = budget.normalize_usage(result.get('usage'))
            if candidate['protocol'] == 'mock':
                cost, cost_status = Decimal('0'), 'none'
            elif usage['input_tokens'] or usage['output_tokens']:
                cost = budget.estimate_cost(usage['input_tokens'], usage['output_tokens'], candidate)
                cost_status = 'estimated'
            else:
                cost = reservation
                cost_status = 'unknown'  # conservative: keep the whole reservation
            with engine.begin() as conn:
                if ledger_id:
                    budget.settle(conn, ledger_id, cost)
                execute(conn, """UPDATE ai_invocations SET status='succeeded',finished_at=now(),
                    attempts=:attempts,attempt_log=CAST(:log AS jsonb),provider_request_id=:rid,
                    input_tokens=:tin,output_tokens=:tout,cached_tokens=:cached,
                    cost_estimate=:cost,cost_settled=:cost,cost_status=:cstatus,latency_ms=:latency,
                    model_label=:mlabel,provider_name=:pname,response_content=:rcontent
                    WHERE id=:id""", id=invocation_id, attempts=len(attempt_log) + 1,
                    log=json.dumps(attempt_log, ensure_ascii=False), rid=result.get('provider_request_id') or '',
                    tin=usage['input_tokens'], tout=usage['output_tokens'],
                    cached=usage['cached_tokens'], cost=cost, cstatus=cost_status,
                    latency=latency, mlabel=candidate['display_name'],
                    pname=candidate['provider_name'],
                    rcontent=(result.get('content') or '')[:12000])
            parsed = _parse_json(result.get('content'))
            return {'invocation_id': invocation_id, 'status': 'succeeded', 'task': task,
                    'task_label': TASK_LABELS[task], 'provider': candidate['provider_name'],
                    'model': candidate['display_name'], 'model_id': candidate['model_id'],
                    'content': result.get('content'), 'parsed': parsed,
                    'usage': usage, 'cost': {'currency': currency, 'amount': str(cost),
                                             'status': cost_status,
                                             'note': _cost_note(cost_status)},
                    'attempts': attempt_log, 'skipped': skipped,
                    'note': '结果未写入知识库、未生成主张或动态；需要人工复核后再决定是否采用。'}
    code = last_error.code if last_error else 'ALL_CANDIDATES_FAILED'
    message = last_error.message if last_error else '所有候选模型均失败'
    with engine.begin() as conn:
        if ledger_id:
            budget.release(conn, ledger_id)
        execute(conn, """UPDATE ai_invocations SET status='failed',finished_at=now(),
            attempts=:attempts,attempt_log=CAST(:log AS jsonb),error_code=:code,error_message=:message,
            cost_settled=0,cost_status='none' WHERE id=:id""",
                id=invocation_id, attempts=len(attempt_log), log=json.dumps(attempt_log, ensure_ascii=False),
                code=code, message=message[:500])
    raise GatewayError(code, message, bool(last_error and last_error.retryable),
                       {'invocation_id': invocation_id, 'attempts': attempt_log})


def _cost_note(status: str) -> str:
    if status == 'none':
        return 'Mock 或免费调用，未产生费用'
    if status == 'unknown':
        return '供应商未返回 token 用量；按最坏情况保留全部预留额度，费用状态为未知'
    return '按供应商返回的 token 用量与已保存单价估算，未经供应商账单确认'


def _parse_json(content):
    if not content:
        return None
    text = content.strip()
    if text.startswith('```'):
        text = re.sub(r'^```[a-zA-Z0-9_-]*\s*', '', text)
        text = re.sub(r'\s*```$', '', text).strip()
    try:
        return json.loads(text)
    except ValueError:
        return None


def test_provider(workspace_id, provider_id) -> dict:
    """Explicit connection test: a fixed synthetic prompt, no user material."""
    with engine.connect() as conn:
        provider = one(conn, 'SELECT * FROM provider_connections WHERE id=:id AND workspace_id=:ws',
                       id=provider_id, ws=workspace_id)
        if not provider:
            raise GatewayError('PROVIDER_NOT_FOUND', '供应商不存在')
        model = one(conn, """SELECT * FROM model_profiles WHERE workspace_id=:ws AND provider_id=:pid
            ORDER BY enabled DESC, created_at LIMIT 1""", ws=workspace_id, pid=provider_id)
        workspace = one(conn, 'SELECT * FROM workspace_settings WHERE id=:ws', ws=workspace_id)
    if not model:
        raise GatewayError('MODEL_REQUIRED', '请先为该供应商添加至少一个模型，再测试连接')
    with engine.connect() as conn:
        candidate = _candidate_rows(conn, workspace_id, [str(model['id'])])
    if not candidate:
        raise GatewayError('MODEL_REQUIRED', '模型缺少可用的供应商配置')
    model = candidate[0]
    route = {'max_input_tokens': PROBE_LIMITS['max_input_tokens'],
             'max_output_tokens': PROBE_LIMITS['max_output_tokens'],
             'max_attempts': 1, 'timeout_seconds': 20, 'task': CONNECTION_TEST_TASK,
             'mode': 'model', 'enabled': True, 'data_policy': 'local_only', 'revision': 0}
    cloud = providers.is_cloud(provider['protocol']) and not provider['is_local']
    if cloud and workspace['data_policy'] != 'approved_cloud':
        raise GatewayError('DATA_POLICY_BLOCKED',
                           '工作区数据策略为“仅本地”；测试连接会向云端发送固定的合成提示。'
                           '请先在系统设置中允许云端发送')
    amount = _candidate_reservation(route, model)
    currency = workspace['budget_currency']
    snapshot = {**_snapshot(route, [model]), 'connection_test': True}
    fingerprint = digest(json.dumps({'task': CONNECTION_TEST_TASK, 'provider': str(provider_id)},
                                    ensure_ascii=False, sort_keys=True))
    summary = {'input_kind': 'synthetic-probe', 'input_chars': 0, 'message_count': 2}
    ledger_id, invocation_id = None, None
    try:
        if amount > 0:
            with engine.begin() as conn:
                ledger = budget.reserve(conn, workspace_id=workspace_id, invocation_id=None,
                                        task=CONNECTION_TEST_TASK, amount=amount, currency=currency,
                                        reason='provider-connection-test')
                ledger_id = ledger['id']
        with engine.begin() as conn:
            invocation = _insert_invocation(conn, workspace_id=workspace_id, task=CONNECTION_TEST_TASK,
                                            snapshot=snapshot, fingerprint=fingerprint, summary=summary,
                                            provider_id=provider_id, model_profile_id=model['id'],
                                            provider_name=provider['name'],
                                            model_label=model['display_name'], currency=currency)
            invocation_id = str(invocation['id'])
            if ledger_id:
                execute(conn, 'UPDATE ai_budget_ledger SET invocation_id=:inv WHERE id=:id',
                        inv=invocation['id'], id=ledger_id)
    except budget.BudgetError as exc:
        raise GatewayError(exc.code, exc.message, False, exc.details)
    started = time.monotonic()
    try:
        api_key = open_api_key(provider['credential_sealed']) if provider['credential_sealed'] else ''
        result = providers.probe(provider, model, api_key=api_key, timeout=20)
    except (providers.ProviderError, SecretError) as exc:
        latency = int((time.monotonic() - started) * 1000)
        code = getattr(exc, 'code', 'SECRET_ERROR')
        status = ('auth_failed' if code == 'AUTH_FAILED'
                  else 'network_failed' if code in ('NETWORK_FAILED', 'RATE_LIMITED', 'UPSTREAM_FAILED')
                  else 'capability_mismatch')
        message = getattr(exc, 'message', str(exc))
        with engine.begin() as conn:
            if ledger_id:
                budget.release(conn, ledger_id)
            execute(conn, """UPDATE ai_invocations SET status='failed',finished_at=now(),attempts=1,
                error_code=:code,error_message=:message,latency_ms=:latency,cost_settled=0,
                cost_status='none' WHERE id=:id""",
                    id=invocation_id, code=code, message=message[:500], latency=latency)
            execute(conn, """UPDATE provider_connections SET status=:status,last_tested_at=now(),
                last_error_code=:code,last_test_detail=:detail,updated_at=now(),revision=revision+1
                WHERE id=:id AND workspace_id=:ws""",
                    id=provider_id, ws=workspace_id, status=status, code=code, detail=message[:500])
        raise GatewayError(code, message, getattr(exc, 'retryable', False),
                           {'invocation_id': invocation_id})
    usage = budget.normalize_usage(result.get('usage'))
    if provider['protocol'] == 'mock':
        cost, cost_status = Decimal('0'), 'none'
    elif usage['input_tokens'] or usage['output_tokens']:
        cost, cost_status = budget.estimate_cost(usage['input_tokens'], usage['output_tokens'], model), 'estimated'
    else:
        cost, cost_status = amount, 'unknown'
    latency = int((time.monotonic() - started) * 1000)
    with engine.begin() as conn:
        if ledger_id:
            budget.settle(conn, ledger_id, cost)
        execute(conn, """UPDATE ai_invocations SET status='succeeded',finished_at=now(),attempts=1,
            provider_request_id=:rid,input_tokens=:tin,output_tokens=:tout,cached_tokens=:cached,
            cost_estimate=:cost,cost_settled=:cost,cost_status=:cstatus,latency_ms=:latency
            WHERE id=:id""", id=invocation_id, rid=result.get('provider_request_id') or '',
            tin=usage['input_tokens'], tout=usage['output_tokens'], cached=usage['cached_tokens'],
            cost=cost, cstatus=cost_status, latency=latency)
        execute(conn, """UPDATE provider_connections SET status='ok',last_tested_at=now(),
            last_error_code=NULL,last_test_detail=:detail,updated_at=now(),revision=revision+1
            WHERE id=:id AND workspace_id=:ws""",
                id=provider_id, ws=workspace_id,
                detail='Mock 提供器：未发起真实请求' if provider['protocol'] == 'mock' else '连接测试成功')
    return {'provider_id': str(provider_id), 'provider_status': 'ok',
            'protocol': provider['protocol'], 'model': model['display_name'],
            'invocation_id': invocation_id, 'synthetic_prompt': True,
            'usage': usage, 'cost': {'currency': currency, 'amount': str(cost), 'status': cost_status},
            'note': ('Mock 提供器不发起网络请求，因此只验证本地流程，不能证明真实供应商可用'
                     if provider['protocol'] == 'mock' else
                     '连接测试使用固定合成提示，未发送导入材料；成功仅代表该端点与密钥当前可用，不代表模型能力匹配')}
