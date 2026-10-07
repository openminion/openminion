from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from openminion.modules.brain.adapters.tool import ToolAdapter
from openminion.modules.skill.config import SkillConfig
from openminion.modules.skill.errors import SkillError
from openminion.modules.skill.interfaces import SkillIngestAuthority
from openminion.modules.skill.runtime.skill import Skill
from openminion.modules.tool.contracts.model_ids import (
    MODEL_SKILL_INGEST,
    MODEL_SKILL_INGEST_URL,
)
from openminion.modules.tool.registry import ToolRegistry
from openminion.modules.tool.runtime.policy_defaults import DEFAULT_POLICY
from openminion.tools.skill.plugin import _h_skill_ingest, register
from openminion.tools.skill.registrar import REGISTRAR


_MARKDOWN = """---
name: Confirmed Save
id: confirmed-save
---

# Procedure
1. Inspect the target.
2. Make the smallest safe change.

# Verification
Run the focused checks before completion.
"""


class _FakeSkill:
    def __init__(self) -> None:
        self.ingest_calls: list[dict[str, Any]] = []

    def ingest_text(self, **kwargs: Any) -> tuple[str, str, list[str]]:
        self.ingest_calls.append(kwargs)
        return "confirmed_save", "version-1", ["admission.pending"]

    def get_skill_version_state(self, **kwargs: Any) -> dict[str, str | None]:
        return {"admission_state": "pending", "active_version_hash": None}

    def render_snippet(self, **kwargs: Any) -> tuple[str, str]:
        return "snippet", "snippet-hash"


def _adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[ToolAdapter, _FakeSkill]:
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / "data"))
    registry = ToolRegistry()
    register(registry)
    skill = _FakeSkill()
    adapter = ToolAdapter(
        workspace_root=tmp_path,
        runtime_registry=registry,
        artifactctl=object(),
        skill_api=skill,
        agent_id="agent-a",
    )
    return adapter, skill


def _command(*, permission_mode: str | None = None) -> dict[str, Any]:
    command: dict[str, Any] = {
        "tool_name": MODEL_SKILL_INGEST,
        "args": {"name": "Confirmed Save", "markdown": _MARKDOWN},
    }
    if permission_mode:
        command["inputs"] = {"permission_mode": permission_mode}
    return command


def _skill(tmp_path: Path, events: list[tuple[str, dict[str, Any]]]) -> Skill:
    return Skill(
        SkillConfig(
            sqlite_path=str(tmp_path / "data" / "skill.db"),
            blob_root=str(tmp_path / "data" / "blobs"),
            fallback_root=str(tmp_path / "data" / "fallback"),
            wal=False,
        ),
        event_callback=lambda name, payload: events.append((name, payload)),
    )


def _activate(
    skill: Skill,
    *,
    skill_id: str,
    version_hash: str,
    expected_active_version_hash: str | None,
) -> None:
    assert skill.store.activate_skill_version(
        skill_id=skill_id,
        version_hash=version_hash,
        expected_active_version_hash=expected_active_version_hash,
        target_status="draft",
        authority_class="local_operator",
        reviewer_id="local:test",
        reason="confirmed save test",
        decided_at="2026-09-28T00:00:00Z",
    )


def test_skill_ingest_requires_confirmation_before_handler_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, skill = _adapter(tmp_path, monkeypatch)
    try:
        result = adapter.execute(
            command=_command(), session_id="session-a", trace_id="turn-a"
        )
    finally:
        adapter.close()

    assert result["status"] == "needs_user"
    assert result["error"]["code"] == "CONFIRM_REQUIRED"
    assert skill.ingest_calls == []


def test_skill_ingest_denial_never_calls_handler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, skill = _adapter(tmp_path, monkeypatch)
    adapter.set_approval_callback(lambda *_args: False)
    try:
        result = adapter.execute(
            command=_command(), session_id="session-a", trace_id="turn-a"
        )
    finally:
        adapter.close()

    assert result["status"] == "error"
    assert result["error"]["code"] == "POLICY_DENIED"
    assert skill.ingest_calls == []


def test_skill_ingest_allow_once_calls_handler_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, skill = _adapter(tmp_path, monkeypatch)
    approvals: list[str] = []
    adapter.set_approval_callback(
        lambda tool_name, _args, _approval_id, _policy_facts: (
            approvals.append(tool_name) or True
        )
    )
    try:
        result = adapter.execute(
            command=_command(), session_id="session-a", trace_id="turn-a"
        )
    finally:
        adapter.close()

    assert result["status"] == "success"
    assert result["outputs"]["admission_state"] == "pending"
    assert result["outputs"]["active_version_hash"] is None
    assert approvals == [MODEL_SKILL_INGEST]
    assert len(skill.ingest_calls) == 1
    assert skill.ingest_calls[0]["agent_id"] == "agent-a"


