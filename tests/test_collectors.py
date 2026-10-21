"""Collector contract, Bilibili adapter and credential sealing — no network, no DB."""
import hashlib

import httpx
import pytest

from app.collectors import CollectorError, Credential, get_collector, register, reset_registry
from app.collectors.base import subtitle_segments
from app.collectors.bilibili import BilibiliCollector
from app.collectors.fake import FakeBilibiliCollector
from app.domain.evidence import validate_anchor
from app.domain.secrets import SecretError, open_sealed, parse_cookie_input, seal
from app.config import settings

BVID = 'BV1xx411c7mD'
URL = f'https://www.bilibili.com/video/{BVID}'


def make(handler, **kwargs):
    return BilibiliCollector(transport=httpx.MockTransport(handler), min_interval=0, **kwargs)


def view_payload(pages=None):
    return {'code': 0, 'data': {
        'bvid': BVID, 'aid': 170001, 'title': '合成视频标题', 'owner': {'name': '虚构作者', 'mid': 42},
        'desc': '这是一段<b>合成简介</b>，说明视频内容与限制。',
        'pubdate': 1700000000, 'duration': 90,
        'pages': pages if pages is not None else [
            {'cid': 111, 'page': 1, 'part': '第一段', 'duration': 90},
            {'cid': 222, 'page': 2, 'part': '第二段', 'duration': 40}]}}


def test_contract_is_exposed_for_bilibili():
    collector = get_collector('bilibili')
    caps = collector.capabilities()
    assert caps.platform == 'bilibili'
    assert caps.single_url and caps.metadata and caps.subtitles
    # The discovery contract exists (M2 Phase 2) but real upstream requests are unverified.
    assert caps.keyword_discovery is True and caps.scheduling is True
    assert caps.max_discovery_pages == 3
    assert caps.local_transcription is False
    assert '字幕清单' in caps.credential_required_for


def test_registry_rejects_unimplemented_platform():
    with pytest.raises(CollectorError) as exc:
        get_collector('youtube')
    assert exc.value.code == 'PLATFORM_NOT_AVAILABLE'


def test_parse_locator_accepts_supported_forms():
    collector = BilibiliCollector(min_interval=0)
    for url, page in [
        (URL, 1),
        (f'{URL}/', 1),
        (f'https://m.bilibili.com/video/{BVID}?p=2&spm_id_from=x', 2),
        (f'http://www.bilibili.com/video/{BVID}?vd_source=abc', 1),
    ]:
        locator = collector.parse_locator(url)
        assert locator.video_id == BVID
        assert locator.page == page
        assert locator.canonical_url.startswith('https://www.bilibili.com/video/')
    assert collector.parse_locator(f'{URL}?p=2').canonical_url.endswith('?p=2')


@pytest.mark.parametrize('url,code', [
    ('', 'INVALID_LOCATOR'),
    ('https://b23.tv/abc123', 'UNSUPPORTED_LOCATOR'),
    ('https://www.bilibili.com/video/av12345', 'UNSUPPORTED_LOCATOR'),
    ('https://www.bilibili.com/bangumi/play/ep123', 'UNSUPPORTED_LOCATOR'),
    ('https://www.bilibili.com/list/123', 'UNSUPPORTED_LOCATOR'),
    ('https://example.com/video/' + BVID, 'UNSUPPORTED_LOCATOR'),
    ('www.bilibili.com/video/' + BVID, 'INVALID_LOCATOR'),
    (f'{URL}?p=0', 'INVALID_LOCATOR'),
    (f'{URL}?p=x', 'INVALID_LOCATOR'),
])
def test_parse_locator_rejects_unsupported(url, code):
    with pytest.raises(CollectorError) as exc:
        BilibiliCollector(min_interval=0).parse_locator(url)
    assert exc.value.code == code


