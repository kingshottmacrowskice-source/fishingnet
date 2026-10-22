"""Focused product flow: synthetic captions and model outputs, no real requests."""
import json
import uuid
import httpx
import pytest
from app.jobs import worker, simple
from app.ai import gateway
from app.collectors import register
from app.collectors.bilibili import BilibiliCollector
from app.collectors.base import Credential
from app.collectors.fake import FakeBilibiliCollector
from app.domain.summaries import chunks, video_key, summary_output, mention_video, should_auto_hide_entity
from app.storage.db import one, many, execute
from test_m3_ai_flow import create_provider, create_model, route_payload, save_routes
from test_discovery_flow import create, request_run, items


@pytest.fixture
def v1(api, monkeypatch):
    with api.engine.begin() as conn:
        execute(conn, 'UPDATE workspace_settings SET simple_mode=true')
    provider = create_provider(api)
    model = create_model(api,provider['id'],context_window=64000,max_output_tokens=4000)
    result = save_routes(api,[route_payload(t,model['id'],max_input_tokens=20000,max_output_tokens=3000) for t in ('update_generation','digest_polish')])
    assert result.status_code==200, result.text
    calls=[]
    def invoke(task, **kwargs):
        calls.append((task,kwargs['messages']))
        value = {'summary':'作者介绍了工具 A 的离线功能及使用限制。','points':['支持离线使用'],
                 'entities':[{'name':'工具 A','type':'工具','description':'作者介绍的离线工具'}]} if task=='update_generation' else {'text':'根据本轮视频，工具 A 提供离线能力，使用时仍需注意条件。'}
        return {'parsed':value,'invocation_id':str(uuid.uuid4())}
    monkeypatch.setattr(gateway,'invoke_messages',invoke)
    api.model_calls=calls
    return api


def drain():
    for _ in range(30):
        if not worker.tick():
            return
    pytest.fail('queue failed to drain')


def import_text(api, content='工具 A 支持离线使用，但需先安装。', title='合成视频资料'):
    r=api.post('/api/v1/sources/import',headers={'Idempotency-Key':uuid.uuid4().hex},json={
        'title':title,'content':content,'format':'text','rights_confirmed':True})
    assert r.status_code==202,r.text
    return r.json()


def test_full_round_one_summary_entity_and_digest(v1):
    import_text(v1)
    drain()
    simple.tick()
    drain()
    feed=v1.get('/api/v1/simple/feed').json()['items']
    assert len(feed)==1 and feed[0]['points']==['支持离线使用']
    entities=v1.get('/api/v1/simple/entities').json()['items']
    assert len(entities)==1 and entities[0]['sources'][0]['id']==feed[0]['source_id']
    assert '工具 A' in v1.get('/api/v1/simple/digest').json()['markdown']
    runs=v1.get('/api/v1/simple/rounds').json()['items']
    assert runs[0]['state']=='succeeded'
    count=len(v1.model_calls)
    simple.tick();drain()
    assert len(v1.model_calls)==count


def test_repeat_source_version_reuses_summary(v1):
    result=import_text(v1);drain()
    source_id=result['source_ids'][0]
    r=v1.post('/api/v1/sources/import',headers={'Idempotency-Key':uuid.uuid4().hex},json={
        'source_id':source_id,'title':'修订','content':'同一份资料的新版内容。','format':'text','rights_confirmed':True})
    assert r.status_code==202,r.text
    drain()
    assert len(v1.get('/api/v1/simple/feed').json()['items'])==1
    assert len([c for c in v1.model_calls if c[0]=='update_generation'])==1


def test_long_caption_covers_tail(v1):
    import_text(v1,'头部。'+('中间内容。'*2000)+'尾部重要限制。');drain()
    prompts=''.join(json.dumps(c[1],ensure_ascii=False) for c in v1.model_calls)
    assert '尾部重要限制。' in prompts
    assert len(v1.get('/api/v1/simple/feed').json()['items'])==1