def test_skill_ingest_bypass_is_prior_authorization_not_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, skill = _adapter(tmp_path, monkeypatch)
    try:
        result = adapter.execute(
            command=_command(permission_mode="bypass"),
            session_id="session-a",
            trace_id="turn-a",
        )
    finally:
        adapter.close()

    assert result["status"] == "success"
    assert result["outputs"]["admission_state"] == "pending"
    assert result["outputs"]["active_version_hash"] is None
    assert len(skill.ingest_calls) == 1


def test_confirmation_policy_does_not_change_url_ingest() -> None:
    required = DEFAULT_POLICY["confirm"]["required_tools"]
    assert MODEL_SKILL_INGEST in required
    assert MODEL_SKILL_INGEST_URL not in required


def test_model_tool_description_states_save_and_pending_contract() -> None:
    manifest = REGISTRAR.get_manifest(SimpleNamespace())
    definition = next(
        item
        for item in manifest.model_tools
        if item.model_tool_id == MODEL_SKILL_INGEST
    )
    assert "at most one" in definition.description
    assert "substantial reusable work" in definition.description
    assert "requires user approval" in definition.description
    assert "both required arguments: name and markdown" in definition.description
    assert "pending until separate operator admission" in definition.description
    assert "not immediately selectable" in definition.description
    assert "report operator_admission_command exactly" in definition.description


