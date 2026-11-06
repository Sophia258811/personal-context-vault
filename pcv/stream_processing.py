"""Turn durable stream segments into bounded, source-linked Agent evidence."""

import argparse
import json
import sqlite3
from pathlib import Path

from . import media
from .agent_tasks import active_evidence
from .store import utc_now


def initialize(store):
    store.connection.execute("""
        CREATE TABLE IF NOT EXISTS stream_analysis (
            segment_id TEXT PRIMARY KEY REFERENCES stream_segments(segment_id) ON DELETE CASCADE,
            evidence_id INTEGER NOT NULL UNIQUE REFERENCES evidence(id) ON DELETE CASCADE,
            recognized_assets INTEGER NOT NULL,
            completed_at TEXT NOT NULL
        )
    """)
    store.connection.commit()


def process_next(store, *, processor=media.windows_process):
    connection = store.connection
    segment = connection.execute("""
        SELECT ss.* FROM stream_segments ss
        JOIN evidence e ON e.id=ss.evidence_id
        JOIN sources s ON s.id=e.source_id
        LEFT JOIN stream_analysis a ON a.segment_id=ss.segment_id
        WHERE a.segment_id IS NULL AND s.status='active'
          AND (s.expires_at IS NULL OR julianday(s.expires_at) > julianday('now'))
        ORDER BY e.id LIMIT 1
    """).fetchone()
    if segment is None:
        return None
    assets = connection.execute("""
        SELECT sa.evidence_id, sa.position_seconds, ma.kind, ma.duration_seconds
        FROM stream_assets sa JOIN media_assets ma ON ma.evidence_id=sa.evidence_id
        WHERE sa.segment_id=? ORDER BY sa.evidence_id
    """, (segment["segment_id"],)).fetchall()
    recognized = []
    unavailable = []
    remaining = 75000
    for asset in assets:
        reference = {"asset_evidence_id": asset["evidence_id"], "kind": asset["kind"],
                     "video_position_seconds": asset["position_seconds"]}
        try:
            derived = media.analyze(store, asset["evidence_id"], processor=processor)
            row = active_evidence(store, derived["evidence_id"])
        except (ValueError, OSError) as error:
            unavailable.append({**reference, "reason": str(error)})
            continue
        text = json.loads(row["content"])
        component = {
            **reference, "derived_evidence_id": row["id"], "processor": text["processor"],
            "segments": text["segments"],
            **({"audio_offsets": "relative_to_clip_start"} if asset["kind"] == "audio" else {}),
        }
        size = len(json.dumps(component, ensure_ascii=False))
        if size > remaining:
            unavailable.append({**reference, "reason": "recognition text exceeds the segment task budget"})
        else:
            recognized.append(component)
            remaining -= size
    content = json.dumps({
        "kind": "stream_media_text", "parent_evidence_id": segment["evidence_id"],
        "platform": segment["platform"], "platform_content_id": segment["platform_content_id"],
        "start_seconds": segment["start_seconds"], "end_seconds": segment["end_seconds"],
        "recognized": recognized, "unavailable": unavailable,
        "limitations": [
            "OCR and transcription are machine-generated; verify against the original media.",
            "Audio offsets are relative to the captured clip. No direct visual or audio interpretation.",
        ],
    }, ensure_ascii=False)
    if len(content) > 100000:
        raise ValueError("stream analysis exceeds the Agent task size limit")
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        parent = active_evidence(store, segment["evidence_id"])
        existing = connection.execute("SELECT evidence_id FROM stream_analysis WHERE segment_id=?",
                                      (segment["segment_id"],)).fetchone()
        if existing:
            return {"segment_id": segment["segment_id"], "evidence_id": existing["evidence_id"], "created": False}
        evidence_id = store._insert_evidence(
            source_id=parent["source_id"], title="Stream segment / " + segment["platform"],
            content=content, locator=f"pcv:evidence:{parent['id']}",
            occurred_at=parent["occurred_at"], sensitivity=parent["sensitivity"],
        )
        connection.execute("INSERT INTO stream_analysis VALUES (?,?,?,?)",
                           (segment["segment_id"], evidence_id, len(recognized), utc_now()))
    return {"segment_id": segment["segment_id"], "evidence_id": evidence_id,
            "recognized": len(recognized), "unavailable": len(unavailable), "created": True}


def expire_processed_media(store):
    # Keep the evidence ID, original hash and derived text after payload expiry.
    with store.connection:
        return store.connection.execute("""
            UPDATE media_assets SET data=X''
            WHERE length(data) > 0 AND evidence_id IN (
                SELECT sa.evidence_id FROM stream_assets sa JOIN stream_analysis a USING(segment_id)
                WHERE a.recognized_assets > 0 AND julianday(a.completed_at) <= julianday('now', '-1 day')
            )
        """).rowcount


def status(database, limit):
    connection = sqlite3.connect(Path(database).expanduser().resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        if not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='stream_analysis'"
        ).fetchone():
            return []
        return [dict(row) for row in connection.execute("""
            SELECT ss.segment_id, ss.platform, ss.platform_content_id, ss.start_seconds, ss.end_seconds,
                   a.evidence_id AS analysis_id, a.recognized_assets,
                   CASE WHEN a.evidence_id IS NULL THEN 'pending'
                        WHEN a.recognized_assets = 0 THEN 'unreadable' ELSE 'processed' END AS status
            FROM stream_segments ss JOIN evidence e ON e.id=ss.evidence_id
            JOIN sources s ON s.id=e.source_id
            LEFT JOIN stream_analysis a USING(segment_id)
            WHERE s.status='active' AND (s.expires_at IS NULL OR julianday(s.expires_at)>julianday('now'))
            ORDER BY ss.evidence_id DESC LIMIT ?
        """, (limit,))]
    finally:
        connection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="List recent stream processing results")
    parser.add_argument("--db", default=".pcv/vault.db")
    parser.add_argument("--limit", type=int, default=10, choices=range(1, 51))
    args = parser.parse_args()
    try:
        print(json.dumps(status(args.db, args.limit), ensure_ascii=True, indent=2))
    except sqlite3.Error as error:
        parser.exit(2, str(error) + "\n")
