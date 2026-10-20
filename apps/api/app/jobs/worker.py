import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from sqlalchemy import text
from app.storage.db import engine, one, many, execute
from app.domain.evidence import digest, validate_anchor
from app.domain.policy import PublicationPolicy, POLICY_VERSION
from app.domain import verification as verification_engine
from app.domain.discovery import (
    merge_watermark, next_run_after, plan_candidates, target_pages,
)
from app.ai import extraction as ai_extraction
from app.ai import review as ai_review
from app.ai import entities as ai_entities
from app.ai import factcheck as ai_factcheck
from app.ai import gateway as ai_gateway
from app.ai.gateway import AIGateway
from app.collectors import (
    CollectorError, Credential, DiscoveryItem, DiscoveryQuery, RemoteSource,
    SubtitleResult, get_collector,
)
from app.domain.secrets import SecretError, open_sealed

MAX_ATTEMPTS = 3
LEASE_SECONDS = 120
# Network stages are bounded (8 s per call, at most 4 calls) and the heartbeat
# renews the lease while they run, so a worker never holds a row lock for I/O.
HEARTBEAT_SECONDS = max(1.0, LEASE_SECONDS / 3)
COLLECT_STAGES = ('fetch_metadata', 'fetch_subtitles', 'store_version')
DISCOVERY_STAGES = ('discover_page', 'plan', 'enqueue')
INITIAL_STAGE = {'collect_source': 'fetch_metadata', 'discover_keywords': 'discover_page_1',
                 'fact_check': 'retrieve'}
FACT_RELATION_TYPES = ('fact_supported_by', 'fact_refuted_by', 'fact_context_by')
MAX_FACT_MATERIALS = 20
ACTIVE_JOB_STATES = ('queued', 'running', 'retry_wait', 'blocked_credentials')
# A platform-side challenge gets a local cooldown; the app never retries around
# it, it only tells the user when a manual recovery may be attempted.
RISK_COOLDOWN_SECONDS = 15 * 60
DEFAULT_DISCOVERY_ITEM_RETENTION_DAYS = 30
DISCOVERY_CLEANUP_INTERVAL_SECONDS = 6 * 60 * 60
DISCOVERY_CLEANUP_BATCH = 5000


class FenceLost(Exception):
    """Another attempt (or a cancellation) took the job while we were working."""


def js(value):
    return json.dumps(value, ensure_ascii=False, default=str)


def relation(conn, ws, ft, fi, kind, tt, ti, payload):
    key = digest(js([str(fi), kind, str(ti), payload]))
    row = one(conn, '''INSERT INTO relations(workspace_id,from_type,from_id,relation_type,to_type,to_id,payload,status,asserted_by,dedup_key)
        VALUES(:ws,:ft,:fi,:kind,:tt,:ti,CAST(:payload AS jsonb),'accepted','rule',:key)
        ON CONFLICT(workspace_id,dedup_key) DO UPDATE SET dedup_key=EXCLUDED.dedup_key RETURNING id''',
        ws=ws, ft=ft, fi=fi, kind=kind, tt=tt, ti=ti, payload=js(payload), key=key)
    return row['id']


def proposed_relation(conn, ws, fi, node_id, payload):
    """A model-suggested claim->entity link that a user must accept before use."""
    key = digest(js([str(fi), 'about', str(node_id), payload]))
    row = one(conn, '''INSERT INTO relations(workspace_id,from_type,from_id,relation_type,to_type,to_id,payload,status,asserted_by,dedup_key)
        VALUES(:ws,'claim',:fi,'about','node',:ti,CAST(:payload AS jsonb),'proposed','model',:key)
        ON CONFLICT(workspace_id,dedup_key) DO UPDATE SET dedup_key=EXCLUDED.dedup_key RETURNING id''',
        ws=ws, fi=fi, ti=node_id, payload=js(payload), key=key)
    return row['id']


class LeaseKeeper:
    """Renew a job lease from a side thread while a bounded network call runs.

    The renewal only matches ``state='running' AND fence=:fence``. If it stops
    matching, the job has been cancelled or re-claimed, and ``lost`` becomes
    true; the caller must stop before writing anything else. All writes are
    still guarded by the same condition, so a missed heartbeat cannot publish
    a stale result.
    """

    def __init__(self, db_engine, job, interval=None):
        self._engine = db_engine
        self._job = job
        self.interval = interval or HEARTBEAT_SECONDS
        self.lost = False
        self._stop = threading.Event()
        self._thread = None

    def _run(self):
        while not self._stop.wait(self.interval):
            try:
                with self._engine.begin() as conn:
                    row = one(conn, """UPDATE jobs SET lease_until=now()+make_interval(secs=>:lease), updated_at=now()
                        WHERE id=:id AND workspace_id=:ws AND fence=:fence AND state='running' RETURNING id""",
                        id=self._job['id'], ws=self._job['workspace_id'],
                        fence=self._job['fence'], lease=LEASE_SECONDS)
                if not row:
                    self.lost = True
                    return
            except Exception:
                # A transient database error must not kill the job; the guarded
                # commit below is the actual safety net.
                continue

    def __enter__(self):
        self._thread = threading.Thread(target=self._run, name='job-lease', daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc_info):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        return False


def claim():
    """Claim one runnable job in a short transaction and commit it."""
    with engine.begin() as conn:
        # A crashed worker leaves a running job whose lease expires. Do not retry
        # forever: after MAX_ATTEMPTS the job becomes an explainable failure.
        execute(conn, """UPDATE jobs SET state='failed',stage='failed',error_code='ATTEMPTS_EXHAUSTED',lease_until=NULL,updated_at=now()
            WHERE state='running' AND attempts>=:max AND lease_until IS NOT NULL AND lease_until<now()""", max=MAX_ATTEMPTS)
        execute(conn, """UPDATE job_attempts SET state='failed' WHERE state='running' AND job_id IN (
            SELECT id FROM jobs WHERE state='failed' AND error_code='ATTEMPTS_EXHAUSTED')""")
        execute(conn, """UPDATE jobs SET state='cancelled',stage='cancelled',lease_until=NULL,updated_at=now()
            WHERE state='cancel_requested' AND (lease_until IS NULL OR lease_until<now())""")
        execute(conn, """UPDATE job_attempts SET state='cancelled' WHERE state='running' AND job_id IN (
            SELECT id FROM jobs WHERE state='cancelled')""")
        # Crash recovery for discovery: an exhausted or cancelled job must not leave
        # its run ledger "queued" forever; the source gets an explainable error and
        # a backed-off next attempt.
        execute(conn, """UPDATE discovery_runs r SET state='cancelled',finished_at=now()
            WHERE r.state IN ('queued','running') AND r.job_id IN (
              SELECT id FROM jobs WHERE state='cancelled' AND kind='discover_keywords')""")
        execute(conn, """WITH exhausted AS (
              UPDATE discovery_runs SET state='failed',finished_at=now(),
                error_code='ATTEMPTS_EXHAUSTED',detail='任务多次中断后放弃，请检查网络或稍后手动重试'
              WHERE state IN ('queued','running') AND job_id IN (
                SELECT id FROM jobs WHERE state='failed' AND error_code='ATTEMPTS_EXHAUSTED')
              RETURNING source_id)
            UPDATE discovery_sources s SET failure_streak=s.failure_streak+1,
              last_run_at=now(),last_error_code='ATTEMPTS_EXHAUSTED',
              next_run_at=now()+interval '15 minutes',updated_at=now(),revision=s.revision+1
            WHERE s.id IN (SELECT source_id FROM exhausted)""")
        job = one(conn, '''SELECT * FROM jobs
            WHERE (state='queued' OR state='retry_wait' OR (state='running' AND lease_until<now())) AND attempts<:max
            ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1''', max=MAX_ATTEMPTS)
        if not job:
            return None
        stage = INITIAL_STAGE.get(job['kind'], 'extract')
        job = one(conn, '''UPDATE jobs SET state='running',
            stage=COALESCE(NULLIF(checkpoint->>'stage',''),:stage),attempts=attempts+1,fence=fence+1,
            lease_until=now()+make_interval(secs=>:lease),updated_at=now() WHERE id=:id RETURNING *''',
            id=job['id'], lease=LEASE_SECONDS, stage=stage)
        execute(conn, "INSERT INTO job_attempts(workspace_id,job_id,fence,state) VALUES(:ws,:id,:f,'running')",
                ws=job['workspace_id'], id=job['id'], f=job['fence'])
        return job


def _fence_lost(job):
    """React to a lost fence without touching rows another attempt owns."""
    with engine.begin() as conn:
        row = one(conn, 'SELECT state,fence FROM jobs WHERE id=:id AND workspace_id=:ws',
                  id=job['id'], ws=job['workspace_id'])
        if not row:
            return
        if row['state'] == 'cancel_requested':
            execute(conn, "UPDATE jobs SET state='cancelled',stage='cancelled',lease_until=NULL,updated_at=now() WHERE id=:id",
                    id=job['id'])
            execute(conn, "UPDATE job_attempts SET state='cancelled' WHERE job_id=:id AND fence=:f",
                    id=job['id'], f=job['fence'])


def _guarded(job, sql, **params):
    """Run one short write that only lands while this attempt still owns the job."""
    with engine.begin() as conn:
        result = conn.execute(text(sql), {**params, 'jid': job['id'], 'fence': job['fence']})
        rowcount = result.rowcount
    if rowcount != 1:
        raise FenceLost()


def _progress(job, stage, delta=None):
    result = {'stage': stage}
    if delta:
        result.update(delta)
    _guarded(job, """UPDATE jobs SET stage=:stage, result=result || CAST(:delta AS jsonb),
        lease_until=now()+make_interval(secs=>:lease), updated_at=now()
        WHERE id=:jid AND fence=:fence AND state='running'""",
             stage=stage, delta=js(delta or {}), lease=LEASE_SECONDS)


def _checkpoint(job, stage, delta):
    """Persist a completed stage so a crashed attempt resumes instead of refetching.

    The write is fenced like every other stage write: if another attempt (or a
    cancellation) took the job, the checkpoint is discarded and ``FenceLost`` is
    raised before any further network call.
    """
    payload = dict(delta or {})
    payload['stage'] = stage
    _guarded(job, """UPDATE jobs SET checkpoint=checkpoint || CAST(:delta AS jsonb),
        stage=:stage, lease_until=now()+make_interval(secs=>:lease), updated_at=now()
        WHERE id=:jid AND fence=:fence AND state='running'""",
             delta=js(payload), stage=stage, lease=LEASE_SECONDS)
    job['checkpoint'] = {**(job.get('checkpoint') or {}), **payload}
    job['stage'] = stage


def _cancelled(job):
    with engine.connect() as conn:
        row = one(conn, 'SELECT state FROM jobs WHERE id=:id AND workspace_id=:ws',
                  id=job['id'], ws=job['workspace_id'])
    return bool(row) and row['state'] == 'cancel_requested'


def _finish(job, state, result, error_code=None):
    with engine.begin() as conn:
        changed = execute(conn, """UPDATE jobs SET state=:state,stage=:stage,result=CAST(:result AS jsonb),
            error_code=:code,lease_until=NULL,updated_at=now()
            WHERE id=:id AND fence=:fence AND state='running'""",
            id=job['id'], fence=job['fence'], state=state, stage='finished',
            result=js(result), code=error_code)
        if changed.rowcount != 1:
            raise FenceLost()
        execute(conn, "UPDATE job_attempts SET state=:state WHERE job_id=:id AND fence=:fence",
                state=state, id=job['id'], fence=job['fence'])


def _load_credential(connection):
    if not connection or not connection.get('credential_sealed'):
        return None
    values = open_sealed(connection['credential_sealed'])
    return Credential(platform=connection['platform'], values=values) if values else None


