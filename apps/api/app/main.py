import base64
import json
import secrets
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from fastapi import FastAPI, Depends, HTTPException, Request, Response, Header, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy.exc import SQLAlchemyError, OperationalError, DBAPIError
from pydantic import BaseModel, Field
from app.config import settings
from app.auth import current_user, hasher, verify_password, new_session, DUMMY_HASH
from app.storage.db import engine, one, many, execute
from app.domain.evidence import digest, parse_segments, validate_anchor
from app.domain.policy import PublicationPolicy, POLICY_VERSION, mode_is_within
from app.domain import verification as verification_engine
from app.domain.fact_evidence import FactEvidenceError, normalize_link
from app.domain.secrets import SecretError, open_sealed, parse_cookie_input, seal
from app.domain.qr_sessions import TERMINAL, QrSessionError, qr_sessions
from app.domain.discovery import (
    DiscoveryConfigError, normalize_config, watermark_summary,
)
from app.domain.model_secrets import seal_api_key, validate_api_key
from app.ai import budget as ai_budget
from app.ai import gateway as ai_gateway
from app.ai import factcheck as ai_factcheck
from app.ai import providers as ai_providers
from app.collectors import CollectorError, credential_for, get_collector
from app.jobs.worker import RISK_COOLDOWN_SECONDS, discovery_item_retention_days, js

app = FastAPI(
    title='FishingNet', version='0.11.0', docs_url='/api/docs',
    description=('本地单用户信息聚合 API。统一错误格式为 '
                 '{"error":{"code","message","retryable","details"},"request_id"}，并在 X-Request-Id 响应头返回同一编号。'
                 '列表使用不透明 cursor，非法游标返回 400；资源修改必须携带 If-Match 数字版本，缺失返回 428，过期返回 409；'
                 '导入等异步写入必须携带 Idempotency-Key，同键不同内容返回 409。所有数据限定当前工作区，'
                 '事实结论受服务端发布门槛约束，来源引用的存在不代表事实为真。'
                 '关键词来源必须显式在本机授权联网后才可能发起请求；真实关键词发现尚未完成烟测。'
                 '模型调用默认关闭且预算为零：必须先配置供应商、模型、任务路由并设置预算，'
                 '显式测试才会发起真实请求；mock 提供器只返回合成结果。'))
User = Annotated[dict, Depends(current_user)]
PREFIX = '/api/v1'
STATUS_CODES = {
    400: 'BAD_REQUEST', 401: 'UNAUTHENTICATED', 403: 'FORBIDDEN', 404: 'NOT_FOUND',
    409: 'CONFLICT', 422: 'UNPROCESSABLE_ENTITY', 428: 'PRECONDITION_REQUIRED',
    429: 'RATE_LIMITED', 503: 'SERVICE_UNAVAILABLE',
}


class ApiError(Exception):
    """Business error with the contract shape: code, safe message, retryable."""

    def __init__(self, status: int, code: str, message: str, retryable: bool = False, details=None):
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}
        super().__init__(message)


def _request_id(request: Request) -> str:
    return getattr(request.state, 'request_id', None) or str(uuid.uuid4())


def _error_response(request: Request, status: int, code: str, message: str,
                    retryable: bool = False, details=None) -> JSONResponse:
    request_id = _request_id(request)
    return JSONResponse(
        {'error': {'code': code, 'message': message, 'retryable': retryable, 'details': details or {}},
         'request_id': request_id},
        status_code=status, headers={'X-Request-Id': request_id})


@app.middleware('http')
async def request_context(request, call_next):
    request.state.request_id = str(uuid.uuid4())
    origin = request.headers.get('origin')
    if request.method not in ('GET', 'HEAD', 'OPTIONS') and origin and origin not in (
            settings.app_origin, 'http://127.0.0.1:8000', 'http://127.0.0.1:5173', 'http://localhost:5173'):
        return _error_response(request, 403, 'ORIGIN_REJECTED', '来源不受信任')
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['X-Request-Id'] = request.state.request_id
    return response


@app.exception_handler(ApiError)
async def api_error_handler(request, exc: ApiError):
    return _error_response(request, exc.status, exc.code, exc.message, exc.retryable, exc.details)


@app.exception_handler(StarletteHTTPException)
async def error_handler(request, exc):
    code = STATUS_CODES.get(exc.status_code, f'HTTP_{exc.status_code}')
    detail = exc.detail if isinstance(exc.detail, str) else '请求未完成'
    return _error_response(request, exc.status_code, code, detail)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc: RequestValidationError):
    details = [{'field': '.'.join(str(part) for part in item.get('loc', [])[1:]),
                'message': item.get('msg', '输入无效'), 'type': item.get('type', 'value_error')}
               for item in exc.errors()]
    return _error_response(request, 422, 'VALIDATION_ERROR', '请求参数不符合要求', False, details)


@app.exception_handler(ValueError)
async def invalid_value(request, exc):
    return _error_response(request, 422, 'INVALID_INPUT', str(exc))


@app.exception_handler(SQLAlchemyError)
async def database_error(request, exc):
    return _error_response(request, 503, 'DATABASE_OPERATION_FAILED',
                           '数据库操作未完成，请检查服务状态或联系管理员', True)


def require_revision(value, subject: str = '资源') -> int:
    """If-Match is mandatory for mutations: missing 428, stale 409, malformed 422."""
    if value is None or not str(value).strip():
        raise ApiError(428, 'PRECONDITION_REQUIRED',
                       f'缺少 If-Match 版本前置条件，请刷新{subject}后重试', True)
    raw = str(value).strip()
    if raw.startswith('W/'):
        raw = raw[2:].strip()
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        raw = raw[1:-1]
    if not raw.isdigit():
        raise ApiError(422, 'INVALID_IF_MATCH', 'If-Match 必须是资源的数字版本号')
    return int(raw)


def encode_cursor(values: dict) -> str:
    raw = json.dumps(values, ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode('utf-8')
    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def decode_cursor(token, kind: str, schema: dict) -> dict:
    if token is None:
        return {}
    try:
        if not isinstance(token, str) or not 0 < len(token) <= 512:
            raise ValueError
        padded = token + '=' * (-len(token) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode('ascii')))
        if not isinstance(data, dict) or data.get('k') != kind:
            raise ValueError
        result = {}
        for field, field_type in schema.items():
            value = data.get(field)
            if not isinstance(value, str) or not value:
                raise ValueError
            if field_type == 'uuid':
                result[field] = str(uuid.UUID(value))
            elif field_type == 'ts':
                result[field] = datetime.fromisoformat(value).isoformat()
            elif field_type == 'int':
                if not value.isdigit():
                    raise ValueError
                result[field] = int(value)
            else:
                result[field] = value
        return result
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise ApiError(400, 'INVALID_CURSOR', '分页游标无效，请从第一页重新加载')


def page_limit(limit: int) -> int:
    if not 1 <= limit <= 100:
        raise ApiError(400, 'INVALID_LIMIT', 'limit 必须在 1 到 100 之间')
    return limit


def parse_time(value, label: str):
    """Accept a date or RFC3339 timestamp; reject anything else as a 400."""
    if value is None or not str(value).strip():
        return None
    try:
        return datetime.fromisoformat(str(value).strip()).isoformat()
    except ValueError:
        raise ApiError(400, 'INVALID_TIME_RANGE', f'{label} 必须是 RFC3339 时间或日期')


@app.get(PREFIX + '/health/live')
def health():
    return {'status': 'ok'}


@app.get(PREFIX + '/health/ready')
def ready():
    try:
        with engine.connect() as conn:
            one(conn, 'SELECT 1 AS ok')
    except SQLAlchemyError:
        return JSONResponse({'status': 'degraded', 'database': 'unavailable'}, status_code=503)
    return {'status': 'ok', 'database': 'ok'}


@app.get(PREFIX+'/auth/status')
def status():
    with engine.connect() as conn:
        return {'initialized': bool(one(conn,'SELECT id FROM users LIMIT 1'))}


class Setup(BaseModel):
    setup_token: str
    username: str = Field(min_length=3,max_length=50)
    password: str = Field(min_length=12,max_length=200)
    workspace_name: str = Field(min_length=1,max_length=80)
    timezone: str = 'Asia/Shanghai'


