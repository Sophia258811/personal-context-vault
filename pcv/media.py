"""Import explicitly shared images and short WAVs and derive local text evidence."""

import argparse
import hashlib
import io
import json
import os
import sqlite3
import subprocess
import tempfile
import wave
from contextlib import closing
from pathlib import Path

from .store import Store


FORMATS = {
    ".png": ("image", "image/png"),
    ".jpg": ("image", "image/jpeg"),
    ".jpeg": ("image", "image/jpeg"),
    ".wav": ("audio", "audio/wav"),
}


def initialize(store):
    store.connection.executescript("""
        CREATE TABLE IF NOT EXISTS media_assets (
            evidence_id INTEGER PRIMARY KEY REFERENCES evidence(id) ON DELETE CASCADE,
            mime TEXT NOT NULL, kind TEXT NOT NULL, suffix TEXT NOT NULL,
            sha256 TEXT NOT NULL, duration_seconds REAL, data BLOB NOT NULL
        );
        CREATE TABLE IF NOT EXISTS media_derivations (
            evidence_id INTEGER PRIMARY KEY REFERENCES evidence(id) ON DELETE CASCADE,
            parent_id INTEGER NOT NULL REFERENCES media_assets(evidence_id) ON DELETE CASCADE,
            processor TEXT NOT NULL, culture TEXT NOT NULL,
            UNIQUE(parent_id, processor, culture)
        );
    """)


def read_sample(path):
    suffix = path.suffix.lower()
    if suffix not in FORMATS:
        raise ValueError("supported media: PNG, JPEG, PCM WAV")
    kind, mime = FORMATS[suffix]
    limit = (10 if kind == "image" else 24) * 1024 * 1024
    with path.open("rb") as input_file:
        data = input_file.read(limit + 1)
    if not data or len(data) > limit:
        raise ValueError("media is empty or exceeds the size limit")
    duration = None
    if suffix == ".wav":
        try:
            with wave.open(io.BytesIO(data)) as audio:
                frames = audio.getnframes()
                duration = frames / audio.getframerate()
                if (not 0 < duration <= 120 or audio.getsampwidth() != 2
                        or audio.getnchannels() not in (1, 2)
                        or audio.getframerate() > 48000
                        or len(audio.readframes(frames)) != frames * 2 * audio.getnchannels()):
                    raise ValueError("expected complete 16-bit mono/stereo PCM WAV, up to 120 seconds and 48 kHz")
        except (wave.Error, EOFError) as exc:
            raise ValueError("invalid PCM WAV") from exc
    elif not data.startswith(b"\x89PNG\r\n\x1a\n" if suffix == ".png" else b"\xff\xd8\xff"):
        raise ValueError("image signature does not match the file extension")
    return kind, mime, suffix, duration, data


def add(store, source_id, path, title, sensitivity="medium"):
    kind, mime, suffix, duration, data = read_sample(path)
    digest = hashlib.sha256(data).hexdigest()
    with store.connection:
        store.connection.execute("BEGIN IMMEDIATE")
        source = store.connection.execute("""
            SELECT 1 FROM sources WHERE id=? AND status='active'
            AND (expires_at IS NULL OR julianday(expires_at) > julianday('now'))
        """, (source_id,)).fetchone()
        if not source:
            raise ValueError("source is missing, expired or inactive")
        evidence_id = store._insert_evidence(
            source_id=source_id, title=title, sensitivity=sensitivity,
            content=json.dumps({"kind": "shared_media", "mime": mime, "sha256": digest,
                                "bytes": len(data), "duration_seconds": duration}),
        )
        store.connection.execute("INSERT INTO media_assets VALUES (?,?,?,?,?,?,?)",
                                 (evidence_id, mime, kind, suffix, digest, duration, data))
    return {"evidence_id": evidence_id, "media_kind": kind, "sha256": digest}


