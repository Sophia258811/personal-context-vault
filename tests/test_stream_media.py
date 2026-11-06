import base64
import io
import json
import sqlite3
import tempfile
import threading
import unittest
import wave
from contextlib import closing
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from pcv import stream_media
from pcv.companion import create_server
from pcv.store import Store


JPEG = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwc"
    "KDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIy"
    "MjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAACAAIDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAf/xAAUEAEA"
    "AAAAAAAAAAAAAAAAAAAA/8QAFAEBAAAAAAAAAAAAAAAAAAAAAP/EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAMAwEAAhED"
    "EQA/AL+AD//Z"
)


def sample():
    data = io.BytesIO()
    with wave.open(data, "wb") as audio:
        audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\0" * 16000)
    return {"id": str(uuid4()), "url": "https://www.douyin.com/video/123?tracking=synthetic#fragment",
            "platform": "douyin", "platform_content_id": "123", "start_seconds": 4, "end_seconds": 6,
            "audio": base64.b64encode(data.getvalue()).decode(),
            "frames": [{"position_seconds": 5, "jpeg": JPEG}]}


class StreamMediaTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.database = self.root / "test.db"
        self.store = self.enterContext(closing(Store(self.database)))
        self.store.init()
        self.source = self.store.add_source(name="Synthetic browser", kind="browser", access_scope="test",
                                            legal_basis="consent")

    def test_ingest_idempotency_quota_and_source_authorization(self):
        event = sample()
        receipt = stream_media.ingest(self.store, self.source, event)
        self.assertFalse(receipt["duplicate"])
        parent = self.store.connection.execute("SELECT content, locator FROM evidence WHERE id=?",
                                               (receipt["evidence_id"],)).fetchone()
        self.assertEqual(parent["locator"], "https://www.douyin.com/video/123")
        self.assertNotIn("tracking", parent["content"])
        rows = self.store.connection.execute("""
            SELECT a.kind, s.position_seconds FROM stream_assets s
            JOIN media_assets a ON a.evidence_id=s.evidence_id ORDER BY s.position_seconds
        """).fetchall()
        self.assertEqual([tuple(row) for row in rows], [("audio", 4), ("image", 5)])
        with patch.object(stream_media, "MAX_STORED_BYTES", 1):
            self.assertEqual(stream_media.ingest(self.store, self.source, event), {**receipt, "duplicate": True})
            with self.assertRaises(stream_media.MediaCapacityError):
                stream_media.ingest(self.store, self.source, sample())
        with self.assertRaises(stream_media.MediaConflict):
            stream_media.ingest(self.store, self.source, {**event, "start_seconds": 3})
        expired = self.store.add_source(name="Expired", kind="browser", access_scope="test",
                                        legal_basis="consent", expires_at="2000-01-01")
        with self.assertRaises(stream_media.InactiveMediaSource):
            stream_media.ingest(self.store, expired, sample())
        self.store.revoke_source(self.source)
        with self.assertRaises(stream_media.InactiveMediaSource):
            stream_media.ingest(self.store, self.source, event)
        self.assertEqual(len(self.store.list_evidence()), 3)

    def test_invalid_payload_and_transaction_rollback(self):
        event = sample()
        for edits in ({"end_seconds": 20}, {"url": "https://douyin.com.example.org/"}, {"audio": "AAAA"},
                      {"frames": [{"position_seconds": 7, "jpeg": JPEG}]},
                      {"frames": [{"position_seconds": 5, "jpeg": "/9j//9k="}]}):
            with self.subTest(edits=edits), self.assertRaises(ValueError):
                stream_media.ingest(self.store, self.source, {**event, **edits})
        stream_media.initialize(self.store)
        self.store.connection.execute("""
            CREATE TRIGGER fail_frame BEFORE INSERT ON media_assets WHEN NEW.kind='image'
            BEGIN SELECT RAISE(ABORT, 'synthetic write failure'); END
        """)
        with self.assertRaises(sqlite3.IntegrityError):
            stream_media.ingest(self.store, self.source, event)
        self.assertEqual(self.store.list_evidence(), [])
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM stream_segments").fetchone()[0], 0)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM media_assets").fetchone()[0], 0)

    def test_http_auth_status_and_durable_ack(self):
        def on_media(event):
            with closing(Store(self.database)) as store:
                stream_media.ingest(store, self.source, event)

        server = create_server(self.root / "inbox", "test-token", port=0, on_media=on_media)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            def post(event=None, token="test-token", **headers):
                with closing(HTTPConnection("127.0.0.1", server.server_port, timeout=5)) as client:
                    client.request("POST", "/media", None if event is None else json.dumps(event),
                                   {"Authorization": "Bearer " + token, **headers})
                    return client.getresponse().status

            with closing(HTTPConnection("127.0.0.1", server.server_port, timeout=5)) as client:
                client.request("GET", "/media-ready")
                self.assertEqual(client.getresponse().status, 204)

            event = sample()
            self.assertEqual(post(token="wrong"), 401)
            self.assertEqual(post(**{"Content-Length": str(stream_media.MAX_BODY + 1)}), 413)
            self.assertEqual(post({**event, "end_seconds": 0}), 400)
            self.assertEqual(post(event), 204)
            self.assertEqual(post(event), 204)
            self.assertEqual(post({**event, "end_seconds": 7}), 409)
            with patch.object(stream_media, "MAX_STORED_BYTES", 1):
                self.assertEqual(post(sample()), 507)
            self.store.revoke_source(self.source)
            self.assertEqual(post(sample()), 403)
            self.assertEqual(len(self.store.list_evidence()), 3)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