def _save_metadata(job, remote):
    _progress(job, 'fetch_subtitles', {'metadata': {'title': remote.title, 'page_count': len(remote.pages)}})
    metadata = dict(remote.metadata)
    # Part map kept for "定位分P" without any extra upstream request: the user can
    # import a single part later instead of the app batch-fetching every part.
    if remote.pages and 'parts' not in metadata:
        metadata['parts'] = [{'page_no': page.page_no, 'cid': page.cid,
                              'part_title': page.part_title,
                              'duration_seconds': page.duration_seconds}
                             for page in remote.pages[:100]]
    with engine.begin() as conn:
        execute(conn, """UPDATE sources SET title=:title,author_name=:author,canonical_url=:url,
            published_at=:published,availability=:availability,metadata=metadata || CAST(:meta AS jsonb),
            last_checked_at=now(),updated_at=now(),revision=revision+1
            WHERE id=:sid AND workspace_id=:ws""",
            title=remote.title, author=remote.author_name, url=remote.canonical_url,
            published=(remote.published_at.isoformat() if remote.published_at else None),
            availability=remote.availability, sid=job['input']['source_id'],
            ws=job['workspace_id'], meta=js(metadata))


def _mark_source(job, *, content_status=None, availability=None, meta=None):
    sets = ["last_checked_at=now()", "updated_at=now()", "revision=revision+1"]
    params = {'sid': job['input']['source_id'], 'ws': job['workspace_id']}
    if content_status:
        sets.append('content_status=:content_status')
        params['content_status'] = content_status
    if availability:
        sets.append('availability=:availability')
        params['availability'] = availability
    if meta:
        sets.append('metadata=metadata || CAST(:meta AS jsonb)')
        params['meta'] = js(meta)
    with engine.begin() as conn:
        execute(conn, f"UPDATE sources SET {','.join(sets)} WHERE id=:sid AND workspace_id=:ws", **params)


def _record_platform_failure(job, exc):
    """Persist a recoverable connection state after an upstream rejection.

    Only a stable code and a safe message are stored; no cookie, header or raw
    response is kept. A platform challenge never changes ``enabled`` or
    ``network_authorized`` and never triggers an automatic retry.
    """
    code = exc.code
    if code not in ('CREDENTIAL_EXPIRED', 'RISK_CONTROL', 'RATE_LIMITED'):
        return
    platform = (job.get('input') or {}).get('platform') or 'bilibili'
    try:
        with engine.begin() as conn:
            if code == 'CREDENTIAL_EXPIRED':
                execute(conn, """UPDATE platform_connections SET status='expired',
                    last_error_code=:code,last_check_detail=:detail,cooldown_until=NULL,
                    updated_at=now(),revision=revision+1
                    WHERE workspace_id=:ws AND platform=:p""",
                        ws=job['workspace_id'], p=platform, code=code,
                        detail='采集时发现登录态已失效；请重新提供本机 Cookie 或扫码登录后恢复任务')
            else:
                execute(conn, """UPDATE platform_connections SET status='risk_control',
                    last_error_code=:code,last_check_detail=:detail,
                    cooldown_until=now()+make_interval(secs=>:secs),
                    updated_at=now(),revision=revision+1
                    WHERE workspace_id=:ws AND platform=:p""",
                        ws=job['workspace_id'], p=platform, code=code,
                        detail='B站风控/限流后已暂停自动恢复 15 分钟；不会换账号或提高频率',
                        secs=RISK_COOLDOWN_SECONDS)
    except Exception:
        # Connection bookkeeping must never hide the job's own failure record.
        pass


def _mark_platform_success(job):
    """Note a successful upstream stage without claiming platform authorization."""
    platform = (job.get('input') or {}).get('platform') or 'bilibili'
    try:
        with engine.begin() as conn:
            execute(conn, """UPDATE platform_connections SET last_success_at=now(),
                cooldown_until=NULL,
                status=CASE WHEN status='risk_control' THEN 'unverified' ELSE status END,
                last_error_code=CASE WHEN status='risk_control' THEN NULL ELSE last_error_code END,
                last_check_detail=CASE WHEN status='risk_control' THEN '风控冷却后已恢复一次采集'
                                       ELSE last_check_detail END,
                updated_at=now(),revision=revision+1
                WHERE workspace_id=:ws AND platform=:p""",
                    ws=job['workspace_id'], p=platform)
    except Exception:
        pass


def discovery_item_retention_days() -> int:
    raw = os.environ.get('FISHINGNET_DISCOVERY_ITEM_RETENTION_DAYS')
    try:
        days = int(raw) if raw is not None else DEFAULT_DISCOVERY_ITEM_RETENTION_DAYS
    except (TypeError, ValueError):
        days = DEFAULT_DISCOVERY_ITEM_RETENTION_DAYS
    return max(1, min(3650, days))


def cleanup_discovery_items(days=None, limit=DISCOVERY_CLEANUP_BATCH) -> int:
    """Expire per-candidate decisions of finished runs; keep the run ledgers.

    Deletion is bounded and only touches rows whose run already finished before
    the retention cutoff. Counters on ``discovery_runs`` and source watermarks are
    untouched, so "how many candidates were enqueued" remains explainable even
    after the detailed titles are gone.
    """
    days = max(1, min(3650, int(days if days is not None else discovery_item_retention_days())))
    with engine.begin() as conn:
        result = execute(conn, """DELETE FROM discovery_run_items WHERE id IN (
            SELECT i.id FROM discovery_run_items i
            JOIN discovery_runs r ON r.workspace_id=i.workspace_id AND r.id=i.run_id
            WHERE r.finished_at IS NOT NULL
              AND r.finished_at < now()-make_interval(days=>:days)
            ORDER BY r.finished_at
            LIMIT :limit)""", days=days, limit=int(limit))
        return int(result.rowcount or 0)


def _store_caption(job, remote, subtitles):
    segments = list(subtitles.segments)
    raw_hash = digest(js({'language': subtitles.language or 'zh', 'segments': segments}))
    normalized_hash = digest(js(segments))
    coverage = {'status': 'complete', 'source': 'platform_caption',
                'track': subtitles.language, 'segment_count': len(segments),
                'note': '平台字幕原文；未做转写、未做 AI 分析'}
    with engine.begin() as conn:
        changed = execute(conn, """UPDATE jobs SET stage='store_version', updated_at=now(),
            lease_until=now()+make_interval(secs=>:lease)
            WHERE id=:jid AND fence=:fence AND state='running'""",
            jid=job['id'], fence=job['fence'], lease=LEASE_SECONDS)
        if changed.rowcount != 1:
            raise FenceLost()
        existing = one(conn, """SELECT * FROM source_versions WHERE workspace_id=:ws AND source_id=:sid
            AND raw_hash=:hash AND extractor_version='bilibili.subtitle.v1'""",
            ws=job['workspace_id'], sid=job['input']['source_id'], hash=raw_hash)
        if existing:
            version = existing
        else:
            version_no = one(conn, 'SELECT coalesce(max(version_no),0)+1 AS n FROM source_versions WHERE source_id=:sid',
                             sid=job['input']['source_id'])['n']
            version = one(conn, '''INSERT INTO source_versions(workspace_id,source_id,version_no,raw_hash,normalized_hash,
                normalization_version,extractor_version,content_origin,language,segments,coverage,retention_expires_at)
                VALUES(:ws,:sid,:n,:hash,:nhash,'bilibili.v1','bilibili.subtitle.v1','platform_caption',:lang,
                CAST(:segments AS jsonb),CAST(:coverage AS jsonb),now()+interval '30 days') RETURNING *''',
                ws=job['workspace_id'], sid=job['input']['source_id'], n=version_no, hash=raw_hash,
                nhash=normalized_hash, lang=subtitles.language or 'zh', segments=js(segments),
                coverage=js(coverage))
        execute(conn, """UPDATE sources SET current_version_id=:vid,content_status='ready',
            metadata=metadata || CAST(:meta AS jsonb),last_checked_at=now(),updated_at=now(),revision=revision+1
            WHERE id=:sid AND workspace_id=:ws""",
            vid=version['id'], sid=job['input']['source_id'], ws=job['workspace_id'],
            meta=js({'subtitle_status': 'available', 'subtitle_language': subtitles.language or 'zh',
                     'subtitle_segments': len(segments)}))
        # Hand the stored text to the extraction stage. The Mock gateway does not
        # analyse real material, so this job will stop at "needs_review".
        execute(conn, '''INSERT INTO jobs(workspace_id,kind,input,config_snapshot,idempotency_key)
            VALUES(:ws,'process_source',CAST(:input AS jsonb),CAST(:snapshot AS jsonb),:key)
            ON CONFLICT(workspace_id,idempotency_key) DO NOTHING''',
            ws=job['workspace_id'], input=js({'version_id': version['id']}),
            snapshot=js({'provider': 'mock', 'data_policy': 'local_only', 'policy_version': POLICY_VERSION,
                         'source': 'bilibili'}),
            key=f'process:{version["id"]}:v1')
    return version


def _fail_collect(job, exc: CollectorError):
    _record_platform_failure(job, exc)
    content_status = 'permission_required' if exc.code in (
        'PERMISSION_REQUIRED', 'CREDENTIAL_EXPIRED') else 'failed'
    availability = 'restricted' if exc.code == 'PERMISSION_REQUIRED' else (
        'deleted' if exc.code == 'SOURCE_NOT_FOUND' else None)
    try:
        _mark_source(job, content_status=content_status, availability=availability,
                     meta={'subtitle_status': 'unavailable', 'last_error_code': exc.code})
        _finish(job, 'failed', {'message': exc.message, 'retryable': exc.retryable,
                                'error': exc.code, 'details': exc.details}, error_code=exc.code)
    except FenceLost:
        raise


