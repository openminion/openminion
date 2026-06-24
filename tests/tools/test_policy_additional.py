from __future__ import annotations

import pytest

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.runtime.policy import Policy


def test_plugin_allow_and_deny_behavior():
    policy = Policy(
        raw={
            "plugins": {
                "allow": ["openminion_tool", "fs"],
                "deny": ["custom_plugin"],
            }
        }
    )

    assert policy.is_plugin_enabled("openminion_tool") is True
    assert policy.is_plugin_enabled("custom_plugin") is False
    # Not in allow list when allow list is non-empty
    assert policy.is_plugin_enabled("unknown") is False


def test_env_filter_allows_configured_keys(monkeypatch):
    monkeypatch.delenv("SYS_ONLY", raising=False)
    monkeypatch.setenv("SYS_ONLY", "system-value")

    policy = Policy(
        raw={
            "env": {
                "allow_keys": ["SYS_ONLY", "USER_OK"],
                "deny_keys_regex": ["SECRET"],
            }
        }
    )

    raw_env = {
        "USER_OK": "user-value",
        "API_SECRET": "should-be-redacted",
        "OUT_OF_SCOPE": "ignored",
    }

    filtered = policy.filter_env(raw_env)

    assert filtered["SYS_ONLY"] == "system-value"
    assert filtered["USER_OK"] == "user-value"
    assert "API_SECRET" not in filtered
    assert "OUT_OF_SCOPE" not in filtered


def test_effective_scope_invalid_requested_value():
    policy = Policy(raw={"scope": "WRITE_SAFE"})

    with pytest.raises(ToolRuntimeError) as excinfo:
        policy.effective_scope("NOT_A_SCOPE")

    assert excinfo.value.code == "INVALID_ARGUMENT"


def test_command_mode_invalid_value_raises_error():
    policy = Policy(
        raw={
            "commands": {
                "mode": "invalid-mode",
                "allow": ["git"],
            }
        }
    )

    with pytest.raises(ToolRuntimeError) as excinfo:
        policy.ensure_command_allowed(["git", "status"])

    assert excinfo.value.code == "INVALID_ARGUMENT"


def test_default_policy_allows_git_and_plan_runtime_prefixes():
    policy = Policy(raw={})

    policy.ensure_tool_allowed("git.status")
    policy.ensure_tool_allowed("plan.list")
