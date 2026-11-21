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


def test_calculate_expression_rejects_zero_to_negative_power(tmp_path: Path):
    ctx = _ctx(tmp_path)
    with pytest.raises(ToolRuntimeError) as exc_info:
        _h_calculate_expression({"expression": "0 ** -1"}, ctx)
    assert exc_info.value.code == "INVALID_ARGUMENT"


def test_calculate_expression_rejects_complex_result(tmp_path: Path):
    ctx = _ctx(tmp_path)
    with pytest.raises(ToolRuntimeError) as exc_info:
        _h_calculate_expression({"expression": "(-1) ** 0.5"}, ctx)
    assert exc_info.value.code == "INVALID_ARGUMENT"


def test_calculate_expression_rejects_large_exponent(tmp_path: Path):
    ctx = _ctx(tmp_path)
    with pytest.raises(ToolRuntimeError):
        _h_calculate_expression({"expression": "2 ** 100"}, ctx)


def test_calculate_expression_allows_negative_power(tmp_path: Path):
    ctx = _ctx(tmp_path)
    payload = _h_calculate_expression({"expression": "2 ** -1"}, ctx)
    assert payload["result"] == 0.5


@pytest.mark.parametrize("expression", ["100000 ** 64", "(-100000) ** 63.5"])
def test_calculate_expression_rejects_overflowing_power_as_invalid_argument(
    tmp_path: Path, expression: str
) -> None:
    ctx = _ctx(tmp_path)
    with pytest.raises(ToolRuntimeError) as exc_info:
        _h_calculate_expression({"expression": expression}, ctx)
    assert exc_info.value.code == "INVALID_ARGUMENT"
    assert exc_info.value.message == "Expression result is out of bounds"


@pytest.mark.parametrize("expression", ["9" * 350, f"-({'9' * 350})"])
def test_calculate_expression_rejects_integer_literal_exceeding_float_range(
    tmp_path: Path, expression: str
) -> None:
    ctx = _ctx(tmp_path)
    with pytest.raises(ToolRuntimeError) as exc_info:
        _h_calculate_expression({"expression": expression}, ctx)
    assert exc_info.value.code == "INVALID_ARGUMENT"
    assert exc_info.value.message == "Expression result is out of bounds"


@pytest.mark.parametrize("expression", ["1e400", "-1e400", "+1e400"])
def test_calculate_expression_rejects_non_finite_float_literal(
    tmp_path: Path, expression: str
) -> None:
    ctx = _ctx(tmp_path)
    with pytest.raises(ToolRuntimeError) as exc_info:
        _h_calculate_expression({"expression": expression}, ctx)
    assert exc_info.value.code == "INVALID_ARGUMENT"
    assert exc_info.value.message == "Expression result is not finite"


@pytest.mark.parametrize("expression", ["1000000000001", "-1000000000001"])
def test_calculate_expression_rejects_out_of_bounds_literal_and_unary(
    tmp_path: Path, expression: str
) -> None:
    ctx = _ctx(tmp_path)
    with pytest.raises(ToolRuntimeError) as exc_info:
        _h_calculate_expression({"expression": expression}, ctx)
    assert exc_info.value.code == "INVALID_ARGUMENT"
    assert exc_info.value.message == "Expression result is out of bounds"


def test_calculate_expression_preserves_exact_bounds_and_scaled_operands(
    tmp_path: Path,
) -> None:
    ctx = _ctx(tmp_path)
    assert (
        _h_calculate_expression({"expression": "1000000000000"}, ctx)["result"]
        == 10**12
    )
    assert _h_calculate_expression({"expression": "-1000000000000"}, ctx)[
        "result"
    ] == -(10**12)
    assert (
        _h_calculate_expression({"expression": "10000000000000 / 100"}, ctx)["result"]
        == 10**11
    )


def test_calculate_expression_registry_dispatch_returns_structured_invalid_argument(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openminion.modules.tool.base import ToolExecutionContext
    from openminion.modules.tool.runtime.registry_toolspec import execute_tool_spec_call

    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / ".openminion"))

    registry = ToolRegistry()
    register(registry)
    spec = registry.list()["utility.calculate_expression"]
    exec_ctx = ToolExecutionContext(
        channel="console",
        target="test-user",
        session_id="test-session",
        metadata={"workspace_root": str(tmp_path)},
    )

    for bad_expr in ("100000 ** 64", "9" * 350, "1e400", "-1000000000001"):
        failed = execute_tool_spec_call(
            tool=spec,
            arguments={"expression": bad_expr},
            context=exec_ctx,
        )
        assert failed.ok is False
        assert failed.data.get("error_code") == "INVALID_ARGUMENT"

    succeeded = execute_tool_spec_call(
        tool=spec,
        arguments={"expression": "-1000000000000"},
        context=exec_ctx,
    )
    assert succeeded.ok is True
    assert succeeded.data["result"] == -(10**12)


def test_calculate_expression_brain_run_tool_spec_propagates_tool_runtime_error(
    tmp_path: Path,
) -> None:
    import time
    from openminion.modules.brain.adapters.tool.results import run_tool_spec

    registry = ToolRegistry()
    register(registry)
    spec = registry.list()["utility.calculate_expression"]
    ctx = _ctx(tmp_path)

    for bad_expr in ("100000 ** 64", "9" * 350, "1e400", "-1000000000001"):
        with pytest.raises(ToolRuntimeError) as exc_info:
            run_tool_spec(
                spec=spec,
                validated_args={"expression": bad_expr},
                context=ctx,
                start_time=time.monotonic(),
                background_write_authorized=False,
                tool_name=spec.name,
            )
        assert exc_info.value.code == "INVALID_ARGUMENT"

    ok_envelope = run_tool_spec(
        spec=spec,
        validated_args={"expression": "1000000000000"},
        context=ctx,
        start_time=time.monotonic(),
        background_write_authorized=False,
        tool_name=spec.name,
    )
    assert ok_envelope["status"] == "success"
    assert ok_envelope["outputs"]["result"] == 10**12


def test_text_stats_counts(tmp_path: Path):
    ctx = _ctx(tmp_path)
    payload = _h_text_stats({"text": "One line.\nTwo words"}, ctx)
    assert payload["line_count"] == 2
    assert payload["word_count"] == 4
    assert payload["sentence_count"] == 2