def validate_segments(kind, segments, duration):
    if not isinstance(segments, list) or len(segments) > 1000:
        raise ValueError("processor must return a bounded list of segments")
    if not segments:
        raise ValueError("processor did not recognize any content")
    result = []
    for segment in segments:
        text = segment.get("text") if isinstance(segment, dict) else None
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise ValueError("invalid segment text")
        if kind == "image":
            box = segment.get("box")
            if (not isinstance(box, list) or len(box) != 4
                    or any(type(value) not in (int, float) or not 0 <= value <= 1 for value in box)
                    or box[0] >= box[2] or box[1] >= box[3]):
                raise ValueError("invalid normalized image region")
            result.append({"text": text, "box": box})
        else:
            start, end = segment.get("start_seconds"), segment.get("end_seconds")
            if (type(start) not in (int, float) or type(end) not in (int, float)
                    or not 0 <= start < end <= duration):
                raise ValueError("audio segment is outside the original clip")
            result.append({"text": text, "start_seconds": start, "end_seconds": end})
    return result


def windows_process(kind, path, culture):
    if os.name != "nt":
        raise ValueError("local media analysis currently requires Windows")
    name = "Image" if kind == "image" else "Audio"
    helper = Path(__file__).resolve().parents[1] / "scripts" / f"Analyze-PCV{name}.ps1"
    if not helper.is_file():
        raise ValueError("Windows processor script is unavailable; run PCV from the repository checkout")
    command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "RemoteSigned",
               "-File", str(helper), "-Path", str(path)]
    if culture:
        command.extend(["-Culture", culture])
    try:
        result = subprocess.run(command, capture_output=True, timeout=90,
                                creationflags=subprocess.CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise ValueError("local media analysis timed out") from None
    if result.returncode:
        raise ValueError(f"Windows {name.lower()} processor failed. Check the installed language and input format.")
    try:
        return json.loads(result.stdout.decode("utf-8-sig"))
    except ValueError:
        raise ValueError("local processor returned invalid JSON") from None


def analyze(store, parent_id, culture="", *, processor=windows_process):
    from .agent_tasks import active_evidence

    active_evidence(store, parent_id)
    asset = store.connection.execute("SELECT * FROM media_assets WHERE evidence_id=?", (parent_id,)).fetchone()
    if asset is None:
        raise ValueError("evidence is not an imported media asset")
    if not asset["data"]:
        raise ValueError("original media payload has expired")
    if hashlib.sha256(asset["data"]).hexdigest() != asset["sha256"]:
        raise ValueError("media hash mismatch")
    processor_name = "windows-ocr-v1" if asset["kind"] == "image" else "windows-speech-v1"
    culture = culture.strip()
    if len(culture) > 32 or any(not (character.isalnum() or character == "-") for character in culture):
        raise ValueError("invalid language tag")
    key = (parent_id, processor_name, culture)
    existing = store.connection.execute(
        "SELECT evidence_id FROM media_derivations WHERE parent_id=? AND processor=? AND culture=?", key
    ).fetchone()
    if existing:
        return {"evidence_id": existing["evidence_id"], "parent_evidence_id": parent_id, "created": False}
    with tempfile.TemporaryDirectory(prefix="pcv-local-media-") as directory:
        path = Path(directory) / ("sample" + asset["suffix"])
        path.write_bytes(asset["data"])
        segments = validate_segments(asset["kind"], processor(asset["kind"], path, culture),
                                     asset["duration_seconds"])
    with store.connection:
        store.connection.execute("BEGIN IMMEDIATE")
        parent = active_evidence(store, parent_id)
        existing = store.connection.execute(
            "SELECT evidence_id FROM media_derivations WHERE parent_id=? AND processor=? AND culture=?",
            (parent_id, processor_name, culture),
        ).fetchone()
        if existing:
            return {"evidence_id": existing["evidence_id"], "parent_evidence_id": parent_id, "created": False}
        content = json.dumps({
            "kind": "media_ocr" if asset["kind"] == "image" else "media_transcript",
            "parent_evidence_id": parent_id, "parent_sha256": asset["sha256"],
            "processor": processor_name, "culture": culture or None,
            "segments": segments,
        }, ensure_ascii=False)
        if len(content) > 100000:
            raise ValueError("derived text is too large; use a smaller sample")
        evidence_id = store._insert_evidence(
            source_id=parent["source_id"], title=parent["title"] + " / " + processor_name,
            content=content, locator=f"pcv:evidence:{parent_id}",
            occurred_at=parent["occurred_at"], sensitivity=parent["sensitivity"],
        )
        store.connection.execute("INSERT INTO media_derivations VALUES (?,?,?,?)",
                                 (evidence_id, parent_id, processor_name, culture))
    return {"evidence_id": evidence_id, "parent_evidence_id": parent_id,
            "segments": len(segments), "created": True}


def task_media(store, evidence_id):
    if not store.connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='media_assets'"
    ).fetchone():
        return []
    if store.connection.execute("SELECT 1 FROM media_assets WHERE evidence_id=?", (evidence_id,)).fetchone():
        raise ValueError("run media analyze and prepare the resulting text evidence")
    if store.connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='stream_analysis'"
    ).fetchone():
        assets = store.connection.execute("""
            SELECT sa.evidence_id, ma.kind, ma.mime, ma.sha256, ma.duration_seconds,
                   sa.position_seconds, length(ma.data) > 0 AS payload_available
            FROM stream_analysis a JOIN stream_assets sa USING(segment_id)
            JOIN media_assets ma ON ma.evidence_id=sa.evidence_id
            WHERE a.evidence_id=? ORDER BY sa.evidence_id
        """, (evidence_id,)).fetchall()
        if assets:
            return [dict(asset) for asset in assets]
    row = store.connection.execute("""
        SELECT d.parent_id AS evidence_id, a.kind, a.mime, a.sha256, a.duration_seconds,
               d.processor, d.culture
        FROM media_derivations d JOIN media_assets a ON a.evidence_id=d.parent_id
        WHERE d.evidence_id=?
    """, (evidence_id,)).fetchone()
    return [dict(row)] if row else []


