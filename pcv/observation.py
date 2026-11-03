"""Validate structured browser observations without accepting arbitrary fields."""
import math
import re
from datetime import datetime


def structured_fields(event, hostname):
    if event.get('schema_version') != 2:
        return {}  # Older clients remain readable and unresolved.
    platform = event.get('platform')
    domains = {'douyin': 'douyin.com', 'youtube': 'youtube.com', 'bilibili': 'bilibili.com'}
    if platform != 'browser':
        domain = domains.get(platform)
        if not domain or not (hostname == domain or hostname.endswith('.' + domain)):
            raise ValueError('platform host mismatch')
    identity = event.get('platform_content_id')
    patterns = {'douyin': r'\d{1,40}', 'youtube': r'[\w-]{11}', 'bilibili': r'(BV[a-zA-Z0-9]+|av\d+):p\d+'}
    if identity is not None and (not isinstance(identity, str) or len(identity) > 100 or
            platform not in patterns or not re.fullmatch(patterns[platform], identity)):
        raise ValueError('invalid content identity')
    sections = event.get('sections')
    if not isinstance(sections, dict) or set(sections) != {'body', 'comments', 'navigation', 'unclassified'}:
        raise ValueError('invalid sections')
    if any(not isinstance(v, str) for v in sections.values()) or sum(map(len, sections.values())) > 20000:
        raise ValueError('sections too large')
    observed = event.get('observed_at')
    if not isinstance(observed, str) or len(observed) > 50 or datetime.fromisoformat(observed.replace('Z', '+00:00')).tzinfo is None:
        raise ValueError('observed_at requires timezone')
    method = event.get('identity_method')
    if method not in {'unresolved', 'page_url', 'player_ancestor_attribute'} or (identity is None) != (method == 'unresolved'):
        raise ValueError('invalid identity provenance')
    media = event.get('media')
    normalized_media = None
    if media is not None:
        if not isinstance(media, dict) or type(media.get('paused')) is not bool:
            raise ValueError('invalid media')
        normalized_media = {'paused': media['paused'], 'observed_at': observed, 'timing_source': 'html_video_element'}
        duration_source = media.get('duration_source', 'unavailable')
        if duration_source not in {'unavailable', 'html_video_element', 'player_time_label'}:
            raise ValueError('invalid duration source')
        normalized_media['duration_source'] = duration_source
        for key in ('position_seconds', 'duration_seconds', 'playback_rate'):
            value = media.get(key)
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                raise ValueError('invalid media timing')
            normalized_media[key] = value
    return dict(schema_version=2, platform=platform, platform_content_id=identity,
                identity_method=method, sections=sections, observed_at=observed,
                media=normalized_media, available_modalities=['text'],
                capture_limitations=['heuristic_sections', 'no_audio_or_video_bytes'])