def collect(job):
    """Collect one Bilibili link: short transactions, network between them.

    Stage order: fetch_metadata -> fetch_subtitles -> store_version. No row lock
    is held while the network call runs; the lease heartbeat keeps ownership and
    every write is fenced. Each completed stage is persisted in
    ``jobs.checkpoint``, so a retried attempt after a crash resumes at the next
    stage instead of repeating requests the platform already answered.
    """
    ws = job['workspace_id']
    with engine.connect() as conn:
        source = one(conn, 'SELECT * FROM sources WHERE id=:id AND workspace_id=:ws',
                     id=job['input']['source_id'], ws=ws)
        connection = one(conn, 'SELECT * FROM platform_connections WHERE workspace_id=:ws AND platform=:p',
                         ws=ws, p=(job['input'].get('platform') or 'bilibili'))
    if not source:
        _finish(job, 'failed', {'message': '来源不存在或已删除', 'error': 'SOURCE_NOT_FOUND'},
                error_code='SOURCE_NOT_FOUND')
        return
    if source['availability'] == 'deleted' or job['input'].get('cancelled'):
        _finish(job, 'cancelled', {'message': '来源已删除，采集取消'})
        return
    platform = source['platform']
    collector = get_collector(platform)
    try:
        credential = _load_credential(connection)
    except SecretError as exc:
        _finish(job, 'blocked_credentials', {'message': exc.message, 'error': exc.code},
                error_code=exc.code)
        return
    url = job['input'].get('url') or source['canonical_url'] or ''
    try:
        locator = collector.parse_locator(url)
    except CollectorError as exc:
        _fail_collect(job, exc)
        return
    checkpoint = job.get('checkpoint') or {}
    languages = tuple(job['input'].get('languages') or ()) or None
    language_policy = job['input'].get('language_policy') or 'prefer'
    with LeaseKeeper(engine, job) as keeper:
        if _cancelled(job):
            _fence_lost(job)
            return
        remote = None
        if isinstance(checkpoint.get('remote'), dict):
            try:
                remote = RemoteSource.from_checkpoint(checkpoint['remote'])
                _progress(job, 'fetch_subtitles', {'resumed_stage': 'fetch_metadata'})
            except (KeyError, TypeError, ValueError):
                remote = None
        if remote is None:
            try:
                remote = collector.fetch_source(locator, credential)
            except CollectorError as exc:
                _fail_collect(job, exc)
                return
            if keeper.lost or _cancelled(job):
                _fence_lost(job)
                return
            _save_metadata(job, remote)
            _checkpoint(job, 'fetch_subtitles', {'remote': remote.to_checkpoint()})
        else:
            _save_metadata(job, remote)
        if keeper.lost or _cancelled(job):
            _fence_lost(job)
            return
        subtitles = None
        if isinstance(checkpoint.get('subtitles'), dict):
            try:
                subtitles = SubtitleResult.from_checkpoint(checkpoint['subtitles'])
                _progress(job, 'store_version', {'resumed_stage': 'fetch_subtitles'})
            except (KeyError, TypeError, ValueError):
                subtitles = None
        if subtitles is None:
            try:
                subtitles = collector.fetch_subtitles(locator, remote, credential,
                                                      languages=languages,
                                                      language_policy=language_policy)
            except CollectorError as exc:
                _fail_collect(job, exc)
                return
            if keeper.lost or _cancelled(job):
                _fence_lost(job)
                return
            _checkpoint(job, 'store_version', {'subtitles': subtitles.to_checkpoint()})
        if subtitles.status == 'available':
            version = _store_caption(job, remote, subtitles)
            _mark_platform_success(job)
            _finish(job, 'succeeded', {
                'message': f'已保存平台字幕原文（{len(subtitles.segments)} 段）；未做 AI 分析，等待配置真实模型或人工复核。',
                'subtitle_status': 'available', 'version_id': str(version['id']),
                'segments': len(subtitles.segments), 'ai_analyzed': False,
                'resumed': bool(checkpoint.get('remote') or checkpoint.get('subtitles')),
                'detail': subtitles.detail})
        elif subtitles.status == 'missing':
            _mark_source(job, content_status='missing',
                         meta={'subtitle_status': 'missing', 'missing_reason': 'no_subtitle_track'})
            _mark_platform_success(job)
            _finish(job, 'succeeded', {
                'message': '元数据已保存；本次接口未返回可用字幕轨，不能据此判断视频没有字幕。未取得正文，不会生成分析。',
                'subtitle_status': 'missing', 'missing_subtitles': True, 'detail': subtitles.detail})
        elif subtitles.status == 'login_required':
            _mark_source(job, content_status='permission_required',
                         meta={'subtitle_status': 'login_required'})
            _finish(job, 'blocked_credentials', {
                'message': '字幕清单需要 B站登录态；请在“平台账号”页提供本机 Cookie 后重试。元数据已保存。',
                'subtitle_status': 'login_required', 'detail': subtitles.detail},
                error_code='CREDENTIAL_REQUIRED')
        elif subtitles.reason_code == 'LANGUAGE_MISMATCH':
            _mark_source(job, content_status='missing',
                         meta={'subtitle_status': 'language_mismatch',
                               'missing_reason': 'language_mismatch'})
            _finish(job, 'succeeded', {
                'message': '该视频字幕语言不符合关键词来源的语言要求，已记录为语言不匹配；未生成内容版本。',
                'subtitle_status': 'language_mismatch', 'missing_subtitles': True,
                'detail': subtitles.detail})
        else:
            _mark_source(job, content_status='permission_required',
                         meta={'subtitle_status': subtitles.status})
            _finish(job, 'failed', {
                'message': subtitles.detail or '平台不允许读取该字幕',
                'subtitle_status': subtitles.status, 'retryable': False},
                error_code=subtitles.reason_code or 'SUBTITLE_UNAVAILABLE')


def _ensure_discovered_source(conn, job, config, entry):
    """Create the pending source for one discovered candidate, idempotently."""
    ws = job['workspace_id']
    page = int(entry['page'])
    canonical = f"https://www.bilibili.com/video/{entry['bvid']}" + (f'?p={page}' if page > 1 else '')
    metadata = {'schema_version': 'source.v1', 'platform': 'bilibili',
                'bvid': entry['bvid'], 'page': page, 'subtitle_status': 'unknown',
                'collector': 'bilibili.v2', 'collector_schema': 'collector.v1',
                'discovery_source_id': str(config['id']), 'discovery_query': ' '.join(config['keywords'])}
    if (entry.get('metadata') or {}).get('synthetic'):
        metadata['synthetic'] = True
    row = one(conn, """SELECT * FROM sources WHERE workspace_id=:ws AND platform=:p
        AND external_id=:e FOR UPDATE""", ws=ws, p='bilibili', e=entry['external_id'])
    if row:
        return row
    row = one(conn, '''INSERT INTO sources(workspace_id,platform,external_id,source_kind,title,
        canonical_url,availability,content_status,rights_policy,metadata,discovery_source_id)
        VALUES(:ws,'bilibili',:e,'video',:title,:url,'unknown','pending',CAST(:rights AS jsonb),
        CAST(:meta AS jsonb),:dsid)
        ON CONFLICT(workspace_id,platform,external_id) DO NOTHING RETURNING *''',
        ws=ws, e=entry['external_id'],
        title=(entry.get('title') or f"B站视频 {entry['bvid']}（第 {page} P，待采集）")[:200],
        url=canonical,
        rights=js({'schema_version': 'rights.v1', 'user_authorized': True,
                   'basis': 'user_keyword_source', 'cloud_allowed': False, 'retention_days': 30}),
        meta=js(metadata), dsid=config['id'])
    if not row:
        row = one(conn, """SELECT * FROM sources WHERE workspace_id=:ws AND platform=:p
            AND external_id=:e""", ws=ws, p='bilibili', e=entry['external_id'])
    return row


def _ensure_collect_job(conn, job, config, source_row, entry, run):
    """Queue one collection for a discovered candidate, reusing an active job."""
    ws = job['workspace_id']
    active = one(conn, """SELECT id,state FROM jobs WHERE workspace_id=:ws AND kind='collect_source'
        AND idempotency_key LIKE :prefix AND state IN ('queued','running','retry_wait','blocked_credentials')
        ORDER BY created_at DESC LIMIT 1""", ws=ws, prefix=f"collect:{source_row['id']}:%")
    if active:
        if active['state'] == 'blocked_credentials':
            one(conn, """UPDATE jobs SET state='queued',stage='queued',fence=fence+1,error_code=NULL,
                checkpoint='{}'::jsonb,lease_until=NULL,updated_at=now() WHERE id=:id RETURNING id,state""",
                id=active['id'])
            return one(conn, 'SELECT id,state FROM jobs WHERE id=:id', id=active['id'])
        return active
    key = f"collect:{source_row['id']}:run{run['id']}:{entry['external_id']}"
    row = one(conn, '''INSERT INTO jobs(workspace_id,kind,input,config_snapshot,idempotency_key)
        VALUES(:ws,'collect_source',CAST(:input AS jsonb),CAST(:snapshot AS jsonb),:key)
        ON CONFLICT(workspace_id,idempotency_key) DO NOTHING RETURNING id,state''',
        ws=ws,
        input=js({'source_id': str(source_row['id']),
                  'url': source_row['canonical_url'], 'page': int(entry['page']),
                  'platform': 'bilibili', 'external_id': entry['external_id'],
                  'languages': list(config['languages']),
                  'language_policy': config['language_policy'] or 'prefer',
                  'discovery_source_id': str(config['id']), 'run_id': str(run['id'])}),
        snapshot=js({'provider': 'none', 'data_policy': 'local_only',
                     'policy_version': POLICY_VERSION, 'collector': 'bilibili.v2',
                     'network': 'real', 'ai_analysis': False,
                     'discovery_source_id': str(config['id'])}),
        key=key)
    if not row:
        row = one(conn, 'SELECT id,state FROM jobs WHERE workspace_id=:ws AND idempotency_key=:key',
                  ws=ws, key=key)
    return row


def _existing_sources(ws, items, config):
    pages = target_pages(config)
    external_ids = [f"{item.video_id}:p{page}" for item in items for page in pages]
    if not external_ids:
        return {}
    with engine.connect() as conn:
        rows = many(conn, """SELECT id,external_id,availability,last_checked_at,content_status
            FROM sources WHERE workspace_id=:ws AND external_id IN
            (SELECT jsonb_array_elements_text(CAST(:ids AS jsonb)))""",
                    ws=ws, ids=js(external_ids))
    return {row['external_id']: row for row in rows}


def _finish_discovery(job, run, source, state, error_code, *, entries=None, requests_used=0,
                      pages_visited=0, detail='', now=None):
    """Write the run ledger, watermark and job state in one short transaction."""
    now = now or datetime.now(timezone.utc)
    ws = job['workspace_id']
    failed = state in ('failed', 'blocked_upstream')
    with engine.begin() as conn:
        saved = []
        for entry in entries or ():
            source_row = None
            if entry['decision'] in ('enqueued', 'refreshed'):
                source_row = _ensure_discovered_source(conn, job, source, entry)
                _ensure_collect_job(conn, job, source, source_row, entry, run)
                entry['source_id'] = source_row['id']
                saved.append(entry)
            execute(conn, '''INSERT INTO discovery_run_items(workspace_id,run_id,external_id,bvid,
                page,title,author_name,published_at,decision,reason,source_id)
                VALUES(:ws,:rid,:e,:bvid,:page,:title,:author,:published,:decision,:reason,:sid)
                ON CONFLICT(workspace_id,run_id,external_id) DO NOTHING''',
                ws=ws, rid=run['id'], e=entry['external_id'], bvid=entry['bvid'],
                page=int(entry['page']), title=(entry.get('title') or '')[:200],
                author=entry.get('author_name'),
                published=entry.get('published_at'), decision=entry['decision'],
                reason=(entry.get('reason') or '')[:300], sid=entry.get('source_id'))
        counters = one(conn, """SELECT
            count(*) AS discovered,
            count(*) FILTER (WHERE decision IN ('enqueued','refreshed')) AS enqueued,
            count(*) FILTER (WHERE decision IN ('keyword_miss','excluded_term')) AS excluded,
            count(*) FILTER (WHERE decision IN ('duplicate','skipped_deleted')) AS duplicates
            FROM discovery_run_items WHERE workspace_id=:ws AND run_id=:rid""",
                       ws=ws, rid=run['id'])
        discovered = int(counters['discovered'])
        excluded = int(counters['excluded'])
        enqueued = int(counters['enqueued'])
        duplicates = int(counters['duplicates'])
        watermark = merge_watermark(source.get('watermark') or {}, saved, now=now,
                                    run_id=str(run['id']))
        streak = int(source['failure_streak']) + 1 if failed else 0
        execute(conn, """UPDATE discovery_sources SET watermark=CAST(:wm AS jsonb),
            last_run_at=:now,last_success_at=CASE WHEN :failed THEN last_success_at ELSE :now END,
            next_run_at=:next,last_error_code=:code,failure_streak=:streak,
            updated_at=now(),revision=revision+1 WHERE id=:id AND workspace_id=:ws""",
                wm=js(watermark), now=now, failed=failed,
                next=next_run_after(now, source['schedule_interval_minutes'], streak),
                code=error_code, streak=streak, id=source['id'], ws=ws)
        execute(conn, """UPDATE discovery_runs SET state=:state,finished_at=:now,
            started_at=COALESCE(started_at,:now),
            requests_used=:requests,pages_visited=:pages,discovered=:discovered,
            matched=:matched,excluded=:excluded,duplicates=:duplicates,enqueued=:enqueued,
            error_code=:code,detail=:detail WHERE id=:rid""",
                state=state, now=now, requests=requests_used, pages=pages_visited,
                discovered=discovered, matched=max(0, discovered - excluded),
                excluded=excluded, duplicates=duplicates, enqueued=enqueued,
                code=error_code, detail=(detail or '')[:1000], rid=run['id'])
        changed = execute(conn, """UPDATE jobs SET state=:state,stage='finished',
            result=CAST(:result AS jsonb),error_code=:code,lease_until=NULL,updated_at=now()
            WHERE id=:jid AND fence=:fence AND state='running'""",
                          state=('failed' if state == 'blocked_upstream' else state),
                          result=js({'discovered': discovered,
                                     'matched': max(0, discovered - excluded),
                                     'excluded': excluded, 'duplicates': duplicates,
                                     'enqueued': enqueued, 'pages': pages_visited,
                                     'requests_used': requests_used,
                                     'discovery_state': state,
                                     'message': detail or '关键词发现完成'}),
                          code=error_code, jid=job['id'], fence=job['fence'])
        if changed.rowcount != 1:
            raise FenceLost()
        execute(conn, "UPDATE job_attempts SET state=:state WHERE job_id=:jid AND fence=:fence",
                state=('failed' if state == 'blocked_upstream' else state),
                jid=job['id'], fence=job['fence'])


