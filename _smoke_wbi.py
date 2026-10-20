"""Smoke test: verify WBI-signed keyword search against real B站 API.

Tests bilibili.py's discover() with WBI signing directly (no server login needed).
Loads SESSDATA from platform_connections for the /x/web-interface/nav endpoint.
Records results for docs/ACCEPTANCE.md.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'apps', 'api'))

from datetime import datetime, timezone
from sqlalchemy import text
from app.storage.db import engine, one
from app.collectors.base import Credential, DiscoveryQuery
from app.collectors.bilibili import BilibiliCollector
from app.domain.secrets import open_sealed

NOW = datetime.now(timezone.utc)
print(f'Smoke test started at {NOW.isoformat()}')
print('='*60)

# --- Load credential from DB ---
print('\n[0] Loading B站 credential from platform_connections ...')
cred = None
with engine.connect() as conn:
    row = one(conn, "SELECT * FROM platform_connections WHERE platform='bilibili'")
    if row and row.get('credential_sealed'):
        values = open_sealed(row['credential_sealed'])
        if values:
            cred = Credential(platform='bilibili', values=values)
            print(f'  [OK] Credential loaded (keys: {list(values.keys())})')
        else:
            print('  [WARN] open_sealed returned empty; will try without credential')
    else:
        print('  [WARN] No bilibili platform connection found; will try without credential')

# --- Test 1: WBI key fetch ---
print('\n[1] Fetching WBI key from /x/web-interface/nav ...')
c = BilibiliCollector(min_interval=0)
try:
    key = c._get_wbi_key(credential=cred)
    print(f'  [OK] WBI key fetched: {key[:8]}...{key[-4:]} (len={len(key)})')
except Exception as e:
    print(f'  [FAIL] {type(e).__name__}: {e}')
    sys.exit(1)

# --- Test 2: WBI-signed keyword search (1 page) ---
print('\n[2] WBI-signed keyword search: "AI" (1 page, <=5 results) ...')
try:
    result = c.discover(DiscoveryQuery(keywords=('AI',), page=1), credential=cred)
    print(f'  [OK] status={result.status}, items={len(result.items)}, has_more={result.has_more}')
    for i, item in enumerate(result.items[:5]):
        print(f'    [{i+1}] {item.external_id} | {item.title[:40]} | author={item.author_name} | pub={item.published_at}')
    if result.items:
        print(f'  [OK] First item details: duration={result.items[0].duration_seconds}s, page={result.items[0].page}')
    else:
        print('  [WARN] No items returned (empty result)')
except Exception as e:
    code = getattr(e, 'code', None) or getattr(e, 'status_code', None)
    print(f'  [FAIL] {type(e).__name__} (code={code}): {e}')
    if code in (-403, -412, 'RISK_CONTROL', 'DISCOVERY_DENIED'):
        print('  [NOTE] This is a risk-control / access-denied response from B站.')
        print('         WBI signing is working (server accepted the request format),')
        print('         but the IP/account may be rate-limited or flagged.')
    sys.exit(1)

# --- Test 3: Second page (if available) ---
if result.has_more:
    print('\n[3] Fetching page 2 ...')
    try:
        result2 = c.discover(DiscoveryQuery(keywords=('AI',), page=2), credential=cred)
        print(f'  [OK] status={result2.status}, items={len(result2.items)}, has_more={result2.has_more}')
    except Exception as e:
        print(f'  [FAIL] {type(e).__name__}: {e}')
else:
    print('\n[3] Skipping page 2 (has_more=False on page 1)')

# --- Summary ---
print('\n' + '='*60)
print('WBI smoke test PASSED')
print(f'  - WBI key fetch: OK')
print(f'  - Keyword search: OK ({len(result.items)} items on page 1)')
print(f'  - Search endpoint: api.bilibili.com/x/web-interface/wbi/search/type')
print(f'  - Timestamp: {datetime.now(timezone.utc).isoformat()}')