@pytest.mark.parametrize("scope", ["global", "project", "user"])
def test_model_skill_ingest_rejects_non_agent_scope(scope: str) -> None:
    skill = _FakeSkill()
    result = _h_skill_ingest(
        {"name": "Confirmed Save", "markdown": _MARKDOWN, "scope": scope},
        SimpleNamespace(skill_api=skill, agent_id="agent-a"),
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_ARGS"
    assert skill.ingest_calls == []


def test_model_skill_ingest_requires_current_agent_identity() -> None:
    skill = _FakeSkill()
    result = _h_skill_ingest(
        {"name": "Confirmed Save", "markdown": _MARKDOWN},
        SimpleNamespace(skill_api=skill),
    )

    assert result == {
        "ok": False,
        "error": {
            "code": "INVALID_RUNTIME_CONTEXT",
            "message": "skill.ingest requires the current agent identity.",
        },
    }
    assert skill.ingest_calls == []


def test_model_skill_ingest_returns_pending_canonical_lifecycle(tmp_path: Path) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    skill = _skill(tmp_path, events)
    try:
        result = _h_skill_ingest(
            {"name": "Confirmed Save", "markdown": _MARKDOWN},
            SimpleNamespace(skill_api=skill, agent_id="agent-a"),
        )
        package = skill.get_skill(result["skill_id"], result["version_hash"])
    finally:
        skill.close()

    assert result["ok"] is True
    assert result["admission_state"] == "pending"
    assert result["active_version_hash"] is None
    assert result["operator_admission_command"] == (
        "openminion skill admit --skill-id confirmed-save "
        f"--version-hash {result['version_hash']} "
        "--expected-active-version-hash none --target-status verified "
        '--reason "Reviewed and approved staged skill"'
    )
    assert package.scope == "agent"
    assert package.agent_id == "agent-a"
    assert any(name == "skill.ingested" for name, _payload in events)


def test_duplicate_returns_current_canonical_lifecycle(tmp_path: Path) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    skill = _skill(tmp_path, events)
    ctx = SimpleNamespace(skill_api=skill, agent_id="agent-a")
    try:
        first = _h_skill_ingest({"name": "Confirmed Save", "markdown": _MARKDOWN}, ctx)
        pending_duplicate = _h_skill_ingest(
            {"name": "Confirmed Save", "markdown": _MARKDOWN}, ctx
        )
        _activate(
            skill,
            skill_id=first["skill_id"],
            version_hash=first["version_hash"],
            expected_active_version_hash=None,
        )
        admitted_duplicate = _h_skill_ingest(
            {"name": "Confirmed Save", "markdown": _MARKDOWN}, ctx
        )
    finally:
        skill.close()

    assert pending_duplicate["version_hash"] == first["version_hash"]
    assert pending_duplicate["admission_state"] == "pending"
    assert pending_duplicate["active_version_hash"] is None
    assert admitted_duplicate["version_hash"] == first["version_hash"]
    assert admitted_duplicate["admission_state"] == "admitted"
    assert admitted_duplicate["active_version_hash"] == first["version_hash"]
    assert admitted_duplicate["operator_admission_command"] is None


def test_duplicate_admitted_noncurrent_reports_active_version(tmp_path: Path) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    skill = _skill(tmp_path, events)
    ctx = SimpleNamespace(skill_api=skill, agent_id="agent-a")
    changed = _MARKDOWN.replace("Run the focused checks", "Run all focused checks")
    try:
        first = _h_skill_ingest({"name": "Confirmed Save", "markdown": _MARKDOWN}, ctx)
        _activate(
            skill,
            skill_id=first["skill_id"],
            version_hash=first["version_hash"],
            expected_active_version_hash=None,
        )
        second = _h_skill_ingest({"name": "Confirmed Save", "markdown": changed}, ctx)
        assert second["operator_admission_command"] == (
            "openminion skill admit --skill-id confirmed-save "
            f"--version-hash {second['version_hash']} "
            f"--expected-active-version-hash {first['version_hash']} "
            "--target-status verified "
            '--reason "Reviewed and approved staged skill"'
        )
        _activate(
            skill,
            skill_id=second["skill_id"],
            version_hash=second["version_hash"],
            expected_active_version_hash=first["version_hash"],
        )
        old_duplicate = _h_skill_ingest(
            {"name": "Confirmed Save", "markdown": _MARKDOWN}, ctx
        )
    finally:
        skill.close()

    assert old_duplicate["version_hash"] == first["version_hash"]
    assert old_duplicate["admission_state"] == "admitted"
    assert old_duplicate["active_version_hash"] == second["version_hash"]
    assert old_duplicate["operator_admission_command"] is None


def test_pending_source_is_exact_and_not_selectable_across_restart(
    tmp_path: Path,
) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    skill = _skill(tmp_path, events)
    authority = SkillIngestAuthority.runtime(
        surface="test.confirmed_save", source_kind="local"
    )
    try:
        skill_id, version_hash, _warnings = skill.ingest_text(
            name="Confirmed Save",
            markdown=_MARKDOWN,
            scope="agent",
            agent_id="agent-a",
            authority=authority,
        )
        package = skill.get_skill(skill_id, version_hash)
        digest = package.source_artifact_ref.rsplit("/", 1)[-1]
        assert (
            Path(skill._blob_store.path_for(digest)).read_bytes() == _MARKDOWN.encode()
        )
        fingerprint = package.to_content_fingerprint()
        assert skill.catalog_summaries("agent-a") == []
    finally:
        skill.close()

    restarted = _skill(tmp_path, [])
    try:
        reloaded = restarted.get_skill(skill_id, version_hash)
        duplicate_id, duplicate_hash, warnings = restarted.ingest_text(
            name="Confirmed Save",
            markdown=_MARKDOWN,
            scope="agent",
            agent_id="agent-a",
            authority=authority,
        )
        assert restarted.catalog_summaries("agent-a") == []
    finally:
        restarted.close()

    assert reloaded.to_content_fingerprint() == fingerprint
    assert reloaded.source_artifact_ref == package.source_artifact_ref
    assert (duplicate_id, duplicate_hash) == (skill_id, version_hash)
    assert "admission.duplicate_content" in warnings


def test_admitted_agent_skill_is_not_visible_to_other_agent(tmp_path: Path) -> None:
    skill = _skill(tmp_path, [])
    try:
        skill_id, version_hash, _warnings = skill.ingest_text(
            name="Confirmed Save",
            markdown=_MARKDOWN,
            scope="agent",
            agent_id="agent-a",
            authority=SkillIngestAuthority.runtime(
                surface="test.confirmed_save", source_kind="local"
            ),
        )
        _activate(
            skill,
            skill_id=skill_id,
            version_hash=version_hash,
            expected_active_version_hash=None,
        )
        own_catalog = skill.catalog_summaries("agent-a")
        other_catalog = skill.catalog_summaries("agent-b")
    finally:
        skill.close()

    assert [item["id"] for item in own_catalog] == [skill_id]
    assert other_catalog == []


def test_missing_canonical_stage_fails_without_success_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    skill = _skill(tmp_path, events)
    monkeypatch.setattr(
        skill.store,
        "stage_skill_version",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("stage failed")),
    )
    try:
        with pytest.raises(SkillError) as excinfo:
            skill.ingest_text(
                name="Confirmed Save",
                markdown=_MARKDOWN,
                scope="agent",
                agent_id="agent-a",
                authority=SkillIngestAuthority.runtime(
                    surface="test.confirmed_save", source_kind="local"
                ),
            )
    finally:
        skill.close()

    assert excinfo.value.code == "INGEST_NOT_DURABLE"
    assert not any(name == "skill.ingested" for name, _payload in events)
