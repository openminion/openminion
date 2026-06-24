from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from openminion.modules.skill.runtime.skill import Skill
from openminion.modules.skill.diagnostics.events import emit_skill_operation
from openminion.modules.telemetry.service import TelemetryCtl, TelemetryService

from .test_skill import DOCKER_SKILL


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _cfg(tmp_path: Path) -> dict:
    return {
        "skill": {
            "sqlite_path": str(tmp_path / "skill.db"),
            "wal": False,
            "default_status_filter": ["draft", "verified", "blessed"],
            "high_risk_status_filter": ["blessed", "verified"],
            "known_tools": ["tool.shell", "tool.log"],
        }
    }


def test_skill_service_emits_shortlist_expand_select_and_fallback(
    tmp_path: Path,
) -> None:
    telemetry = TelemetryService(str(tmp_path / ".openminion" / "telemetry.db"))
    ctl = TelemetryCtl(telemetry)
    skill = Skill(_cfg(tmp_path), telemetryctl=ctl)
    skill.set_telemetry_context(session_id="sess-skill", turn_id="turn-1")
    skill_id, version_hash, warnings = skill.ingest_text(
        name="Restart Docker Services Safely",
        markdown=DOCKER_SKILL,
    )
    assert warnings == []

    matches = skill.match(
        intent_text="restart docker and inspect daemon logs",
        step_hint={"tool_id": "tool.shell", "risk": "medium", "verify": True},
        agent_id="agent.ops",
        k=3,
    )
    assert matches

    snippet, _ = skill.render_snippet(
        skill_id=skill_id,
        version_hash=version_hash,
        purpose="act",
        max_tokens=80,
    )
    assert snippet

    run_id = skill.log_run(
        session_id="sess-skill",
        agent_id="agent.ops",
        skill_id=skill_id,
        version_hash=version_hash,
        used_for="act",
        outcome="partial",
    )
    assert run_id

    summary = _run(telemetry.get_module_summary("sess-skill"))
    stats = summary["openminion-skill"]
    assert stats["operation_counts"]["shortlist"] == 1
    assert stats["operation_counts"]["select"] == 1
    assert stats["operation_counts"]["expand"] == 1
    assert stats["operation_counts"]["fallback"] == 1
    assert stats["custom_counter_sums"]["candidate_count"] >= 1.0
    assert stats["custom_counter_sums"]["selected_cards"] == 1.0

    skill.close()
    _run(telemetry.close())


def test_skill_helper_rejects_unknown_operation_and_absent_adapter() -> None:
    assert (
        emit_skill_operation(
            telemetryctl=None,
            session_id="sess-skill-invalid",
            turn_id="turn-1",
            operation="unknown",
        )
        is False
    )
