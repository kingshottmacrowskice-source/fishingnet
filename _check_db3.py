import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'apps', 'api'))
from app.storage.db import engine
from sqlalchemy import text
with engine.connect() as c:
    # Get jobs columns
    rows = c.execute(text("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='jobs' ORDER BY ordinal_position")).fetchall()
    col_names = [r[0] for r in rows]
    print('Jobs columns:', col_names)

    # jobs - use * and print all
    print('\n=== Jobs ===')
    for r in c.execute(text('SELECT * FROM jobs ORDER BY created_at DESC LIMIT 10')):
        print(dict(r._mapping))

    # discovery sources
    print('\n=== Discovery Sources ===')
    for r in c.execute(text('SELECT * FROM discovery_sources WHERE deleted_at IS NULL')):
        print(dict(r._mapping))

    # discovery runs
    print('\n=== Discovery Runs ===')
    for r in c.execute(text('SELECT * FROM discovery_runs')):
        print(dict(r._mapping))

    # platform connections
    print('\n=== Platform Connections ===')
    for r in c.execute(text('SELECT id, platform, status, account_label, last_checked_at, external_account_id FROM platform_connections WHERE revoked_at IS NULL')):
        print(dict(r._mapping))

    # source_versions for bilibili source
    print('\n=== Source Versions (bilibili) ===')
    for r in c.execute(text("SELECT sv.id, sv.source_id, sv.version_no, sv.content_origin, sv.language, sv.captured_at FROM source_versions sv JOIN sources s ON sv.source_id = s.id WHERE s.platform='bilibili'")):
        print(dict(r._mapping))

    # check subtitle export file
    import os
    export_path = os.path.join(os.path.dirname(__file__), '.runtime', 'exports', 'BV1MLJe6fEmH-subtitles.txt')
    if os.path.exists(export_path):
        with open(export_path, encoding='utf-8') as f:
            content = f.read()
        print(f'\n=== Subtitle Export ({len(content)} chars) ===')
        print(content[:500])
    else:
        print('\n=== Subtitle Export === NOT FOUND')