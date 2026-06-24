from __future__ import annotations

import pytest

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.runtime.policy import Policy


def _policy_with_exec(exec_block):
    return Policy(
        raw={
            "commands": {"mode": "allowlist", "allow": ["ls", "git"]},
            "exec": exec_block,
        }
    )


def test_exec_allowlist_requires_confirm_on_miss(tmp_path):
    policy = _policy_with_exec(
        {"security": "allowlist", "ask": "on-miss", "allowlist": ["git"]}
    )

    with pytest.raises(ToolRuntimeError) as excinfo:
        policy.ensure_exec_allowed(
            argv=["ls", "-la"], workspace=tmp_path, confirm=False
        )

    assert excinfo.value.code == "CONFIRM_REQUIRED"


def test_exec_allowlist_allows_when_confirmed(tmp_path):
    policy = _policy_with_exec(
        {"security": "allowlist", "ask": "on-miss", "allowlist": ["git"]}
    )

    policy.ensure_exec_allowed(argv=["ls", "-la"], workspace=tmp_path, confirm=True)


def test_exec_allowlist_allows_allowlisted(tmp_path):
    policy = _policy_with_exec(
        {"security": "allowlist", "ask": "on-miss", "allowlist": ["git"]}
    )

    policy.ensure_exec_allowed(
        argv=["git", "status"], workspace=tmp_path, confirm=False
    )


def test_exec_security_deny_blocks(tmp_path):
    policy = _policy_with_exec({"security": "deny"})

    with pytest.raises(ToolRuntimeError) as excinfo:
        policy.ensure_exec_allowed(argv=["ls", "-la"], workspace=tmp_path, confirm=True)

    assert excinfo.value.code == "POLICY_DENIED"


def test_exec_ask_always_requires_confirm(tmp_path):
    policy = _policy_with_exec({"security": "full", "ask": "always"})

    with pytest.raises(ToolRuntimeError) as excinfo:
        policy.ensure_exec_allowed(
            argv=["ls", "-la"], workspace=tmp_path, confirm=False
        )

    assert excinfo.value.code == "CONFIRM_REQUIRED"
