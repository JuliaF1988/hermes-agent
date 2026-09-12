#!/usr/bin/env python3
"""Strict file control for the operator-owned RAG organization policy."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

import yaml


POLICY_NAME = "organization-policy.yaml"
RECOMMENDATIONS_NAME = "organization-recommendations.json"
POLICY_SCHEMA = "rag.organization-policy.v1"
RECOMMENDATIONS_SCHEMA = "rag.organization-recommendations.v1"
MATCH_FIELDS = {"documentType", "project", "topic", "issuer", "employer", "logicalSourceId"}
RECOMMENDATION_STATUSES = {"uncovered", "covered", "conflicts_with_policy", "superseded"}
_RULE_ID = re.compile(r"[a-z][a-z0-9-]{0,63}")
_SOURCE_ID = re.compile(r"lsrc_[0-9a-f]{40}")


class PolicyControlError(ValueError):
    """A content-safe policy or revision validation failure."""


def _mapping(value: Any, fields: set[str], code: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise PolicyControlError(code)
    return value


def _root(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 160 or "\\" in value:
        raise PolicyControlError("policy_root_invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise PolicyControlError("policy_root_unsafe")
    return path.as_posix()


def _route(value: Any, code: str) -> dict[str, str]:
    route = _mapping(value, {"root", "layout"}, code)
    if route["layout"] != "flat":
        raise PolicyControlError("policy_layout_unsupported")
    return {"root": _root(route["root"]), "layout": "flat"}


def validate_policy(value: Any) -> dict[str, Any]:
    policy = _mapping(value, {"schema", "revision", "default", "rules"}, "policy_fields_invalid")
    if policy["schema"] != POLICY_SCHEMA:
        raise PolicyControlError("policy_schema_unsupported")
    revision = policy["revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise PolicyControlError("policy_revision_invalid")
    rules = policy["rules"]
    if not isinstance(rules, list) or len(rules) > 1000:
        raise PolicyControlError("policy_rules_invalid")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in rules:
        rule = _mapping(
            value,
            {"id", "priority", "match", "route", "terminal"},
            "policy_rule_fields_invalid",
        )
        rule_id = rule["id"]
        if not isinstance(rule_id, str) or _RULE_ID.fullmatch(rule_id) is None or rule_id in seen:
            raise PolicyControlError("policy_rule_id_invalid")
        priority = rule["priority"]
        if isinstance(priority, bool) or not isinstance(priority, int) or not 0 <= priority <= 1000:
            raise PolicyControlError("policy_rule_priority_invalid")
        match = rule["match"]
        if not isinstance(match, dict) or not match or not set(match) <= MATCH_FIELDS:
            raise PolicyControlError("policy_rule_match_invalid")
        normalized_match: dict[str, str] = {}
        for key, raw in match.items():
            if not isinstance(raw, str) or not raw.strip() or len(raw.encode("utf-8")) > 160:
                raise PolicyControlError("policy_rule_match_value_invalid")
            candidate = raw.strip()
            if key == "logicalSourceId" and _SOURCE_ID.fullmatch(candidate) is None:
                raise PolicyControlError("policy_logical_source_id_invalid")
            normalized_match[key] = candidate
        if not isinstance(rule["terminal"], bool):
            raise PolicyControlError("policy_rule_terminal_invalid")
        normalized.append(
            {
                "id": rule_id,
                "priority": priority,
                "match": normalized_match,
                "route": _route(rule["route"], "policy_rule_route_invalid"),
                "terminal": rule["terminal"],
            }
        )
        seen.add(rule_id)
    return {
        "schema": POLICY_SCHEMA,
        "revision": revision,
        "default": _route(policy["default"], "policy_default_invalid"),
        "rules": normalized,
    }


def load_policy(path: Path) -> dict[str, Any]:
    try:
        return validate_policy(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise PolicyControlError("policy_file_unreadable") from exc


def _policy_bytes(policy: dict[str, Any]) -> bytes:
    return yaml.safe_dump(policy, allow_unicode=True, sort_keys=False).encode("utf-8")


def _policy_hash(policy: dict[str, Any]) -> str:
    encoded = json.dumps(policy, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _load_recommendations(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PolicyControlError("recommendations_file_unreadable") from exc
    if not isinstance(value, dict) or value.get("schema") != RECOMMENDATIONS_SCHEMA:
        raise PolicyControlError("recommendations_schema_unsupported")
    rows = value.get("recommendations")
    if not isinstance(rows, list) or len(rows) > 5000:
        raise PolicyControlError("recommendations_invalid")
    for row in rows:
        if not isinstance(row, dict) or row.get("status") not in RECOMMENDATION_STATUSES:
            raise PolicyControlError("recommendation_invalid")
    return rows


def status(control_dir: Path, *, include_all: bool = False) -> dict[str, Any]:
    policy = load_policy(control_dir / POLICY_NAME)
    rows = _load_recommendations(control_dir / RECOMMENDATIONS_NAME)
    selected = rows if include_all else [row for row in rows if row.get("status") == "uncovered"]
    summaries = [
        {
            key: row.get(key)
            for key in (
                "recommendationId",
                "kind",
                "scope",
                "suggestedPolicyRule",
                "confidence",
                "evidenceCount",
                "reasonCodes",
                "status",
                "policyRevisionEvaluated",
            )
        }
        for row in selected
    ]
    return {
        "status": "ok",
        "policyRevision": policy["revision"],
        "policyHash": _policy_hash(policy),
        "default": policy["default"],
        "rules": policy["rules"],
        "recommendations": summaries,
        "recommendationCounts": {
            state: sum(row.get("status") == state for row in rows)
            for state in sorted(RECOMMENDATION_STATUSES)
        },
    }


def _atomic_write(path: Path, data: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
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


@contextmanager
def _policy_write_lock(control_dir: Path) -> Iterator[None]:
    lock = control_dir / ".organization-policy.write-lock"
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise PolicyControlError("policy_write_locked") from exc
    try:
        yield
    finally:
        lock.rmdir()


def apply_policy(control_dir: Path, candidate_path: Path, *, expected_revision: int) -> dict[str, Any]:
    policy_path = control_dir / POLICY_NAME
    candidate = load_policy(candidate_path)
    if candidate["revision"] != expected_revision + 1:
        raise PolicyControlError("policy_revision_must_increment_once")
    with _policy_write_lock(control_dir):
        if policy_path.is_symlink():
            raise PolicyControlError("policy_file_symlink_rejected")
        current = load_policy(policy_path)
        if current["revision"] != expected_revision:
            raise PolicyControlError("policy_revision_conflict")
        candidate_at_current_revision = {**candidate, "revision": expected_revision}
        if candidate_at_current_revision == current:
            raise PolicyControlError("policy_revision_changed_without_content")
        history = control_dir / ".history"
        history.mkdir(mode=0o700, exist_ok=True)
        old_data = policy_path.read_bytes()
        old_hash = _policy_hash(current)
        history_path = history / f"organization-policy-r{expected_revision}-{old_hash[:12]}.yaml"
        if history_path.exists():
            if history_path.read_bytes() != old_data:
                raise PolicyControlError("policy_history_conflict")
        else:
            _atomic_write(history_path, old_data)
        _atomic_write(policy_path, _policy_bytes(candidate))
        confirmed = load_policy(policy_path)
        if confirmed != candidate:
            raise PolicyControlError("policy_write_verification_failed")
    return {
        "status": "applied",
        "previousRevision": expected_revision,
        "policyRevision": candidate["revision"],
        "policyHash": _policy_hash(candidate),
        "historyFile": history_path.name,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    status_parser = commands.add_parser("status")
    status_parser.add_argument("--control-dir", type=Path, required=True)
    status_parser.add_argument("--all", action="store_true", dest="include_all")
    validate_parser = commands.add_parser("validate")
    validate_parser.add_argument("--candidate", type=Path, required=True)
    apply_parser = commands.add_parser("apply")
    apply_parser.add_argument("--control-dir", type=Path, required=True)
    apply_parser.add_argument("--candidate", type=Path, required=True)
    apply_parser.add_argument("--expected-revision", type=int, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "status":
            result = status(args.control_dir, include_all=args.include_all)
        elif args.command == "validate":
            policy = load_policy(args.candidate)
            result = {
                "status": "valid",
                "policyRevision": policy["revision"],
                "policyHash": _policy_hash(policy),
            }
        elif args.command == "apply":
            result = apply_policy(
                args.control_dir,
                args.candidate,
                expected_revision=args.expected_revision,
            )
        else:
            raise AssertionError(args.command)
    except PolicyControlError as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
