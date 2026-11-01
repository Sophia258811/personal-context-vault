---
name: personal-context-vault
description: Use only when the user explicitly asks to recall, review, update, or apply their local evidence-backed personal profile in Personal Context Vault.
---

# Personal Context Vault

Use the local vault as a source of claims, not as unquestionable truth.

1. Confirm the vault path if it is not already known.
2. Run `python -m pcv --db <path> export --format json`.
3. Use only confirmed claims relevant to the current request.
4. Preserve claim type, source, time range, sensitivity, and uncertainty.
5. Never promote an inference to a fact.
6. Do not infer sensitive traits, relationships, health, finances, politics, religion, or precise location.
7. Do not read raw evidence unless the user asks to inspect provenance.
8. Do not send vault data to another service without specific confirmation.
9. To record new context, register a source first, then add evidence, then create a pending claim for user review.