def discover(job):
    """Run one keyword discovery: bounded pages, then plan and enqueue in a short tx.

    Network calls happen between checkpoints; every page is persisted in
    ``jobs.checkpoint`` so a crash resumes at the first page not yet read instead
    of re-requesting pages the platform already answered.
    """
    ws = job['workspace_id']
    with engine.connect() as conn:
        source = one(conn, 'SELECT * FROM discovery_sources WHERE id=:id AND workspace_id=:ws',
                     id=job['input'].get('discovery_source_id'), ws=ws)
        run = one(conn, """SELECT * FROM discovery_runs WHERE workspace_id=:ws AND job_id=:jid
            ORDER BY requested_at DESC LIMIT 1""", ws=ws, jid=job['id'])
    if not run:
        _finish(job, 'failed', {'message': '找不到关键词运行记录', 'error': 'DISCOVERY_RUN_NOT_FOUND'},
                error_code='DISCOVERY_RUN_NOT_FOUND')
        return
    if (run['state'] in ('succeeded', 'failed', 'cancelled', 'blocked_upstream')
            and source and not source['deleted_at']):
        # An explicit retry of a finished job starts a new run instead of rewriting
        # the previous (possibly cancelled) run ledger.
        with engine.begin() as conn:
            run = one(conn, """INSERT INTO discovery_runs(workspace_id,source_id,trigger,state,
                request_budget) VALUES(:ws,:sid,'retry','queued',:budget) RETURNING *""",
                      ws=ws, sid=run['source_id'],
                      budget=source['max_requests_per_run'])
            execute(conn, 'UPDATE discovery_runs SET job_id=:jid WHERE id=:rid',
                    jid=job['id'], rid=run['id'])
    if not source or source['deleted_at']:
        _finish_discovery(job, run, source or {'id': None, 'watermark': {}, 'failure_streak': 0,
                                                'schedule_interval_minutes': 1440,
                                                'workspace_id': ws},
                          'cancelled', 'DISCOVERY_SOURCE_DELETED',
                          detail='关键词来源已删除，本次运行取消')
        return
    if not source['network_authorized']:
        _finish_discovery(job, run, source, 'blocked_upstream', 'NETWORK_NOT_AUTHORIZED',
                          detail='该关键词来源尚未获得本机联网授权，未发起任何请求')
        return
    try:
        collector = get_collector(source['platform'])
        if not collector.capabilities().keyword_discovery:
            raise CollectorError('DISCOVERY_NOT_IMPLEMENTED', '该平台尚未实现关键词发现', False)
    except CollectorError as exc:
        _finish_discovery(job, run, source, 'failed', exc.code, detail=exc.message)
        return
    # Load platform credential for WBI signing (search API requires it).
    discovery_credential = None
    try:
        with engine.connect() as conn:
            connection = one(conn,
                'SELECT * FROM platform_connections WHERE workspace_id=:ws AND platform=:p',
                ws=ws, p=source['platform'])
        discovery_credential = _load_credential(connection)
    except SecretError as exc:
        _finish_discovery(job, run, source, 'blocked_upstream', exc.code, detail=exc.message)
        return
    checkpoint = job.get('checkpoint') or {}
    pages = {str(key): list(value) for key, value in (checkpoint.get('pages') or {}).items()}
    requests_used = int(checkpoint.get('requests') or 0)
    block = checkpoint.get('block')
    max_pages = int(source['max_pages_per_run'])
    watermark = source['watermark'] if isinstance(source['watermark'], dict) else {}
    newest = _as_time(watermark.get('newest_published_at'))
    with LeaseKeeper(engine, job) as keeper:
        page = 1
        while page <= max_pages and not block:
            if str(page) in pages:
                page += 1
                continue
            if requests_used >= int(source['max_requests_per_run']):
                break
            try:
                result = collector.discover(DiscoveryQuery(keywords=tuple(source['keywords']),
                                                           page=page),
                                            credential=discovery_credential)
            except CollectorError as exc:
                requests_used += 1
                block = {'code': exc.code, 'message': exc.message,
                         'retryable': exc.retryable, 'details': exc.details, 'page': page}
                _checkpoint(job, f'discover_page_{page}',
                            {'pages': pages, 'requests': requests_used, 'block': block})
                break
            requests_used += 1
            pages[str(page)] = [item.to_checkpoint() for item in result.items]
            _checkpoint(job, f'discover_page_{page}',
                        {'pages': pages, 'requests': requests_used})
            _progress(job, f'discover_page_{page}',
                      {'discovered_so_far': sum(len(value) for value in pages.values()),
                       'requests_used': requests_used})
            if keeper.lost or _cancelled(job):
                _fence_lost(job)
                return
            if not result.items or not result.has_more:
                break
            if newest is not None and all(item.published_at is None or item.published_at <= newest
                                          for item in result.items):
                # Incremental stop: the whole page is at/behind the watermark.
                break
            page += 1
        if keeper.lost or _cancelled(job):
            _fence_lost(job)
            return
    if block:
        state = 'blocked_upstream' if block['code'] in (
            'RISK_CONTROL', 'CREDENTIAL_EXPIRED', 'PERMISSION_REQUIRED', 'DISCOVERY_DENIED',
            'RATE_LIMITED', 'NETWORK_TIMEOUT', 'UPSTREAM_UNAVAILABLE', 'REQUEST_BUDGET_EXCEEDED',
        ) else 'failed'
        _finish_discovery(job, run, source, state, block['code'],
                          requests_used=requests_used, pages_visited=len(pages),
                          detail=block['message'])
        return
    items = []
    for page_no in sorted(pages, key=int):
        items.extend(DiscoveryItem.from_checkpoint(value) for value in pages[page_no])
    now = datetime.now(timezone.utc)
    entries = plan_candidates(items, source, existing=_existing_sources(ws, items, source), now=now)
    enqueued = sum(1 for entry in entries if entry['decision'] in ('enqueued', 'refreshed'))
    message = (f'关键词发现完成：读取 {len(pages)} 页、{len(items)} 条候选，'
               f'{enqueued} 条进入采集队列。' if entries else '关键词发现完成：没有可用候选。')
    _finish_discovery(job, run, source, 'succeeded', None, entries=entries,
                      requests_used=requests_used, pages_visited=len(pages),
                      detail=message, now=now)


def _as_time(value):
    if not value:
        return None
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        except ValueError:
            return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _fact_materials(conn, ws, claim_id):
    """Materials that can be used for this claim, with their stored availability."""
    return many(conn, f"""SELECT r.id,r.relation_type,r.status,r.payload,r.to_id,r.created_at,
        s.title AS material_title,s.platform,s.author_name,s.canonical_url,
        s.availability,s.id AS source_id,v.retention_expires_at,v.version_no,
        s.current_version_id,
        (s.current_version_id IS NOT NULL AND v.id<>s.current_version_id) AS superseded,
        (v.retention_expires_at<=now()) AS retention_expired
        FROM relations r JOIN source_versions v ON v.id=r.to_id
        JOIN sources s ON s.id=v.source_id
        WHERE r.workspace_id=:ws AND r.from_id=:cid
        AND r.relation_type IN ({','.join("'%s'" % item for item in FACT_RELATION_TYPES)})
        AND r.status IN ('accepted','proposed')
        ORDER BY r.created_at,r.id""", ws=ws, cid=claim_id)


def _fact_check_advisory(job, claim, materials, assessment):
    """Optional routed model advisory over material metadata; never decides a grade.

    The call is bounded, budget-reserved and recorded in the invocation ledger like
    every other model call. A missing route, exhausted budget, policy block or invalid
    answer leaves the deterministic assessment untouched: the record still requires
    human review and the publication gate is not lowered (AC44).
    """
    ws = job['workspace_id']
    with engine.connect() as conn:
        route = _task_route(conn, ws, 'fact_check')
    if not route:
        return {'status': 'not_configured'}
    if not materials:
        # There is nothing for a model to assess; never spend a call on an empty set.
        return {'status': 'skipped', 'error': 'NO_FACT_MATERIALS',
                'message': '库内没有事实材料，未调用事实核查模型'}
    try:
        messages, coverage = ai_factcheck.build_fact_check_messages(
            claim, materials, max_input_tokens=route['max_input_tokens'])
    except ai_factcheck.FactCheckError as exc:
        return {'status': 'skipped', 'error': exc.code, 'message': exc.message}
    summary = {'input_kind': 'fact_check_materials', 'claim_id': str(claim['id']),
               'materials_included': coverage['materials'],
               'materials_total': coverage['materials_total'],
               'truncated': coverage['truncated'],
               'message_count': len(messages)}
    try:
        with LeaseKeeper(engine, job) as keeper:
            result = ai_gateway.invoke_messages('fact_check', workspace_id=ws,
                                                messages=messages, summary=summary,
                                                job_id=job['id'])
            lost = keeper.lost
        if lost:
            raise FenceLost()
    except ai_gateway.GatewayError as exc:
        return {'status': 'unavailable', 'error': exc.code, 'message': exc.message,
                'invocation_id': (exc.details or {}).get('invocation_id')}
    try:
        advice = ai_factcheck.validate_advisory(result.get('parsed'), coverage)
    except ai_factcheck.FactCheckError as exc:
        return {'status': 'invalid', 'error': exc.code, 'message': exc.message,
                'invocation_id': result.get('invocation_id'), 'model': result.get('model'),
                'cost': result.get('cost')}
    return {'status': 'performed', **advice,
            'agreement': ai_factcheck.agreement(advice['verdict'], assessment['status']),
            'truncated': bool(coverage['truncated']),
            'model': result.get('model'), 'provider': result.get('provider'),
            'invocation_id': result.get('invocation_id'), 'cost': result.get('cost')}


