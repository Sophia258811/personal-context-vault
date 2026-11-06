"""Validate and durably import a short browser media segment."""

import base64
import hashlib
import io
import json
import re
import wave
from urllib.parse import urlsplit
from uuid import UUID

from . import media
from .store import utc_now


MAX_BODY = 4 * 1024 * 1024
MAX_SECONDS = 15
MAX_STORED_BYTES = 2 * 1024 ** 3
PLATFORMS = {
    "douyin": ("douyin.com", r"[0-9]{1,40}"),
    "youtube": ("youtube.com", r"[A-Za-z0-9_-]{11}"),
    "bilibili": ("bilibili.com", r"(?:BV[A-Za-z0-9]+|av[0-9]+):p[0-9]+"),
}


class MediaConflict(ValueError):
    """A segment ID was already committed with different content or source."""


class InactiveMediaSource(PermissionError):
    """The source cannot accept new evidence or replay acknowledgements."""


class MediaCapacityError(OSError):
    """The local raw-media quota has been reached."""


def initialize(store):
    if store.connection.in_transaction:
        raise RuntimeError("initialize stream media outside a transaction")
    media.initialize(store)
    store.connection.executescript("""
        CREATE TABLE IF NOT EXISTS stream_segments (
            segment_id TEXT PRIMARY KEY,
            evidence_id INTEGER NOT NULL UNIQUE REFERENCES evidence(id) ON DELETE CASCADE,
            payload_hash TEXT NOT NULL, platform TEXT NOT NULL, platform_content_id TEXT NOT NULL,
            start_seconds REAL NOT NULL, end_seconds REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS stream_assets (
            segment_id TEXT NOT NULL REFERENCES stream_segments(segment_id) ON DELETE CASCADE,
            evidence_id INTEGER PRIMARY KEY REFERENCES media_assets(evidence_id) ON DELETE CASCADE,
            position_seconds REAL
        );
        CREATE INDEX IF NOT EXISTS stream_assets_segment_idx ON stream_assets(segment_id);
    """)


def _seconds(value):
    if type(value) not in (float, int) or not 0 <= value <= 1_000_000_000:
        raise ValueError("invalid media timestamp")
    return float(value) or 0.0


def _decode(value):
    if not isinstance(value, str) or not 0 < len(value) <= MAX_BODY:
        raise ValueError("missing or oversized media asset")
    return base64.b64decode(value, validate=True)


def _wav_duration(data):
    try:
        with wave.open(io.BytesIO(data)) as audio:
            frames, rate = audio.getnframes(), audio.getframerate()
            channels = audio.getnchannels()
            if (not 0 < rate <= 48000 or not 0 < frames <= MAX_SECONDS * rate
                    or channels not in (1, 2) or audio.getsampwidth() != 2
                    or len(audio.readframes(frames)) != frames * channels * 2):
                raise ValueError("expected complete PCM16 WAV of at most 15 seconds")
            return frames / rate
    except (wave.Error, EOFError) as exc:
        raise ValueError("invalid WAV") from exc


def _check_jpeg(data):
    if not data.startswith(b"\xff\xd8\xff") or not data.endswith(b"\xff\xd9"):
        raise ValueError("invalid JPEG signature")
    offset = 2
    has_dimensions = False
    while offset + 4 <= len(data) and data[offset] == 0xff:
        marker = data[offset + 1]
        length = int.from_bytes(data[offset + 2:offset + 4], "big")
        stop = offset + 2 + length
        if length < 2 or stop > len(data):
            break
        if marker in (0xc0, 0xc1, 0xc2):
            if length < 8 or data[offset + 4] != 8:
                break
            height = int.from_bytes(data[offset + 5:offset + 7], "big")
            width = int.from_bytes(data[offset + 7:offset + 9], "big")
            if not (0 < height <= 4096 and 0 < width <= 4096):
                break
            has_dimensions = True
        if marker == 0xda and has_dimensions and stop < len(data) - 2:
            return
        offset = stop
    raise ValueError("invalid JPEG frame header or dimensions")