def test_fetch_source_maps_metadata_without_credential():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen['url'] = str(request.url)
        seen['ua'] = request.headers.get('user-agent', '')
        return httpx.Response(200, json=view_payload())

    collector = make(handler)
    locator = collector.parse_locator(f'{URL}?p=2')
    source = collector.fetch_source(locator)
    assert 'x/web-interface/view' in seen['url']
    assert 'FishingNet' in seen['ua']
    assert source.external_id == f'{BVID}:p2'
    assert source.title == '合成视频标题'
    assert source.author_name == '虚构作者'
    assert source.author_external_id == '42'
    assert source.published_at.year == 2023
    assert source.metadata['video_cid'] == '222'
    assert source.metadata['page_count'] == 2
    assert source.availability == 'available'
    assert collector.requests == 1


def test_fetch_source_reports_missing_page():
    collector = make(lambda request: httpx.Response(200, json=view_payload()))
    locator = collector.parse_locator(f'{URL}?p=9')
    with pytest.raises(CollectorError) as exc:
        collector.fetch_source(locator)
    assert exc.value.code == 'SOURCE_PAGE_NOT_FOUND'
    assert exc.value.details['available_pages'] == 2


def test_fetch_source_maps_upstream_codes():
    cases = {-404: 'SOURCE_NOT_FOUND', -403: 'PERMISSION_REQUIRED',
             -412: 'RISK_CONTROL', -101: 'CREDENTIAL_EXPIRED', -999: 'UPSTREAM_ERROR'}
    for upstream, expected in cases.items():
        collector = make(lambda request, upstream=upstream: httpx.Response(200, json={'code': upstream, 'message': 'x'}))
        with pytest.raises(CollectorError) as exc:
            collector.fetch_source(collector.parse_locator(URL))
        assert exc.value.code == expected
        assert exc.value.details.get('upstream_code') == upstream


def test_http_412_is_risk_control_and_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(412, json={'code': -412})

    collector = make(handler)
    with pytest.raises(CollectorError) as exc:
        collector.fetch_source(collector.parse_locator(URL))
    assert exc.value.code == 'RISK_CONTROL'
    assert exc.value.retryable is False
    assert len(calls) == 1


def test_timeout_is_retried_once_within_budget():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ReadTimeout('timeout', request=request)
        return httpx.Response(200, json=view_payload())

    collector = make(handler, sleep=lambda _s: None)
    source = collector.fetch_source(collector.parse_locator(URL))
    assert source.title == '合成视频标题'
    assert len(calls) == 2
    assert collector.requests == 2


def test_request_budget_is_enforced():
    collector = make(lambda request: httpx.Response(200, json={'code': 0, 'data': {}}))
    for _ in range(5):
        collector._request_json('https://api.bilibili.com/x/web-interface/nav')
    with pytest.raises(CollectorError) as exc:
        collector._request_json('https://api.bilibili.com/x/web-interface/nav')
    assert exc.value.code == 'REQUEST_BUDGET_EXCEEDED'


def test_fetch_source_keeps_description_and_aid():
    collector = make(lambda request: httpx.Response(200, json=view_payload()))
    source = collector.fetch_source(collector.parse_locator(URL))
    assert source.metadata['desc'] == '这是一段 合成简介 ，说明视频内容与限制。'
    assert source.metadata['aid'] == 170001


def test_fetch_pinned_comments_extracts_top_and_upper():
    def handler(request):
        assert 'x/v2/reply' in str(request.url)
        assert request.url.params['type'] == '1' and request.url.params['oid'] == '170001'
        return httpx.Response(200, json={'code': 0, 'data': {
            'top': {'member': {'uname': 'UP主'}, 'content': {'message': '置顶说明：<b>补充</b>了条件'}},
            'upper': {'member': {'uname': '作者'}, 'content': {'message': '另一条补充'}},
            'replies': [{'member': {'uname': '路人'}, 'content': {'message': '普通评论'}, 'is_top': False}],
        }})

    collector = make(handler)
    locator = collector.parse_locator(URL)
    comments = collector.fetch_pinned_comments(locator, aid=170001,
                                               credential=Credential(platform='bilibili', values={'SESSDATA': 'x'}))
    assert comments == ['UP主：置顶说明： 补充 了条件', '作者：另一条补充']


def test_fetch_pinned_comments_without_aid_is_offline():
    def handler(request):
        raise AssertionError('must not call the network without an aid')

    collector = make(handler)
    assert collector.fetch_pinned_comments(collector.parse_locator(URL), aid=None) == []
    assert collector.requests == 0


