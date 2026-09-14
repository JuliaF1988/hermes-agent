from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from agent.skill_utils import parse_frontmatter
from tools import skills_tool

SKILL = Path(__file__).resolve().parents[2] / "optional-skills/mcp/rag-source-wiki"


def _module():
    path = SKILL / "scripts/wiki_control.py"
    spec = importlib.util.spec_from_file_location("rag_source_wiki_control", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ids(ordinal: int) -> tuple[str, str]:
    return f"lsrc_{ordinal:040x}", f"sver_{ordinal:040x}"


def _binding(ordinal: int) -> dict:
    logical_id, version_id = _ids(ordinal)
    return {
        "logicalSourceId": logical_id,
        "sourceVersionId": version_id,
        "uri": f"rag://source/{logical_id}/version/{version_id}",
        "member": {
            "role": "primary_text",
            "path": "Transcript.md",
            "sha256": f"{ordinal:064x}",
            "size": 123,
            "mediaType": "text/markdown",
        },
    }


def _event(kind: str, ordinal: int, *, refresh: bool = True) -> dict:
    logical_id, _version_id = _ids(ordinal)
    return {
        "changeId": f"chg_{ordinal:040x}",
        "eventType": kind,
        "logicalSourceIds": [logical_id],
        "wikiRefreshRequired": refresh,
    }


def _page(body: str, *ordinals: int) -> dict:
    return {
        "path": "Projekte/Test.md",
        "title": "Testprojekt",
        "markdown": body,
        "sourceBindings": [_binding(ordinal) for ordinal in ordinals],
        "personClaims": [],
    }


def _batch(
    *,
    mode: str,
    base: dict | None,
    generation: str,
    sequence: int,
    pages: list[dict],
    events: list[dict] | None = None,
) -> dict:
    return {
        "schema": "rag.wiki-batch.v1",
        "mode": mode,
        "wikiTarget": "wiki-test",
        "baseCheckpoint": base,
        "nextCheckpoint": {"feedGeneration": generation, "sequence": sequence},
        "events": events or [],
        "pages": pages,
        "pendingReviewCount": 0,
    }


def _write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def test_skill_loads_with_three_explicit_target_settings(tmp_path, monkeypatch):
    target = tmp_path / "mcp/rag-source-wiki"
    shutil.copytree(SKILL, target)
    monkeypatch.setattr(skills_tool, "SKILLS_DIR", tmp_path)
    monkeypatch.setattr(skills_tool, "_skill_search_dirs", lambda: ([], [tmp_path], tmp_path))
    monkeypatch.setattr(skills_tool, "_is_skill_disabled", lambda *args, **kwargs: False)

    result = json.loads(skills_tool.skill_view("rag-source-wiki", preprocess=False))
    frontmatter, body = parse_frontmatter((target / "SKILL.md").read_text(encoding="utf-8"))

    assert result["success"] is True
    assert frontmatter["version"] == "1.0.0"
    assert [item["key"] for item in frontmatter["metadata"]["hermes"]["config"]] == [
        "rag.wiki_target_root",
        "rag.wiki_target_identity",
        "rag.wiki_checkpoint_path",
    ]
    assert len(frontmatter["description"]) <= 60
    assert len(body.split()) < 700


def test_initial_incremental_revision_and_locator_noop_are_idempotent(tmp_path: Path) -> None:
    module = _module()
    root = tmp_path / "vault"
    checkpoint = tmp_path / "state/checkpoint.json"
    batch_path = tmp_path / "batch.json"
    first = _batch(mode="full", base=None, generation="feed_a", sequence=5, pages=[_page("# Stand 1", 1)])
    _write(batch_path, first)

    assert module.apply(root, "wiki-test", checkpoint, batch_path)["created"] == 1
    assert module.apply(root, "wiki-test", checkpoint, batch_path)["status"] == "no_change"
    original = (root / "Projekte/Test.md").read_bytes()
    assert b"rag://source/lsrc_" in original

    base = {"feedGeneration": "feed_a", "sequence": 5}
    new_source = _batch(
        mode="incremental",
        base=base,
        generation="feed_a",
        sequence=6,
        pages=[_page("# Stand 2", 1, 2)],
        events=[_event("NEW_RELEVANT_SOURCE", 2)],
    )
    _write(batch_path, new_source)
    assert module.apply(root, "wiki-test", checkpoint, batch_path)["updated"] == 1

    revision = _batch(
        mode="incremental",
        base={"feedGeneration": "feed_a", "sequence": 6},
        generation="feed_a",
        sequence=7,
        pages=[_page("# Stand 3", 1, 3)],
        events=[_event("REVISION_REPLACE", 1)],
    )
    _write(batch_path, revision)
    assert module.apply(root, "wiki-test", checkpoint, batch_path)["updated"] == 1

    before_locator = (root / "Projekte/Test.md").read_bytes()
    locator = _batch(
        mode="incremental",
        base={"feedGeneration": "feed_a", "sequence": 7},
        generation="feed_a",
        sequence=8,
        pages=[],
        events=[_event("LOCATOR_MOVE", 1, refresh=False)],
    )
    _write(batch_path, locator)
    result = module.apply(root, "wiki-test", checkpoint, batch_path)
    assert result["status"] == "checkpoint_advanced"
    assert (root / "Projekte/Test.md").read_bytes() == before_locator
    assert json.loads(checkpoint.read_text())["sequence"] == 8


def test_generation_mismatch_requires_full_sync(tmp_path: Path) -> None:
    module = _module()
    root = tmp_path / "vault"
    checkpoint = tmp_path / "checkpoint.json"
    batch_path = tmp_path / "batch.json"
    _write(batch_path, _batch(mode="full", base=None, generation="feed_old", sequence=4, pages=[_page("# Old", 1)]))
    module.apply(root, "wiki-test", checkpoint, batch_path)

    stale_incremental = _batch(
        mode="incremental",
        base={"feedGeneration": "feed_new", "sequence": 0},
        generation="feed_new",
        sequence=1,
        pages=[_page("# Wrong", 1)],
        events=[_event("NEW_RELEVANT_SOURCE", 1)],
    )
    _write(batch_path, stale_incremental)
    with pytest.raises(module.WikiControlError, match="wiki_checkpoint_conflict"):
        module.apply(root, "wiki-test", checkpoint, batch_path)

    full = _batch(
        mode="full",
        base={"feedGeneration": "feed_old", "sequence": 4},
        generation="feed_new",
        sequence=0,
        pages=[_page("# Resynced", 1)],
    )
    _write(batch_path, full)
    assert module.apply(root, "wiki-test", checkpoint, batch_path)["updated"] == 1
    assert json.loads(checkpoint.read_text())["feedGeneration"] == "feed_new"


def test_failed_write_rolls_back_pages_and_preserves_checkpoint(tmp_path: Path, monkeypatch) -> None:
    module = _module()
    root = tmp_path / "vault"
    checkpoint = tmp_path / "checkpoint.json"
    batch_path = tmp_path / "batch.json"
    _write(batch_path, _batch(mode="full", base=None, generation="feed_a", sequence=1, pages=[_page("# Good", 1)]))
    module.apply(root, "wiki-test", checkpoint, batch_path)
    page_before = (root / "Projekte/Test.md").read_bytes()
    checkpoint_before = checkpoint.read_bytes()

    _write(
        batch_path,
        _batch(
            mode="incremental",
            base={"feedGeneration": "feed_a", "sequence": 1},
            generation="feed_a",
            sequence=2,
            pages=[_page("# Must rollback", 1)],
            events=[_event("REVISION_REPLACE", 1)],
        ),
    )
    original = module._atomic_write
    failed = False

    def fail_checkpoint(path: Path, data: bytes) -> None:
        nonlocal failed
        if path == checkpoint and not failed:
            failed = True
            raise OSError("injected")
        original(path, data)

    monkeypatch.setattr(module, "_atomic_write", fail_checkpoint)
    with pytest.raises(OSError, match="injected"):
        module.apply(root, "wiki-test", checkpoint, batch_path)
    assert (root / "Projekte/Test.md").read_bytes() == page_before
    assert checkpoint.read_bytes() == checkpoint_before


def test_unmanaged_content_and_speaker_role_overreach_fail_closed(tmp_path: Path) -> None:
    module = _module()
    root = tmp_path / "vault"
    (root / "Projekte").mkdir(parents=True)
    (root / "Projekte/Test.md").write_text("human page", encoding="utf-8")
    batch_path = tmp_path / "batch.json"
    _write(batch_path, _batch(mode="full", base=None, generation="feed_a", sequence=0, pages=[_page("# Managed", 1)]))
    with pytest.raises(module.WikiControlError, match="wiki_unmanaged_target_conflict"):
        module.plan(root, "wiki-test", tmp_path / "checkpoint.json", batch_path)

    page = _page("# Bad attribution", 1)
    logical_id, version_id = _ids(1)
    page["personClaims"] = [{
        "semanticRole": "subject",
        "person": "Person A",
        "logicalSourceId": logical_id,
        "sourceVersionId": version_id,
        "evidenceKind": "speaker_binding",
        "speakerStatus": "confirmed",
    }]
    with pytest.raises(module.WikiControlError, match="wiki_speaker_role_overreach"):
        module.validate_batch(
            _batch(mode="full", base=None, generation="feed_a", sequence=0, pages=[page]),
            "wiki-test",
        )
