import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'apps', 'api'))
from app.storage.db import engine
from sqlalchemy import text
with engine.connect() as c:
    # Get jobs columns
    cols = [r[1] for r in c.execute(text("SELECT * FROM information_schema.columns WHERE table_name='jobs' ORDER BY ordinal_position"))]
    print('Jobs columns:', [c2 for c2 in cols])

    # jobs
    print('\n=== Jobs ===')
    for r in c.execute(text('SELECT id, type, status, error_code, created_at FROM jobs ORDER BY created_at DESC LIMIT 10')):
        print(f'  {r[0]} | {r[1]} | {r[2]} | err={r[3]} | {r[4]}')

    # discovery sources
    print('\n=== Discovery Sources ===')
    for r in c.execute(text('SELECT id, name, enabled, network_authorized, failure_streak, last_error_code FROM discovery_sources WHERE deleted_at IS NULL')):
        print(f'  {r[0]} | name={r[1]} | enabled={r[2]} | net_auth={r[3]} | fails={r[4]} | last_err={r[5]}')

    # discovery runs
    print('\n=== Discovery Runs ===')
    for r in c.execute(text('SELECT id, discovery_source_id, status, pages_fetched, candidates_found, enqueued, started_at, finished_at, error_code FROM discovery_runs')):
        print(f'  {r[0]} | src={r[1]} | {r[2]} | pages={r[3]} | cand={r[4]} | enq={r[5]} | start={r[6]} | fin={r[7]} | err={r[8]}')

    # platform connections
    print('\n=== Platform Connections ===')
    for r in c.execute(text('SELECT id, platform, status, account_label, last_checked_at FROM platform_connections WHERE revoked_at IS NULL')):
        print(f'  {r[0]} | {r[1]} | {r[2]} | label={r[3]} | last_check={r[4]}')

    # source_versions for bilibili source
    print('\n=== Source Versions (bilibili) ===')
    for r in c.execute(text("SELECT sv.id, sv.source_id, sv.version_no, sv.content_origin, sv.language FROM source_versions sv JOIN sources s ON sv.source_id = s.id WHERE s.platform='bilibili'")):
        print(f'  {r[0]} | src={r[1]} | v{r[2]} | origin={r[3]} | lang={r[4]}')