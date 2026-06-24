from __future__ import annotations

from pathlib import Path

from openminion.modules.identity.interfaces import (
    ensure_identity_repository_compatibility,
)
from openminion.modules.identity.models import (
    AgentProfile,
    PersonalitySpec,
    RiskSpec,
    RoleSpec,
    ToolPostureSpec,
)
from openminion.modules.identity.storage.repository import (
    create_sqlite_identity_repository,
)


def _profile(agent_id: str) -> AgentProfile:
    return AgentProfile(
        agent_id=agent_id,
        display_name=agent_id,
        profile_revision=1,
        role=RoleSpec(mission=f"I am {agent_id}, a pragmatic assistant."),
        personality=PersonalitySpec(tone="professional", verbosity="normal"),
        risk=RiskSpec(risk_level="medium", confirm_before=["destructive_actions"]),
        tool_posture=ToolPostureSpec(tool_use="allowed"),
        meta={},
    )


def test_sqlite_identity_repository_round_trip(tmp_path: Path) -> None:
    repo = create_sqlite_identity_repository(sqlite_path=tmp_path / "identity.db")
    try:
        profile = _profile("agent-adapter")
        version = repo.upsert_profile(profile)
        loaded = repo.get_profile("agent-adapter")
    finally:
        repo.close()

    assert str(version).strip() != ""
    assert loaded is not None
    assert loaded.agent_id == "agent-adapter"


def test_sqlite_identity_repository_is_contract_compatible(tmp_path: Path) -> None:
    repo = create_sqlite_identity_repository(sqlite_path=tmp_path / "identity.db")
    try:
        success, errors = ensure_identity_repository_compatibility(repo, strict=False)
    finally:
        repo.close()
    assert success is True
    assert errors == []
