from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .store import Store


SUPPORTED_FORMATS = {"auto", "text", "json", "chatgpt", "douyin"}
TEXT_SUFFIXES = {".md", ".txt"}
MAX_FILE_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class CollectedItem:
    external_id: str
    title: str
    content: str
    locator: str
    occurred_at: str | None = None


def sync_connector(store: Store, connector_id: int) -> dict[str, Any]:
    connector = store.get_connector(connector_id)
    if not connector["enabled"]:
        raise ValueError("connector is disabled")
    if connector["source_status"] != "active":
        raise ValueError("connector source must be active")

    items, errors = collect_path(
        Path(connector["input_path"]),
        connector["format"],
        content_scope=connector["content_scope"],
    )
    imported = 0
    skipped = 0
    failed = len(errors)
    for item in items:
        try:
            evidence_id = store.import_connector_evidence(
                connector_id=connector_id,
                external_id=item.external_id,
                title=item.title,
                content=item.content,
                locator=item.locator,
                occurred_at=item.occurred_at,
            )
        except (OSError, ValueError) as exc:
            failed += 1
            errors.append(str(exc))
            continue
        if evidence_id is None:
            skipped += 1
        else:
            imported += 1

    error = "; ".join(errors[:3]) if errors else None
    store.record_connector_sync(connector_id, error=error)
    return {
        "connector_id": connector_id,
        "discovered": len(items),
        "imported": imported,
        "skipped": skipped,
        "failed": failed,
        "errors": errors[:10],
    }


def collect_path(
    path: Path,
    format_name: str,
    *,
    content_scope: str = "self_authored",
) -> tuple[list[CollectedItem], list[str]]:
    if format_name not in SUPPORTED_FORMATS:
        raise ValueError(
            f"format must be one of: {', '.join(sorted(SUPPORTED_FORMATS))}"
        )
    path = path.expanduser().resolve()
    if not path.exists():
        raise ValueError(f"input path does not exist: {path}")
    files = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file())
    items: list[CollectedItem] = []
    errors: list[str] = []
    for file in files:
        if file.suffix.lower() not in TEXT_SUFFIXES | {".json"}:
            continue
        try:
            if file.stat().st_size > MAX_FILE_BYTES:
                raise ValueError(f"file exceeds {MAX_FILE_BYTES} bytes")
            items.extend(_collect_file(file, format_name, content_scope))
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"{file}: {exc}")
    return items, errors


def _collect_file(
    path: Path, format_name: str, content_scope: str
) -> list[CollectedItem]:
    selected = format_name
    if selected == "auto":
        selected = _detect_format(path)
    if selected == "text":
        return [_text_item(path)]
    if path.suffix.lower() != ".json":
        raise ValueError(f"{selected} format requires JSON")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if selected == "chatgpt":
        return list(_chatgpt_items(path, data, content_scope))
    if selected == "douyin":
        return list(_douyin_items(path, data))
    if selected == "json":
        return list(_json_items(path, data))
    raise ValueError(f"unsupported format: {selected}")


def _detect_format(path: Path) -> str:
    if path.suffix.lower() in TEXT_SUFFIXES:
        return "text"
    if path.suffix.lower() != ".json":
        raise ValueError("unsupported file type")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    name = path.name.lower()
    if name == "conversations.json" or (
        isinstance(data, list)
        and data
        and isinstance(data[0], dict)
        and "mapping" in data[0]
    ):
        return "chatgpt"
    if "douyin" in name or (
        isinstance(data, dict)
        and any(key in data for key in ("recommendation", "history", "likes", "favorites"))
    ):
        return "douyin"
    return "json"


def _text_item(path: Path) -> CollectedItem:
    content = path.read_text(encoding="utf-8-sig")
    digest = _digest(content)
    return CollectedItem(
        external_id=f"{path.name}:{digest[:16]}",
        title=path.stem,
        content=content,
        locator=str(path),
    )


