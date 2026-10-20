"""Restricted bridge to pinned bilibili-api-python 17.4.1 (GPL-3.0-or-later).

Use inherited Video.get_subtitle and the library's standard WBI signer, but
replace player transport entirely. No library network client, global settings,
WBI2, device activation, ticket acquisition or library retries are invoked.
Private signing API usage is pinned and covered by transport contract tests.
"""
import asyncio
import re
from urllib.parse import urlsplit

from bilibili_api.video import Video
from bilibili_api.utils.network import OE, _enc_wbi

from app.collectors.base import CollectorError


async def _subtitle(collector, bvid, cid, credential):
    class RestrictedVideo(Video):
        async def get_player_info(self, cid=None, epid=None):
            nav = collector._request_json(
                'https://api.bilibili.com/x/web-interface/nav',
                credential=credential)
            try:
                images = nav['data']['wbi_img']
                parts = [urlsplit(images[k]).path.rsplit('/', 1)[-1].split('.')[0]
                         for k in ('img_url', 'sub_url')]
                if not all(re.fullmatch(r'[0-9a-fA-F]{32}', part) for part in parts):
                    raise ValueError('Invalid key')
                combined = ''.join(parts)
                key = ''.join(combined[i] for i in OE)[:32]
            except (KeyError, TypeError, ValueError, AttributeError):
                raise CollectorError('UPSTREAM_INVALID_JSON',
                                     'B站未返回有效字幕签名参数，已停止本次采集', False) from None
            params = _enc_wbi({'bvid': bvid, 'cid': cid}, key)
            payload = collector._request_json(
                'https://api.bilibili.com/x/player/wbi/v2', params,
                credential=credential)
            data = payload.get('data')
            if not isinstance(data, dict):
                raise CollectorError('UPSTREAM_INVALID_JSON',
                                     'B站字幕清单结构异常，已停止本次采集', False)
            return data

    return await RestrictedVideo(bvid=bvid).get_subtitle(cid=cid)


def get_subtitle(collector, bvid, cid, credential):
    result = asyncio.run(_subtitle(collector, bvid, cid, credential))
    if result is not None and not isinstance(result, dict):
        raise CollectorError('UPSTREAM_INVALID_JSON', 'B站字幕清单结构异常，已停止本次采集', False)
    return result or {}
