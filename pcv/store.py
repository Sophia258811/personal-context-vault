from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


CLAIM_TYPES = {
    "direct_fact",
    "observation",
    "inference",
    "preference",
    "relationship",
    "trait",
}
SENSITIVITIES = {"low", "medium", "high"}
REVIEW_STATUSES = {"pending", "confirmed", "rejected", "stale", "needs_review"}
EVIDENCE_RELATIONS = {"supports", "contradicts"}
CONNECTOR_ACCESS_MODES = {"local_export", "official_api", "browser_assisted"}
CONNECTOR_CONTENT_SCOPES = {"self_authored", "all_authorized"}
CONNECTOR_SESSION_POLICIES = {"none", "ephemeral_browser_session"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class Store:
    def __init__(self, database: str | Path):
        self.path = Path(database).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")

    def close(self) -> None:
        self.connection.close()

    def init(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                account_ref TEXT,
                access_scope TEXT NOT NULL,
                legal_basis TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'revoked', 'expired')),
                retention_days INTEGER,
                authorized_at TEXT NOT NULL,
                expires_at TEXT,
                last_sync_at TEXT,
                notes TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS evidence (
                id INTEGER PRIMARY KEY,
                source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                locator TEXT,
                content_hash TEXT NOT NULL,
                occurred_at TEXT,
                collected_at TEXT NOT NULL,
                sensitivity TEXT NOT NULL DEFAULT 'medium'
                    CHECK (sensitivity IN ('low', 'medium', 'high'))
            );

            CREATE TABLE IF NOT EXISTS claims (
                id INTEGER PRIMARY KEY,
                statement TEXT NOT NULL,
                claim_type TEXT NOT NULL CHECK (
                    claim_type IN (
                        'direct_fact', 'observation', 'inference',
                        'preference', 'relationship', 'trait'
                    )
                ),
                confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
                sensitivity TEXT NOT NULL DEFAULT 'medium'
                    CHECK (sensitivity IN ('low', 'medium', 'high')),
                review_status TEXT NOT NULL DEFAULT 'pending' CHECK (
                    review_status IN (
                        'pending', 'confirmed', 'rejected', 'stale', 'needs_review'
                    )
                ),
                valid_from TEXT,
                valid_to TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS claim_evidence (
                claim_id INTEGER NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
                evidence_id INTEGER NOT NULL REFERENCES evidence(id) ON DELETE CASCADE,
                relation TEXT NOT NULL CHECK (relation IN ('supports', 'contradicts')),
                PRIMARY KEY (claim_id, evidence_id, relation)
            );

            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY,
                occurred_at TEXT NOT NULL,
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                object_type TEXT NOT NULL,
                object_id INTEGER,
                details_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS connectors (
                id INTEGER PRIMARY KEY,
                source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                platform TEXT NOT NULL,
                format TEXT NOT NULL,
                input_path TEXT NOT NULL,
                access_mode TEXT NOT NULL DEFAULT 'local_export' CHECK (
                    access_mode IN ('local_export', 'official_api', 'browser_assisted')
                ),
                content_scope TEXT NOT NULL DEFAULT 'self_authored' CHECK (
                    content_scope IN ('self_authored', 'all_authorized')
                ),
                session_policy TEXT NOT NULL DEFAULT 'none' CHECK (
                    session_policy IN ('none', 'ephemeral_browser_session')
                ),
                enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                last_sync_at TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS imported_items (
                id INTEGER PRIMARY KEY,
                connector_id INTEGER NOT NULL
                    REFERENCES connectors(id) ON DELETE CASCADE,
                evidence_id INTEGER NOT NULL
                    REFERENCES evidence(id) ON DELETE CASCADE,
                external_id TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                locator TEXT,
                imported_at TEXT NOT NULL,
                UNIQUE (connector_id, external_id),
                UNIQUE (connector_id, content_hash)
            );

            CREATE INDEX IF NOT EXISTS idx_evidence_source ON evidence(source_id);
            CREATE INDEX IF NOT EXISTS idx_claim_status ON claims(review_status);
            CREATE INDEX IF NOT EXISTS idx_claim_evidence_claim ON claim_evidence(claim_id);
            CREATE INDEX IF NOT EXISTS idx_connectors_source ON connectors(source_id);
            CREATE INDEX IF NOT EXISTS idx_imported_connector ON imported_items(connector_id);
            PRAGMA user_version = 2;
            """
        )
        self.connection.commit()
        try:
            os.chmod(self.path.parent, 0o700)
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _audit(
        self,
        action: str,
        object_type: str,
        object_id: int | None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO audit_events
                (occurred_at, actor, action, object_type, object_id, details_json)
            VALUES (?, 'local_user', ?, ?, ?, ?)
            """,
            (
                utc_now(),
                action,
                object_type,
                object_id,
                json.dumps(details or {}, ensure_ascii=True, sort_keys=True),
            ),
        )

    def add_source(
        self,
        *,
        name: str,
        kind: str,
        access_scope: str,
        legal_basis: str,
        account_ref: str | None = None,
        retention_days: int | None = None,
        expires_at: str | None = None,
        notes: str | None = None,
    ) -> int:
        if (
            not name.strip()
            or not kind.strip()
            or not access_scope.strip()
            or not legal_basis.strip()
        ):
            raise ValueError("name, kind, access_scope and legal_basis are required")
        if retention_days is not None and retention_days < 1:
            raise ValueError("retention_days must be positive")
        now = utc_now()
        cursor = self.connection.execute(
            """
            INSERT INTO sources (
                name, kind, account_ref, access_scope, legal_basis,
                retention_days, authorized_at, expires_at, notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name.strip(),
                kind.strip(),
                account_ref,
                access_scope.strip(),
                legal_basis.strip(),
                retention_days,
                now,
                expires_at,
                notes,
                now,
            ),
        )
        source_id = int(cursor.lastrowid)
        self._audit("source.add", "source", source_id, {"kind": kind.strip()})
        self.connection.commit()
        return source_id

    def list_sources(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT id, name, kind, account_ref, access_scope, legal_basis,
                   status, retention_days, authorized_at, expires_at,
                   last_sync_at, notes, created_at
            FROM sources ORDER BY id
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def revoke_source(self, source_id: int) -> None:
        self._require_source(source_id)
        self.connection.execute(
            "UPDATE sources SET status = 'revoked' WHERE id = ?", (source_id,)
        )
        self._refresh_claims_for_source(source_id)
        self._audit("source.revoke", "source", source_id)
        self.connection.commit()

    def delete_source(self, source_id: int, *, confirmed: bool) -> dict[str, int]:
        if not confirmed:
            raise ValueError("source deletion requires explicit confirmation")
        self._require_source(source_id)
        claim_ids = [
            int(row["claim_id"])
            for row in self.connection.execute(
                "SELECT DISTINCT claim_id FROM claim_evidence WHERE evidence_id IN "
                "(SELECT id FROM evidence WHERE source_id = ?)",
                (source_id,),
            )
        ]
        self.connection.execute("DELETE FROM sources WHERE id = ?", (source_id,))
        changed = 0
        for claim_id in claim_ids:
            evidence_count = self.connection.execute(
                "SELECT COUNT(*) FROM claim_evidence WHERE claim_id = ?", (claim_id,)
            ).fetchone()[0]
            if evidence_count == 0:
                cursor = self.connection.execute(
                    """
                    UPDATE claims
                    SET review_status = 'needs_review', updated_at = ?
                    WHERE id = ? AND review_status != 'rejected'
                    """,
                    (utc_now(), claim_id),
                )
                changed += cursor.rowcount
        self._audit(
            "source.delete",
            "source",
            source_id,
            {"affected_claims": len(claim_ids), "claims_marked_needs_review": changed},
        )
        self.connection.commit()
        return {
            "affected_claims": len(claim_ids),
            "claims_marked_needs_review": changed,
        }

    def add_evidence(
        self,
        *,
        source_id: int,
        title: str,
        content: str,
        locator: str | None = None,
        occurred_at: str | None = None,
        sensitivity: str = "medium",
    ) -> int:
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            return self._insert_evidence(
                source_id=source_id, title=title, content=content, locator=locator,
                occurred_at=occurred_at, sensitivity=sensitivity,
            )

    def _insert_evidence(
        self, *, source_id: int, title: str, content: str, locator: str | None = None,
        occurred_at: str | None = None, sensitivity: str = "medium",
    ) -> int:
        source = self._require_source(source_id)
        if source["status"] != "active":
            raise ValueError("source must be active")
        if not title.strip() or not content.strip():
            raise ValueError("title and content are required")
        self._validate_choice("sensitivity", sensitivity, SENSITIVITIES)
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        cursor = self.connection.execute(
            """
            INSERT INTO evidence (
                source_id, title, content, locator, content_hash,
                occurred_at, collected_at, sensitivity
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id,
                title.strip(),
                content,
                locator,
                digest,
                occurred_at,
                utc_now(),
                sensitivity,
            ),
        )
        evidence_id = int(cursor.lastrowid)
        self._audit(
            "evidence.add",
            "evidence",
            evidence_id,
            {"source_id": source_id, "content_hash": digest},
        )
        return evidence_id

    def list_evidence(self, *, include_content: bool = False) -> list[dict[str, Any]]:
        columns = (
            "id, source_id, title, locator, content_hash, occurred_at, "
            "collected_at, sensitivity"
        )
        if include_content:
            columns += ", content"
        rows = self.connection.execute(
            f"SELECT {columns} FROM evidence ORDER BY id"
        ).fetchall()
        return [dict(row) for row in rows]

    def add_claim(
        self,
        *,
        statement: str,
        claim_type: str,
        evidence_refs: Iterable[tuple[int, str]],
        confidence: float = 0.5,
        sensitivity: str = "medium",
        valid_from: str | None = None,
        valid_to: str | None = None,
    ) -> int:
        if not statement.strip():
            raise ValueError("statement is required")
        self._validate_choice("claim_type", claim_type, CLAIM_TYPES)
        self._validate_choice("sensitivity", sensitivity, SENSITIVITIES)
        if not 0 <= confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")

        refs = list(evidence_refs)
        if not refs:
            raise ValueError("at least one evidence reference is required")
        for evidence_id, relation in refs:
            self._validate_choice("relation", relation, EVIDENCE_RELATIONS)
            if self.connection.execute(
                "SELECT 1 FROM evidence WHERE id = ?", (evidence_id,)
            ).fetchone() is None:
                raise ValueError(f"evidence {evidence_id} does not exist")

        status = (
            "needs_review"
            if any(relation == "contradicts" for _, relation in refs)
            else "pending"
        )
        now = utc_now()
        cursor = self.connection.execute(
            """
            INSERT INTO claims (
                statement, claim_type, confidence, sensitivity, review_status,
                valid_from, valid_to, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                statement.strip(),
                claim_type,
                confidence,
                sensitivity,
                status,
                valid_from,
                valid_to,
                now,
                now,
            ),
        )
        claim_id = int(cursor.lastrowid)
        for evidence_id, relation in refs:
            self.connection.execute(
                """
                INSERT OR IGNORE INTO claim_evidence (claim_id, evidence_id, relation)
                VALUES (?, ?, ?)
                """,
                (claim_id, evidence_id, relation),
            )
        self._audit(
            "claim.add",
            "claim",
            claim_id,
            {"claim_type": claim_type, "evidence_count": len(refs)},
        )
        self.connection.commit()
        return claim_id

    def list_claims(self, *, review_status: str | None = None) -> list[dict[str, Any]]:
        if review_status is not None:
            self._validate_choice("review_status", review_status, REVIEW_STATUSES)
            rows = self.connection.execute(
                """
                SELECT id, statement, claim_type, confidence, sensitivity,
                       review_status, valid_from, valid_to, created_at, updated_at
                FROM claims WHERE review_status = ? ORDER BY id
                """,
                (review_status,),
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT id, statement, claim_type, confidence, sensitivity,
                       review_status, valid_from, valid_to, created_at, updated_at
                FROM claims ORDER BY id
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def review_claim(
        self,
        claim_id: int,
        *,
        status: str,
        allow_conflicts: bool = False,
        replaces: int | None = None,
    ) -> None:
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            if replaces is not None:
                if status != "confirmed" or replaces == claim_id:
                    raise ValueError("replacement requires a different claim and confirmed status")
                candidate = self.connection.execute(
                    "SELECT review_status FROM claims WHERE id = ? "
                    "AND (valid_from IS NULL OR julianday(valid_from) <= julianday('now')) "
                    "AND (valid_to IS NULL OR julianday(valid_to) > julianday('now'))", (claim_id,)
                ).fetchone()
                if candidate is None or candidate["review_status"] not in {"pending", "needs_review"}:
                    raise ValueError("replacement must be a pending or needs_review claim")
                retired = self.connection.execute(
                    "UPDATE claims SET review_status = 'stale', updated_at = ? "
                    "WHERE id = ? AND review_status = 'confirmed'",
                    (utc_now(), replaces),
                )
                if retired.rowcount != 1:
                    raise ValueError("replaced claim must be confirmed")
            self._review_claim(claim_id, status, allow_conflicts, replaces)

    def _review_claim(
        self, claim_id: int, status: str, allow_conflicts: bool, replaces: int | None
    ) -> None:
        self._validate_choice("review_status", status, REVIEW_STATUSES)
        claim = self.connection.execute(
            "SELECT id FROM claims WHERE id = ?", (claim_id,)
        ).fetchone()
        if claim is None:
            raise ValueError(f"claim {claim_id} does not exist")
        if status == "confirmed":
            supports = self._evidence_count(claim_id, "supports", active_only=True)
            contradictions = self._evidence_count(
                claim_id, "contradicts", active_only=True
            )
            if supports == 0:
                raise ValueError("claim cannot be confirmed without active supporting evidence")
            if contradictions and not allow_conflicts:
                raise ValueError(
                    "claim has active contradictory evidence; use --allow-conflicts "
                    "only after manual review"
                )
        self.connection.execute(
            "UPDATE claims SET review_status = ?, updated_at = ? WHERE id = ?",
            (status, utc_now(), claim_id),
        )
        self._audit(
            "claim.review",
            "claim",
            claim_id,
            {"status": status, "allow_conflicts": allow_conflicts,
             **({"replaces": replaces} if replaces is not None else {})},
        )

    def export_profile(
        self, *, include_pending: bool = False
    ) -> dict[str, Any]:
        confirmed = self._profile_claims("confirmed", require_active_support=True)
        pending = self._profile_claims("pending", require_active_support=False)
        pending += self._profile_claims("needs_review", require_active_support=False)
        profile: dict[str, Any] = {
            "generated_at": utc_now(),
            "principles": {
                "confirmed_only": True,
                "direct_facts_and_inferences_are_separate": True,
                "sensitive_inferences_require_user_confirmation": True,
            },
            "claims": confirmed,
        }
        if include_pending:
            profile["pending_review"] = pending
        return profile

    def list_audit(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT id, occurred_at, actor, action, object_type, object_id, details_json
            FROM audit_events ORDER BY id
            """
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["details"] = json.loads(item.pop("details_json"))
            result.append(item)
        return result

    def add_connector(
        self,
        *,
        source_id: int,
        name: str,
        platform: str,
        format_name: str,
        input_path: str | Path,
        access_mode: str = "local_export",
        content_scope: str = "self_authored",
        session_policy: str = "none",
        enabled: bool = True,
    ) -> int:
        source = self._require_source(source_id)
        if source["status"] != "active":
            raise ValueError("source must be active")
        if not name.strip() or not platform.strip() or not format_name.strip():
            raise ValueError("name, platform and format are required")
        self._validate_choice("access_mode", access_mode, CONNECTOR_ACCESS_MODES)
        self._validate_choice("content_scope", content_scope, CONNECTOR_CONTENT_SCOPES)
        self._validate_choice(
            "session_policy", session_policy, CONNECTOR_SESSION_POLICIES
        )
        if access_mode == "browser_assisted":
            if session_policy != "ephemeral_browser_session":
                raise ValueError(
                    "browser_assisted requires ephemeral_browser_session consent"
                )
        elif session_policy != "none":
            raise ValueError(
                "ephemeral_browser_session is only valid for browser_assisted"
            )
        path = Path(input_path).expanduser().resolve()
        if not path.exists():
            raise ValueError(f"input path does not exist: {path}")
        now = utc_now()
        cursor = self.connection.execute(
            """
            INSERT INTO connectors (
                source_id, name, platform, format, input_path,
                access_mode, content_scope, session_policy, enabled,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id,
                name.strip(),
                platform.strip(),
                format_name.strip(),
                str(path),
                access_mode,
                content_scope,
                session_policy,
                int(enabled),
                now,
                now,
            ),
        )
        connector_id = int(cursor.lastrowid)
        self._audit(
            "connector.add",
            "connector",
            connector_id,
            {"source_id": source_id, "platform": platform.strip()},
        )
        self.connection.commit()
        return connector_id

    def list_connectors(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        where = "WHERE c.enabled = 1" if enabled_only else ""
        rows = self.connection.execute(
            f"""
            SELECT c.id, c.source_id, c.name, c.platform, c.format,
                   c.input_path, c.access_mode, c.content_scope,
                   c.session_policy, c.enabled, c.last_sync_at, c.last_error,
                   c.created_at, c.updated_at, s.status AS source_status
            FROM connectors c
            JOIN sources s ON s.id = c.source_id
            {where}
            ORDER BY c.id
            """
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["enabled"] = bool(item["enabled"])
            result.append(item)
        return result

    def get_connector(self, connector_id: int) -> dict[str, Any]:
        row = self.connection.execute(
            """
            SELECT c.id, c.source_id, c.name, c.platform, c.format,
                   c.input_path, c.access_mode, c.content_scope,
                   c.session_policy, c.enabled, c.last_sync_at, c.last_error,
                   c.created_at, c.updated_at, s.status AS source_status
            FROM connectors c
            JOIN sources s ON s.id = c.source_id
            WHERE c.id = ?
            """,
            (connector_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"connector {connector_id} does not exist")
        result = dict(row)
        result["enabled"] = bool(result["enabled"])
        return result

    def set_connector_enabled(self, connector_id: int, enabled: bool) -> None:
        self.get_connector(connector_id)
        self.connection.execute(
            "UPDATE connectors SET enabled = ?, updated_at = ? WHERE id = ?",
            (int(enabled), utc_now(), connector_id),
        )
        self._audit(
            "connector.enable" if enabled else "connector.disable",
            "connector",
            connector_id,
        )
        self.connection.commit()

    def import_connector_evidence(
        self,
        *,
        connector_id: int,
        external_id: str,
        title: str,
        content: str,
        locator: str | None = None,
        occurred_at: str | None = None,
        sensitivity: str = "medium",
    ) -> int | None:
        connector = self.get_connector(connector_id)
        if not connector["enabled"]:
            raise ValueError("connector is disabled")
        if connector["source_status"] != "active":
            raise ValueError("connector source must be active")
        if not external_id.strip() or not title.strip() or not content.strip():
            raise ValueError("external_id, title and content are required")
        self._validate_choice("sensitivity", sensitivity, SENSITIVITIES)
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        duplicate = self.connection.execute(
            """
            SELECT id FROM imported_items
            WHERE connector_id = ? AND (external_id = ? OR content_hash = ?)
            """,
            (connector_id, external_id.strip(), digest),
        ).fetchone()
        if duplicate is not None:
            return None

        now = utc_now()
        cursor = self.connection.execute(
            """
            INSERT INTO evidence (
                source_id, title, content, locator, content_hash,
                occurred_at, collected_at, sensitivity
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                connector["source_id"],
                title.strip(),
                content,
                locator,
                digest,
                occurred_at,
                now,
                sensitivity,
            ),
        )
        evidence_id = int(cursor.lastrowid)
        self.connection.execute(
            """
            INSERT INTO imported_items (
                connector_id, evidence_id, external_id, content_hash,
                locator, imported_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                connector_id,
                evidence_id,
                external_id.strip(),
                digest,
                locator,
                now,
            ),
        )
        self._audit(
            "connector.import",
            "evidence",
            evidence_id,
            {"connector_id": connector_id, "content_hash": digest},
        )
        self.connection.commit()
        return evidence_id

    def record_connector_sync(
        self, connector_id: int, *, error: str | None = None
    ) -> None:
        self.get_connector(connector_id)
        now = utc_now()
        self.connection.execute(
            """
            UPDATE connectors
            SET last_sync_at = ?, last_error = ?, updated_at = ?
            WHERE id = ?
            """,
            (now, error, now, connector_id),
        )
        self._audit(
            "connector.sync",
            "connector",
            connector_id,
            {"ok": error is None},
        )
        self.connection.commit()

    def _require_source(self, source_id: int) -> sqlite3.Row:
        row = self.connection.execute(
            "SELECT id, status FROM sources WHERE id = ?", (source_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"source {source_id} does not exist")
        return row

    def _evidence_count(
        self, claim_id: int, relation: str, *, active_only: bool
    ) -> int:
        active_clause = (
            "AND s.status = 'active' "
            "AND (s.expires_at IS NULL OR julianday(s.expires_at) > julianday('now'))"
            if active_only else ""
        )
        row = self.connection.execute(
            f"""
            SELECT COUNT(*)
            FROM claim_evidence ce
            JOIN evidence e ON e.id = ce.evidence_id
            JOIN sources s ON s.id = e.source_id
            WHERE ce.claim_id = ? AND ce.relation = ? {active_clause}
            """,
            (claim_id, relation),
        ).fetchone()
        return int(row[0])

    def _refresh_claims_for_source(self, source_id: int) -> None:
        claim_ids = [
            int(row["claim_id"])
            for row in self.connection.execute(
                """
                SELECT DISTINCT ce.claim_id
                FROM claim_evidence ce
                JOIN evidence e ON e.id = ce.evidence_id
                WHERE e.source_id = ?
                """,
                (source_id,),
            )
        ]
        for claim_id in claim_ids:
            if self._evidence_count(claim_id, "supports", active_only=True) == 0:
                self.connection.execute(
                    """
                    UPDATE claims
                    SET review_status = 'needs_review', updated_at = ?
                    WHERE id = ? AND review_status = 'confirmed'
                    """,
                    (utc_now(), claim_id),
                )

    def _profile_claims(
        self, status: str, *, require_active_support: bool
    ) -> list[dict[str, Any]]:
        active_filter = (
            """
              AND EXISTS (
                SELECT 1
                FROM claim_evidence ce
                JOIN evidence e ON e.id = ce.evidence_id
                JOIN sources s ON s.id = e.source_id
                WHERE ce.claim_id = c.id
                  AND ce.relation = 'supports'
                  AND s.status = 'active'
              )
            """
            if require_active_support
            else ""
        )
        rows = self.connection.execute(
            f"""
            SELECT c.id, c.statement, c.claim_type, c.confidence, c.sensitivity,
                   c.valid_from, c.valid_to
            FROM claims c
            WHERE c.review_status = ?
            {active_filter}
            ORDER BY c.id
            """,
            (status,),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["evidence"] = self._claim_provenance(int(row["id"]))
            result.append(item)
        return result

    def _claim_provenance(self, claim_id: int) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT ce.relation, e.id AS evidence_id, e.title, e.locator,
                   e.content_hash, e.occurred_at, s.id AS source_id,
                   s.name AS source_name, s.kind AS source_kind, s.status AS source_status
            FROM claim_evidence ce
            JOIN evidence e ON e.id = ce.evidence_id
            JOIN sources s ON s.id = e.source_id
            WHERE ce.claim_id = ?
            ORDER BY ce.relation, e.id
            """,
            (claim_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _validate_choice(field: str, value: str, allowed: set[str]) -> None:
        if value not in allowed:
            raise ValueError(f"{field} must be one of: {', '.join(sorted(allowed))}")
