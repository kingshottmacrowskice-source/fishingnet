"""M2 Phase 2 unit rules: config bounds, filtering, watermark, discovery adapter.

No database and no network: the adapter is exercised through ``httpx.MockTransport``
so every assertion is about our own code, never about Bilibili availability.
"""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.collectors import CollectorError, Credential
from app.collectors.base import (
    DiscoveryItem, DiscoveryQuery, Page, RemoteSource, SubtitleResult, SubtitleTrack,
)
from app.collectors.bilibili import BilibiliCollector
from app.domain.discovery import (
    DiscoveryConfigError, keyword_hit, excluded, merge_watermark, next_run_after,
    normalize_config, plan_candidates, run_counters, seen_map, target_pages,
    watermark_summary,
)

BVID = 'BV1xx411c7mD'
NOW = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)


def config(**overrides):
    payload = {'name': 'AI 观察', 'keywords': ['AI']}
    payload.update(overrides)
    return normalize_config(payload)


def item(bvid, title, published=None, author='作者'):
    return DiscoveryItem(platform='bilibili', external_id=bvid, video_id=bvid,
                         title=title, author_name=author,
                         published_at=published or datetime(2026, 2, 1, tzinfo=timezone.utc),
                         page=1)


def entry(bvid, title, published, decision='enqueued'):
    return {'external_id': f'{bvid}:p1', 'bvid': bvid, 'page': 1, 'title': title,
            'author_name': '作者', 'published_at': published, 'decision': decision}


# ---------------------------------------------------------------- config bounds
def test_normalize_config_defaults_are_conservative():
    value = config()
    assert value['enabled'] is False and value['network_authorized'] is False
    assert value['languages'] == ['zh-CN', 'zh-Hans']
    assert value['max_results_per_run'] == 10
    assert value['page_policy'] == 'first' and value['target_pages'] == []
    assert value['language_policy'] == 'prefer'


@pytest.mark.parametrize('overrides,field', [
    ({'keywords': []}, 'keywords'),
    ({'keywords': ['a', 'b', 'c', 'd', 'e', 'f']}, 'keywords'),
    ({'keywords': ['x' * 41]}, 'keywords'),
    ({'exclude_terms': [f't{n}' for n in range(21)]}, 'exclude_terms'),
    ({'max_results_per_run': 21}, 'max_results_per_run'),
    ({'max_pages_per_run': 0}, 'max_pages_per_run'),
    ({'max_requests_per_run': 9}, 'max_requests_per_run'),
    ({'schedule_interval_minutes': 5}, 'schedule_interval_minutes'),
    ({'rescan_after_hours': 9000}, 'rescan_after_hours'),
    ({'match_mode': 'maybe'}, 'match_mode'),
    ({'language_policy': 'strict'}, 'language_policy'),
    ({'languages': ['zh-CN!']}, 'languages'),
    ({'page_policy': 'explicit'}, 'target_pages'),
    ({'target_pages': [1, 2, 3, 4]}, 'target_pages'),
    ({'platform': 'youtube'}, 'platform'),
])
def test_normalize_config_rejects_out_of_bounds(overrides, field):
    with pytest.raises(DiscoveryConfigError) as exc:
        config(**overrides)
    assert exc.value.field == field


def test_target_pages_respects_policy():
    assert target_pages(config()) == (1,)
    assert target_pages(config(page_policy='explicit', target_pages=[2, 3])) == (2, 3)


# --------------------------------------------------------------------- filters
def test_keyword_matching_is_unicode_aware_and_supports_all_mode():
    assert keyword_hit('关于 ＡＩ 的讨论', ['ai']) is True  # NFKC folds fullwidth letters
    assert keyword_hit('关于 AI 的讨论', ['ａｉ']) is True  # ... on both sides
    assert keyword_hit('AI 与 ＣＮ', ['ai', 'cn']) is True  # any
    assert keyword_hit('只有 AI', ['ai', 'cn'], 'all') is False
    assert keyword_hit('AI 与 CN', ['ai', 'cn'], 'all') is True
    assert keyword_hit('', ['ai']) is False