def test_subtitles_without_credential_are_login_required_and_offline():
    def handler(request):
        raise AssertionError('must not call the network without a credential')

    collector = make(handler)
    locator = collector.parse_locator(URL)
    fake_source = FakeBilibiliCollector().fetch_source(locator)
    result = collector.fetch_subtitles(locator, fake_source, None)
    assert result.status == 'login_required'
    assert result.reason_code == 'CREDENTIAL_REQUIRED'
    assert collector.requests == 0


def test_missing_subtitle_track_is_reported_as_missing():
    def handler(request):
        if 'web-interface/nav' in str(request.url):
            return httpx.Response(200, json={'code': 0, 'data': {'wbi_img': {
                'img_url': 'https://i.test/' + 'a' * 32 + '.png',
                'sub_url': 'https://i.test/' + 'b' * 32 + '.png'}}})
        if 'player/wbi/v2' in str(request.url):
            return httpx.Response(200, json={'code': 0, 'data': {'subtitle': {'subtitles': []}}})
        return httpx.Response(200, json=view_payload())

    collector = make(handler)
    locator = collector.parse_locator(URL)
    source = collector.fetch_source(locator)
    result = collector.fetch_subtitles(locator, source, Credential('bilibili', {'SESSDATA': 'abc'}))
    assert result.status == 'missing'
    assert result.reason_code == 'NO_SUBTITLE_TRACK'
    assert result.segments == ()


def test_subtitle_track_is_converted_to_valid_segments():
    def handler(request):
        url = str(request.url)
        if 'web-interface/view' in url:
            return httpx.Response(200, json=view_payload())
        if 'web-interface/nav' in str(request.url):
            return httpx.Response(200, json={'code': 0, 'data': {'wbi_img': {
                'img_url': 'https://i.test/' + 'a' * 32 + '.png',
                'sub_url': 'https://i.test/' + 'b' * 32 + '.png'}}})
        if 'player/wbi/v2' in url:
            return httpx.Response(200, json={'code': 0, 'data': {'subtitle': {'subtitles': [
                {'lan': 'en-US', 'lan_doc': 'English', 'subtitle_url': '//example.test/en.json', 'ai_type': 0},
                {'lan': 'zh-CN', 'lan_doc': '中文（中国）', 'subtitle_url': '//example.test/zh.json', 'ai_type': 0},
                {'lan': 'ai-zh', 'lan_doc': '中文（自动生成）', 'subtitle_url': '//example.test/ai.json', 'ai_type': 1},
            ]}}})
        if url.endswith('zh.json'):
            return httpx.Response(200, json={'body': [
                {'from': 0.0, 'to': 1.5, 'content': '第一句字幕'},
                {'from': 1.5, 'to': 3.0, 'content': '第二句字幕'}]})
        return httpx.Response(200, json={'body': []})

    collector = make(handler)
    locator = collector.parse_locator(URL)
    source = collector.fetch_source(locator)
    result = collector.fetch_subtitles(locator, source, Credential('bilibili', {'SESSDATA': 'abc'}))
    assert result.status == 'available'
    assert result.language == 'zh-CN'
    assert len(result.segments) == 1
    segment = result.segments[0]
    assert segment['segment_id'] == 'seg_1'
    assert segment['start_ms'] == 0 and segment['end_ms'] == 3000
    assert '第一句字幕' in segment['text']
    validate_anchor(list(result.segments), {
        'segment_id': 'seg_1', 'char_start': 0, 'char_end': 3,
        'quote': segment['text'][0:3], 'quote_hash': hashlib.sha256(
            segment['text'][0:3].encode()).hexdigest()})


