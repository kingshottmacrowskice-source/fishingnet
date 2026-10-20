"""Isolated disposable UI QA schema; never initializes the user's workspace."""
import os
import secrets
import sys
import uuid
from pathlib import Path
from sqlalchemy import create_engine
from dotenv import dotenv_values

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'apps'/'api'))
config=dotenv_values(root/'.env')
schema='qa_'+uuid.uuid4().hex
base=create_engine(config['DATABASE_URL'])
with base.begin() as conn:
    conn.exec_driver_sql(f'CREATE SCHEMA {schema}')
os.environ['DATABASE_URL']=config['DATABASE_URL']+f'?options=-csearch_path%3D{schema}%2Cpublic'
os.environ['APP_ORIGIN']='http://127.0.0.1:8001'
os.environ['SETUP_TOKEN']=secrets.token_urlsafe(32)
from app.storage.db import engine
from app.main import app
from app.jobs.worker import tick
from fastapi.testclient import TestClient
with engine.begin() as conn:
    with conn.connection.driver_connection.cursor() as cursor:
        for file in sorted((root/'migrations/versions').glob('*.sql')):
            cursor.execute(file.read_text(encoding='utf8'))
with TestClient(app) as client:
    client.post('/api/v1/auth/setup',json={'setup_token':os.environ['SETUP_TOKEN'],'username':'qa-review','password':'Synthetic-QA-Only-2026','workspace_name':'界面验证 · 合成数据'})
    client.headers['X-CSRF-Token']=client.cookies['fn_csrf']
    client.post('/api/v1/demo')
    while tick():pass
(root/'.runtime/qa-schema.txt').write_text(schema)
print('Isolated QA preview: http://127.0.0.1:8001',flush=True)
import uvicorn
uvicorn.run(app,host='127.0.0.1',port=8001,access_log=False)
