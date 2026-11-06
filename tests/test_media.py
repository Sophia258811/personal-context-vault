import base64
import json
import tempfile
import unittest
import wave
from contextlib import closing
from pathlib import Path

from pcv import agent_tasks, media
from pcv.store import Store


class MediaTests(unittest.TestCase):
    def test_image_derivation_handoff_summary_and_source_deletion(self):
        with tempfile.TemporaryDirectory() as temp, closing(Store(Path(temp) / "vault.db")) as store:
            store.init()
            media.initialize(store)
            agent_tasks.initialize(store)
            source = store.add_source(name="Synthetic image", kind="self_report",
                                      access_scope="test", legal_basis="test")
            path = Path(temp) / "image.png"
            path.write_bytes(base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+X0e0AAAAASUVORK5CYII="
            ))
            original = media.add(store, source, path, "Synthetic image")
            with self.assertRaisesRegex(ValueError, "run media analyze"):
                agent_tasks.prepare(store, original["evidence_id"], temp)
            segments = [{"text": "ARRAY BOUNDS", "box": [0.1, 0.2, 0.6, 0.4]}]
            derivative = media.analyze(store, original["evidence_id"], processor=lambda *_: segments)
            repeated = media.analyze(store, original["evidence_id"], processor=lambda *_: segments)
            self.assertEqual(repeated["evidence_id"], derivative["evidence_id"])
            task = agent_tasks.prepare(store, derivative["evidence_id"], temp)
            payload = json.loads(Path(task["path"]).read_text(encoding="utf-8"))
            self.assertEqual(payload["input_modalities"], ["text"])
            self.assertEqual(payload["available_media"][0]["sha256"], original["sha256"])
            self.assertEqual(json.loads(payload["evidence"]["content"])["segments"], segments)
            result = {
                "task_id": task["task_id"], "evidence_hash": payload["evidence_hash"],
                "summary": "Image evidence 1 contains ARRAY BOUNDS in region [0.1, 0.2, 0.6, 0.4].",
                "limitations": ["OCR only"], "candidate_inferences": [],
            }
            summary = agent_tasks.accept(store, result)
            stored = store.connection.execute("SELECT content FROM evidence WHERE id=?",
                                              (summary["summary_id"],)).fetchone()
            self.assertEqual(json.loads(stored["content"])["derived_from_media"][0]["evidence_id"],
                             original["evidence_id"])
            store.revoke_source(source)
            with self.assertRaises(ValueError):
                media.analyze(store, original["evidence_id"], processor=lambda *_: segments)
            store.delete_source(source, confirmed=True)
            for table in ("evidence", "media_assets", "media_derivations", "agent_tasks"):
                self.assertEqual(store.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)

    def test_audio_rejects_out_of_range_segment_without_writing_evidence(self):
        with tempfile.TemporaryDirectory() as temp, closing(Store(Path(temp) / "vault.db")) as store:
            store.init()
            media.initialize(store)
            source = store.add_source(name="Synthetic audio", kind="self_report",
                                      access_scope="test", legal_basis="test")
            path = Path(temp) / "audio.wav"
            with wave.open(str(path), "wb") as audio:
                audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                audio.writeframes(bytes(32000))
            original = media.add(store, source, path, "Synthetic audio")
            with self.assertRaisesRegex(ValueError, "outside the original clip"):
                media.analyze(store, original["evidence_id"], processor=lambda *_: [
                    {"text": "out of range", "start_seconds": 0, "end_seconds": 1.5},
                ])
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM evidence").fetchone()[0], 1)
            result = media.analyze(store, original["evidence_id"], processor=lambda *_: [
                {"text": "within range", "start_seconds": 0.1, "end_seconds": 0.9},
            ])
            self.assertEqual(result["segments"], 1)
