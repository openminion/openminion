from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from openminion.modules.brain.loop.tools.plan import _validate_workflow_id
from openminion.modules.skill.interfaces import SkillIngestAuthority
from openminion.modules.skill.runtime.skill import Skill
from openminion.modules.task.plan import TaskPlan


COMPLEX_SKILL = """---
name: release-check
id: release-check
tools: [tool.shell, tool.fetch]
recipe:
  objective: Validate and publish a release
  steps:
    - step_id: test
      instruction: Run tests
      tool_id: tool.shell
    - step_id: verify
      instruction: Verify the release manifest
      tool_id: tool.fetch
    - step_id: publish
      instruction: Publish the release
      tool_id: tool.shell
---

# Summary
Validate and publish a release.

# Procedure
- tool.shell run tests
- tool.fetch verify the release manifest
- tool.shell publish the release

# Verification
- Confirm the published manifest.

# Rollback
- Restore the prior release pointer.
"""


def test_complex_skill_workflow_is_version_pinned_in_model_authored_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(data_root))
    ctl = Skill(
        {
            "skill": {
                "sqlite_path": str(data_root / "skill.db"),
                "blob_root": str(data_root / "blob"),
                "fallback_root": str(data_root / "fallback"),
                "known_tools": ["tool.shell", "tool.fetch"],
                "wal": False,
            },
            "paths": {"data_root": str(data_root)},
        }
    )
    authority = SkillIngestAuthority.local_operator(
        surface="test.complex_skill", principal_id="local:test"
    )
    try:
        skill_id, version_hash, _ = ctl.ingest_text(
            "release-check", COMPLEX_SKILL, authority=authority
        )
        ctl.admit_skill_version(
            skill_id=skill_id,
            version_hash=version_hash,
            expected_active_version_hash=None,
            target_status="verified",
            reason="complex workflow reviewed",
            authority=authority,
        )
        workflow = ctl.get_workflow(f"workflow.{skill_id}")
        plan = TaskPlan(
            plan_id="release-plan",
            objective="Publish safely",
            workflow_id=workflow.workflow.workflow_id,
            workflow_version_hash=workflow.version_hash,
            steps=[
                {
                    "step_id": "test",
                    "description": "Run tests",
                    "tool_families": ["exec"],
                },
                {
                    "step_id": "verify",
                    "description": "Verify manifest",
                    "depends_on": ["test"],
                    "tool_families": ["fetch"],
                },
                {
                    "step_id": "publish",
                    "description": "Publish release",
                    "depends_on": ["verify"],
                    "tool_families": ["exec"],
                },
            ],
        )
        assert plan.workflow_version_hash == version_hash

        loop_ctx = SimpleNamespace(
            state=SimpleNamespace(agent_id="agent.release"),
            skill_api=ctl,
        )
        assert (
            _validate_workflow_id(
                loop_ctx,
                workflow_id=plan.workflow_id,
                workflow_version_hash=plan.workflow_version_hash,
            )
            is None
        )
        conflict = _validate_workflow_id(
            loop_ctx,
            workflow_id=plan.workflow_id,
            workflow_version_hash="stale-version",
        )
        assert conflict is not None
        assert conflict.error.code == "PLAN_WORKFLOW_VERSION_CONFLICT"
    finally:
        ctl.close()