def fact_check(job):
    """Append one pending fact-check record from library materials.

    The deterministic rule assessment is authoritative. When a ``fact_check`` model
    route is explicitly enabled, one bounded, budget-reserved advisory call is made
    over material *metadata* and stored in ``model_runs``; it never changes the grade,
    the record status or ``claims.fact_check_status``. No external retrieval happens
    here and the manifest stays ``kb_only``. Only a human acceptance appends the
    accepted version and moves the verification pointer.
    """
    ws, jid, fence = job['workspace_id'], job['id'], job['fence']
    claim_id = (job.get('input') or {}).get('claim_id')
    trigger = (job.get('input') or {}).get('trigger') or 'manual'
    with engine.connect() as conn:
        current = one(conn, 'SELECT state,fence FROM jobs WHERE id=:id', id=jid)
        claim = one(conn, '''SELECT c.*,v.segments,v.retention_expires_at,s.availability
            FROM claims c JOIN source_versions v ON v.id=c.origin_version_id
            JOIN sources s ON s.id=v.source_id
            WHERE c.id=:cid AND c.workspace_id=:ws''', cid=claim_id, ws=ws)
        materials = _fact_materials(conn, ws, claim_id) if claim else []
        nodes = many(conn, """SELECT r.to_id FROM relations r WHERE r.workspace_id=:ws
            AND r.from_id=:cid AND r.relation_type='about' AND r.status='accepted'""",
                     ws=ws, cid=claim_id) if claim else []
        node_ids = [str(row['to_id']) for row in nodes]
        candidates = []
        if node_ids:
            candidates = many(conn, """SELECT c2.id,c2.fact_check_status FROM claims c2
                JOIN relations r ON r.from_id=c2.id
                WHERE r.workspace_id=:ws AND r.relation_type='about' AND r.status='accepted'
                AND r.to_id=ANY(CAST(:nodes AS uuid[])) AND c2.id<>:cid
                ORDER BY c2.created_at DESC,c2.id DESC LIMIT 20""",
                             ws=ws, nodes=node_ids, cid=claim_id)
    if not current or current['fence'] != fence or current['state'] not in ('running', 'cancel_requested'):
        return
    if current['state'] == 'cancel_requested':
        _cancel_process_job(job)
        return
    if not claim or claim['availability'] != 'available':
        _finish(job, 'failed', {'mode': 'kb_only', 'fact_confirmation': False,
                                'message': '主张或来源已不可用，未生成核查记录'},
                error_code='CLAIM_UNAVAILABLE')
        return
    accepted = [item for item in materials if item['status'] == 'accepted']
    proposed = [item for item in materials if item['status'] == 'proposed']
    support = [item for item in materials if item['relation_type'] == 'fact_supported_by']
    refute = [item for item in materials if item['relation_type'] == 'fact_refuted_by']
    context = [item for item in materials if item['relation_type'] == 'fact_context_by']
    assessment = verification_engine.assess(claim=claim, materials=materials)
    snapshot = verification_engine.build_snapshot(claim=claim, materials=materials)
    advisory = _fact_check_advisory(job, claim, materials, assessment)
    model_runs = []
    if advisory.get('status') != 'not_configured':
        entry = {'task': 'fact_check', 'schema_version': ai_factcheck.SCHEMA_VERSION,
                 'status': advisory.get('status')}
        for field in ('verdict', 'agreement', 'model', 'provider', 'invocation_id',
                      'cost', 'truncated'):
            if advisory.get(field) is not None:
                entry[field] = advisory[field]
        if advisory.get('status') == 'performed':
            entry['reason'] = advisory.get('reason')
            entry['limitations'] = advisory.get('limitations') or []
            entry['material_refs'] = advisory.get('material_refs') or []
            entry['rejected'] = advisory.get('rejected') or []
            entry['confidence_ignored'] = bool(advisory.get('confidence_ignored'))
        else:
            entry['error_code'] = advisory.get('error')
            entry['error_message'] = str(advisory.get('message') or '')[:300]
        model_runs.append(entry)
    manifest = {
        'schema_version': 'retrieval.v1',
        'mode': 'kb_only',
        'external_reads': [],
        'query': {'claim_id': str(claim['id']), 'node_ids': node_ids,
                  'aspect': claim['aspect'], 'qualifiers': claim['qualifiers']},
        'candidate_claims': [{'id': str(row['id']), 'fact_check_status': row['fact_check_status']}
                             for row in candidates],
        'material_counts': {'support': len(support), 'refute': len(refute),
                            'context': len(context), 'accepted': len(accepted),
                            'proposed': len(proposed)},
        'result': 'materials_found' if materials else 'no_fact_evidence',
        'model_route': ('fact_check_advisory' if advisory.get('status') != 'not_configured'
                        else 'not_wired_in_this_phase'),
        'model_advisory_status': advisory.get('status'),
        'limitations': ['未配置外部检索连接器；空结果不是反证',
                        '只评估库内已保存并已接受的证据材料'] + (
            [] if advisory.get('status') == 'not_configured'
            else ['模型仅提供建议，不改变分档、状态或发布门槛']),
    }
    applicability = {'claim_qualifiers': claim['qualifiers'],
                     'materials': [{'id': str(item['id']),
                                    'applicability': (item['payload'] or {}).get('applicability')}
                                   for item in materials]}
    result = {'mode': 'kb_only', 'status': assessment['status'],
              'support_strength': assessment['support_strength'],
              'refutation_strength': assessment['refutation_strength'],
              'materials': len(materials), 'accepted_materials': len(accepted),
              'proposed_materials': len(proposed),
              'stale_support_materials': assessment.get('stale_support_materials', 0),
              'model_advisory': advisory.get('status'),
              'model_verdict': advisory.get('verdict'),
              'model_agreement': advisory.get('agreement'),
              'human_review': 'pending', 'fact_confirmation': False,
              'message': '已生成待人工复核的核查记录；接受前不改变事实状态与发布门槛'}
    with engine.begin() as conn:
        locked = one(conn, 'SELECT state,fence FROM jobs WHERE id=:id FOR UPDATE', id=jid)
        if not locked or locked['fence'] != fence:
            return
        if locked['state'] != 'running':
            raise FenceLost()
        version_no = one(conn, '''SELECT coalesce(max(version_no),0)+1 AS n FROM verification_records
            WHERE workspace_id=:ws AND claim_id=:cid''', ws=ws, cid=claim['id'])['n']
        record = one(conn, '''INSERT INTO verification_records(workspace_id,record_kind,claim_id,version_no,
            schema_version,policy_version,input_fingerprint,target_revision,trigger,job_id,checker_type,
            retrieval_manifest,evidence_snapshot,applicability,valid_until,assessment,model_runs,review_status)
            VALUES(:ws,'claim_fact_check',:cid,:no,:schema,:policy,:fp,:rev,:trigger,:jid,'rule',
            CAST(:manifest AS jsonb),CAST(:snapshot AS jsonb),CAST(:applicability AS jsonb),:valid,
            CAST(:assessment AS jsonb),CAST(:runs AS jsonb),'pending') RETURNING *''',
            ws=ws, cid=claim['id'], no=version_no, schema=verification_engine.SCHEMA_VERSION,
            policy=POLICY_VERSION, fp=claim['fingerprint'], rev=claim['revision'],
            trigger=trigger, jid=jid, manifest=js(manifest), snapshot=js(snapshot),
            applicability=js(applicability), valid=verification_engine.valid_until(claim),
            assessment=js(assessment), runs=js(model_runs))
        result['record_id'] = str(record['id'])
        result['version_no'] = version_no
        changed = execute(conn, '''UPDATE jobs SET state='succeeded',stage='finished',
            result=CAST(:result AS jsonb),error_code=NULL,lease_until=NULL,updated_at=now()
            WHERE id=:id AND fence=:f AND state='running' ''',
                          id=jid, f=fence, result=js(result))
        if changed.rowcount != 1:
            raise FenceLost()
        execute(conn, "UPDATE job_attempts SET state='succeeded' WHERE job_id=:id AND fence=:f",
                id=jid, f=fence)


def process(job):
    """Dispatch one ``process_source`` job.

    The M1 synthetic-fixture path (``_process_local``) is unchanged: it only reads
    the demo grammar and never contacts a model. When the ``claim_extraction`` task
    route is explicitly enabled in ``model`` mode and the stored version is not a
    synthetic fixture, extraction goes through the budgeted gateway: reserve, HTTP
    outside any transaction, settle, then write only server-validated claims in one
    fenced transaction. Any other case falls back to the local path, which keeps
    real material at ``needs_review`` instead of pretending it was analysed.
    """
    ws = job['workspace_id']
    with engine.connect() as conn:
        version = one(conn, """SELECT s.metadata AS metadata,s.availability FROM source_versions v
            JOIN sources s ON s.id=v.source_id WHERE v.id=:id AND v.workspace_id=:ws""",
                      id=job['input']['version_id'], ws=ws)
        model_route = _route_uses_model(conn, ws)
    if (model_route and version and version['availability'] == 'available'
            and not (version['metadata'] or {}).get('demo')):
        return _process_with_model(job)
    return _process_local(job)


def _route_uses_model(conn, ws) -> bool:
    route = one(conn, "SELECT enabled,mode FROM task_routes WHERE workspace_id=:ws AND task=:task",
                ws=ws, task='claim_extraction')
    return bool(route and route['enabled'] and route['mode'] == 'model')


def _cancel_process_job(job):
    with engine.begin() as conn:
        changed = execute(conn, """UPDATE jobs SET state='cancelled',stage='cancelled',lease_until=NULL,updated_at=now()
            WHERE id=:id AND fence=:fence AND state IN ('running','cancel_requested')""",
                          id=job['id'], fence=job['fence'])
        if changed.rowcount != 1:
            raise FenceLost()
        execute(conn, "UPDATE job_attempts SET state='cancelled' WHERE job_id=:id AND fence=:fence",
                id=job['id'], fence=job['fence'])