@pytest.mark.parametrize('cdn_host', ['example.test', 'subtitle.bilibili.com'])
def test_subtitle_cdn_never_receives_platform_cookies_even_on_retry(cdn_host):
    body_requests = []

    def handler(request):
        if 'web-interface/view' in str(request.url):
            return httpx.Response(200, json=view_payload())
        if 'web-interface/nav' in str(request.url):
            return httpx.Response(200, json={'code': 0, 'data': {'wbi_img': {
                'img_url': 'https://i.test/' + 'a' * 32 + '.png',
                'sub_url': 'https://i.test/' + 'b' * 32 + '.png'}}})
        if 'player/wbi/v2' in str(request.url):
            assert 'SESSDATA=synthetic-secret' in request.headers.get('cookie', '')
            return httpx.Response(200, headers={
                'set-cookie': 'platform_session=synthetic; Domain=.bilibili.com; Path=/'
            }, json={'code': 0, 'data': {'subtitle': {'subtitles': [
                {'lan': 'zh-CN', 'subtitle_url': f'https://{cdn_host}/zh.json'}
            ]}}})
        body_requests.append(request)
        assert 'cookie' not in request.headers
        if len(body_requests) == 1:
            return httpx.Response(503, headers={'set-cookie': 'cdn_session=synthetic; Path=/'})
        return httpx.Response(200, json={'body': [
            {'from': 0, 'to': 1, 'content': '合成字幕'}]})

    collector = make(handler, sleep=lambda _: None)
    locator = collector.parse_locator(URL)
    source = collector.fetch_source(locator)
    # Simulate a resumed task with metadata already checkpointed: four calls
    # remain for nav, subtitle listing and a single CDN retry.
    collector.requests = 0
    result = collector.fetch_subtitles(
        locator, source, Credential('bilibili', {'SESSDATA': 'synthetic-secret'}))
    assert result.status == 'available'
    assert len(body_requests) == 2
    assert collector.requests == 4
    collector.close()


def test_unreadable_subtitle_body_is_an_explicit_error():
    def handler(request):
        url = str(request.url)
        if 'web-interface/nav' in str(request.url):
            return httpx.Response(200, json={'code': 0, 'data': {'wbi_img': {
                'img_url': 'https://i.test/' + 'a' * 32 + '.png',
                'sub_url': 'https://i.test/' + 'b' * 32 + '.png'}}})
        if 'player/wbi/v2' in url:
            return httpx.Response(200, json={'code': 0, 'data': {'subtitle': {'subtitles': [
                {'lan': 'zh-CN', 'lan_doc': '中文', 'subtitle_url': '//example.test/zh.json'}]}}})
        if url.endswith('zh.json'):
            return httpx.Response(200, json={'unexpected': []})
        return httpx.Response(200, json=view_payload())

    collector = make(handler)
    locator = collector.parse_locator(URL)
    source = collector.fetch_source(locator)
    with pytest.raises(CollectorError) as exc:
        collector.fetch_subtitles(locator, source, Credential('bilibili', {'SESSDATA': 'abc'}))
    assert exc.value.code == 'SUBTITLE_UNREADABLE'


def test_account_check_statuses():
    def nav(is_login, uname=None):
        return {'code': 0, 'data': {'isLogin': is_login, 'uname': uname, 'mid': 7}}

    collector = make(lambda request: httpx.Response(200, json=nav(True, '我的账号')))
    result = collector.check_account(Credential('bilibili', {'SESSDATA': 'abc'}))
    assert result.status == 'connected' and result.display_name == '我的账号'
    assert result.subtitle_capability is True

    collector = make(lambda request: httpx.Response(200, json=nav(False)))
    assert collector.check_account(Credential('bilibili', {'SESSDATA': 'abc'})).status == 'expired'

    collector = make(lambda request: httpx.Response(200, json={'code': -101}))
    expired = collector.check_account(Credential('bilibili', {'SESSDATA': 'abc'}))
    assert expired.status == 'expired' and expired.error_code == 'CREDENTIAL_EXPIRED'

    collector = make(lambda request: (_ for _ in ()).throw(AssertionError('no network')))
    offline = collector.check_account(None)
    assert offline.status == 'not_configured'
    assert offline.subtitle_capability is False
    assert collector.requests == 0


def test_credential_repr_never_leaks_secret():
    credential = Credential('bilibili', {'SESSDATA': 'super-secret-value'})
    assert 'super-secret-value' not in repr(credential)
    assert 'super-secret-value' not in str(credential)