def _validate(payload):
    if not isinstance(payload, dict):
        raise ValueError("expected a media segment object")
    segment_id = payload.get("id")
    if not isinstance(segment_id, str) or len(segment_id) != 36:
        raise ValueError("invalid segment UUID")
    parsed_id = str(UUID(segment_id))
    if parsed_id != segment_id.lower():
        raise ValueError("invalid segment UUID")
    platform, identity = payload.get("platform"), payload.get("platform_content_id")
    if not isinstance(platform, str) or platform not in PLATFORMS:
        raise ValueError("unsupported platform")
    domain, pattern = PLATFORMS[platform]
    if not isinstance(identity, str) or len(identity) > 100 or not re.fullmatch(pattern, identity):
        raise ValueError("invalid platform content ID")
    address = payload.get("url")
    if (not isinstance(address, str) or not 0 < len(address) <= 4096
            or any(ord(char) <= 32 or ord(char) == 127 for char in address)):
        raise ValueError("invalid page URL")
    url = urlsplit(address)
    host = url.hostname or ""
    if (url.scheme != "https" or url.username is not None or url.port not in (None, 443)
            or not (host == domain or host.endswith("." + domain))):
        raise ValueError("URL does not match the media platform")
    start, end = _seconds(payload.get("start_seconds")), _seconds(payload.get("end_seconds"))
    if not 0 < end - start <= MAX_SECONDS:
        raise ValueError("segment must cover at most 15 media seconds")
    frames = payload.get("frames")
    if not isinstance(frames, list) or len(frames) > 3:
        raise ValueError("expected at most three JPEG frames")
    assets = []
    if payload.get("audio") is not None:
        data = _decode(payload["audio"])
        assets.append(("audio", "audio/wav", ".wav", start, _wav_duration(data), data))
    for frame in frames:
        if not isinstance(frame, dict):
            raise ValueError("invalid frame")
        position = _seconds(frame.get("position_seconds"))
        if not start <= position <= end:
            raise ValueError("frame position is outside the segment")
        data = _decode(frame.get("jpeg"))
        _check_jpeg(data)
        assets.append(("image", "image/jpeg", ".jpg", position, None, data))
    if not assets or sum(len(asset[-1]) for asset in assets) > MAX_BODY:
        raise ValueError("segment is empty or too large")
    return parsed_id, f"https://{host}{url.path or '/'}", platform, identity, start, end, assets


def ingest(store, source_id, payload):
    """Commit one segment and all its assets before acknowledging the browser."""
    segment_id, url, platform, identity, start, end, assets = _validate(payload)
    metadata = {
        "kind": "stream_segment", "segment_id": segment_id, "url": url,
        "platform": platform, "platform_content_id": identity,
        "start_seconds": start, "end_seconds": end,
        "assets": [
            {"kind": kind, "mime": mime, "position_seconds": position, "duration_seconds": duration,
             "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
            for kind, mime, suffix, position, duration, data in assets
        ],
    }
    content = json.dumps(metadata, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    initialize(store)
    with store.connection:
        store.connection.execute("BEGIN IMMEDIATE")
        source = store.connection.execute("""
            SELECT 1 FROM sources WHERE id=? AND status='active'
            AND (expires_at IS NULL OR julianday(expires_at) > julianday('now'))
        """, (source_id,)).fetchone()
        if not source:
            raise InactiveMediaSource("source is missing, expired or inactive")
        existing = store.connection.execute("""
            SELECT s.evidence_id, s.payload_hash, e.source_id
            FROM stream_segments s JOIN evidence e ON e.id=s.evidence_id WHERE s.segment_id=?
        """, (segment_id,)).fetchone()
        if existing:
            if existing["payload_hash"] != digest or existing["source_id"] != source_id:
                raise MediaConflict("segment ID already exists")
            return {"segment_id": segment_id, "evidence_id": existing["evidence_id"], "duplicate": True}

        stored_bytes = store.connection.execute("""
            SELECT COALESCE(SUM(length(a.data)), 0)
            FROM stream_assets s JOIN media_assets a ON a.evidence_id=s.evidence_id
        """).fetchone()[0]
        if stored_bytes + sum(asset["bytes"] for asset in metadata["assets"]) > MAX_STORED_BYTES:
            raise MediaCapacityError("local browser media storage limit reached")

        now = utc_now()
        title = f"{platform} {identity} / browser segment"
        parent = store._insert_evidence(source_id=source_id, title=title, content=content,
                                        locator=url, occurred_at=now, sensitivity="high")
        store.connection.execute("INSERT INTO stream_segments VALUES (?,?,?,?,?,?,?)",
                                 (segment_id, parent, digest, platform, identity, start, end))
        for asset, asset_metadata in zip(assets, metadata["assets"]):
            kind, mime, suffix, position, duration, data = asset
            asset_content = {**asset_metadata, "kind": "stream_asset", "media_kind": kind,
                             "parent_evidence_id": parent, "segment_id": segment_id}
            evidence_id = store._insert_evidence(
                source_id=source_id, title=f"{title} / {kind} at {position:g}s",
                content=json.dumps(asset_content), locator=f"pcv:evidence:{parent}",
                occurred_at=now, sensitivity="high",
            )
            store.connection.execute("INSERT INTO media_assets VALUES (?,?,?,?,?,?,?)",
                                     (evidence_id, mime, kind, suffix, asset_metadata["sha256"], duration, data))
            store.connection.execute("INSERT INTO stream_assets VALUES (?,?,?)",
                                     (segment_id, evidence_id, position))
    return {"segment_id": segment_id, "evidence_id": parent, "duplicate": False}