def _process_local(job):
    """Process one claimed job in a single short transaction.

    Writes and the final state update commit together. A stale fence therefore
    cannot publish partial results: either the guarded UPDATE still matches the
    claimed attempt, or the whole transaction rolls back.
    """
    ws, jid, fence = job['workspace_id'], job['id'], job['fence']
    with engine.begin() as conn:
        current = one(conn, 'SELECT * FROM jobs WHERE id=:id FOR UPDATE', id=jid)
        if not current:
            return
        if current['state'] == 'cancel_requested':
            execute(conn, "UPDATE job_attempts SET state='cancelled' WHERE job_id=:id AND fence=:f", id=jid, f=fence)
            execute(conn, "UPDATE jobs SET state='cancelled',stage='cancelled',fence=fence+1,lease_until=NULL,updated_at=now() WHERE id=:id", id=jid)
            return
        if current['fence'] != fence or current['state'] != 'running':
            return
        # Short, deterministic Mock processing holds the job lock; real network stages
        # must renew a lease and release the lock between stages (see collect()).
        execute(conn, 'SELECT pg_advisory_xact_lock(hashtextextended(CAST(:ws AS text),0))', ws=str(ws))
        version = one(conn, '''SELECT v.*,s.title,s.author_name,s.metadata,s.availability FROM source_versions v JOIN sources s ON s.id=v.source_id
            WHERE v.id=:id AND v.workspace_id=:ws''', id=job['input']['version_id'], ws=ws)
        if not version or version['availability'] != 'available':
            execute(conn, "UPDATE jobs SET state='cancelled',stage='cancelled',lease_until=NULL,updated_at=now() WHERE id=:id AND fence=:f", id=jid, f=fence)
            execute(conn, "UPDATE job_attempts SET state='cancelled' WHERE job_id=:id AND fence=:f", id=jid, f=fence)
            return
        candidates = AIGateway().extract(version['segments'], demo=version['metadata'].get('demo', False))
        published, held = 0, 0
        for candidate in candidates:
            anchor = candidate.pop('anchor')
            validate_anchor(version['segments'], anchor)
            fingerprint = digest(js(candidate))
            claim = one(conn, '''INSERT INTO claims(workspace_id,origin_version_id,statement,claim_kind,aspect,qualifiers,polarity,importance,verifiability,review_status,fingerprint,provenance)
                VALUES(:ws,:vid,:statement,:claim_kind,:aspect,CAST(:qualifiers AS jsonb),:polarity,:importance,:verifiability,'validated',:fingerprint,CAST(:provenance AS jsonb))
                ON CONFLICT(workspace_id,origin_version_id,fingerprint) DO NOTHING RETURNING *''',
                ws=ws, vid=version['id'], **{k: v for k, v in candidate.items() if k not in ('entity','qualifiers')}, qualifiers=js(candidate['qualifiers']), fingerprint=fingerprint, provenance=js({'job_id':jid,'provider':'mock','schema_version':'claim_extract.v1'}))
            if not claim:
                continue
            evidence_id = relation(conn, ws, 'claim', claim['id'], 'evidenced_by', 'source_version', version['id'], anchor)
            node = one(conn, "SELECT * FROM nodes WHERE workspace_id=:ws AND identity_keys->>'fixture_id'='plugin-x'", ws=ws)
            if not node:
                node = one(conn, '''INSERT INTO nodes(workspace_id,node_kind,canonical_name,entity_type,status,identity_keys,description)
                  VALUES(:ws,'entity','Plugin X','plugin','active','{"fixture_id":"plugin-x"}','仅用于流程测试的虚构产品；不是真实产品信息。') RETURNING *''', ws=ws)
            relation(conn, ws, 'claim', claim['id'], 'about', 'node', node['id'], {'role':'primary'})
            prior = many(conn, '''SELECT c.* FROM claims c JOIN relations r ON r.from_id=c.id WHERE r.to_id=:nid AND r.relation_type='about'
                 AND c.workspace_id=:ws AND c.id<>:cid ORDER BY c.created_at DESC LIMIT 20''', nid=node['id'], ws=ws, cid=claim['id'])
            for other in prior:
                if other['qualifiers'] != claim['qualifiers']:
                    relation(conn, ws, 'claim', claim['id'], 'qualifies', 'claim', other['id'], {'reason':'版本或条件不同，不能据此判定矛盾','comparison_scope':claim['qualifiers']})
            verification = one(conn, '''INSERT INTO verification_records(workspace_id,claim_id,version_no,policy_version,input_fingerprint,target_revision,trigger,checker_type,retrieval_manifest,evidence_snapshot,applicability,assessment,review_status,review_reason,valid_until)
              VALUES(:ws,:cid,1,:policy,:fp,1,'new_claim','rule',CAST(:manifest AS jsonb),'[]',CAST(:scope AS jsonb),CAST(:assessment AS jsonb),'accepted','仅接受证据不足这一评估，不授予事实资格',now()+interval '7 days') RETURNING *''',
              ws=ws,cid=claim['id'],policy=POLICY_VERSION,fp=fingerprint, manifest=js({'mode':'kb_only','query':{'entity_id':node['id'],'aspect':claim['aspect'],'qualifiers':claim['qualifiers']},'candidate_claim_ids':[r['id'] for r in prior], 'external_reads':[], 'result':'no_accepted_fact_evidence'}),scope=js(claim['qualifiers']),assessment=js({'status':'insufficient','support_strength':'E0','refutation_strength':'E0','gaps':['仅有表达溯源，未取得可检查的事实材料'],'unresolved_counterevidence':False}))
            execute(conn, "UPDATE claims SET current_verification_id=:v,verification_validity='current',fact_check_status='insufficient' WHERE id=:id", v=verification['id'],id=claim['id'])
            claim.update(current_verification_id=verification['id'],verification_validity='current',fact_check_status='insufficient')
            decision = PublicationPolicy.evaluate('feed',claim,verification,True)
            if decision['action'] != 'allow':
                held += 1
                continue
            display = f'示例作者观点（非事实确认）：{claim["statement"]}'
            body = [{'text':display,'claim_id':str(claim['id']),'relation_id':str(evidence_id),'verification_record_ids':[str(verification['id'])],**decision}]
            key = digest(js([str(node['id']),claim['aspect'],claim['qualifiers'],claim['statement']]))
            update = one(conn, '''INSERT INTO updates(workspace_id,update_type,title,summary,change_reason,event_key,state,first_published_at,input_fingerprint,structured_body,provenance)
                VALUES(:ws,'new_viewpoint',:title,:summary,'新增有原文引用的作者观点',:key,'published',now(),:fp,CAST(:body AS jsonb),CAST(:provenance AS jsonb))
                ON CONFLICT(workspace_id,event_key) DO UPDATE SET revision=updates.revision+1,last_changed_at=now(),structured_body=updates.structured_body || EXCLUDED.structured_body,input_fingerprint=EXCLUDED.input_fingerprint RETURNING *''',
                ws=ws,title=display,summary=display,key=key,fp=fingerprint,body=js(body),provenance=js({'provider':'mock','demo':True}))
            relation(conn, ws, 'update', update['id'], 'includes', 'claim', claim['id'], {'role':'support'})
            relation(conn, ws, 'update', update['id'], 'about', 'node', node['id'], {})
            execute(conn, 'UPDATE updates SET revision_history=revision_history || CAST(:snapshot AS jsonb) WHERE id=:id', id=update['id'],snapshot=js([{'revision':update['revision'],'title':update['title'],'structured_body':update['structured_body'],'reason':'新增表达证据','at':update['last_changed_at']}]))
            published += 1
        state = 'succeeded' if candidates else 'needs_review'
        changed = execute(conn, '''UPDATE jobs SET state=:state,stage='finished',result=CAST(:result AS jsonb),lease_until=NULL,updated_at=now()
          WHERE id=:id AND fence=:f AND state='running' ''', id=jid,f=fence,state=state,result=js({'claims':len(candidates),'published':published,'held':held,'message':'示例处理完成' if candidates else '原文已保存。Mock 不理解任意材料，等待配置真实模型或人工复核。'}))
        if changed.rowcount != 1:
            # The fence moved while this attempt was writing; roll everything back.
            raise RuntimeError('JOB_FENCE_LOST')
        execute(conn, "UPDATE job_attempts SET state=:state WHERE job_id=:id AND fence=:f", state=state,id=jid,f=fence)


def _process_with_model(job):
    """Extract claims with the routed model, then write server-validated evidence.

    The provider call never runs inside a database transaction and the lease is
    renewed while it runs. Only after the answer passes ``validate_claim_output``
    does one fenced transaction insert claims, evidence relations, verification
    records and (when the publication policy allows it) updates.
    """
    ws, jid, fence = job['workspace_id'], job['id'], job['fence']
    with engine.connect() as conn:
        current = one(conn, 'SELECT state,fence FROM jobs WHERE id=:id', id=jid)
        version = one(conn, '''SELECT v.*,s.title,s.author_name,s.metadata,s.availability
            FROM source_versions v JOIN sources s ON s.id=v.source_id
            WHERE v.id=:id AND v.workspace_id=:ws''', id=job['input']['version_id'], ws=ws)
        route = one(conn, "SELECT * FROM task_routes WHERE workspace_id=:ws AND task='claim_extraction'", ws=ws)
    if not current or current['fence'] != fence or current['state'] not in ('running', 'cancel_requested'):
        return
    if current['state'] == 'cancel_requested':
        _cancel_process_job(job)
        return
    if not version or version['availability'] != 'available':
        _cancel_process_job(job)
        return
    if not route:
        _finish(job, 'needs_review', {'extraction': 'model',
                                      'message': '任务路由已被移除，未执行模型抽取；原文已保存'})
        return
    try:
        messages, coverage = ai_extraction.build_claim_messages(
            version['segments'], max_input_tokens=route['max_input_tokens'])
    except ai_extraction.ExtractionError as exc:
        _finish(job, 'needs_review', {'extraction': 'model', 'message': exc.message,
                                      'error': exc.code}, error_code=exc.code)
        return
    summary = {'input_kind': 'source_version',
               'input_chars': coverage['included_chars'],
               'message_count': len(messages),
               'segments_included': coverage['included_segments'],
               'segments_total': coverage['total_segments'],
               'truncated': coverage['truncated'],
               'source_version_id': str(version['id'])}
    try:
        with LeaseKeeper(engine, job) as keeper:
            result = ai_gateway.invoke_messages('claim_extraction', workspace_id=ws,
                                                messages=messages, summary=summary, job_id=jid)
            lost = keeper.lost
        if lost:
            raise FenceLost()
    except ai_gateway.GatewayError as exc:
        _finish_model_error(job, exc)
        return
    try:
        validated = ai_extraction.validate_claim_output(result.get('parsed'), version['segments'], coverage)
    except ai_extraction.ExtractionError as exc:
        _finish(job, 'needs_review',
                {'extraction': 'model', 'message': exc.message, 'error': exc.code,
                 'invocation_id': result.get('invocation_id'), 'model': result.get('model'),
                 'cost': result.get('cost'), 'claims': 0, 'published': 0, 'held': 0},
                error_code=exc.code)
        return
    counts = validated['counts']
    if counts['proposed'] == 0:
        _finish(job, 'needs_review',
                {'extraction': 'model', 'message': '模型未在该材料中抽取到主张；原文已保存。',
                 'invocation_id': result.get('invocation_id'), 'model': result.get('model'),
                 'cost': result.get('cost'), 'claims': 0, 'published': 0, 'held': 0,
                 'fact_confirmation': False, 'expression_review': 'not_performed'},
                error_code=None)
        return
    # Anchored claims and unanchored proposals are written together; the latter stay
    # as ``candidate`` for review and the job ends at needs_review when none anchored.
    review_state, entity_state = {'status': 'not_configured'}, {'status': 'not_configured'}
    if validated['claims']:
        review_state = _review_claims(job, version, validated)
        if review_state.get('status') in ('not_configured', 'performed'):
            entity_state = _suggest_entities(job, validated)
    _store_model_claims(job, version, result, validated,
                        review=review_state, entities=entity_state)


def _finish_model_error(job, exc):
    """A refused/failed model call is reported; it never writes claims."""
    state = 'blocked_budget' if exc.code == 'BUDGET_EXHAUSTED' else 'failed'
    payload = {'extraction': 'model', 'message': exc.message, 'error': exc.code,
               'claims': 0, 'published': 0, 'held': 0}
    if isinstance(exc.details, dict) and exc.details.get('invocation_id'):
        payload['invocation_id'] = exc.details['invocation_id']
    _finish(job, state, payload, error_code=exc.code)


def _reason_counts(items):
    counts = {}
    for item in items or []:
        reason = item.get('reason')
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def _task_route(conn, ws, task):
    return one(conn, """SELECT * FROM task_routes WHERE workspace_id=:ws
        AND task=:task AND enabled AND mode='model'""", ws=ws, task=task)


def _review_claims(job, version, validated):
    """Run the optional expression-support review; never write or publish here.

    A configured route that cannot answer (budget, policy, network, invalid output)
    returns a non-``performed`` state. The caller then keeps every anchored claim as
    a candidate instead of publishing something that was not reviewed.
    """
    ws = job['workspace_id']
    with engine.connect() as conn:
        route = _task_route(conn, ws, 'evidence_review')
    if not route:
        return {'status': 'not_configured'}
    try:
        messages, coverage = ai_review.build_review_messages(
            validated['claims'], version['segments'],
            max_input_tokens=route['max_input_tokens'])
    except ai_review.ReviewError as exc:
        return {'status': 'skipped', 'error': exc.code, 'message': exc.message}
    summary = {'input_kind': 'claim_expression_review',
               'claims_included': coverage['claims'],
               'message_count': len(messages),
               'truncated': coverage['truncated'],
               'source_version_id': str(version['id'])}
    try:
        with LeaseKeeper(engine, job) as keeper:
            result = ai_gateway.invoke_messages('evidence_review', workspace_id=ws,
                                                messages=messages, summary=summary,
                                                job_id=job['id'])
            lost = keeper.lost
        if lost:
            raise FenceLost()
    except ai_gateway.GatewayError as exc:
        return {'status': 'unavailable', 'error': exc.code, 'message': exc.message,
                'invocation_id': (exc.details or {}).get('invocation_id')}
    try:
        reviewed = ai_review.validate_review_output(result.get('parsed'), coverage)
    except ai_review.ReviewError as exc:
        return {'status': 'invalid', 'error': exc.code, 'message': exc.message,
                'invocation_id': result.get('invocation_id'), 'model': result.get('model'),
                'cost': result.get('cost')}
    for ref, index in coverage['map'].items():
        item = reviewed['reviews'].get(ref)
        if item:
            validated['claims'][index]['expression_review'] = item
    return {'status': 'performed', **reviewed['counts'],
            'truncated': bool(reviewed.get('truncated') or coverage.get('truncated')),
            'rejected_reasons': _reason_counts(reviewed['rejected']),
            'model': result.get('model'), 'provider': result.get('provider'),
            'invocation_id': result.get('invocation_id'), 'cost': result.get('cost')}