def test_keyword_round_waits_for_summary(v1):
    collector=FakeBilibiliCollector(discovery_items=items(('BV1aa0000001','AI 工具介绍',3)))
    register('bilibili',lambda:collector)
    source=create(v1)
    assert request_run(v1,source['id']).status_code==202
    worker.tick()
    simple.tick()
    assert v1.get('/api/v1/simple/rounds').json()['items'][0]['state']=='collecting'
    drain();simple.tick();drain()
    assert len(v1.get('/api/v1/simple/feed').json()['items'])==1
    assert v1.get('/api/v1/simple/rounds').json()['items'][0]['state']=='succeeded'


def test_failed_summary_is_visible_and_retryable(v1,monkeypatch):
    real=gateway.invoke_messages
    def blocked(*args,**kwargs):
        raise gateway.GatewayError('BUDGET_EXCEEDED','预算不足')
    monkeypatch.setattr(gateway,'invoke_messages',blocked)
    import_text(v1);drain();simple.tick();drain()
    run=v1.get('/api/v1/simple/rounds').json()['items'][0]
    assert run['state']=='partial' and run['items'][0]['process_error']=='BUDGET_EXCEEDED'
    monkeypatch.setattr(gateway,'invoke_messages',real)
    assert v1.post('/api/v1/simple/rounds/'+run['id']+'/retry').status_code==200
    drain();simple.tick();drain()
    assert v1.get('/api/v1/simple/rounds').json()['items'][0]['state']=='succeeded'
    assert len(v1.get('/api/v1/simple/feed').json()['items'])==1


def test_malformed_summary_never_published(v1,monkeypatch):
    monkeypatch.setattr(gateway,'invoke_messages',lambda *a,**k:{'parsed':{'summary':None},'invocation_id':'bad'})
    import_text(v1);drain()
    assert v1.get('/api/v1/simple/feed').json()['items']==[]


def test_mentions_disabled_does_not_enqueue(v1):
    v1.put('/api/v1/simple/mentions',json={'enabled':True,'network_authorized':False,'interval_minutes':5})
    simple.tick()
    with v1.engine.connect() as conn:
        assert one(conn,"SELECT count(*) n FROM jobs WHERE kind='poll_mentions'")['n']==0


def test_mentions_duplicate_notification_and_video(v1,monkeypatch):
    class Mentions(FakeBilibiliCollector):
        def fetch_mentions(self,**kwargs):
            return {'items':[{'id':1,'item':{'uri':'https://www.bilibili.com/video/BV1aa0000001'}}], 'cursor':{'is_end':True}}
        def close(self):pass
    register('bilibili',lambda:Mentions())
    monkeypatch.setattr(worker,'_load_credential',lambda c:Credential(platform='bilibili',values={'SESSDATA':'synthetic'}))
    for _ in range(2):
        v1.put('/api/v1/simple/mentions',json={'enabled':True,'network_authorized':True,'interval_minutes':5})
        simple.tick();drain();simple.tick();drain()
    assert len(v1.get('/api/v1/simple/feed').json()['items'])==1
    with v1.engine.connect() as conn:
        assert one(conn,'SELECT count(*) n FROM mention_receipts')['n']==1
    assert len([c for c in v1.model_calls if c[0]=='update_generation'])==1


def test_one_bvid_identity_across_parts():
    assert video_key({'platform':'bilibili','external_id':'BV1aa0000001:p1'})==video_key({'platform':'bilibili','external_id':'BV1aa0000001:p2'})


def test_chunks_do_not_drop_long_single_segment():
    text='abc'*10001
    assert ''.join(chunks([{'text':text}],500)).strip()==text


def test_mentions_parse_video_and_dynamic_link():
    assert mention_video({'item':{'uri':'https://www.bilibili.com/video/BV1aa0000001?x=1'}})=='BV1aa0000001'
    assert mention_video({'item':{'source_content':'看 https://www.bilibili.com/video/BV1aa0000002'}})=='BV1aa0000002'
    assert mention_video({'item':{'uri':'https://evil.example/video/BV1aa0000001'}}) is None


