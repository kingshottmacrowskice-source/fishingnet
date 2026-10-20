"""Collector contract shared by every platform adapter.

The contract is deliberately small: parse a user-supplied locator, fetch remote
metadata, fetch one allowed caption track, and report account capability.

Two rules shape the design:

* Adapters never touch the database and never log credentials. They return plain
  values or raise :class:`CollectorError` with a stable, user-safe code.
* Network failures are *reported*, never smoothed over. "No caption track" and
  "not allowed to read captions without a login" are different states, and the
  caller is expected to persist the difference.

Captions are converted into the same immutable segment shape used by manual
imports (``segment_id``/``text``/``start_ms``/``end_ms``/``content_hash``) so the
existing evidence anchor validator works unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

COLLECTOR_SCHEMA_VERSION = 'collector.v1'


class CollectorError(Exception):
    """Stable, user-safe collector failure.

    ``code`` is what the API/job records; ``message`` is safe to show in the UI.
    Never put a cookie, URL query token or raw upstream body into either field.
    """

    def __init__(self, code: str, message: str, retryable: bool = False,
                 details: dict | None = None, upstream_code=None):
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}
        self.upstream_code = upstream_code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Credential:
    """Opaque platform credential; ``repr`` must stay redacted."""

    platform: str
    values: dict = field(default_factory=dict, repr=False)

    def get(self, name, default=None):
        return self.values.get(name, default)

    def __repr__(self):
        return f'Credential(platform={self.platform!r}, configured={bool(self.values)})'

    __str__ = __repr__


@dataclass(frozen=True, slots=True)
class Capabilities:
    platform: str
    label: str
    single_url: bool
    metadata: bool
    subtitles: bool
    keyword_discovery: bool = False
    scheduling: bool = False
    local_transcription: bool = False
    qr_login: bool = False
    credential_required_for: tuple[str, ...] = ()
    max_requests_per_job: int = 1
    max_discovery_pages: int = 0
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            'platform': self.platform, 'label': self.label,
            'single_url': self.single_url, 'metadata': self.metadata,
            'subtitles': self.subtitles, 'keyword_discovery': self.keyword_discovery,
            'scheduling': self.scheduling, 'local_transcription': self.local_transcription,
            'qr_login': self.qr_login,
            'credential_required_for': list(self.credential_required_for),
            'max_requests_per_job': self.max_requests_per_job,
            'max_discovery_pages': self.max_discovery_pages,
            'notes': list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class Locator:
    """A normalised, offline-parsed pointer to one content unit."""

    platform: str
    video_id: str
    page: int
    canonical_url: str
    raw: str


@dataclass(frozen=True, slots=True)
class Page:
    page_no: int
    cid: str
    part_title: str = ''
    duration_seconds: int = 0


@dataclass(frozen=True, slots=True)
class RemoteSource:
    """Metadata only: no content bytes, no video stream."""

    platform: str
    external_id: str
    canonical_url: str
    title: str
    author_name: str | None = None
    author_external_id: str | None = None
    published_at: datetime | None = None
    duration_seconds: int | None = None
    availability: str = 'available'
    pages: tuple[Page, ...] = ()
    metadata: dict = field(default_factory=dict)

    def page(self, page_no: int) -> Page | None:
        return next((p for p in self.pages if p.page_no == page_no), None)

    def to_checkpoint(self) -> dict:
        """Serialise into a JSON column so a crashed stage does not refetch."""
        return {
            'platform': self.platform, 'external_id': self.external_id,
            'canonical_url': self.canonical_url, 'title': self.title,
            'author_name': self.author_name, 'author_external_id': self.author_external_id,
            'published_at': self.published_at.isoformat() if self.published_at else None,
            'duration_seconds': self.duration_seconds, 'availability': self.availability,
            'pages': [{'page_no': p.page_no, 'cid': p.cid, 'part_title': p.part_title,
                       'duration_seconds': p.duration_seconds} for p in self.pages],
            'metadata': self.metadata,
        }

    @classmethod
    def from_checkpoint(cls, value: dict) -> 'RemoteSource':
        published = value.get('published_at')
        return cls(
            platform=value['platform'], external_id=value['external_id'],
            canonical_url=value['canonical_url'], title=value['title'],
            author_name=value.get('author_name'),
            author_external_id=value.get('author_external_id'),
            published_at=datetime.fromisoformat(published) if published else None,
            duration_seconds=value.get('duration_seconds'),
            availability=value.get('availability') or 'available',
            pages=tuple(Page(page_no=int(p['page_no']), cid=str(p.get('cid') or ''),
                             part_title=p.get('part_title') or '',
                             duration_seconds=int(p.get('duration_seconds') or 0))
                        for p in value.get('pages') or []),
            metadata=dict(value.get('metadata') or {}),
        )


@dataclass(frozen=True, slots=True)
class SubtitleTrack:
    language: str
    label: str = ''
    url: str = ''
    machine: bool = False


@dataclass(frozen=True, slots=True)
class SubtitleResult:
    """Outcome of one caption attempt.

    ``status`` is one of ``available``, ``missing``, ``login_required``,
    ``permission_required``, ``unavailable``.
    """

    status: str
    segments: tuple[dict, ...] = ()
    content: str = ''
    language: str | None = None
    format: str = 'json'
    reason_code: str | None = None
    detail: str = ''
    tracks: tuple[SubtitleTrack, ...] = ()

    @property
    def usable(self) -> bool:
        return self.status == 'available' and bool(self.segments)

    def to_checkpoint(self) -> dict:
        return {
            'status': self.status,
            'segments': [dict(s) for s in self.segments],
            'content': self.content, 'language': self.language, 'format': self.format,
            'reason_code': self.reason_code, 'detail': self.detail,
            'tracks': [{'language': t.language, 'label': t.label, 'url': t.url,
                        'machine': t.machine} for t in self.tracks],
        }

    @classmethod
    def from_checkpoint(cls, value: dict) -> 'SubtitleResult':
        return cls(
            status=value['status'],
            segments=tuple(dict(s) for s in value.get('segments') or ()),
            content=value.get('content') or '', language=value.get('language'),
            format=value.get('format') or 'json', reason_code=value.get('reason_code'),
            detail=value.get('detail') or '',
            tracks=tuple(SubtitleTrack(language=t.get('language') or 'unknown',
                                       label=t.get('label') or '', url=t.get('url') or '',
                                       machine=bool(t.get('machine')))
                         for t in value.get('tracks') or ()),
        )


@dataclass(frozen=True, slots=True)
class DiscoveryQuery:
    """One bounded keyword-search page.

    ``keywords`` is already normalised by the caller. Adapters must not expand it
    into extra queries, follow related-video chains or enumerate uploaders.
    """

    keywords: tuple[str, ...]
    page: int = 1
    page_size: int = 20
    order: str = 'pubdate'

    @property
    def text(self) -> str:
        return ' '.join(self.keywords)


@dataclass(frozen=True, slots=True)
class DiscoveryItem:
    """One candidate returned by discovery. Metadata only, never content."""

    platform: str
    external_id: str
    video_id: str
    title: str
    author_name: str | None = None
    author_external_id: str | None = None
    published_at: datetime | None = None
    duration_seconds: int | None = None
    page: int = 1
    metadata: dict = field(default_factory=dict)

    def to_checkpoint(self) -> dict:
        return {
            'platform': self.platform, 'external_id': self.external_id,
            'video_id': self.video_id, 'title': self.title,
            'author_name': self.author_name, 'author_external_id': self.author_external_id,
            'published_at': self.published_at.isoformat() if self.published_at else None,
            'duration_seconds': self.duration_seconds, 'page': self.page,
            'metadata': self.metadata,
        }

    @classmethod
    def from_checkpoint(cls, value: dict) -> 'DiscoveryItem':
        published = value.get('published_at')
        return cls(
            platform=value['platform'], external_id=value['external_id'],
            video_id=value['video_id'], title=value.get('title') or '',
            author_name=value.get('author_name'),
            author_external_id=value.get('author_external_id'),
            published_at=datetime.fromisoformat(published) if published else None,
            duration_seconds=value.get('duration_seconds'),
            page=int(value.get('page') or 1), metadata=dict(value.get('metadata') or {}),
        )


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    """Outcome of one discovery page.

    ``status`` is ``ok``, ``empty`` or ``blocked``. A blocked page must carry a
    ``reason_code`` (for example ``DISCOVERY_DENIED``) so the caller can report the
    real upstream state instead of pretending no new material exists.
    """

    status: str
    items: tuple[DiscoveryItem, ...] = ()
    page: int = 1
    has_more: bool = False
    detail: str = ''
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class QrLogin:
    """A short-lived QR login challenge. ``key`` must never be logged."""

    url: str
    key: str = field(repr=False)
    expires_in: int = 180


@dataclass(frozen=True, slots=True)
class QrPoll:
    """One poll of a QR challenge.

    ``status`` is ``waiting_scan``, ``scanned``, ``confirmed``, ``expired`` or
    ``error``. Cookies are only present on ``confirmed`` and are redacted from
    ``repr``.
    """

    status: str
    message: str = ''
    cookies: dict = field(default_factory=dict, repr=False)
    error_code: str | None = None
    retryable: bool = False

    def __repr__(self):
        return f'QrPoll(status={self.status!r}, cookies={"<redacted>" if self.cookies else "{}"})'

    __str__ = __repr__


@dataclass(frozen=True, slots=True)
class AccountCapability:
    """Result of an explicit, user-triggered account check."""

    platform: str
    status: str
    display_name: str | None = None
    user_id: str | None = None
    checked_at: datetime | None = None
    metadata_capability: bool = True
    subtitle_capability: bool = False
    error_code: str | None = None
    detail: str = ''
    detail_untrusted: bool = True


class Collector(Protocol):
    platform: str

    def capabilities(self) -> Capabilities: ...

    def parse_locator(self, url: str) -> Locator: ...

    def fetch_source(self, locator: Locator, credential: Credential | None = None) -> RemoteSource: ...

    def fetch_subtitles(self, locator: Locator, source: RemoteSource,
                        credential: Credential | None = None, *,
                        languages: tuple[str, ...] | None = None,
                        language_policy: str = 'prefer') -> SubtitleResult: ...

    def discover(self, query: DiscoveryQuery) -> DiscoveryResult: ...

    def check_account(self, credential: Credential | None = None) -> AccountCapability: ...

    def start_qr_login(self) -> QrLogin: ...

    def poll_qr_login(self, key: str) -> QrPoll: ...


def subtitle_segments(cues, *, max_chars: int = 400, max_span_ms: int = 30_000) -> tuple[dict, ...]:
    """Group caption cues into readable paragraph segments.

    Platform captions arrive as short rolling cues. Keeping one segment per cue
    would produce thousands of rows and make quotes hard to read; grouping keeps
    timing precision (group start/end) while staying within the 5 MiB segment cap.
    """
    from app.domain.evidence import digest

    segments: list[dict] = []
    buffer: list[dict] = []
    chars = 0

    def flush():
        nonlocal buffer, chars
        text = ' '.join(part['text'] for part in buffer).strip()
        if text:
            segments.append({
                'text': text,
                'start_ms': buffer[0]['start_ms'],
                'end_ms': buffer[-1]['end_ms'],
            })
        buffer, chars = [], 0

    for cue in cues:
        text = (cue.get('text') or '').strip()
        if not text:
            continue
        start_ms = int(cue.get('start_ms') or 0)
        end_ms = max(start_ms, int(cue.get('end_ms') or start_ms))
        if buffer and (chars + len(text) > max_chars or end_ms - buffer[0]['start_ms'] > max_span_ms):
            flush()
        buffer.append({'text': text, 'start_ms': start_ms, 'end_ms': end_ms})
        chars += len(text)
    flush()

    for index, segment in enumerate(segments):
        segment['segment_id'] = f'seg_{index + 1}'
        segment['content_hash'] = digest(segment['text'])
    return tuple(segments)