def _suggest_entities(job, validated):
    """Ask the routed model for existing entity links and new-entity proposals."""
    ws = job['workspace_id']
    with engine.connect() as conn:
        route = _task_route(conn, ws, 'entity_disambiguation')
        if not route:
            return {'status': 'not_configured'}
        nodes = many(conn, """SELECT id,canonical_name,entity_type,aliases FROM nodes
            WHERE workspace_id=:ws AND node_kind='entity'
              AND status IN ('active','provisional')
            ORDER BY canonical_name,id LIMIT 80""", ws=ws)
    try:
        messages, coverage = ai_entities.build_entity_messages(
            validated['claims'], nodes, max_input_tokens=route['max_input_tokens'])
    except ai_entities.EntityError as exc:
        return {'status': 'skipped', 'error': exc.code, 'message': exc.message}
    summary = {'input_kind': 'claim_entity_candidates',
               'claims_included': coverage['claims'],
               'entities_included': coverage['entities'],
               'message_count': len(messages), 'truncated': coverage['truncated']}
    try:
        with LeaseKeeper(engine, job) as keeper:
            result = ai_gateway.invoke_messages('entity_disambiguation', workspace_id=ws,
                                                messages=messages, summary=summary,
                                                job_id=job['id'])
            lost = keeper.lost
        if lost:
            raise FenceLost()
    except ai_gateway.GatewayError as exc:
        return {'status': 'unavailable', 'error': exc.code, 'message': exc.message,
                'invocation_id': (exc.details or {}).get('invocation_id')}
    try:
        suggested = ai_entities.validate_entity_output(result.get('parsed'), coverage)
    except ai_entities.EntityError as exc:
        return {'status': 'invalid', 'error': exc.code, 'message': exc.message,
                'invocation_id': result.get('invocation_id'), 'model': result.get('model'),
                'cost': result.get('cost')}
    for link in suggested['links']:
        index = coverage['map'][link['claim_ref']]
        validated['claims'][index].setdefault('entity_links', []).append(link)
    for entry in suggested['new_entities']:
        for ref in entry.get('claim_refs') or []:
            index = coverage['map'].get(ref)
            if index is None:
                continue
            # First new-entity wins for grouping; a claim belongs to one primary entity.
            validated['claims'][index].setdefault('new_entity', entry)
    return {'status': 'performed', **suggested['counts'],
            'new_entities': suggested['new_entities'],
            'truncated': coverage['truncated'],
            'rejected_reasons': _reason_counts(suggested['rejected']),
            'model': result.get('model'), 'provider': result.get('provider'),
            'invocation_id': result.get('invocation_id'), 'cost': result.get('cost')}


