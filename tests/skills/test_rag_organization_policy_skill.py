from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest
import yaml

from agent.skill_utils import parse_frontmatter
from tools import skills_tool


SKILL = Path(__file__).resolve().parents[2] / "optional-skills/mcp/rag-organization-policy"


def _module():
    path = SKILL / "scripts/policy_control.py"
    spec = importlib.util.spec_from_file_location("rag_organization_policy_control", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _policy(revision: int = 1) -> dict:
    return {
        "schema": "rag.organization-policy.v1",
        "revision": revision,
        "default": {"root": "Ablage", "layout": "flat"},
        "rules": [
            {
                "id": "invoices-flat",
                "priority": 1000,
                "match": {"documentType": "invoice"},
                "route": {"root": "Rechnungen", "layout": "flat"},
                "terminal": True,
            }
        ],
    }


def _write_yaml(path: Path, value: dict) -> None:
    path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")


def test_skill_loads_and_declares_control_path(tmp_path, monkeypatch):
    target = tmp_path / "mcp/rag-organization-policy"
    shutil.copytree(SKILL, target)
    monkeypatch.setattr(skills_tool, "SKILLS_DIR", tmp_path)
    monkeypatch.setattr(skills_tool, "_skill_search_dirs", lambda: ([], [tmp_path], tmp_path))
    monkeypatch.setattr(skills_tool, "_is_skill_disabled", lambda *args, **kwargs: False)
    result = json.loads(skills_tool.skill_view("rag-organization-policy", preprocess=False))
    frontmatter, body = parse_frontmatter((target / "SKILL.md").read_text(encoding="utf-8"))
    assert result["success"] is True
    assert frontmatter["version"] == "1.0.0"
    assert frontmatter["metadata"]["hermes"]["config"][0]["key"] == "rag.organization_control_path"
    assert len(frontmatter["description"]) <= 60
    assert len(body.split()) < 600


def test_status_defaults_to_uncovered_and_never_writes_recommendations(tmp_path):
    control = tmp_path / "control"
    control.mkdir()
    _write_yaml(control / "organization-policy.yaml", _policy())
    recommendations = {
        "schema": "rag.organization-recommendations.v1",
        "recommendations": [
            {"recommendationId": "uncovered", "status": "uncovered"},
            {"recommendationId": "covered", "status": "covered"},
        ],
    }
    recommendation_path = control / "organization-recommendations.json"
    recommendation_path.write_text(json.dumps(recommendations), encoding="utf-8")
    before = recommendation_path.read_bytes()

    result = _module().status(control)

    assert [row["recommendationId"] for row in result["recommendations"]] == ["uncovered"]
    assert result["recommendationCounts"] == {
        "conflicts_with_policy": 0,
        "covered": 1,
        "superseded": 0,
        "uncovered": 1,
    }
    assert recommendation_path.read_bytes() == before


def test_apply_is_atomic_revision_checked_and_preserves_unrelated_rules(tmp_path, monkeypatch):
    module = _module()
    control = tmp_path / "control"
    control.mkdir()
    current = _policy()
    current["rules"].append(
        {
            "id": "payslips-flat",
            "priority": 1000,
            "match": {"documentType": "payslip"},
            "route": {"root": "Lohnabrechnungen", "layout": "flat"},
            "terminal": True,
        }
    )
    _write_yaml(control / "organization-policy.yaml", current)
    recommendations_path = control / "organization-recommendations.json"
    recommendations_path.write_text(
        json.dumps({"schema": "rag.organization-recommendations.v1", "recommendations": []}),
        encoding="utf-8",
    )
    recommendations_before = recommendations_path.read_bytes()
    candidate = {**current, "revision": 2, "rules": [*current["rules"], {
        "id": "projects-flat",
        "priority": 500,
        "match": {"project": "Sicherungskasten"},
        "route": {"root": "Projekte/Sicherungskasten", "layout": "flat"},
        "terminal": True,
    }]}
    candidate_path = tmp_path / "candidate.yaml"
    _write_yaml(candidate_path, candidate)
    replacements: list[tuple[Path, Path]] = []
    original_replace = module.os.replace

    def record_replace(source, target):
        replacements.append((Path(source), Path(target)))
        original_replace(source, target)

    monkeypatch.setattr(module.os, "replace", record_replace)
    result = module.apply_policy(control, candidate_path, expected_revision=1)

    assert result["policyRevision"] == 2
    assert module.load_policy(control / "organization-policy.yaml") == candidate
    assert module.load_policy(control / ".history" / result["historyFile"]) == current
    assert all(source.parent == target.parent for source, target in replacements)
    assert {rule["id"] for rule in candidate["rules"]} >= {"invoices-flat", "payslips-flat"}
    assert recommendations_path.read_bytes() == recommendations_before

    with pytest.raises(module.PolicyControlError, match="policy_revision_conflict"):
        module.apply_policy(control, candidate_path, expected_revision=1)

    locked_candidate = {**candidate, "revision": 3, "default": {"root": "Archiv", "layout": "flat"}}
    _write_yaml(candidate_path, locked_candidate)
    (control / ".organization-policy.write-lock").mkdir()
    with pytest.raises(module.PolicyControlError, match="policy_write_locked"):
        module.apply_policy(control, candidate_path, expected_revision=2)
    assert module.load_policy(control / "organization-policy.yaml") == candidate


def test_invalid_or_nonincrementing_policy_never_replaces_current(tmp_path):
    module = _module()
    control = tmp_path / "control"
    control.mkdir()
    policy_path = control / "organization-policy.yaml"
    _write_yaml(policy_path, _policy())
    before = policy_path.read_bytes()
    candidate = _policy(revision=2)
    candidate["rules"][0]["route"]["root"] = "../escape"
    candidate_path = tmp_path / "candidate.yaml"
    _write_yaml(candidate_path, candidate)

    with pytest.raises(module.PolicyControlError, match="policy_root_unsafe"):
        module.apply_policy(control, candidate_path, expected_revision=1)
    assert policy_path.read_bytes() == before

    _write_yaml(candidate_path, _policy(revision=3))
    with pytest.raises(module.PolicyControlError, match="policy_revision_must_increment_once"):
        module.apply_policy(control, candidate_path, expected_revision=1)
    assert policy_path.read_bytes() == before

    _write_yaml(candidate_path, _policy(revision=2))
    with pytest.raises(module.PolicyControlError, match="policy_revision_changed_without_content"):
        module.apply_policy(control, candidate_path, expected_revision=1)
    assert policy_path.read_bytes() == before
