from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pcv.connectors import sync_connector
from pcv.store import Store


class ConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "vault.db")
        self.store.init()
        self.source_id = self.store.add_source(
            name="authorized exports",
            kind="platform_export",
            access_scope="user-provided local files",
            legal_basis="user_consent",
        )

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def add_connector(
        self,
        path: Path,
        format_name: str = "auto",
        content_scope: str = "self_authored",
    ) -> int:
        return self.store.add_connector(
            source_id=self.source_id,
            name="test connector",
            platform="test",
            format_name=format_name,
            input_path=path,
            content_scope=content_scope,
        )

    def test_text_sync_is_incremental_and_imports_changed_version(self) -> None:
        inbox = self.root / "inbox"
        inbox.mkdir()
        note = inbox / "note.md"
        note.write_text("first version", encoding="utf-8")
        connector_id = self.add_connector(inbox)

        first = sync_connector(self.store, connector_id)
        second = sync_connector(self.store, connector_id)
        note.write_text("second version", encoding="utf-8")
        third = sync_connector(self.store, connector_id)

        self.assertEqual((first["imported"], second["skipped"], third["imported"]), (1, 1, 1))
        self.assertEqual(len(self.store.list_evidence()), 2)

    def test_chatgpt_import_keeps_user_messages_only(self) -> None:
        export = self.root / "conversations.json"
        export.write_text(
            json.dumps(
                [
                    {
                        "id": "conversation-1",
                        "title": "Example",
                        "mapping": {
                            "u1": {
                                "message": {
                                    "id": "u1",
                                    "author": {"role": "user"},
                                    "content": {"parts": ["my question"]},
                                    "create_time": 1_700_000_000,
                                }
                            },
                            "a1": {
                                "message": {
                                    "id": "a1",
                                    "author": {"role": "assistant"},
                                    "content": {"parts": ["model answer"]},
                                }
                            },
                        },
                    }
                ]
            ),
            encoding="utf-8",
        )
        report = sync_connector(self.store, self.add_connector(export, "chatgpt"))
        evidence = self.store.list_evidence(include_content=True)

        self.assertEqual(report["imported"], 1)
        self.assertIn('"actor_role": "user"', evidence[0]["content"])
        self.assertIn("my question", evidence[0]["content"])
        self.assertNotIn("model answer", repr(evidence))

        all_id = self.add_connector(export, "chatgpt", "all_authorized")
        all_report = sync_connector(self.store, all_id)
        all_evidence = self.store.list_evidence(include_content=True)
        self.assertEqual(all_report["imported"], 2)
        self.assertIn("model answer", repr(all_evidence))

    def test_douyin_preserves_event_kind_and_revocation_blocks_new_data(self) -> None:
        export = self.root / "douyin_snapshot.json"
        data = {
            "recommendation": [{"content_id": "1", "title": "shown"}],
            "likes": [{"content_id": "1", "title": "liked"}],
        }
        export.write_text(json.dumps(data), encoding="utf-8")
        connector_id = self.add_connector(export, "douyin")
        first = sync_connector(self.store, connector_id)
        contents = [item["content"] for item in self.store.list_evidence(include_content=True)]

        self.assertEqual(first["imported"], 2)
        self.assertTrue(any('"event_type": "recommendation"' in item for item in contents))
        self.assertTrue(any('"event_type": "likes"' in item for item in contents))

        self.store.revoke_source(self.source_id)
        with self.assertRaisesRegex(ValueError, "source must be active"):
            sync_connector(self.store, connector_id)


if __name__ == "__main__":
    unittest.main()
