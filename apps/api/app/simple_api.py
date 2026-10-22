"""Reading surfaces for the focused first version."""
import json
import uuid
from datetime import datetime, timezone
from typing import Annotated
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from app.auth import current_user
from app.storage.db import one, many, execute
from app.jobs import simple

router = APIRouter(prefix='/api/v1/simple')
User = Annotated[dict, Depends(current_user)]


def db():
    from app import main
    return main.engine


@router.get('/feed')
def feed(user: User, q: str = '', offset: int = 0):
    offset = max(0, offset)
    with db().connect() as conn:
        rows = many(conn, """SELECT v.*,s.canonical_url,s.author_name FROM video_summaries v
            JOIN sources s ON s.id=v.source_id WHERE v.workspace_id=:ws AND s.availability='available'
            AND (v.title ILIKE :q OR v.summary ILIKE :q)
            ORDER BY v.created_at DESC,v.id DESC LIMIT 21 OFFSET :offset""", ws=user['workspace_id'], q='%'+q+'%', offset=offset)
    return {'items': rows[:20], 'next_offset': offset+20 if len(rows)>20 else None}


@router.get('/rounds')
def rounds(user: User):
    with db().connect() as conn:
        rows = many(conn, 'SELECT * FROM summary_rounds WHERE workspace_id=:ws ORDER BY created_at DESC LIMIT 50', ws=user['workspace_id'])
        for row in rows:
            row['items'] = simple.round_items(conn, user['workspace_id'], row['id'])
    return {'items': rows}


def _hidden_by(row):
    return (row.get('metadata') or {}).get('hidden_by')


@router.get('/entities')
def entities(user: User, include_hidden: bool = False):
    ws = user['workspace_id']
    items = []
    with db().connect() as conn:
        rows = many(conn, """SELECT n.* FROM nodes n WHERE n.workspace_id=:ws AND n.node_kind='entity'
            AND n.status<>'deleted' AND EXISTS(SELECT 1 FROM summary_entity_history h WHERE h.node_id=n.id)
            ORDER BY (n.metadata ? 'hidden_by'), n.canonical_name LIMIT 200""", ws=ws)
        hidden_count = one(conn, """SELECT count(*) AS n FROM nodes n WHERE n.workspace_id=:ws
            AND n.node_kind='entity' AND n.status<>'deleted' AND n.metadata ? 'hidden_by'
            AND EXISTS(SELECT 1 FROM summary_entity_history h WHERE h.node_id=n.id)""", ws=ws)['n']
        for row in rows:
            row['hidden_by'] = _hidden_by(row)
            row['hidden'] = bool(row['hidden_by'])
            if row['hidden'] and not include_hidden:
                continue
            # 视频资源：实体出现过的原始视频/资料（可跳转查看原文）。
            row['sources'] = many(conn, """SELECT DISTINCT s.id,s.title,s.canonical_url,s.platform
                FROM summary_entity_history h
                CROSS JOIN LATERAL jsonb_array_elements_text(h.summary_ids) ids(value)
                JOIN video_summaries v ON v.id=ids.value::uuid AND v.workspace_id=h.workspace_id
                JOIN sources s ON s.id=v.source_id AND s.workspace_id=h.workspace_id
                WHERE h.workspace_id=:ws AND h.node_id=:id AND s.availability='available'
                ORDER BY s.title""", ws=ws, id=row['id'])
            # 信息动态：与实体相关的视频/资料总结（feed 里的动态条目）。
            row['summaries'] = many(conn, """SELECT DISTINCT v.id,v.title,v.summary,v.created_at
                FROM summary_entity_history h
                CROSS JOIN LATERAL jsonb_array_elements_text(h.summary_ids) ids(value)
                JOIN video_summaries v ON v.id=ids.value::uuid AND v.workspace_id=h.workspace_id
                WHERE h.workspace_id=:ws AND h.node_id=:id
                ORDER BY v.created_at DESC, v.id DESC""", ws=ws, id=row['id'])
            items.append(row)
    return {'items': items, 'hidden_count': int(hidden_count or 0)}


class EntityVisibility(BaseModel):
    hidden: bool


