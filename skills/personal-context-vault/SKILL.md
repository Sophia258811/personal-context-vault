---
name: personal-context-vault
description: Use only when the user explicitly asks to recall, review, update, or apply their local evidence-backed personal profile in Personal Context Vault.
---

# Personal Context Vault

Use the local vault as a source of claims, not as unquestionable truth.

1. Confirm the vault path if it is not already known.
2. For a focused task, prefer the configured PCV MCP tool `get_task_context` with short topic terms. When the tool is unavailable and shell access is allowed, call `python -m pcv --db <path> context --term "<topic>" --limit 5` from the PCV checkout or installed package. Repeat `--term` for short synonyms. If no claims match, treat the topic as unknown.
3. Query again in a new conversation or after a correction. Use the returned confirmed claims and cite their evidence IDs when helpful. Use full profile export only for an explicitly requested profile review.
4. Preserve claim type, source, time range, sensitivity, and uncertainty.
5. Never promote an inference to a fact.
6. Do not infer sensitive traits, relationships, health, finances, politics, religion, or precise location.
7. Do not read raw evidence unless the user asks to inspect provenance.
8. Do not send vault data to another service without specific confirmation.
9. To record new context, register a source first, then add evidence, then create a pending claim for user review.
10. To propose a revision, identify the old claim ID and the candidate supported by new evidence. After the user confirms the replacement, run `pcv --db <path> claim review --id NEW --status confirmed --replaces OLD`. Re-query to verify the change.
