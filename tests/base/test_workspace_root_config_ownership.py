from __future__ import annotations

import copy

from openminion.base.config.parser import openminion_config_from_dict
from openminion.modules.tool.runtime.policy import DEFAULT_POLICY
from openminion.modules.tool.runtime.registry_toolspec import resolve_workspace
from openminion.services.runtime.ingress import apply_workspace_root


_BASE_FIXTURE: dict[str, object] = {
    "agents": {
        "hello": {
            "name": "hello",
            "provider": "echo",
            "default_channel": "console",
        }
    },
    "default_agent": "hello",
    "enabled_channels": ["console"],
    "providers": {"echo": {"model": "echo-test"}},
}


def _config_with_runtime(runtime_overrides: dict[str, object]) -> dict[str, object]:
    payload = copy.deepcopy(_BASE_FIXTURE)
    payload["runtime"] = runtime_overrides
    return payload


def test_parser_preserves_workspace_root_at_named_json_location() -> None:
    configured_path = "/tmp/example/workspace_root"
    config = openminion_config_from_dict(
        _config_with_runtime(
            {
                "log_level": "INFO",
                "tool_workspace_root": configured_path,
            }
        )
    )
    assert config.runtime.tool_workspace_root == configured_path, (
        "parser must preserve runtime.tool_workspace_root at OpenMinionConfig"
        f".runtime.tool_workspace_root; got {config.runtime.tool_workspace_root!r}"
    )


def test_workspace_root_config_value_reaches_policy_raw_top_level() -> None:
    from pathlib import Path

    configured_path = "/tmp/example/workspace_root"
    config = openminion_config_from_dict(
        _config_with_runtime({"tool_workspace_root": configured_path})
    )

    # Step 1: config field populates APIRuntime.tool_workspace_root analog
    runtime_workspace_root = (
        Path(config.runtime.tool_workspace_root).expanduser()
        if config.runtime.tool_workspace_root
        else None
    )
    assert runtime_workspace_root is not None

    # Step 2: ingress lifts into inbound_metadata
    metadata = apply_workspace_root(
        inbound_metadata=None,
        runtime_workspace_root=runtime_workspace_root,
    )
    assert metadata is not None
    assert metadata.get("workspace_root") == str(runtime_workspace_root), (
        "ingress must write workspace_root at top-level of inbound_metadata; "
        f"got {metadata!r}"
    )

    # Step 3: resolve_workspace reads from metadata
    from types import SimpleNamespace

    ctx = SimpleNamespace(metadata=dict(metadata))
    workspace = resolve_workspace(context=ctx)
    assert str(workspace) == str(runtime_workspace_root.resolve()), (
        "resolve_workspace must round-trip the configured value"
    )

    # Step 4: policy_payload top-level assignment (registry_toolspec.py:117 pattern)
    policy_payload = copy.deepcopy(DEFAULT_POLICY)
    policy_payload["workspace_root"] = str(workspace)
    assert policy_payload["workspace_root"] == str(runtime_workspace_root.resolve()), (
        "policy_payload['workspace_root'] (the top-level key the resolver reads) "
        "must receive the configured value"
    )


def test_resolve_workspace_honors_config_over_cwd_fallback() -> None:
    from pathlib import Path
    from types import SimpleNamespace

    configured_path = "/tmp/example/workspace_root"
    config = openminion_config_from_dict(
        _config_with_runtime({"tool_workspace_root": configured_path})
    )
    runtime_workspace_root = Path(config.runtime.tool_workspace_root).expanduser()
    metadata = apply_workspace_root(
        inbound_metadata=None, runtime_workspace_root=runtime_workspace_root
    )
    ctx = SimpleNamespace(metadata=dict(metadata or {}))
    workspace = resolve_workspace(context=ctx)
    # Configured value wins over the cwd fallback
    assert str(workspace) == str(runtime_workspace_root.resolve())
    import os

    assert str(workspace) != str(Path(os.getcwd()).resolve()), (
        "resolved workspace must be the configured value, not cwd"
    )


def test_unset_workspace_root_falls_back_to_cwd_via_resolve_workspace() -> None:
    from pathlib import Path
    from types import SimpleNamespace
    import os

    config = openminion_config_from_dict(_config_with_runtime({"log_level": "INFO"}))
    assert config.runtime.tool_workspace_root == "", (
        "unset field must default to empty string per RuntimeConfig default"
    )
    runtime_workspace_root = (
        Path(config.runtime.tool_workspace_root).expanduser()
        if config.runtime.tool_workspace_root
        else None
    )
    assert runtime_workspace_root is None, (
        "empty string must translate to None (no override)"
    )

    metadata = apply_workspace_root(
        inbound_metadata=None, runtime_workspace_root=runtime_workspace_root
    )
    assert metadata is None, (
        "when tool_workspace_root is None, ingress must NOT write to inbound_metadata"
    )

    # Falls back to cwd
    ctx = SimpleNamespace(metadata={})
    workspace = resolve_workspace(context=ctx)
    assert str(workspace) == str(Path(os.getcwd()).resolve()), (
        "unset field → resolve_workspace falls back to cwd (pre-WRCO behavior preserved)"
    )

    # DEFAULT_POLICY's workspace_root value remains as the typed default for the field
    assert DEFAULT_POLICY["workspace_root"] == "~/openminion_tool_runs", (
        "DEFAULT_POLICY default value preserved unchanged by WRCO"
    )


def test_resolve_workspace_honors_working_dir_metadata_fallback() -> None:
    from pathlib import Path
    from types import SimpleNamespace

    working_dir = "/tmp/example/focus-working-dir"

    ctx = SimpleNamespace(metadata={"working_dir": working_dir})
    workspace = resolve_workspace(context=ctx)

    assert str(workspace) == str(Path(working_dir).resolve())
