"""Read a small, task-scoped projection of confirmed memory."""

import sqlite3
from pathlib import Path


def retrieve(database, terms, limit=5):
    keywords = {term.strip().casefold() for term in terms if term.strip()}
    if not keywords or len(keywords) > 10 or any(len(term) > 100 for term in keywords):
        raise ValueError("provide 1–10 topic terms, each up to 100 characters")
    if not 1 <= limit <= 20:
        raise ValueError("limit must be between 1 and 20")
    connection = sqlite3.connect(Path(database).expanduser().resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute("""
            SELECT c.id, c.statement, c.claim_type, c.valid_from, c.valid_to,
                   e.id AS evidence_id, e.occurred_at, s.id AS source_id, s.kind AS source_kind
            FROM claims c
            JOIN claim_evidence ce ON ce.claim_id = c.id AND ce.relation = 'supports'
            JOIN evidence e ON e.id = ce.evidence_id
            JOIN sources s ON s.id = e.source_id
            WHERE c.review_status = 'confirmed' AND c.sensitivity != 'high'
              AND s.status = 'active'
              AND (s.expires_at IS NULL OR julianday(s.expires_at) > julianday('now'))
              AND (c.valid_from IS NULL OR julianday(c.valid_from) <= julianday('now'))
              AND (c.valid_to IS NULL OR julianday(c.valid_to) > julianday('now'))
            ORDER BY c.updated_at DESC, c.id DESC, e.id
        """)
        matches = {}
        for row in rows:
            score = sum(term in row["statement"].casefold() for term in keywords)
            if not score:
                continue
            claim, _ = matches.setdefault(row["id"], (
                {key: row[key] for key in ("id", "statement", "claim_type", "valid_from", "valid_to")},
                score,
            ))
            claim.setdefault("evidence", []).append({
                key: row[key] for key in ("evidence_id", "source_id", "source_kind", "occurred_at")
            })
        ranked = sorted(matches.values(), key=lambda item: item[1], reverse=True)
        return {"claims": [claim for claim, _ in ranked[:limit]]}
    finally:
        connection.close()
