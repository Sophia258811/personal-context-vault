import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from pcv.store import Store


class TaskContextTests(unittest.TestCase):
    def test_two_sources_revision_and_fresh_process_recall(self):
        with tempfile.TemporaryDirectory() as temp, closing(Store(Path(temp) / "vault.db")) as store:
            database = store.path
            store.init()
            study = store.add_source(name="Synthetic exercises", kind="self_report",
                                     access_scope="test", legal_basis="test")
            browser = store.add_source(name="Synthetic feed", kind="browser",
                                       access_scope="test", legal_basis="test")
            before = store.add_evidence(source_id=study, title="Attempt one", content="Needed a hint.")
            later = store.add_evidence(source_id=study, title="Attempt two", content="Solved independently.")
            exposure = store.add_evidence(source_id=browser, title="Feed", content="An array video appeared.")
            old = store.add_claim(statement="数组边界需要提示", claim_type="observation",
                                  evidence_refs=[(before, "supports")])
            candidate = store.add_claim(statement="数组边界在本组题中已能独立处理",
                                        claim_type="observation", evidence_refs=[(later, "supports")])
            unsupported = store.add_claim(statement="数组边界仅有推荐曝光", claim_type="observation",
                                          evidence_refs=[(exposure, "supports")])
            store.review_claim(old, status="confirmed")
            store.revoke_source(browser)

            def cli(*arguments, check=True):
                return subprocess.run([sys.executable, "-X", "utf8", "-m", "pcv",
                                       "--db", str(database), *arguments],
                                      capture_output=True, encoding="utf-8", check=check)

            def recall():
                return json.loads(cli("context", "--term", "数组边界").stdout)["claims"]

            self.assertEqual([item["id"] for item in recall()], [old])
            failed = cli("claim", "review", "--id", str(unsupported), "--status", "confirmed",
                         "--replaces", str(old), check=False)
            self.assertNotEqual(failed.returncode, 0)
            self.assertEqual([item["id"] for item in recall()], [old])
            cli("claim", "review", "--id", str(candidate), "--status", "confirmed",
                "--replaces", str(old))
            claims = recall()
            self.assertEqual([item["id"] for item in claims], [candidate])
            self.assertEqual(claims[0]["evidence"][0]["evidence_id"], later)
            self.assertNotIn("content", claims[0]["evidence"][0])

            def mcp_requests(*requests):
                wire = "\n".join(json.dumps({"jsonrpc": "2.0", **request}) for request in requests)
                process = subprocess.run(
                    [sys.executable, "-m", "pcv.context_mcp", "--db", str(database)],
                    input=wire + "\n", capture_output=True, encoding="utf-8", check=True, timeout=10,
                )
                return [json.loads(line) for line in process.stdout.splitlines()]

            lookup = {"id": 3, "method": "tools/call",
                      "params": {"name": "get_task_context", "arguments": {"terms": ["数组边界"]}}}
            handshake, catalog, result, invalid = mcp_requests(
                {"id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                {"method": "notifications/initialized"},
                {"id": 2, "method": "tools/list"},
                lookup,
                {"id": 4, "method": "tools/call", "params": {
                    "name": "get_task_context", "arguments": {"terms": ["数组边界"], "db": "other.db"}
                }},
            )
            self.assertEqual(handshake["result"]["protocolVersion"], "2025-06-18")
            self.assertEqual([tool["name"] for tool in catalog["result"]["tools"]], ["get_task_context"])
            self.assertEqual(json.loads(result["result"]["content"][0]["text"])["claims"], claims)
            self.assertTrue(invalid["result"]["isError"])
            store.revoke_source(study)
            self.assertEqual(recall(), [])
            result, = mcp_requests(lookup)
            self.assertEqual(json.loads(result["result"]["content"][0]["text"])["claims"], [])
