from openminion.base.runtime.sandbox import ExecutionSandboxSpec


def test_build_defaults_workspace_root():
    spec = ExecutionSandboxSpec.build(workspace_root="/workspace")
    assert spec.workspace_root == "/workspace"
    assert "/workspace" in spec.read_allow
    assert "/workspace" in spec.write_allow
    assert spec.net_mode == "deny"
    assert spec.timeout_s == 30.0
    assert spec.max_output_bytes == 1_048_576


def test_build_applies_tool_caps():
    spec = ExecutionSandboxSpec.build(
        workspace_root="/ws",
        tool_caps={
            "cmd_allowlist": ["git", "ls"],
            "timeout_s": 60.0,
            "max_output_bytes": 512,
            "address_space_bytes": 1024,
            "cpu_seconds": 5.0,
            "session_mode": "foreground",
            "net_mode": "allow",
            "allowed_domains": ["example.com"],
            "env_allowlist": ["HOME", "PATH"],
        },
    )
    assert set(spec.cmd_allowlist) == {"git", "ls"}
    assert spec.timeout_s == 60.0
    assert spec.max_output_bytes == 512
    assert spec.address_space_bytes == 1024
    assert spec.cpu_seconds == 5.0
    assert spec.session_mode == "foreground"
    assert spec.net_mode == "allow"
    assert "example.com" in spec.allowed_domains
    assert set(spec.env_allowlist) == {"HOME", "PATH"}


def test_policy_narrows_cmd_allowlist():
    spec = ExecutionSandboxSpec.build(
        workspace_root="/ws",
        tool_caps={"cmd_allowlist": ["git", "ls", "cat"]},
        policy_constraints={"cmd_allowlist": ["git", "ls"]},
    )
    assert "git" in spec.cmd_allowlist
    assert "ls" in spec.cmd_allowlist
    assert "cat" not in spec.cmd_allowlist


def test_policy_timeout_is_min():
    spec = ExecutionSandboxSpec.build(
        workspace_root="/ws",
        tool_caps={"timeout_s": 60.0},
        policy_constraints={"timeout_s": 10.0},
    )
    assert spec.timeout_s == 10.0


def test_policy_cannot_expand_timeout():
    spec = ExecutionSandboxSpec.build(
        workspace_root="/ws",
        tool_caps={"timeout_s": 5.0},
        policy_constraints={"timeout_s": 120.0},
    )
    assert spec.timeout_s == 5.0


def test_policy_max_output_bytes_is_min():
    spec = ExecutionSandboxSpec.build(
        workspace_root="/ws",
        tool_caps={"max_output_bytes": 10_000},
        policy_constraints={"max_output_bytes": 1_000},
    )
    assert spec.max_output_bytes == 1_000


def test_policy_address_space_bytes_is_min():
    spec = ExecutionSandboxSpec.build(
        workspace_root="/ws",
        tool_caps={"address_space_bytes": 10_000},
        policy_constraints={"address_space_bytes": 1_000},
    )
    assert spec.address_space_bytes == 1_000


def test_policy_cannot_expand_address_space_bytes():
    spec = ExecutionSandboxSpec.build(
        workspace_root="/ws",
        tool_caps={"address_space_bytes": 1_000},
        policy_constraints={"address_space_bytes": 10_000},
    )
    assert spec.address_space_bytes == 1_000


def test_policy_cpu_seconds_is_min():
    spec = ExecutionSandboxSpec.build(
        workspace_root="/ws",
        tool_caps={"cpu_seconds": 10.0},
        policy_constraints={"cpu_seconds": 2.5},
    )
    assert spec.cpu_seconds == 2.5


def test_policy_cannot_expand_cpu_seconds():
    spec = ExecutionSandboxSpec.build(
        workspace_root="/ws",
        tool_caps={"cpu_seconds": 2.5},
        policy_constraints={"cpu_seconds": 10.0},
    )
    assert spec.cpu_seconds == 2.5


def test_policy_session_mode_can_narrow_or_set_mode():
    spec = ExecutionSandboxSpec.build(
        workspace_root="/ws",
        tool_caps={"session_mode": "background"},
        policy_constraints={"session_mode": "foreground"},
    )
    assert spec.session_mode == "foreground"


def test_new_fields_default_to_none():
    spec = ExecutionSandboxSpec.build(workspace_root="/workspace")
    assert spec.address_space_bytes is None
    assert spec.cpu_seconds is None
    assert spec.session_mode is None


def test_policy_deny_overrides_allow_net_mode():
    spec = ExecutionSandboxSpec.build(
        workspace_root="/ws",
        tool_caps={"net_mode": "allow", "allowed_domains": ["example.com"]},
        policy_constraints={"net_mode": "deny"},
    )
    assert spec.net_mode == "deny"
    assert spec.allowed_domains == []


def test_tool_deny_cannot_be_expanded_by_policy():
    spec = ExecutionSandboxSpec.build(
        workspace_root="/ws",
        tool_caps={"net_mode": "deny"},
        policy_constraints={"net_mode": "allow", "allowed_domains": ["example.com"]},
    )
    assert spec.net_mode == "deny"
    assert spec.allowed_domains == []


def test_policy_narrows_write_allow():
    spec = ExecutionSandboxSpec.build(
        workspace_root="/ws",
        tool_caps={"write_allow": ["/ws/a", "/ws/b"]},
        policy_constraints={"write_allow": ["/ws/a"]},
    )
    assert "/ws/a" in spec.write_allow
    assert "/ws/b" not in spec.write_allow


def test_policy_narrows_read_allow():
    spec = ExecutionSandboxSpec.build(
        workspace_root="/ws",
        tool_caps={"read_allow": ["/ws", "/data"]},
        policy_constraints={"read_allow": ["/ws"]},
    )
    assert "/ws" in spec.read_allow
    assert "/data" not in spec.read_allow


def test_policy_narrows_delete_allow():
    spec = ExecutionSandboxSpec.build(
        workspace_root="/ws",
        tool_caps={"delete_allow": ["/ws/tmp", "/ws/cache"]},
        policy_constraints={"delete_allow": ["/ws/tmp"]},
    )
    assert "/ws/tmp" in spec.delete_allow
    assert "/ws/cache" not in spec.delete_allow


def test_policy_narrows_env_allowlist():
    spec = ExecutionSandboxSpec.build(
        workspace_root="/ws",
        tool_caps={"env_allowlist": ["HOME", "PATH", "TERM"]},
        policy_constraints={"env_allowlist": ["PATH"]},
    )
    assert "PATH" in spec.env_allowlist
    assert "HOME" not in spec.env_allowlist
    assert "TERM" not in spec.env_allowlist


def test_policy_narrows_allowed_domains():
    spec = ExecutionSandboxSpec.build(
        workspace_root="/ws",
        tool_caps={
            "net_mode": "allow",
            "allowed_domains": ["api.example.com", "cdn.example.com"],
        },
        policy_constraints={
            "net_mode": "allow",
            "allowed_domains": ["api.example.com"],
        },
    )
    assert "api.example.com" in spec.allowed_domains
    assert "cdn.example.com" not in spec.allowed_domains


def test_no_policy_constraints_keeps_tool_caps():
    spec = ExecutionSandboxSpec.build(
        workspace_root="/ws",
        tool_caps={"cmd_allowlist": ["git"]},
    )
    assert "git" in spec.cmd_allowlist


def test_idempotency_key_not_set_by_build():
    spec = ExecutionSandboxSpec.build(workspace_root="/ws")
    assert spec.idempotency_key is None
