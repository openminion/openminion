from __future__ import annotations

import pytest

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.runtime.policy import Policy


@pytest.fixture
def policy(workspace_fixture):
    _workspace, policy_path = workspace_fixture
    return Policy.load(policy_path)


def test_required_tool_needs_confirm(policy):
    with pytest.raises(ToolRuntimeError) as excinfo:
        policy.ensure_confirm_if_required(
            tool_name="file.delete",
            args={"path": "file.txt"},
            confirm=False,
            dangerous_default=False,
        )

    assert excinfo.value.code == "CONFIRM_REQUIRED"

    # Passing confirm should allow it
    policy.ensure_confirm_if_required(
        tool_name="file.delete",
        args={"path": "file.txt"},
        confirm=True,
        dangerous_default=False,
    )


def test_recursive_delete_requires_confirm(policy):
    with pytest.raises(ToolRuntimeError):
        policy.ensure_confirm_if_required(
            tool_name="file.delete",
            args={"path": "folder", "recursive": True},
            confirm=False,
            dangerous_default=False,
        )


def test_cmd_run_with_sudo_requires_confirm(policy):
    with pytest.raises(ToolRuntimeError):
        policy.ensure_confirm_if_required(
            tool_name="cmd.run",
            args={"argv": ["sudo", "apt", "install"]},
            confirm=False,
            dangerous_default=False,
        )

    # Once confirm flag is true, it should pass.
    policy.ensure_confirm_if_required(
        tool_name="cmd.run",
        args={"argv": ["sudo", "apt", "install"]},
        confirm=True,
        dangerous_default=False,
    )


def test_copy_overwrite_requires_confirm(policy):
    with pytest.raises(ToolRuntimeError) as excinfo:
        policy.ensure_confirm_if_required(
            tool_name="file.copy",
            args={"src": "a.txt", "dst": "b.txt", "overwrite": True},
            confirm=False,
            dangerous_default=False,
        )

    assert excinfo.value.code == "CONFIRM_REQUIRED"

    policy.ensure_confirm_if_required(
        tool_name="file.copy",
        args={"src": "a.txt", "dst": "b.txt", "overwrite": True},
        confirm=True,
        dangerous_default=False,
    )


def test_move_overwrite_requires_confirm(policy):
    with pytest.raises(ToolRuntimeError) as excinfo:
        policy.ensure_confirm_if_required(
            tool_name="file.move",
            args={"src": "a.txt", "dst": "b.txt", "overwrite": True},
            confirm=False,
            dangerous_default=False,
        )

    assert excinfo.value.code == "CONFIRM_REQUIRED"

    policy.ensure_confirm_if_required(
        tool_name="file.move",
        args={"src": "a.txt", "dst": "b.txt", "overwrite": True},
        confirm=True,
        dangerous_default=False,
    )


def test_move_without_overwrite_does_not_require_confirm(policy):
    policy.ensure_confirm_if_required(
        tool_name="file.move",
        args={"src": "a.txt", "dst": "b.txt", "overwrite": False},
        confirm=False,
        dangerous_default=False,
    )
