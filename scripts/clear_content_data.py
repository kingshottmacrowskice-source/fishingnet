"""清空 FishingNet 的内容数据（信息/实体/日报/自动任务），保留登录、账号与模型配置。

安全说明：
- 只 TRUNCATE 内容与任务相关表；以下表会被保留（不清空）：
    workspace_settings, users, auth_sessions,           # 工作区设置 + 登录
    platform_connections,                                # B站账号/平台凭证
    provider_connections, model_profiles, task_routes    # 模型配置
- 使用 TRUNCATE ... CASCADE，会绕过行级触发器（immutable_row / prevent_endpoint_delete 等），
  因此可以干净地清空 source_versions / nodes / relations 等带约束的表。
- 全部操作在单个事务内完成，是原子的；运行中的 worker 只会短暂等待表锁。

用法：
    .venv\\Scripts\\python.exe scripts\\clear_content_data.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent

# 保留（不清空）
KEEP_TABLES = (
    'workspace_settings',
    'users',
    'auth_sessions',
    'platform_connections',
    'provider_connections',
    'model_profiles',
    'task_routes',
)

# 清空（内容 + 任务 + 调用/费用账本）
CLEAR_TABLES = (
    # 日报
    'daily_digests',
    # 实体与实体历史
    'summary_entity_history',
    'nodes',
    # 信息（轮次、视频总结、来源）
    'summary_round_items',
    'summary_rounds',
    'video_summaries',
    'source_versions',
    'sources',
    # 自动任务（关键词发现 + @提及 + 任务队列）
    'discovery_run_items',
    'discovery_runs',
    'discovery_sources',
    'mention_receipts',
    'mention_monitors',
    'jobs',
    'job_attempts',
    'request_idempotency',
    # 高级事实核查/主张/动态/关系（若存在）
    'claim_expression_reviews',
    'verification_records',
    'claims',
    'updates',
    'relations',
    'user_item_states',
    'subscriptions',
    # 模型调用与费用账本（保留模型配置，只清调用历史）
    'ai_budget_ledger',
    'ai_invocations',
    # 杂项
    'audit_events',
    'feedback',
)


def load_env() -> dict:
    env: dict = {}
    env_path = ROOT / '.env'
    if env_path.exists():
        for raw in env_path.read_text(encoding='utf-8').splitlines():
            line = raw.strip()
            if line and not line.startswith('#') and '=' in line:
                key, _, value = line.partition('=')
                env[key.strip()] = value.strip()
    return env


def dsn_from(env: dict) -> str:
    url = env.get('DATABASE_URL', 'postgresql+psycopg://fishingnet@127.0.0.1:55432/fishingnet')
    # psycopg 直接使用 postgresql:// 形式
    return url.replace('postgresql+psycopg://', 'postgresql://')


def counts(conn, tables) -> dict:
    out = {}
    with conn.cursor() as cur:
        for t in tables:
            cur.execute(f'SELECT count(*) FROM {t}')
            out[t] = cur.fetchone()[0]
    return out


def main() -> int:
    env = load_env()
    dsn = dsn_from(env)
    print('连接数据库：', dsn.split('@')[-1])
    print('将清空以下内容表（保留登录/账号/模型配置）：')
    print('  ', ', '.join(CLEAR_TABLES))
    print()

    with psycopg.connect(dsn, autocommit=False) as conn:
        before_keep = counts(conn, KEEP_TABLES)
        before_clear_total = sum(counts(conn, CLEAR_TABLES).values())

        with conn.cursor() as cur:
            cur.execute('TRUNCATE TABLE ' + ', '.join(CLEAR_TABLES) + ' CASCADE')

        after_clear = counts(conn, CLEAR_TABLES)
        after_keep = counts(conn, KEEP_TABLES)

        print('已清空内容行数：', before_clear_total)
        print()
        print('保留表（清空前 -> 清空后）：')
        for t in KEEP_TABLES:
            print(f'  {t}: {before_keep[t]} -> {after_keep[t]}')
        print()
        remaining = sum(after_clear.values())
        if remaining:
            print('警告：仍有内容表残留行数：')
            for t, n in after_clear.items():
                if n:
                    print(f'  {t}: {n}')
            return 1
        print('完成：信息、实体、日报与自动任务数据已清空；登录、账号与模型配置保持不变。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
