"""Bilibili collector (M2 Phase 1 single link, M2 Phase 2 bounded discovery).

Scope: one user-supplied video link -> public metadata -> caption track (only when
the user provided a local login cookie), plus a bounded keyword-search page used
by an explicitly authorised, user-configured discovery source. No scheduling
inside the adapter, no video download, no transcription, no automatic interaction.

Policy constraints implemented here (see ``docs/bilibili-api-policy.md``):

* at most ``MAX_REQUESTS`` upstream calls per job instance;
* a minimum interval between calls, shared across threads;
* one delayed retry for timeout/5xx only, never for 412/429/-352;
* discovery is one page per call, capped by ``MAX_DISCOVERY_PAGES``, and never
  follows uploaders, related-video chains or playlists;
* credentials are passed as an opaque ``Credential`` and never formatted into
  messages, logs or error details.

These are undocumented web endpoints, not an authorised open-platform API, so
every failure is surfaced with a stable code instead of being retried away.
"""

from __future__ import annotations

import html
import os
import re
import threading
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

import httpx

from bilibili_api.utils.network import OE, _enc_wbi

from app.collectors.base import (
    AccountCapability, Capabilities, CollectorError, Credential, DiscoveryItem,
    DiscoveryQuery, DiscoveryResult, Locator, Page, QrLogin, QrPoll, RemoteSource,
    SubtitleResult, SubtitleTrack, subtitle_segments,
)

COLLECTOR_VERSION = 'bilibili.v2'
API = 'https://api.bilibili.com'
PASSPORT = 'https://passport.bilibili.com'
DEFAULT_ORIGIN = 'https://www.bilibili.com'
MAX_REQUESTS = 5
MAX_DISCOVERY_PAGES = 3
DISCOVERY_PAGE_SIZE = 20
MIN_INTERVAL_SECONDS = 1.5
DEFAULT_TIMEOUT = 8.0
# Web login lives on the passport host; using api.bilibili.com returns HTTP 404.
QR_GENERATE = f'{PASSPORT}/x/passport-login/web/qrcode/generate'
QR_POLL = f'{PASSPORT}/x/passport-login/web/qrcode/poll'
QR_TTL_SECONDS = 180
SEARCH_ENDPOINT = f'{API}/x/web-interface/search/type'
USER_AGENT = 'FishingNet/0.1 (local single-user collector; contact: local operator)'
BV_PATTERN = re.compile(r'^BV[0-9A-Za-z]{10}$')
_BV_IN_PATH = re.compile(r'/video/(BV[0-9A-Za-z]{10})(?:/|$)')
_MARKUP = re.compile(r'<[^>]+>')
_DURATION_PART = re.compile(r'^\d+(?::\d{1,2}){0,2}$')
_HOSTS = {'www.bilibili.com', 'bilibili.com', 'm.bilibili.com'}
_SHORT_HOSTS = {'b23.tv', 'bili2233.cn'}

_PACE_LOCK = threading.Lock()
_LAST_CALL = [0.0]


def _pace(interval: float, sleep, clock) -> None:
    with _PACE_LOCK:
        remaining = interval - (clock() - _LAST_CALL[0])
        if remaining > 0:
            sleep(remaining)
        _LAST_CALL[0] = clock()