@router.patch('/entities/{entity_id}')
def set_entity_hidden(entity_id: uuid.UUID, body: EntityVisibility, user: User):
    """Manual hide is reversible; unhiding opts the entity out of auto-hide."""
    ws = user['workspace_id']
    with db().begin() as conn:
        row = one(conn, """SELECT * FROM nodes WHERE id=:id AND workspace_id=:ws
            AND node_kind='entity' AND status<>'deleted' FOR UPDATE""", id=entity_id, ws=ws)
        if not row:
            raise HTTPException(404, '实体不存在或已删除')
        meta = dict(row.get('metadata') or {})
        if body.hidden:
            meta['hidden_by'] = 'manual'
            meta['hidden_at'] = datetime.now(timezone.utc).isoformat()
            meta.pop('no_auto_hide', None)
        else:
            meta.pop('hidden_by', None)
            meta.pop('hidden_at', None)
            meta['no_auto_hide'] = True
        execute(conn, 'UPDATE nodes SET metadata=CAST(:meta AS jsonb),revision=revision+1 WHERE id=:id',
                meta=json.dumps(meta, ensure_ascii=False), id=entity_id)
        try:
            simple.refresh_daily_digest_entities(conn, ws)
        except Exception:  # noqa: BLE001 - digest refresh must not block hiding
            pass
    return {'ok': True, 'hidden': body.hidden}


@router.delete('/entities/{entity_id}')
def delete_entity(entity_id: uuid.UUID, user: User):
    """Delete one entity and every link between it and information updates.

    The videos/summaries themselves are kept: only ``summary_entity_history`` links
    (and any advanced-mode ``about`` relation) disappear. The node is tombstoned so
    a later round cannot recreate an entity the user removed on purpose.
    """
    ws = user['workspace_id']
    with db().begin() as conn:
        row = one(conn, """SELECT * FROM nodes WHERE id=:id AND workspace_id=:ws
            AND node_kind='entity' AND status<>'deleted' FOR UPDATE""", id=entity_id, ws=ws)
        if not row:
            return {'ok': True, 'already_deleted': True, 'removed_links': 0}
        removed = execute(conn, 'DELETE FROM summary_entity_history WHERE workspace_id=:ws AND node_id=:id',
                          ws=ws, id=entity_id).rowcount or 0
        # Advanced mode links an update to an entity with an ``about`` relation.
        execute(conn, """DELETE FROM relations WHERE workspace_id=:ws
            AND ((from_type='node' AND from_id=:id) OR (to_type='node' AND to_id=:id))""", ws=ws, id=entity_id)
        execute(conn, 'DELETE FROM subscriptions WHERE workspace_id=:ws AND node_id=:id', ws=ws, id=entity_id)
        meta = dict(row.get('metadata') or {})
        meta['deleted_at'] = datetime.now(timezone.utc).isoformat()
        execute(conn, "UPDATE nodes SET status='deleted',metadata=CAST(:meta AS jsonb),revision=revision+1 WHERE id=:id",
                meta=json.dumps(meta, ensure_ascii=False), id=entity_id)
        try:
            simple.refresh_daily_digest_entities(conn, ws)
        except Exception:  # noqa: BLE001 - digest refresh must not block deletion
            pass
    return {'ok': True, 'removed_links': int(removed)}


@router.get('/digest')
def digest(user: User, download: bool = False):
    with db().connect() as conn:
        workspace = one(conn, 'SELECT timezone FROM workspace_settings WHERE id=:ws', ws=user['workspace_id'])
        day = datetime.now(ZoneInfo(workspace['timezone'])).date()
        row = one(conn, 'SELECT * FROM daily_digests WHERE workspace_id=:ws AND day=:day',
                  ws=user['workspace_id'], day=day)
    text = row['markdown'] if row else f'# {day} 信息日报\n\n今天还没有生成日报。'
    if download:
        return PlainTextResponse(text, media_type='text/markdown', headers={'Content-Disposition': f'attachment; filename="digest-{day}.md"'})
    return {'date': str(day), 'markdown': text, 'summary': row['summary'] if row else ''}


class MonitorInput(BaseModel):
    enabled: bool = False
    network_authorized: bool = False
    interval_minutes: int = Field(default=30, ge=5, le=10080)


@router.get('/mentions')
def monitor(user: User):
    with db().connect() as conn:
        row = one(conn, 'SELECT * FROM mention_monitors WHERE workspace_id=:ws', ws=user['workspace_id'])
        recent = many(conn, 'SELECT * FROM mention_receipts WHERE workspace_id=:ws ORDER BY created_at DESC LIMIT 20', ws=user['workspace_id'])
    return {'monitor': row or {'enabled': False, 'network_authorized': False, 'interval_minutes':30}, 'recent':recent}