def _chatgpt_items(
    path: Path, data: Any, content_scope: str
) -> Iterable[CollectedItem]:
    if not isinstance(data, list):
        raise ValueError("ChatGPT export must be a conversation list")
    for conversation in data:
        if not isinstance(conversation, dict):
            continue
        conversation_id = str(conversation.get("id") or _digest(_canonical(conversation))[:16])
        title = str(conversation.get("title") or "Untitled conversation")
        mapping = conversation.get("mapping") or {}
        if not isinstance(mapping, dict):
            continue
        for node_id, node in mapping.items():
            if not isinstance(node, dict):
                continue
            message = node.get("message")
            if not isinstance(message, dict):
                continue
            author = message.get("author") or {}
            if not isinstance(author, dict):
                continue
            role = str(author.get("role") or "unknown")
            if content_scope == "self_authored" and role != "user":
                continue
            text = _message_text(message.get("content"))
            if not text.strip():
                continue
            message_id = str(message.get("id") or node_id)
            content = _canonical(
                {
                    "platform": "chatgpt",
                    "actor_role": role,
                    "conversation_title": title,
                    "text": text,
                }
            )
            yield CollectedItem(
                external_id=f"chatgpt:{conversation_id}:{message_id}",
                title=f"ChatGPT / {role} / {title}",
                content=content,
                locator=f"{path}#{conversation_id}/{message_id}",
                occurred_at=_timestamp(message.get("create_time")),
            )


def _douyin_items(path: Path, data: Any) -> Iterable[CollectedItem]:
    if not isinstance(data, dict):
        raise ValueError("Douyin export must be a JSON object")
    buckets: list[tuple[str, list[Any]]] = []
    for key in ("recommendation", "history", "likes", "favorites", "comments"):
        value = data.get(key)
        if isinstance(value, list):
            buckets.append((key, value))
    if isinstance(data.get("items"), list):
        inferred = next(
            (name for name in ("recommendation", "history", "likes", "favorites", "comments") if name in path.stem.lower()),
            "items",
        )
        buckets.append((inferred, data["items"]))
    if not buckets:
        raise ValueError("no supported Douyin item arrays found")

    for bucket, records in buckets:
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                continue
            wrapped = {"platform": "douyin", "event_type": bucket, "record": record}
            content = _canonical(wrapped)
            identity = (
                record.get("content_id")
                or record.get("video_id")
                or record.get("id")
                or _digest(content)[:20]
            )
            label = record.get("title") or record.get("description") or record.get("topic")
            author = record.get("author") or record.get("video_author")
            summary = " - ".join(str(value) for value in (author, label) if value)
            yield CollectedItem(
                external_id=f"douyin:{bucket}:{identity}:{index}",
                title=f"Douyin / {bucket}" + (f" / {summary[:120]}" if summary else ""),
                content=content,
                locator=str(record.get("url") or record.get("video_url") or path),
                occurred_at=_timestamp(record.get("captured_at_utc")),
            )


def _json_items(path: Path, data: Any) -> Iterable[CollectedItem]:
    records: list[Any]
    if isinstance(data, list):
        records = data
    elif isinstance(data, dict) and isinstance(data.get("items"), list):
        records = data["items"]
    else:
        records = [data]
    for index, record in enumerate(records):
        content = _canonical(record)
        identity = None
        title = f"JSON item {index + 1}"
        occurred_at = None
        locator = str(path)
        if isinstance(record, dict):
            identity = record.get("id") or record.get("uuid") or record.get("content_id")
            title = str(
                record.get("title")
                or record.get("name")
                or record.get("topic")
                or title
            )
            occurred_at = _timestamp(
                record.get("created_at")
                or record.get("create_time")
                or record.get("timestamp")
                or record.get("date")
            )
            locator = str(record.get("url") or record.get("locator") or path)
        yield CollectedItem(
            external_id=f"json:{identity or _digest(content)[:20]}",
            title=title[:160],
            content=content,
            locator=locator,
            occurred_at=occurred_at,
        )


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, dict):
        return ""
    parts = content.get("parts") or []
    rendered = []
    for part in parts:
        if isinstance(part, str):
            rendered.append(part)
        elif isinstance(part, dict) and isinstance(part.get("text"), str):
            rendered.append(part["text"])
    return "\n".join(rendered)


def _timestamp(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
