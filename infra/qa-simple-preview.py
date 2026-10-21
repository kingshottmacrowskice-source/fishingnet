"""Isolated, synthetic V1 preview. No platform requests or model calls."""
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
schema='qa_simple_'+uuid.uuid4().hex
base=create_engine(config['DATABASE_URL'])
with base.begin() as conn:
    conn.exec_driver_sql('CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public')
    conn.exec_driver_sql(f'CREATE SCHEMA {schema}')
os.environ['DATABASE_URL']=config['DATABASE_URL']+f'?options=-csearch_path%3D{schema}%2Cpublic'
os.environ['APP_ORIGIN']='http://127.0.0.1:8001'
os.environ['SETUP_TOKEN']=secrets.token_urlsafe(32)
from app.storage.db import engine,one,execute
from app.main import app
from app.jobs import simple
from fastapi.testclient import TestClient
with engine.begin() as conn:
    with conn.connection.driver_connection.cursor() as cursor:
        for file in sorted((root/'migrations/versions').glob('*.sql')):
            cursor.execute(file.read_text(encoding='utf8'))
with TestClient(app) as client:
    response=client.post('/api/v1/auth/setup',json={'setup_token':os.environ['SETUP_TOKEN'],'username':'qa-review','password':'Synthetic-QA-Only-2026','workspace_name':'界面验证 · 合成数据'})
    assert response.status_code==200,response.text
    client.headers['X-CSRF-Token']=client.cookies['fn_csrf']
    for title,summary,points in [
        ('【界面演示】从零搭建个人知识库','这份合成资料演示如何把零散视频整理成可检索的知识库。作者从收集、总结和复盘三个步骤展开，强调先建立稳定的信息输入，再逐步完善分类。',['先按少量关键词建立稳定的信息来源。','每个视频只保留一条完整总结，避免碎片化。','每日复盘新增信息，持续更新相关对象。']),
        ('【界面演示】离线工具的使用与边界','这份合成资料介绍工具 A 的离线功能，并讨论安装准备、使用条件和常见限制。总结保留了适用条件，便于阅读时判断是否值得回看原资料。',['离线使用前需要完成初次安装。','不同使用场景的体验可能存在差异。','需要详细步骤时可回到原始资料。'])]:
        r=client.post('/api/v1/sources/import',headers={'Idempotency-Key':uuid.uuid4().hex},json={'title':title,'content':summary,'format':'text','rights_confirmed':True})
        assert r.status_code==202,r.text
        with engine.begin() as conn:
            source=one(conn,'SELECT * FROM sources WHERE id=:id',id=r.json()['source_ids'][0])
            saved=one(conn,"""INSERT INTO video_summaries(workspace_id,video_key,source_id,version_id,title,summary,points)
                VALUES(:ws,:key,:sid,:vid,:title,:summary,CAST(:points AS jsonb)) RETURNING *""",
                ws=source['workspace_id'],key=simple.video_key(source),sid=source['id'],vid=source['current_version_id'],title=title,summary=summary,points=simple.js(points))
            run=simple.new_round(conn,source['workspace_id'],'manual')
            simple.add_item(conn,run,source)
            execute(conn,"UPDATE summary_rounds SET state='succeeded',report=:report,finished_at=now() WHERE id=:id",id=run['id'],report='【合成演示】'+summary)
            execute(conn,"UPDATE jobs SET state='succeeded' WHERE id=:id",id=r.json()['job_id'])
    client.post('/api/v1/discovery-sources',json={'name':'AI 工具观察（演示，未启用）','keywords':['AI 工具','知识库'],'max_results_per_run':5,'daily_time':'08:00','enabled':False,'network_authorized':False})
(root/'.runtime/qa-simple-schema.txt').write_text(schema)
print('Synthetic V1 preview: http://127.0.0.1:8001',flush=True)
import uvicorn
try:
    uvicorn.run(app,host='127.0.0.1',port=8001,access_log=False)
finally:
    engine.dispose()
    with base.begin() as conn:
        conn.exec_driver_sql(f'DROP SCHEMA {schema} CASCADE')
    base.dispose()
