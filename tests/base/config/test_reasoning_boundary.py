import pytest

from openminion.base.config.runtime.reasoning import (
    resolve_runtime_reasoning_config,
)
from openminion.modules.llm.reasoning import build_runtime_thinking_diagnostics


@pytest.mark.parametrize(
    ("system", "agent", "invocation"),
    [
        (None, None, None),
        ("off", "minimal", "detailed"),
        ("disabled", "low", "deep"),
        ("minimal", None, "unknown-profile"),
    ],
)
def test_base_config_boundary_matches_llm_reasoning_owner(
    system: str | None,
    agent: str | None,
    invocation: str | None,
) -> None:
    kwargs = {
        "code_default_profile": "minimal",
        "system_profile": system,
        "agent_profile": agent,
        "invocation_requested_profile": invocation,
        "provider_name": "openai",
        "model_name": "gpt-test",
        "purpose": "boundary-parity",
    }

    lower = resolve_runtime_reasoning_config(
        **{key: value for key, value in kwargs.items() if key != "purpose"}
    )
    owner = build_runtime_thinking_diagnostics(**kwargs)

    assert owner.code_default_profile == "minimal"
    assert lower.system_profile == owner.system_profile
    assert lower.agent_profile == owner.agent_profile
    assert lower.requested_profile == owner.invocation_requested_profile
    assert lower.diagnostics_payload() == owner.effective.diagnostics_payload()