def test_exclusion_checks_title_and_author():
    assert excluded('AI 速成广告', '某人', ['广告']) == '广告'
    assert excluded('AI 教程', '广告号', ['广告']) == '广告'
    assert excluded('AI 教程', '某人', ['广告']) is None
    assert excluded('AI 教程', '某人', []) is None


# ------------------------------------------------------------------- planning
def test_plan_enqueues_new_candidates_and_respects_limit():
    items = [item('BV1aaaaaaaaa', 'AI 一月', datetime(2026, 1, 3, tzinfo=timezone.utc)),
             item('BV1bbbbbbbbb', 'AI 二月', datetime(2026, 2, 3, tzinfo=timezone.utc)),
             item('BV1ccccccccc', 'AI 三月', datetime(2026, 3, 1, tzinfo=timezone.utc))]
    entries = plan_candidates(items, config(max_results_per_run=2), existing={}, now=NOW)
    decisions = {entry['external_id']: entry['decision'] for entry in entries}
    assert decisions['BV1ccccccccc:p1'] == 'enqueued'
    assert decisions['BV1bbbbbbbbb:p1'] == 'enqueued'
    assert decisions['BV1aaaaaaaaa:p1'] == 'over_limit'
    counters = run_counters(entries)
    assert counters['enqueued'] == 2 and counters['matched'] == 3


def test_plan_records_every_rejection_reason():
    items = [item('BV1aaaaaaaaa', 'AI 新闻'),
             item('BV1bbbbbbbbb', 'AI 广告'),
             item('BV1ccccccccc', '与关键词无关')]
    entries = plan_candidates(items, config(exclude_terms=['广告']), existing={}, now=NOW)
    by_id = {entry['external_id']: entry for entry in entries}
    assert by_id['BV1aaaaaaaaa:p1']['decision'] == 'enqueued'
    assert by_id['BV1bbbbbbbbb:p1']['decision'] == 'excluded_term'
    assert '广告' in by_id['BV1bbbbbbbbb:p1']['reason']
    assert by_id['BV1ccccccccc:p1']['decision'] == 'keyword_miss'
    counters = run_counters(entries)
    assert counters['excluded'] == 2


def test_plan_distinguishes_duplicate_refresh_and_deleted():
    items = [item('BV1aaaaaaaaa', 'AI 新'), item('BV1bbbbbbbbb', 'AI 旧'),
             item('BV1ccccccccc', 'AI 已删')]
    existing = {
        'BV1aaaaaaaaa:p1': {'id': 's1', 'availability': 'available',
                            'content_status': 'ready', 'last_checked_at': NOW - timedelta(hours=2)},
        'BV1bbbbbbbbb:p1': {'id': 's2', 'availability': 'available',
                            'content_status': 'ready', 'last_checked_at': NOW - timedelta(days=30)},
        'BV1ccccccccc:p1': {'id': 's3', 'availability': 'deleted',
                            'content_status': 'ready', 'last_checked_at': None},
    }
    entries = plan_candidates(items, config(), existing=existing, now=NOW)
    decisions = {entry['external_id']: entry['decision'] for entry in entries}
    assert decisions['BV1aaaaaaaaa:p1'] == 'duplicate'
    assert decisions['BV1bbbbbbbbb:p1'] == 'refreshed'
    assert decisions['BV1ccccccccc:p1'] == 'skipped_deleted'


def test_pending_source_counts_as_already_queued():
    items = [item('BV1aaaaaaaaa', 'AI 新')]
    existing = {'BV1aaaaaaaaa:p1': {'id': 's1', 'availability': 'unknown',
                                    'content_status': 'pending', 'last_checked_at': None}}
    entries = plan_candidates(items, config(), existing=existing, now=NOW)
    assert entries[0]['decision'] == 'duplicate'


def test_explicit_pages_expand_candidates():
    items = [item('BV1aaaaaaaaa', 'AI 多P')]
    entries = plan_candidates(items, config(page_policy='explicit', target_pages=[2, 3]),
                              existing={}, now=NOW)
    assert sorted(entry['external_id'] for entry in entries) == [
        'BV1aaaaaaaaa:p2', 'BV1aaaaaaaaa:p3']