def _store_model_claims(job, version, result, validated, *, review=None, entities=None):
    """Write model claims and finish the job in one fenced transaction.

    Anchored claims become ``validated`` only when the server located every quote
    *and* (if the expression-review route is enabled) the separately routed review
    judged that quote to support the claim. ``fact_check_status`` stays
    ``insufficient``; semantic support is not fact confirmation. Claims without a
    valid anchor, without a supporting review, or whose review could not be
    completed are stored as ``candidate`` and never produce an update.
    """
    review = review or {'status': 'not_configured'}
    entities = entities or {'status': 'not_configured'}
    ws, jid, fence = job['workspace_id'], job['id'], job['fence']
    counts = validated['counts']
    with engine.begin() as conn:
        current = one(conn, 'SELECT * FROM jobs WHERE id=:id FOR UPDATE', id=jid)
        if not current or current['fence'] != fence or current['state'] != 'running':
            raise FenceLost()
        execute(conn, 'SELECT pg_advisory_xact_lock(hashtextextended(CAST(:ws AS text),0))', ws=str(ws))
        provenance = {'provider': 'model', 'task': 'claim_extraction',
                      'schema_version': ai_extraction.SCHEMA_VERSION,
                      'invocation_id': result.get('invocation_id'),
                      'model': result.get('model'),
                      'job_id': jid,
                      'server_validation': 'quote_anchor',
                      'semantic_review': review.get('status', 'not_configured')}
        published, held_policy, held_expression, anchors, proposed = 0, 0, 0, 0, 0
        claim_ids = []
        entity_node_ids = {}
        created_entities = 0
        groups = {}
        if entities.get('status') == 'performed':
            wanted = {}
            for claim_data in validated['claims']:
                entry = claim_data.get('new_entity')
                if entry and entry.get('name'):
                    wanted.setdefault(entry['name'], entry)
            if wanted:
                existing = {row['canonical_name']: row['id'] for row in many(conn, """SELECT id,canonical_name
                    FROM nodes WHERE workspace_id=:ws AND node_kind='entity'""", ws=ws)}
                for name, entry in wanted.items():
                    node_id = existing.get(name)
                    if not node_id:
                        node = one(conn, '''INSERT INTO nodes(workspace_id,node_kind,canonical_name,entity_type,aliases,description,status,metadata)
                            VALUES(:ws,'entity',:name,:etype,CAST(:aliases AS jsonb),:desc,'provisional',CAST(:meta AS jsonb)) RETURNING id''',
                                   ws=ws, name=name, etype=entry.get('entity_type') or '',
                                   aliases=js(entry.get('aliases') or []),
                                   desc=entry.get('description') or '',
                                   meta=js({'asserted_by': 'model', 'auto_created': True,
                                            'invocation_id': entities.get('invocation_id'),
                                            'model': entities.get('model')}))
                        node_id = node['id']
                        existing[name] = node_id
                        created_entities += 1
                    entity_node_ids[name] = node_id
        for claim_data in validated['claims']:
            review_info = (claim_data.get('expression_review')
                           if review.get('status') == 'performed' else None)
            if review.get('status') == 'performed':
                expression_ok = bool(review_info and ai_review.is_supporting(review_info['verdict']))
            elif review.get('status') == 'not_configured':
                expression_ok = True
            else:
                expression_ok = False
            review_status = 'validated' if expression_ok else 'candidate'
            fingerprint = ai_extraction.claim_fingerprint(claim_data)
            claim_provenance = {**provenance, 'review_status': review_status}
            if review_info:
                claim_provenance['expression_review'] = review_info['verdict']
            claim = one(conn, '''INSERT INTO claims(workspace_id,origin_version_id,statement,claim_kind,aspect,qualifiers,polarity,importance,verifiability,review_status,fingerprint,provenance)
                VALUES(:ws,:vid,:statement,:claim_kind,:aspect,CAST(:qualifiers AS jsonb),:polarity,:importance,:verifiability,:review_status,:fingerprint,CAST(:provenance AS jsonb))
                ON CONFLICT(workspace_id,origin_version_id,fingerprint) DO NOTHING RETURNING *''',
                        ws=ws, vid=version['id'], statement=claim_data['statement'],
                        claim_kind=claim_data['claim_kind'], aspect=claim_data['aspect'],
                        qualifiers=js(claim_data['qualifiers']), polarity=claim_data['polarity'],
                        importance=claim_data['importance'],
                        verifiability=claim_data['verifiability'],
                        review_status=review_status,
                        fingerprint=fingerprint, provenance=js(claim_provenance))
            claim_ids.append(claim['id'] if claim else None)
            if not claim:
                continue
            evidence_ids = []
            for anchor in claim_data['anchors']:
                evidence_ids.append(str(relation(conn, ws, 'claim', claim['id'], 'evidenced_by',
                                                 'source_version', version['id'], anchor)))
                anchors += 1
            expression_review_id = None
            if review_info:
                review_row = one(conn, '''INSERT INTO claim_expression_reviews(workspace_id,claim_id,job_id,invocation_id,policy_version,verdict,rationale,limitations,quote_count,model_label)
                    VALUES(:ws,:cid,:jid,:inv,:policy,:verdict,:rationale,CAST(:limitations AS jsonb),:quotes,:model)
                    RETURNING id''',
                                 ws=ws, cid=claim['id'], jid=jid,
                                 inv=review.get('invocation_id'), policy=POLICY_VERSION,
                                 verdict=review_info['verdict'],
                                 rationale=review_info.get('reason') or '',
                                 limitations=js(review_info.get('limitations') or []),
                                 quotes=len(claim_data.get('anchors') or []),
                                 model=review.get('model') or '')
                expression_review_id = str(review_row['id'])
            if not expression_ok:
                held_expression += 1
                continue
            verification = one(conn, '''INSERT INTO verification_records(workspace_id,claim_id,version_no,policy_version,input_fingerprint,target_revision,trigger,checker_type,retrieval_manifest,evidence_snapshot,applicability,assessment,review_status,review_reason,valid_until)
              VALUES(:ws,:cid,1,:policy,:fp,1,'new_claim','rule',CAST(:manifest AS jsonb),'[]',CAST(:scope AS jsonb),CAST(:assessment AS jsonb),'accepted','仅接受证据不足这一评估，不授予事实资格',now()+interval '7 days') RETURNING *''',
                                  ws=ws, cid=claim['id'], policy=POLICY_VERSION, fp=fingerprint,
                                  manifest=js({'mode': 'kb_only',
                                               'query': {'origin_version_id': str(version['id']),
                                                         'aspect': claim['aspect'],
                                                         'qualifiers': claim_data['qualifiers']},
                                               'candidate_claim_ids': [], 'external_reads': [],
                                               'result': 'no_accepted_fact_evidence'}),
                                  scope=js(claim_data['qualifiers']),
                                  assessment=js({'status': 'insufficient', 'support_strength': 'E0',
                                                 'refutation_strength': 'E0',
                                                 'gaps': ['仅有模型抽取与原文锚点，未取得可检查的事实材料'],
                                                 'unresolved_counterevidence': False}))
            execute(conn, "UPDATE claims SET current_verification_id=:v,verification_validity='current',fact_check_status='insufficient' WHERE id=:id",
                    v=verification['id'], id=claim['id'])
            claim.update(current_verification_id=verification['id'], verification_validity='current',
                         fact_check_status='insufficient')
            decision = PublicationPolicy.evaluate('feed', claim, verification, True)
            if decision['action'] != 'allow':
                held_policy += 1
                continue
            display = f'来源观点（未经事实确认）：{claim["statement"]}'
            body_entry = {'text': display, 'claim_id': str(claim['id']),
                          'relation_id': evidence_ids[0],
                          'verification_record_ids': [str(verification['id'])],
                          'additional_relation_ids': evidence_ids[1:], **decision}
            if review_info:
                body_entry['expression_review'] = review_info['verdict']
                if expression_review_id:
                    body_entry['expression_review_id'] = expression_review_id
                if review_info.get('limitations'):
                    body_entry['expression_limitations'] = review_info['limitations']
            new_entity_name = (claim_data.get('new_entity') or {}).get('name')
            entity_node_id = entity_node_ids.get(new_entity_name) if new_entity_name else None
            if entity_node_id:
                group_key = digest(js([str(version['id']), 'entity', str(entity_node_id)]))
                group = groups.setdefault(group_key, {'entity_id': entity_node_id,
                                                      'entity_name': new_entity_name, 'entries': []})
                akey = digest(js([str(claim['id']), 'about', str(entity_node_id), 'primary']))
                execute(conn, '''INSERT INTO relations(workspace_id,from_type,from_id,relation_type,to_type,to_id,payload,status,asserted_by,dedup_key)
                    VALUES(:ws,'claim',:cid,'about','node',:nid,CAST(:payload AS jsonb),'accepted','model',:key)
                    ON CONFLICT(workspace_id,dedup_key) DO NOTHING''',
                    ws=ws, cid=claim['id'], nid=entity_node_id,
                    payload=js({'role': 'primary', 'auto_created': True,
                                'invocation_id': entities.get('invocation_id'),
                                'model': entities.get('model')}), key=akey)
            else:
                group_key = digest(js([str(version['id']), claim['aspect'], claim_data['qualifiers'],
                                       claim['statement']]))
                group = groups.setdefault(group_key, {'entity_id': None,
                                                      'entity_name': None, 'entries': []})
            group['entries'].append(body_entry)
        candidates = 0
        for claim_data in validated['unverified']:
            fingerprint = ai_extraction.claim_fingerprint(claim_data)
            inserted = one(conn, '''INSERT INTO claims(workspace_id,origin_version_id,statement,claim_kind,aspect,qualifiers,polarity,importance,verifiability,review_status,fingerprint,provenance)
                VALUES(:ws,:vid,:statement,:claim_kind,:aspect,CAST(:qualifiers AS jsonb),:polarity,:importance,:verifiability,'candidate',:fingerprint,CAST(:provenance AS jsonb))
                ON CONFLICT(workspace_id,origin_version_id,fingerprint) DO NOTHING RETURNING id''',
                           ws=ws, vid=version['id'], statement=claim_data['statement'],
                           claim_kind=claim_data['claim_kind'], aspect=claim_data['aspect'],
                           qualifiers=js(claim_data['qualifiers']), polarity=claim_data['polarity'],
                           importance=claim_data['importance'],
                           verifiability=claim_data['verifiability'],
                           fingerprint=fingerprint,
                           provenance=js({**provenance, 'review_reason': claim_data['reason']}))
            candidates += 1 if inserted else 0
        candidates += held_expression
        if entities.get('status') == 'performed':
            for index, claim_data in enumerate(validated['claims']):
                claim_id = claim_ids[index] if index < len(claim_ids) else None
                if not claim_id:
                    continue
                for link in claim_data.get('entity_links') or []:
                    existing = one(conn, """SELECT id FROM relations WHERE workspace_id=:ws
                        AND from_type='claim' AND from_id=:cid AND relation_type='about'
                        AND to_type='node' AND to_id=:nid""",
                                   ws=ws, cid=claim_id, nid=link['node_id'])
                    if existing:
                        continue
                    node = one(conn, """SELECT id FROM nodes WHERE workspace_id=:ws AND id=:nid
                        AND node_kind='entity'""", ws=ws, nid=link['node_id'])
                    if not node:
                        continue
                    proposed_relation(conn, ws, claim_id, link['node_id'], {
                        'role': link['role'], 'asserted_by': 'model',
                        'invocation_id': entities.get('invocation_id'),
                        'model': entities.get('model'), 'job_id': jid})
                    proposed += 1
        for group_key, group in groups.items():
            entries = group['entries']
            if not entries:
                continue
            entity_id = group['entity_id']
            title = group['entity_name'] if entity_id else entries[0]['text']
            update_provenance = {**provenance, 'source_version_id': str(version['id'])}
            if entity_id:
                update_provenance['entity_id'] = str(entity_id)
                update_provenance['entity_name'] = group['entity_name']
            update = one(conn, '''INSERT INTO updates(workspace_id,update_type,title,summary,change_reason,event_key,state,first_published_at,input_fingerprint,structured_body,provenance)
                VALUES(:ws,'new_viewpoint',:title,:summary,'模型抽取的作者表达；服务端已校验原文锚点，事实未核实',:key,'published',now(),:fp,CAST(:body AS jsonb),CAST(:provenance AS jsonb))
                ON CONFLICT(workspace_id,event_key) DO UPDATE SET revision=updates.revision+1,last_changed_at=now(),structured_body=updates.structured_body || EXCLUDED.structured_body,input_fingerprint=EXCLUDED.input_fingerprint RETURNING *''',
                         ws=ws, title=title, summary=title, key=group_key,
                         fp=digest(js(entries)), body=js(entries),
                         provenance=js(update_provenance))
            for entry in entries:
                relation(conn, ws, 'update', update['id'], 'includes', 'claim', entry['claim_id'],
                         {'role': 'support'})
            if entity_id:
                relation(conn, ws, 'update', update['id'], 'about', 'node', entity_id, {})
            execute(conn, 'UPDATE updates SET revision_history=revision_history || CAST(:snapshot AS jsonb) WHERE id=:id',
                    id=update['id'],
                    snapshot=js([{'revision': update['revision'], 'title': update['title'],
                                  'structured_body': update['structured_body'],
                                  'reason': '模型抽取表达证据', 'at': update['last_changed_at']}]))
            published += 1
        reason_counts = _reason_counts(validated['rejected'])
        held = held_policy + held_expression
        if counts['accepted']:
            review_note = ''
            if review.get('status') == 'performed':
                review_note = (f'表达复核：支持 {review.get("supports", 0)} 条、部分支持 '
                               f'{review.get("partially_supports", 0)} 条、不支持 '
                               f'{review.get("unsupported", 0)} 条、上下文不足 '
                               f'{review.get("unclear", 0)} 条；')
            elif review.get('status') == 'not_configured':
                review_note = '表达复核未配置；'
            else:
                review_note = f'表达复核未完成（{review.get("error") or review.get("status")}）；'
            message = (f'模型抽取完成：候选 {counts["proposed"]} 条，通过原文锚点 {counts["accepted"]} 条，'
                       f'待复核 {counts["unverified"] + held_expression} 条，拒绝 {counts["rejected"]} 条；'
                       f'{review_note}按发布门槛发布 {published} 条动态'
                       + (f'，新建实体 {created_entities} 个' if created_entities else '')
                       + '。事实未核实。')
        else:
            message = ('模型提出的主张没有可校验的原文锚点；已全部保留为待复核候选，未发布。')
        accepted_for_publication = max(0, counts['accepted'] - held_expression)
        state = 'succeeded' if accepted_for_publication else 'needs_review'
        if state == 'succeeded':
            error_code = None
        elif review.get('status') in ('unavailable', 'skipped'):
            error_code = 'EXPRESSION_REVIEW_UNAVAILABLE'
        elif review.get('status') == 'invalid':
            error_code = 'EXPRESSION_REVIEW_INVALID'
        elif counts['accepted'] and held_expression:
            error_code = 'EXPRESSION_REVIEW_HOLD'
        else:
            error_code = 'MODEL_OUTPUT_UNVERIFIED'
        payload = {'extraction': 'model', 'claims': counts['proposed'],
                   'validated': counts['accepted'], 'candidates': candidates,
                   'rejected': counts['rejected'], 'rejected_reasons': reason_counts,
                   'anchors': anchors, 'published': published, 'held': held,
                   'held_policy': held_policy, 'held_expression': held_expression,
                   'entity_links_proposed': proposed,
                   'entities_created': created_entities,
                   'model': result.get('model'), 'provider': result.get('provider'),
                   'invocation_id': result.get('invocation_id'),
                   'cost': result.get('cost'), 'truncated': validated.get('truncated', False),
                   'fact_confirmation': False,
                   'semantic_review': ('performed' if review.get('status') == 'performed'
                                       else ('not_performed' if review.get('status') == 'not_configured'
                                             else 'unavailable')),
                   'expression_review': review.get('status', 'not_configured'),
                   'expression_review_detail': review,
                   'entity_disambiguation': entities.get('status', 'not_configured'),
                   'entity_disambiguation_detail': entities,
                   'message': message}
        changed = execute(conn, '''UPDATE jobs SET state=:state,stage='finished',
            result=CAST(:result AS jsonb),error_code=:code,lease_until=NULL,updated_at=now()
            WHERE id=:id AND fence=:fence AND state='running' ''',
                          id=jid, fence=fence, state=state, result=js(payload), code=error_code)
        if changed.rowcount != 1:
            raise FenceLost()
        execute(conn, "UPDATE job_attempts SET state=:state WHERE job_id=:id AND fence=:fence",
                state=state, id=jid, fence=fence)


def tick():
    job = claim()
    if not job:
        return False
    try:
        if job['kind'] == 'collect_source':
            collect(job)
        elif job['kind'] == 'discover_keywords':
            discover(job)
        elif job['kind'] == 'fact_check':
            fact_check(job)
        else:
            process(job)
    except FenceLost:
        _fence_lost(job)
        return True
    except CollectorError as exc:
        try:
            if job['kind'] == 'discover_keywords':
                run = None
                with engine.connect() as conn:
                    run = one(conn, """SELECT * FROM discovery_runs WHERE workspace_id=:ws
                        AND job_id=:jid ORDER BY requested_at DESC LIMIT 1""",
                              ws=job['workspace_id'], jid=job['id'])
                if run:
                    with engine.connect() as conn:
                        source = one(conn, 'SELECT * FROM discovery_sources WHERE id=:id AND workspace_id=:ws',
                                     id=job['input'].get('discovery_source_id'), ws=job['workspace_id'])
                    if source:
                        _finish_discovery(job, run, source, 'failed', exc.code, detail=exc.message)
                else:
                    _fail_collect(job, exc)
            else:
                _fail_collect(job, exc)
        except FenceLost:
            _fence_lost(job)
        return True
    except Exception as exc:
        with engine.begin() as conn:
            execute(conn, "UPDATE jobs SET state='failed',stage='failed',error_code=:code,lease_until=NULL,updated_at=now() WHERE id=:id AND fence=:f AND state='running'",id=job['id'],f=job['fence'],code=type(exc).__name__)
            execute(conn, "UPDATE job_attempts SET state='failed' WHERE job_id=:id AND fence=:f",id=job['id'],f=job['fence'])
        raise
    return True


def run_scheduler_tick(now=None):
    """Enqueue due keyword sources. Imported lazily to keep the queue module thin."""
    from app.jobs import scheduler
    return scheduler.run_due(now)


def main_loop():
    """Worker process: bounded jobs, plus the scheduler in its own short ticks.

    The scheduler only writes rows (no network), so running it in this process
    keeps the existing three-service launch scripts intact. Set
    ``FISHINGNET_SCHEDULER=0`` to disable automatic scheduling entirely.
    """
    from app.jobs import scheduler
    last_scheduled = 0.0
    last_cleanup = 0.0
    interval = scheduler.tick_seconds()
    while True:
        now = time.monotonic()
        if now - last_cleanup >= DISCOVERY_CLEANUP_INTERVAL_SECONDS:
            last_cleanup = now
            try:
                removed = cleanup_discovery_items()
                if removed:
                    print(f'Retention removed {removed} discovery decision row(s).', flush=True)
            except Exception:
                print('Retention cleanup failed; run ledgers are unchanged.', flush=True)
        if scheduler.scheduler_enabled() and now - last_scheduled >= interval:
            last_scheduled = now
            try:
                created = run_scheduler_tick()
                if created:
                    print(f'Scheduler enqueued {len(created)} discovery run(s).', flush=True)
            except Exception:
                print('Scheduler tick failed; inspect discovery source state.', flush=True)
        try:
            if tick():
                continue
        except Exception:
            # No source text, credentials, SQL parameters or exception payload in logs.
            print('Worker stage failed; inspect the job error code.', flush=True)
            time.sleep(2)
            continue
        time.sleep(1)


if __name__ == '__main__':
    main_loop()
