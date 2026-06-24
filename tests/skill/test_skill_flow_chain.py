from __future__ import annotations

from pathlib import Path

from openminion.modules.skill.runtime.skill import Skill


def _cfg(tmp_path: Path) -> dict:
    return {
        "skill": {
            "sqlite_path": str(tmp_path / "skill-flow.db"),
            "wal": False,
            "default_status_filter": ["draft", "verified", "blessed"],
            "high_risk_status_filter": ["blessed", "verified", "draft"],
            "known_tools": ["tool.shell", "tool.log"],
        }
    }


FLOW_SKILL = """
---
name: Docker Recovery Flow
id: docker_recovery_flow
status: verified
tags: [docker, ops, recovery]
tools: [tool.shell, tool.log]
risk: medium
applies_to:
  intents: [restart docker, recover docker]
  steps: [ToolStep:tool.shell, ToolStep:tool.log]
inputs:
  - name: host
    type: string
    description: Target host name
  - name: service
    type: string
    description: Service name
---

# Summary
Recover the docker service with validation and logging.

# Procedure
- tool.shell run "systemctl restart docker"
- tool.log tail "docker"
- tool.shell run "systemctl status docker"

# Verification
- tool.shell run "systemctl status docker"

# Rollback
- tool.shell run "systemctl restart docker"
""".strip()


def test_skill_flow_chain_end_to_end(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path))
    try:
        skill_id, version_hash, warnings = ctl.ingest_text(
            name="Docker Recovery Flow",
            markdown=FLOW_SKILL,
        )

        assert skill_id == "docker_recovery_flow"
        assert len(version_hash) == 64
        assert warnings == []

        package = ctl.get_skill(skill_id, version_hash)
        assert package.name == "Docker Recovery Flow"
        assert set(package.tools) == {"tool.shell", "tool.log"}
        assert "restart docker" in package.applies_to.get("intents", [])

        recipe = package.recipe
        assert recipe is not None
        assert [step.tool_id for step in recipe.steps] == [
            "tool.shell",
            "tool.log",
            "tool.shell",
        ]

        matches = ctl.match(
            intent_text="restart docker and collect logs",
            step_hint={"tool_id": "tool.shell", "risk": "medium", "verify": True},
            agent_id="agent.ops",
            k=1,
        )
        assert matches
        assert matches[0].skill_id == "docker_recovery_flow"

        snippet, snippet_hash = ctl.render_snippet(
            skill_id=skill_id,
            version_hash=version_hash,
            purpose="act",
            max_tokens=200,
        )
        assert snippet
        assert len(snippet_hash) == 64
        assert "tool.shell" in snippet
    finally:
        ctl.close()