def test_mentions_adapter_uses_credentials_and_cursor():
    def handler(request):
        assert request.url.path=='/x/msgfeed/at'
        assert request.url.params['reply_time']=='123'
        assert 'SESSDATA=synthetic' in request.headers['cookie']
        return httpx.Response(200,json={'code':0,'data':{'items':[],'cursor':{'is_end':True}}})
    c=BilibiliCollector(transport=httpx.MockTransport(handler),min_interval=0)
    try:
        result=c.fetch_mentions(credential=Credential(platform='bilibili',values={'SESSDATA':'synthetic'}),cursor={'id':2,'time':123})
        assert result['items']==[]
    finally:c.close()


def test_daily_schedule_in_shanghai(v1):
    from datetime import datetime,timezone
    from app.domain.discovery import next_configured_run
    config={'daily_time':'08:00','schedule_timezone':'Asia/Shanghai','schedule_interval_minutes':1440}
    assert next_configured_run(datetime(2026,10,21,1,tzinfo=timezone.utc),config)==datetime(2026,10,22,0,tzinfo=timezone.utc)
    source=create(v1,enabled=True,daily_time='08:00',schedule_timezone='Asia/Shanghai')
    assert source['daily_time']=='08:00' and source['next_run_at']
    result=v1.patch('/api/v1/discovery-sources/'+source['id'],headers={'If-Match':str(source['revision'])},json={
        'name':source['name'],'daily_time':None})
    assert result.status_code==200,result.text
    assert result.json()['daily_time'] is None


def test_no_subtitles_produces_partial_report(v1):
    register('bilibili',lambda:FakeBilibiliCollector(subtitles='missing',discovery_items=items(('BV1aa0000001','AI 没字幕',3))))
    source=create(v1);request_run(v1,source['id']);drain();simple.tick();drain()
    run=v1.get('/api/v1/simple/rounds').json()['items'][0]
    assert run['state']=='partial'
    assert '未完成' in run['report']
    assert not v1.model_calls


def test_search_failure_is_not_reported_as_no_new_videos(v1):
    register('bilibili',lambda:FakeBilibiliCollector(fail_discover_page=1,fail_code='RISK_CONTROL'))
    source=create(v1);request_run(v1,source['id']);drain();simple.tick();drain()
    run=v1.get('/api/v1/simple/rounds').json()['items'][0]
    assert run['state']=='partial' and '关键词发现未完成' in run['report']


def test_dynamic_attachment_resolves_video():
    def handler(request):
        assert request.url.path=='/x/polymer/web-dynamic/v1/detail'
        assert request.url.params['id']=='123456'
        return httpx.Response(200,json={'code':0,'data':{'item':{'modules':{'module_dynamic':{'major':{'archive':{'bvid':'BV1aa0000001'}}}}}}})
    c=BilibiliCollector(transport=httpx.MockTransport(handler),min_interval=0)
    try:
        assert c.resolve_mention_video({'item':{'uri':'https://t.bilibili.com/123456'}},credential=Credential(platform='bilibili',values={'SESSDATA':'synthetic'}))=='BV1aa0000001'
    finally:c.close()


def test_summary_failure_does_not_modify_entity(v1,monkeypatch):
    import_text(v1);drain()
    def blocked(*args,**kwargs):raise gateway.GatewayError('BUDGET_EXCEEDED','预算不足')
    monkeypatch.setattr(gateway,'invoke_messages',blocked)
    simple.tick();drain()
    assert v1.get('/api/v1/simple/rounds').json()['items'][0]['state']=='failed'
    assert not v1.get('/api/v1/simple/entities').json()['items']
    assert len(v1.get('/api/v1/simple/feed').json()['items'])==1


def test_digest_is_daily_not_per_round(v1):
    import_text(v1);drain();simple.tick();drain()
    markdown=v1.get('/api/v1/simple/digest').json()['markdown']
    assert '今日视频' in markdown and '今日实体' in markdown
    assert '第 1 轮' not in markdown and '## 第' not in markdown


