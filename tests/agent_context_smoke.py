"""Opt-in integration check using a fresh Codex session and synthetic evidence."""

import json
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path

from pcv.store import Store


def main():
    project = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="pcv-agent-smoke-") as temp:
        with closing(Store(Path(temp) / "synthetic.db")) as store:
            store.init()
            source = store.add_source(name="Synthetic practice", kind="self_report",
                                      access_scope="test", legal_basis="test")
            before = store.add_evidence(source_id=source, title="First attempt", content="Needed hints.")
            later = store.add_evidence(source_id=source, title="New attempt", content="Solved independently.")
            old = store.add_claim(statement="Array bounds practice: needs an index-by-index walkthrough",
                                  claim_type="observation", evidence_refs=[(before, "supports")])
            new = store.add_claim(statement="Array bounds practice: can solve basic cases independently",
                                  claim_type="observation", evidence_refs=[(later, "supports")])
            store.review_claim(old, status="confirmed")
            store.review_claim(new, status="confirmed", replaces=old)
            database = store.path

        prompt = (
            "You are testing a tutor's memory retrieval on synthetic data only. "
            "Call the pcv get_task_context MCP tool with terms ['Array bounds']. "
            "Use only the returned task context. Do not run shell commands.\n"
            "Answer with a short tutoring plan based on the active claim. "
            "Include the active claim statement verbatim and its evidence ID as 'Evidence ID: N'."
        )
        try:
            result = subprocess.run(
                ["codex", "exec", "--ephemeral", "--ignore-user-config", "--sandbox", "read-only",
                 "--skip-git-repo-check", "--cd", temp, "--json",
                 "-c", "mcp_servers.pcv.command=" + json.dumps(sys.executable),
                 "-c", "mcp_servers.pcv.cwd=" + json.dumps(str(project)),
                 "-c", "mcp_servers.pcv.args=" + json.dumps(["-m", "pcv.context_mcp", "--db", str(database)]),
                 "-c", "mcp_servers.pcv.required=true", prompt],
                capture_output=True, encoding="utf-8", timeout=60,
            )
        except subprocess.TimeoutExpired as error:
            diagnostics = error.stderr or error.stdout or ""
            if isinstance(diagnostics, bytes):
                diagnostics = diagnostics.decode("utf-8", errors="replace")
            raise SystemExit("Fresh Codex session timed out.\n" + diagnostics[-2000:]) from None
        if result.returncode:
            raise SystemExit(result.stderr[-2000:] or result.stdout[-2000:])
        items = [event.get("item", {}) for line in result.stdout.splitlines()
                 if (event := json.loads(line)).get("type") == "item.completed"]
        answer = "\n".join(item.get("text", "") for item in items if item.get("type") == "agent_message")
        calls = [item for item in items if item.get("type") == "mcp_tool_call"]
        assert any(call.get("server") == "pcv" and call.get("tool") == "get_task_context"
                   and call.get("status") == "completed" for call in calls), (calls, answer)
        assert "can solve basic cases independently" in answer, answer
        assert "index-by-index walkthrough" not in answer, answer
        assert f"Evidence ID: {later}" in answer, answer
        print(answer)
        print("PASS: fresh Agent called the read-only MCP tool and used the revised synthetic memory")


if __name__ == "__main__":
    main()
