from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from openminion.modules.skill.config import SkillConfig
from openminion.modules.skill.runtime.skill import Skill


_SAMPLE_SKILL = """
---
name: Sample Test Skill
id: sample_test_skill
status: draft
tags: [test]
tools: [tool.shell]
risk: low
---

## Summary
A minimal skill used for delete/retention tests.

## Procedure
1. Echo hello.
""".strip()


def _cfg(tmp_path: Path, *, retention: str | None = None) -> dict:
    skill_cfg: dict[str, Any] = {
        "sqlite_path": str(tmp_path / "blob-retention.db"),
        "wal": False,
        "default_status_filter": ["draft", "verified", "blessed"],
        "high_risk_status_filter": ["blessed", "verified", "draft"],
        "known_tools": ["tool.shell"],
    }
    if retention is not None:
        skill_cfg["skill_blob_retention"] = retention
    return {"skill": skill_cfg}


def _ingest_and_resolve_blob_path(ctl: Skill) -> tuple[str, Path]:
    skill_id, version_hash, _ = ctl.ingest_text(
        name="Sample Test Skill", markdown=_SAMPLE_SKILL
    )
    package = ctl.get_skill(skill_id=skill_id, version_hash=version_hash)
    ref = str(package.source_artifact_ref or "")
    assert ref.startswith("artifact://sha256/")
    digest = ref.rsplit("/", 1)[-1]
    blob_path = Path(ctl._blob_store.path_for(digest))
    assert blob_path.exists(), "ingest should produce a source blob on disk"
    return skill_id, blob_path


def test_config_default_is_retain() -> None:
    cfg = SkillConfig()
    assert cfg.skill_blob_retention == "retain"


def test_delete_skill_default_retains_blob(tmp_path: Path) -> None:
    events: list[tuple[str, dict]] = []

    ctl = Skill(_cfg(tmp_path), event_callback=lambda e, d: events.append((e, d)))
    skill_id, blob_path = _ingest_and_resolve_blob_path(ctl)

    ctl.delete_skill(skill_id=skill_id)

    # Blob remains on disk under the default retention policy.
    assert blob_path.exists(), "default 'retain' policy must keep the source blob"

    retained = [e for e in events if e[0] == "skill.blob_retained_on_delete"]
    assert retained, "audit event 'skill.blob_retained_on_delete' must fire"
    payload = retained[-1][1]
    assert payload["skill_id"] == skill_id
    assert payload["retention_policy"] == "retain"
    assert payload["reason"] == "default_policy"
    assert isinstance(payload["source_refs"], list)
    assert any("artifact://sha256/" in ref for ref in payload["source_refs"])


def test_delete_skill_gc_removes_blob_when_configured(tmp_path: Path) -> None:
    events: list[tuple[str, dict]] = []

    ctl = Skill(
        _cfg(tmp_path, retention="gc"),
        event_callback=lambda e, d: events.append((e, d)),
    )
    skill_id, blob_path = _ingest_and_resolve_blob_path(ctl)

    ctl.delete_skill(skill_id=skill_id)

    assert not blob_path.exists(), "'gc' policy must remove the source blob"

    gc_events = [e for e in events if e[0] == "skill.blob_gc_on_delete"]
    assert gc_events, "audit event 'skill.blob_gc_on_delete' must fire"
    payload = gc_events[-1][1]
    assert payload["skill_id"] == skill_id
    assert payload["retention_policy"] == "gc"
    assert payload["outcome"] == "gc_attempted"
    assert payload["failed_refs"] == []


def test_delete_skill_gc_failure_does_not_block_delete(tmp_path: Path) -> None:
    events: list[tuple[str, dict]] = []

    ctl = Skill(
        _cfg(tmp_path, retention="gc"),
        event_callback=lambda e, d: events.append((e, d)),
    )
    skill_id, _ = _ingest_and_resolve_blob_path(ctl)

    # Force blob_store.delete to raise so we can verify the SQL delete still
    # completes and the audit event records the failure.
    with patch.object(
        ctl._blob_store, "delete", side_effect=RuntimeError("simulated blob failure")
    ):
        result = ctl.delete_skill(skill_id=skill_id)

    # SQL rows still cleaned despite the blob failure.
    assert isinstance(result, dict)
    with pytest.raises(Exception):
        ctl.get_skill(skill_id=skill_id)

    gc_events = [e for e in events if e[0] == "skill.blob_gc_on_delete"]
    assert gc_events, "audit event must still fire on blob GC failure"
    payload = gc_events[-1][1]
    assert payload["outcome"] == "gc_partial_failure"
    assert payload["failed_refs"], "failed_refs must enumerate the failing blob refs"
    assert "simulated blob failure" in payload["failed_refs"][0]["error"]
