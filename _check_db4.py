import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'apps', 'api'))
from app.storage.db import engine
from sqlalchemy import text
with engine.connect() as c:
    # platform connections
    print('=== Platform Connections ===')
    rows = c.execute(text("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='platform_connections' ORDER BY ordinal_position")).fetchall()
    print('Columns:', [r[0] for r in rows])
    for r in c.execute(text('SELECT * FROM platform_connections')):
        d = dict(r._mapping)
        # Remove secret_ref for safety
        d.pop('secret_ref', None)
        print(d)

    # Check if subtitle export exists
    import pathlib
    export = pathlib.Path(__file__).parent / '.runtime' / 'exports' / 'BV1MLJe6fEmH-subtitles.txt'
    if export.exists():
        content = export.read_text(encoding='utf-8')
        print(f'\n=== Subtitle Export ({len(content)} chars) ===')
        print(content[:500])
    else:
        print('\nSubtitle export NOT FOUND')

    # discovery source details
    print('\n=== Discovery Source Config ===')
    for r in c.execute(text('SELECT * FROM discovery_sources WHERE deleted_at IS NULL')):
        print(dict(r._mapping))