def test_summary_output_drops_vague_entities():
    value = {'summary': '介绍几个对象。', 'points': ['要点'],
             'entities': [
                 {'name': 'DeepSeek', 'type': '技术', 'description': '开源模型'},
                 {'name': '必剪', 'type': '工具', 'description': '剪辑工具'},
                 {'name': '作者自用预设', 'type': '其他', 'description': '预设方案'},
                 {'name': '官方桌面端', 'type': '其他', 'description': '客户端'},
                 {'name': '必剪模块', 'type': '其他', 'description': '模块'},
                 {'name': '在校大学生（主角）', 'type': '人物', 'description': '角色'},
                 {'name': '《莫愁乡》', 'type': '其他', 'description': 'MV'},
                 {'name': 'patch the planet（修补地球）', 'type': '其他', 'description': '口号'},
             ]}
    out = summary_output(value)
    assert [e['name'] for e in out['entities']] == ['DeepSeek', '必剪']


def test_summary_prompt_includes_description_and_pinned_comments(v1):
    result=import_text(v1)
    with v1.engine.begin() as conn:
        execute(conn, 'UPDATE sources SET metadata=metadata || CAST(:meta AS jsonb) WHERE id=:id',
                id=result['source_ids'][0],
                meta=simple.js({'desc':'视频简介内容','pinned_comments':['作者：置顶评论内容']}))
    drain()
    prompts=''.join(json.dumps(c[1],ensure_ascii=False) for c in v1.model_calls if c[0]=='update_generation')
    assert '视频简介内容' in prompts
    assert '置顶评论内容' in prompts


def test_entity_type_is_stored_on_node(v1):
    import_text(v1);drain();simple.tick();drain()
    entity=v1.get('/api/v1/simple/entities').json()['items'][0]
    assert entity['metadata']['entity_type']=='工具'


def test_near_duplicate_entity_names_are_consolidated(v1,monkeypatch):
    real=gateway.invoke_messages
    def invoke(task, **kwargs):
        system=str(kwargs['messages'][0]['content'])
        if task=='update_generation':
            return {'parsed':{'summary':'介绍墨记的离线功能。','points':['离线使用'],
                    'entities':[{'name':'墨记','type':'产品','description':'笔记软件'},
                                {'name':'墨记软件','type':'产品','description':'本地优先'}]},
                    'invocation_id':'a'}
        if 'groups' in system:
            return {'parsed':{'groups':[{'name':'墨记','members':['墨记','墨记软件']}]},'invocation_id':'g'}
        return {'parsed':{'text':'本轮汇总'},'invocation_id':'r'}
    monkeypatch.setattr(gateway,'invoke_messages',invoke)
    import_text(v1);drain();simple.tick();drain()
    entities=v1.get('/api/v1/simple/entities').json()['items']
    assert len(entities)==1 and entities[0]['canonical_name']=='墨记'


def test_auto_hide_only_context_types_absent_from_video_intro():
    # Context-like types: hidden only when no associated title/description names them.
    assert should_auto_hide_entity('某某大学','机构',['工具 A 使用介绍','作者介绍工具 A']) is True
    assert should_auto_hide_entity('某某大学','机构',['某某大学发布新模型']) is False
    assert should_auto_hide_entity('某某大学','机构',['某某大学','工具 A']) is False
    assert should_auto_hide_entity('英伟达','组织',['视频简介：英伟达 GPU 供货']) is False
    assert should_auto_hide_entity('张三','人物',['对张三的访谈']) is False
    # Products/tools/technology are never auto-hidden.
    assert should_auto_hide_entity('工具 A','工具',['与本视频无关']) is False
    # De-duplicated form (entity_key strips a product suffix) still counts as introduced.
    assert should_auto_hide_entity('墨记软件','人物',['墨记的作者']) is False