@app.post(PREFIX+'/auth/setup')
def setup(body:Setup,request:Request,response:Response):
    if not settings.setup_token or not secrets.compare_digest(body.setup_token,settings.setup_token):
        raise HTTPException(403,'初始化口令错误')
    try:
        ZoneInfo(body.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ApiError(422,'INVALID_TIMEZONE','未知时区，请填写如 Asia/Shanghai 的时区名称')
    with engine.begin() as conn:
        execute(conn,'SELECT pg_advisory_xact_lock(10001)')
        if one(conn,'SELECT id FROM users LIMIT 1'):
            raise HTTPException(409,'实例已经初始化')
        ws = one(conn,'INSERT INTO workspace_settings(name,timezone) VALUES(:name,:tz) RETURNING id',name=body.workspace_name,tz=body.timezone)
        user = one(conn,'INSERT INTO users(workspace_id,username,password_hash,display_name) VALUES(:ws,:name,:pw,:name) RETURNING *',ws=ws['id'],name=body.username,pw=hasher.hash(body.password))
        new_session(conn,user,request,response)
    return {'initialized':True}


class Login(BaseModel):
    username:str = Field(max_length=50)
    password:str = Field(max_length=200)


login_attempts = {}


@app.post(PREFIX+'/auth/login')
def login(body:Login,request:Request,response:Response):
    # Single API process in M1; distributed rate limiting is a release prerequisite.
    now = datetime.now(timezone.utc).timestamp()
    key = request.client.host if request.client else 'local'
    attempts = [t for t in login_attempts.get(key,[]) if t>now-300]
    if len(attempts)>=10:
        raise HTTPException(429,'尝试过于频繁，请稍后重试')
    login_attempts[key] = attempts+[now]
    with engine.begin() as conn:
        user = one(conn,'SELECT * FROM users WHERE username=:name',name=body.username)
        valid = verify_password(user['password_hash'] if user else DUMMY_HASH,body.password)
        if not user or not valid:
            raise HTTPException(401,'用户名或密码错误')
        new_session(conn,user,request,response)
    login_attempts.pop(key,None)
    return {'ok':True}


@app.get(PREFIX+'/auth/me')
def me(user:User):
    return {k:user[k] for k in ('id','username','display_name','workspace_id','revision')}


@app.post(PREFIX+'/auth/logout')
def logout(user:User,response:Response):
    with engine.begin() as conn:
        execute(conn,'UPDATE auth_sessions SET revoked_at=now() WHERE id=:id',id=user['session_id'])
    response.delete_cookie('fn_session'); response.delete_cookie('fn_csrf')
    return {'ok':True}


@app.get(PREFIX+'/auth/sessions')
def sessions(user:User):
    with engine.connect() as conn:
        items = many(conn,'SELECT id,created_at,last_seen_at,device FROM auth_sessions WHERE user_id=:uid AND revoked_at IS NULL AND absolute_expires_at>now() AND idle_expires_at>now() ORDER BY created_at DESC,id DESC',uid=user['id'])
    return {'items':[{**i,'current':i['id']==user['session_id']} for i in items],'next_cursor':None}


@app.delete(PREFIX+'/auth/sessions/{session_id}')
def revoke_session(session_id:uuid.UUID,user:User):
    with engine.begin() as conn:
        execute(conn,'UPDATE auth_sessions SET revoked_at=now() WHERE id=:id AND user_id=:uid',id=session_id,uid=user['id'])
    return {'ok':True}


@app.post(PREFIX+'/auth/sessions/revoke-others')
def revoke_other_sessions(user:User,request:Request,response:Response):
    """Revoke every other session and rotate the current one."""
    with engine.begin() as conn:
        execute(conn,'UPDATE auth_sessions SET revoked_at=now() WHERE user_id=:uid AND id<>:sid',uid=user['id'],sid=user['session_id'])
        execute(conn,'UPDATE auth_sessions SET revoked_at=now() WHERE id=:sid',sid=user['session_id'])
        new_session(conn,user,request,response)
    return {'ok':True,'message':'其他会话已注销，当前会话已轮换'}


class PasswordChange(BaseModel):
    current_password:str
    new_password:str=Field(min_length=12,max_length=200)


class ProfileChange(BaseModel):
    display_name:str=Field(min_length=1,max_length=80)
    timezone:str


@app.patch(PREFIX+'/auth/profile')
def profile_change(body:ProfileChange,user:User,if_match:Annotated[str|None,Header()]=None):
    try:
        ZoneInfo(body.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ApiError(422,'INVALID_TIMEZONE','未知时区，请填写如 Asia/Shanghai 的时区名称')
    revision=require_revision(if_match,'个人资料')
    with engine.begin() as conn:
        changed=one(conn,'UPDATE users SET display_name=:name,revision=revision+1 WHERE id=:id AND revision=:rev RETURNING id',name=body.display_name,id=user['id'],rev=revision)
        if not changed: raise ApiError(409,'VERSION_CONFLICT','个人资料已改变，请刷新后重试',True)
        execute(conn,'UPDATE workspace_settings SET timezone=:tz,revision=revision+1 WHERE id=:id',tz=body.timezone,id=user['workspace_id'])
    return {'ok':True}


@app.post(PREFIX+'/auth/password')
def password_change(body:PasswordChange,user:User,request:Request,response:Response):
    if not verify_password(user['password_hash'],body.current_password):
        raise HTTPException(403,'当前密码错误')
    with engine.begin() as conn:
        execute(conn,'UPDATE users SET password_hash=:pw,revision=revision+1 WHERE id=:id',pw=hasher.hash(body.new_password),id=user['id'])
        execute(conn,'UPDATE auth_sessions SET revoked_at=now() WHERE user_id=:id',id=user['id'])
        new_session(conn,user,request,response)
    return {'ok':True}


class ImportBody(BaseModel):
    title:str=Field(min_length=1,max_length=200)
    author_name:str=Field(default='用户导入',max_length=100)
    content:str=Field(min_length=1,max_length=5*1024*1024)
    format:Literal['text','md','srt','vtt']='text'
    rights_confirmed:bool
    source_id:uuid.UUID|None=None


def require_idempotency_key(value) -> str:
    key=(value or '').strip()
    if not key:
        raise ApiError(400,'IDEMPOTENCY_KEY_REQUIRED','写入请求必须提供 Idempotency-Key 请求头')
    if not 8<=len(key)<=200:
        raise ApiError(400,'INVALID_IDEMPOTENCY_KEY','Idempotency-Key 长度必须在 8 到 200 个字符之间')
    return key


def import_source(conn,body,ws,key,demo=False):
    if not body.rights_confirmed:
        raise ApiError(422,'RIGHTS_CONFIRMATION_REQUIRED','请确认拥有提供及处理材料的权利')
    body_hash=digest(body.model_dump_json()+str(demo))
    execute(conn,'SELECT pg_advisory_xact_lock(hashtextextended(:key,0))',key=str(ws)+key)
    prior=one(conn,'SELECT * FROM request_idempotency WHERE workspace_id=:ws AND key=:key',ws=ws,key=key)
    if prior:
        if prior['body_hash']!=body_hash:
            raise ApiError(409,'IDEMPOTENCY_KEY_CONFLICT','同一幂等键不能提交不同内容',False)
        return prior['response']
    segments=parse_segments(body.content,body.format)
    raw_hash=digest(body.content)
    if body.source_id:
        source=one(conn,"SELECT * FROM sources WHERE id=:id AND workspace_id=:ws AND availability='available' FOR UPDATE",id=body.source_id,ws=ws)
        if not source:
            raise ApiError(404,'SOURCE_NOT_FOUND','来源不存在')
    else:
        source=one(conn,'''INSERT INTO sources(workspace_id,platform,external_id,title,author_name,rights_policy,metadata)
          VALUES(:ws,'manual',:eid,:title,:author,CAST(:rights AS jsonb),CAST(:metadata AS jsonb)) RETURNING *''',
          ws=ws,eid=str(uuid.uuid4()),title=body.title,author=body.author_name,rights=js({'schema_version':'rights.v1','user_authorized':True,'cloud_allowed':False,'retention_days':30}),metadata=js({'schema_version':'source.v1','demo':demo}))
    version=one(conn,'SELECT * FROM source_versions WHERE source_id=:sid AND raw_hash=:hash',sid=source['id'],hash=raw_hash)
    if not version:
        count=one(conn,'SELECT coalesce(max(version_no),0)+1 AS n FROM source_versions WHERE source_id=:sid',sid=source['id'])['n']
        version=one(conn,'''INSERT INTO source_versions(workspace_id,source_id,version_no,raw_hash,normalized_hash,segments)
          VALUES(:ws,:sid,:n,:hash,:nhash,CAST(:segments AS jsonb)) RETURNING *''',ws=ws,sid=source['id'],n=count,hash=raw_hash,nhash=digest(js(segments)),segments=js(segments))
        execute(conn,'UPDATE sources SET current_version_id=:vid,revision=revision+1,updated_at=now() WHERE id=:sid',vid=version['id'],sid=source['id'])
        if count>1:
            invalidate_source(conn,ws,source['id'],'source_version_changed')
    job=one(conn,'''INSERT INTO jobs(workspace_id,kind,input,config_snapshot,idempotency_key)
      VALUES(:ws,'process_source',CAST(:input AS jsonb),:snapshot,:key)
      ON CONFLICT(workspace_id,idempotency_key) DO UPDATE SET idempotency_key=EXCLUDED.idempotency_key RETURNING id''',
      ws=ws,input=js({'version_id':version['id']}),snapshot=js({'provider':'mock','data_policy':'local_only','policy_version':POLICY_VERSION}),key=f'process:{version["id"]}:v1')
    result={'source_ids':[str(source['id'])],'job_id':str(job['id'])}
    execute(conn,'INSERT INTO request_idempotency(workspace_id,key,body_hash,response) VALUES(:ws,:key,:hash,CAST(:response AS jsonb))',ws=ws,key=key,hash=body_hash,response=js(result))
    return result


@app.post(PREFIX+'/sources/import',status_code=202)
def import_document(body:ImportBody,user:User,idempotency_key:Annotated[str|None,Header()]=None):
    key=require_idempotency_key(idempotency_key)
    with engine.begin() as conn:
        return import_source(conn,body,user['workspace_id'],key)


@app.post(PREFIX+'/demo',status_code=202)
def demo(user:User):
    results=[]
    examples=[
       ('初次试用','【合成示例】Plugin X 2.0：我认为它的界面很清晰。'),
       ('另一份界面评价','【合成示例】Plugin X 2.0：我认为它的界面很清晰。'),
       ('批评与限制','【合成示例】Plugin X 1.0：我认为它的设置过程令人困惑。'),
       ('功能宣传待核实','【合成示例】Plugin X 2.0 支持离线索引。'),
       ('有限测试待复核','【合成示例】Plugin X 2.0 在我的测试环境 Windows 中不能离线索引。'),
       ('更正材料待复核','【合成示例】Plugin X 2.0 只在测试版可用，正式版不支持离线索引。')]
    with engine.begin() as conn:
        for i,(title,content) in enumerate(examples):
            results.append(import_source(conn,ImportBody(title='示例数据 · '+title,author_name=f'虚构作者 {i+1}',content=content,rights_confirmed=True),user['workspace_id'],f'demo-fixture-v1-{i}',True))
    return {'items':results}


class UrlImport(BaseModel):
    url:str=Field(min_length=1,max_length=500)
    rights_confirmed:bool
    platform:Literal['bilibili']='bilibili'


def collector_api_error(exc:CollectorError) -> ApiError:
    return ApiError(422,exc.code,exc.message,exc.retryable,exc.details)


def active_collect_job(conn,ws,source_id):
    return one(conn,"""SELECT * FROM jobs WHERE workspace_id=:ws AND idempotency_key LIKE :prefix
        AND state IN ('queued','running','retry_wait','blocked_credentials') ORDER BY created_at DESC LIMIT 1""",
        ws=ws,prefix=f'collect:{source_id}:%')


def import_url_source(conn,body,ws,key):
    """Create a pending B站 source and enqueue collection. No network here."""
    if not body.rights_confirmed:
        raise ApiError(422,'RIGHTS_CONFIRMATION_REQUIRED','请确认你有权提供并处理该链接对应的内容')
    try:
        collector=get_collector(body.platform)
        locator=collector.parse_locator(body.url)
    except CollectorError as exc:
        raise collector_api_error(exc)
    body_hash=digest(body.model_dump_json())
    execute(conn,'SELECT pg_advisory_xact_lock(hashtextextended(:key,0))',key=str(ws)+key)
    prior=one(conn,'SELECT * FROM request_idempotency WHERE workspace_id=:ws AND key=:key',ws=ws,key=key)
    if prior:
        if prior['body_hash']!=body_hash:
            raise ApiError(409,'IDEMPOTENCY_KEY_CONFLICT','同一幂等键不能提交不同内容',False)
        return prior['response']
    external_id=f'{locator.video_id}:p{locator.page}'
    source=one(conn,'SELECT * FROM sources WHERE workspace_id=:ws AND platform=:p AND external_id=:e FOR UPDATE',
               ws=ws,p=body.platform,e=external_id)
    if source and source['availability']=='deleted':
        raise ApiError(409,'SOURCE_DELETED','该来源已被删除，不能重新导入同一链接',False)
    existed=bool(source)
    if not source:
        source=one(conn,'''INSERT INTO sources(workspace_id,platform,external_id,source_kind,title,canonical_url,
            availability,content_status,rights_policy,metadata)
            VALUES(:ws,:p,:e,'video',:title,:url,'unknown','pending',CAST(:rights AS jsonb),CAST(:meta AS jsonb)) RETURNING *''',
            ws=ws,p=body.platform,e=external_id,
            title=f'B站视频 {locator.video_id}（第 {locator.page} P，待采集）',url=locator.canonical_url,
            rights=js({'schema_version':'rights.v1','user_authorized':True,'basis':'user_provided_link',
                       'cloud_allowed':False,'retention_days':30}),
            meta=js({'schema_version':'source.v1','platform':body.platform,'bvid':locator.video_id,
                     'page':locator.page,'subtitle_status':'unknown','collector':'bilibili.v2',
                     'collector_schema':'collector.v1'}))
    job=active_collect_job(conn,ws,source['id'])
    if not job:
        job=one(conn,'''INSERT INTO jobs(workspace_id,kind,input,config_snapshot,idempotency_key)
            VALUES(:ws,'collect_source',CAST(:input AS jsonb),CAST(:snapshot AS jsonb),:key) RETURNING id,state''',
            ws=ws,
            input=js({'source_id':str(source['id']),'url':locator.canonical_url,'page':locator.page,
                      'platform':body.platform,'external_id':external_id}),
            snapshot=js({'provider':'none','data_policy':'local_only','policy_version':POLICY_VERSION,
                         'collector':'bilibili.v2','network':'real','ai_analysis':False}),
            key=f'collect:{source["id"]}:r{source["revision"]}:{uuid.uuid4().hex[:8]}')
    result={'source_ids':[str(source['id'])],'job_id':str(job['id']),'existed':existed,
            'collection':'queued','note':'采集需要用户已授权的本机网络；元数据与字幕状态在任务中更新。未进行 AI 分析。'}
    execute(conn,'INSERT INTO request_idempotency(workspace_id,key,body_hash,response) VALUES(:ws,:key,:hash,CAST(:response AS jsonb))',
            ws=ws,key=key,hash=body_hash,response=js(result))
    execute(conn,"INSERT INTO audit_events(workspace_id,action,object_id) VALUES(:ws,'bilibili_url_import',:sid)",
            ws=ws,sid=source['id'])
    return result


@app.post(PREFIX+'/sources/import-url',status_code=202)
def import_url(body:UrlImport,user:User,idempotency_key:Annotated[str|None,Header()]=None):
    key=require_idempotency_key(idempotency_key)
    with engine.begin() as conn:
        return import_url_source(conn,body,user['workspace_id'],key)


@app.post(PREFIX+'/sources/{source_id}/refresh',status_code=202)
def refresh_source(source_id:uuid.UUID,user:User,if_match:Annotated[str|None,Header()]=None):
    with engine.begin() as conn:
        source=one(conn,'SELECT * FROM sources WHERE id=:id AND workspace_id=:ws FOR UPDATE',id=source_id,ws=user['workspace_id'])
        if not source: raise ApiError(404,'SOURCE_NOT_FOUND','来源不存在')
        if source['availability']=='deleted': raise ApiError(409,'SOURCE_DELETED','来源已删除，不能重新采集',False)
        if source['platform']!='bilibili':
            raise ApiError(422,'PLATFORM_NOT_COLLECTABLE','只有 B站 来源支持重新采集；手动材料请重新导入')
        revision=require_revision(if_match,'来源')
        if source['revision']!=revision: raise ApiError(409,'VERSION_CONFLICT','来源已变化，请刷新后重试',True)
        job=active_collect_job(conn,user['workspace_id'],source_id)
        if job and job['state']=='blocked_credentials':
            # A credential-blocked collection resumes in place instead of piling up
            # a second task that would never run.
            job=one(conn,"""UPDATE jobs SET state='queued',stage='queued',fence=fence+1,error_code=NULL,
                lease_until=NULL,checkpoint='{}'::jsonb,updated_at=now() WHERE id=:id RETURNING id,state""",id=job['id'])
        if not job:
            job=one(conn,'''INSERT INTO jobs(workspace_id,kind,input,config_snapshot,idempotency_key)
                VALUES(:ws,'collect_source',CAST(:input AS jsonb),CAST(:snapshot AS jsonb),:key) RETURNING id,state''',
                ws=user['workspace_id'],
                input=js({'source_id':str(source_id),'url':source['canonical_url'],'page':(source['metadata'] or {}).get('page',1),
                          'platform':'bilibili','external_id':source['external_id']}),
                snapshot=js({'provider':'none','data_policy':'local_only','policy_version':POLICY_VERSION,
                             'collector':'bilibili.v2','network':'real','ai_analysis':False}),
                key=f'collect:{source_id}:r{revision}:{uuid.uuid4().hex[:8]}')
    return {'ok':True,'job_id':str(job['id']),'state':job['state'],
            'note':'已排队重新采集；真实网络请求只在 worker 内低频执行，未进行 AI 分析。'}


CHECK_COOLDOWN_SECONDS=30


def connection_view(row):
    if not row:
        return {'status':'not_configured','credential_configured':False,'alias':'',
                'last_checked_at':None,'last_error_code':None,'last_check_detail':'',
                'cooldown_until':None,'last_success_at':None}
    return {'status':row['status'],'credential_configured':bool(row['credential_sealed']),
            'alias':row['alias'],'last_checked_at':row['last_checked_at'],
            'last_error_code':row['last_error_code'],'last_check_detail':row['last_check_detail'] or '',
            'cooldown_until':row['cooldown_until'],'last_success_at':row['last_success_at']}


@app.get(PREFIX+'/platforms')
def platforms(user:User):
    with engine.connect() as conn:
        row=one(conn,'SELECT * FROM platform_connections WHERE workspace_id=:ws AND platform=:p',
                ws=user['workspace_id'],p='bilibili')
        discovery=one(conn,"""SELECT count(*) AS total,
            count(*) FILTER (WHERE enabled) AS enabled,
            count(*) FILTER (WHERE enabled AND network_authorized) AS scheduled
            FROM discovery_sources WHERE workspace_id=:ws AND deleted_at IS NULL""",
            ws=user['workspace_id'])
        pending=one(conn,"""SELECT
            count(*) FILTER (WHERE state='blocked_credentials') AS blocked,
            count(*) FILTER (WHERE state='failed' AND error_code IN
              ('CREDENTIAL_EXPIRED','CREDENTIAL_REQUIRED','RISK_CONTROL','RATE_LIMITED')) AS failed
            FROM jobs WHERE workspace_id=:ws AND kind='collect_source'""",
            ws=user['workspace_id'])
    now=datetime.now(timezone.utc)
    cooldown=row['cooldown_until'] if row else None
    remaining=max(0,int((cooldown-now).total_seconds())) if cooldown else 0
    blocked=int(pending['blocked'])
    failed=int(pending['failed'])
    recoverable=bool(row and row['credential_sealed'] and row['status']!='expired'
                     and remaining==0 and blocked+failed>0)
    recovery={'blocked_jobs':blocked,'failed_jobs':failed,
              'recoverable':recoverable,'cooldown_until':cooldown,
              'retry_after_seconds':remaining or None,
              'note':('登录态过期时请先在应用内重新提供 Cookie 或扫码；风控冷却期内不会自动重试。'
                      '恢复只会重新排队采集任务，不会修改来源、启用状态或联网授权。')}
    caps=get_collector('bilibili').capabilities().as_dict()
    return {'items':[
        {'platform':'bilibili','label':'哔哩哔哩','implemented':True,'collector':caps,
         'connection':connection_view(row),'recovery':recovery,
         'discovery':{'total':int(discovery['total']),'enabled':int(discovery['enabled']),
                      'scheduled':int(discovery['scheduled']),'real_verified':False,
                      'max_pages_per_run':caps['max_discovery_pages']},
         'requirements':['只支持单个 BV 号视频链接；b23.tv 短链暂不支持','读取字幕需要你在本机提供 SESSDATA，或在应用内扫码登录',
                         '扫码登录会实时访问 B站 网页登录接口（非官方开放平台授权），二维码与轮询都只在本机进行',
                         '关键词来源需要你显式勾选联网授权；未授权时不会发起任何请求，调度器也不会运行它',
                         '真实网络请求只在你手动导入/检查/扫码或授权后的调度中低频执行，尚未完成真实烟测']},
        {'platform':'manual','label':'手动材料导入','implemented':True,'collector':None,'connection':None,
         'requirements':['文本 / Markdown / SRT / VTT 单文件导入；批量导入已按用户决定推迟']},
        {'platform':'youtube','label':'YouTube','implemented':False,'collector':None,'connection':None,
         'requirements':['仅保留适配器契约，未实现真实联网采集']}],
        'credential_schema_version':'platform-credential.v1',
        'live_collection_verified':False,'live_keyword_discovery_verified':False,
        'note':'平台值不能由客户端决定账号身份；未实现官方开放平台授权；关键词发现只读取搜索结果的公开元数据。'}


class CredentialInput(BaseModel):
    cookie:str=Field(min_length=1,max_length=4096)
    alias:str=Field(default='',max_length=80)


def _platform_row(conn,ws,platform):
    return one(conn,'SELECT * FROM platform_connections WHERE workspace_id=:ws AND platform=:p',ws=ws,p=platform)


@app.put(PREFIX+'/platforms/{platform}/credential')
def set_platform_credential(platform:Literal['bilibili'],body:CredentialInput,user:User):
    try:
        values=parse_cookie_input(body.cookie)
        sealed=seal(values)
    except SecretError as exc:
        status=503 if exc.code=='MASTER_KEY_NOT_CONFIGURED' else 422
        raise ApiError(status,exc.code,exc.message,False)
    caps=get_collector(platform).capabilities().as_dict()
    with engine.begin() as conn:
        row=one(conn,'''INSERT INTO platform_connections(workspace_id,platform,alias,credential_sealed,status,capabilities)
            VALUES(:ws,:p,:alias,:sealed,'unverified',CAST(:caps AS jsonb))
            ON CONFLICT(workspace_id,platform) DO UPDATE SET
              credential_sealed=EXCLUDED.credential_sealed,alias=EXCLUDED.alias,status='unverified',
              last_error_code=NULL,last_check_detail=NULL,capabilities=EXCLUDED.capabilities,
              updated_at=now(),revision=platform_connections.revision+1
            RETURNING id,status,alias,revision,updated_at''',
            ws=user['workspace_id'],p=platform,alias=body.alias,sealed=sealed,caps=js(caps))
        execute(conn,"INSERT INTO audit_events(workspace_id,action,object_id) VALUES(:ws,'platform_credential_set',:id)",
                ws=user['workspace_id'],id=row['id'])
    return {'platform':platform,'status':row['status'],'alias':row['alias'],'configured':True,
            'revision':row['revision'],'updated_at':row['updated_at'],
            'message':'已在本地加密保存 SESSDATA（不回显）。这不是平台授权登录；请点击“检查连接”验证是否仍有效。'}


@app.delete(PREFIX+'/platforms/{platform}/credential')
def remove_platform_credential(platform:Literal['bilibili'],user:User):
    qr_sessions.clear(user['workspace_id'],platform)
    with engine.begin() as conn:
        row=_platform_row(conn,user['workspace_id'],platform)
        if not row:
            return {'ok':True,'status':'unconfigured','already_removed':True,
                    'message':'本地没有该平台的连接记录'}
        execute(conn,"""UPDATE platform_connections SET credential_sealed=NULL,status='unconfigured',
            last_error_code=NULL,last_check_detail='已移除本地连接',updated_at=now(),revision=revision+1
            WHERE id=:id AND workspace_id=:ws""",id=row['id'],ws=user['workspace_id'])
        execute(conn,"INSERT INTO audit_events(workspace_id,action,object_id) VALUES(:ws,'platform_credential_removed',:id)",
                ws=user['workspace_id'],id=row['id'])
    return {'ok':True,'status':'unconfigured',
            'message':'已删除本地密文与连接记录；平台侧登录状态不受影响，本应用不会声称“平台授权已撤销”。'}


@app.post(PREFIX+'/platforms/{platform}/check')
def check_platform(platform:Literal['bilibili'],user:User):
    with engine.connect() as conn:
        row=_platform_row(conn,user['workspace_id'],platform)
    now=datetime.now(timezone.utc)
    if not row or not row['credential_sealed']:
        return {'platform':platform,'status':'not_configured','live_check':False,
                'checked_at':now.isoformat(),'metadata_capability':True,'subtitle_capability':False,
                'detail':'未配置 B站 登录 Cookie：只能读取公开元数据。未发起任何网络请求。','verification':'not_executed'}
    if row['last_checked_at'] and (now-row['last_checked_at']).total_seconds()<CHECK_COOLDOWN_SECONDS:
        remaining=CHECK_COOLDOWN_SECONDS-int((now-row['last_checked_at']).total_seconds())
        raise ApiError(429,'RATE_LIMITED','检查连接过于频繁，请稍后再试',True,
                       {'retry_after_seconds':max(1,remaining)})
    try:
        sealed=open_sealed(row['credential_sealed'])
    except SecretError as exc:
        raise ApiError(409,exc.code,exc.message,False)
    collector=get_collector(platform)
    result=collector.check_account(credential_for(platform,sealed))
    caps=collector.capabilities().as_dict()
    with engine.begin() as conn:
        execute(conn,"""UPDATE platform_connections SET status=:status,capabilities=CAST(:caps AS jsonb),
            last_checked_at=now(),last_error_code=:code,last_check_detail=:detail,updated_at=now(),revision=revision+1
            WHERE id=:id AND workspace_id=:ws""",
            status=result.status,caps=js(caps),code=result.error_code,detail=result.detail[:500],
            id=row['id'],ws=user['workspace_id'])
        execute(conn,"INSERT INTO audit_events(workspace_id,action,object_id,details) VALUES(:ws,'platform_check',:id,CAST(:d AS jsonb))",
                ws=user['workspace_id'],id=row['id'],d=js({'status':result.status,'error_code':result.error_code}))
    return {'platform':platform,'status':result.status,'live_check':True,
            'checked_at':result.checked_at.isoformat() if result.checked_at else now.isoformat(),
            'display_name':result.display_name,'user_id':result.user_id,
            'metadata_capability':result.metadata_capability,'subtitle_capability':result.subtitle_capability,
            'error_code':result.error_code,'detail':result.detail,
            'verification':'live' if result.status in ('connected','expired','invalid','unavailable') else 'not_executed'}


@app.post(PREFIX+'/platforms/{platform}/recover',status_code=202)
def recover_platform_work(platform:Literal['bilibili'],user:User):
    """Requeue collection work paused by an expired login or risk cooldown.

    This never bypasses a platform challenge: expired credentials must be
    replaced first, and a risk cooldown is enforced server-side. Only the
    user-owned collection jobs are requeued; sources and switches stay untouched.
    """
    now=datetime.now(timezone.utc)
    with engine.begin() as conn:
        row=one(conn,'SELECT * FROM platform_connections WHERE workspace_id=:ws AND platform=:p FOR UPDATE',
                ws=user['workspace_id'],p=platform)
        if not row or not row['credential_sealed']:
            raise ApiError(409,'CREDENTIAL_REQUIRED',
                           '本机还没有可用的 B站 登录态；请先提供 Cookie 或扫码登录，再恢复采集',False)
        if row['status']=='expired':
            raise ApiError(409,'CREDENTIAL_EXPIRED',
                           '登录态已失效。请重新提供本机 Cookie 或扫码登录后再恢复；不会自动重试',False)
        if row['cooldown_until'] and row['cooldown_until']>now:
            remaining=max(1,int((row['cooldown_until']-now).total_seconds())+1)
            raise ApiError(429,'RISK_COOLDOWN',
                           'B站风控冷却尚未结束，暂时不能恢复采集；请稍后再试',True,
                           {'retry_after_seconds':remaining})
        jobs=many(conn,"""SELECT id FROM jobs WHERE workspace_id=:ws AND kind='collect_source'
            AND (state='blocked_credentials' OR (state='failed' AND error_code IN
              ('CREDENTIAL_EXPIRED','CREDENTIAL_REQUIRED','RISK_CONTROL','RATE_LIMITED')))
            ORDER BY created_at LIMIT 20 FOR UPDATE""",ws=user['workspace_id'])
        for job in jobs:
            execute(conn,"""UPDATE jobs SET state='queued',stage='queued',fence=fence+1,attempts=0,
                error_code=NULL,lease_until=NULL,result=CAST('{}' AS jsonb),checkpoint='{}'::jsonb,
                updated_at=now() WHERE id=:id AND workspace_id=:ws""",
                    id=job['id'],ws=user['workspace_id'])
        execute(conn,"""UPDATE platform_connections SET
            status=CASE WHEN status='risk_control' THEN 'unverified' ELSE status END,
            cooldown_until=NULL,last_error_code=CASE WHEN status='risk_control' THEN NULL ELSE last_error_code END,
            last_check_detail=:detail,updated_at=now(),revision=revision+1
            WHERE id=:id AND workspace_id=:ws""",
                detail=('已恢复 %d 个被登录态/风控阻塞的采集任务' % len(jobs)) if jobs
                       else '没有需要恢复的采集任务',
                id=row['id'],ws=user['workspace_id'])
        execute(conn,"INSERT INTO audit_events(workspace_id,action,object_id,details) VALUES(:ws,'platform_recovery',:id,CAST(:d AS jsonb))",
                ws=user['workspace_id'],id=row['id'],d=js({'requeued':len(jobs)}))
    return {'ok':True,'platform':platform,'requeued':len(jobs),
            'job_ids':[str(job['id']) for job in jobs],
            'state':'queued' if jobs else 'idle',
            'note':('已重新排队；worker 会在下一次 tick 按低频限速执行。'
                    '若仍未替换失效凭证，任务会再次被标记为凭证阻塞。')}


@app.post(PREFIX+'/platforms/{platform}/qr',status_code=201)
def start_platform_qr(platform:Literal['bilibili'],user:User):
    """Create a web-login QR challenge. This is one bounded upstream call."""
    collector=get_collector(platform)
    if not collector.capabilities().qr_login:
        raise ApiError(422,'QR_LOGIN_UNSUPPORTED','该平台没有可用的扫码登录适配器')
    try:
        session=qr_sessions.reserve(user['workspace_id'],platform)
    except QrSessionError as exc:
        details={}
        if exc.retry_after:
            details={'retry_after_seconds':exc.retry_after}
        raise ApiError(429 if exc.code=='QR_RATE_LIMITED' else 409,exc.code,exc.message,
                       exc.code=='QR_RATE_LIMITED',details)
    try:
        login=collector.start_qr_login()
    except CollectorError as exc:
        qr_sessions.drop(user['workspace_id'],platform,session.auth_id)
        raise collector_api_error(exc)
    qr_sessions.attach(session,login.key,login.url)
    with engine.begin() as conn:
        execute(conn,"INSERT INTO audit_events(workspace_id,action) VALUES(:ws,'platform_qr_started')",
                ws=user['workspace_id'])
    view=session.view()
    view.update(status='waiting_scan',qr_url=login.url,expires_in=login.expires_in)
    return view


@app.get(PREFIX+'/platforms/{platform}/qr/{auth_id}')
def poll_platform_qr(platform:Literal['bilibili'],auth_id:uuid.UUID,user:User):
    session=qr_sessions.get(user['workspace_id'],platform,str(auth_id))
    if not session:
        raise ApiError(410,'QR_SESSION_EXPIRED','扫码会话已失效，请刷新二维码',False)
    if session.status in TERMINAL or not qr_sessions.begin_poll(session):
        return session.view()
    collector=get_collector(platform)
    try:
        result=collector.poll_qr_login(session.key)
    except CollectorError as exc:
        qr_sessions.update(session,'error',exc.message,exc.code,exc.retryable)
        return session.view()
    if result.status=='confirmed':
        try:
            sealed=seal(result.cookies)
        except SecretError as exc:
            qr_sessions.update(session,'error',exc.message,exc.code,False)
            return session.view()
        caps=collector.capabilities().as_dict()
        with engine.begin() as conn:
            row=one(conn,'''INSERT INTO platform_connections(workspace_id,platform,alias,credential_sealed,status,capabilities,last_check_detail)
                VALUES(:ws,:p,'扫码登录',:sealed,'connected',CAST(:caps AS jsonb),'扫码登录成功')
                ON CONFLICT(workspace_id,platform) DO UPDATE SET
                  credential_sealed=EXCLUDED.credential_sealed,status='connected',
                  capabilities=EXCLUDED.capabilities,last_error_code=NULL,last_check_detail='扫码登录成功',
                  alias=CASE WHEN platform_connections.alias='' THEN '扫码登录' ELSE platform_connections.alias END,
                  updated_at=now(),revision=platform_connections.revision+1
                RETURNING id,status,revision''',
                ws=user['workspace_id'],p=platform,sealed=sealed,caps=js(caps))
            execute(conn,"INSERT INTO audit_events(workspace_id,action,object_id) VALUES(:ws,'platform_qr_connected',:id)",
                    ws=user['workspace_id'],id=row['id'])
        qr_sessions.drop(user['workspace_id'],platform,session.auth_id)
        return {'auth_id':session.auth_id,'platform':platform,'status':'connected',
                'message':'扫码登录成功：凭证已加密保存，不回显、不写日志',
                'credential_configured':True,'revision':row['revision']}
    qr_sessions.update(session,result.status,result.message,result.error_code,result.retryable)
    view=session.view()
    if result.status=='expired':
        qr_sessions.drop(user['workspace_id'],platform,session.auth_id)
    return view


@app.post(PREFIX+'/platforms/{platform}/qr/{auth_id}/cancel')
def cancel_platform_qr(platform:Literal['bilibili'],auth_id:uuid.UUID,user:User):
    qr_sessions.drop(user['workspace_id'],platform,str(auth_id))
    return {'auth_id':str(auth_id),'platform':platform,'status':'cancelled',
            'message':'已取消扫码登录；没有保存任何凭证'}


# ---------------------------------------------------------------- discovery
MANUAL_RUN_COOLDOWN_SECONDS=10


class DiscoverySourceInput(BaseModel):
    name:str=Field(min_length=1,max_length=80)
    platform:Literal['bilibili']='bilibili'
    keywords:list[str]=Field(default_factory=list)
    exclude_terms:list[str]=Field(default_factory=list)
    languages:list[str]|None=None
    match_mode:Literal['any','all']='any'
    language_policy:Literal['prefer','require']='prefer'
    page_policy:Literal['first','explicit']='first'
    target_pages:list[int]=Field(default_factory=list)
    max_results_per_run:int=10
    max_pages_per_run:int=2
    max_requests_per_run:int=3
    rescan_after_hours:int=168
    schedule_interval_minutes:int=1440
    enabled:bool=False
    network_authorized:bool=False


def discovery_view(row):
    return {
        'id':str(row['id']),'platform':row['platform'],'name':row['name'],
        'keywords':row['keywords'],'exclude_terms':row['exclude_terms'],
        'languages':row['languages'],'match_mode':row['match_mode'],
        'language_policy':row['language_policy'],'page_policy':row['page_policy'],
        'target_pages':row['target_pages'],
        'limits':{'max_results_per_run':row['max_results_per_run'],
                  'max_pages_per_run':row['max_pages_per_run'],
                  'max_requests_per_run':row['max_requests_per_run'],
                  'rescan_after_hours':row['rescan_after_hours']},
        'schedule_interval_minutes':row['schedule_interval_minutes'],
        'enabled':row['enabled'],'network_authorized':row['network_authorized'],
        'next_run_at':row['next_run_at'],'last_run_at':row['last_run_at'],
        'last_success_at':row['last_success_at'],'last_error_code':row['last_error_code'],
        'failure_streak':row['failure_streak'],
        'watermark':watermark_summary(row['watermark']),
        'revision':row['revision'],'created_at':row['created_at'],'updated_at':row['updated_at'],
        'live_discovery_verified':False,
        'note':('关键词发现的上游请求尚未完成真实烟测。只有在本机明确授权联网、且启用来源后，'
                'scheduler 才会低频发起请求；失败会记录稳定错误码，不会伪装成“没有新内容”。'),
    }


def discovery_config_error(exc:DiscoveryConfigError) -> ApiError:
    return ApiError(422,'DISCOVERY_CONFIG_INVALID',exc.message,False,{'field':exc.field})


def _discovery_row(conn,ws,source_id,lock=False):
    return one(conn,'SELECT * FROM discovery_sources WHERE workspace_id=:ws AND id=:id'
               + (' FOR UPDATE' if lock else ''),ws=ws,id=source_id)


@app.get(PREFIX+'/discovery-sources')
def discovery_sources(user:User):
    with engine.connect() as conn:
        rows=many(conn,"""SELECT s.*, (SELECT count(*) FROM discovery_runs r WHERE r.workspace_id=s.workspace_id
            AND r.source_id=s.id) AS run_count,
            (SELECT r.state FROM discovery_runs r WHERE r.workspace_id=s.workspace_id
            AND r.source_id=s.id ORDER BY r.requested_at DESC LIMIT 1) AS last_run_state,
            (SELECT count(*) FROM sources o WHERE o.workspace_id=s.workspace_id
            AND o.discovery_source_id=s.id AND o.availability<>'deleted') AS source_count
            FROM discovery_sources s WHERE s.workspace_id=:ws AND s.deleted_at IS NULL
            ORDER BY s.created_at DESC""",ws=user['workspace_id'])
    capability=get_collector('bilibili').capabilities().as_dict()
    return {'items':[{**discovery_view(row),'run_count':int(row['run_count']),
                      'last_run_state':row['last_run_state'],
                      'source_count':int(row['source_count'])} for row in rows],
            'capability':{'keyword_discovery':capability['keyword_discovery'],
                          'max_discovery_pages':capability['max_discovery_pages'],
                          'real_discovery_verified':False},
            'retention':{'run_items_days':discovery_item_retention_days(),
                         'note':('候选明细只保留最近 N 天；运行账本与计数不会删除。'
                                 '保留期由 FISHINGNET_DISCOVERY_ITEM_RETENTION_DAYS 控制（1–3650 天）。')},
            'scheduler':{'enabled':True,
                         'note':'调度器仅在 worker 进程中运行短事务；只处理已启用且已在本机授权联网的来源。'},
            'note':'关键词来源只产生来源与采集任务，不产生推荐流，也不会自动评论/@/回复。'}


@app.post(PREFIX+'/discovery-sources',status_code=201)
def create_discovery_source(body:DiscoverySourceInput,user:User):
    try:
        config=normalize_config(body.model_dump())
    except DiscoveryConfigError as exc:
        raise discovery_config_error(exc)
    caps=get_collector(config['platform']).capabilities()
    if not caps.keyword_discovery:
        raise ApiError(422,'DISCOVERY_NOT_IMPLEMENTED','该平台尚未实现关键词发现',False)
    with engine.begin() as conn:
        existing=one(conn,'SELECT id FROM discovery_sources WHERE workspace_id=:ws AND name=:name',
                     ws=user['workspace_id'],name=config['name'])
        if existing:
            raise ApiError(409,'DISCOVERY_NAME_CONFLICT','已有同名的关键词来源',False)
        row=one(conn,'''INSERT INTO discovery_sources(workspace_id,platform,name,keywords,exclude_terms,
            languages,match_mode,language_policy,page_policy,target_pages,max_results_per_run,
            max_pages_per_run,max_requests_per_run,rescan_after_hours,schedule_interval_minutes,
            enabled,network_authorized,next_run_at)
            VALUES(:ws,:platform,:name,CAST(:keywords AS jsonb),CAST(:terms AS jsonb),
            CAST(:languages AS jsonb),:match_mode,:language_policy,:page_policy,
            CAST(:pages AS jsonb),:max_results,:max_pages,:max_requests,:rescan,:interval,
            :enabled,:authorized,CASE WHEN :enabled AND :authorized THEN now() ELSE NULL END)
            RETURNING *''',
            ws=user['workspace_id'],platform=config['platform'],name=config['name'],
            keywords=js(config['keywords']),terms=js(config['exclude_terms']),
            languages=js(config['languages']),match_mode=config['match_mode'],
            language_policy=config['language_policy'],page_policy=config['page_policy'],
            pages=js(config['target_pages']),max_results=config['max_results_per_run'],
            max_pages=config['max_pages_per_run'],max_requests=config['max_requests_per_run'],
            rescan=config['rescan_after_hours'],interval=config['schedule_interval_minutes'],
            enabled=config['enabled'],authorized=config['network_authorized'])
        execute(conn,"INSERT INTO audit_events(workspace_id,action,object_id) VALUES(:ws,'discovery_source_created',:id)",
                ws=user['workspace_id'],id=row['id'])
    return discovery_view(row)


@app.get(PREFIX+'/discovery-sources/{source_id}')
def discovery_source_detail(source_id:uuid.UUID,user:User):
    with engine.connect() as conn:
        row=_discovery_row(conn,user['workspace_id'],source_id)
        if not row or row['deleted_at']:
            raise ApiError(404,'DISCOVERY_SOURCE_NOT_FOUND','关键词来源不存在')
        runs=many(conn,"""SELECT * FROM discovery_runs WHERE workspace_id=:ws AND source_id=:id
            ORDER BY requested_at DESC LIMIT 10""",ws=user['workspace_id'],id=source_id)
        recent=many(conn,"""SELECT external_id,decision FROM discovery_run_items WHERE workspace_id=:ws
            AND run_id IN (SELECT id FROM discovery_runs WHERE workspace_id=:ws AND source_id=:id)
            ORDER BY created_at DESC LIMIT 40""",ws=user['workspace_id'],id=source_id)
    return {**discovery_view(row),'runs':[_run_view(run) for run in runs],
            'recent_decisions':recent,
            'explanation':'每轮只读取配置的页数；命中排除词/语言不匹配/超过每轮上限都会记录原因，不会静默丢弃。'}


@app.patch(PREFIX+'/discovery-sources/{source_id}')
def update_discovery_source(source_id:uuid.UUID,body:DiscoverySourceInput,user:User,
                            if_match:Annotated[str|None,Header()]=None):
    with engine.begin() as conn:
        row=_discovery_row(conn,user['workspace_id'],source_id,lock=True)
        if not row or row['deleted_at']:
            raise ApiError(404,'DISCOVERY_SOURCE_NOT_FOUND','关键词来源不存在')
        revision=require_revision(if_match,'关键词来源')
        if row['revision']!=revision:
            raise ApiError(409,'VERSION_CONFLICT','关键词来源已变化，请刷新后重试',True)
        merged={key:row[key] for key in ('name','platform','keywords','exclude_terms','languages',
            'match_mode','language_policy','page_policy','target_pages','max_results_per_run',
            'max_pages_per_run','max_requests_per_run','rescan_after_hours',
            'schedule_interval_minutes','enabled','network_authorized')}
        merged.update({key:value for key,value in body.model_dump(exclude_unset=True).items()
                       if value is not None})
        try:
            config=normalize_config(merged)
        except DiscoveryConfigError as exc:
            raise discovery_config_error(exc)
        conflict=one(conn,"""SELECT id FROM discovery_sources WHERE workspace_id=:ws AND name=:name
            AND id<>:id""",ws=user['workspace_id'],name=config['name'],id=source_id)
        if conflict:
            raise ApiError(409,'DISCOVERY_NAME_CONFLICT','已有同名的关键词来源',False)
        row=one(conn,"""UPDATE discovery_sources SET name=:name,keywords=CAST(:keywords AS jsonb),
            exclude_terms=CAST(:terms AS jsonb),languages=CAST(:languages AS jsonb),
            match_mode=:match_mode,language_policy=:language_policy,page_policy=:page_policy,
            target_pages=CAST(:pages AS jsonb),max_results_per_run=:max_results,
            max_pages_per_run=:max_pages,max_requests_per_run=:max_requests,
            rescan_after_hours=:rescan,schedule_interval_minutes=:interval,enabled=:enabled,
            network_authorized=:authorized,
            next_run_at=CASE WHEN NOT :enabled OR NOT :authorized THEN NULL
                WHEN next_run_at IS NULL THEN now() ELSE next_run_at END,
            updated_at=now(),revision=revision+1 WHERE id=:id AND workspace_id=:ws RETURNING *""",
            name=config['name'],keywords=js(config['keywords']),terms=js(config['exclude_terms']),
            languages=js(config['languages']),match_mode=config['match_mode'],
            language_policy=config['language_policy'],page_policy=config['page_policy'],
            pages=js(config['target_pages']),max_results=config['max_results_per_run'],
            max_pages=config['max_pages_per_run'],max_requests=config['max_requests_per_run'],
            rescan=config['rescan_after_hours'],interval=config['schedule_interval_minutes'],
            enabled=config['enabled'],authorized=config['network_authorized'],
            id=source_id,ws=user['workspace_id'])
        execute(conn,"INSERT INTO audit_events(workspace_id,action,object_id) VALUES(:ws,'discovery_source_updated',:id)",
                ws=user['workspace_id'],id=source_id)
    return discovery_view(row)


@app.delete(PREFIX+'/discovery-sources/{source_id}')
def delete_discovery_source(source_id:uuid.UUID,user:User,
                            if_match:Annotated[str|None,Header()]=None):
    with engine.begin() as conn:
        row=_discovery_row(conn,user['workspace_id'],source_id,lock=True)
        if not row:
            raise ApiError(404,'DISCOVERY_SOURCE_NOT_FOUND','关键词来源不存在')
        if row['deleted_at']:
            return {'ok':True,'already_deleted':True,'message':'关键词来源已删除'}
        revision=require_revision(if_match,'关键词来源')
        if row['revision']!=revision:
            raise ApiError(409,'VERSION_CONFLICT','关键词来源已变化，请刷新后重试',True)
        execute(conn,"""UPDATE discovery_sources SET deleted_at=now(),enabled=false,next_run_at=NULL,
            updated_at=now(),revision=revision+1 WHERE id=:id AND workspace_id=:ws""",
                id=source_id,ws=user['workspace_id'])
        execute(conn,"INSERT INTO audit_events(workspace_id,action,object_id) VALUES(:ws,'discovery_source_deleted',:id)",
                ws=user['workspace_id'],id=source_id)
    return {'ok':True,'message':'关键词来源已删除；已采集的来源与历史运行记录保留，不会被物理删除。'}


@app.post(PREFIX+'/discovery-sources/{source_id}/run',status_code=202)
def run_discovery_source(source_id:uuid.UUID,user:User):
    with engine.begin() as conn:
        row=_discovery_row(conn,user['workspace_id'],source_id,lock=True)
        if not row or row['deleted_at']:
            raise ApiError(404,'DISCOVERY_SOURCE_NOT_FOUND','关键词来源不存在')
        if not row['network_authorized']:
            raise ApiError(422,'NETWORK_NOT_AUTHORIZED',
                           '该关键词来源尚未在本机授权联网，未发起任何请求。请先勾选联网授权并保存。',False)
        capability=get_collector(row['platform']).capabilities()
        if not capability.keyword_discovery:
            raise ApiError(422,'DISCOVERY_NOT_IMPLEMENTED','该平台尚未实现关键词发现',False)
        active=one(conn,"""SELECT id FROM discovery_runs WHERE workspace_id=:ws AND source_id=:id
            AND state IN ('queued','running') LIMIT 1""",ws=user['workspace_id'],id=source_id)
        if active:
            raise ApiError(409,'DISCOVERY_RUN_ACTIVE','该来源已有一轮发现正在排队或运行',True)
        latest=one(conn,"""SELECT requested_at FROM discovery_runs WHERE workspace_id=:ws AND source_id=:id
            ORDER BY requested_at DESC LIMIT 1""",ws=user['workspace_id'],id=source_id)
        now=datetime.now(timezone.utc)
        if latest and (now-latest['requested_at']).total_seconds()<MANUAL_RUN_COOLDOWN_SECONDS:
            remaining=MANUAL_RUN_COOLDOWN_SECONDS-int((now-latest['requested_at']).total_seconds())
            raise ApiError(429,'RATE_LIMITED','手动运行过于频繁，请稍后再试',True,
                           {'retry_after_seconds':max(1,remaining)})
        run=one(conn,"""INSERT INTO discovery_runs(workspace_id,source_id,trigger,state,request_budget)
            VALUES(:ws,:id,'manual','queued',:budget) RETURNING *""",
                ws=user['workspace_id'],id=source_id,budget=row['max_requests_per_run'])
        job=one(conn,"""INSERT INTO jobs(workspace_id,kind,input,config_snapshot,idempotency_key)
            VALUES(:ws,'discover_keywords',CAST(:input AS jsonb),CAST(:snapshot AS jsonb),:key)
            RETURNING id,state""",ws=user['workspace_id'],
            input=js({'discovery_source_id':str(source_id),'run_id':str(run['id']),'trigger':'manual'}),
            snapshot=js({'provider':'none','data_policy':'local_only','collector':row['platform'],
                         'network':'real','ai_analysis':False,'keyword_discovery':True}),
            key=f'discover:{source_id}:manual:{uuid.uuid4().hex[:12]}')
        execute(conn,"UPDATE discovery_runs SET job_id=:jid WHERE id=:rid",jid=job['id'],rid=run['id'])
        execute(conn,"INSERT INTO audit_events(workspace_id,action,object_id) VALUES(:ws,'discovery_run_requested',:id)",
                ws=user['workspace_id'],id=source_id)
    return {'run_id':str(run['id']),'job_id':str(job['id']),'state':'queued',
            'note':'已排队一次关键词发现；真实网络请求只在 worker 内低频执行，结果会记录每个候选的处理决定。'}


def _run_view(row):
    return {'id':str(row['id']),'source_id':str(row['source_id']),'trigger':row['trigger'],
            'state':row['state'],'requested_at':row['requested_at'],
            'started_at':row['started_at'],'finished_at':row['finished_at'],
            'requests_used':row['requests_used'],'request_budget':row['request_budget'],
            'pages_visited':row['pages_visited'],'discovered':row['discovered'],
            'matched':row['matched'],'excluded':row['excluded'],
            'duplicates':row['duplicates'],'enqueued':row['enqueued'],
            'error_code':row['error_code'],'detail':row['detail'],'job_id':
            str(row['job_id']) if row['job_id'] else None}


@app.get(PREFIX+'/discovery-sources/{source_id}/runs')
def discovery_runs(source_id:uuid.UUID,user:User,limit:int=20,cursor:str|None=None):
    limit=page_limit(limit)
    cur=decode_cursor(cursor,'discovery-runs',{'t':'ts','i':'uuid'})
    with engine.connect() as conn:
        if not _discovery_row(conn,user['workspace_id'],source_id):
            raise ApiError(404,'DISCOVERY_SOURCE_NOT_FOUND','关键词来源不存在')
        rows=many(conn,"""SELECT * FROM discovery_runs WHERE workspace_id=:ws AND source_id=:id
            AND (CAST(:cur AS timestamptz) IS NULL OR (requested_at,id)<(CAST(:cur AS timestamptz),CAST(:cur_id AS uuid)))
            ORDER BY requested_at DESC,id DESC LIMIT :take""",
                ws=user['workspace_id'],id=source_id,cur=cur.get('t'),cur_id=cur.get('i'),take=limit+1)
    nxt=None
    if len(rows)>limit:
        nxt=encode_cursor({'k':'discovery-runs','t':rows[limit-1]['requested_at'].isoformat(),
                           'i':str(rows[limit-1]['id'])})
    return {'items':[_run_view(row) for row in rows[:limit]],'next_cursor':nxt}


@app.get(PREFIX+'/discovery-runs/{run_id}')
def discovery_run_detail(run_id:uuid.UUID,user:User):
    with engine.connect() as conn:
        run=one(conn,'SELECT * FROM discovery_runs WHERE workspace_id=:ws AND id=:id',
                ws=user['workspace_id'],id=run_id)
        if not run:
            raise ApiError(404,'DISCOVERY_RUN_NOT_FOUND','运行记录不存在')
        items=many(conn,"""SELECT external_id,bvid,page,title,author_name,published_at,decision,
            reason,source_id,created_at FROM discovery_run_items WHERE workspace_id=:ws AND run_id=:id
            ORDER BY created_at,external_id LIMIT 200""",ws=user['workspace_id'],id=run_id)
    return {**_run_view(run),'items':items,
            'items_retention_days':discovery_item_retention_days(),
            'note':('decision 说明每个候选的去向：enqueued/refreshed 进入采集；keyword_miss/excluded_term/over_limit/duplicate/skipped_deleted 均保留原因。'
                    '候选明细按保留期清理，运行计数与来源水位不删除。')}


MAX_PART_BATCH = 20


class PartBatchInput(BaseModel):
    page_nos:list[int]=Field(default_factory=list)
    confirmed:bool=False


def _part_target(conn,ws,parent,page_no,part):
    """Create or reuse the source row for one part of an already-known video."""
    metadata=parent['metadata'] or {}
    parts=metadata.get('parts') or []
    bvid=str(metadata.get('bvid') or str(parent['external_id']).split(':')[0])
    external_id=f'{bvid}:p{page_no}'
    canonical=f'https://www.bilibili.com/video/{bvid}'+('' if page_no==1 else f'?p={page_no}')
    target=one(conn,'SELECT * FROM sources WHERE workspace_id=:ws AND platform=:p AND external_id=:e',
               ws=ws,p='bilibili',e=external_id)
    created=False
    if not target:
        target=one(conn,'''INSERT INTO sources(workspace_id,platform,external_id,source_kind,title,
            canonical_url,availability,content_status,rights_policy,metadata)
            VALUES(:ws,'bilibili',:e,'video',:title,:url,'unknown','pending',CAST(:rights AS jsonb),
            CAST(:meta AS jsonb)) RETURNING *''',
            ws=ws,e=external_id,
            title=(part.get('part_title') or parent['title'])[:200],url=canonical,
            rights=js({'schema_version':'rights.v1','user_authorized':True,
                       'basis':'user_provided_link','cloud_allowed':False,'retention_days':30}),
            meta=js({'schema_version':'source.v1','platform':'bilibili','bvid':bvid,
                     'page':page_no,'page_count':len(parts),'part_title':part.get('part_title'),
                     'subtitle_status':'unknown','collector':'bilibili.v2',
                     'collector_schema':'collector.v1'}))
        created=True
    return target,created,canonical,external_id


def _queue_part_collect(conn,ws,target,canonical,page_no,external_id):
    job=active_collect_job(conn,ws,target['id'])
    if not job:
        job=one(conn,'''INSERT INTO jobs(workspace_id,kind,input,config_snapshot,idempotency_key)
            VALUES(:ws,'collect_source',CAST(:input AS jsonb),CAST(:snapshot AS jsonb),:key)
            RETURNING id,state''',
            ws=ws,
            input=js({'source_id':str(target['id']),'url':canonical,'page':page_no,
                      'platform':'bilibili','external_id':external_id}),
            snapshot=js({'provider':'none','data_policy':'local_only',
                         'policy_version':POLICY_VERSION,'collector':'bilibili.v2',
                         'network':'real','ai_analysis':False}),
            key=f'collect:{target["id"]}:r{target["revision"]}:{uuid.uuid4().hex[:8]}')
    return job


@app.post(PREFIX+'/sources/{source_id}/parts/{page_no}/import',status_code=202)
def import_source_part(source_id:uuid.UUID,page_no:int,user:User):
    """Locate one part of an already-known video and queue it, without batch fetching."""
    if not 1<=page_no<=999:
        raise ApiError(422,'INVALID_LOCATOR','分P序号必须是 1 到 999 的整数',False)
    with engine.begin() as conn:
        parent=one(conn,'SELECT * FROM sources WHERE workspace_id=:ws AND id=:id FOR UPDATE',
                   ws=user['workspace_id'],id=source_id)
        if not parent:
            raise ApiError(404,'SOURCE_NOT_FOUND','来源不存在')
        if parent['availability']=='deleted':
            raise ApiError(409,'SOURCE_DELETED','来源已删除，不能采集其分P',False)
        metadata=parent['metadata'] or {}
        parts=metadata.get('parts') or []
        part=next((item for item in parts if int(item.get('page_no') or 0)==page_no),None)
        if part is None:
            raise ApiError(404,'SOURCE_PART_NOT_FOUND',
                           '该来源的元数据里没有这个分P；请重新采集元数据后再试',False,
                           {'available_pages':[int(item.get('page_no') or 0) for item in parts]})
        target,created,canonical,external_id=_part_target(conn,user['workspace_id'],parent,page_no,part)
        job=_queue_part_collect(conn,user['workspace_id'],target,canonical,page_no,external_id)
        execute(conn,"INSERT INTO audit_events(workspace_id,action,object_id) VALUES(:ws,'source_part_import',:id)",
                ws=user['workspace_id'],id=target['id'])
    return {'ok':True,'source_id':str(target['id']),'job_id':str(job['id']),'state':job['state'],
            'created':created,'page':page_no,
            'note':'只排队这一个分P；没有批量抓取其它分P。'}


@app.post(PREFIX+'/sources/{source_id}/parts/import',status_code=202)
def import_source_parts(source_id:uuid.UUID,body:PartBatchInput,user:User):
    """Queue an explicitly confirmed, bounded batch of parts of one known video.

    This is a real networked operation: each selected part becomes its own
    collection job, so the caller must include ``confirmed: true`` after showing
    the user how many jobs and upstream calls will be queued. The batch is capped
    and is all-or-nothing: an unknown part rejects the whole request rather than
    silently skipping it.
    """
    if not body.confirmed:
        raise ApiError(422,'PART_BATCH_CONFIRM_REQUIRED',
                       '批量导入分P会排队多次真实上游请求；请在确认对话框中明确确认',False,
                       {'max_parts':MAX_PART_BATCH})
    requested=[]
    for raw in body.page_nos:
        if isinstance(raw,bool) or not isinstance(raw,int) or not 1<=raw<=999:
            raise ApiError(422,'INVALID_LOCATOR','分P序号必须是 1 到 999 的整数',False)
        if raw not in requested:
            requested.append(raw)
    if not requested:
        raise ApiError(422,'PART_BATCH_EMPTY','请至少选择一个分P',False)
    if len(requested)>MAX_PART_BATCH:
        raise ApiError(422,'PART_BATCH_TOO_LARGE',
                       f'一次最多批量导入 {MAX_PART_BATCH} 个分P',False,
                       {'max_parts':MAX_PART_BATCH,'requested':len(requested)})
    with engine.begin() as conn:
        parent=one(conn,'SELECT * FROM sources WHERE workspace_id=:ws AND id=:id FOR UPDATE',
                   ws=user['workspace_id'],id=source_id)
        if not parent:
            raise ApiError(404,'SOURCE_NOT_FOUND','来源不存在')
        if parent['availability']=='deleted':
            raise ApiError(409,'SOURCE_DELETED','来源已删除，不能采集其分P',False)
        metadata=parent['metadata'] or {}
        parts=metadata.get('parts') or []
        by_page={int(item.get('page_no') or 0):item for item in parts}
        missing=[page for page in requested if page not in by_page]
        if missing:
            raise ApiError(404,'SOURCE_PART_NOT_FOUND',
                           '所选分P中有不存在的序号；请重新采集元数据后再试',False,
                           {'missing_pages':missing,
                            'available_pages':sorted(by_page)})
        queued=[]
        for page_no in sorted(requested):
            target,created,canonical,external_id=_part_target(
                conn,user['workspace_id'],parent,page_no,by_page[page_no])
            job=_queue_part_collect(conn,user['workspace_id'],target,canonical,page_no,external_id)
            queued.append({'page':page_no,'source_id':str(target['id']),
                           'job_id':str(job['id']),'state':job['state'],'created':created})
        execute(conn,"""INSERT INTO audit_events(workspace_id,action,object_id,details)
            VALUES(:ws,'source_parts_batch_import',:id,CAST(:d AS jsonb))""",
                ws=user['workspace_id'],id=source_id,
                d=js({'requested':len(requested),'queued':len(queued),
                      'pages':sorted(requested)}))
    return {'ok':True,'source_id':str(source_id),'queued':len(queued),'items':queued,
            'estimated_upstream_requests':len(queued)*3,
            'confirmation':'用户已确认批量排队；每个分P是独立采集任务，仍受单任务 4 次请求上限约束。',
            'note':('只排队所选分P，其他分P不会被自动抓取；未获取任何视频文件，也未做转写或 AI 分析。'
                    '任务由 worker 低频串行执行，可在“任务与费用”页取消。')}


def invalidate_source(conn,ws,sid,reason):
    execute(conn,"UPDATE claims SET verification_validity='stale' WHERE workspace_id=:ws AND origin_version_id IN (SELECT id FROM source_versions WHERE source_id=:sid)",ws=ws,sid=sid)
    execute(conn,"""UPDATE updates SET state='stale',revision=revision+1,last_changed_at=now() WHERE workspace_id=:ws AND id IN
        (SELECT r.from_id FROM relations r JOIN claims c ON c.id=r.to_id JOIN source_versions v ON v.id=c.origin_version_id WHERE r.relation_type='includes' AND v.source_id=:sid)""",ws=ws,sid=sid)
    execute(conn,'INSERT INTO audit_events(workspace_id,action,object_id) VALUES(:ws,:action,:sid)',ws=ws,action=reason,sid=sid)


@app.delete(PREFIX+'/sources/{source_id}')
def remove_source(source_id:uuid.UUID,user:User,if_match:Annotated[str|None,Header()]=None):
    with engine.begin() as conn:
        source=one(conn,'SELECT * FROM sources WHERE id=:id AND workspace_id=:ws FOR UPDATE',id=source_id,ws=user['workspace_id'])
        if not source: raise ApiError(404,'SOURCE_NOT_FOUND','来源不存在')
        if source['availability']=='deleted':
            return {'ok':True,'already_deleted':True,'message':'内容已停止展示；再次删除不会改变数据'}
        revision=require_revision(if_match,'来源')
        if source['revision']!=revision: raise ApiError(409,'VERSION_CONFLICT','来源已变化，请刷新后重试',True)
        execute(conn,"UPDATE sources SET availability='deleted',revision=revision+1 WHERE id=:id",id=source_id)
        invalidate_source(conn,user['workspace_id'],source_id,'source_removed')
    return {'ok':True,'message':'内容已停止展示；物理清理能力尚待后续迁移交付'}


@app.get(PREFIX+'/sources')
def sources(user:User,q:str='',limit:int=20,cursor:str|None=None):
    limit=page_limit(limit)
    cur=decode_cursor(cursor,'sources',{'c':'ts','i':'uuid'})
    with engine.connect() as conn:
        rows=many(conn,"""SELECT * FROM sources WHERE workspace_id=:ws AND availability<>'deleted' AND title ILIKE :q
          AND (CAST(:cur_ts AS timestamptz) IS NULL OR (created_at,id)<(CAST(:cur_ts AS timestamptz),CAST(:cur_id AS uuid)))
          ORDER BY created_at DESC,id DESC LIMIT :take""",ws=user['workspace_id'],q=f'%{q}%',cur_ts=cur.get('c'),cur_id=cur.get('i'),take=limit+1)
    nxt=None
    if len(rows)>limit:
        nxt=encode_cursor({'k':'sources','c':rows[limit-1]['created_at'].isoformat(),'i':str(rows[limit-1]['id'])})
    return {'items':rows[:limit],'next_cursor':nxt}


@app.get(PREFIX+'/sources/{source_id}')
def source_detail(source_id:uuid.UUID,user:User):
    with engine.connect() as conn:
        item=one(conn,"SELECT * FROM sources WHERE id=:id AND workspace_id=:ws AND availability<>'deleted'",id=source_id,ws=user['workspace_id'])
        if not item: raise HTTPException(404,'来源不可用')
        item['versions']=many(conn,'SELECT id,version_no,created_at,raw_hash,coverage FROM source_versions WHERE source_id=:id ORDER BY version_no DESC',id=source_id)
        item['claims']=many(conn,'SELECT * FROM claims WHERE origin_version_id IN (SELECT id FROM source_versions WHERE source_id=:id)',id=source_id)
        return item


@app.get(PREFIX+'/source-versions/{version_id}/segments')
def segments(version_id:uuid.UUID,user:User,limit:int=20,cursor:str|None=None):
    limit=page_limit(limit)
    cur=decode_cursor(cursor,'segments',{'o':'int'})
    offset=cur.get('o',0)
    with engine.connect() as conn:
        row=one(conn,"SELECT v.segments FROM source_versions v JOIN sources s ON s.id=v.source_id WHERE v.id=:id AND v.workspace_id=:ws AND s.availability='available' AND v.retention_expires_at>now()",id=version_id,ws=user['workspace_id'])
        if not row: raise ApiError(404,'EVIDENCE_UNAVAILABLE','证据不可用或保留期已到')
        items=row['segments'][offset:offset+limit]
    nxt=None
    if len(row['segments'])>offset+limit:
        nxt=encode_cursor({'k':'segments','o':str(offset+limit)})
    return {'items':items,'next_cursor':nxt}


def evidence(conn,rid,ws):
    row=one(conn,"""SELECT r.*,v.segments,v.retention_expires_at,s.title,s.author_name,s.canonical_url,s.platform,s.metadata,s.id AS source_id
        FROM relations r JOIN source_versions v ON v.id=r.to_id JOIN sources s ON s.id=v.source_id
        WHERE r.id=:id AND r.workspace_id=:ws AND r.relation_type='evidenced_by' AND r.status='accepted'
        AND s.availability='available' AND v.retention_expires_at>now()""",id=rid,ws=ws)
    if not row: return None
    validate_anchor(row['segments'],row['payload'])
    row.pop('segments')
    return row


@app.get(PREFIX+'/evidence/{relation_id}')
def get_evidence(relation_id:uuid.UUID,user:User):
    with engine.connect() as conn:
        row=evidence(conn,relation_id,user['workspace_id'])
        if not row: raise HTTPException(404,'证据不可用')
        return row


def visible_update(conn,row,ws,surface='feed'):
    if row['state']!='published': return None
    refs=[]
    for sentence in row['structured_body']:
        claim=one(conn,'SELECT * FROM claims WHERE id=:id AND workspace_id=:ws',id=sentence['claim_id'],ws=ws)
        if not claim: return None
        ev=evidence(conn,sentence['relation_id'],ws)
        record=one(conn,'SELECT * FROM verification_records WHERE id=:id AND workspace_id=:ws',id=claim['current_verification_id'],ws=ws) if claim['current_verification_id'] else None
        decision=PublicationPolicy.evaluate(surface,claim,record,bool(ev))
        if decision['action']!='allow' or not mode_is_within(sentence.get('publication_mode'),decision['publication_mode']): return None
        if surface in ('feed','detail','title'):
            # Titles and list snippets follow the same threshold as the body; a weaker
            # stored mode is still allowed, a stronger one never is.
            title_decision=PublicationPolicy.evaluate('title',claim,record,bool(ev))
            if title_decision['action']!='allow' or not mode_is_within(sentence.get('publication_mode'),title_decision['publication_mode']): return None
        refs.append(ev)
    return {**row,'evidence':refs,'independence':'unknown','independent_groups':0}


@app.get(PREFIX+'/updates')
def updates(user:User,q:str='',bookmarked:bool=False,unread:bool=False,entity_id:uuid.UUID|None=None,
            topic_id:uuid.UUID|None=None,since:str|None=None,until:str|None=None,update_type:str|None=None,
            limit:int=20,cursor:str|None=None):
    limit=page_limit(limit)
    since_value=parse_time(since,'since')
    until_value=parse_time(until,'until')
    if since_value and until_value and datetime.fromisoformat(since_value)>datetime.fromisoformat(until_value):
        raise ApiError(400,'INVALID_TIME_RANGE','since 不能晚于 until')
    update_type=(update_type or '').strip() or None
    cur=decode_cursor(cursor,'updates',{'p':'ts','i':'uuid'})
    with engine.connect() as conn:
        rows=many(conn,"""SELECT u.*,coalesce(s.bookmarked,false) AS bookmarked,coalesce(s.read_revision,0) AS read_revision FROM updates u
         LEFT JOIN user_item_states s ON s.update_id=u.id AND s.user_id=:uid
         WHERE u.workspace_id=:ws AND u.state='published' AND u.title ILIKE :q
         AND (NOT :bookmarked OR s.bookmarked=true) AND (NOT :unread OR coalesce(s.read_revision,0)<u.revision)
         AND (CAST(:node AS uuid) IS NULL OR EXISTS(SELECT 1 FROM relations r WHERE r.from_id=u.id AND r.to_id=CAST(:node AS uuid) AND r.relation_type='about'))
         AND (CAST(:topic AS uuid) IS NULL OR EXISTS(
              SELECT 1 FROM relations r WHERE r.from_id=u.id AND r.relation_type='about' AND (
                r.to_id=CAST(:topic AS uuid) OR EXISTS(
                  SELECT 1 FROM relations m WHERE m.from_id=r.to_id AND m.to_id=CAST(:topic AS uuid)
                  AND m.relation_type='member_of' AND m.status='accepted'))))
         AND (CAST(:since_ts AS timestamptz) IS NULL OR u.first_published_at>=CAST(:since_ts AS timestamptz))
         AND (CAST(:until_ts AS timestamptz) IS NULL OR u.first_published_at<CAST(:until_ts AS timestamptz))
         AND (CAST(:update_type AS text) IS NULL OR u.update_type=CAST(:update_type AS text))
         AND (CAST(:cur_p AS timestamptz) IS NULL OR (u.first_published_at,u.id)<(CAST(:cur_p AS timestamptz),CAST(:cur_i AS uuid)))
         ORDER BY u.first_published_at DESC,u.id DESC LIMIT :take""",ws=user['workspace_id'],uid=user['id'],q=f'%{q}%',bookmarked=bookmarked,unread=unread,node=str(entity_id) if entity_id else None,topic=str(topic_id) if topic_id else None,since_ts=since_value,until_ts=until_value,update_type=update_type,cur_p=cur.get('p'),cur_i=cur.get('i'),take=limit+1)
        items=[r for row in rows[:limit] if (r:=visible_update(conn,row,user['workspace_id']))]
    nxt=None
    if len(rows)>limit:
        last=rows[limit-1]
        nxt=encode_cursor({'k':'updates','p':last['first_published_at'].isoformat(),'i':str(last['id'])})
    return {'items':items,'next_cursor':nxt}


@app.get(PREFIX+'/updates/{update_id}')
def update_detail(update_id:uuid.UUID,user:User):
    with engine.connect() as conn:
        row=one(conn,'SELECT * FROM updates WHERE id=:id AND workspace_id=:ws',id=update_id,ws=user['workspace_id'])
        result=visible_update(conn,row,user['workspace_id'],'detail') if row else None
        if not result: raise HTTPException(404,'动态不可发布或证据已失效')
        return result


class ItemState(BaseModel):
    bookmarked:bool|None=None
    read_revision:int|None=Field(default=None,ge=0)


@app.put(PREFIX+'/updates/{update_id}/state')
def state_update(update_id:uuid.UUID,body:ItemState,user:User):
    with engine.begin() as conn:
        update=one(conn,'SELECT revision FROM updates WHERE id=:id AND workspace_id=:ws',id=update_id,ws=user['workspace_id'])
        if not update: raise HTTPException(404,'动态不存在')
        if body.read_revision is not None and body.read_revision>update['revision']: raise HTTPException(422,'已读版本不存在')
        execute(conn,'''INSERT INTO user_item_states(workspace_id,user_id,update_id,bookmarked,read_revision)
          VALUES(:ws,:uid,:id,coalesce(:bookmark,false),coalesce(:read,0)) ON CONFLICT(user_id,update_id)
          DO UPDATE SET bookmarked=coalesce(:bookmark,user_item_states.bookmarked),read_revision=coalesce(:read,user_item_states.read_revision)''',
          ws=user['workspace_id'],uid=user['id'],id=update_id,bookmark=body.bookmarked,read=body.read_revision)
    return {'ok':True}


@app.get(PREFIX+'/nodes')
def nodes(user:User,kind:Literal['entity','topic']='entity',q:str='',limit:int=20,cursor:str|None=None):
    limit=page_limit(limit)
    cur=decode_cursor(cursor,'nodes',{'n':'str','i':'uuid'})
    with engine.connect() as conn:
        rows=many(conn,'''SELECT n.*,EXISTS(SELECT 1 FROM subscriptions s WHERE s.node_id=n.id AND s.user_id=:uid) AS followed
            FROM nodes n WHERE n.workspace_id=:ws AND n.node_kind=:kind AND n.canonical_name ILIKE :q
            AND (CAST(:cur_n AS text) IS NULL OR (n.canonical_name,n.id)>(CAST(:cur_n AS text),CAST(:cur_i AS uuid)))
            ORDER BY n.canonical_name,n.id LIMIT :take''',ws=user['workspace_id'],uid=user['id'],kind=kind,q=f'%{q}%',cur_n=cur.get('n'),cur_i=cur.get('i'),take=limit+1)
    nxt=None
    if len(rows)>limit:
        nxt=encode_cursor({'k':'nodes','n':rows[limit-1]['canonical_name'],'i':str(rows[limit-1]['id'])})
    return {'items':rows[:limit],'next_cursor':nxt}


class NodeCreate(BaseModel):
    kind: Literal['entity','topic']
    name: str = Field(min_length=1,max_length=200)
    entity_type: str|None = Field(default=None,max_length=50)
    description: str|None = Field(default=None,max_length=2000)
    aliases: list[str] = Field(default_factory=list)


class NodePatch(BaseModel):
    name: str|None = Field(default=None,min_length=1,max_length=200)
    description: str|None = Field(default=None,max_length=2000)
    aliases: list[str]|None = None


class TopicLink(BaseModel):
    child_id: uuid.UUID
    parent_id: uuid.UUID


def clean_aliases(values):
    aliases=[str(value).strip() for value in (values or []) if str(value).strip()]
    if len(aliases)>20 or any(len(value)>100 for value in aliases):
        raise ApiError(422,'INVALID_ALIASES','别名最多20个，每个不超过100个字符')
    return aliases


@app.post(PREFIX+'/nodes',status_code=201)
def create_node(body:NodeCreate,user:User):
    name=body.name.strip()
    if not name:
        raise ApiError(422,'INVALID_NODE_NAME','名称不能为空')
    aliases=clean_aliases(body.aliases)
    entity_type=body.entity_type if body.kind=='entity' else None
    with engine.begin() as conn:
        row=one(conn,'''INSERT INTO nodes(workspace_id,node_kind,canonical_name,entity_type,aliases,description,status,metadata)
            VALUES(:ws,:kind,:name,:etype,CAST(:aliases AS jsonb),:description,'active',CAST(:metadata AS jsonb)) RETURNING *''',
            ws=user['workspace_id'],kind=body.kind,name=name,etype=entity_type,aliases=js(aliases),
            description=(body.description or '').strip() or None,
            metadata=js({'schema_version':'node.v1','created_by':'user'}))
    return row


@app.patch(PREFIX+'/nodes/{node_id}')
def patch_node(node_id:uuid.UUID,body:NodePatch,user:User,if_match:Annotated[str|None,Header()]=None):
    revision=require_revision(if_match,'对象')
    sets=[]; params={'id':node_id,'ws':user['workspace_id'],'rev':revision}
    if body.name is not None:
        name=body.name.strip()
        if not name: raise ApiError(422,'INVALID_NODE_NAME','名称不能为空')
        sets.append('canonical_name=:name'); params['name']=name
    if body.description is not None:
        sets.append('description=:description'); params['description']=body.description.strip() or None
    if body.aliases is not None:
        sets.append('aliases=CAST(:aliases AS jsonb)'); params['aliases']=js(clean_aliases(body.aliases))
    if not sets:
        raise ApiError(400,'NO_CHANGES','没有可更新的字段')
    with engine.begin() as conn:
        row=one(conn,'UPDATE nodes SET '+', '.join(sets)+',revision=revision+1 WHERE id=:id AND workspace_id=:ws AND revision=:rev RETURNING *',**params)
        if not row: raise ApiError(409,'VERSION_CONFLICT','对象已改变，请刷新后重试',True)
    return row


@app.post(PREFIX+'/topic-links',status_code=201)
def create_topic_link(body:TopicLink,user:User):
    ws=user['workspace_id']
    try:
        with engine.begin() as conn:
            execute(conn,'SELECT pg_advisory_xact_lock(hashtextextended(CAST(:ws AS text),0))',ws=str(ws))
            child=one(conn,'SELECT * FROM nodes WHERE id=:id AND workspace_id=:ws',id=body.child_id,ws=ws)
            parent=one(conn,'SELECT * FROM nodes WHERE id=:id AND workspace_id=:ws',id=body.parent_id,ws=ws)
            if not child or not parent:
                raise ApiError(404,'NODE_NOT_FOUND','主题或实体不存在')
            if parent['node_kind']!='topic':
                raise ApiError(422,'PARENT_NOT_TOPIC','父节点必须是主题')
            if child['node_kind']=='topic':
                relation_type='subtopic_of'
                cycle=one(conn,"""WITH RECURSIVE reach(id) AS (
                    SELECT CAST(:parent AS uuid)
                    UNION
                    SELECT r.to_id FROM relations r JOIN reach x ON r.from_id=x.id
                    WHERE r.workspace_id=:ws AND r.relation_type='subtopic_of' AND r.status='accepted')
                    SELECT 1 AS hit FROM reach WHERE id=CAST(:child AS uuid)""",child=str(child['id']),parent=str(parent['id']),ws=ws)
                if cycle:
                    raise ApiError(409,'TOPIC_CYCLE','该关系会形成主题环，已拒绝')
            elif child['node_kind']=='entity':
                relation_type='member_of'
            else:
                raise ApiError(422,'INVALID_CHILD_KIND','子节点必须是主题或实体')
            if str(child['id'])==str(parent['id']):
                raise ApiError(409,'TOPIC_CYCLE','节点不能以自身为父主题')
            key=digest(js([str(child['id']),relation_type,str(parent['id']),{}]))
            row=one(conn,'''INSERT INTO relations(workspace_id,from_type,from_id,relation_type,to_type,to_id,payload,status,asserted_by,dedup_key)
                VALUES(:ws,'node',:fi,:kind,'node',:ti,CAST(:payload AS jsonb),'accepted','user',:key)
                ON CONFLICT(workspace_id,dedup_key) DO UPDATE SET status='accepted' RETURNING id''',
                ws=ws,fi=child['id'],kind=relation_type,ti=parent['id'],payload=js({'created_by':'user'}),key=key)
    except DBAPIError as exc:
        detail=str(exc)
        if 'relation cycle' in detail:
            raise ApiError(409,'TOPIC_CYCLE','该关系会形成主题环，已拒绝')
        if 'invalid node subtype' in detail or 'invalid relation matrix' in detail:
            raise ApiError(422,'INVALID_TOPIC_LINK','主题关系不符合类型约束')
        raise
    return {'ok':True,'relation_id':str(row['id'])}


@app.delete(PREFIX+'/topic-links/{relation_id}')
def delete_topic_link(relation_id:uuid.UUID,user:User):
    with engine.begin() as conn:
        row=one(conn,'''DELETE FROM relations WHERE id=:id AND workspace_id=:ws
            AND relation_type IN ('subtopic_of','member_of') RETURNING id''',id=relation_id,ws=user['workspace_id'])
        if not row: raise ApiError(404,'TOPIC_LINK_NOT_FOUND','主题关系不存在')
        execute(conn,'INSERT INTO audit_events(workspace_id,action,object_id) VALUES(:ws,:action,:id)',ws=user['workspace_id'],action='topic_link_removed',id=relation_id)
    return {'ok':True}


@app.get(PREFIX+'/nodes/{node_id}')
def node_detail(node_id:uuid.UUID,user:User):
    with engine.connect() as conn:
        row=one(conn,'SELECT * FROM nodes WHERE id=:id AND workspace_id=:ws',id=node_id,ws=user['workspace_id'])
        if not row: raise HTTPException(404,'对象不存在')
        ws=user['workspace_id']
        row['claims']=many(conn,"SELECT c.* FROM claims c JOIN relations r ON r.from_id=c.id JOIN source_versions v ON v.id=c.origin_version_id JOIN sources s ON s.id=v.source_id WHERE r.to_id=:id AND r.relation_type='about' AND r.status='accepted' AND c.workspace_id=:ws AND s.availability='available' AND v.retention_expires_at>now()",id=node_id,ws=ws)
        row['parents']=many(conn,"""SELECT r.id AS relation_id,n.id,n.canonical_name,n.node_kind FROM relations r
            JOIN nodes n ON n.id=r.to_id WHERE r.from_id=:id AND r.relation_type='subtopic_of' AND r.status='accepted' AND r.workspace_id=:ws
            ORDER BY n.canonical_name""",id=node_id,ws=ws)
        row['children']=many(conn,"""SELECT r.id AS relation_id,n.id,n.canonical_name,n.node_kind FROM relations r
            JOIN nodes n ON n.id=r.from_id WHERE r.to_id=:id AND r.relation_type='subtopic_of' AND r.status='accepted' AND r.workspace_id=:ws
            ORDER BY n.canonical_name""",id=node_id,ws=ws)
        row['entities']=many(conn,"""SELECT r.id AS relation_id,n.id,n.canonical_name,n.node_kind FROM relations r
            JOIN nodes n ON n.id=r.from_id WHERE r.to_id=:id AND r.relation_type='member_of' AND r.status='accepted' AND r.workspace_id=:ws
            ORDER BY n.canonical_name""",id=node_id,ws=ws)
        row['topics']=many(conn,"""SELECT r.id AS relation_id,n.id,n.canonical_name,n.node_kind FROM relations r
            JOIN nodes n ON n.id=r.to_id WHERE r.from_id=:id AND r.relation_type='member_of' AND r.status='accepted' AND r.workspace_id=:ws
            ORDER BY n.canonical_name""",id=node_id,ws=ws)
        related=many(conn,"""SELECT DISTINCT u.* FROM updates u WHERE u.workspace_id=:ws AND u.state='published' AND (
            EXISTS(SELECT 1 FROM relations r WHERE r.from_id=u.id AND r.to_id=:id AND r.relation_type='about')
            OR EXISTS(SELECT 1 FROM relations r JOIN relations m ON m.from_id=r.to_id
                WHERE r.from_id=u.id AND r.relation_type='about' AND m.to_id=:id
                AND m.relation_type='member_of' AND m.status='accepted'))
            ORDER BY u.first_published_at DESC,u.id DESC LIMIT 10""",ws=ws,id=node_id)
        row['updates']=[u for r in related if (u:=visible_update(conn,r,ws,'detail'))]
        row['overview']='尚无通过事实发布门槛的概览。下列内容是来源主张，不能视为事实确认。'
        return row


class Subscription(BaseModel):
    enabled:bool


@app.put(PREFIX+'/subscriptions/{node_id}')
def follow(node_id:uuid.UUID,body:Subscription,user:User):
    with engine.begin() as conn:
        if not one(conn,'SELECT id FROM nodes WHERE id=:id AND workspace_id=:ws',id=node_id,ws=user['workspace_id']): raise HTTPException(404,'对象不存在')
        if body.enabled:
            execute(conn,'INSERT INTO subscriptions(workspace_id,user_id,node_id) VALUES(:ws,:uid,:id) ON CONFLICT DO NOTHING',ws=user['workspace_id'],uid=user['id'],id=node_id)
        else:
            execute(conn,'DELETE FROM subscriptions WHERE user_id=:uid AND node_id=:id',uid=user['id'],id=node_id)
    return {'ok':True}


@app.get(PREFIX+'/claims/{claim_id}')
def claim_detail(claim_id:uuid.UUID,user:User):
    with engine.connect() as conn:
        row=one(conn,"SELECT c.* FROM claims c JOIN source_versions v ON v.id=c.origin_version_id JOIN sources s ON s.id=v.source_id WHERE c.id=:id AND c.workspace_id=:ws AND s.availability='available' AND v.retention_expires_at>now()",id=claim_id,ws=user['workspace_id'])
        if not row: raise HTTPException(404,'主张不存在')
        row['verification_records']=many(conn,'SELECT * FROM verification_records WHERE claim_id=:id ORDER BY version_no DESC',id=claim_id)
        row['current_verification']=(one(conn,'SELECT * FROM verification_records WHERE id=:id AND workspace_id=:ws',
            id=row['current_verification_id'],ws=user['workspace_id']) if row['current_verification_id'] else None)
        materials=_claim_materials(conn,user['workspace_id'],claim_id)
        row['fact_evidence']=materials
        row['fact_evidence_groups']={role:[item for item in materials if item['role']==role]
                                     for role in ('support','refute','context')}
        row['pending_verification_records']=[_record_view(item,claim=row) for item in row['verification_records']
                                             if item['review_status']=='pending']
        row['expression_reviews']=many(conn,"""SELECT * FROM claim_expression_reviews WHERE claim_id=:id
            AND workspace_id=:ws ORDER BY created_at DESC,id DESC""",id=claim_id,ws=user['workspace_id'])
        row['expression_review']=row['expression_reviews'][0] if row['expression_reviews'] else None
        row['relations']=many(conn,'SELECT * FROM relations WHERE from_id=:id AND workspace_id=:ws',id=claim_id,ws=user['workspace_id'])
        row['about_nodes']=many(conn,"""SELECT r.id AS relation_id,r.status,r.asserted_by,r.payload,
            n.id AS node_id,n.canonical_name,n.node_kind FROM relations r
            JOIN nodes n ON n.id=r.to_id AND n.workspace_id=r.workspace_id
            WHERE r.from_id=:id AND r.workspace_id=:ws AND r.relation_type='about'
            ORDER BY n.canonical_name""",id=claim_id,ws=user['workspace_id'])
        return row


class RelationReview(BaseModel):
    action:Literal['accept','reject']
    reason:str|None=Field(default=None,max_length=300)


@app.post(PREFIX+'/relations/{relation_id}/review')
def review_relation(relation_id:uuid.UUID,body:RelationReview,user:User):
    """Human accept/reject for a model-proposed claim->entity link.

    This is topic assignment, not fact confirmation: accepting a link never changes
    ``fact_check_status``. Accepting also attaches the entity to any already
    published update that includes the claim, so the entity view can show it.
    """
    ws=user['workspace_id']
    with engine.begin() as conn:
        row=one(conn,'SELECT * FROM relations WHERE id=:id AND workspace_id=:ws FOR UPDATE',id=relation_id,ws=ws)
        if not row: raise ApiError(404,'RELATION_NOT_FOUND','关系不存在')
        if not (row['from_type']=='claim' and row['relation_type']=='about' and row['asserted_by']=='model'):
            raise ApiError(409,'RELATION_NOT_REVIEWABLE','只有模型提出的主张-实体候选可以人工复核',False,
                           {'from_type':row['from_type'],'relation_type':row['relation_type'],'asserted_by':row['asserted_by']})
        if row['status']!='proposed':
            raise ApiError(409,'RELATION_STATE_CONFLICT','该候选已被复核',False,{'status':row['status']})
        status='accepted' if body.action=='accept' else 'rejected'
        payload={**(row['payload'] or {}),'reviewed_by':'user','reviewed_at':datetime.now(timezone.utc).isoformat(),
                 'review_reason':(body.reason or '').strip()}
        updated=one(conn,'UPDATE relations SET status=:status,payload=CAST(:payload AS jsonb) WHERE id=:id RETURNING *',
                    status=status,payload=js(payload),id=relation_id)
        linked=0
        if status=='accepted':
            updates=many(conn,"""SELECT u.id FROM updates u
                JOIN relations inc ON inc.from_id=u.id AND inc.relation_type='includes'
                WHERE u.workspace_id=:ws AND u.state='published' AND inc.workspace_id=:ws
                  AND inc.to_id=:cid""",ws=ws,cid=row['from_id'])
            for item in updates:
                existing=one(conn,"""SELECT id FROM relations WHERE workspace_id=:ws AND from_type='update'
                    AND from_id=:uid AND relation_type='about' AND to_type='node' AND to_id=:nid""",
                             ws=ws,uid=item['id'],nid=row['to_id'])
                if existing:
                    execute(conn,"UPDATE relations SET status='accepted' WHERE id=:id",id=existing['id'])
                else:
                    key=digest(js([str(item['id']),'about',str(row['to_id']),{}]))
                    one(conn,"""INSERT INTO relations(workspace_id,from_type,from_id,relation_type,to_type,to_id,payload,status,asserted_by,dedup_key)
                        VALUES(:ws,'update',:uid,'about','node',:nid,CAST(:payload AS jsonb),'accepted','user',:key)
                        ON CONFLICT(workspace_id,dedup_key) DO UPDATE SET status='accepted' RETURNING id""",
                        ws=ws,uid=item['id'],nid=row['to_id'],payload=js({'created_by':'human','from_claim_id':str(row['from_id'])}),key=key)
                linked+=1
        execute(conn,"""INSERT INTO audit_events(workspace_id,action,object_id,details)
            VALUES(:ws,:action,:id,CAST(:details AS jsonb))""",
                ws=ws,action='relation.review',id=relation_id,
                details=js({'action':body.action,'claim_id':str(row['from_id']),'node_id':str(row['to_id']),'updates_linked':linked}))
        return {'ok':True,'status':status,'relation':updated,'updates_linked':linked}


@app.get(PREFIX+'/verification-queue')
def verification_queue(user:User,limit:int=20,cursor:str|None=None,status:str='',review:str=''):
    limit=page_limit(limit)
    cur=decode_cursor(cursor,'verification',{'c':'ts','i':'uuid'})
    status=(status or '').strip()
    if status and status not in verification_engine.STATUSES:
        raise ApiError(400,'INVALID_STATUS','status 必须是 unchecked/insufficient/corroborated/conflicted/refuted 之一')
    review=(review or '').strip()
    if review and review not in ('pending','accepted','rejected'):
        raise ApiError(400,'INVALID_REVIEW','review 必须是 pending/accepted/rejected 之一')
    with engine.connect() as conn:
        rows=many(conn,"""SELECT c.*,v.assessment AS assessment,v.assessment AS current_assessment,
          er.verdict AS expression_verdict,
          er.rationale AS expression_rationale,er.limitations AS expression_limitations,
          latest.id AS latest_record_id,latest.review_status AS latest_review_status,
          latest.version_no AS latest_record_version,latest.assessment AS latest_assessment,
          coalesce(pending.n,0) AS pending_reviews
          FROM claims c JOIN source_versions sv ON sv.id=c.origin_version_id
          JOIN sources s ON s.id=sv.source_id
          LEFT JOIN verification_records v ON v.id=c.current_verification_id
          LEFT JOIN LATERAL (
            SELECT verdict,rationale,limitations FROM claim_expression_reviews e
            WHERE e.claim_id=c.id AND e.workspace_id=c.workspace_id
            ORDER BY e.created_at DESC,e.id DESC LIMIT 1
          ) er ON true
          LEFT JOIN LATERAL (
            SELECT r2.id,r2.review_status,r2.version_no,r2.assessment FROM verification_records r2
            WHERE r2.claim_id=c.id AND r2.workspace_id=c.workspace_id
            ORDER BY r2.version_no DESC LIMIT 1
          ) latest ON true
          LEFT JOIN LATERAL (
            SELECT count(*) AS n FROM verification_records r3
            WHERE r3.claim_id=c.id AND r3.workspace_id=c.workspace_id AND r3.review_status='pending'
          ) pending ON true
          WHERE c.workspace_id=:ws AND s.availability='available' AND sv.retention_expires_at>now()
          AND (CAST(:status AS text) IS NULL OR c.fact_check_status=CAST(:status AS text))
          AND (CAST(:review AS text) IS NULL OR EXISTS(
                SELECT 1 FROM verification_records rv WHERE rv.claim_id=c.id
                AND rv.workspace_id=c.workspace_id AND rv.review_status=CAST(:review AS text)))
          AND (CAST(:cur_c AS timestamptz) IS NULL OR (c.created_at,c.id)<(CAST(:cur_c AS timestamptz),CAST(:cur_i AS uuid)))
          ORDER BY c.created_at DESC,c.id DESC LIMIT :take""",ws=user['workspace_id'],
          status=status or None,review=review or None,cur_c=cur.get('c'),cur_i=cur.get('i'),take=limit+1)
    nxt=None
    if len(rows)>limit:
        nxt=encode_cursor({'k':'verification','c':rows[limit-1]['created_at'].isoformat(),'i':str(rows[limit-1]['id'])})
    return {'items':rows[:limit],'next_cursor':nxt,'retrieval_mode':'kb_only'}


MAX_FACT_MATERIALS = 20
FACT_TYPES = ('fact_supported_by', 'fact_refuted_by', 'fact_context_by')


def _claim_materials(conn, ws, claim_id):
    """Fact evidence relations of one claim with server-computed tiers."""
    rows = many(conn, """SELECT r.id,r.relation_type,r.status,r.asserted_by,r.payload,r.created_at,
        v.id AS source_version_id,v.version_no,s.id AS source_id,s.title AS material_title,
        s.platform,s.author_name,s.canonical_url,s.availability,s.current_version_id,
        (v.retention_expires_at>now()) AS retention_ok,
        (s.current_version_id IS NOT NULL AND v.id<>s.current_version_id) AS superseded
        FROM relations r JOIN source_versions v ON v.id=r.to_id JOIN sources s ON s.id=v.source_id
        WHERE r.workspace_id=:ws AND r.from_id=:cid
        AND r.relation_type IN ('fact_supported_by','fact_refuted_by','fact_context_by')
        ORDER BY r.created_at,r.id""", ws=ws, cid=claim_id)
    for item in rows:
        item['tier'], item['tier_reasons'] = verification_engine.material_tier(item)
        item['role'] = {'fact_supported_by': 'support', 'fact_refuted_by': 'refute',
                        'fact_context_by': 'context'}[item['relation_type']]
        item['displayable'] = bool(item['availability'] == 'available' and item['retention_ok'])
    return rows


def _claim_row(conn, ws, claim_id):
    return one(conn, """SELECT c.*,v.segments,v.retention_expires_at,s.availability AS source_availability,
        s.title AS source_title
        FROM claims c JOIN source_versions v ON v.id=c.origin_version_id JOIN sources s ON s.id=v.source_id
        WHERE c.id=:id AND c.workspace_id=:ws""", id=claim_id, ws=ws)


def _record_view(row, *, claim=None):
    view = dict(row)
    view['assessment'] = view.get('assessment') or {}
    view['evidence_snapshot'] = view.get('evidence_snapshot') or {}
    view['reviewable'] = bool(view.get('review_status') == 'pending' and view.get('record_kind') == 'claim_fact_check')
    if claim:
        view['gate'] = (verification_engine.accept_gate(claim, view['assessment'])
                        if view['reviewable'] else [])
    return view


class EvidenceLinkInput(BaseModel):
    source_version_id: uuid.UUID
    segment_id: str = Field(min_length=1, max_length=60)
    quote: str = Field(min_length=2, max_length=500)
    relation: Literal['support', 'refute', 'context'] = 'support'
    proposition_scope: str = Field(min_length=2, max_length=600)
    directness: Literal['direct', 'indirect'] = 'direct'
    origin_group_id: str | None = Field(default=None, max_length=120)
    independence_status: Literal['confirmed', 'shared_origin', 'unknown'] = 'unknown'
    applicability: dict[str, str] | None = None
    artifact_integrity: Literal['verified', 'unverified', 'suspected_manipulation'] = 'unverified'
    method_transparency: dict[str, bool | None] | None = None
    limitations: list[str] = Field(default_factory=list)
    note: str | None = Field(default=None, max_length=300)


@app.post(PREFIX + '/claims/{claim_id}/evidence-links', status_code=201)
def create_evidence_link(claim_id: uuid.UUID, body: EvidenceLinkInput, user: User):
    """Propose one library material as fact evidence for a claim.

    The anchor offsets and hash are computed here; a material that is the claim's own
    origin version must explain why it can serve a second role. The relation is always
    created as ``proposed`` and only becomes usable when a human accepts a verification
    record that lists it.
    """
    ws = user['workspace_id']
    with engine.begin() as conn:
        claim = _claim_row(conn, ws, claim_id)
        if not claim:
            raise ApiError(404, 'CLAIM_NOT_FOUND', '主张不存在')
        version = one(conn, """SELECT v.*,s.id AS source_id,s.availability,s.title AS source_title
            FROM source_versions v JOIN sources s ON s.id=v.source_id
            WHERE v.id=:id AND v.workspace_id=:ws""", id=body.source_version_id, ws=ws)
        if not version:
            raise ApiError(404, 'SOURCE_VERSION_NOT_FOUND', '来源版本不存在')
        if version['availability'] != 'available':
            raise ApiError(409, 'MATERIAL_UNAVAILABLE', '该来源当前不可用，不能作为证据材料')
        if version['retention_expires_at'] and version['retention_expires_at'] <= datetime.now(timezone.utc):
            raise ApiError(409, 'MATERIAL_RETENTION_EXPIRED', '该来源版本保留期已到，不能作为证据材料')
        if str(version['id']) == str(claim['origin_version_id']):
            note = (body.note or '').strip()
            if len(note) < 10:
                raise ApiError(422, 'MATERIAL_ROLE_EXPLANATION_REQUIRED',
                               '同一来源版本同时承担表达溯源与事实证据时，必须说明为何可用作事实证据',
                               False, {'field': 'note'})
        try:
            relation_type, payload = normalize_link(
                body=body.model_dump(), segments=version['segments'], source_id=str(version['source_id']))
        except FactEvidenceError as exc:
            raise ApiError(422, exc.code, exc.message, False, exc.details)
        execute(conn, 'SELECT pg_advisory_xact_lock(hashtextextended(:key,0))',
                key=str(ws) + str(claim_id))
        used = one(conn, """SELECT count(*) AS n FROM relations WHERE workspace_id=:ws AND from_id=:cid
            AND relation_type IN ('fact_supported_by','fact_refuted_by','fact_context_by')
            AND status<>'rejected'""", ws=ws, cid=claim_id)['n']
        if used >= MAX_FACT_MATERIALS:
            raise ApiError(422, 'EVIDENCE_LIMIT_REACHED',
                           f'每个主张最多 {MAX_FACT_MATERIALS} 条事实材料', False,
                           {'limit': MAX_FACT_MATERIALS})
        key = digest(js([str(claim_id), relation_type, str(version['id']), payload['anchor']]))
        existing = one(conn, 'SELECT id,status FROM relations WHERE workspace_id=:ws AND dedup_key=:key',
                       ws=ws, key=key)
        if existing:
            return {'id': existing['id'], 'relation_type': relation_type,
                    'status': existing['status'], 'deduplicated': True, 'payload': payload}
        created = one(conn, """INSERT INTO relations(workspace_id,from_type,from_id,relation_type,to_type,to_id,
            payload,status,asserted_by,dedup_key,provenance)
            VALUES(:ws,'claim',:cid,:type,'source_version',:vid,CAST(:payload AS jsonb),'proposed','user',:key,
            CAST(:provenance AS jsonb)) RETURNING id,status,created_at""",
                      ws=ws, cid=claim_id, type=relation_type, vid=version['id'],
                      payload=js(payload), key=key,
                      provenance=js({'provided_by': 'user', 'role': 'fact_evidence',
                                     'schema_version': payload['schema_version']}))
        execute(conn, """INSERT INTO audit_events(workspace_id,action,object_id,details)
            VALUES(:ws,'evidence.link_proposed',:id,CAST(:details AS jsonb))""",
                ws=ws, id=created['id'],
                details=js({'claim_id': str(claim_id), 'source_version_id': str(version['id']),
                            'relation_type': relation_type, 'tier': verification_engine.material_tier(
                                {'payload': payload})[0]}))
        return {'id': created['id'], 'relation_type': relation_type, 'status': created['status'],
                'deduplicated': False, 'payload': payload,
                'tier': verification_engine.material_tier({'payload': payload})[0]}


class FactCheckRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=300)


@app.post(PREFIX + '/claims/{claim_id}/fact-checks', status_code=202)
def create_fact_check(claim_id: uuid.UUID, user: User, body: FactCheckRequest | None = None,
                      idempotency_key: Annotated[str | None, Header()] = None):
    """Queue one rule-based, library-only fact check for a claim."""
    ws = user['workspace_id']
    with engine.begin() as conn:
        claim = _claim_row(conn, ws, claim_id)
        if not claim:
            raise ApiError(404, 'CLAIM_NOT_FOUND', '主张不存在')
        active = one(conn, """SELECT id,state FROM jobs WHERE workspace_id=:ws AND kind='fact_check'
            AND state IN ('queued','running','retry_wait') AND input->>'claim_id'=:cid
            ORDER BY created_at DESC LIMIT 1""", ws=ws, cid=str(claim_id))
        if active:
            return {'job_id': active['id'], 'status': active['state'], 'deduplicated': True,
                    'retrieval_mode': 'kb_only'}
        key = (idempotency_key or '').strip()
        if key:
            key = require_idempotency_key(key)
            existing = one(conn, 'SELECT id,state FROM jobs WHERE workspace_id=:ws AND idempotency_key=:key',
                           ws=ws, key=key)
            if existing:
                return {'job_id': existing['id'], 'status': existing['state'],
                        'deduplicated': True, 'retrieval_mode': 'kb_only'}
        else:
            key = 'fact-check:%s:%s' % (claim_id, uuid.uuid4())
        job = one(conn, """INSERT INTO jobs(workspace_id,kind,input,config_snapshot,idempotency_key)
            VALUES(:ws,'fact_check',CAST(:input AS jsonb),CAST(:snapshot AS jsonb),:key)
            RETURNING id,state""", ws=ws, key=key,
                  input=js({'claim_id': str(claim_id), 'trigger': 'manual',
                            'reason': ((body.reason if body else None) or '').strip()[:300]}),
                  snapshot=js({'mode': 'kb_only', 'policy_version': POLICY_VERSION,
                               'external_reads': False}))
        execute(conn, """INSERT INTO audit_events(workspace_id,action,object_id,details)
            VALUES(:ws,'fact_check.queued',:id,CAST(:details AS jsonb))""",
                ws=ws, id=job['id'], details=js({'claim_id': str(claim_id), 'mode': 'kb_only'}))
        return {'job_id': job['id'], 'status': job['state'], 'deduplicated': False,
                'retrieval_mode': 'kb_only'}


@app.get(PREFIX + '/claims/{claim_id}/fact-checks')
def claim_fact_checks(claim_id: uuid.UUID, user: User):
    ws = user['workspace_id']
    with engine.connect() as conn:
        claim = _claim_row(conn, ws, claim_id)
        if not claim:
            raise ApiError(404, 'CLAIM_NOT_FOUND', '主张不存在')
        rows = many(conn, """SELECT * FROM verification_records WHERE workspace_id=:ws
            AND claim_id=:cid AND record_kind='claim_fact_check'
            ORDER BY version_no DESC""", ws=ws, cid=claim_id)
        items = [_record_view(row, claim=claim) for row in rows]
        for item in items:
            item['current'] = str(item['id']) == str(claim['current_verification_id'])
        return {'items': items, 'next_cursor': None,
                'claim': {'id': claim['id'], 'fact_check_status': claim['fact_check_status'],
                          'verification_validity': claim['verification_validity'],
                          'current_verification_id': claim['current_verification_id'],
                          'importance': claim['importance'], 'revision': claim['revision'],
                          'fingerprint': claim['fingerprint']},
                'retrieval_mode': 'kb_only'}


@app.get(PREFIX + '/verification-records/{record_id}')
def verification_record_detail(record_id: uuid.UUID, user: User):
    ws = user['workspace_id']
    with engine.connect() as conn:
        row = one(conn, 'SELECT * FROM verification_records WHERE id=:id AND workspace_id=:ws',
                  id=record_id, ws=ws)
        if not row:
            raise ApiError(404, 'VERIFICATION_RECORD_NOT_FOUND', '核查记录不存在')
        claim = (one(conn, """SELECT c.*,v.segments FROM claims c JOIN source_versions v ON v.id=c.origin_version_id
            WHERE c.id=:id AND c.workspace_id=:ws""", id=row['claim_id'], ws=ws)
                 if row['claim_id'] else None)
        view = _record_view(row, claim=claim)
        view['claim'] = ({'id': claim['id'], 'statement': claim['statement'],
                          'importance': claim['importance'], 'claim_kind': claim['claim_kind'],
                          'fact_check_status': claim['fact_check_status'],
                          'verification_validity': claim['verification_validity'],
                          'current_verification_id': claim['current_verification_id'],
                          'revision': claim['revision'], 'fingerprint': claim['fingerprint']}
                         if claim else None)
        view['supersedes'] = (one(conn, 'SELECT id,version_no,review_status,created_at FROM verification_records\n'
                                   ' WHERE id=:id AND workspace_id=:ws', id=row['supersedes_id'], ws=ws)
                              if row['supersedes_id'] else None)
        view['superseded_by'] = many(conn, """SELECT id,version_no,review_status,reviewer_id,created_at
            FROM verification_records WHERE workspace_id=:ws AND supersedes_id=:id
            ORDER BY version_no""", ws=ws, id=record_id)
        return view


class VerificationReviewInput(BaseModel):
    decision: Literal['accept', 'reject']
    reason: str = Field(min_length=5, max_length=600)
    expected_input_fingerprint: str = Field(min_length=8, max_length=200)


@app.post(PREFIX + '/verification-records/{record_id}/review')
def review_verification_record(record_id: uuid.UUID, body: VerificationReviewInput, user: User):
    """Append a human review version; only acceptance can move the claim pointer.

    The input fingerprint, the claim revision and the material snapshot must still
    match, otherwise the review is rejected as stale. A gate violation (for example a
    ``corroborated`` record with E1 material, or a high-importance claim without E3)
    returns 422 and never becomes a fact confirmation.
    """
    ws = user['workspace_id']
    with engine.begin() as conn:
        record = one(conn, """SELECT * FROM verification_records WHERE id=:id AND workspace_id=:ws
            FOR UPDATE""", id=record_id, ws=ws)
        if not record:
            raise ApiError(404, 'VERIFICATION_RECORD_NOT_FOUND', '核查记录不存在')
        if record['record_kind'] != 'claim_fact_check' or not record['claim_id']:
            raise ApiError(409, 'RECORD_NOT_REVIEWABLE', '只有主张核查记录可以人工复核')
        if record['review_status'] != 'pending':
            raise ApiError(409, 'VERIFICATION_ALREADY_REVIEWED', '该核查记录已复核',
                           False, {'review_status': record['review_status']})
        successor = one(conn, """SELECT id,review_status FROM verification_records
            WHERE workspace_id=:ws AND supersedes_id=:id ORDER BY version_no DESC LIMIT 1""",
                        ws=ws, id=record_id)
        if successor:
            # The original candidate stays immutable; its decision lives in the version
            # that supersedes it, so the same candidate cannot be decided twice.
            raise ApiError(409, 'VERIFICATION_ALREADY_REVIEWED', '该核查记录已有复核版本',
                           False, {'review_status': successor['review_status'],
                                   'superseded_by': str(successor['id'])})
        claim = one(conn, 'SELECT * FROM claims WHERE id=:id AND workspace_id=:ws FOR UPDATE',
                    id=record['claim_id'], ws=ws)
        if not claim:
            raise ApiError(404, 'CLAIM_NOT_FOUND', '主张不存在')
        materials = _claim_materials(conn, ws, claim['id'])
        current_snapshot = verification_engine.snapshot_hash(claim_id=claim['id'], materials=materials)
        stored_snapshot = (record['evidence_snapshot'] or {}).get('snapshot_hash')
        fingerprint = body.expected_input_fingerprint.strip()
        if (fingerprint != record['input_fingerprint'] or record['input_fingerprint'] != claim['fingerprint']
                or record['target_revision'] != claim['revision']
                or (stored_snapshot and stored_snapshot != current_snapshot)):
            raise ApiError(409, 'STALE_VERIFICATION_INPUT', '主张或证据材料已变化，请重新创建核查任务',
                           False, {'expected_input_fingerprint': claim['fingerprint'],
                                   'current_revision': claim['revision'],
                                   'record_input_fingerprint': record['input_fingerprint']})
        reasons = verification_engine.accept_gate(claim, record['assessment']) if body.decision == 'accept' else []
        if reasons:
            raise ApiError(422, 'PUBLICATION_GATE_BLOCKED', '该评估不满足事实发布门槛，不能接受',
                           False, {'reason_codes': reasons,
                                   'assessment_status': record['assessment'].get('status'),
                                   'support_strength': record['assessment'].get('support_strength')})
        version_no = one(conn, """SELECT coalesce(max(version_no),0)+1 AS n FROM verification_records
            WHERE workspace_id=:ws AND claim_id=:cid""", ws=ws, cid=claim['id'])['n']
        accepted = body.decision == 'accept'
        new = one(conn, """INSERT INTO verification_records(workspace_id,record_kind,claim_id,version_no,
            supersedes_id,schema_version,policy_version,input_fingerprint,target_revision,trigger,job_id,
            checker_type,retrieval_manifest,evidence_snapshot,applicability,valid_until,assessment,
            model_runs,review_status,reviewer_id,review_reason,reviewed_at)
            VALUES(:ws,'claim_fact_check',:cid,:no,:prev,:schema,:policy,:fp,:rev,:trigger,:job,'human',
            CAST(:manifest AS jsonb),CAST(:snapshot AS jsonb),CAST(:applicability AS jsonb),:valid,
            CAST(:assessment AS jsonb),CAST(:runs AS jsonb),:status,:reviewer,:reason,now()) RETURNING *""",
                    ws=ws, cid=claim['id'], no=version_no, prev=record['id'],
                    schema=record['schema_version'], policy=POLICY_VERSION,
                    fp=record['input_fingerprint'], rev=record['target_revision'],
                    trigger=record['trigger'], job=record['job_id'],
                    manifest=js(record['retrieval_manifest']), snapshot=js(record['evidence_snapshot']),
                    applicability=js(record['applicability']),
                    valid=verification_engine.valid_until(claim),
                    assessment=js(record['assessment']), runs=js(record['model_runs']),
                    status='accepted' if accepted else 'rejected',
                    reviewer=user['id'], reason=body.reason.strip())
        material_ids = (record['evidence_snapshot'] or {}).get('material_ids') or []
        linked = 0
        if accepted:
            if material_ids:
                result = execute(conn, """UPDATE relations SET status='accepted',
                    payload=payload || CAST(:patch AS jsonb)
                    WHERE workspace_id=:ws AND from_id=:cid AND id=ANY(CAST(:ids AS uuid[]))
                    AND relation_type IN ('fact_supported_by','fact_refuted_by','fact_context_by')""",
                                 ws=ws, cid=claim['id'], ids=[str(item) for item in material_ids],
                                 patch=js({'assessment_record_id': str(new['id']),
                                           'accepted_at': datetime.now(timezone.utc).isoformat()}))
                linked = result.rowcount or 0
            execute(conn, """UPDATE claims SET current_verification_id=:v,verification_validity='current',
                fact_check_status=:status WHERE id=:id AND workspace_id=:ws""",
                    v=new['id'], status=record['assessment'].get('status', 'insufficient'),
                    id=claim['id'], ws=ws)
        execute(conn, """INSERT INTO audit_events(workspace_id,action,object_id,details)
            VALUES(:ws,'verification.review',:id,CAST(:details AS jsonb))""",
                ws=ws, id=new['id'],
                details=js({'claim_id': str(claim['id']), 'decision': body.decision,
                            'supersedes_id': str(record['id']),
                            'assessment_status': record['assessment'].get('status'),
                            'materials_accepted': linked,
                            'reason': body.reason.strip()[:600]}))
        return {'ok': True, 'record': _record_view(new),
                'claim': {'id': claim['id'],
                          'fact_check_status': record['assessment'].get('status') if accepted else claim['fact_check_status'],
                          'verification_validity': 'current' if accepted else claim['verification_validity'],
                          'current_verification_id': new['id'] if accepted else claim['current_verification_id']},
                'materials_accepted': linked}


@app.get(PREFIX + '/verification-capabilities')
def verification_capabilities(user: User):
    """Honest capability declaration: library-only retrieval, no external tool, no model."""
    return {'schema_version': 'verification_capabilities.v1',
            'retrieval': {'mode': 'kb_only',
                          'external_read_enabled': False,
                          'connectors': [],
                          'implemented': ['库内已保存来源版本与事实证据关系检索'],
                          'not_implemented': ['外部网页检索', '自动抓取官方文档']},
            'evidence_links': {'relation_types': list(FACT_TYPES),
                               'schema_version': 'fact_evidence.v1',
                               'max_materials_per_claim': MAX_FACT_MATERIALS,
                               'requires_server_anchor': True,
                               'accepted_only_after_human_review': True},
            'records': {'schema_version': verification_engine.SCHEMA_VERSION,
                        'append_only': True,
                        'checker_types': ['rule', 'human'],
                        'model_route': 'fact_check 模型路由可作为可选建议接入；不改变分档、状态或人工复核要求'},
            'fact_check_route': {'implemented': True,
                                 'mode': 'advisory_only',
                                 'primary_and_fallback_models': True,
                                 'budget_reserved_before_call': True,
                                 'verdicts': list(ai_factcheck.VERDICTS),
                                 'changes_tier_or_status': False,
                                 'rejects_claimed_external_reads': True,
                                 'live_model_fact_check_verified': False},
            'review': {'human_review_required': True, 'auto_accept_allowed': False,
                       'requires_expected_input_fingerprint': True},
            'publication_gate': {'policy_version': POLICY_VERSION,
                                 'scoped_fact_requires_accepted_current_record': True,
                                 'scoped_fact_requires_human_checker': True},
            'source_reliability': 'unknown（来源画像评估尚未实现）',
            'live_fact_check_verified': False,
            'live_note': '自动测试使用隔离 schema 的库内材料与 MockTransport 模型替身；'
                         '没有真实外部检索，也没有用真实模型做事实核查'}


@app.get(PREFIX + '/sources/{source_id}/reliability')
def source_reliability(source_id: uuid.UUID, user: User):
    ws = user['workspace_id']
    with engine.connect() as conn:
        source = one(conn, 'SELECT id,title,reliability_validity FROM sources WHERE id=:id AND workspace_id=:ws',
                     id=source_id, ws=ws)
        if not source:
            raise ApiError(404, 'SOURCE_NOT_FOUND', '来源不存在')
        records = many(conn, """SELECT id,version_no,review_status,assessment,created_at
            FROM verification_records WHERE workspace_id=:ws AND source_id=:sid
            AND record_kind='source_reliability' ORDER BY version_no DESC""", ws=ws, sid=source_id)
    return {'source_id': source['id'], 'reliability_validity': source['reliability_validity'] or 'unknown',
            'dimensions': {'official_identity': 'unknown', 'originality': 'unknown',
                           'error_history': 'unknown', 'transparency': 'unknown'},
            'assessments': records,
            'note': '来源画像评估尚未实现；unknown 不等于低可靠，也不影响单条主张的证据分档'}


class PublicationPreviewInput(BaseModel):
    surface: Literal['feed', 'detail', 'title', 'overview', 'digest', 'search', 'export'] = 'feed'
    expected_revision: int | None = Field(default=None, ge=1)


@app.post(PREFIX + '/updates/{update_id}/publication-preview')
def publication_preview(update_id: uuid.UUID, body: PublicationPreviewInput, user: User):
    """Preview the per-sentence gate without publishing or changing anything."""
    ws = user['workspace_id']
    with engine.connect() as conn:
        row = one(conn, 'SELECT * FROM updates WHERE id=:id AND workspace_id=:ws', id=update_id, ws=ws)
        if not row:
            raise ApiError(404, 'UPDATE_NOT_FOUND', '动态不存在')
        if body.expected_revision is not None and body.expected_revision != row['revision']:
            raise ApiError(409, 'STALE_UPDATE_REVISION', '动态版本已变化，请重新读取',
                           False, {'current_revision': row['revision']})
        decisions = []
        for sentence in row['structured_body'] or []:
            claim = one(conn, 'SELECT * FROM claims WHERE id=:id AND workspace_id=:ws',
                        id=sentence.get('claim_id'), ws=ws)
            if not claim:
                decisions.append({'claim_id': sentence.get('claim_id'), 'action': 'retract',
                                  'publication_mode': None, 'reason_codes': ['CLAIM_UNAVAILABLE'],
                                  'recorded_mode': sentence.get('publication_mode')})
                continue
            ev = evidence(conn, sentence.get('relation_id'), ws)
            record = (one(conn, 'SELECT * FROM verification_records WHERE id=:id AND workspace_id=:ws',
                          id=claim['current_verification_id'], ws=ws)
                      if claim['current_verification_id'] else None)
            decision = PublicationPolicy.evaluate(body.surface, claim, record, bool(ev))
            decisions.append({'claim_id': claim['id'], 'recorded_mode': sentence.get('publication_mode'),
                              'action': decision['action'],
                              'publication_mode': decision['publication_mode'],
                              'reason_codes': decision['reason_codes'],
                              'verification_record_id': claim['current_verification_id'],
                              'fact_check_status': claim['fact_check_status'],
                              'verification_validity': claim['verification_validity'],
                              'mode_still_allowed': mode_is_within(sentence.get('publication_mode'),
                                                                   decision['publication_mode'])})
    blocked = any(item['action'] != 'allow' or not item['mode_still_allowed'] for item in decisions)
    return {'update_id': row['id'], 'revision': row['revision'], 'surface': body.surface,
            'policy_version': POLICY_VERSION, 'blocked': blocked, 'sentences': decisions,
            'note': '预览只读取当前核查状态；不会发布、改写或提升任何句子的事实资格'}


@app.get(PREFIX+'/jobs')
def jobs(user:User,limit:int=20,cursor:str|None=None):
    limit=page_limit(limit)
    cur=decode_cursor(cursor,'jobs',{'c':'ts','i':'uuid'})
    with engine.connect() as conn:
        rows=many(conn,"""SELECT * FROM jobs WHERE workspace_id=:ws
          AND (CAST(:cur_c AS timestamptz) IS NULL OR (created_at,id)<(CAST(:cur_c AS timestamptz),CAST(:cur_i AS uuid)))
          ORDER BY created_at DESC,id DESC LIMIT :take""",ws=user['workspace_id'],cur_c=cur.get('c'),cur_i=cur.get('i'),take=limit+1)
    nxt=None
    if len(rows)>limit:
        nxt=encode_cursor({'k':'jobs','c':rows[limit-1]['created_at'].isoformat(),'i':str(rows[limit-1]['id'])})
    return {'items':rows[:limit],'next_cursor':nxt}


@app.get(PREFIX+'/jobs/{job_id}')
def job_detail(job_id:uuid.UUID,user:User):
    with engine.connect() as conn:
        row=one(conn,'SELECT * FROM jobs WHERE id=:id AND workspace_id=:ws',id=job_id,ws=user['workspace_id'])
        if not row: raise ApiError(404,'JOB_NOT_FOUND','任务不存在')
        row['attempts_history']=many(conn,'SELECT fence,state,created_at FROM job_attempts WHERE job_id=:id ORDER BY created_at',id=job_id)
        return row


@app.post(PREFIX+'/jobs/{job_id}/{action}',status_code=202)
def job_action(job_id:uuid.UUID,action:Literal['cancel','retry'],user:User):
    with engine.begin() as conn:
        try:
            lock=' FOR UPDATE NOWAIT' if action=='cancel' else ' FOR UPDATE'
            job=one(conn,'SELECT * FROM jobs WHERE id=:id AND workspace_id=:ws'+lock,id=job_id,ws=user['workspace_id'])
        except OperationalError as exc:
            if getattr(getattr(exc,'orig',None),'sqlstate',None)=='55P03':
                raise ApiError(409,'JOB_BUSY','任务正在处理，暂时无法取消；请稍后重试',True)
            raise
        if not job: raise ApiError(404,'JOB_NOT_FOUND','任务不存在')
        if action=='cancel':
            if job['state'] in ('cancelled','cancel_requested'):
                return {'ok':True,'state':job['state'],'already_requested':True}
            if job['state'] in ('queued','retry_wait','blocked_budget','blocked_credentials'):
                execute(conn,"UPDATE jobs SET state='cancelled',stage='cancelled',fence=fence+1,lease_until=NULL,updated_at=now() WHERE id=:id",id=job_id)
                return {'ok':True,'state':'cancelled'}
            if job['state']=='running':
                execute(conn,"UPDATE jobs SET state='cancel_requested',updated_at=now() WHERE id=:id",id=job_id)
                return {'ok':True,'state':'cancel_requested','message':'已请求取消，任务将在安全检查点停止'}
            raise ApiError(409,'JOB_STATE_CONFLICT','当前状态不支持取消',False,{'state':job['state']})
        if job['state'] not in ('failed','cancelled','blocked_budget'):
            raise ApiError(409,'JOB_STATE_CONFLICT','只有失败、已取消或预算暂停的任务可以重试',False,{'state':job['state']})
        execute(conn,"UPDATE jobs SET state='queued',stage='queued',fence=fence+1,attempts=0,error_code=NULL,lease_until=NULL,result=CAST('{}' AS jsonb),checkpoint='{}'::jsonb,updated_at=now() WHERE id=:id",id=job_id)
        return {'ok':True,'state':'queued'}


def _settings_view(conn, workspace_id):
    row=one(conn,'SELECT * FROM workspace_settings WHERE id=:id',id=workspace_id)
    state=ai_budget.budget_state(conn, workspace_id)
    routed=one(conn,"""SELECT COUNT(*) AS n FROM task_routes
        WHERE workspace_id=:ws AND enabled AND mode='model'""",ws=workspace_id)['n']
    return {**row,
            'daily_budget':str(row['daily_budget']),'monthly_budget':str(row['monthly_budget']),
            'budget':{'currency':state['currency'],'day':state['day'].isoformat(),
                      'month_start':state['month_start'].isoformat(),
                      'day_spent':str(state['day_spent']),'month_spent':str(state['month_spent']),
                      'day_remaining':str(state['day_remaining']),
                      'month_remaining':str(state['month_remaining'])},
            'routing_revision':row['routing_revision'],
            'mode':'model_routed' if routed else 'mock',
            'cloud_enabled':row['data_policy']=='approved_cloud',
            'retrieval_mode':'kb_only'}


@app.get(PREFIX+'/settings')
def get_settings(user:User):
    with engine.connect() as conn:
        return _settings_view(conn,user['workspace_id'])


class BudgetSettings(BaseModel):
    daily_budget: str|float|int = '0'
    monthly_budget: str|float|int = '0'
    budget_currency: Literal['USD','CNY']|None = None
    data_policy: Literal['local_only','approved_cloud']|None = None


@app.put(PREFIX+'/settings/budget')
def put_budget_settings(body:BudgetSettings,user:User,if_match:Annotated[str|None,Header()]=None):
    """Update budget and data policy together; If-Match uses the workspace revision."""
    expected=require_revision(if_match,'预算设置')
    daily=ai_budget.to_decimal(body.daily_budget,'daily_budget')
    monthly=ai_budget.to_decimal(body.monthly_budget,'monthly_budget')
    with engine.begin() as conn:
        row=one(conn,'SELECT * FROM workspace_settings WHERE id=:id FOR UPDATE',id=user['workspace_id'])
        if not row: raise ApiError(404,'WORKSPACE_NOT_FOUND','工作区不存在')
        if row['revision']!=expected:
            raise ApiError(409,'REVISION_CONFLICT','预算设置已被修改，请刷新后重试',True,
                           {'current_revision':row['revision']})
        currency=body.budget_currency or row['budget_currency']
        if currency!=row['budget_currency']:
            spent=one(conn,"""SELECT COUNT(*) AS n FROM ai_budget_ledger
                WHERE workspace_id=:ws AND currency<>:currency""",ws=user['workspace_id'],currency=currency)
            if spent['n']:
                raise ApiError(409,'CURRENCY_CHANGE_BLOCKED',
                               '已存在其他币种的预算记录；更换预算币种需要先归档账本或使用新的工作区',False,
                               {'ledger_rows':spent['n']})
            models=one(conn,"""SELECT COUNT(*) AS n FROM model_profiles
                WHERE workspace_id=:ws AND price_currency<>:currency""",
                       ws=user['workspace_id'],currency=currency)
            if models['n']:
                raise ApiError(409,'CURRENCY_CHANGE_BLOCKED',
                               '已有模型使用其他计价货币；请先更新模型单价与币种，再更换预算币种',False,
                               {'models':models['n']})
        execute(conn,"""UPDATE workspace_settings SET daily_budget=:daily,monthly_budget=:monthly,
            budget_currency=:currency,data_policy=COALESCE(:policy,data_policy),
            revision=revision+1 WHERE id=:id""",
                id=user['workspace_id'],daily=daily,monthly=monthly,currency=currency,policy=body.data_policy)
        execute(conn,"""INSERT INTO audit_events(workspace_id,action,details)
            VALUES(:ws,'budget.update',CAST(:details AS jsonb))""",
                ws=user['workspace_id'],
                details=js({'daily_budget':str(daily),'monthly_budget':str(monthly),
                            'currency':currency,'data_policy':body.data_policy}))
        return _settings_view(conn,user['workspace_id'])


@app.get(PREFIX+'/digests/today')
def digest_today(user:User,download:bool=False):
    with engine.connect() as conn:
        ws=one(conn,'SELECT timezone FROM workspace_settings WHERE id=:id',id=user['workspace_id'])
        rows=many(conn,"SELECT * FROM updates WHERE workspace_id=:ws AND state='published' AND (first_published_at AT TIME ZONE :tz)::date=(now() AT TIME ZONE :tz)::date ORDER BY first_published_at",ws=user['workspace_id'],tz=ws['timezone'])
        items=[u for r in rows if (u:=visible_update(conn,r,user['workspace_id'],'export' if download else 'digest'))]
        runs=many(conn,"""SELECT r.*, s.name AS source_name FROM discovery_runs r
            LEFT JOIN discovery_sources s ON s.workspace_id=r.workspace_id AND s.id=r.source_id
            WHERE r.workspace_id=:ws
              AND (r.requested_at AT TIME ZONE :tz)::date=(now() AT TIME ZONE :tz)::date
            ORDER BY r.requested_at DESC""",ws=user['workspace_id'],tz=ws['timezone'])
        decisions=many(conn,"""SELECT i.external_id,i.title,i.decision,i.reason,i.created_at
            FROM discovery_run_items i JOIN discovery_runs r
              ON r.workspace_id=i.workspace_id AND r.id=i.run_id
            WHERE r.workspace_id=:ws AND i.decision IN ('enqueued','refreshed')
              AND (r.requested_at AT TIME ZONE :tz)::date=(now() AT TIME ZONE :tz)::date
            ORDER BY i.created_at DESC LIMIT 20""",ws=user['workspace_id'],tz=ws['timezone'])
        collected=many(conn,"""SELECT id,title,author_name,content_status,availability,
            metadata->>'subtitle_status' AS subtitle_status,last_checked_at
            FROM sources WHERE workspace_id=:ws AND discovery_source_id IS NOT NULL
              AND availability<>'deleted' AND last_checked_at IS NOT NULL
              AND (last_checked_at AT TIME ZONE :tz)::date=(now() AT TIME ZONE :tz)::date
            ORDER BY last_checked_at DESC LIMIT 20""",ws=user['workspace_id'],tz=ws['timezone'])
    day=datetime.now(ZoneInfo(ws['timezone'])).strftime('%Y-%m-%d')
    totals={'runs':len(runs),
            'succeeded':sum(1 for run in runs if run['state']=='succeeded'),
            'failed':sum(1 for run in runs if run['state'] in ('failed','blocked_upstream')),
            'cancelled':sum(1 for run in runs if run['state']=='cancelled'),
            'discovered':sum(int(run['discovered'] or 0) for run in runs),
            'enqueued':sum(int(run['enqueued'] or 0) for run in runs),
            'requests_used':sum(int(run['requests_used'] or 0) for run in runs)}
    grouped={}
    for run in runs:
        name=run['source_name'] or '（已删除来源）'
        row=grouped.setdefault(name,{'runs':0,'discovered':0,'enqueued':0,'failed':0,
                                     'errors':[],'last_error_code':None})
        row['runs']+=1
        row['discovered']+=int(run['discovered'] or 0)
        row['enqueued']+=int(run['enqueued'] or 0)
        if run['state'] in ('failed','blocked_upstream'):
            row['failed']+=1
            if run['error_code'] and run['error_code'] not in row['errors']:
                row['errors'].append(run['error_code'])
        if run['error_code']:
            row['last_error_code']=run['error_code']
    discovery_md='## 关键词发现（本日）\n\n'
    if not runs:
        discovery_md+='本日没有关键词发现运行。\n\n'
    else:
        for name,row in grouped.items():
            line=(f'- {name}：运行 {row["runs"]} 次 · 候选 {row["discovered"]} 条 · 入队 '
                  f'{row["enqueued"]} 条 · 失败 {row["failed"]} 次')
            if row['errors']:
                line+=f'（错误码：{"、".join(row["errors"])}）'
            discovery_md+=line+'\n'
        discovery_md+='\n入队只表示已排队采集，不等于已采集、未做 AI 分析，也不代表事实确认。\n\n'
    discovery_md+='## 关键词来源采集（本日）\n\n'
    if not collected:
        discovery_md+='本日没有关键词来源完成一轮采集。\n\n'
    else:
        for source in collected:
            subtitle=(source['subtitle_status'] or 'unknown')
            discovery_md+=(f'- 「{source["title"]}」/ {source["author_name"] or "未知作者"}：'
                           f'{source["content_status"]} · 字幕 {subtitle}\n')
        discovery_md+='\n缺字幕、需要登录与失败分别记录；不生成推荐流，也不把采集结果当作事实结论。\n\n'
    if decisions:
        discovery_md+='### 本日入队候选\n\n'
        for decision in decisions:
            discovery_md+=(f'- {decision["title"] or decision["external_id"]}'
                           f'（{decision["external_id"]}）\n')
        discovery_md+='\n'
    md=f'# 信息日报 · {day}\n\n示例/来源主张不等于事实确认。导出是当前状态快照，外部副本无法远程收回。\n\n'
    if not items: md+='本期无新增动态。\n\n'
    for item in items:
        md+=f'## {item["title"]}\n\n动态 {item["id"]} · 修订 {item["revision"]}\n\n'
        for ev in item['evidence']:
            md+=f'- 来源：{ev["title"]} / {ev["author_name"]}；导入标识 {ev["source_id"]}\n'
    md+='\n'+discovery_md
    if download:
        return PlainTextResponse(md,media_type='text/markdown',headers={'Content-Disposition':f'attachment; filename="digest-{day}.md"'})
    return {'date':day,'markdown':md,'items':items,
            'discovery':{'totals':totals,'runs':[_run_view(run)|{'source_name':run['source_name']}
                                                 for run in runs[:20]],
                         'decisions':decisions,'collected_sources':collected,
                         'note':('发现运行与入队候选只进入采集队列；候选明细按保留期清理，'
                                 '运行账本计数不删除。')}}


# ------------------------------------------------- models, routing and cost (M3)

PROTOCOL_LABELS={'openai':'OpenAI 原生','openai_compatible':'OpenAI 兼容','local':'本地兼容端点','mock':'Mock（仅合成）'}
PROVIDER_STATUS_LABELS={'unverified':'未测试','ok':'可连接','auth_failed':'认证失败',
                        'network_failed':'网络失败','capability_mismatch':'能力不匹配','disabled':'已停用'}


def _gateway_error(exc) -> ApiError:
    upstream={'AUTH_FAILED','RATE_LIMITED','UPSTREAM_FAILED','UPSTREAM_REJECTED','INVALID_RESPONSE',
              'NETWORK_FAILED','ENDPOINT_NOT_FOUND','UNSUPPORTED_PROTOCOL'}
    status=502 if exc.code in upstream else 409
    if exc.code in ('UNKNOWN_TASK','CALL_CONFIRM_REQUIRED','ROUTE_CLOUD_CONFIRM_REQUIRED',
                    'INVALID_PROVIDER_CONFIG','INVALID_MODEL_PRICE','ROUTE_MODEL_REQUIRED',
                    'ROUTE_INVALID'):
        status=422
    return ApiError(status,exc.code,exc.message,getattr(exc,'retryable',False),getattr(exc,'details',{}))


def _provider_view(row):
    return {key:value for key,value in row.items() if key!='credential_sealed'} | {
        'configured':bool(row.get('credential_sealed')),
        'credential_hint':row.get('credential_hint') or '',
        'is_cloud':ai_providers.is_cloud(row['protocol']) and not row['is_local'],
        'protocol_label':PROTOCOL_LABELS.get(row['protocol'],row['protocol']),
        'status_label':PROVIDER_STATUS_LABELS.get(row['status'],row['status'])}


def _model_view(row):
    return {**{key:value for key,value in row.items() if key!='metadata'},
            'input_price_per_mtok':str(row['input_price_per_mtok']),
            'output_price_per_mtok':str(row['output_price_per_mtok'])}


def _provider_row(conn,ws,provider_id,lock=False):
    row=one(conn,'SELECT * FROM provider_connections WHERE id=:id AND workspace_id=:ws'+
            (' FOR UPDATE' if lock else ''),id=provider_id,ws=ws)
    if not row: raise ApiError(404,'PROVIDER_NOT_FOUND','模型供应商不存在')
    return row


def _model_row(conn,ws,model_id,lock=False):
    row=one(conn,'SELECT * FROM model_profiles WHERE id=:id AND workspace_id=:ws'+
            (' FOR UPDATE' if lock else ''),id=model_id,ws=ws)
    if not row: raise ApiError(404,'MODEL_NOT_FOUND','模型不存在')
    return row


def _workspace_currency(conn,ws):
    row=one(conn,'SELECT budget_currency FROM workspace_settings WHERE id=:id',id=ws)
    if not row: raise ApiError(404,'WORKSPACE_NOT_FOUND','工作区不存在')
    return row['budget_currency']


def _price(value,field):
    try:
        return ai_budget.to_decimal(value,field)
    except ai_budget.BudgetError as exc:
        raise ApiError(422,'INVALID_MODEL_PRICE',exc.message)


def _clean_name(value,label='名称',limit=80):
    text=(value or '').strip()
    if not text: raise ApiError(422,'INVALID_NAME',f'{label}不能为空')
    if len(text)>limit: raise ApiError(422,'INVALID_NAME',f'{label}不能超过 {limit} 个字符')
    if any(ch in text for ch in '\r\n\t'): raise ApiError(422,'INVALID_NAME',f'{label}不能含换行或制表符')
    return text


class ProviderInput(BaseModel):
    name:str=Field(min_length=1,max_length=80)
    protocol:Literal['openai','openai_compatible','local','mock']='openai_compatible'
    base_url:str=''
    is_local:bool=False
    api_key:str|None=None
    enabled:bool=False


class ProviderPatch(BaseModel):
    name:str|None=None
    protocol:Literal['openai','openai_compatible','local','mock']|None=None
    base_url:str|None=None
    is_local:bool|None=None
    enabled:bool|None=None


class ProviderSecret(BaseModel):
    api_key:str|None=None
    clear:bool=False


def _normalize_provider_fields(protocol,base_url,is_local):
    if protocol=='mock':
        return '',True
    if protocol=='local':
        is_local=True
    url=ai_providers.validate_base_url(base_url,protocol)
    return url,is_local


def _sealed(key):
    try:
        return seal_api_key(key)
    except SecretError as exc:
        raise ApiError(422,exc.code,exc.message)


@app.get(PREFIX+'/model-providers')
def model_providers(user:User):
    with engine.connect() as conn:
        rows=many(conn,'SELECT * FROM provider_connections WHERE workspace_id=:ws ORDER BY name',
                  ws=user['workspace_id'])
        counts={str(r['provider_id']):r['n'] for r in many(conn,
            'SELECT provider_id,COUNT(*) AS n FROM model_profiles WHERE workspace_id=:ws GROUP BY provider_id',
            ws=user['workspace_id'])}
    return {'items':[(_provider_view(row)|{'model_count':counts.get(str(row['id']),0)}) for row in rows]}


@app.post(PREFIX+'/model-providers',status_code=201)
def create_model_provider(body:ProviderInput,user:User):
    ws=user['workspace_id']
    name=_clean_name(body.name,'供应商名称')
    with engine.begin() as conn:
        if one(conn,'SELECT 1 AS x FROM provider_connections WHERE workspace_id=:ws AND name=:name',
               ws=ws,name=name):
            raise ApiError(409,'PROVIDER_NAME_TAKEN','同名供应商已存在')
        try:
            base_url,is_local=_normalize_provider_fields(body.protocol,body.base_url,body.is_local)
        except ai_providers.ProviderError as exc:
            raise ApiError(422,'INVALID_PROVIDER_CONFIG',exc.message)
        sealed,hint=('', '')
        if body.api_key: sealed,hint=_sealed(body.api_key)
        row=one(conn,"""INSERT INTO provider_connections(workspace_id,name,protocol,base_url,is_local,
            credential_sealed,credential_hint,enabled) VALUES(:ws,:name,:protocol,:url,:local,:sealed,:hint,:enabled)
            RETURNING *""",ws=ws,name=name,protocol=body.protocol,url=base_url,local=is_local,
                    sealed=sealed,hint=hint,enabled=body.enabled)
        execute(conn,"""INSERT INTO audit_events(workspace_id,action,object_id,details)
            VALUES(:ws,'model_provider.create',:id,CAST(:details AS jsonb))""",
                ws=ws,id=row['id'],details=js({'name':name,'protocol':body.protocol}))
        return _provider_view(row)|{'model_count':0}


@app.patch(PREFIX+'/model-providers/{provider_id}')
def patch_model_provider(provider_id:uuid.UUID,body:ProviderPatch,user:User,
                         if_match:Annotated[str|None,Header()]=None):
    expected=require_revision(if_match,'模型供应商')
    ws=user['workspace_id']
    with engine.begin() as conn:
        row=_provider_row(conn,ws,provider_id,lock=True)
        if row['revision']!=expected:
            raise ApiError(409,'REVISION_CONFLICT','供应商已被修改，请刷新后重试',True,
                           {'current_revision':row['revision']})
        name=_clean_name(body.name,'供应商名称') if body.name is not None else row['name']
        protocol=body.protocol or row['protocol']
        base_url=row['base_url'] if body.base_url is None else body.base_url
        is_local=row['is_local'] if body.is_local is None else body.is_local
        try:
            base_url,is_local=_normalize_provider_fields(protocol,base_url,is_local)
        except ai_providers.ProviderError as exc:
            raise ApiError(422,'INVALID_PROVIDER_CONFIG',exc.message)
        if name!=row['name'] and one(conn,
                'SELECT 1 AS x FROM provider_connections WHERE workspace_id=:ws AND name=:name AND id<>:id',
                ws=ws,name=name,id=provider_id):
            raise ApiError(409,'PROVIDER_NAME_TAKEN','同名供应商已存在')
        reset=protocol!=row['protocol'] or base_url!=row['base_url']
        updated=one(conn,"""UPDATE provider_connections SET name=:name,protocol=:protocol,base_url=:url,
            is_local=:local,enabled=COALESCE(:enabled,enabled),
            status=CASE WHEN :reset THEN 'unverified' ELSE status END,
            revision=revision+1,updated_at=now()
            WHERE id=:id AND workspace_id=:ws RETURNING *""",
                id=provider_id,ws=ws,name=name,protocol=protocol,url=base_url,local=is_local,
                enabled=body.enabled,reset=reset)
        return _provider_view(updated)


@app.delete(PREFIX+'/model-providers/{provider_id}')
def delete_model_provider(provider_id:uuid.UUID,user:User,if_match:Annotated[str|None,Header()]=None):
    expected=require_revision(if_match,'模型供应商')
    ws=user['workspace_id']
    with engine.begin() as conn:
        row=_provider_row(conn,ws,provider_id,lock=True)
        if row['revision']!=expected:
            raise ApiError(409,'REVISION_CONFLICT','供应商已被修改，请刷新后重试',True,
                           {'current_revision':row['revision']})
        models=many(conn,'SELECT id,display_name FROM model_profiles WHERE workspace_id=:ws AND provider_id=:id',
                    ws=ws,id=provider_id)
        if models:
            raise ApiError(409,'PROVIDER_IN_USE','该供应商仍有模型；请先删除或迁移模型',False,
                           {'models':[m['display_name'] for m in models]})
        history=one(conn,"""SELECT COUNT(*) AS n FROM ai_invocations
            WHERE workspace_id=:ws AND provider_id=:id""",ws=ws,id=provider_id)
        if history['n']:
            raise ApiError(409,'PROVIDER_HAS_HISTORY',
                           '该供应商已有调用账本，不能删除；请停用并保留费用历史',False,
                           {'invocations':history['n']})
        execute(conn,'DELETE FROM provider_connections WHERE id=:id AND workspace_id=:ws',id=provider_id,ws=ws)
        execute(conn,"""INSERT INTO audit_events(workspace_id,action,object_id,details)
            VALUES(:ws,'model_provider.delete',:id,CAST(:details AS jsonb))""",
                ws=ws,id=provider_id,details=js({'name':row['name']}))
        return {'ok':True,'removed':str(provider_id),
                'note':'只删除本地供应商配置；没有调用任何平台撤权接口'}


@app.put(PREFIX+'/model-providers/{provider_id}/secret')
def set_model_provider_secret(provider_id:uuid.UUID,body:ProviderSecret,user:User):
    ws=user['workspace_id']
    with engine.begin() as conn:
        row=_provider_row(conn,ws,provider_id,lock=True)
        if body.clear:
            sealed,hint=None,''
        else:
            sealed,hint=_sealed(body.api_key)
        updated=one(conn,"""UPDATE provider_connections SET credential_sealed=:sealed,
            credential_hint=:hint,status=CASE WHEN credential_sealed IS DISTINCT FROM :sealed
              THEN 'unverified' ELSE status END,revision=revision+1,updated_at=now()
            WHERE id=:id AND workspace_id=:ws RETURNING *""",
                id=provider_id,ws=ws,sealed=sealed,hint=hint)
        execute(conn,"""INSERT INTO audit_events(workspace_id,action,object_id,details)
            VALUES(:ws,'model_provider.secret',:id,CAST(:details AS jsonb))""",
                ws=ws,id=provider_id,details=js({'cleared':bool(body.clear)}))
        return _provider_view(updated)


@app.post(PREFIX+'/model-providers/{provider_id}/test')
def test_model_provider(provider_id:uuid.UUID,user:User):
    try:
        return ai_gateway.test_provider(user['workspace_id'],provider_id)
    except ai_gateway.GatewayError as exc:
        raise _gateway_error(exc)
    except SecretError as exc:
        raise ApiError(503,exc.code,exc.message)


class ModelInput(BaseModel):
    provider_id:uuid.UUID
    model_id:str=Field(min_length=1,max_length=200)
    display_name:str|None=None
    tier:Literal['economy','standard','high']='standard'
    context_window:int=Field(default=8192,ge=512,le=2000000)
    max_output_tokens:int=Field(default=1024,ge=1,le=200000)
    supports_structured_output:bool=False
    input_price_per_mtok:str|float|int='0'
    output_price_per_mtok:str|float|int='0'
    price_currency:Literal['USD','CNY']|None=None
    enabled:bool=False


class ModelPatch(BaseModel):
    display_name:str|None=None
    tier:Literal['economy','standard','high']|None=None
    context_window:int|None=Field(default=None,ge=512,le=2000000)
    max_output_tokens:int|None=Field(default=None,ge=1,le=200000)
    supports_structured_output:bool|None=None
    input_price_per_mtok:str|float|int|None=None
    output_price_per_mtok:str|float|int|None=None
    price_currency:Literal['USD','CNY']|None=None
    enabled:bool|None=None


def _model_id_text(value):
    text=(value or '').strip()
    if not 1<=len(text)<=200: raise ApiError(422,'INVALID_MODEL_ID','真实模型 ID 长度必须在 1 到 200 之间')
    if any(ch.isspace() or ch in '\r\n\t' for ch in text):
        raise ApiError(422,'INVALID_MODEL_ID','真实模型 ID 不能含空白')
    return text


@app.get(PREFIX+'/models')
def model_list(user:User):
    with engine.connect() as conn:
        rows=many(conn,"""SELECT m.*,p.name AS provider_name,p.protocol,p.is_local,
              p.enabled AS provider_enabled,p.status AS provider_status
            FROM model_profiles m JOIN provider_connections p
              ON p.workspace_id=m.workspace_id AND p.id=m.provider_id
            WHERE m.workspace_id=:ws ORDER BY p.name,m.created_at""",ws=user['workspace_id'])
        usage={}
        for link in many(conn,"""SELECT primary_model_id AS id,task FROM task_routes
              WHERE workspace_id=:ws AND primary_model_id IS NOT NULL""",ws=user['workspace_id']):
            usage.setdefault(str(link['id']),[]).append(link['task'])
        currency=_workspace_currency(conn,user['workspace_id'])
    return {'items':[_model_view(row)|{'used_by_tasks':sorted(usage.get(str(row['id']),[]))}
                     for row in rows],
            'workspace_currency':currency}


@app.post(PREFIX+'/models',status_code=201)
def create_model(body:ModelInput,user:User):
    ws=user['workspace_id']
    with engine.begin() as conn:
        provider=_provider_row(conn,ws,body.provider_id)
        currency=body.price_currency or _workspace_currency(conn,ws)
        workspace_currency=_workspace_currency(conn,ws)
        if currency!=workspace_currency:
            raise ApiError(422,'CURRENCY_MISMATCH',
                           f'模型计价货币必须与工作区预算货币 {workspace_currency} 一致')
        if body.max_output_tokens>body.context_window:
            raise ApiError(422,'INVALID_MODEL_LIMITS','最大输出不能大于上下文窗口')
        if one(conn,'SELECT 1 AS x FROM model_profiles WHERE workspace_id=:ws AND provider_id=:p AND model_id=:m',
               ws=ws,p=body.provider_id,m=body.model_id.strip()):
            raise ApiError(409,'MODEL_ALREADY_EXISTS','该供应商下已存在同一真实模型 ID')
        row=one(conn,"""INSERT INTO model_profiles(workspace_id,provider_id,model_id,display_name,tier,
            context_window,max_output_tokens,supports_structured_output,input_price_per_mtok,
            output_price_per_mtok,price_currency,enabled)
            VALUES(:ws,:provider,:model,:display,:tier,:ctx,:out,:structured,:price_in,:price_out,
            :currency,:enabled) RETURNING *""",
                ws=ws,provider=body.provider_id,model=_model_id_text(body.model_id),
                display=(body.display_name or body.model_id).strip()[:120] or body.model_id,
                tier=body.tier,ctx=body.context_window,out=body.max_output_tokens,
                structured=body.supports_structured_output,
                price_in=_price(body.input_price_per_mtok,'input_price_per_mtok'),
                price_out=_price(body.output_price_per_mtok,'output_price_per_mtok'),
                currency=currency,enabled=body.enabled)
        execute(conn,"""INSERT INTO audit_events(workspace_id,action,object_id,details)
            VALUES(:ws,'model.create',:id,CAST(:details AS jsonb))""",
                ws=ws,id=row['id'],details=js({'provider':provider['name'],'model':row['model_id']}))
        return _model_view(row)|{'provider_name':provider['name'],'used_by_tasks':[]}


@app.patch(PREFIX+'/models/{model_id}')
def patch_model(model_id:uuid.UUID,body:ModelPatch,user:User,
                if_match:Annotated[str|None,Header()]=None):
    expected=require_revision(if_match,'模型')
    ws=user['workspace_id']
    with engine.begin() as conn:
        row=_model_row(conn,ws,model_id,lock=True)
        if row['revision']!=expected:
            raise ApiError(409,'REVISION_CONFLICT','模型已被修改，请刷新后重试',True,
                           {'current_revision':row['revision']})
        currency=body.price_currency or row['price_currency']
        if body.price_currency and currency!=_workspace_currency(conn,ws):
            raise ApiError(422,'CURRENCY_MISMATCH','模型计价货币必须与工作区预算货币一致')
        context_window=body.context_window if body.context_window is not None else row['context_window']
        max_output=body.max_output_tokens if body.max_output_tokens is not None else row['max_output_tokens']
        if max_output>context_window:
            raise ApiError(422,'INVALID_MODEL_LIMITS','最大输出不能大于上下文窗口')
        updated=one(conn,"""UPDATE model_profiles SET display_name=COALESCE(:display,display_name),
            tier=COALESCE(:tier,tier),context_window=:ctx,max_output_tokens=:out,
            supports_structured_output=COALESCE(:structured,supports_structured_output),
            input_price_per_mtok=COALESCE(:price_in,input_price_per_mtok),
            output_price_per_mtok=COALESCE(:price_out,output_price_per_mtok),
            price_currency=:currency,enabled=COALESCE(:enabled,enabled),
            revision=revision+1,updated_at=now()
            WHERE id=:id AND workspace_id=:ws RETURNING *""",
                id=model_id,ws=ws,
                display=(body.display_name or '').strip()[:120] or None,
                tier=body.tier,ctx=context_window,out=max_output,structured=body.supports_structured_output,
                price_in=_price(body.input_price_per_mtok,'input_price_per_mtok')
                          if body.input_price_per_mtok is not None else None,
                price_out=_price(body.output_price_per_mtok,'output_price_per_mtok')
                          if body.output_price_per_mtok is not None else None,
                currency=currency,enabled=body.enabled)
        return _model_view(updated)


@app.delete(PREFIX+'/models/{model_id}')
def delete_model(model_id:uuid.UUID,user:User,if_match:Annotated[str|None,Header()]=None):
    expected=require_revision(if_match,'模型')
    ws=user['workspace_id']
    with engine.begin() as conn:
        row=_model_row(conn,ws,model_id,lock=True)
        if row['revision']!=expected:
            raise ApiError(409,'REVISION_CONFLICT','模型已被修改，请刷新后重试',True,
                           {'current_revision':row['revision']})
        used=[item['task'] for item in many(conn,"""SELECT task FROM task_routes
            WHERE workspace_id=:ws AND (primary_model_id=:id OR fallback_model_ids ? :text)""",
            ws=ws,id=model_id,text=str(model_id))]
        if used:
            raise ApiError(409,'MODEL_IN_USE','该模型仍被任务路由引用；请先替换',False,
                           {'tasks':sorted(used)})
        history=one(conn,"""SELECT COUNT(*) AS n FROM ai_invocations
            WHERE workspace_id=:ws AND model_profile_id=:id""",ws=ws,id=model_id)
        if history['n']:
            raise ApiError(409,'MODEL_HAS_HISTORY',
                           '该模型已有调用账本，不能删除；请停用并保留费用历史',False,
                           {'invocations':history['n']})
        execute(conn,'DELETE FROM model_profiles WHERE id=:id AND workspace_id=:ws',id=model_id,ws=ws)
        execute(conn,"""INSERT INTO audit_events(workspace_id,action,object_id,details)
            VALUES(:ws,'model.delete',:id,CAST(:details AS jsonb))""",
                ws=ws,id=model_id,details=js({'model':row['model_id']}))
        return {'ok':True,'removed':str(model_id)}


class RouteInput(BaseModel):
    task:str
    mode:Literal['mock','rule','model']='mock'
    enabled:bool=False
    primary_model_id:uuid.UUID|None=None
    fallback_model_ids:list[uuid.UUID]=[]
    max_input_tokens:int=Field(default=8000,ge=1,le=200000)
    max_output_tokens:int=Field(default=1024,ge=1,le=200000)
    timeout_seconds:int=Field(default=30,ge=1,le=300)
    max_attempts:int=Field(default=1,ge=1,le=3)
    data_policy:Literal['local_only','approved_cloud']='local_only'


class RoutesInput(BaseModel):
    routes:list[RouteInput]
    confirm_cloud:bool=False


def _routes_view(conn,ws):
    revision=one(conn,'SELECT routing_revision FROM workspace_settings WHERE id=:id',id=ws)['routing_revision']
    return {'items':ai_gateway.route_list(conn,ws),'revision':revision,
            'tasks':[{'task':key,'label':label} for key,label in ai_gateway.TASKS]}


@app.get(PREFIX+'/task-routes')
def task_routes(user:User):
    with engine.connect() as conn:
        return _routes_view(conn,user['workspace_id'])


@app.put(PREFIX+'/task-routes')
def save_task_routes(body:RoutesInput,user:User,if_match:Annotated[str|None,Header()]=None):
    """Atomic group save: every route validates or nothing changes."""
    expected=require_revision(if_match,'任务路由')
    ws=user['workspace_id']
    if not 1<=len(body.routes)<=len(ai_gateway.TASKS):
        raise ApiError(422,'ROUTE_INVALID','一次至少保存 1 条、最多 8 条任务路由')
    seen=set()
    for route in body.routes:
        if route.task not in ai_gateway.TASK_KEYS:
            raise ApiError(422,'ROUTE_INVALID',f'未知任务类型：{route.task}')
        if route.task in seen:
            raise ApiError(422,'ROUTE_INVALID',f'任务重复：{route.task}')
        seen.add(route.task)
        if route.mode=='model' and not route.primary_model_id:
            raise ApiError(422,'ROUTE_MODEL_REQUIRED',f'任务 {route.task} 选择模型模式时必须指定主模型')
        if len(set(route.fallback_model_ids))>3:
            raise ApiError(422,'ROUTE_INVALID','备用模型最多 3 个')
        if len(set(route.fallback_model_ids))!=len(route.fallback_model_ids):
            raise ApiError(422,'ROUTE_INVALID','备用模型不能重复')
        if route.primary_model_id and route.primary_model_id in route.fallback_model_ids:
            raise ApiError(422,'ROUTE_INVALID','主模型不能同时作为备用模型')
    with engine.begin() as conn:
        current=one(conn,'SELECT routing_revision FROM workspace_settings WHERE id=:id FOR UPDATE',id=ws)
        if current['routing_revision']!=expected:
            raise ApiError(409,'REVISION_CONFLICT','任务路由已被修改，请刷新后重试',True,
                           {'current_revision':current['routing_revision']})
        referenced=[route.primary_model_id for route in body.routes if route.primary_model_id]
        for route in body.routes:
            referenced.extend(route.fallback_model_ids)
        cloud_needed=False
        for model_id in dict.fromkeys(referenced):
            model=one(conn,"""SELECT m.id,m.model_id,p.name AS provider_name,p.protocol,p.is_local
                FROM model_profiles m JOIN provider_connections p
                  ON p.workspace_id=m.workspace_id AND p.id=m.provider_id
                WHERE m.workspace_id=:ws AND m.id=:id""",ws=ws,id=model_id)
            if not model:
                raise ApiError(422,'ROUTE_INVALID',f'模型 {model_id} 不属于当前工作区')
            if ai_providers.is_cloud(model['protocol']) and not model['is_local']:
                cloud_needed=True
        if cloud_needed and any(route.data_policy=='approved_cloud' for route in body.routes) \
                and not body.confirm_cloud:
            raise ApiError(422,'ROUTE_CLOUD_CONFIRM_REQUIRED',
                           '所选路由包含云端模型：请确认已了解材料将发送给该供应商，并显式确认')
        totals=[]
        for route in body.routes:
            primary=route.primary_model_id if route.mode=='model' else None
            fallbacks=[str(value) for value in route.fallback_model_ids] if route.mode=='model' else []
            row=one(conn,"""INSERT INTO task_routes(workspace_id,task,mode,enabled,primary_model_id,
                fallback_model_ids,max_input_tokens,max_output_tokens,timeout_seconds,max_attempts,data_policy)
                VALUES(:ws,:task,:mode,:enabled,:primary,CAST(:fallbacks AS jsonb),:tin,:tout,:timeout,:attempts,:policy)
                ON CONFLICT(workspace_id,task) DO UPDATE SET mode=EXCLUDED.mode,enabled=EXCLUDED.enabled,
                  primary_model_id=EXCLUDED.primary_model_id,fallback_model_ids=EXCLUDED.fallback_model_ids,
                  max_input_tokens=EXCLUDED.max_input_tokens,max_output_tokens=EXCLUDED.max_output_tokens,
                  timeout_seconds=EXCLUDED.timeout_seconds,max_attempts=EXCLUDED.max_attempts,
                  data_policy=EXCLUDED.data_policy,revision=task_routes.revision+1,updated_at=now()
                RETURNING *""",
                    ws=ws,task=route.task,mode=route.mode,enabled=route.enabled,primary=primary,
                    fallbacks=js(fallbacks),tin=route.max_input_tokens,tout=route.max_output_tokens,
                    timeout=route.timeout_seconds,attempts=route.max_attempts,policy=route.data_policy)
            totals.append(str(row['id']))
        execute(conn,'UPDATE workspace_settings SET routing_revision=routing_revision+1 WHERE id=:id',id=ws)
        execute(conn,"""INSERT INTO audit_events(workspace_id,action,details)
            VALUES(:ws,'task_routes.save',CAST(:details AS jsonb))""",
                ws=ws,details=js({'tasks':sorted(seen),'cloud_confirmed':body.confirm_cloud}))
        return _routes_view(conn,ws)


class TestCallInput(BaseModel):
    task:str
    confirm_spend:bool=False


@app.post(PREFIX+'/ai/test-call')
def ai_test_call(body:TestCallInput,user:User):
    if not body.confirm_spend:
        raise ApiError(422,'CALL_CONFIRM_REQUIRED','测试调用会产生真实费用；请显式确认后再执行')
    if body.task not in ai_gateway.TASK_KEYS:
        raise ApiError(422,'UNKNOWN_TASK',f'未知任务类型：{body.task}')
    try:
        return ai_gateway.invoke(body.task,workspace_id=user['workspace_id'],
                                 text=ai_gateway.synthetic_sample(body.task),synthetic=True)
    except ai_gateway.GatewayError as exc:
        raise _gateway_error(exc)


@app.get(PREFIX+'/ai/invocations')
def ai_invocations(user:User,limit:int=20,cursor:str|None=None,task:str='',status:str=''):
    limit=page_limit(limit)
    if task and task not in ai_gateway.TASK_KEYS and task!=ai_gateway.CONNECTION_TEST_TASK:
        raise ApiError(400,'INVALID_TASK_FILTER','未知任务类型筛选值')
    if status and status not in ('running','succeeded','failed','blocked'):
        raise ApiError(400,'INVALID_STATUS_FILTER','未知调用状态筛选值')
    cur=decode_cursor(cursor,'aiinv',{'c':'ts','i':'uuid'})
    with engine.connect() as conn:
        rows=many(conn,"""SELECT id,task,status,provider_name,model_label,request_fingerprint,input_summary,
              attempts,input_tokens,output_tokens,cached_tokens,cost_estimate,cost_settled,cost_status,
              currency,error_code,error_message,latency_ms,created_at,finished_at
            FROM ai_invocations WHERE workspace_id=:ws
              AND (:task='' OR task=:task) AND (:status='' OR status=:status)
              AND (CAST(:cur_c AS timestamptz) IS NULL OR (created_at,id)<(CAST(:cur_c AS timestamptz),CAST(:cur_i AS uuid)))
            ORDER BY created_at DESC,id DESC LIMIT :take""",
                ws=user['workspace_id'],task=task,status=status,cur_c=cur.get('c'),cur_i=cur.get('i'),take=limit+1)
    nxt=None
    if len(rows)>limit:
        nxt=encode_cursor({'k':'aiinv','c':rows[limit-1]['created_at'].isoformat(),
                           'i':str(rows[limit-1]['id'])})
    for row in rows:
        for field in ('cost_estimate','cost_settled'):
            if row[field] is not None: row[field]=str(row[field])
        row['task_label']=ai_gateway.TASK_LABELS.get(row['task'],'连接测试' if row['task']=='connection_test' else row['task'])
    return {'items':rows[:limit],'next_cursor':nxt}


@app.get(PREFIX+'/ai/invocations/{invocation_id}')
def ai_invocation_detail(invocation_id:uuid.UUID,user:User):
    with engine.connect() as conn:
        row=one(conn,'SELECT * FROM ai_invocations WHERE id=:id AND workspace_id=:ws',
                id=invocation_id,ws=user['workspace_id'])
        if not row: raise ApiError(404,'INVOCATION_NOT_FOUND','调用记录不存在')
        ledger=one(conn,'SELECT * FROM ai_budget_ledger WHERE invocation_id=:id',id=invocation_id)
    for field in ('cost_estimate','cost_settled'):
        if row.get(field) is not None: row[field]=str(row[field])
    if ledger:
        ledger['reserved']=str(ledger['reserved'])
        ledger['settled']=str(ledger['settled']) if ledger['settled'] is not None else None
    row['ledger']=ledger
    row['task_label']=ai_gateway.TASK_LABELS.get(row['task'],'连接测试' if row['task']=='connection_test' else row['task'])
    return row


@app.get(PREFIX+'/costs/summary')
def cost_summary(user:User,days:int=30):
    if not 1<=days<=366:
        raise ApiError(400,'INVALID_RANGE','days 必须在 1 到 366 之间')
    ws=user['workspace_id']
    with engine.connect() as conn:
        groups=many(conn,"""SELECT (created_at AT TIME ZONE 'UTC')::date AS day,task,provider_name,
              model_label,cost_status,cost_settled,
              COUNT(*) AS calls,
              COUNT(*) FILTER (WHERE status='succeeded') AS succeeded,
              COUNT(*) FILTER (WHERE status='failed') AS failed,
              COUNT(*) FILTER (WHERE status='blocked') AS blocked,
              COALESCE(SUM(input_tokens),0) AS input_tokens,
              COALESCE(SUM(output_tokens),0) AS output_tokens,
              COALESCE(SUM(cached_tokens),0) AS cached_tokens,
              COALESCE(SUM(cost_settled),0) AS settled_total
            FROM ai_invocations WHERE workspace_id=:ws
              AND created_at>=now()-make_interval(days=>:days)
            GROUP BY 1,2,3,4,5,6 ORDER BY 1 DESC,calls DESC""",ws=ws,days=days)
        totals={'calls':0,'succeeded':0,'failed':0,'blocked':0,'input_tokens':0,'output_tokens':0,
                'cached_tokens':0,'settled':Decimal('0'),'unknown':Decimal('0')}
        by_day={}
        for group in groups:
            amount=Decimal(str(group['settled_total']))
            totals['calls']+=group['calls']
            totals['succeeded']+=group['succeeded']
            totals['failed']+=group['failed']
            totals['blocked']+=group['blocked']
            totals['input_tokens']+=int(group['input_tokens'])
            totals['output_tokens']+=int(group['output_tokens'])
            totals['cached_tokens']+=int(group['cached_tokens'])
            if group['cost_status']=='unknown':
                totals['unknown']+=amount
            else:
                totals['settled']+=amount
            day=str(group['day'])
            bucket=by_day.setdefault(day,{'day':day,'calls':0,'tokens':0,'cost':Decimal('0'),'unknown':Decimal('0')})
            bucket['calls']+=group['calls']
            bucket['tokens']+=int(group['input_tokens'])+int(group['output_tokens'])
            if group['cost_status']=='unknown': bucket['unknown']+=amount
            else: bucket['cost']+=amount
        open_rows=one(conn,"""SELECT COUNT(*) AS n,COALESCE(SUM(reserved),0) AS amount
            FROM ai_budget_ledger WHERE workspace_id=:ws AND status='reserved'""",ws=ws)
        state=ai_budget.budget_state(conn,ws)
    for group in groups:
        group['settled_total']=str(group['settled_total'])
        group['day']=str(group['day'])
    return {'window_days':days,
            'currency':state['currency'],
            'totals':{**{key:value for key,value in totals.items() if not isinstance(value,Decimal)},
                      'settled':str(totals['settled']),'unknown':str(totals['unknown'])},
            'by_day':[{**bucket,'cost':str(bucket['cost']),'unknown':str(bucket['unknown'])}
                      for bucket in by_day.values()],
            'groups':groups,
            'budget':{'day':state['day'].isoformat(),'month_start':state['month_start'].isoformat(),
                      'daily_budget':str(state['daily_budget']),'monthly_budget':str(state['monthly_budget']),
                      'day_spent':str(state['day_spent']),'month_spent':str(state['month_spent']),
                      'day_remaining':str(state['day_remaining']),
                      'month_remaining':str(state['month_remaining']),
                      'open_reservations':open_rows['n'],'open_reserved':str(open_rows['amount'])},
            'note':'费用为按供应商返回的 token 用量与本地保存单价估算，未经供应商账单确认；'
                   'unknown 表示供应商未返回用量、已按最坏情况计入预算。'}


@app.get(PREFIX+'/capabilities')
def capabilities(user:User):
    collector=get_collector('bilibili').capabilities().as_dict()
    return {'stage':'M4 Phase 2 开发预览（M1 核心 + M2 Phase 1/2/收尾 + M3 Phase 1/2/收尾 + 事实材料/版本化核查记录/人工复核 + 事实核查建议路由/版本过期封顶/AC32–45 阻断数据集）',
            'models':{'mock':'已实现（仅合成样例，不发起网络请求）',
                      'openai':'协议已实现，未执行真实调用验证',
                      'openai_compatible':'协议已实现，未执行真实调用验证',
                      'local':'OpenAI 兼容本地端点，未执行真实调用验证',
                      'openai_responses':'未实现','anthropic':'未实现','gemini':'未实现'},
            'model_gateway':{'protocols':list(ai_providers.PROTOCOLS),'routing_tasks':len(ai_gateway.TASKS),
                             'fallbacks':True,'short_transaction_with_budget_reservation':True,
                             'live_model_protocol_verified':True,
                             'live_model_protocol_verified_at':'2026-10-20',
                             'live_model_protocol_verified_note':'用户在本机用 openai_compatible 协议对 MIMO 2.6 端点执行一次固定合成提示的连接测试成功（37 输入 / 26 输出 token，估算 0.0000445 USD）；只证明该端点与密钥当时可用，不证明持续可用或抽取质量'},
            'model_extraction':{'implemented':True,'task':'claim_extraction',
                                'server_side_anchor_validation':True,
                                'model_offsets':'仅在与服务端计算一致时接受，不信任模型偏移',
                                'claims_without_anchor':'保存为 candidate，不发布',
                                'fact_check_status':'固定 insufficient；不由抽取模型设置',
                                'semantic_review':'已接入 evidence_review 路由：只判断引文是否支持表达，不判断事实真假；未配置时不调用第二模型，配置后复核未完成的主张保持 candidate',
                                'live_model_extraction_verified':False,
                                'live_note':'尚未用真实模型执行抽取；连接测试成功不等于抽取质量或引用校验在真实输出上通过'},
            'expression_review':{'implemented':True,'task':'evidence_review',
                                 'schema_version':'expression_review.v1',
                                 'verdicts':['supports','partially_supports','unsupported','unclear'],
                                 'publication_gate':'只有 supports / partially_supports 可进入发布门槛；不支持或未完成复核保存为 candidate',
                                 'is_fact_confirmation':False,
                                 'live_verified':False,
                                 'live_note':'自动测试使用 MockTransport；没有用真实模型做语义复核'},
            'entity_disambiguation':{'implemented':True,'task':'entity_disambiguation',
                                     'links_to_existing':'proposed（需人工接受）',
                                     'new_entities':'proposed and auto-created（provisional，按名称去重、不自动合并）',
                                     'grouping':'按已接受/新建实体把同一来源的主张合并为一条动态',
                                     'accept_endpoint':'POST /api/v1/relations/{id}/review',
                                     'live_verified':False,
                                     'live_note':'自动测试使用 MockTransport；没有用真实模型做实体消歧'},
            'budget':{'default_daily':'0','default_monthly':'0','default_currency':'USD',
                      'reservation':'调用前按 token 上限预留最坏费用，调用后按用量结算',
                      'unknown_usage':'供应商未返回用量时保留全部预留额度并标记 unknown'},
            'platforms':{'manual':'已实现（单文件）','bilibili':'已实现单链接导入、元数据/字幕状态、关键词来源配置与调度、分P批量（需确认）、凭证过期/风控恢复与日报联动；真实元数据/字幕与 WBI 关键词搜索已短时烟测，真实扫码确认、分P批量、凭证恢复与定时调度未执行验证','youtube':'后续扩展'},
            'collectors':{'schema':'collector.v1','bilibili':collector},
            'fact_verification':{'implemented':True,'phase':'M4 Phase 2（规则分档 + 可选模型建议 + AC32–45 阻断数据集）',
                                 'retrieval':'kb_only','external_read_enabled':False,
                                 'connectors':[],
                                 'evidence_relations':['fact_supported_by','fact_refuted_by','fact_context_by'],
                                 'evidence_schema':'fact_evidence.v1',
                                 'max_materials_per_claim':MAX_FACT_MATERIALS,
                                 'record_schema':verification_engine.SCHEMA_VERSION,
                                 'records_append_only':True,
                                 'human_review_required':True,'auto_accept_allowed':False,
                                 'review_requires_input_fingerprint':True,
                                 'tiers':list(verification_engine.TIERS),
                                 'fact_check_model_route':'已接入 fact_check 路由，仅对库内材料元数据给出建议；不改变分档、状态或发布门槛，模型声称外部读取会被拒绝',
                                 'superseded_source_version':'材料所属来源已有新版本时封顶为 E1，不能确认当前版本；材料变化会使待复核记录 409 过期',
                                 'truncated_quote_guard':'引文在同一句的限定分句前截断时保存为 candidate，不发布',
                                 'ac_dataset':'tests/fixtures/ac32-45.json（48 案例 + 12 对照，全部合成）',
                                 'publication_gate':'只有已接受且仍有效的核查记录可产生 scoped_fact，且必须由人工接受；其余保持未核实/条件体验表达',
                                 'source_reliability':'unknown（评估尚未实现）',
                                 'live_fact_check_verified':False,
                                 'live_note':'自动测试全部使用隔离 schema 的库内材料与 MockTransport 模型替身；没有真实外部检索，也没有用真实模型做事实核查'},
            'external_verification':'kb_only','publication_policy':POLICY_VERSION,
            'live_collection_verified':False,'live_qr_login_verified':False,
            'live_keyword_discovery_verified':False,
            'keyword_discovery':'WBI 关键词搜索已完成两页真实烟测；完整定时发现、分P批量与凭证恢复未验证',
            'scheduling':'调度器在 worker 进程内运行短事务；默认只处理已启用且已授权联网的来源',
            'incremental_watermark':True,'page_positioning':True,
            'part_batch':{'implemented':True,'requires_confirmation':True,'max_parts_per_request':MAX_PART_BATCH,
                          'live_verified':False},
            'credential_recovery':{'implemented':True,'risk_cooldown_seconds':RISK_COOLDOWN_SECONDS,
                                   'live_verified':False},
            'discovery_digest':True,
            'discovery_run_item_retention_days':discovery_item_retention_days(),
            'local_transcription':False,
            'deferred':['批量导入','密码恢复','语言偏好设置界面','完整 E2E',
                        'UP主订阅/合集/番剧展开','多进程调度',
                        'claim_comparison 模型路由接入流水线',
                        '来源画像评估（GET /sources/{id}/reliability 目前返回 unknown）',
                        '纠错表达式生成与来源画像评估记录',
                        '真实模型抽取/语义复核/实体消歧/事实核查烟测（尚未用真实模型处理材料）',
                        '并发调用上限与多节点预算','Anthropic/Gemini 原生协议']}


def _openapi_document():
    if app.openapi_schema:
        return app.openapi_schema
    from fastapi.openapi.utils import get_openapi
    schema = get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes)
    schemas = schema.setdefault('components', {}).setdefault('schemas', {})
    schemas['ErrorResponse'] = {
        'type': 'object', 'required': ['error', 'request_id'],
        'properties': {
            'error': {
                'type': 'object', 'required': ['code', 'message', 'retryable', 'details'],
                'properties': {
                    'code': {'type': 'string', 'description': '稳定的机器可读错误码'},
                    'message': {'type': 'string', 'description': '可安全展示的错误说明，不含 SQL 或材料正文'},
                    'retryable': {'type': 'boolean'},
                    'details': {'type': 'object', 'additionalProperties': True},
                },
            },
            'request_id': {'type': 'string', 'format': 'uuid'},
        },
    }
    schemas['CursorPage'] = {
        'type': 'object', 'required': ['items', 'next_cursor'],
        'properties': {
            'items': {'type': 'array', 'items': {}},
            'next_cursor': {'type': ['string', 'null'], 'description': '不透明游标；null 表示没有下一页'},
        },
    }
    app.openapi_schema = schema
    return schema


app.openapi = _openapi_document


# A built frontend can also be served directly for a single-origin local deployment.
from fastapi.staticfiles import StaticFiles
web_dist=Path(__file__).resolve().parents[2]/'web'/'dist'
if web_dist.exists():
    app.mount('/',StaticFiles(directory=str(web_dist),html=True),name='web')
