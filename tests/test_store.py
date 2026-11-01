from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pcv.store import Store


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "vault.db")
        self.store.init()
        self.source_id = self.store.add_source(
            name="manual export",
            kind="chat_export",
            access_scope="self-authored conversations",
            legal_basis="user_consent",
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def add_evidence(self, source_id: int | None = None, content: str = "evidence") -> int:
        return self.store.add_evidence(
            source_id=source_id or self.source_id,
            title="sample",
            content=content,
        )

    def add_claim(self, evidence_id: int, claim_type: str = "direct_fact") -> int:
        return self.store.add_claim(
            statement="The user prefers concise answers",
            claim_type=claim_type,
            evidence_refs=[(evidence_id, "supports")],
            confidence=0.9,
        )

    def test_persists_source_and_evidence_metadata(self) -> None:
        evidence_id = self.add_evidence(content="stable content")
        sources = self.store.list_sources()
        evidence = self.store.list_evidence()

        self.assertEqual(sources[0]["status"], "active")
        self.assertEqual(evidence[0]["id"], evidence_id)
        self.assertEqual(len(evidence[0]["content_hash"]), 64)
        self.assertNotIn("content", evidence[0])

    def test_confirmation_requires_active_support(self) -> None:
        claim_id = self.add_claim(self.add_evidence())
        self.store.revoke_source(self.source_id)
        with self.assertRaisesRegex(ValueError, "active supporting evidence"):
            self.store.review_claim(claim_id, status="confirmed")

    def test_profile_separates_confirmed_and_pending(self) -> None:
        confirmed = self.add_claim(self.add_evidence(content="one"))
        self.store.review_claim(confirmed, status="confirmed")
        pending = self.add_claim(self.add_evidence(content="two"))

        profile = self.store.export_profile(include_pending=True)
        confirmed_ids = [item["id"] for item in profile["claims"]]
        pending_ids = [item["id"] for item in profile["pending_review"]]

        self.assertEqual(confirmed_ids, [confirmed])
        self.assertEqual(pending_ids, [pending])
        self.assertEqual(profile["claims"][0]["evidence"][0]["relation"], "supports")

    def test_contradiction_requires_explicit_override(self) -> None:
        evidence_id = self.add_evidence()
        claim_id = self.store.add_claim(
            statement="Candidate trait",
            claim_type="trait",
            evidence_refs=[
                (evidence_id, "supports"),
                (self.add_evidence(content="opposite"), "contradicts"),
            ],
        )
        with self.assertRaisesRegex(ValueError, "contradictory evidence"):
            self.store.review_claim(claim_id, status="confirmed")
        self.store.review_claim(
            claim_id, status="confirmed", allow_conflicts=True
        )
        profile = self.store.export_profile()
        self.assertEqual(profile["claims"][0]["id"], claim_id)

    def test_source_delete_marks_orphan_claim_for_review(self) -> None:
        claim_id = self.add_claim(self.add_evidence())
        self.store.review_claim(claim_id, status="confirmed")
        result = self.store.delete_source(self.source_id, confirmed=True)

        claim = self.store.list_claims()[0]
        profile = self.store.export_profile(include_pending=True)
        self.assertEqual(claim["review_status"], "needs_review")
        self.assertEqual(result["claims_marked_needs_review"], 1)
        self.assertEqual(self.store.list_evidence(), [])
        self.assertEqual(profile["claims"], [])
        self.assertEqual(profile["pending_review"][0]["id"], claim_id)
        self.assertEqual(profile["pending_review"][0]["evidence"], [])

    def test_source_delete_requires_confirmation(self) -> None:
        with self.assertRaisesRegex(ValueError, "explicit confirmation"):
            self.store.delete_source(self.source_id, confirmed=False)
        self.assertEqual(len(self.store.list_sources()), 1)

    def test_audit_does_not_store_evidence_content(self) -> None:
        secret_text = "private evidence payload"
        self.add_evidence(content=secret_text)
        audit_json = repr(self.store.list_audit())
        self.assertNotIn(secret_text, audit_json)
        self.assertIn("evidence.add", audit_json)


if __name__ == "__main__":
    unittest.main()
