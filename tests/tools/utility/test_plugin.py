from __future__ import annotations

from pathlib import Path

import pytest

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.runtime.policy import Policy
from openminion.modules.tool.registry import ToolRegistry
from openminion.modules.tool.runtime import RuntimeContext
from openminion.tools.utility.plugin import (
    _h_calculate_expression,
    _h_text_stats,
    _h_utc_now,
    register,
)


def _ctx(tmp_path: Path) -> RuntimeContext:
    run_root = tmp_path / "run"
    run_root.mkdir(parents=True, exist_ok=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    policy = Policy(
        raw={
            "workspace_root": str(workspace),
            "paths": {
                "read_allow": [str(workspace)],
                "write_allow": [str(workspace)],
                "deny": [],
            },
            "commands": {"mode": "allowlist", "allow": ["echo"]},
            "tools": {"allow_prefix": [""]},
        }
    )
    return RuntimeContext(
        policy=policy,
        workspace=workspace,
        run_root=run_root,
        scope="READ_ONLY",
        confirm=False,
    )


def test_register_adds_utility_tools():
    registry = ToolRegistry()
    register(registry)
    names = set(registry.list().keys())
    assert "utility.utc_now" in names
    assert "utility.calculate_expression" in names
    assert "utility.text_stats" in names


def test_utc_now_supports_iso_and_epoch(tmp_path: Path):
    ctx = _ctx(tmp_path)
    iso_payload = _h_utc_now({"format": "iso"}, ctx)
    epoch_payload = _h_utc_now({"format": "epoch"}, ctx)
    assert "iso" in iso_payload
    assert iso_payload["timezone"] == "UTC"
    assert isinstance(epoch_payload["epoch_seconds"], int)
    assert epoch_payload["timezone"] == "UTC"


def test_calculate_expression_returns_result(tmp_path: Path):
    ctx = _ctx(tmp_path)
    payload = _h_calculate_expression({"expression": "1 + 2 * 3"}, ctx)
    assert payload["result"] == 7


def test_calculate_expression_rejects_divide_by_zero(tmp_path: Path):
    ctx = _ctx(tmp_path)
    with pytest.raises(ToolRuntimeError):
        _h_calculate_expression({"expression": "10 / 0"}, ctx)


def test_text_stats_counts(tmp_path: Path):
    ctx = _ctx(tmp_path)
    payload = _h_text_stats({"text": "One line.\nTwo words"}, ctx)
    assert payload["line_count"] == 2
    assert payload["word_count"] == 4
    assert payload["sentence_count"] == 2
