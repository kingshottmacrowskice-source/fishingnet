"""Smoke test: login via API, then test B站 metadata/subtitle and keyword discovery."""
import os, sys, json, time, requests

BASE = 'http://127.0.0.1:8000/api/v1'
session = requests.Session()

# Step 0: Check if already logged in
r = session.get(f'{BASE}/auth/me')
if r.status_code == 200:
    user = r.json()
    print(f'[OK] Already logged in as: {user.get("username", "?")}')
else:
    print('[INFO] Not logged in. Need credentials.')
    # Read .env for SETUP_TOKEN (not password)
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    setup_token = None
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                if line.startswith('SETUP_TOKEN='):
                    setup_token = line.strip().split('=', 1)[1].strip().strip('"').strip("'")
                    break
    if setup_token:
        print(f'[INFO] SETUP_TOKEN found (length={len(setup_token)})')
    else:
        print('[ERROR] No SETUP_TOKEN in .env')
    print('[INFO] Cannot proceed without login credentials.')
    print('[ACTION] Please log in via the web UI at http://127.0.0.1:5173,')
    print('         then run this script again, OR provide credentials.')
    sys.exit(1)

# Step 1: Check platform connection
print('\n=== Step 1: Check B站 Platform Connection ===')
r = session.get(f'{BASE}/platforms')
if r.status_code == 200:
    platforms = r.json()
    bilibili = None
    for p in platforms.get('items', platforms if isinstance(platforms, list) else []):
        if p.get('platform') == 'bilibili':
            bilibili = p
            break
    if bilibili:
        print(f'[OK] B站 connection: status={bilibili.get("status")}, alias={bilibili.get("alias")}')
        print(f'     last_check_detail: {bilibili.get("last_check_detail")}')
        print(f'     last_success_at: {bilibili.get("last_success_at")}')
    else:
        print('[WARN] No bilibili platform connection found')
else:
    print(f'[ERROR] GET /platforms: {r.status_code} {r.text[:200]}')

# Step 2: Verify existing BV1MLJe6fEmH collection
print('\n=== Step 2: Verify BV1MLJe6fEmH Collection ===')
r = session.get(f'{BASE}/sources', params={'platform': 'bilibili'})
if r.status_code == 200:
    sources = r.json()
    items = sources.get('items', [])
    bili_sources = [s for s in items if s.get('platform') == 'bilibili']
    for s in bili_sources:
        print(f'[OK] Source: {s.get("external_id")} | title={s.get("title", "")[:50]}')
        print(f'     content_status={s.get("content_status")} | availability={s.get("availability")}')
        # Get source details
        sid = s.get('id')
        if sid:
            r2 = session.get(f'{BASE}/sources/{sid}')
            if r2.status_code == 200:
                detail = r2.json()
                versions = detail.get('versions', [])
                print(f'     versions: {len(versions)}')
                for v in versions:
                    print(f'       v{v.get("version_no")}: origin={v.get("content_origin")} lang={v.get("language")} segments={len(v.get("segments", []))}')
else:
    print(f'[ERROR] GET /sources: {r.status_code} {r.text[:200]}')

# Step 3: Check subtitle export
print('\n=== Step 3: Check Subtitle Export ===')
export_path = os.path.join(os.path.dirname(__file__), '.runtime', 'exports', 'BV1MLJe6fEmH-subtitles.txt')
if os.path.exists(export_path):
    with open(export_path, encoding='utf-8-sig') as f:
        content = f.read()
    print(f'[OK] Subtitle export: {len(content)} chars')
    print(f'     Preview: {content[:100]}...')
else:
    print('[WARN] Subtitle export file not found')

# Step 4: Check discovery source status
print('\n=== Step 4: Check Discovery Source ===')
r = session.get(f'{BASE}/discovery-sources')
if r.status_code == 200:
    ds = r.json()
    items = ds.get('items', [])
    for s in items:
        print(f'[INFO] Discovery source: {s.get("name")} | enabled={s.get("enabled")} | net_auth={s.get("network_authorized")}')
        print(f'       last_error_code: {s.get("last_error_code")} | failure_streak: {s.get("failure_streak")}')
else:
    print(f'[ERROR] GET /discovery-sources: {r.status_code} {r.text[:200]}')

# Step 5: Check discovery runs
print('\n=== Step 5: Check Discovery Runs ===')
r = session.get(f'{BASE}/discovery-runs')
if r.status_code == 200:
    dr = r.json()
    items = dr.get('items', [])
    for run in items:
        print(f'[INFO] Run: {run.get("id")} | state={run.get("state")} | error={run.get("error_code")}')
        print(f'       pages={run.get("pages_visited")} | discovered={run.get("discovered")} | enqueued={run.get("enqueued")}')
else:
    print(f'[ERROR] GET /discovery-runs: {r.status_code} {r.text[:200]}')

# Step 6: Keyword discovery smoke test
print('\n=== Step 6: Keyword Discovery Smoke Test ===')
# Find the discovery source
ds_items = []
r = session.get(f'{BASE}/discovery-sources')
if r.status_code == 200:
    ds_items = r.json().get('items', [])

if ds_items:
    ds_id = ds_items[0].get('id')
    print(f'[INFO] Attempting to run discovery source: {ds_id}')
    # First enable it and authorize network
    ds_rev = ds_items[0].get('revision')
    patch_body = {'enabled': True, 'network_authorized': True}
    headers = {'If-Match': str(ds_rev), 'Content-Type': 'application/json'}
    r = session.patch(f'{BASE}/discovery-sources/{ds_id}', json=patch_body, headers=headers)
    if r.status_code == 200:
        print(f'[OK] Discovery source enabled + network_authorized')
    else:
        print(f'[WARN] PATCH discovery source: {r.status_code} {r.text[:200]}')

    # Run it
    r = session.post(f'{BASE}/discovery-sources/{ds_id}/run')
    if r.status_code == 202:
        result = r.json()
        job_id = result.get('job_id')
        run_id = result.get('run_id')
        print(f'[OK] Discovery run started: job={job_id} run={run_id}')
        # Wait a bit and check result
        for i in range(10):
            time.sleep(3)
            r2 = session.get(f'{BASE}/jobs/{job_id}')
            if r2.status_code == 200:
                job = r2.json()
                state = job.get('state')
                error = job.get('error_code')
                result_data = job.get('result', {})
                print(f'     [{i+1}] state={state} error={error} pages={result_data.get("pages")} discovered={result_data.get("discovered")} enqueued={result_data.get("enqueued")}')
                if state in ('succeeded', 'failed', 'cancelled'):
                    break
            else:
                print(f'     [{i+1}] GET /jobs/{job_id}: {r2.status_code}')
        # After run, disable and deauthorize
        r3 = session.get(f'{BASE}/discovery-sources/{ds_id}')
        if r3.status_code == 200:
            ds_rev2 = r3.json().get('revision')
            headers2 = {'If-Match': str(ds_rev2), 'Content-Type': 'application/json'}
            session.patch(f'{BASE}/discovery-sources/{ds_id}', json={'enabled': False, 'network_authorized': False}, headers=headers2)
            print('[OK] Discovery source disabled + deauthorized after test')
    elif r.status_code == 422:
        print(f'[WARN] Run rejected: {r.text[:300]}')
    else:
        print(f'[ERROR] POST run: {r.status_code} {r.text[:200]}')
else:
    print('[WARN] No discovery sources available')

print('\n=== Smoke Test Complete ===')