from __future__ import annotations


import pytest

from openminion.modules.tool.runtime.policy import Policy
from openminion.modules.tool.errors import ToolRuntimeError


@pytest.fixture
def policy_with_workspace(workspace_fixture):
    workspace_dir, policy_path = workspace_fixture
    policy = Policy.load(policy_path)
    return policy, workspace_dir


def test_read_allowed_within_workspace(policy_with_workspace):
    policy, workspace_dir = policy_with_workspace
    file_path = workspace_dir / "notes.txt"
    file_path.write_text("ok")

    resolved = policy.ensure_path_allowed(str(file_path), workspace_dir, "read")

    assert resolved == file_path.resolve()


def test_read_denied_root(policy_with_workspace):
    policy, workspace_dir = policy_with_workspace

    with pytest.raises(ToolRuntimeError) as excinfo:
        policy.ensure_path_allowed("/etc/hosts", workspace_dir, "read")

    assert excinfo.value.code == "POLICY_DENIED"


def test_write_outside_workspace_denied(policy_with_workspace):
    policy, workspace_dir = policy_with_workspace
    outside_path = workspace_dir.parent / "outside.txt"

    with pytest.raises(ToolRuntimeError) as excinfo:
        policy.ensure_path_allowed(str(outside_path), workspace_dir, "write")

    assert excinfo.value.code == "POLICY_DENIED"


def test_symlink_escape_denied(policy_with_workspace, tmp_path):
    policy, workspace_dir = policy_with_workspace

    escape_root = tmp_path / "escape"
    escape_root.mkdir()
    target = escape_root / "secret.txt"
    target.write_text("secret")

    symlink_path = workspace_dir / "link-out"
    if symlink_path.exists() or symlink_path.is_symlink():
        symlink_path.unlink()
    symlink_path.symlink_to(target)

    with pytest.raises(ToolRuntimeError) as excinfo:
        policy.ensure_path_allowed(str(symlink_path), workspace_dir, "read")

    assert excinfo.value.code == "POLICY_DENIED"
    assert "symlink_escape" in str(excinfo.value.details)


def test_allow_root_alias_path_is_accepted(tmp_path):
    real_root = tmp_path / "real-root"
    real_root.mkdir()
    alias_root = tmp_path / "alias-root"
    try:
        alias_root.symlink_to(real_root, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation not available in this environment")

    target = real_root / "notes.txt"
    target.write_text("ok")

    policy = Policy(
        raw={
            "paths": {
                "read_allow": [str(alias_root)],
                "write_allow": [str(alias_root)],
                "deny": [],
            }
        }
    )

    resolved = policy.ensure_path_allowed(
        str(alias_root / "notes.txt"), alias_root, "read"
    )

    assert resolved == target.resolve()
