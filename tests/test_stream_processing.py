import base64
import io
import json
import tempfile
import unittest
import wave
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from pcv import agent_tasks, media, stream_media, stream_processing
from pcv.store import Store


# A 1x1 synthetic JPEG keeps this test independent of image libraries.
JPEG = ("/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/"
        "wAALCAABAAEBAREA/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/9oACAEBAAA/APn+v//Z")


class StreamProcessingTests(unittest.TestCase):
    def test_two_modalities_traceability_retention_and_revocation(self):
        with tempfile.TemporaryDirectory() as folder, closing(Store(Path(folder) / "vault.db")) as store:
            store.init()
            stream_media.initialize(store)
            stream_processing.initialize(store)
            agent_tasks.initialize(store)
            source = store.add_source(name="Synthetic tab", kind="browser", access_scope="test", legal_basis="test")
            samples = io.BytesIO()
            with wave.open(samples, "wb") as clip:
                clip.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                clip.writeframes(bytes(32000))
            segment = stream_media.ingest(store, source, {
                "id": str(uuid4()), "url": "https://www.douyin.com/video/123456789",
                "platform": "douyin", "platform_content_id": "123456789",
                "start_seconds": 10, "end_seconds": 11,
                "audio": base64.b64encode(samples.getvalue()).decode(),
                "frames": [{"position_seconds": 10.5, "jpeg": JPEG}],
            })
            self.assertEqual(stream_processing.status(store.path, 1)[0]["status"], "pending")

            def recognize(kind, path, culture):
                if kind == "audio":
                    return [{"text": "Check the array bounds", "start_seconds": 0.1, "end_seconds": 0.8}]
                return [{"text": "ARRAY BOUNDS", "box": [0.1, 0.2, 0.9, 0.5]}]

            analysis = stream_processing.process_next(store, processor=recognize)
            self.assertEqual(analysis["recognized"], 2)
            self.assertIsNone(stream_processing.process_next(store, processor=recognize))
            task = agent_tasks.prepare(store, analysis["evidence_id"], Path(folder) / "tasks")
            payload = json.loads(Path(task["path"]).read_text(encoding="utf-8"))
            evidence = json.loads(payload["evidence"]["content"])
            self.assertEqual(evidence["parent_evidence_id"], segment["evidence_id"])
            self.assertEqual(payload["input_modalities"], ["text"])
            self.assertEqual([m["kind"] for m in payload["available_media"]], ["audio", "image"])
            frame_id = payload["available_media"][1]["evidence_id"]
            vision = agent_tasks.prepare(store, analysis["evidence_id"], Path(folder) / "tasks", frame_id=frame_id)
            vision_task = json.loads(Path(vision["path"]).read_text(encoding="utf-8"))
            self.assertEqual(vision_task["optional_frame"]["evidence_id"], frame_id)
            self.assertEqual((Path(vision["path"]).parent / vision_task["optional_frame"]["file"]).read_bytes(),
                             base64.b64decode(JPEG))
            result = {
                "task_id": vision["task_id"], "evidence_hash": vision_task["evidence_hash"],
                "summary": "One selected frame was inspected.", "limitations": ["One frame only"],
                "candidate_inferences": [], "observed_frame_id": frame_id + 1,
            }
            with self.assertRaisesRegex(ValueError, "does not match"):
                agent_tasks.accept(store, result)
            result["observed_frame_id"] = frame_id
            accepted = agent_tasks.accept(store, result)
            summary = json.loads(agent_tasks.active_evidence(store, accepted["summary_id"])["content"])
            self.assertEqual(summary["input_modalities"], ["text", "image"])
            exported = Path(folder) / "frame.jpg"
            media.export_asset(store, frame_id, exported)
            self.assertEqual(exported.read_bytes(), base64.b64decode(JPEG))
            with self.assertRaises(FileExistsError):
                media.export_asset(store, frame_id, exported)
            store.connection.execute("UPDATE stream_analysis SET completed_at='2000-01-01T00:00:00Z'")
            store.connection.commit()
            self.assertEqual(stream_processing.expire_processed_media(store), 2)
            self.assertTrue(all(not m["payload_available"] for m in media.task_media(store, analysis["evidence_id"])))
            with self.assertRaisesRegex(ValueError, "unavailable"):
                media.export_asset(store, frame_id, Path(folder) / "expired.jpg")
            store.revoke_source(source)
            self.assertEqual(stream_processing.status(store.path, 1), [])
