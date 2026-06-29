from __future__ import annotations

from pathlib import Path
from typing import Any

from openminion.modules.tool.registry import ToolRegistry
from openminion.modules.tool.runtime import RuntimeContext
from openminion.modules.tool.runtime.policy import Policy
from openminion.tools.host.plugin import _h_metrics, register


def _ctx(tmp_path: Path) -> RuntimeContext:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return RuntimeContext(
        policy=Policy(
            raw={
                "workspace_root": str(workspace),
                "paths": {
                    "read_allow": [str(workspace)],
                    "write_allow": [str(workspace)],
                    "deny": [],
                },
                "commands": {"mode": "allowlist", "allow": []},
                "tools": {"allow_prefix": [""]},
            }
        ),
        workspace=workspace,
        run_root=tmp_path / "run",
        scope="READ_ONLY",
        confirm=False,
    )


def test_register_adds_host_metrics_tool() -> None:
    registry = ToolRegistry()
    register(registry)
    assert "host.metrics" in registry.list()


def test_h_metrics_returns_platform_disk_and_memory(tmp_path: Path) -> None:
    payload = _h_metrics({}, _ctx(tmp_path))
    assert payload["ok"] is True
    assert payload["verified"] is True
    assert payload["data"]["method"] == "host.metrics"
    assert payload["data"]["platform"]["system"]
    assert payload["data"]["disk"]
    assert "Memory:" in payload["content"]


def test_h_metrics_accepts_relative_disk_path(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    workspace = Path(ctx.workspace)
    nested = workspace / "nested"
    nested.mkdir()

    payload = _h_metrics(
        {"path": "nested", "include_memory": False},
        ctx,
    )

    assert payload["ok"] is True
    paths = {item["path"] for item in payload["data"]["disk"]}
    assert str(nested) in paths
    assert "Memory:" not in payload["content"]


def test_h_metrics_content_formats_unknown_memory(monkeypatch, tmp_path: Path) -> None:
    def fake_memory() -> dict[str, Any]:
        return {
            "total_bytes": None,
            "available_bytes": None,
            "used_bytes": None,
            "used_percent": None,
            "source": "test",
        }

    monkeypatch.setattr("openminion.tools.host.plugin._memory_metrics", fake_memory)
    payload = _h_metrics({"include_disk": False}, _ctx(tmp_path))
    assert payload["ok"] is True
    assert "Memory: unknown used / unknown total" in payload["content"]
