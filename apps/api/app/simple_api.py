"""Reading surfaces for the focused first version."""
import uuid
from datetime import datetime
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


@router.get('/entities')
def entities(user: User):
    with db().connect() as conn:
        rows = many(conn, """SELECT n.* FROM nodes n WHERE n.workspace_id=:ws AND n.node_kind='entity'
            AND n.status<>'deleted' AND EXISTS(SELECT 1 FROM summary_entity_history h WHERE h.node_id=n.id)
            ORDER BY n.canonical_name LIMIT 200""", ws=user['workspace_id'])
        for row in rows:
            row['sources'] = many(conn, """SELECT DISTINCT v.id,v.title,s.canonical_url FROM summary_entity_history h
                CROSS JOIN LATERAL jsonb_array_elements_text(h.summary_ids) ids(value)
                JOIN video_summaries v ON v.id=ids.value::uuid AND v.workspace_id=h.workspace_id
                JOIN sources s ON s.id=v.source_id WHERE h.workspace_id=:ws AND h.node_id=:id
                AND s.availability='available'""", ws=user['workspace_id'], id=row['id'])
    return {'items': rows}


@router.get('/digest')
def digest(user: User, download: bool = False):
    with db().connect() as conn:
        workspace = one(conn, 'SELECT timezone FROM workspace_settings WHERE id=:ws', ws=user['workspace_id'])
        day = datetime.now(ZoneInfo(workspace['timezone'])).date()
        runs = many(conn, """SELECT * FROM summary_rounds WHERE workspace_id=:ws
            AND (created_at AT TIME ZONE :tz)::date=:day ORDER BY created_at,id""", ws=user['workspace_id'], tz=workspace['timezone'], day=day)
    text = '# '+str(day)+' 信息日报\n\n'
    if not runs:
        text += '今天还没有运行记录。'
    for index, run in enumerate(runs, 1):
        text += f'## 第 {index} 轮\n\n'
        text += (run['report'] or ('汇总未完成：'+run['error_code'] if run['error_code'] else '正在收集或总结视频。'))+'\n\n'
    if download:
        return PlainTextResponse(text, media_type='text/markdown', headers={'Content-Disposition':f'attachment; filename="digest-{day}.md"'})
    return {'date': str(day), 'markdown': text, 'rounds': runs}


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
