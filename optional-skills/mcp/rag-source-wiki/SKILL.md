---
name: rag-source-wiki
description: Build a sourced external wiki from canonical RAG state.
version: 1.0.0
author: JuliaF1988
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [RAG, Wiki, Obsidian, MCP]
    category: mcp
    config:
      - key: rag.wiki_target_root
        description: External Markdown or Obsidian target root.
        prompt: Absolute path to the external wiki target.
      - key: rag.wiki_target_identity
        description: Stable configured identity of this wiki target.
        prompt: Stable short name for this wiki target.
      - key: rag.wiki_checkpoint_path
        description: Persistent checkpoint file for this wiki target.
        prompt: Absolute path for the RAG feed checkpoint.
---

# RAG Source Wiki Skill

Build and incrementally maintain a sourced external Markdown wiki. RAG remains
the retrieval, resolver, and change-feed authority; this skill never changes
RAG state, policy, packages, or RAGFlow.

## When to Use

- Create a bounded initial wiki view from current canonical RAG sources.
- Apply relevant semantic change-feed events to existing managed pages.
- Re-evaluate claims after a source revision, including a speaker-name revision.

## Prerequisites

- The configured RAG MCP/API provides native retrieval, current/exact resolver,
  bounded current-source listing, change-feed status/pages, and bound member read.
- Configure `rag.wiki_target_root`, `rag.wiki_target_identity`, and
  `rag.wiki_checkpoint_path`. Never guess among plausible vaults.
- Use `scripts/wiki_control.py` for plans and atomic writes.

## How to Run

Create a strict `rag.wiki-batch.v1` JSON plan from resolved evidence, then use
`terminal` with this skill-relative helper:

```bash
python scripts/wiki_control.py plan --target-root "<root>" --target-id "<id>" \
  --checkpoint "<checkpoint>" --batch "<batch.json>"
python scripts/wiki_control.py apply --target-root "<root>" --target-id "<id>" \
  --checkpoint "<checkpoint>" --batch "<batch.json>"
```

Use a preview and explicit approval before a large split, merge, retirement, or
navigation change. Small evidence-preserving updates may follow configured
automation policy.

## Quick Reference

| State | Action |
|---|---|
| No checkpoint | bounded current-state full sync, then record current feed checkpoint |
| Normal checkpoint | read only changes after `(feedGeneration, sequence)` |
| Generation mismatch | fail closed; bounded current-state full sync |
| `wikiRefreshRequired=false` | advance checkpoint without rewriting pages |
| `REVISION_REPLACE` | resolve current and exact version; reassess only referencing pages |
| Write failure | checkpoint remains old; safely retry the same batch |

## Procedure

1. For initial sync, use bounded current RAG state and retrieval; historical feed
   events are not the source of truth.
2. Map every retrieval hit to `LogicalSourceId`, resolve current/exact, and read
   only members bound by resolver role/path/SHA/size/media type.
3. Synthesize claims from those originals. Preserve contradictions and
   uncertainty instead of inventing a winner.
4. Put stable `rag://source/...` references and exact SourceVersion evidence in
   each page plan. The helper adds the managed frontmatter.
5. For incremental sync, read bounded changes after the checkpoint. Ignore a
   pure locator/policy move for content. Update only affected managed pages.
6. Run `plan`; inspect its content-light counts. Run `apply` only when permitted.
7. Confirm the authoritative checkpoint and run a second time; it must be
   `no_change`.

For Transcript claims, keep these roles separate:

```text
speaker/utteranceOwner != subject != addressee != relationship
```

A confirmed non-biometric Speaker Identity proves only `utteranceOwner`.
`SPEAKER_nn`, neutral, or unresolved labels never identify a person. Subject,
addressee, and relationship require their own bound source evidence. Voiceprints,
scores, review clips, and Telegram state are never evidence.

## Pitfalls

- Do not use a free Library/Nextcloud path from model text; resolve an ID first.
- Do not base a claim only on a chunk when the bound original is available.
- Do not call the legacy `POST /v1/wiki` path.
- Do not write semantic assignments, organization policy, RAGFlow state, or
  package members.
- Do not overwrite files that lack this skill's managed marker/index entry.
- Do not advance a checkpoint after a failed page commit.

## Verification

The helper reports only target identity, checkpoints, affected IDs/pages,
created/updated/unchanged counts, and status. A retry has no duplicate page; a
locator-only event changes no Markdown; all persisted source links remain stable
`rag://source/...` URIs.
