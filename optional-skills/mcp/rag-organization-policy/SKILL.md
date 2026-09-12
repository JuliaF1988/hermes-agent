---
name: rag-organization-policy
description: Review and update RAG organization policy safely.
version: 1.0.0
author: JuliaF1988
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [RAG, organization, policy, operations]
    category: mcp
    config:
      - key: rag.organization_control_path
        description: Persistent RAG organization control directory.
        prompt: Path containing organization-policy.yaml and recommendations.
---

# RAG Organization Policy Skill

Review advisory RAG organization recommendations and maintain the single
operator-owned policy file. This skill never moves packages or edits RAG state.

## When to Use

- Show uncovered organization recommendations.
- Explain the effective organization policy.
- Draft a general routing preference such as “invoices always flat”.
- Apply a policy change after the user explicitly approves it.

## Prerequisites

- `skills.config.rag.organization_control_path` points to the persistent control
  directory shared with RAG.
- That directory contains `organization-policy.yaml`; recommendations are read
  from `organization-recommendations.json` when present.
- Use the bundled `scripts/policy_control.py`; do not create a policy MCP or a
  direct RAG database client.

## How to Run

Use `terminal` with the configured control path and this skill-relative script:

```bash
python scripts/policy_control.py status --control-dir "<configured-path>"
python scripts/policy_control.py validate --candidate "<draft.yaml>"
python scripts/policy_control.py apply --control-dir "<configured-path>" \
  --candidate "<approved-draft.yaml>" --expected-revision <current-revision>
```

The apply command is allowed only after explicit user approval. It validates the
complete YAML, performs revision compare-and-swap, writes on the same filesystem,
flushes, and atomically renames the file.

## Quick Reference

| Intent | Rule |
|---|---|
| Read policy | `status`; report revision and effective general rules |
| Read recommendations | show `uncovered` by default; covered/conflicting only on request |
| Draft | prefer one broad source-fact rule over many source-specific overrides |
| Approve | explicit user confirmation is mandatory |
| Write | candidate revision must equal current revision + 1 |
| Conflict | reread; never overwrite a changed revision |

## Procedure

1. Run `status` and retain the returned `policyRevision`.
2. Review only uncovered recommendations unless the user asks for all states.
3. Translate a preference into the broadest validated source-fact rule:
   `documentType`, `project`, `topic`, `issuer`, `employer`, or exact
   `logicalSourceId`. Never derive a path from free prose.
4. Explain the proposed rule, priority, route and terminal behavior.
5. Stop unless the user explicitly approves writing it.
6. Read the full current policy again, preserve unrelated rules, increment its
   revision once, and create a complete draft with `patch`.
7. Run `validate`, then `apply` with the revision read in step 1.
8. Run `status` again and report the authoritative new revision.

Examples of good general rules:

- “Rechnungen immer flach” → terminal `documentType: invoice` route.
- “Lohnabrechnungen gemeinsam; Arbeitgeber nur Metadatum” → terminal
  `documentType: payslip` route, without employer subfolders.

## Pitfalls

- Never move a package directory or one of its members. RAG reconciles policy.
- Never edit `organization-recommendations.json`; it is RAG-owned advisory state.
- Never access RAGFlow, lifecycle storage, or a database to apply policy.
- Never create one rule per invoice, issuer or recommendation when one general
  rule expresses the preference.
- On revision mismatch, reread and ask for approval of the revised proposal.
- Do not route from guessed paths, model prose, or unbound document content.

## Verification

`status` must show the expected revision and no validation error. A successful
policy write changes only `organization-policy.yaml` and optional `.history`
evidence; package movement is a later RAG reconcile operation.
