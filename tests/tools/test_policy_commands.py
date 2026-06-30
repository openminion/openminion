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
    policy.ensure_tool_allowed("host.metrics")


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


@pytest.mark.parametrize(
    "argv",
    [
        ["command", "-v", "nasm"],
        ["which", "clang"],
        ["nasm", "--version"],
        ["uname", "-m"],
        ["uname", "-s"],
        ["sw_vers"],
        ["sysctl", "-n", "hw.machine"],
    ],
)
def test_default_policy_allows_exact_discovery_patterns(tmp_path, argv):
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text("version: 1\n", encoding="utf-8")
    policy = Policy.load(policy_path)

    assert policy.ensure_command_allowed(argv) == argv[0]


@pytest.mark.parametrize(
    "argv",
    [
        ["clang", "-v"],
        ["nasm", "-f", "macho64", "ping.asm"],
        ["clang", "ping.s", "-o", "ping"],
        ["pip", "install", "nasm"],
        ["npm", "install", "left-pad"],
        ["./ping"],
        ["command", "-v", "unknown-local-tool"],
        ["which", "unknown-local-tool"],
    ],
)
def test_default_policy_denies_non_discovery_toolchain_shapes(tmp_path, argv):
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text("version: 1\n", encoding="utf-8")
    policy = Policy.load(policy_path)

    with pytest.raises(ToolRuntimeError) as excinfo:
        policy.ensure_command_allowed(argv)

    assert excinfo.value.code == "POLICY_DENIED"
    assert excinfo.value.details["rule"] in {"commands.allow", "commands.install"}
    assert excinfo.value.details["action_class"] in {
        "compile",
        "discovery",
        "install",
        "run",
        "unknown",
    }


def test_default_policy_deny_rules_win_before_allow_patterns(tmp_path):
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        """
version: 1
commands:
  deny_regex:
    - ".*nasm.*"
""",
        encoding="utf-8",
    )
    policy = Policy.load(policy_path)

    with pytest.raises(ToolRuntimeError) as excinfo:
        policy.ensure_command_allowed(["command", "-v", "nasm"])

    assert excinfo.value.code == "POLICY_DENIED"
    assert excinfo.value.details["rule"] == "commands.deny_regex"


def test_exec_approval_honors_command_discovery_allow_pattern(tmp_path):
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text("version: 1\n", encoding="utf-8")
    policy = Policy.load(policy_path)

    assert (
        policy.ensure_exec_allowed(
            argv=["command", "-v", "nasm"],
            workspace=tmp_path,
            confirm=False,
        )
        == "command"
    )


def test_exec_approval_still_gates_compile_and_run_shapes(tmp_path):
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text("version: 1\n", encoding="utf-8")
    policy = Policy.load(policy_path)

    for argv in (["clang", "ping.s", "-o", "ping"], ["./ping"]):
        with pytest.raises(ToolRuntimeError) as excinfo:
            policy.ensure_exec_allowed(argv=argv, workspace=tmp_path, confirm=False)

        assert excinfo.value.code == "CONFIRM_REQUIRED"
        assert excinfo.value.details["rule"] == "exec.ask.on_miss"
