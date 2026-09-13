#!/usr/bin/env python3
"""Validate and atomically apply source-bound RAG wiki batches."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA = "rag.wiki-batch.v1"
INDEX_SCHEMA = "rag.wiki-managed-index.v1"
CHECKPOINT_SCHEMA = "rag.wiki-checkpoint.v1"
SOURCE_ID = re.compile(r"lsrc_[0-9a-f]{40}")
VERSION_ID = re.compile(r"sver_[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
MANAGED_MARKER = "hermes_rag_wiki_managed: true"
REFRESH_EVENTS = {
    "NEW_RELEVANT_SOURCE",
    "REVISION_REPLACE",
    "SOURCE_RETIRE",
    "PROJECT_ASSIGN",
    "PROJECT_REMOVE",
    "PROJECT_RECLASSIFY",
    "PROJECT_RENAME",
    "PROJECT_SPLIT",
    "PROJECT_MERGE",
    "TOPIC_ASSIGN",
    "TOPIC_REMOVE",
    "TOPIC_RECLASSIFY",
    "SOURCE_RECLASSIFY",
}
EVENTS = REFRESH_EVENTS | {"LOCATOR_MOVE"}
PERSON_ROLES = {"utteranceOwner", "subject", "addressee", "relationship"}


class WikiControlError(ValueError):
    """A content-safe plan, ownership, or checkpoint error."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _load_json(path: Path, code: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WikiControlError(code) from exc


