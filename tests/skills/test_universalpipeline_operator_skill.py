"""Exercise the optional skill and normal MCP config loader, not a custom client."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import yaml

from agent.skill_utils import parse_frontmatter
from tools.mcp_tool_config import _interpolate_env_vars
from tools import skills_tool


SKILL = Path(__file__).resolve().parents[2] / "optional-skills/mcp/universalpipeline-operator"


def test_operator_skill_loads_via_normal_skill_surface(tmp_path, monkeypatch):
    target = tmp_path / "mcp/universalpipeline-operator"
    shutil.copytree(SKILL, target)
    monkeypatch.setattr(skills_tool, "SKILLS_DIR", tmp_path)
    monkeypatch.setattr(skills_tool, "_skill_search_dirs", lambda: ([], [tmp_path], tmp_path))
    monkeypatch.setattr(skills_tool, "_is_skill_disabled", lambda *args, **kwargs: False)
    result = json.loads(skills_tool.skill_view("universalpipeline-operator", preprocess=False))
    assert result["success"] is True
    assert result["name"] == "universalpipeline-operator"
    frontmatter, body = parse_frontmatter((target / "SKILL.md").read_text())
    assert frontmatter["version"] == "1.0.0"
    assert len(frontmatter["description"]) <= 60
    assert len(body.split()) < 600
    assert result.get("linked_files") or result.get("files") or "references/connection.md" in result["content"]


def test_mcp_config_uses_runtime_secret_scope_without_changing_other_servers(monkeypatch):
    from agent import secret_scope

    secrets = {"UP_MCP_URL": "http://isolated-core:8000/mcp", "UP_MCP_TOKEN": "synthetic-test-only"}
    monkeypatch.setattr(secret_scope, "get_secret", lambda key, default=None: secrets.get(key, default))
    example = yaml.safe_load((SKILL / "references/mcp-config.example.yaml").read_text())
    existing = {"other": {"url": "https://untouched.invalid/mcp"}}
    combined = {**existing, **example["mcp_servers"]}
    loaded = _interpolate_env_vars(combined)
    assert loaded["other"] == existing["other"]
    up = loaded["universalpipeline"]
    assert up["url"] == secrets["UP_MCP_URL"]
    assert up["headers"]["Authorization"] == "Bearer " + secrets["UP_MCP_TOKEN"]
    assert up["supports_parallel_tool_calls"] is False
    assert "command" not in up
    assert example["mcp_servers"]["universalpipeline"]["headers"]["Authorization"] == "Bearer ${UP_MCP_TOKEN}"


def test_unset_secret_is_not_invented(monkeypatch):
    from agent import secret_scope

    monkeypatch.setattr(secret_scope, "get_secret", lambda key, default=None: default)
    example = yaml.safe_load((SKILL / "references/mcp-config.example.yaml").read_text())
    loaded = _interpolate_env_vars(example)
    assert loaded["mcp_servers"]["universalpipeline"]["headers"]["Authorization"] == "Bearer ${UP_MCP_TOKEN}"
