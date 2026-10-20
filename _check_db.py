import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'apps', 'api'))
from app.storage.db import engine
from sqlalchemy import text
with engine.connect() as c:
    print('=== Sources ===')
    for r in c.execute(text('SELECT id, platform, external_id, title, content_status, availability FROM sources ORDER BY created_at')):
        title = r[3][:50] if r[3] else '(none)'
        print(f'  {r[0]} | {r[1]} | {r[2]} | {title} | status={r[4]} | avail={r[5]}')
    print()
    print('=== Jobs (recent) ===')
    for r in c.execute(text('SELECT id, job_type, status, error_code, created_at FROM jobs ORDER BY created_at DESC LIMIT 10')):
        print(f'  {r[0]} | {r[1]} | {r[2]} | err={r[3]} | {r[4]}')
    print()
    print('=== Discovery Sources ===')
    for r in c.execute(text('SELECT id, name, enabled, network_authorized, failure_streak, last_error_code FROM discovery_sources WHERE deleted_at IS NULL')):
        print(f'  {r[0]} | name={r[1]} | enabled={r[2]} | net_auth={r[3]} | fails={r[4]} | last_err={r[5]}')
    print()
    print('=== Discovery Runs ===')
    for r in c.execute(text('SELECT id, discovery_source_id, status, pages_fetched, candidates_found, enqueued, started_at, finished_at, error_code FROM discovery_runs')):
        print(f'  {r[0]} | src={r[1]} | {r[2]} | pages={r[3]} | cand={r[4]} | enq={r[5]} | start={r[6]} | fin={r[7]} | err={r[8]}')
    print()
    print('=== Platform Connections ===')
    for r in c.execute(text('SELECT id, platform, status, account_label, last_checked_at FROM platform_connections WHERE revoked_at IS NULL')):
        print(f'  {r[0]} | {r[1]} | {r[2]} | label={r[3]} | last_check={r[4]}')