def test_entity_auto_hide_manual_hide_and_delete_links(v1,monkeypatch):
    monkeypatch.setattr(gateway,'invoke_messages',lambda task,**kwargs: {
        'parsed': ({'summary':'介绍工具 A 与某某大学的合作。','points':['要点'],
                    'entities':[{'name':'工具 A','type':'工具','description':'离线工具'},
                                {'name':'某某大学','type':'机构','description':'合作机构'}]}
                   if task=='update_generation' else {'text':'本轮汇总'}),
        'invocation_id':'x'})
    result=import_text(v1,'工具 A 与某某大学合作。');drain();simple.tick();drain()
    assert len(v1.get('/api/v1/simple/feed').json()['items'])==1

    listing=v1.get('/api/v1/simple/entities?include_hidden=1').json()
    by_name={e['canonical_name']:e for e in listing['items']}
    assert listing['hidden_count']==1
    assert by_name['某某大学']['hidden'] is True and by_name['某某大学']['hidden_by']=='auto'
    assert by_name['工具 A']['hidden'] is False
    visible=v1.get('/api/v1/simple/entities').json()['items']
    assert [e['canonical_name'] for e in visible]==['工具 A']
    # Hidden entities never leak into the daily digest's entity section.
    digest_markdown=v1.get('/api/v1/simple/digest').json()['markdown']
    assert '某某大学' not in digest_markdown.split('## 今日实体',1)[1]

    entity_id=by_name['某某大学']['id']
    assert v1.patch('/api/v1/simple/entities/'+entity_id,json={'hidden':False}).status_code==200
    after=v1.get('/api/v1/simple/entities').json()['items']
    assert '某某大学' in [e['canonical_name'] for e in after]
    assert '某某大学' in v1.get('/api/v1/simple/digest').json()['markdown'].split('## 今日实体',1)[1]
    # Manual hide works too, and is reversible.
    assert v1.patch('/api/v1/simple/entities/'+entity_id,json={'hidden':True}).status_code==200
    assert '某某大学' not in [e['canonical_name'] for e in v1.get('/api/v1/simple/entities').json()['items']]
    assert '某某大学' not in v1.get('/api/v1/simple/digest').json()['markdown'].split('## 今日实体',1)[1]

    deleted=v1.delete('/api/v1/simple/entities/'+entity_id)
    assert deleted.status_code==200 and deleted.json()['removed_links']>=1
    assert '某某大学' not in [e['canonical_name'] for e in
                              v1.get('/api/v1/simple/entities?include_hidden=1').json()['items']]
    with v1.engine.connect() as conn:
        assert one(conn,'SELECT count(*) AS n FROM summary_entity_history WHERE node_id=:id',id=entity_id)['n']==0
        assert one(conn,'SELECT status FROM nodes WHERE id=:id',id=entity_id)['status']=='deleted'
    # The information update itself is kept; only the link was removed.
    assert len(v1.get('/api/v1/simple/feed').json()['items'])==1
    assert v1.get('/api/v1/simple/entities?include_hidden=1').json()['hidden_count']==0


def test_auto_hide_respects_a_title_from_an_earlier_round(v1,monkeypatch):
    monkeypatch.setattr(gateway,'invoke_messages',lambda task,**kwargs: {
        'parsed': ({'summary':'介绍某某大学的进展。','points':['要点'],
                    'entities':[{'name':'某某大学','type':'机构','description':'合作机构'},
                                {'name':'工具 A','type':'工具','description':'离线工具'}]}
                   if task=='update_generation' else {'text':'本轮汇总'}),
        'invocation_id':'x'})
    import_text(v1,'第一轮内容','某某大学发布新模型');drain();simple.tick();drain()
    assert '某某大学' in [e['canonical_name'] for e in v1.get('/api/v1/simple/entities').json()['items']]
    # The second video never names it, but the first video's title already introduced it.
    import_text(v1,'第二轮内容','工具 A 更新');drain();simple.tick();drain()
    assert '某某大学' in [e['canonical_name'] for e in v1.get('/api/v1/simple/entities').json()['items']]