# ------------------------------------------------------------------ watermark
def test_watermark_advances_and_is_bounded():
    saved = [entry('BV1aaaaaaaaa', 'AI', datetime(2026, 2, 10, tzinfo=timezone.utc)),
             entry('BV1bbbbbbbbb', 'AI', datetime(2026, 2, 20, tzinfo=timezone.utc))]
    merged = merge_watermark({'schema_version': 'watermark.v1'}, saved, now=NOW,
                             run_id='run-1', max_seen=3)
    assert set(seen_map(merged)) == {'BV1aaaaaaaaa:p1', 'BV1bbbbbbbbb:p1'}
    summary = watermark_summary(merged)
    assert summary['newest_published_at'].startswith('2026-02-20')
    assert summary['last_run_id'] == 'run-1' and summary['seen_count'] == 2
    for index in range(5):
        saved.append(entry(f'BV1extra000{index}', 'AI',
                           datetime(2026, 2, 21, tzinfo=timezone.utc)))
    merged = merge_watermark(merged, saved, now=NOW, run_id='run-2', max_seen=3)
    assert len(seen_map(merged)) == 3


def test_watermark_keeps_newest_when_run_saved_nothing():
    previous = {'schema_version': 'watermark.v1',
                'newest_published_at': '2026-02-01T00:00:00+00:00',
                'seen': {'BV1aaaaaaaaa:p1': '2026-02-01T00:00:00+00:00'}}
    merged = merge_watermark(previous, [], now=NOW, run_id='run-3')
    assert merged['newest_published_at'].startswith('2026-02-01')
    assert merged['last_success_at'] == NOW.isoformat()


def test_next_run_backoff_is_bounded():
    assert next_run_after(NOW, 1440) == NOW + timedelta(minutes=1440)
    assert next_run_after(NOW, 1440, 1) == NOW + timedelta(minutes=15)
    assert next_run_after(NOW, 1440, 2) == NOW + timedelta(minutes=30)
    assert next_run_after(NOW, 1440, 20) == NOW + timedelta(minutes=1440)
    assert next_run_after(NOW, 60, 20) == NOW + timedelta(minutes=60)