def test_cookie_input_parsing_keeps_only_sessdata():
    values = parse_cookie_input('SESSDATA=abc12345678; bili_jct=xyz; DedeUserID=9')
    assert values == {'SESSDATA': 'abc12345678'}
    assert parse_cookie_input('abc123def456') == {'SESSDATA': 'abc123def456'}
    for bad in ('', 'bili_jct=xyz', 'SESSDATA='):
        with pytest.raises(SecretError):
            parse_cookie_input(bad)


def test_seal_roundtrip_and_wrong_key(monkeypatch):
    monkeypatch.setattr(settings, 'master_key', 'unit-test-master-key-0123456789')
    token = seal({'SESSDATA': 'secret-cookie'})
    assert 'secret-cookie' not in token
    assert open_sealed(token) == {'SESSDATA': 'secret-cookie'}
    monkeypatch.setattr(settings, 'master_key', 'another-master-key-0123456789')
    with pytest.raises(SecretError) as exc:
        open_sealed(token)
    assert exc.value.code == 'CREDENTIAL_UNREADABLE'


def test_seal_requires_master_key(monkeypatch):
    monkeypatch.setattr(settings, 'master_key', '')
    with pytest.raises(SecretError) as exc:
        seal({'SESSDATA': 'x' * 20})
    assert exc.value.code == 'MASTER_KEY_NOT_CONFIGURED'


def test_subtitle_segments_group_long_captions():
    cues = [{'text': 'x' * 300, 'start_ms': 0, 'end_ms': 1000},
            {'text': 'y' * 300, 'start_ms': 1000, 'end_ms': 2000},
            {'text': 'z', 'start_ms': 2000, 'end_ms': 2100}]
    segments = subtitle_segments(cues, max_chars=400)
    assert len(segments) == 2
    assert segments[0]['text'].startswith('x')
    assert segments[0]['end_ms'] == 1000
    assert segments[1]['segment_id'] == 'seg_2'
    assert all(len(segment['content_hash']) == 64 for segment in segments)


def test_fake_collector_states():
    register('bilibili', lambda: FakeBilibiliCollector(subtitles='missing'))
    collector = get_collector('bilibili')
    locator = collector.parse_locator(URL)
    source = collector.fetch_source(locator)
    assert source.metadata['synthetic'] is True
    assert collector.fetch_subtitles(locator, source).status == 'missing'
    reset_registry()
    register('bilibili', lambda: FakeBilibiliCollector(fail_at='fetch_source', fail_code='RISK_CONTROL'))
    with pytest.raises(CollectorError) as exc:
        get_collector('bilibili').fetch_source(get_collector('bilibili').parse_locator(URL))
    assert exc.value.code == 'RISK_CONTROL'


def poll_collector(code, cookies=None):
    def handler(request):
        headers = [('set-cookie', f'{name}={value}; Path=/') for name, value in (cookies or {}).items()]
        return httpx.Response(200, json={'code': 0, 'data': {'code': code}}, headers=headers)

    return make(handler)


def test_qr_endpoints_use_passport_host():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if 'qrcode/generate' in str(request.url):
            return httpx.Response(200, json={'code': 0, 'data': {
                'url': 'https://passport.bilibili.com/qrcode/h5/test', 'qrcode_key': 'k'}})
        return httpx.Response(200, json={'code': 0, 'data': {'code': 86101}})

    collector = make(handler)
    collector.start_qr_login()
    collector.poll_qr_login('k')
    assert len(seen) == 2
    assert all('passport.bilibili.com' in url for url in seen)
    assert all('api.bilibili.com' not in url for url in seen)


def test_qr_login_challenge_redacts_key():
    def handler(request):
        assert 'qrcode/generate' in str(request.url)
        return httpx.Response(200, json={'code': 0, 'data': {
            'url': 'https://passport.bilibili.com/qrcode/h5/test', 'qrcode_key': 'qr-key-secret'}})

    collector = make(handler)
    assert collector.capabilities().qr_login is True
    login = collector.start_qr_login()
    assert login.url.startswith('https://')
    assert login.expires_in == 180
    assert 'qr-key-secret' not in repr(login)


def test_qr_poll_status_mapping():
    assert poll_collector(86101).poll_qr_login('k').status == 'waiting_scan'
    assert poll_collector(86090).poll_qr_login('k').status == 'scanned'
    assert poll_collector(86038).poll_qr_login('k').status == 'expired'
    unexpected = poll_collector(99999).poll_qr_login('k')
    assert unexpected.status == 'error'
    assert unexpected.error_code == 'QR_UPSTREAM_99999'


