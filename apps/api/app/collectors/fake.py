"""Deterministic collector doubles.

They exist **only** for automated tests (and for a future explicit demo mode).
They never touch the network and their output is always marked as synthetic, so
a synthetic run cannot be mistaken for a real Bilibili collection.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.collectors.base import (
    AccountCapability, Capabilities, CollectorError, Credential, DiscoveryItem,
    DiscoveryQuery, DiscoveryResult, Locator, Page,
    QrLogin, QrPoll, RemoteSource, SubtitleResult, SubtitleTrack, subtitle_segments,
)

SYNTHETIC_NOTICE = '【合成示例】测试替身返回，非真实B站内容'


class FakeBilibiliCollector:
    """Configurable in-process double for the Bilibili contract."""

    platform = 'bilibili'

    def __init__(self, *, subtitles='available', account='connected', fail_at=None,
                 fail_code='UPSTREAM_ERROR', on_fetch=None, title=None,
                 qr='success', qr_polls_before_confirm=2, page_count=1,
                 discovery_items=None, discover_pages=1, on_discover=None,
                 fail_discover_page=None, subtitle_languages=('zh-CN',)):
        self.subtitles = subtitles
        self.account = account
        self.fail_at = fail_at
        self.fail_code = fail_code
        self.on_fetch = on_fetch
        self.title = title or f'{SYNTHETIC_NOTICE}：单链接采集'
        self.qr = qr
        self.qr_polls_before_confirm = qr_polls_before_confirm
        self.qr_polls = 0
        self.page_count = max(1, int(page_count))
        self.discovery_items = discovery_items
        self.discover_pages = max(1, int(discover_pages))
        self.on_discover = on_discover
        self.fail_discover_page = fail_discover_page
        self.subtitle_languages = tuple(subtitle_languages)
        self.calls: list[str] = []

    def capabilities(self) -> Capabilities:
        return Capabilities(
            platform='bilibili', label='哔哩哔哩（测试替身）', single_url=True, metadata=True,
            subtitles=True, keyword_discovery=True, scheduling=True, qr_login=True,
            credential_required_for=('字幕清单',), max_requests_per_job=3,
            max_discovery_pages=3, notes=(SYNTHETIC_NOTICE,))

    def parse_locator(self, url: str) -> Locator:
        from app.collectors.bilibili import BilibiliCollector
        return BilibiliCollector().parse_locator(url)

    def _maybe_fail(self, stage: str):
        if self.fail_at == stage:
            raise CollectorError(self.fail_code, f'测试替身在 {stage} 阶段失败', False)
        if self.on_fetch:
            self.on_fetch(stage)

    def fetch_source(self, locator: Locator, credential: Credential | None = None) -> RemoteSource:
        self.calls.append('fetch_source')
        self._maybe_fail('fetch_source')
        pages = tuple(
            Page(page_no=number, cid=f'cid-{locator.video_id}-{number}',
                 part_title=f'合成分P {number}', duration_seconds=120)
            for number in range(1, self.page_count + 1))
        page = next((item for item in pages if item.page_no == locator.page), None)
        if page is None:
            raise CollectorError('SOURCE_PAGE_NOT_FOUND',
                                 f'该视频只有 {len(pages)} 个分P，找不到第 {locator.page} P', False,
                                 {'available_pages': len(pages)})
        return RemoteSource(
            platform='bilibili', external_id=f'{locator.video_id}:p{locator.page}',
            canonical_url=locator.canonical_url, title=self.title,
            author_name='虚构UP主', author_external_id='10000',
            published_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
            duration_seconds=120, availability='available', pages=pages,
            metadata={'schema_version': 'source.v1', 'collector': 'bilibili.fake.v1',
                      'bvid': locator.video_id, 'page': locator.page,
                      'page_count': len(pages),
                      'parts': [{'page_no': p.page_no, 'cid': p.cid, 'part_title': p.part_title,
                                 'duration_seconds': p.duration_seconds} for p in pages],
                      'video_cid': page.cid, 'synthetic': True, 'subtitle_status': 'unknown',
                      'desc': f'{SYNTHETIC_NOTICE}：合成视频简介', 'aid': 0})

    def fetch_subtitles(self, locator: Locator, source: RemoteSource,
                        credential: Credential | None = None, *,
                        languages: tuple[str, ...] | None = None,
                        language_policy: str = 'prefer') -> SubtitleResult:
        self.calls.append('fetch_subtitles')
        self._maybe_fail('fetch_subtitles')
        if self.subtitles in ('login_required', 'credential_gated') and not credential:
            return SubtitleResult(status='login_required', reason_code='CREDENTIAL_REQUIRED',
                                  detail='测试替身：需要登录态')
        if self.subtitles == 'missing':
            return SubtitleResult(status='missing', reason_code='NO_SUBTITLE_TRACK',
                                  detail='测试替身：缺字幕')
        if languages and language_policy == 'require' and not any(
                language.startswith(tuple(languages)) for language in self.subtitle_languages):
            return SubtitleResult(
                status='unavailable', reason_code='LANGUAGE_MISMATCH',
                tracks=(SubtitleTrack(language=self.subtitle_languages[0], label='合成字幕'),),
                detail='测试替身：语言不匹配')
        cues = [
            {'text': f'{SYNTHETIC_NOTICE}。第一句合成字幕。', 'start_ms': 0, 'end_ms': 2000},
            {'text': '第二句合成字幕，仅用于验证解析与证据定位。', 'start_ms': 2000, 'end_ms': 5000},
        ]
        segments = subtitle_segments(cues)
        return SubtitleResult(
            status='available', segments=segments,
            content='\n'.join(segment['text'] for segment in segments),
            language='zh-CN', format='json',
            tracks=(SubtitleTrack(language='zh-CN', label='中文（合成）', url='synthetic://caption',
                                  machine=False),),
            detail='测试替身：合成字幕')

    def fetch_pinned_comments(self, locator: Locator, *, aid=None,
                              credential: Credential | None = None) -> list[str]:
        self.calls.append('fetch_pinned_comments')
        self._maybe_fail('fetch_pinned_comments')
        return []

    def discover(self, query: DiscoveryQuery, *, credential: Credential | None = None) -> DiscoveryResult:
        self.calls.append(f'discover:{query.page}')
        if self.fail_discover_page == query.page:
            raise CollectorError(self.fail_code, f'测试替身在发现第 {query.page} 页失败', False)
        if self.on_discover:
            self.on_discover(query.page)
        if query.page > self.discover_pages:
            return DiscoveryResult(status='empty', page=query.page, has_more=False,
                                   detail='测试替身：没有更多页')
        raw_items = self.discovery_items
        if raw_items is None:
            raw_items = default_discovery_items(query.page)
        items = tuple(DiscoveryItem(
            platform='bilibili', external_id=item['bvid'], video_id=item['bvid'],
            title=item['title'], author_name=item.get('author_name', '虚构UP主'),
            author_external_id='10000',
            published_at=item.get('published_at', datetime(2026, 1, 1, tzinfo=timezone.utc)),
            duration_seconds=item.get('duration_seconds', 120), page=1,
            metadata={'schema_version': 'discovery.v1', 'collector': 'bilibili.fake.v1',
                      'synthetic': True}) for item in raw_items)
        return DiscoveryResult(status='ok', items=items, page=query.page,
                               has_more=query.page < self.discover_pages,
                               detail=f'测试替身：第 {query.page} 页 {len(items)} 条')

    def check_account(self, credential: Credential | None = None) -> AccountCapability:
        self.calls.append('check_account')
        now = datetime.now(timezone.utc)
        if self.account == 'connected':
            return AccountCapability(platform='bilibili', status='connected', checked_at=now,
                                     display_name='合成账号', user_id='10000',
                                     subtitle_capability=True, detail=SYNTHETIC_NOTICE)
        if self.account == 'not_configured':
            return AccountCapability(platform='bilibili', status='not_configured', checked_at=now,
                                     subtitle_capability=False, detail=SYNTHETIC_NOTICE)
        return AccountCapability(platform='bilibili', status='expired', checked_at=now,
                                 error_code='CREDENTIAL_EXPIRED', subtitle_capability=False,
                                 detail=SYNTHETIC_NOTICE)

    def start_qr_login(self) -> QrLogin:
        self.calls.append('start_qr_login')
        self._maybe_fail('start_qr_login')
        return QrLogin(url='https://example.test/synthetic-qr', key='synthetic-qrcode-key',
                       expires_in=180)

    def poll_qr_login(self, key: str) -> QrPoll:
        self.calls.append('poll_qr_login')
        self.qr_polls += 1
        if self.qr == 'expired':
            return QrPoll(status='expired', message='测试替身：二维码已过期')
        if self.qr == 'risk':
            return QrPoll(status='error', message='测试替身：风控', error_code='RISK_CONTROL')
        if self.qr == 'no_credential':
            raise CollectorError('QR_NO_CREDENTIAL', '测试替身：没有返回凭证', True)
        if self.qr_polls < self.qr_polls_before_confirm:
            return QrPoll(status='waiting_scan', message='测试替身：等待扫码')
        if self.qr_polls == self.qr_polls_before_confirm:
            return QrPoll(status='scanned', message='测试替身：已扫码待确认')
        return QrPoll(status='confirmed', message='测试替身：扫码确认',
                      cookies={'SESSDATA': 'fake-qr-session-value'})


def synthetic_bvid(page: int, index: int) -> str:
    """A deterministic, format-valid BV id that can never resolve upstream."""
    return f'BV1fk{int(page) % 10}{int(index):02d}0000'


def default_discovery_items(page: int = 1) -> list[dict]:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        {'bvid': synthetic_bvid(page, index),
         'title': f'{SYNTHETIC_NOTICE}：关键词候选 {page}-{index}',
         'author_name': '虚构UP主',
         'published_at': base.replace(day=max(1, 20 - (page - 1) * 3 - index)),
         'duration_seconds': 120 + index}
        for index in range(1, 4)
    ]
