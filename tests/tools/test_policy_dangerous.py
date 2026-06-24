from __future__ import annotations

import pytest

from openminion.modules.tool.runtime.dangerous import detect_dangerous_command
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.runtime.policy import Policy


def test_dangerous_detector_flags_rm_rf():
    match = detect_dangerous_command(["rm", "-rf", "/"])
    assert match.dangerous is True
    assert match.pattern_id


def test_dangerous_detector_allows_safe():
    match = detect_dangerous_command(["ls", "-la"])
    assert match.dangerous is False


def test_dangerous_policy_prompt_requires_confirm():
    policy = Policy(raw={"dangerous": {"enabled": True, "mode": "prompt"}})

    with pytest.raises(ToolRuntimeError) as excinfo:
        policy.ensure_dangerous_allowed(
            dangerous=True,
            pattern_id="rm_rf",
            reason="matched",
            confirm=False,
        )

    assert excinfo.value.code == "CONFIRM_REQUIRED"


def test_dangerous_policy_deny_blocks():
    policy = Policy(raw={"dangerous": {"enabled": True, "mode": "deny"}})

    with pytest.raises(ToolRuntimeError) as excinfo:
        policy.ensure_dangerous_allowed(
            dangerous=True,
            pattern_id="rm_rf",
            reason="matched",
            confirm=True,
        )

    assert excinfo.value.code == "POLICY_DENIED"


def test_dangerous_policy_allow_skips():
    policy = Policy(raw={"dangerous": {"enabled": True, "mode": "allow"}})

    policy.ensure_dangerous_allowed(
        dangerous=True,
        pattern_id="rm_rf",
        reason="matched",
        confirm=False,
    )
