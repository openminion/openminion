from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openminion.modules.identity.runtime.service import IdentityCtl


def ensure_default_profile(
    identityctl: "IdentityCtl", agent_id: str, system_prompt: str = ""
) -> None:
    """Ensures that an AgentProfile exists for the given agent_id."""
    # Check if profile already exists
    existing_profile = identityctl.get_profile(agent_id)
    if existing_profile is not None:
        return  # Profile exists, nothing to do

    # Build a minimal default profile
    # Extract first sentence of system_prompt or create generic one
    import re

    first_sentence_match = re.search(r"^([^\.!?]*\.)(?:\s|$)", system_prompt or "")
    if first_sentence_match:
        mission = first_sentence_match.group(1).strip()
        if len(mission) > 120:
            mission = first_sentence_match.group(1)[:120].strip() + "..."
    else:
        mission = f"I am {agent_id}, a pragmatic AI assistant."

    # Import here to avoid circular dependencies
    from openminion.modules.identity.models import (
        AgentProfile,
        RoleSpec,
        PersonalitySpec,
        RiskSpec,
        ToolPostureSpec,
    )

    default_profile = AgentProfile(
        agent_id=agent_id,
        display_name=agent_id,
        profile_revision=1,
        role=RoleSpec(mission=mission, responsibilities=[], hard_constraints=[]),
        personality=PersonalitySpec(tone="professional", verbosity="normal"),
        risk=RiskSpec(risk_level="medium", confirm_before=["destructive_actions"]),
        tool_posture=ToolPostureSpec(tool_use="allowed"),
        meta={"source": "default"},
    )

    # Save the new profile
    identityctl.upsert_profile(default_profile)
