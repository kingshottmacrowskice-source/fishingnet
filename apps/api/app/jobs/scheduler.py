"""Bounded database scheduler for keyword discovery (M2 Phase 2).

The scheduler **only writes rows**: it decides which discovery source is due and
enqueues a ``discover_keywords`` job. It never performs a network call, so it is
safe to run inside the worker process in a short transaction.

Concurrency model:

* ``FOR UPDATE SKIP LOCKED`` plus a conditional ``next_run_at`` update means two
  schedulers cannot enqueue the same source for the same slot;
* every enqueue is also guarded by a unique job idempotency key, so a crash
  between the run insert and the job insert cannot duplicate work;
* a failure advances ``failure_streak`` and backs the next attempt off, but never
  silently disables the source.

Only sources that are both ``enabled`` and explicitly ``network_authorized`` are
scheduled: enabling a keyword source is a configuration change, authorising real
network access is a separate, revocable decision made by the local user.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from app.domain.discovery import next_run_after
from app.storage.db import engine, many, one, execute

DEFAULT_TICK_SECONDS = 30.0
MAX_DUE_PER_TICK = 3


def scheduler_enabled() -> bool:
    return str(os.environ.get('FISHINGNET_SCHEDULER', '1')).strip().lower() not in (
        '0', 'false', 'no', 'off')


def tick_seconds() -> float:
    try:
        return max(5.0, float(os.environ.get('FISHINGNET_SCHEDULER_TICK', DEFAULT_TICK_SECONDS)))
    except ValueError:
        return DEFAULT_TICK_SECONDS


def due_sources(conn, now: datetime, limit: int = MAX_DUE_PER_TICK) -> list[dict]:
    return many(conn, """SELECT * FROM discovery_sources
        WHERE enabled AND network_authorized AND deleted_at IS NULL
          AND (next_run_at IS NULL OR next_run_at<=:now)
        ORDER BY next_run_at NULLS FIRST, created_at
        LIMIT :limit FOR UPDATE SKIP LOCKED""", now=now, limit=limit)


def enqueue_run(conn, source: dict, now: datetime, trigger: str = 'schedule') -> dict:
    """Insert one run and its job for an already-locked source row."""
    run = one(conn, """INSERT INTO discovery_runs(workspace_id,source_id,trigger,state,
        request_budget) VALUES(:ws,:sid,:trigger,'queued',:budget)
        RETURNING *""", ws=source['workspace_id'], sid=source['id'], trigger=trigger,
        budget=source['max_requests_per_run'])
    slot = int(now.timestamp())
    job = one(conn, """INSERT INTO jobs(workspace_id,kind,input,config_snapshot,idempotency_key)
        VALUES(:ws,'discover_keywords',CAST(:input AS jsonb),CAST(:snapshot AS jsonb),:key)
        ON CONFLICT(workspace_id,idempotency_key) DO NOTHING RETURNING id,state""",
        ws=source['workspace_id'],
        input=_json({'discovery_source_id': str(source['id']), 'run_id': str(run['id']),
                     'trigger': trigger}),
        snapshot=_json({'provider': 'none', 'data_policy': 'local_only',
                        'collector': source['platform'], 'network': 'real',
                        'ai_analysis': False, 'keyword_discovery': True}),
        key=f"discover:{source['id']}:{slot}")
    if not job:
        job = one(conn, """SELECT id,state FROM jobs WHERE workspace_id=:ws AND idempotency_key=:key""",
                  ws=source['workspace_id'], key=f"discover:{source['id']}:{slot}")
    execute(conn, """UPDATE discovery_runs SET job_id=:jid WHERE id=:rid""",
            jid=job['id'], rid=run['id'])
    execute(conn, """UPDATE discovery_sources SET next_run_at=:next,last_run_at=:now,
        updated_at=now() WHERE id=:id""",
            next=next_run_after(now, source['schedule_interval_minutes'],
                                source['failure_streak']), now=now, id=source['id'])
    run['job_id'] = job['id']
    return run


def run_due(now: datetime | None = None, limit: int = MAX_DUE_PER_TICK) -> list[dict]:
    """Enqueue at most ``limit`` due sources. Returns the created runs."""
    if not scheduler_enabled():
        return []
    now = now or datetime.now(timezone.utc)
    created: list[dict] = []
    with engine.begin() as conn:
        for source in due_sources(conn, now, limit):
            created.append(enqueue_run(conn, source, now))
    return created


def _json(value) -> str:
    import json
    return json.dumps(value, ensure_ascii=False, default=str)