# ----------------------------------------------------------- discovery adapter
def search_handler(payload, seen=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(str(request.url))
        return httpx.Response(200, json=payload)
    return handler


def make(handler):
    c = BilibiliCollector(transport=httpx.MockTransport(handler), min_interval=0)
    # Pre-set WBI key so discover() doesn't need to fetch /x/web-interface/nav
    c._wbi_key = 'a]b$cdefghijklmnopqrstuvwxyz123456'
    c._wbi_key_at = c._clock()
    # Pre-set buvid so discover() doesn't need to fetch /x/frontend/finger/spi
    c._buvid3 = 'test-buvid3-fingerprint'
    c._buvid4 = 'test-buvid4-fingerprint'
    return c


def search_payload(**data):
    return {'code': 0, 'data': {'result': [{
        'type': 'video', 'bvid': BVID,
        'title': '<em class="keyword">AI</em> 视频 &amp; 标题',
        'author': '虚构作者', 'mid': 42, 'pubdate': 1700000000, 'duration': '12:34'}], **data}}


def test_discover_maps_search_result_without_credentials():
    seen = []
    collector = make(search_handler(search_payload(has_more=True), seen))
    result = collector.discover(DiscoveryQuery(keywords=('AI', '观察')))
    assert result.status == 'ok' and result.has_more is True
    assert result.items[0].title == 'AI 视频 & 标题'
    assert result.items[0].external_id == BVID
    assert result.items[0].duration_seconds == 754
    assert result.items[0].author_external_id == '42'
    assert 'synthetic' not in result.items[0].metadata
    assert result.items[0].metadata['rights_basis'] == 'user_keyword_query'
    assert 'search/type' in seen[0]
    assert 'keyword=AI+%E8%A7%82%E5%AF%9F' in seen[0]
    assert 'order=pubdate' in seen[0]
    assert collector.requests == 1


def test_discover_reports_empty_page():
    collector = make(search_handler({'code': 0, 'data': {'result': []}}))
    result = collector.discover(DiscoveryQuery(keywords=('AI',)))
    assert result.status == 'empty' and result.items == () and result.has_more is False


def test_discover_skips_non_video_and_invalid_bv_items():
    payload = {'code': 0, 'data': {'result': [
        {'type': 'article', 'bvid': BVID, 'title': 'AI 文章'},
        {'type': 'video', 'bvid': 'not-a-bv', 'title': 'AI 坏号'},
        {'type': 'video', 'bvid': BVID, 'title': 'AI 正常'}]}}
    collector = make(search_handler(payload))
    result = collector.discover(DiscoveryQuery(keywords=('AI',)))
    assert len(result.items) == 1 and result.items[0].title == 'AI 正常'


def test_discover_maps_refusal_instead_of_retrying():
    collector = make(search_handler({'code': -403, 'message': '访问权限不足'}))
    with pytest.raises(CollectorError) as exc:
        collector.discover(DiscoveryQuery(keywords=('AI',)))
    assert exc.value.code == 'DISCOVERY_DENIED'
    assert exc.value.retryable is False
    assert collector.requests == 1


def test_discover_maps_bad_keyword_and_page_limit():
    collector = make(search_handler({'code': -400}))
    with pytest.raises(CollectorError) as exc:
        collector.discover(DiscoveryQuery(keywords=('AI',)))
    assert exc.value.code == 'INVALID_QUERY'
    with pytest.raises(CollectorError) as exc:
        make(search_handler(search_payload())).discover(DiscoveryQuery(keywords=('AI',), page=4))
    assert exc.value.code == 'DISCOVERY_PAGE_LIMIT'
    with pytest.raises(CollectorError) as exc:
        make(search_handler(search_payload())).discover(DiscoveryQuery(keywords=()))
    assert exc.value.code == 'INVALID_QUERY'


def test_discover_shares_the_request_budget():
    collector = make(search_handler({'code': 0, 'data': {'result': []}}))
    for _ in range(4):
        collector.discover(DiscoveryQuery(keywords=('AI',)))
    with pytest.raises(CollectorError) as exc:
        collector.discover(DiscoveryQuery(keywords=('AI',)))
    assert exc.value.code == 'REQUEST_BUDGET_EXCEEDED'


def test_discover_maps_risk_control_immediately():
    collector = make(lambda request: httpx.Response(412, json={'code': -412}))
    with pytest.raises(CollectorError) as exc:
        collector.discover(DiscoveryQuery(keywords=('AI',)))
    assert exc.value.code == 'RISK_CONTROL' and exc.value.retryable is False
    assert collector.requests == 1


# ------------------------------------------------------- subtitle languages
def subtitle_handler(tracks, body=None):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if 'web-interface/view' in url:
            return httpx.Response(200, json={'code': 0, 'data': {
                'bvid': BVID, 'title': '标题', 'owner': {'name': '作者', 'mid': 1},
                'pubdate': 1700000000, 'pages': [{'cid': 111, 'page': 1, 'part': '第一段'}]}})
        if 'web-interface/nav' in str(request.url):
            return httpx.Response(200, json={'code': 0, 'data': {'wbi_img': {
                'img_url': 'https://i.test/' + 'a' * 32 + '.png',
                'sub_url': 'https://i.test/' + 'b' * 32 + '.png'}}})
        if 'player/wbi/v2' in url:
            return httpx.Response(200, json={'code': 0, 'data': {'subtitle': {'subtitles': tracks}}})
        return httpx.Response(200, json=body or {'body': [
            {'from': 0, 'to': 1, 'content': '合成字幕'}]})
    return handler


@pytest.mark.parametrize('languages,policy,expected', [
    ((), 'prefer', 'ai-zh'),
    (('zh-CN',), 'require', 'ai-zh'),
    (('en-US',), 'require', 'ai-en'),
])
def test_machine_subtitle_language_is_matched_and_chinese_is_default(languages, policy, expected):
    tracks = [{'lan': 'ai-en', 'lan_doc': 'English AI', 'ai_type': 1,
               'subtitle_url': '//x/en.json'},
              {'lan': 'ai-zh', 'lan_doc': '中文 AI', 'ai_type': 1,
               'subtitle_url': '//x/zh.json'}]
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return subtitle_handler(tracks)(request)

    collector = make(handler)
    locator = collector.parse_locator(f'https://www.bilibili.com/video/{BVID}')
    source = collector.fetch_source(locator)
    result = collector.fetch_subtitles(locator, source, Credential('bilibili', {'SESSDATA': 'x'}),
                                       languages=languages, language_policy=policy)
    assert result.status == 'available' and result.language == expected
    assert result.content == '合成字幕'
    assert len(seen) == 4
    assert seen[-1].endswith('/zh.json' if expected == 'ai-zh' else '/en.json')


def test_subtitle_language_preference_picks_requested_track():
    tracks = [{'lan': 'zh-CN', 'lan_doc': '中文', 'subtitle_url': '//x/zh.json'},
              {'lan': 'en-US', 'lan_doc': 'English', 'subtitle_url': '//x/en.json'}]
    collector = make(subtitle_handler(tracks))
    locator = collector.parse_locator(f'https://www.bilibili.com/video/{BVID}')
    source = collector.fetch_source(locator)
    result = collector.fetch_subtitles(locator, source, Credential('bilibili', {'SESSDATA': 'x'}),
                                       languages=('en-US',))
    assert result.status == 'available' and result.language == 'en-US'


def test_subtitle_language_requirement_can_be_mismatched_without_body_request():
    tracks = [{'lan': 'en-US', 'lan_doc': 'English', 'subtitle_url': '//x/en.json'}]
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return subtitle_handler(tracks)(request)

    collector = make(handler)
    locator = collector.parse_locator(f'https://www.bilibili.com/video/{BVID}')
    source = collector.fetch_source(locator)
    result = collector.fetch_subtitles(locator, source, Credential('bilibili', {'SESSDATA': 'x'}),
                                       languages=('zh-CN',), language_policy='require')
    assert result.status == 'unavailable' and result.reason_code == 'LANGUAGE_MISMATCH'
    assert not any(url.endswith('en.json') for url in seen)


def test_subtitle_language_preference_is_not_a_filter_by_default():
    tracks = [{'lan': 'en-US', 'lan_doc': 'English', 'subtitle_url': '//x/en.json'}]
    collector = make(subtitle_handler(tracks))
    locator = collector.parse_locator(f'https://www.bilibili.com/video/{BVID}')
    source = collector.fetch_source(locator)
    result = collector.fetch_subtitles(locator, source, Credential('bilibili', {'SESSDATA': 'x'}),
                                       languages=('zh-CN',))
    assert result.status == 'available' and result.language == 'en-US'


# --------------------------------------------------------------- checkpoints
def test_remote_source_checkpoint_roundtrip():
    now = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    original = RemoteSource(platform='bilibili', external_id=f'{BVID}:p2',
                            canonical_url='https://www.bilibili.com/video/x?p=2',
                            title='标题', author_name='作者', author_external_id='9',
                            published_at=now, duration_seconds=42,
                            pages=(Page(page_no=2, cid='222', part_title='第二段',
                                        duration_seconds=42),),
                            metadata={'parts': [{'page_no': 2, 'cid': '222'}]})
    restored = RemoteSource.from_checkpoint(original.to_checkpoint())
    assert restored == original
    assert restored.published_at == now


def test_subtitle_checkpoint_roundtrip():
    original = SubtitleResult(status='available',
                              segments=({'segment_id': 'seg_1', 'text': 'x',
                                         'start_ms': 0, 'end_ms': 1000,
                                         'content_hash': 'h'},),
                              content='x', language='zh-CN',
                              tracks=(SubtitleTrack(language='zh-CN', label='中文'),))
    restored = SubtitleResult.from_checkpoint(original.to_checkpoint())
    assert restored.segments == original.segments
    assert restored.tracks[0].language == 'zh-CN'
    assert restored.usable is True


def test_discovery_item_checkpoint_roundtrip():
    original = item(BVID, 'AI 标题', datetime(2026, 2, 1, tzinfo=timezone.utc))
    restored = DiscoveryItem.from_checkpoint(original.to_checkpoint())
    assert restored == original