def test_qr_confirmed_returns_only_sessdata_without_leaking():
    collector = poll_collector(0, {'SESSDATA': 'cookie-secret', 'bili_jct': 'csrf-value'})
    result = collector.poll_qr_login('k')
    assert result.status == 'confirmed'
    assert result.cookies == {'SESSDATA': 'cookie-secret'}
    assert 'cookie-secret' not in repr(result)


def test_qr_confirmed_without_sessdata_is_an_error():
    collector = poll_collector(0, {'bili_jct': 'only-csrf'})
    with pytest.raises(CollectorError) as exc:
        collector.poll_qr_login('k')
    assert exc.value.code == 'QR_NO_CREDENTIAL'


def test_qr_poll_respects_risk_control():
    collector = make(lambda request: httpx.Response(412, json={'code': -412}))
    with pytest.raises(CollectorError) as exc:
        collector.poll_qr_login('k')
    assert exc.value.code == 'RISK_CONTROL'


def test_fake_qr_states():
    fake = FakeBilibiliCollector(qr='expired')
    assert fake.start_qr_login().url.startswith('https://')
    assert fake.poll_qr_login('k').status == 'expired'
    fake = FakeBilibiliCollector(qr='risk')
    assert fake.poll_qr_login('k').error_code == 'RISK_CONTROL'
    fake = FakeBilibiliCollector()
    assert fake.poll_qr_login('k').status == 'waiting_scan'
    assert fake.poll_qr_login('k').status == 'scanned'
    confirmed = fake.poll_qr_login('k')
    assert confirmed.status == 'confirmed' and confirmed.cookies['SESSDATA']


@pytest.mark.parametrize('upstream_code,expected', [(-403, 'PERMISSION_REQUIRED'),
                                                 (-352, 'RISK_CONTROL'),
                                                 (-101, 'CREDENTIAL_EXPIRED')])
def test_signed_subtitle_stops_without_library_retry(upstream_code, expected, monkeypatch):
    from bilibili_api.utils import network
    monkeypatch.setattr(network, 'get_client', lambda: pytest.fail('Library transport used'))
    seen = []

    def handler(request):
        seen.append(request)
        assert request.headers['user-agent'].startswith('FishingNet/')
        assert 'buvid' not in request.headers.get('cookie', '')
        if request.url.path.endswith('/view'):
            return httpx.Response(200, json=view_payload())
        if request.url.path.endswith('/nav'):
            return httpx.Response(200, json={'code': 0, 'data': {'wbi_img': {
                'img_url': 'https://i.test/' + 'a' * 32 + '.png',
                'sub_url': 'https://i.test/' + 'b' * 32 + '.png'}}})
        assert request.url.path == '/x/player/wbi/v2'
        assert len(request.url.params['w_rid']) == 32
        assert 'wts' in request.url.params
        assert not any(k.startswith('dm_') for k in request.url.params)
        return httpx.Response(200, json={'code': upstream_code})

    collector = make(handler)
    locator = collector.parse_locator(URL)
    source = collector.fetch_source(locator)
    with pytest.raises(CollectorError) as exc:
        collector.fetch_subtitles(locator, source, Credential('bilibili', {'SESSDATA': 'fake'}))
    assert exc.value.code == expected
    assert len(seen) == 3
    collector.close()


def test_signed_subtitle_bad_key_stops_before_player_request():
    seen = []
    def handler(request):
        seen.append(request.url.path)
        if request.url.path.endswith('/view'):
            return httpx.Response(200, json=view_payload())
        return httpx.Response(200, json={'code': 0, 'data': {'wbi_img': {}}})
    collector = make(handler)
    locator = collector.parse_locator(URL)
    source = collector.fetch_source(locator)
    with pytest.raises(CollectorError) as exc:
        collector.fetch_subtitles(locator, source, Credential('bilibili', {'SESSDATA': 'fake'}))
    assert exc.value.code == 'UPSTREAM_INVALID_JSON'
    assert len(seen) == 2
    collector.close()
