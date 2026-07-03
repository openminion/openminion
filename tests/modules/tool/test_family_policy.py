from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from openminion.modules.tool.runtime.policy import Policy
from openminion.modules.tool.runtime import RuntimeContext
from openminion.modules.tool.family.policy import (
    get_family_tool_config,
    is_tool_disabled_by_policy,
)


def _make_ctx(tmp_path: Path, *, tools_cfg: dict | None = None) -> RuntimeContext:
    workspace = tmp_path / "workspace"
    run_root = tmp_path / "run"
    workspace.mkdir(parents=True, exist_ok=True)
    run_root.mkdir(parents=True, exist_ok=True)
    raw: dict = {
        "workspace_root": str(workspace),
        "paths": {
            "read_allow": [str(workspace)],
            "write_allow": [str(workspace)],
            "deny": [],
        },
        "commands": {"mode": "allowlist", "allow": []},
    }
    if tools_cfg is not None:
        raw["tools"] = tools_cfg
    return RuntimeContext(
        policy=Policy(raw=raw),
        workspace=workspace,
        run_root=run_root,
        scope="READ_ONLY",
        confirm=False,
    )


def test_get_family_tool_config_returns_family_dict(tmp_path: Path) -> None:
    ctx = _make_ctx(
        tmp_path, tools_cfg={"fetch": {"enabled": False, "timeout_ms": 5000}}
    )
    result = get_family_tool_config(ctx, "fetch")
    assert result == {"enabled": False, "timeout_ms": 5000}


def test_get_family_tool_config_returns_empty_for_missing_family(
    tmp_path: Path,
) -> None:
    ctx = _make_ctx(tmp_path, tools_cfg={"fetch": {"enabled": True}})
    result = get_family_tool_config(ctx, "search")
    assert result == {}


def test_get_family_tool_config_returns_empty_when_no_tools_key(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    result = get_family_tool_config(ctx, "fetch")
    assert result == {}


def test_get_family_tool_config_returns_empty_for_non_runtime_context() -> None:
    ctx = SimpleNamespace(
        policy=SimpleNamespace(raw={"tools": {"fetch": {"enabled": False}}})
    )
    result = get_family_tool_config(ctx, "fetch")
    assert result == {}


def test_get_family_tool_config_returns_empty_for_none_context() -> None:
    result = get_family_tool_config(None, "fetch")
    assert result == {}


def test_is_tool_disabled_when_enabled_false(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path, tools_cfg={"fetch": {"enabled": False}})
    assert is_tool_disabled_by_policy(ctx, "fetch") is True


def test_is_tool_enabled_when_enabled_true(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path, tools_cfg={"fetch": {"enabled": True}})
    assert is_tool_disabled_by_policy(ctx, "fetch") is False


def test_is_tool_enabled_when_enabled_missing(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path, tools_cfg={"fetch": {"timeout_ms": 3000}})
    assert is_tool_disabled_by_policy(ctx, "fetch") is False


def test_is_tool_enabled_when_family_missing(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    assert is_tool_disabled_by_policy(ctx, "fetch") is False


def test_is_tool_disabled_returns_false_for_non_runtime_context() -> None:
    ctx = SimpleNamespace()
    assert is_tool_disabled_by_policy(ctx, "fetch") is False
