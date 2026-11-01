from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from .store import (
    CONNECTOR_ACCESS_MODES,
    CONNECTOR_CONTENT_SCOPES,
    SENSITIVITIES,
    Store,
)
from .connectors import SUPPORTED_FORMATS, sync_connector


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _parse_evidence_refs(raw: str) -> list[tuple[int, str]]:
    refs: list[tuple[int, str]] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            evidence_id, relation = item.split(":", 1)
            refs.append((int(evidence_id), relation.strip()))
        except ValueError as exc:
            raise argparse.ArgumentTypeError(
                "evidence must look like 1:supports,2:contradicts"
            ) from exc
    if not refs:
        raise argparse.ArgumentTypeError("at least one evidence reference is required")
    return refs


def _markdown_profile(profile: dict[str, Any]) -> str:
    lines = [
        "# Personal Context Profile",
        "",
        f"Generated: {profile['generated_at']}",
        "",
        "Only confirmed claims with active evidence are included.",
        "",
    ]
    if not profile["claims"]:
        lines.append("No confirmed claims.")
    for claim in profile["claims"]:
        lines.extend(
            [
                f"## {claim['statement']}",
                "",
                f"- Type: `{claim['claim_type']}`",
                f"- Confidence: {claim['confidence']}",
                f"- Sensitivity: `{claim['sensitivity']}`",
            ]
        )
        if claim["valid_from"] or claim["valid_to"]:
            lines.append(
                f"- Valid: {claim['valid_from'] or 'unknown'} to "
                f"{claim['valid_to'] or 'present'}"
            )
        lines.append("- Evidence:")
        for evidence in claim["evidence"]:
            lines.append(
                f"  - {evidence['relation']}: {evidence['source_name']} / "
                f"{evidence['title']} / `{evidence['content_hash'][:12]}`"
            )
        lines.append("")
    if profile.get("pending_review"):
        lines.extend(["## Pending Review", ""])
        for claim in profile["pending_review"]:
            lines.append(
                f"- [{claim['claim_type']}] {claim['statement']} "
                f"({claim['confidence']})"
            )
        lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pcv", description="Local-first evidence-backed personal context"
    )
    parser.add_argument("--db", default=".pcv/vault.db", help="SQLite database path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="initialize the database")

    source = subparsers.add_parser("source", help="manage authorized sources")
    source_sub = source.add_subparsers(dest="source_command", required=True)
    source_add = source_sub.add_parser("add", help="register an authorized source")
    source_add.add_argument("--name", required=True)
    source_add.add_argument("--kind", required=True)
    source_add.add_argument("--scope", required=True)
    source_add.add_argument("--basis", required=True)
    source_add.add_argument("--account-ref")
    source_add.add_argument("--retention-days", type=int)
    source_add.add_argument("--expires-at")
    source_add.add_argument("--notes")
    source_sub.add_parser("list", help="list sources")
    source_revoke = source_sub.add_parser("revoke", help="revoke a source")
    source_revoke.add_argument("--id", type=int, required=True)
    source_delete = source_sub.add_parser("delete", help="delete a source and evidence")
    source_delete.add_argument("--id", type=int, required=True)
    source_delete.add_argument("--yes", action="store_true", help="confirm deletion")

    evidence = subparsers.add_parser("evidence", help="manage raw evidence")
    evidence_sub = evidence.add_subparsers(dest="evidence_command", required=True)
    evidence_add = evidence_sub.add_parser("add", help="add raw evidence")
    evidence_add.add_argument("--source", type=int, required=True)
    evidence_add.add_argument("--title", required=True)
    evidence_input = evidence_add.add_mutually_exclusive_group(required=True)
    evidence_input.add_argument("--content")
    evidence_input.add_argument("--file", type=Path)
    evidence_add.add_argument("--locator")
    evidence_add.add_argument("--occurred-at")
    evidence_add.add_argument(
        "--sensitivity", choices=sorted(SENSITIVITIES), default="medium"
    )
    evidence_list = evidence_sub.add_parser("list", help="list evidence metadata")
    evidence_list.add_argument("--content", action="store_true")

    connector = subparsers.add_parser(
        "connector", help="continuously import local platform exports"
    )
    connector_sub = connector.add_subparsers(dest="connector_command", required=True)
    connector_add = connector_sub.add_parser("add", help="register an import path")
    connector_add.add_argument("--source", type=int, required=True)
    connector_add.add_argument("--name", required=True)
    connector_add.add_argument("--platform", required=True)
    connector_add.add_argument(
        "--format", choices=sorted(SUPPORTED_FORMATS), default="auto"
    )
    connector_add.add_argument("--path", type=Path, required=True)
    connector_add.add_argument(
        "--access-mode",
        choices=sorted(CONNECTOR_ACCESS_MODES),
        default="local_export",
    )
    connector_add.add_argument(
        "--content-scope",
        choices=sorted(CONNECTOR_CONTENT_SCOPES),
        default="self_authored",
    )
    connector_add.add_argument(
        "--allow-session-use",
        action="store_true",
        help="allow ephemeral use of an already logged-in browser session",
    )
    connector_add.add_argument("--disabled", action="store_true")
    connector_sub.add_parser("list", help="list connectors")
    connector_enable = connector_sub.add_parser("enable")
    connector_enable.add_argument("--id", type=int, required=True)
    connector_disable = connector_sub.add_parser("disable")
    connector_disable.add_argument("--id", type=int, required=True)
    connector_sync = connector_sub.add_parser("sync", help="run one incremental sync")
    connector_sync.add_argument("--id", type=int)
    connector_watch = connector_sub.add_parser("watch", help="poll connectors in foreground")
    connector_watch.add_argument("--id", type=int)
    connector_watch.add_argument("--interval", type=float, default=60.0)
    connector_watch.add_argument("--once", action="store_true")

    claim = subparsers.add_parser("claim", help="manage claims")
    claim_sub = claim.add_subparsers(dest="claim_command", required=True)
    claim_add = claim_sub.add_parser("add", help="propose a claim")
    claim_add.add_argument("--statement", required=True)
    claim_add.add_argument(
        "--type",
        required=True,
        choices=[
            "direct_fact",
            "observation",
            "inference",
            "preference",
            "relationship",
            "trait",
        ],
    )
    claim_add.add_argument("--evidence", required=True, type=_parse_evidence_refs)
    claim_add.add_argument("--confidence", type=float, default=0.5)
    claim_add.add_argument(
        "--sensitivity", choices=sorted(SENSITIVITIES), default="medium"
    )
    claim_add.add_argument("--valid-from")
    claim_add.add_argument("--valid-to")
    claim_list = claim_sub.add_parser("list", help="list claims")
    claim_list.add_argument(
        "--status",
        choices=["pending", "confirmed", "rejected", "stale", "needs_review"],
    )
    claim_review = claim_sub.add_parser("review", help="review a claim")
    claim_review.add_argument("--id", type=int, required=True)
    claim_review.add_argument(
        "--status",
        required=True,
        choices=["pending", "confirmed", "rejected", "stale", "needs_review"],
    )
    claim_review.add_argument("--allow-conflicts", action="store_true")

    export = subparsers.add_parser("export", help="export the confirmed profile")
    export.add_argument("--format", choices=["json", "markdown"], default="json")
    export.add_argument("--output", type=Path)
    export.add_argument("--include-pending", action="store_true")

    audit = subparsers.add_parser("audit", help="inspect audit events")
    audit_sub = audit.add_subparsers(dest="audit_command", required=True)
    audit_sub.add_parser("list")
    return parser


def run(args: argparse.Namespace) -> Any:
    store = Store(args.db)
    try:
        if args.command == "init":
            store.init()
            return {"ok": True, "database": str(store.path)}

        if args.command == "source":
            if args.source_command == "add":
                source_id = store.add_source(
                    name=args.name,
                    kind=args.kind,
                    access_scope=args.scope,
                    legal_basis=args.basis,
                    account_ref=args.account_ref,
                    retention_days=args.retention_days,
                    expires_at=args.expires_at,
                    notes=args.notes,
                )
                return {"ok": True, "source_id": source_id}
            if args.source_command == "list":
                return {"sources": store.list_sources()}
            if args.source_command == "revoke":
                store.revoke_source(args.id)
                return {"ok": True, "source_id": args.id, "status": "revoked"}
            if args.source_command == "delete":
                result = store.delete_source(args.id, confirmed=args.yes)
                return {"ok": True, "source_id": args.id, **result}

        if args.command == "evidence":
            if args.evidence_command == "add":
                content = args.content
                if args.file:
                    content = args.file.read_text(encoding="utf-8")
                evidence_id = store.add_evidence(
                    source_id=args.source,
                    title=args.title,
                    content=content,
                    locator=args.locator,
                    occurred_at=args.occurred_at,
                    sensitivity=args.sensitivity,
                )
                return {"ok": True, "evidence_id": evidence_id}
            if args.evidence_command == "list":
                return {
                    "evidence": store.list_evidence(include_content=args.content)
                }

        if args.command == "connector":
            if args.connector_command == "add":
                connector_id = store.add_connector(
                    source_id=args.source,
                    name=args.name,
                    platform=args.platform,
                    format_name=args.format,
                    input_path=args.path,
                    access_mode=args.access_mode,
                    content_scope=args.content_scope,
                    session_policy=(
                        "ephemeral_browser_session"
                        if args.allow_session_use
                        else "none"
                    ),
                    enabled=not args.disabled,
                )
                return {"ok": True, "connector_id": connector_id}
            if args.connector_command == "list":
                return {"connectors": store.list_connectors()}
            if args.connector_command in {"enable", "disable"}:
                enabled = args.connector_command == "enable"
                store.set_connector_enabled(args.id, enabled)
                return {"ok": True, "connector_id": args.id, "enabled": enabled}
            if args.connector_command in {"sync", "watch"}:
                if args.connector_command == "watch" and args.interval <= 0:
                    raise ValueError("interval must be positive")
                while True:
                    if args.id:
                        connector_ids = [args.id]
                    else:
                        connector_ids = [
                            item["id"] for item in store.list_connectors(enabled_only=True)
                        ]
                    reports = [sync_connector(store, item) for item in connector_ids]
                    if args.connector_command == "sync":
                        return {"reports": reports}
                    _print({"reports": reports})
                    if args.once:
                        return None
                    time.sleep(args.interval)

        if args.command == "claim":
            if args.claim_command == "add":
                claim_id = store.add_claim(
                    statement=args.statement,
                    claim_type=args.type,
                    evidence_refs=args.evidence,
                    confidence=args.confidence,
                    sensitivity=args.sensitivity,
                    valid_from=args.valid_from,
                    valid_to=args.valid_to,
                )
                status = next(
                    claim["review_status"]
                    for claim in store.list_claims()
                    if claim["id"] == claim_id
                )
                return {"ok": True, "claim_id": claim_id, "status": status}
            if args.claim_command == "list":
                return {"claims": store.list_claims(review_status=args.status)}
            if args.claim_command == "review":
                store.review_claim(
                    args.id,
                    status=args.status,
                    allow_conflicts=args.allow_conflicts,
                )
                return {"ok": True, "claim_id": args.id, "status": args.status}

        if args.command == "export":
            profile = store.export_profile(include_pending=args.include_pending)
            if args.format == "markdown":
                rendered = _markdown_profile(profile)
            else:
                rendered = json.dumps(profile, ensure_ascii=False, indent=2)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(rendered + "\n", encoding="utf-8")
                return {"ok": True, "output": str(args.output.resolve())}
            print(rendered)
            return None

        if args.command == "audit" and args.audit_command == "list":
            return {"events": store.list_audit()}

        raise RuntimeError("unhandled command")
    finally:
        store.close()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        value = run(args)
    except (ValueError, OSError, sqlite3.Error, json.JSONDecodeError) as exc:
        parser.exit(2, f"error: {exc}\n")
    if value is not None:
        _print(value)
    return 0