def _safe_relative(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value or len(value.encode()) > 240:
        raise WikiControlError("wiki_page_path_invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or path.suffix.lower() != ".md" or any(part in {"", ".", ".."} for part in path.parts):
        raise WikiControlError("wiki_page_path_unsafe")
    return path.as_posix()


def _checkpoint(value: Any, *, nullable: bool = False) -> dict[str, Any] | None:
    if nullable and value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"feedGeneration", "sequence"}:
        raise WikiControlError("wiki_checkpoint_invalid")
    generation = value["feedGeneration"]
    sequence = value["sequence"]
    if not isinstance(generation, str) or not generation.startswith("feed_") or len(generation) > 80:
        raise WikiControlError("wiki_checkpoint_invalid")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
        raise WikiControlError("wiki_checkpoint_invalid")
    return {"feedGeneration": generation, "sequence": sequence}


def _binding(value: Any) -> dict[str, Any]:
    fields = {"logicalSourceId", "sourceVersionId", "uri", "member"}
    if not isinstance(value, dict) or set(value) != fields:
        raise WikiControlError("wiki_source_binding_invalid")
    logical_id = value["logicalSourceId"]
    version_id = value["sourceVersionId"]
    if SOURCE_ID.fullmatch(logical_id or "") is None or VERSION_ID.fullmatch(version_id or "") is None:
        raise WikiControlError("wiki_source_binding_invalid")
    if value["uri"] != f"rag://source/{logical_id}/version/{version_id}":
        raise WikiControlError("wiki_source_uri_invalid")
    member = value["member"]
    expected = {"role", "path", "sha256", "size", "mediaType"}
    if not isinstance(member, dict) or set(member) != expected:
        raise WikiControlError("wiki_member_binding_invalid")
    if (
        not isinstance(member["role"], str)
        or not member["role"]
        or _safe_member_path(member["path"]) != member["path"]
        or SHA256.fullmatch(member["sha256"] or "") is None
        or isinstance(member["size"], bool)
        or not isinstance(member["size"], int)
        or member["size"] < 0
        or not isinstance(member["mediaType"], str)
        or not member["mediaType"]
    ):
        raise WikiControlError("wiki_member_binding_invalid")
    return value


def _safe_member_path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value or len(value.encode()) > 512:
        raise WikiControlError("wiki_member_path_invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise WikiControlError("wiki_member_path_invalid")
    return path.as_posix()


def _person_claim(value: Any, bindings: set[tuple[str, str]]) -> None:
    fields = {
        "semanticRole",
        "person",
        "logicalSourceId",
        "sourceVersionId",
        "evidenceKind",
        "speakerStatus",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise WikiControlError("wiki_person_claim_invalid")
    if value["semanticRole"] not in PERSON_ROLES or not isinstance(value["person"], str) or not value["person"].strip():
        raise WikiControlError("wiki_person_claim_invalid")
    if (value["logicalSourceId"], value["sourceVersionId"]) not in bindings:
        raise WikiControlError("wiki_person_claim_unbound")
    evidence_kind = value["evidenceKind"]
    status = value["speakerStatus"]
    if evidence_kind not in {"speaker_binding", "explicit_text", "structured_metadata"}:
        raise WikiControlError("wiki_person_claim_evidence_invalid")
    if status not in {"confirmed", "neutral", "unresolved", "not_applicable"}:
        raise WikiControlError("wiki_person_claim_evidence_invalid")
    if evidence_kind == "speaker_binding" and (
        value["semanticRole"] != "utteranceOwner" or status != "confirmed"
    ):
        raise WikiControlError("wiki_speaker_role_overreach")
    if status in {"neutral", "unresolved"}:
        raise WikiControlError("wiki_unresolved_person_attribution")


def validate_batch(value: Any, target_id: str) -> dict[str, Any]:
    fields = {
        "schema",
        "mode",
        "wikiTarget",
        "baseCheckpoint",
        "nextCheckpoint",
        "events",
        "pages",
        "pendingReviewCount",
    }
    if not isinstance(value, dict) or set(value) != fields or value.get("schema") != SCHEMA:
        raise WikiControlError("wiki_batch_contract_invalid")
    if value["mode"] not in {"full", "incremental"} or value["wikiTarget"] != target_id:
        raise WikiControlError("wiki_batch_target_invalid")
    base = _checkpoint(value["baseCheckpoint"], nullable=True)
    next_checkpoint = _checkpoint(value["nextCheckpoint"])
    assert next_checkpoint is not None
    if value["mode"] == "incremental" and base is None:
        raise WikiControlError("wiki_incremental_checkpoint_missing")
    events = value["events"]
    if not isinstance(events, list) or len(events) > 200:
        raise WikiControlError("wiki_events_invalid")
    refresh_required = False
    for event in events:
        if not isinstance(event, dict) or set(event) != {
            "changeId", "eventType", "logicalSourceIds", "wikiRefreshRequired"
        }:
            raise WikiControlError("wiki_event_invalid")
        if event["eventType"] not in EVENTS or not isinstance(event["wikiRefreshRequired"], bool):
            raise WikiControlError("wiki_event_invalid")
        ids = event["logicalSourceIds"]
        if not isinstance(ids, list) or len(ids) > 100 or any(SOURCE_ID.fullmatch(item or "") is None for item in ids):
            raise WikiControlError("wiki_event_invalid")
        if event["eventType"] == "LOCATOR_MOVE" and event["wikiRefreshRequired"]:
            raise WikiControlError("wiki_locator_move_cannot_refresh")
        refresh_required = refresh_required or event["wikiRefreshRequired"]
    pages = value["pages"]
    if not isinstance(pages, list) or len(pages) > 200:
        raise WikiControlError("wiki_pages_invalid")
    if events and not refresh_required and pages:
        raise WikiControlError("wiki_nonsemantic_event_cannot_rewrite_pages")
    seen: set[str] = set()
    normalized_pages: list[dict[str, Any]] = []
    for page in pages:
        expected = {"path", "title", "markdown", "sourceBindings", "personClaims"}
        if not isinstance(page, dict) or set(page) != expected:
            raise WikiControlError("wiki_page_invalid")
        path = _safe_relative(page["path"])
        if path in seen or not isinstance(page["title"], str) or not page["title"].strip():
            raise WikiControlError("wiki_page_invalid")
        markdown = page["markdown"]
        if not isinstance(markdown, str) or not markdown.strip() or len(markdown.encode()) > 2_000_000:
            raise WikiControlError("wiki_page_content_invalid")
        raw_bindings = page["sourceBindings"]
        if not isinstance(raw_bindings, list) or not 1 <= len(raw_bindings) <= 100:
            raise WikiControlError("wiki_page_sources_invalid")
        bindings = [_binding(item) for item in raw_bindings]
        identities = {(item["logicalSourceId"], item["sourceVersionId"]) for item in bindings}
        claims = page["personClaims"]
        if not isinstance(claims, list) or len(claims) > 200:
            raise WikiControlError("wiki_person_claims_invalid")
        for claim in claims:
            _person_claim(claim, identities)
        seen.add(path)
        normalized_pages.append({**page, "path": path, "sourceBindings": bindings})
    pending = value["pendingReviewCount"]
    if isinstance(pending, bool) or not isinstance(pending, int) or not 0 <= pending <= 10_000:
        raise WikiControlError("wiki_pending_review_count_invalid")
    if base is not None and base["feedGeneration"] == next_checkpoint["feedGeneration"] and base["sequence"] > next_checkpoint["sequence"]:
        raise WikiControlError("wiki_checkpoint_regression")
    return {**value, "baseCheckpoint": base, "nextCheckpoint": next_checkpoint, "pages": normalized_pages}


def _read_checkpoint(path: Path, target_id: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    value = _load_json(path, "wiki_checkpoint_unreadable")
    if not isinstance(value, dict) or value.get("schema") != CHECKPOINT_SCHEMA or value.get("wikiTarget") != target_id:
        raise WikiControlError("wiki_checkpoint_identity_invalid")
    return _checkpoint({"feedGeneration": value.get("feedGeneration"), "sequence": value.get("sequence")})


def _index_path(root: Path) -> Path:
    return root / ".hermes-rag-wiki" / "index.json"


def _read_index(root: Path, target_id: str) -> dict[str, Any]:
    path = _index_path(root)
    if not path.exists():
        return {"schema": INDEX_SCHEMA, "wikiTarget": target_id, "pages": {}}
    value = _load_json(path, "wiki_index_unreadable")
    if not isinstance(value, dict) or value.get("schema") != INDEX_SCHEMA or value.get("wikiTarget") != target_id or not isinstance(value.get("pages"), dict):
        raise WikiControlError("wiki_index_invalid")
    return value


def _page_bytes(page: dict[str, Any], target_id: str) -> bytes:
    logical = sorted({item["logicalSourceId"] for item in page["sourceBindings"]})
    versions = sorted({item["sourceVersionId"] for item in page["sourceBindings"]})
    frontmatter = [
        "---",
        MANAGED_MARKER,
        f"wiki_target: {json.dumps(target_id, ensure_ascii=False)}",
        f"title: {json.dumps(page['title'].strip(), ensure_ascii=False)}",
        "rag_sources:",
        *[f"  - rag://source/{item}" for item in logical],
        "rag_source_versions:",
        *[f"  - {item}" for item in versions],
        "---",
        "",
    ]
    return ("\n".join(frontmatter) + page["markdown"].strip() + "\n").encode("utf-8")


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            if os.name != "nt":
                os.fchmod(handle.fileno(), mode)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _planned(batch: dict[str, Any], root: Path, target_id: str, checkpoint: Path) -> dict[str, Any]:
    current_checkpoint = _read_checkpoint(checkpoint, target_id)
    base = batch["baseCheckpoint"]
    next_checkpoint = batch["nextCheckpoint"]
    replay = current_checkpoint == next_checkpoint
    if not replay:
        if batch["mode"] == "incremental" and current_checkpoint != base:
            raise WikiControlError("wiki_checkpoint_conflict")
        if batch["mode"] == "full" and base is not None and current_checkpoint != base:
            raise WikiControlError("wiki_checkpoint_conflict")
    index = _read_index(root, target_id)
    pages: list[dict[str, Any]] = []
    for page in batch["pages"]:
        target = root / page["path"]
        content = _page_bytes(page, target_id)
        digest = hashlib.sha256(content).hexdigest()
        owned = page["path"] in index["pages"]
        if target.exists() and not owned:
            raise WikiControlError("wiki_unmanaged_target_conflict")
        if target.exists() and MANAGED_MARKER.encode() not in target.read_bytes()[:4096]:
            raise WikiControlError("wiki_managed_marker_missing")
        before = hashlib.sha256(target.read_bytes()).hexdigest() if target.exists() else None
        action = "unchanged" if before == digest else "updated" if target.exists() else "created"
        pages.append({"path": page["path"], "action": action, "sha256": digest, "content": content, "sourceBindings": page["sourceBindings"]})
    return {"checkpoint": current_checkpoint, "nextCheckpoint": next_checkpoint, "replay": replay, "index": index, "pages": pages}


def plan(root: Path, target_id: str, checkpoint: Path, batch_path: Path) -> dict[str, Any]:
    batch = validate_batch(_load_json(batch_path, "wiki_batch_unreadable"), target_id)
    state = _planned(batch, root, target_id, checkpoint)
    counts = {name: sum(item["action"] == name for item in state["pages"]) for name in ("created", "updated", "unchanged")}
    return {
        "status": "no_change" if state["replay"] and counts["created"] + counts["updated"] == 0 else "planned",
        "wikiTarget": target_id,
        "checkpoint": state["checkpoint"],
        "nextCheckpoint": state["nextCheckpoint"],
        **counts,
        "affectedPages": [item["path"] for item in state["pages"] if item["action"] != "unchanged"],
        "pendingReviewCount": batch["pendingReviewCount"],
    }


def apply(root: Path, target_id: str, checkpoint: Path, batch_path: Path) -> dict[str, Any]:
    batch = validate_batch(_load_json(batch_path, "wiki_batch_unreadable"), target_id)
    state = _planned(batch, root, target_id, checkpoint)
    changed = [item for item in state["pages"] if item["action"] != "unchanged"]
    if state["replay"] and not changed:
        return plan(root, target_id, checkpoint, batch_path)
    backups: dict[Path, bytes | None] = {}
    index_path = _index_path(root)
    paths = [root / item["path"] for item in changed] + [index_path, checkpoint]
    for path in paths:
        backups[path] = path.read_bytes() if path.exists() else None
    next_index = json.loads(json.dumps(state["index"]))
    for item in state["pages"]:
        next_index["pages"][item["path"]] = {
            "sha256": item["sha256"],
            "logicalSourceIds": sorted({row["logicalSourceId"] for row in item["sourceBindings"]}),
            "sourceVersionIds": sorted({row["sourceVersionId"] for row in item["sourceBindings"]}),
        }
    run_id = "wrun_" + hashlib.sha256(_canonical(batch)).hexdigest()[:40]
    checkpoint_value = {
        "schema": CHECKPOINT_SCHEMA,
        "wikiTarget": target_id,
        **batch["nextCheckpoint"],
    }
    try:
        for item in changed:
            _atomic_write(root / item["path"], item["content"])
        _atomic_write(index_path, _canonical(next_index) + b"\n")
        _atomic_write(checkpoint, _canonical(checkpoint_value) + b"\n")
    except Exception:
        for path in reversed(paths):
            previous = backups[path]
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                _atomic_write(path, previous)
        raise
    counts = {name: sum(item["action"] == name for item in state["pages"]) for name in ("created", "updated", "unchanged")}
    return {
        "status": "applied" if changed else "checkpoint_advanced",
        "runId": run_id,
        "wikiTarget": target_id,
        "checkpoint": batch["nextCheckpoint"],
        **counts,
        "affectedPages": [item["path"] for item in changed],
        "logicalSourceIds": sorted({row["logicalSourceId"] for item in state["pages"] for row in item["sourceBindings"]}),
        "sourceVersionIds": sorted({row["sourceVersionId"] for item in state["pages"] for row in item["sourceBindings"]}),
        "pendingReviewCount": batch["pendingReviewCount"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "apply"):
        command = commands.add_parser(name)
        command.add_argument("--target-root", required=True, type=Path)
        command.add_argument("--target-id", required=True)
        command.add_argument("--checkpoint", required=True, type=Path)
        command.add_argument("--batch", required=True, type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        function = plan if args.command == "plan" else apply
        result = function(args.target_root, args.target_id, args.checkpoint, args.batch)
    except WikiControlError as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