@router.put('/mentions')
def save_monitor(body: MonitorInput, user: User):
    with db().begin() as conn:
        row = one(conn, """INSERT INTO mention_monitors(workspace_id,enabled,network_authorized,interval_minutes)
            VALUES(:ws,:enabled,:authorized,:minutes) ON CONFLICT(workspace_id) DO UPDATE SET
            enabled=EXCLUDED.enabled,network_authorized=EXCLUDED.network_authorized,
            interval_minutes=EXCLUDED.interval_minutes,next_run_at=NULL RETURNING *""",
            ws=user['workspace_id'], enabled=body.enabled, authorized=body.network_authorized, minutes=body.interval_minutes)
    return row


@router.post('/rounds/{round_id}/retry')
def retry_round(round_id: uuid.UUID, user: User):
    with db().begin() as conn:
        run = one(conn, 'SELECT * FROM summary_rounds WHERE id=:id AND workspace_id=:ws FOR UPDATE', id=round_id, ws=user['workspace_id'])
        if not run:
            raise HTTPException(404, '本轮记录不存在')
        if run['state'] not in ('failed','partial'):
            raise HTTPException(409, '本轮无需重试或仍在运行')
        if run['discovery_run_id']:
            discovery=one(conn,'SELECT * FROM discovery_runs WHERE id=:id AND workspace_id=:ws',id=run['discovery_run_id'],ws=user['workspace_id'])
            if discovery and discovery['state'] in ('failed','blocked_upstream','cancelled'):
                execute(conn,"""UPDATE jobs SET state='queued',attempts=0,error_code=NULL,lease_until=NULL,fence=fence+1,checkpoint='{}'
                    WHERE id=:id AND workspace_id=:ws AND state IN ('failed','cancelled')""",id=discovery['job_id'],ws=user['workspace_id'])
                return {'status':'queued','note':'重新开始发现，结果会记录为新一轮'}
        unfinished = [i for i in simple.round_items(conn,user['workspace_id'],round_id) if not i['summary_id']]
        for item in unfinished:
            jid = item.get('process_job_id') or item.get('collect_job_id')
            if jid:
                execute(conn, """UPDATE jobs SET state='queued',attempts=0,error_code=NULL,lease_until=NULL,fence=fence+1
                    WHERE id=:id AND workspace_id=:ws AND state IN ('failed','blocked_budget','blocked_credentials','needs_review','cancelled')""", id=jid, ws=user['workspace_id'])
        if run['job_id']:
            execute(conn, """UPDATE jobs SET state='queued',attempts=0,error_code=NULL,lease_until=NULL,fence=fence+1
                WHERE id=:id AND state IN ('failed','blocked_budget','needs_review','succeeded')""", id=run['job_id'])
            if unfinished:
                execute(conn, "UPDATE jobs SET state='needs_review' WHERE id=:id", id=run['job_id'])
            execute(conn, "UPDATE summary_rounds SET state=:state,error_code=NULL,report='',finished_at=NULL WHERE id=:id", id=round_id, state='collecting' if unfinished else 'queued')
    return {'status':'queued'}


# 清空内容时保留这些表（登录 / 平台账号 / 模型配置）。
# TRUNCATE ... CASCADE 会绕过 immutable_row / prevent_endpoint_delete 等行级触发器，
# 从而能干净地删除 source_versions / nodes / relations 等带约束的表；CASCADE 只影响
# 被清空表之间的外键，不会触及上方保留表（保留表不会引用内容表）。
_CLEAR_CONTENT_TABLES = (
    'daily_digests', 'summary_entity_history', 'nodes', 'summary_round_items',
    'summary_rounds', 'video_summaries', 'source_versions', 'sources',
    'discovery_run_items', 'discovery_runs', 'discovery_sources',
    'mention_receipts', 'mention_monitors', 'jobs', 'job_attempts',
    'request_idempotency', 'claim_expression_reviews', 'verification_records',
    'claims', 'updates', 'relations', 'user_item_states', 'subscriptions',
    'ai_budget_ledger', 'ai_invocations', 'audit_events', 'feedback',
)


@router.post('/clear-content')
def clear_content(user: User):
    """清空信息/实体/日报与自动任务数据，保留登录、账号与模型配置。"""
    try:
        with db().begin() as conn:
            execute(conn, 'TRUNCATE TABLE ' + ', '.join(_CLEAR_CONTENT_TABLES) + ' CASCADE')
    except Exception as exc:  # noqa: BLE001 - 把本地数据库错误透传给前端以便排查
        raise HTTPException(500, f'清空失败：{exc}')
    return {'status': 'ok', 'cleared': len(_CLEAR_CONTENT_TABLES)}
