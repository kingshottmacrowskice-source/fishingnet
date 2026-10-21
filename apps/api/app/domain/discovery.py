"""Keyword-discovery configuration and planning rules (M2 Phase 2).

Pure functions only: no database, no network, no clock reads except where a
``now`` value is passed in. The worker owns transactions; this module owns the
rules that decide what a run is allowed to do.

Two invariants matter for the product:

* every candidate gets an explicit decision, including why it was skipped, so a
  run can be explained later without guessing;
* limits are enforced locally, not trusted to the upstream ordering, so a single
  run can never explode into more work than the user configured.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MAX_KEYWORDS = 5
MAX_KEYWORD_LENGTH = 40
MAX_TERMS = 20
MAX_TERM_LENGTH = 40
MAX_LANGUAGES = 5
MAX_TARGET_PAGES = 3
MAX_RESULTS_PER_RUN = 20
MAX_PAGES_PER_RUN = 3
MAX_REQUESTS_PER_RUN = 4
MAX_RESCAN_HOURS = 8760
MIN_INTERVAL_MINUTES = 60
MAX_INTERVAL_MINUTES = 10080
DEFAULT_LANGUAGES = ('zh-CN', 'zh-Hans')
DECISIONS = ('enqueued', 'refreshed', 'excluded_term', 'keyword_miss', 'duplicate',
             'over_limit', 'skipped_deleted')
_LANGUAGE_TAG = re.compile(r'^[a-z]{2,3}(-[a-z0-9]{2,8}){0,2}$')


class DiscoveryConfigError(ValueError):
    """Invalid discovery configuration; ``field`` names the offending input."""

    def __init__(self, field: str, message: str):
        self.field = field
        self.message = message
        super().__init__(message)


def normalize_text(value: str) -> str:
    """NFKC + casefold + collapsed whitespace, used for matching only."""
    text = unicodedata.normalize('NFKC', str(value or ''))
    return ' '.join(text.casefold().split())


def _clean_strings(values, *, field: str, limit: int, max_length: int,
                   required: bool = False) -> tuple[str, ...]:
    if values is None:
        values = []
    if not isinstance(values, (list, tuple)):
        raise DiscoveryConfigError(field, f'{field} 必须是字符串数组')
    cleaned: list[str] = []
    for raw in values:
        text = ' '.join(str(raw or '').split())
        if not text:
            continue
        if len(text) > max_length:
            raise DiscoveryConfigError(field, f'每个条目最多 {max_length} 个字符')
        key = normalize_text(text)
        if key and key not in {normalize_text(item) for item in cleaned}:
            cleaned.append(text)
    if required and not cleaned:
        raise DiscoveryConfigError(field, '关键词来源至少需要一个关键词')
    if len(cleaned) > limit:
        raise DiscoveryConfigError(field, f'最多允许 {limit} 个条目')
    return tuple(cleaned)


def _bound_int(value, *, field: str, low: int, high: int, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise DiscoveryConfigError(field, f'{field} 必须是整数')
    if not low <= value <= high:
        raise DiscoveryConfigError(field, f'{field} 必须在 {low} 到 {high} 之间')
    return value


def normalize_config(payload: dict) -> dict:
    """Validate an API payload and return the row-shaped configuration."""
    if not isinstance(payload, dict):
        raise DiscoveryConfigError('body', '请求内容必须是对象')
    name = ' '.join(str(payload.get('name') or '').split())
    if not name:
        raise DiscoveryConfigError('name', '请为关键词来源填写名称')
    if len(name) > 80:
        raise DiscoveryConfigError('name', '名称最多 80 个字符')
    platform = payload.get('platform') or 'bilibili'
    if platform != 'bilibili':
        raise DiscoveryConfigError('platform', '当前只支持 B站 关键词来源')
    keywords = _clean_strings(payload.get('keywords'), field='keywords',
                              limit=MAX_KEYWORDS, max_length=MAX_KEYWORD_LENGTH,
                              required=True)
    terms = _clean_strings(payload.get('exclude_terms'), field='exclude_terms',
                           limit=MAX_TERMS, max_length=MAX_TERM_LENGTH)
    raw_languages = payload.get('languages')
    if raw_languages is None:
        languages = DEFAULT_LANGUAGES
    else:
        cleaned = _clean_strings(raw_languages, field='languages',
                                 limit=MAX_LANGUAGES, max_length=12)
        # Keep the user's casing for display; matching always casefolds.
        languages = tuple(cleaned) or DEFAULT_LANGUAGES
        for tag in languages:
            if not _LANGUAGE_TAG.match(tag.lower()):
                raise DiscoveryConfigError('languages', f'语言标签不合法：{tag}')
    match_mode = payload.get('match_mode') or 'any'
    if match_mode not in ('any', 'all'):
        raise DiscoveryConfigError('match_mode', 'match_mode 只能是 any 或 all')
    language_policy = payload.get('language_policy') or 'prefer'
    if language_policy not in ('prefer', 'require'):
        raise DiscoveryConfigError('language_policy', 'language_policy 只能是 prefer 或 require')
    page_policy = payload.get('page_policy') or 'first'
    if page_policy not in ('first', 'explicit'):
        raise DiscoveryConfigError('page_policy', 'page_policy 只能是 first 或 explicit')
    raw_pages = payload.get('target_pages') or []
    if not isinstance(raw_pages, (list, tuple)):
        raise DiscoveryConfigError('target_pages', 'target_pages 必须是整数数组')
    pages: list[int] = []
    for raw in raw_pages:
        if isinstance(raw, bool) or not isinstance(raw, int) or not 1 <= raw <= 999:
            raise DiscoveryConfigError('target_pages', '分P序号必须是 1 到 999 的整数')
        if raw not in pages:
            pages.append(raw)
    if len(pages) > MAX_TARGET_PAGES:
        raise DiscoveryConfigError('target_pages', f'最多指定 {MAX_TARGET_PAGES} 个分P')
    if page_policy == 'first':
        pages = []
    elif not pages:
        raise DiscoveryConfigError('target_pages', '选择“指定分P”时至少填写一个分P序号')
    daily_time = payload.get('daily_time') or None
    zone = payload.get('schedule_timezone') or 'Asia/Shanghai'
    if daily_time and (not isinstance(daily_time,str) or not re.fullmatch(r'([01][0-9]|2[0-3]):[0-5][0-9]',daily_time)):
        raise DiscoveryConfigError('daily_time','每日时间须为 HH:MM')
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError,ValueError,TypeError):
        raise DiscoveryConfigError('schedule_timezone','无效时区')
    return {
        'daily_time':daily_time, 'schedule_timezone':zone,
        'platform': platform, 'name': name, 'keywords': list(keywords),
        'exclude_terms': list(terms), 'languages': list(languages),
        'match_mode': match_mode, 'language_policy': language_policy,
        'page_policy': page_policy,
        'target_pages': pages,
        'max_results_per_run': _bound_int(payload.get('max_results_per_run'),
                                          field='max_results_per_run',
                                          low=1, high=MAX_RESULTS_PER_RUN, default=10),
        'max_pages_per_run': _bound_int(payload.get('max_pages_per_run'),
                                        field='max_pages_per_run',
                                        low=1, high=MAX_PAGES_PER_RUN, default=2),
        'max_requests_per_run': _bound_int(payload.get('max_requests_per_run'),
                                           field='max_requests_per_run',
                                           low=1, high=MAX_REQUESTS_PER_RUN, default=3),
        'rescan_after_hours': _bound_int(payload.get('rescan_after_hours'),
                                         field='rescan_after_hours',
                                         low=0, high=MAX_RESCAN_HOURS, default=168),
        'schedule_interval_minutes': _bound_int(payload.get('schedule_interval_minutes'),
                                                field='schedule_interval_minutes',
                                                low=MIN_INTERVAL_MINUTES,
                                                high=MAX_INTERVAL_MINUTES, default=1440),
        'enabled': bool(payload.get('enabled', False)),
        'network_authorized': bool(payload.get('network_authorized', False)),
    }


def target_pages(config: dict) -> tuple[int, ...]:
    if config.get('page_policy') == 'explicit':
        return tuple(int(page) for page in config.get('target_pages') or ())
    return (1,)


def keyword_hit(title: str, keywords, match_mode: str = 'any') -> bool:
    haystack = normalize_text(title)
    if not haystack:
        return False
    hits = [normalize_text(keyword) in haystack for keyword in keywords if normalize_text(keyword)]
    if not hits:
        return False
    return all(hits) if match_mode == 'all' else any(hits)


def excluded(title: str, author: str | None, terms) -> str | None:
    """Return the first matching exclusion term, or ``None``."""
    haystacks = (normalize_text(title), normalize_text(author or ''))
    for term in terms:
        needle = normalize_text(term)
        if needle and any(needle in haystack for haystack in haystacks):
            return term
    return None


def seen_map(watermark: dict) -> dict:
    value = (watermark or {}).get('seen')
    return dict(value) if isinstance(value, dict) else {}


def merge_watermark(previous: dict, saved_items, *, now: datetime, run_id: str,
                    max_seen: int = 500) -> dict:
    """Advance the watermark with the candidates this run actually persisted."""
    previous = previous or {}
    seen = seen_map(previous)
    stamp = now.astimezone(timezone.utc).isoformat()
    newest = _parse_time(previous.get('newest_published_at'))
    for item in saved_items:
        seen[item['external_id']] = stamp
        published = item.get('published_at')
        if published and (newest is None or published > newest):
            newest = published
    if len(seen) > max_seen:
        ordered = sorted(seen.items(), key=lambda pair: pair[1], reverse=True)[:max_seen]
        seen = dict(ordered)
    return {
        'schema_version': 'watermark.v1',
        'last_run_id': run_id,
        'last_success_at': stamp,
        'newest_published_at': newest.astimezone(timezone.utc).isoformat() if newest else None,
        'seen': seen,
    }


def watermark_summary(watermark: dict) -> dict:
    value = watermark or {}
    newest = _parse_time(value.get('newest_published_at'))
    return {
        'schema_version': value.get('schema_version') or 'watermark.v1',
        'last_run_id': value.get('last_run_id'),
        'last_success_at': value.get('last_success_at'),
        'newest_published_at': newest.isoformat() if newest else None,
        'seen_count': len(seen_map(value)),
    }


def next_run_after(now: datetime, interval_minutes: int, failure_streak: int = 0) -> datetime:
    """Schedule the next attempt; failures back off but never below 15 minutes."""
    base = max(MIN_INTERVAL_MINUTES, int(interval_minutes)) * 60
    if failure_streak <= 0:
        return now + timedelta(seconds=base)
    delay = min(base, 900 * (2 ** min(int(failure_streak) - 1, 8)))
    return now + timedelta(seconds=delay)


def next_configured_run(now: datetime, config: dict, failure_streak: int = 0):
    if not config.get('daily_time') or failure_streak:
        return next_run_after(now,config['schedule_interval_minutes'],failure_streak)
    zone = ZoneInfo(config.get('schedule_timezone') or 'Asia/Shanghai')
    hour, minute = map(int,config['daily_time'].split(':'))
    local = now.astimezone(zone)
    candidate = local.replace(hour=hour,minute=minute,second=0,microsecond=0)
    if candidate <= local:
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def plan_candidates(items, config: dict, *, existing: dict, now: datetime) -> list[dict]:
    """Decide what to do with each candidate.

    ``existing`` maps ``external_id`` to a source row summary
    (``id``/``availability``/``last_checked_at``/``content_status``).
    """
    rescan_hours = int(config.get('rescan_after_hours') or 0)
    cutoff = now - timedelta(hours=rescan_hours) if rescan_hours else None
    results: list[dict] = []
    seen: set[str] = set()
    per_video: dict[str, list[dict]] = {}
    order: list[str] = []

    for item in items:
        key = _value(item, 'video_id') or _value(item, 'external_id')
        if key not in per_video:
            per_video[key] = []
            order.append(key)
        per_video[key].append(item)

    matched: list[dict] = []
    for key in order:
        candidates = per_video[key]
        first = candidates[0]
        bvid = _value(first, 'video_id') or _value(first, 'external_id')
        title = _value(first, 'title') or ''
        author = _value(first, 'author_name')
        published = _value(first, 'published_at')
        metadata = _value(first, 'metadata') or {}
        pages = target_pages(config)
        for page in pages:
            external_id = f'{bvid}:p{page}'
            entry = {
                'external_id': external_id, 'bvid': bvid, 'page': page,
                'title': title, 'author_name': author,
                'published_at': published, 'metadata': metadata,
            }
            if not keyword_hit(title, config.get('keywords') or (),
                               config.get('match_mode') or 'any'):
                entry.update(decision='keyword_miss',
                             reason='标题不包含配置的关键词')
            else:
                hit = excluded(title, author, config.get('exclude_terms') or ())
                if hit:
                    entry.update(decision='excluded_term', reason=f'命中排除词：{hit}')
                else:
                    matched.append(entry)
                    continue
            if entry['external_id'] not in seen:
                seen.add(entry['external_id'])
                results.append(entry)

    matched.sort(key=lambda entry: (
        entry['published_at'] is None,
        -(entry['published_at'].timestamp() if entry['published_at'] else 0),
        entry['external_id']))
    limit = int(config.get('max_results_per_run') or 10)
    selected = 0
    for index, entry in enumerate(matched):
        source = existing.get(entry['external_id'])
        if config.get('_simple_mode') and source and source.get('has_summary'):
            entry.update(decision='duplicate',reason='该视频已经总结，直接跳过')
        elif (selected if config.get('_simple_mode') else index) >= limit:
            entry.update(decision='over_limit',
                         reason=f'超过每轮上限（{limit} 条），未入队')
        else:
            if source and source.get('availability') == 'deleted':
                entry.update(decision='skipped_deleted', reason='该来源已被删除，不会重新导入')
            elif source and (source.get('content_status') == 'pending'
                             or _recent(source.get('last_checked_at'), cutoff)):
                # A pending source already has a queued collection; treat it as queued
                # rather than stale so one candidate cannot be enqueued twice.
                entry.update(decision='duplicate',
                             reason=('该来源已排队采集' if source.get('content_status') == 'pending'
                                     else '最近已采集过，未到重新检查时间'))
            elif source:
                entry.update(decision='refreshed', reason='来源已存在且超过重新检查时间，排队刷新',
                             source_id=source.get('id'))
            else:
                entry.update(decision='enqueued', reason='新来源，已排队采集')
            if entry['decision'] in ('enqueued','refreshed'):
                selected += 1
        if entry['external_id'] in seen:
            continue
        seen.add(entry['external_id'])
        results.append(entry)
    return results


def run_counters(entries) -> dict:
    counters = {'discovered': len(entries), 'matched': 0, 'excluded': 0,
                'duplicates': 0, 'enqueued': 0}
    for entry in entries:
        decision = entry['decision']
        if decision in ('enqueued', 'refreshed'):
            counters['enqueued'] += 1
            counters['matched'] += 1
        elif decision in ('duplicate', 'skipped_deleted'):
            counters['duplicates'] += 1
            counters['matched'] += 1
        elif decision == 'over_limit':
            counters['matched'] += 1
        else:
            counters['excluded'] += 1
    return counters


def _value(item, name):
    if isinstance(item, dict):
        return item.get(name)
    return getattr(item, name, None)


def _recent(value, cutoff) -> bool:
    if cutoff is None:
        return False
    moment = _parse_time(value)
    return bool(moment and moment >= cutoff)


def _parse_time(value):
    if not value:
        return None
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        except ValueError:
            return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)
