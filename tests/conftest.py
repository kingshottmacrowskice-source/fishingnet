"""Shared integration fixture: isolated PostgreSQL schema per test."""
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.config import settings
import app.main as main
import app.auth as auth
import app.jobs.worker as worker
import app.jobs.scheduler as scheduler
from app.ai import gateway as ai_gateway
from app.ai import providers as ai_providers
from app.collectors import reset_registry
from app.domain.qr_sessions import qr_sessions


@pytest.fixture(autouse=True)
def _fresh_registry():
    reset_registry()
    ai_providers.reset_transport_factory()
    qr_sessions.clear_all()
    yield
    reset_registry()
    ai_providers.reset_transport_factory()
    qr_sessions.clear_all()


@pytest.fixture
def db_client(monkeypatch):
    schema = 'test_' + uuid.uuid4().hex
    base = create_engine(settings.database_url, connect_args={'connect_timeout': 5})
    with base.begin() as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA {schema}')
    engine = create_engine(settings.database_url,
                           connect_args={'options': f'-c search_path={schema},public',
                                         'connect_timeout': 5})
    try:
        with engine.begin() as conn:
            with conn.connection.driver_connection.cursor() as cur:
                for migration in sorted(Path('migrations/versions').glob('*.sql')):
                    cur.execute(migration.read_text(encoding='utf8'))
        for module in (main, auth, worker, scheduler, ai_gateway):
            monkeypatch.setattr(module, 'engine', engine)
        monkeypatch.setattr(settings, 'setup_token', 'test-setup')
        main.login_attempts.clear()
        with TestClient(main.app) as client:
            client.engine = engine
            yield client
    finally:
        engine.dispose()
        with base.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA {schema} CASCADE')
        base.dispose()


def initialize(client):
    response = client.post('/api/v1/auth/setup', json={
        'setup_token': 'test-setup', 'username': 'tester',
        'password': 'test-password-12345', 'workspace_name': '测试知识库'})
    assert response.status_code == 200, response.text
    client.headers['X-CSRF-Token'] = client.cookies['fn_csrf']
    # Existing M1-M4 tests deliberately exercise the retained advanced pipeline.
    # V1 tests explicitly turn on the new product mode (the deployment default).
    with client.engine.begin() as conn:
        conn.exec_driver_sql('UPDATE workspace_settings SET simple_mode=false')


@pytest.fixture
def api(db_client):
    initialize(db_client)
    return db_client


BVID = 'BV1xx411c7mD'
URL = f'https://www.bilibili.com/video/{BVID}'
