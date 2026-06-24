from __future__ import annotations

from unittest import mock

import pytest

from openminion.modules.controlplane.contracts.models import (
    ParsedCommand,
    ResolvedContext,
)
from openminion.modules.identity.config import IdentityCtlConfig, PurposeBudget
from openminion.modules.identity.controlplane.main import IdentityCommandModule
from openminion.modules.identity.models import (
    AgentProfile,
    IdentitySnippet,
    PersonalitySpec,
    RiskSpec,
    RoleSpec,
    SnippetBudget,
    ToolPostureSpec,
)


def _ctx() -> ResolvedContext:
    return ResolvedContext(
        user_key="user",
        chat_key="chat",
        session_id="session",
        agent_id="ops",
        role="user",
        trace_id="trace",
        span_id="span",
    )


def _snippet(*, purpose: str, max_tokens: int) -> IdentitySnippet:
    return IdentitySnippet(
        agent_id="ops",
        purpose=purpose,
        text=f"purpose={purpose}",
        profile_version="profile-v1",
        render_version="render-v1",
        budget=SnippetBudget(
            max_tokens=max_tokens,
            used_tokens=min(max_tokens, 42),
            max_chars=max_tokens * 4,
            used_chars=min(max_tokens * 4, 120),
        ),
    )


def _profile() -> AgentProfile:
    return AgentProfile(
        agent_id="ops",
        display_name="Ops",
        profile_revision=2,
        role=RoleSpec(
            mission="Keep systems healthy.",
            responsibilities=["Operate safely"],
            hard_constraints=["Ask before destructive changes"],
        ),
        personality=PersonalitySpec(tone="professional", verbosity="normal"),
        risk=RiskSpec(
            risk_level="medium",
            confirm_before=["destructive_actions"],
        ),
        tool_posture=ToolPostureSpec(tool_use="allowed"),
        meta={"owner": "ops"},
    )


@pytest.mark.parametrize(
    ("requested_purpose", "canonical_purpose", "expected_tokens"),
    [
        ("chat", "act", 180),
        ("validate", "judge", 170),
        ("summary", "summarize", 160),
        ("decision", "decide", 160),
        ("judge", "judge", 170),
        ("summarize", "summarize", 160),
    ],
)
def test_identity_controlplane_render_normalizes_purpose_and_uses_canonical_budget(
    requested_purpose: str,
    canonical_purpose: str,
    expected_tokens: int,
) -> None:
    identity_ctl = mock.Mock()
    identity_ctl.render.return_value = _snippet(
        purpose=canonical_purpose,
        max_tokens=expected_tokens,
    )
    module = IdentityCommandModule(identity_ctl, identity_cfg=IdentityCtlConfig())

    result = module.handle_render(
        ParsedCommand(
            canonical="identity.render",
            original_text=f"/identity.render ops {requested_purpose}",
            args=["ops", requested_purpose],
        ),
        _ctx(),
    )

    assert result.ok is True
    identity_ctl.render.assert_called_once_with(
        agent_id="ops",
        purpose=canonical_purpose,
        max_tokens=expected_tokens,
    )
    assert result.data["purpose"] == canonical_purpose
    assert result.data["requested_purpose"] == requested_purpose
    assert result.data["max_tokens"] == expected_tokens
    assert f"Purpose: {canonical_purpose}" in result.text
    assert f"Max Tokens: {expected_tokens}" in result.text


def test_identity_controlplane_render_uses_injected_identity_config_budget() -> None:
    identity_ctl = mock.Mock()
    identity_ctl.render.return_value = _snippet(purpose="act", max_tokens=222)

    cfg = IdentityCtlConfig()
    cfg.rendering.default_budgets["act"] = PurposeBudget(max_tokens=222)
    module = IdentityCommandModule(identity_ctl, identity_cfg=cfg)

    result = module.handle_render(
        ParsedCommand(
            canonical="identity.render",
            original_text="/identity.render ops chat",
            args=["ops", "chat"],
        ),
        _ctx(),
    )

    assert result.ok is True
    identity_ctl.render.assert_called_once_with(
        agent_id="ops",
        purpose="act",
        max_tokens=222,
    )
    assert result.data["max_tokens"] == 222


@pytest.mark.parametrize(
    ("handler_name", "args", "expected_text", "field_value"),
    [
        (
            "handle_set_tone",
            ["ops", "friendly"],
            "Tone for ops updated to 'friendly'",
            "friendly",
        ),
        (
            "handle_set_verbosity",
            ["ops", "detailed"],
            "Verbosity for ops updated to 'detailed'",
            "detailed",
        ),
    ],
)
def test_identity_controlplane_quick_personality_updates_reuse_profile_copy_path(
    handler_name: str,
    args: list[str],
    expected_text: str,
    field_value: str,
) -> None:
    identity_ctl = mock.Mock()
    identity_ctl.get_profile.return_value = _profile()
    module = IdentityCommandModule(identity_ctl, identity_cfg=IdentityCtlConfig())

    result = getattr(module, handler_name)(
        ParsedCommand(
            canonical=f"identity.{handler_name.removeprefix('handle_').replace('_', '.')}",
            original_text="",
            args=args,
        ),
        _ctx(),
    )

    assert result.ok is True
    assert result.text == expected_text
    updated = identity_ctl.upsert_profile.call_args.args[0]
    assert updated.profile_revision == 3
    assert updated.role.mission == "Keep systems healthy."
    assert updated.meta == {"owner": "ops"}
    if handler_name == "handle_set_tone":
        assert updated.personality.tone == field_value
        assert result.data["tone"] == field_value
    else:
        assert updated.personality.verbosity == field_value
        assert result.data["verbosity"] == field_value


def test_identity_controlplane_set_mission_updates_role_without_rebuilding_profile() -> (
    None
):
    identity_ctl = mock.Mock()
    identity_ctl.get_profile.return_value = _profile()
    module = IdentityCommandModule(identity_ctl, identity_cfg=IdentityCtlConfig())

    result = module.handle_set_mission(
        ParsedCommand(
            canonical="identity.set.mission",
            original_text="",
            args=["ops", "Keep", "users", "moving"],
        ),
        _ctx(),
    )

    assert result.ok is True
    assert result.text == "Mission for ops updated successfully"
    updated = identity_ctl.upsert_profile.call_args.args[0]
    assert updated.profile_revision == 3
    assert updated.role.mission == "Keep users moving"
    assert updated.personality.tone == "professional"
    assert result.data["mission"] == "Keep users moving"
