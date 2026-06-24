from __future__ import annotations

import pytest

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.runtime.policy import Policy, canonical_tool_name


@pytest.fixture
def policy_allowlist(workspace_fixture):
    _workspace_dir, policy_path = workspace_fixture
    policy = Policy.load(policy_path)
    return policy


@pytest.fixture
def policy_blocklist(tmp_path):
    policy_raw = tmp_path / "policy_block.yaml"
    policy_raw.write_text(
        """
version: 1
commands:
  mode: blocklist
  deny_exact:
    - rm
    - dd
    - mkfs
"""
    )
    return Policy.load(policy_raw)


def test_allowlist_permits_allowed_command(policy_allowlist):
    exec_name = policy_allowlist.ensure_command_allowed(["git", "status"])
    assert exec_name == "git"


def test_allowlist_denies_missing_command(policy_allowlist):
    with pytest.raises(ToolRuntimeError) as excinfo:
        policy_allowlist.ensure_command_allowed(["curl", "https://example.com"])

    assert excinfo.value.code == "POLICY_DENIED"


def test_blocklist_denies_blocked_command(policy_blocklist):
    with pytest.raises(ToolRuntimeError) as excinfo:
        policy_blocklist.ensure_command_allowed(["rm", "-rf", "/tmp"])

    assert excinfo.value.code == "POLICY_DENIED"


def test_blocklist_allows_other_command(policy_blocklist):
    exec_name = policy_blocklist.ensure_command_allowed(["ls", "-la"])
    assert exec_name == "ls"


def test_blocklist_mode_ignores_allow_list_as_blocklist():
    policy = Policy(
        raw={
            "commands": {
                "mode": "blocklist",
                "allow": ["ls"],  # ignored in blocklist mode
            }
        }
    )
    exec_name = policy.ensure_command_allowed(["ls", "-la"])
    assert exec_name == "ls"


def test_deny_regex_triggers(policy_allowlist):
    with pytest.raises(ToolRuntimeError) as excinfo:
        policy_allowlist.ensure_command_allowed(["sudo", "shutdown", "now"])

    assert excinfo.value.code == "POLICY_DENIED"


def test_default_policy_allows_time_tools(tmp_path):
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text("version: 1\n", encoding="utf-8")
    policy = Policy.load(policy_path)
    policy.ensure_tool_allowed("time.now")
    policy.ensure_tool_allowed("location.get")


def test_default_policy_allows_legacy_runtime_aliases(tmp_path):
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text("version: 1\n", encoding="utf-8")
    policy = Policy.load(policy_path)
    for alias in (
        "file.list_dir",
        "file.read",
        "file.write",
        "file.find",
        "web.search",
        "location",
    ):
        policy.ensure_tool_allowed(canonical_tool_name(alias))


def test_default_policy_allows_skill_model_tool(tmp_path):
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text("version: 1\n", encoding="utf-8")
    policy = Policy.load(policy_path)
    policy.ensure_tool_allowed("skill.list")