def export_asset(store, evidence_id, output):
    from .agent_tasks import active_evidence

    active_evidence(store, evidence_id)
    asset = store.connection.execute(
        "SELECT mime, sha256, data FROM media_assets WHERE evidence_id=?", (evidence_id,)
    ).fetchone()
    if asset is None or not asset["data"]:
        raise ValueError("original media payload is unavailable")
    if FORMATS.get(output.suffix.lower(), (None, None))[1] != asset["mime"]:
        raise ValueError("output extension must match the original media type")
    if hashlib.sha256(asset["data"]).hexdigest() != asset["sha256"]:
        raise ValueError("media hash mismatch")
    with output.open("xb") as destination:
        destination.write(asset["data"])
    return {"evidence_id": evidence_id, "sha256": asset["sha256"], "output": str(output.resolve())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=".pcv/vault.db")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser("add")
    ingest.add_argument("--source", type=int, required=True)
    ingest.add_argument("--file", type=Path, required=True)
    ingest.add_argument("--title", required=True)
    ingest.add_argument("--sensitivity", choices=("low", "medium", "high"), default="medium")
    derive = commands.add_parser("analyze")
    derive.add_argument("--evidence", type=int, required=True)
    derive.add_argument("--culture", default="")
    export = commands.add_parser("export")
    export.add_argument("--evidence", type=int, required=True)
    export.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not Path(args.db).expanduser().is_file():
        parser.error("vault does not exist; run pcv init first")
    try:
        with closing(Store(args.db)) as store:
            initialize(store)
            if args.command == "add":
                result = add(store, args.source, args.file, args.title, args.sensitivity)
            elif args.command == "analyze":
                result = analyze(store, args.evidence, args.culture)
            else:
                result = export_asset(store, args.evidence, args.output)
        print(json.dumps(result, ensure_ascii=True, indent=2))
    except (ValueError, OSError, sqlite3.Error) as error:
        parser.exit(2, str(error) + "\n")


if __name__ == "__main__":
    main()
