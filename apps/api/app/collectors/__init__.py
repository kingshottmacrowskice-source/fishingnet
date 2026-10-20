"""Collector registry.

``get_collector`` returns a **fresh instance per call** so that a per-job request
budget cannot leak between jobs. Tests replace factories via ``register``.
"""

from __future__ import annotations

from app.collectors.base import (
    AccountCapability, Capabilities, Collector, CollectorError, Credential,
    DiscoveryItem, DiscoveryQuery, DiscoveryResult,
    Locator, Page, QrLogin, QrPoll, RemoteSource, SubtitleResult, SubtitleTrack,
    COLLECTOR_SCHEMA_VERSION, subtitle_segments,
)
from app.collectors.bilibili import BilibiliCollector

__all__ = [
    'AccountCapability', 'Capabilities', 'Collector', 'CollectorError', 'Credential',
    'DiscoveryItem', 'DiscoveryQuery', 'DiscoveryResult',
    'Locator', 'Page', 'QrLogin', 'QrPoll', 'RemoteSource', 'SubtitleResult',
    'SubtitleTrack', 'COLLECTOR_SCHEMA_VERSION', 'subtitle_segments',
    'get_collector', 'register', 'reset_registry', 'platforms', 'credential_for',
]


def _default_factories() -> dict:
    return {'bilibili': BilibiliCollector, 'youtube': None}


_FACTORIES = _default_factories()


def get_collector(platform: str) -> Collector:
    factory = _FACTORIES.get(platform)
    if factory is None:
        raise CollectorError('PLATFORM_NOT_AVAILABLE', '该平台尚未接入采集器', False,
                             {'platform': platform})
    return factory()


def register(platform: str, factory):
    _FACTORIES[platform] = factory


def reset_registry():
    global _FACTORIES
    _FACTORIES = _default_factories()


def platforms() -> list[str]:
    return [name for name, factory in _FACTORIES.items() if factory is not None]


def credential_for(platform: str, values: dict | None) -> Credential | None:
    if not values:
        return None
    return Credential(platform=platform, values=dict(values))


class UnavailableCollector:
    """Placeholder used to describe a platform contract that has no adapter yet."""

    def __init__(self, platform: str, label: str):
        self.platform = platform
        self.label = label

    def capabilities(self) -> Capabilities:
        return Capabilities(platform=self.platform, label=self.label, single_url=False,
                            metadata=False, subtitles=False,
                            notes=('仅保留适配器契约，真实联网实现尚未交付',))