class BilibiliCollector:
    platform = 'bilibili'

    def __init__(self, *, transport=None, min_interval: float | None = None,
                 timeout: float = DEFAULT_TIMEOUT, sleep=time.sleep, clock=time.monotonic):
        self._transport = transport
        if min_interval is None:
            try:
                min_interval = float(os.environ.get('FISHINGNET_BILIBILI_MIN_INTERVAL',
                                                    MIN_INTERVAL_SECONDS))
            except ValueError:
                min_interval = MIN_INTERVAL_SECONDS
        self.min_interval = max(0.0, float(min_interval))
        self.timeout = timeout
        self._sleep = sleep
        self._clock = clock
        self.requests = 0
        self._client_instance = None
        self._wbi_key = None
        self._wbi_key_at = 0.0
        self._buvid3 = None
        self._buvid4 = None

    # ------------------------------------------------------------------ http
    def _client(self):
        if self._client_instance is None:
            self._client_instance = httpx.Client(
                timeout=self.timeout, follow_redirects=False, transport=self._transport,
                headers={'Accept': 'application/json', 'Referer': DEFAULT_ORIGIN + '/'})
        return self._client_instance

    def close(self):
        if self._client_instance is not None:
            self._client_instance.close()
            self._client_instance = None

    def fetch_mentions(self, *, credential, cursor=None):
        """One bounded page of account @ notifications, with no mark-read/write calls."""
        if not credential:
            raise CollectorError('CREDENTIAL_EXPIRED', '读取 @ 通知需要 B站登录态', False)
        params = {'platform': 'web', 'build': 0, 'mobi_app': 'web'}
        cursor = cursor or {}
        if cursor.get('id'):
            params['id'] = cursor['id']
        if cursor.get('time'):
            params['reply_time'] = cursor['time']
        payload = self._request_json(f'{API}/x/msgfeed/at', params=params, credential=credential, retry=False)
        if payload.get('code') != 0:
            raise self._code_error(payload.get('code'))
        data = payload.get('data')
        if not isinstance(data, dict) or not isinstance(data.get('items'), list) or len(data['items']) > 100:
            raise CollectorError('UPSTREAM_INVALID_JSON', '@ 通知格式异常，已停止本轮', False)
        if any(not isinstance(item, dict) or not str(item.get('id') or '').isdigit() for item in data['items']):
            raise CollectorError('UPSTREAM_INVALID_JSON', '@ 通知缺少有效标识', False)
        return data

    def resolve_mention_video(self, notification, *, credential):
        from app.domain.summaries import mention_video
        bvid=mention_video(notification)
        if bvid:
            return bvid
        uri=urlsplit(str((notification.get('item') or {}).get('uri') or ''))
        match=None
        if uri.hostname in ('www.bilibili.com','bilibili.com'):
            match=re.fullmatch(r'/(?:opus|dynamic)/([0-9]+)/?',uri.path)
        elif uri.hostname=='t.bilibili.com':
            match=re.fullmatch(r'/([0-9]+)/?',uri.path)
        if not match:
            return None
        response=self._request_json(f'{API}/x/polymer/web-dynamic/v1/detail',params={'id':match.group(1)},credential=credential,retry=False)
        if response.get('code')!=0:
            raise self._code_error(response.get('code'))
        item=(response.get('data') or {}).get('item') or {}
        for candidate in (item,item.get('orig') or {}):
            archive=(((candidate.get('modules') or {}).get('module_dynamic') or {}).get('major') or {}).get('archive') or {}
            bvid=archive.get('bvid')
            if isinstance(bvid,str) and BV_PATTERN.fullmatch(bvid):
                return bvid
        return None

    def _request(self, url: str, params=None, *, credential: Credential | None = None,
                 retry: bool = True):
        if self.requests >= MAX_REQUESTS:
            raise CollectorError('REQUEST_BUDGET_EXCEEDED',
                                 '本次采集已达到上游请求上限，已停止；请稍后由您手动重试', False)
        self.requests += 1
        _pace(self.min_interval, self._sleep, self._clock)
        cookies = dict(credential.values) if credential else {}
        # Merge buvid fingerprint cookies (needed for search endpoint).
        if self._buvid3 and self._buvid4:
            cookies.setdefault('buvid3', self._buvid3)
            cookies.setdefault('buvid4', self._buvid4)
        headers = {'User-Agent': USER_AGENT}
        try:
            client = self._client()
            # Authentication is explicit per request; never inherit Set-Cookie
            # values from a previous platform response into a CDN request.
            client.cookies.clear()
            response = client.get(url, params=params, headers=headers,
                                          cookies=cookies, timeout=self.timeout)
        except httpx.TimeoutException:
            if retry:
                self._sleep(2.0)
                return self._request(url, params, credential=credential, retry=False)
            raise CollectorError('NETWORK_TIMEOUT', '连接 B站超时，已停止本次采集', True)
        except httpx.HTTPError:
            raise CollectorError('NETWORK_ERROR', '无法连接 B站，请检查本机网络后手动重试', True)

        status = response.status_code
        if status == 412:
            raise CollectorError('RISK_CONTROL',
                                 'B站风控拦截（HTTP 412），已停止本次采集；请勿提高频率或更换账号规避',
                                 False, {'upstream_status': 412})
        if status == 429:
            raise CollectorError('RATE_LIMITED', 'B站限流（HTTP 429），请稍后手动重试', True)
        if status >= 500:
            if retry:
                self._sleep(2.0)
                return self._request(url, params, credential=credential, retry=False)
            raise CollectorError('UPSTREAM_UNAVAILABLE', 'B站服务暂时不可用，已停止本次采集', True)
        if status >= 400:
            raise CollectorError('UPSTREAM_HTTP_ERROR', f'B站返回 HTTP {status}，已停止本次请求',
                                 False, {'upstream_status': status})
        try:
            payload = response.json()
        except ValueError:
            raise CollectorError('UPSTREAM_INVALID_JSON', 'B站返回内容不是有效 JSON，已停止本次采集', False)
        if not isinstance(payload, dict):
            raise CollectorError('UPSTREAM_INVALID_JSON', 'B站返回结构不符合预期，已停止本次采集', False)
        code = payload.get('code')
        if code not in (0, None):
            raise self._code_error(code)
        return payload, response

    def _request_json(self, url: str, params=None, *, credential: Credential | None = None,
                      retry: bool = True):
        return self._request(url, params, credential=credential, retry=retry)[0]

    def _get_wbi_key(self, credential: Credential | None = None) -> str:
        """Fetch and cache the WBI mixin key from /x/web-interface/nav.

        The key is cached for 30 minutes since B站 rotates it infrequently.
        Uses the same OE table and extraction logic as the subtitle adapter.
        """
        now = self._clock()
        if self._wbi_key and (now - self._wbi_key_at) < 1800:
            return self._wbi_key
        nav = self._request_json(f'{API}/x/web-interface/nav', credential=credential, retry=False)
        images = (nav.get('data') or {}).get('wbi_img')
        if not isinstance(images, dict):
            raise CollectorError('UPSTREAM_INVALID_JSON',
                                 'B站未返回有效 WBI 签名参数，已停止本次发现', False)
        parts = []
        for key in ('img_url', 'sub_url'):
            url_val = images.get(key)
            if not url_val:
                raise CollectorError('UPSTREAM_INVALID_JSON',
                                     'B站 WBI 签名参数不完整，已停止本次发现', False)
            part = urlsplit(url_val).path.rsplit('/', 1)[-1].split('.')[0]
            if not re.fullmatch(r'[0-9a-fA-F]{32}', part):
                raise CollectorError('UPSTREAM_INVALID_JSON',
                                     'B站 WBI 签名参数格式异常，已停止本次发现', False)
            parts.append(part)
        combined = ''.join(parts)
        key = ''.join(combined[i] for i in OE)[:32]
        self._wbi_key = key
        self._wbi_key_at = now
        return key

    def _sign_wbi(self, params: dict, credential: Credential | None = None) -> dict:
        """Sign request parameters with WBI (w_rid + wts)."""
        key = self._get_wbi_key(credential)
        return _enc_wbi(params, key)

    def _get_buvid(self, credential: Credential | None = None) -> dict:
        """Fetch and cache buvid3/buvid4 device fingerprint cookies.

        B站's search endpoint requires these fingerprint cookies; without them
        the server returns HTTP 412 even with valid WBI signing.
        """
        if self._buvid3 and self._buvid4:
            return {'buvid3': self._buvid3, 'buvid4': self._buvid4}
        try:
            payload = self._request_json(f'{API}/x/frontend/finger/spi',
                                         credential=credential, retry=False)
        except CollectorError:
            return {}
        data = (payload.get('data') or {})
        b3 = data.get('b_3')
        b4 = data.get('b_4')
        if b3 and b4:
            self._buvid3 = b3
            self._buvid4 = b4
            return {'buvid3': b3, 'buvid4': b4}
        return {}

    def _code_error(self, code) -> CollectorError:
        mapping = {
            -101: ('CREDENTIAL_EXPIRED', 'B站登录态已失效，请在“平台账号”页重新提供本机 Cookie', False),
            -400: ('SOURCE_NOT_FOUND', 'B站没有找到该视频，请检查链接或视频是否已删除', False),
            -404: ('SOURCE_NOT_FOUND', 'B站没有找到该视频，请检查链接或视频是否已删除', False),
            -403: ('PERMISSION_REQUIRED', '当前账号无权读取该内容，已停止本次采集', False),
            -412: ('RISK_CONTROL', 'B站风控拦截（-412），已停止本次采集；请稍后由您手动重试', False),
            -352: ('RISK_CONTROL', 'B站风控拦截（-352），已停止本次采集；请稍后由您手动重试', False),
            -509: ('RATE_LIMITED', 'B站限流，请稍后手动重试', True),
            -799: ('RATE_LIMITED', 'B站请求过于频繁，请稍后手动重试', True),
        }
        known = mapping.get(code)
        if known:
            return CollectorError(known[0], known[1], known[2], {'upstream_code': code})
        return CollectorError('UPSTREAM_ERROR', 'B站返回未预期的错误，已停止本次采集', False,
                              {'upstream_code': code})

    # ------------------------------------------------------------ contract
    def capabilities(self) -> Capabilities:
        return Capabilities(
            platform=self.platform, label='哔哩哔哩', single_url=True, metadata=True, subtitles=True,
            keyword_discovery=True, scheduling=True, qr_login=True,
            credential_required_for=('字幕清单', '登录态检查'),
            max_requests_per_job=5, max_discovery_pages=MAX_DISCOVERY_PAGES,
            notes=('网页内部接口，非官方开放平台授权；结果受平台风控影响',
                   '字幕需要用户在本机提供的登录 Cookie，匿名只能读取公开元数据',
                   '关键词发现只读取搜索结果的公开元数据页，每轮最多 3 页；不遍历 UP 主、不抓取合集、不下载视频',
                   '搜索接口可能要求签名或登录态；本项目的真实搜索请求尚未验证，失败会如实显示错误码',
                   '扫码登录使用网页登录流程，不是开放平台 OAuth；二维码与轮询都只在本机进行',
                   '不做视频下载、本地转写或自动评论/@/回复'))

    def parse_locator(self, url: str) -> Locator:
        raw = (url or '').strip()
        if not raw:
            raise CollectorError('INVALID_LOCATOR', '请输入 B站视频链接', False)
        if len(raw) > 500:
            raise CollectorError('INVALID_LOCATOR', '链接过长，请粘贴标准视频地址', False)
        try:
            parsed = urlsplit(raw)
        except ValueError:
            raise CollectorError('INVALID_LOCATOR', '链接格式无法识别', False)
        host = (parsed.hostname or '').lower()
        if parsed.scheme not in ('http', 'https'):
            raise CollectorError('INVALID_LOCATOR', '请提供以 https:// 开头的完整链接', False)
        if host in _SHORT_HOSTS:
            raise CollectorError(
                'UNSUPPORTED_LOCATOR',
                '暂不支持 b23.tv 短链。请在浏览器打开短链后，复制包含 BV 号的完整视频地址再导入', False)
        if host not in _HOSTS:
            raise CollectorError('UNSUPPORTED_LOCATOR', '目前只接受 bilibili.com 的视频链接', False)
        match = _BV_IN_PATH.search(parsed.path or '')
        if not match:
            if '/bangumi/' in (parsed.path or ''):
                raise CollectorError('UNSUPPORTED_LOCATOR', '暂不支持番剧/影视链接，本阶段只处理普通投稿视频', False)
            if '/medialist/' in (parsed.path or '') or '/playlist/' in (parsed.path or ''):
                raise CollectorError('UNSUPPORTED_LOCATOR', '请提供单个视频链接，而不是播放列表', False)
            raise CollectorError('UNSUPPORTED_LOCATOR', '链接中没有找到 BV 号，请使用 /video/BV… 形式的地址', False)
        bvid = match.group(1)
        if not BV_PATTERN.match(bvid):
            raise CollectorError('INVALID_LOCATOR', 'BV 号格式不正确', False)
        page_raw = (parse_qs(parsed.query).get('p') or ['1'])[0]
        if not page_raw.isdigit() or not 1 <= int(page_raw) <= 999:
            raise CollectorError('INVALID_LOCATOR', '分P参数必须是 1 到 999 的整数', False)
        page = int(page_raw)
        canonical = f'{DEFAULT_ORIGIN}/video/{bvid}' + (f'?p={page}' if page > 1 else '')
        return Locator(platform=self.platform, video_id=bvid, page=page, canonical_url=canonical, raw=raw)

    def fetch_source(self, locator: Locator, credential: Credential | None = None) -> RemoteSource:
        payload = self._request_json(f'{API}/x/web-interface/view', {'bvid': locator.video_id},
                                     credential=credential)
        data = payload.get('data') or {}
        title = (data.get('title') or '').strip()
        if not title:
            raise CollectorError('UPSTREAM_INVALID_JSON', 'B站元数据缺少标题，已停止本次采集', False)
        pages = tuple(
            Page(page_no=int(item.get('page') or index + 1), cid=str(item.get('cid') or ''),
                 part_title=(item.get('part') or '').strip(),
                 duration_seconds=int(item.get('duration') or 0))
            for index, item in enumerate(data.get('pages') or []) if item.get('cid'))
        if not pages:
            pages = (Page(page_no=1, cid=str(data.get('cid') or ''),
                          part_title=title, duration_seconds=int(data.get('duration') or 0)),)
        page = next((p for p in pages if p.page_no == locator.page), None)
        if page is None:
            raise CollectorError('SOURCE_PAGE_NOT_FOUND',
                                 f'该视频只有 {len(pages)} 个分P，找不到第 {locator.page} P', False,
                                 {'available_pages': len(pages)})
        owner = data.get('owner') or {}
        published = None
        if data.get('pubdate'):
            try:
                published = datetime.fromtimestamp(int(data['pubdate']), tz=timezone.utc)
            except (TypeError, ValueError, OSError):
                published = None
        metadata = {
            'schema_version': 'source.v1',
            'collector': COLLECTOR_VERSION,
            'bvid': data.get('bvid') or locator.video_id,
            'page': page.page_no,
            'page_count': len(pages),
            'video_cid': page.cid,
            'part_title': page.part_title,
            'duration_seconds': page.duration_seconds or int(data.get('duration') or 0),
            'parts': [{'page_no': item.page_no, 'cid': item.cid, 'part_title': item.part_title,
                       'duration_seconds': item.duration_seconds} for item in pages[:100]],
            'subtitle_status': 'unknown',
            'rights_basis': 'user_provided_link',
            'desc': re.sub(_MARKUP, ' ', str(data.get('desc') or '')).strip()[:5000],
            'aid': int(data.get('aid') or 0),
        }
        external_id = f"{data.get('bvid') or locator.video_id}:p{page.page_no}"
        return RemoteSource(
            platform=self.platform, external_id=external_id, canonical_url=locator.canonical_url,
            title=title[:200], author_name=(owner.get('name') or None),
            author_external_id=str(owner.get('mid') or '') or None, published_at=published,
            duration_seconds=metadata['duration_seconds'], availability='available',
            pages=pages, metadata=metadata)

    def fetch_subtitles(self, locator: Locator, source: RemoteSource,
                        credential: Credential | None = None, *,
                        languages: tuple[str, ...] | None = None,
                        language_policy: str = 'prefer') -> SubtitleResult:
        if not credential or not credential.get('SESSDATA'):
            return SubtitleResult(
                status='login_required', reason_code='CREDENTIAL_REQUIRED',
                detail=('B站字幕清单需要登录态。未提供本机 Cookie 时无法区分“视频没有字幕”和'
                        '“匿名不可见字幕”，因此只记录为“需要登录”，不标记为缺字幕。'))
        page = source.page(locator.page)
        if page is None or not page.cid:
            raise CollectorError('UPSTREAM_INVALID_JSON', '缺少分P标识（cid），无法读取字幕清单', False)
        from app.collectors.bilibili_subtitles import get_subtitle
        subtitle = get_subtitle(self, locator.video_id, page.cid, credential)
        raw_tracks = subtitle.get('subtitles') or []
        tracks = tuple(
            SubtitleTrack(language=(item.get('lan') or 'unknown').strip(),
                          label=(item.get('lan_doc') or '').strip(),
                          url=(item.get('subtitle_url') or '').strip(),
                          machine=bool(item.get('ai_type')))
            for item in raw_tracks if item.get('subtitle_url'))
        if not tracks:
            return SubtitleResult(
                status='missing', reason_code='NO_SUBTITLE_TRACK', tracks=(),
                detail=('本次接口未返回可用字幕轨，原因尚未确认；这不能证明播放器没有字幕。'
                        '未取得字幕正文，不会据此生成任何分析。'))
        preferred = tuple(language for language in (languages or ()) if language)
        matching = tuple(t for t in tracks if any(_language_matches(t.language, (p,)) for p in preferred))
        if preferred and language_policy == 'require' and not matching:
            available = '、'.join(sorted({t.language for t in tracks}))
            return SubtitleResult(
                status='unavailable', reason_code='LANGUAGE_MISMATCH', tracks=tracks,
                detail=(f'该视频只有 {available} 字幕，不符合这个关键词来源要求的语言'
                        f'（{", ".join(preferred)}）；已记录为语言不匹配，不会生成内容版本。'))
        track = self._pick_track(matching or tracks, preferred)
        url = track.url
        if url.startswith('//'):
            url = 'https:' + url
        elif url.startswith('http://'):
            url = 'https://' + url[len('http://'):]
        payload = self._request_json(url, None, credential=None)
        body = payload.get('body') if isinstance(payload, dict) else None
        if not isinstance(body, list) or not body:
            raise CollectorError('SUBTITLE_UNREADABLE', '字幕正文结构不符合预期，已停止本次采集', False)
        cues = []
        for item in body:
            text = (item.get('content') or '').strip()
            if not text:
                continue
            try:
                start = round(float(item.get('from') or 0) * 1000)
                end = round(float(item.get('to') or 0) * 1000)
            except (TypeError, ValueError):
                raise CollectorError('SUBTITLE_UNREADABLE', '字幕时间戳格式异常，已停止本次采集', False)
            if end < start or start < 0:
                raise CollectorError('SUBTITLE_UNREADABLE', '字幕时间戳不合法，已停止本次采集', False)
            cues.append({'text': text, 'start_ms': start, 'end_ms': end})
        segments = subtitle_segments(cues)
        if not segments:
            raise CollectorError('SUBTITLE_UNREADABLE', '字幕正文为空，已停止本次采集', False)
        content = '\n'.join(segment['text'] for segment in segments)
        return SubtitleResult(
            status='available', segments=segments, content=content, language=track.language,
            format='json', tracks=tracks,
            detail=f"使用字幕轨：{track.label or track.language}{'（机器生成）' if track.machine else ''}")

    def fetch_pinned_comments(self, locator: Locator, *, aid: int | None = None,
                              credential: Credential | None = None) -> list[str]:
        """One bounded read of the UP-pinned (置顶) comment for summary context.

        Best-effort by design: callers catch ``CollectorError`` and continue with
        an empty list, since the caption is the authoritative content and a pinned
        comment is only extra context. ``type=1`` is a video comment thread.
        """
        if not aid:
            return []
        payload = self._request_json(f'{API}/x/v2/reply', {'type': 1, 'oid': aid, 'ps': 20, 'sort': 2},
                                      credential=credential, retry=False)
        data = payload.get('data') if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            return []
        comments: list[str] = []
        for key in ('top', 'upper'):
            comment = data.get(key)
            if isinstance(comment, dict):
                text = self._comment_text(comment)
                if text and text not in comments:
                    comments.append(text)
        for reply in (data.get('replies') or []):
            if isinstance(reply, dict) and reply.get('is_top'):
                text = self._comment_text(reply)
                if text and text not in comments:
                    comments.append(text)
        return comments[:5]

    @staticmethod
    def _comment_text(comment: dict) -> str:
        member = comment.get('member') or {}
        name = str(member.get('uname') or '').strip()
        content = re.sub(_MARKUP, ' ', str((comment.get('content') or {}).get('message') or '')).strip()
        content = re.sub(r'\s+', ' ', content)
        if not content:
            return ''
        return f'{name}：{content}' if name else content

    # ----------------------------------------------------------- discovery
    def discover(self, query: DiscoveryQuery, *, credential: Credential | None = None) -> DiscoveryResult:
        """Read one keyword-search page with WBI signing. Never expands beyond one page.

        Uses the library's standard WBI signer (same as subtitle queries) to sign
        the search parameters. The search endpoint requires WBI signing; without it
        the platform returns HTTP 412. No browser impersonation, WBI2 or device
        activation is used.
        """
        if not query.keywords:
            raise CollectorError('INVALID_QUERY', '关键词来源没有配置关键词', False)
        if not 1 <= int(query.page) <= MAX_DISCOVERY_PAGES:
            raise CollectorError('DISCOVERY_PAGE_LIMIT', '关键词发现每轮最多读取 3 页', False,
                                 {'max_pages': MAX_DISCOVERY_PAGES})
        size = max(1, min(int(query.page_size or DISCOVERY_PAGE_SIZE), DISCOVERY_PAGE_SIZE))
        params = {
            'search_type': 'video', 'keyword': query.text, 'page': int(query.page),
            'page_size': size,
            'order': 'pubdate' if query.order == 'pubdate' else 'totalrank',
        }
        try:
            # Ensure buvid fingerprint cookies are available (search requires them).
            self._get_buvid(credential)
            signed_params = self._sign_wbi(params, credential)
            payload = self._request_json(SEARCH_ENDPOINT, signed_params, credential=credential)
        except CollectorError as exc:
            raise self._discovery_error(exc)
        data = payload.get('data')
        if not isinstance(data, dict):
            raise CollectorError('UPSTREAM_INVALID_JSON', 'B站搜索返回结构不符合预期，已停止本次发现', False)
        raw_items = data.get('result')
        if raw_items is None:
            raw_items = []
        if not isinstance(raw_items, list):
            raise CollectorError('UPSTREAM_INVALID_JSON', 'B站搜索返回结构不符合预期，已停止本次发现', False)
        items: list[DiscoveryItem] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            if raw.get('type') not in (None, 'video'):
                continue
            bvid = str(raw.get('bvid') or '').strip()
            if not BV_PATTERN.match(bvid):
                continue
            title = _clean_text(raw.get('title'))
            if not title:
                continue
            items.append(DiscoveryItem(
                platform=self.platform, external_id=bvid, video_id=bvid, title=title[:200],
                author_name=(_clean_text(raw.get('author')) or None),
                author_external_id=str(raw.get('mid') or '') or None,
                published_at=_timestamp(raw.get('pubdate')),
                duration_seconds=_duration(raw.get('duration')),
                page=1,
                metadata={'schema_version': 'discovery.v1', 'collector': COLLECTOR_VERSION,
                          'rights_basis': 'user_keyword_query', 'order': params['order'],
                          'query': query.text, 'search_page': int(query.page)}))
        has_more = bool(data.get('has_more')) if isinstance(data.get('has_more'), bool) else (
            len(items) >= size and int(query.page) < MAX_DISCOVERY_PAGES)
        if not items:
            return DiscoveryResult(status='empty', items=(), page=int(query.page),
                                   has_more=False, detail='该页没有匹配到视频')
        return DiscoveryResult(status='ok', items=tuple(items), page=int(query.page),
                               has_more=has_more,
                               detail=f'第 {query.page} 页返回 {len(items)} 条候选')

    def _discovery_error(self, exc: CollectorError) -> CollectorError:
        if exc.code == 'PERMISSION_REQUIRED':
            return CollectorError(
                'DISCOVERY_DENIED',
                ('B站拒绝了关键词搜索请求（-403）。该网页搜索接口可能需要 WBI 签名或登录态，'
                 '本项目没有伪造签名或绕过风控，因此停止本次发现；请改用单链接导入。'),
                False, dict(exc.details))
        if exc.code == 'SOURCE_NOT_FOUND' and exc.details.get('upstream_code') == -400:
            return CollectorError('INVALID_QUERY', 'B站认为关键词不合法，已停止本次发现', False)
        return exc


    def check_account(self, credential: Credential | None = None) -> AccountCapability:
        now = datetime.now(timezone.utc)
        if not credential or not credential.get('SESSDATA'):
            return AccountCapability(
                platform=self.platform, status='not_configured', checked_at=now,
                metadata_capability=True, subtitle_capability=False,
                detail='未在本机提供 B站登录 Cookie：只能读取公开元数据，不能读取字幕清单。')
        try:
            payload = self._request_json(f'{API}/x/web-interface/nav', credential=credential,
                                         retry=False)
        except CollectorError as exc:
            status = 'expired' if exc.code == 'CREDENTIAL_EXPIRED' else (
                'unavailable' if exc.retryable else 'invalid')
            return AccountCapability(
                platform=self.platform, status=status, checked_at=now,
                metadata_capability=True, subtitle_capability=False,
                error_code=exc.code, detail=exc.message)
        data = payload.get('data') or {}
        if not data.get('isLogin'):
            return AccountCapability(
                platform=self.platform, status='expired', checked_at=now,
                metadata_capability=True, subtitle_capability=False,
                error_code='NOT_LOGGED_IN',
                detail='B站返回未登录：Cookie 可能已失效或在其他设备被退出，请重新提供。')
        return AccountCapability(
            platform=self.platform, status='connected', checked_at=now,
            display_name=(data.get('uname') or None),
            user_id=str(data.get('mid') or '') or None,
            metadata_capability=True, subtitle_capability=True,
            detail='登录态有效：可读取公开元数据与该账号可见的字幕清单。这不是平台 OAuth 授权。')

    def start_qr_login(self) -> QrLogin:
        """Create a web-login QR challenge. One upstream request."""
        payload = self._request_json(QR_GENERATE, None, retry=False)
        data = payload.get('data') or {}
        url = str(data.get('url') or '').strip()
        key = str(data.get('qrcode_key') or '').strip()
        if not url.startswith('https://') or not key:
            raise CollectorError('UPSTREAM_INVALID_JSON', 'B站没有返回有效的登录二维码，请重试', True)
        return QrLogin(url=url, key=key, expires_in=QR_TTL_SECONDS)

    def poll_qr_login(self, key: str) -> QrPoll:
        """Poll one QR challenge. The key and any returned cookie stay server-side."""
        payload, response = self._request(QR_POLL, {'qrcode_key': key}, retry=False)
        data = payload.get('data') or {}
        code = data.get('code')
        if code == 0:
            values = {name: value for name, value in response.cookies.items()
                      if name == 'SESSDATA' and value}
            if not values.get('SESSDATA'):
                raise CollectorError('QR_NO_CREDENTIAL',
                                     '扫码已确认，但 B站没有返回登录凭证；请刷新二维码后重试', True)
            return QrPoll(status='confirmed', cookies=values, message='扫码登录已确认')
        if code == 86101:
            return QrPoll(status='waiting_scan', message='等待手机扫码')
        if code == 86090:
            return QrPoll(status='scanned', message='已扫码，请在手机上确认登录')
        if code == 86038:
            return QrPoll(status='expired', message='二维码已过期，请刷新')
        return QrPoll(status='error', message='B站返回了未预期的扫码状态',
                      error_code=f'QR_UPSTREAM_{code}', retryable=False)

    @staticmethod
    def _pick_track(tracks, languages: tuple[str, ...] = ()) -> SubtitleTrack:
        preferred = tuple(language.lower() for language in (languages or ()) if language)

        def score(track: SubtitleTrack):
            language = (track.language or '').lower()
            rank = len(preferred)
            for index, wanted in enumerate(preferred):
                if _language_matches(language, (wanted,)):
                    rank = index
                    break
            if language.removeprefix('ai-').startswith('zh'):
                base = 0
            else:
                base = 1
            return (rank, base, 1 if track.machine else 0)

        return sorted(tracks, key=score)[0]


def _language_matches(language: str, candidates) -> bool:
    """True when ``language`` equals or extends one of the candidate tags."""
    # Bilibili uses ai-zh / ai-en for machine captions, not BCP-47 language tags.
    # Preserve the original track label, but match the underlying language.
    current = (language or '').strip().lower().removeprefix('ai-')
    if not current:
        return False
    for candidate in candidates:
        wanted = (candidate or '').strip().lower().removeprefix('ai-')
        if not wanted:
            continue
        if current == wanted or current.startswith(wanted + '-') or wanted.startswith(current + '-'):
            return True
    return False


def _clean_text(value) -> str:
    """Strip search-result markup (``<em class="keyword">``) and decode entities."""
    if value is None:
        return ''
    text = html.unescape(str(value))
    text = _MARKUP.sub('', text)
    return ' '.join(text.split()).strip()


def _timestamp(value) -> datetime | None:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _duration(value) -> int | None:
    """Parse ``mm:ss`` / ``h:mm:ss`` durations returned by the search endpoint."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if not _DURATION_PART.match(text):
        return None
    total = 0
    for part in text.split(':'):
        total = total * 60 + int(part)
    return total
