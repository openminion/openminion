"""Trusted scan context follows the actual tool turn without leaking to another turn."""

import asyncio
from contextlib import nullcontext
import json
from threading import Barrier
from types import SimpleNamespace

import pytest

from openminion.modules.brain.adapters.tool.runtime import ToolAdapter
from openminion.modules.tool.registry import ToolRegistry, ToolSpec
from openminion.services.brain.post_execution.mixin import _bind_tool_workspace_root
from openminion.tools.security.trusted_source import run_context


@pytest.fixture
def adapter(tmp_path):
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="blockchain.debug",
            args_model=dict,
            min_scope="READ_ONLY",
            handler=lambda _args, ctx: {
                "ok": True,
                "content": json.dumps(run_context(ctx)),
            },
        )
    )
    tool = ToolAdapter(
        workspace_root=tmp_path,
        runtime_registry=registry,
        artifactctl=SimpleNamespace(),
    )
    try:
        yield tool
    finally:
        tool.close()


def _execute(adapter):
    result = adapter.execute(
        command={
            "tool_name": "blockchain.debug",
            "args": {},
            "inputs": {"permission_mode": "readonly"},
        },
        session_id="context-probe",
        trace_id="context-probe",
    )
    assert result["status"] == "success"
    return json.loads(result["summary"])


@pytest.mark.parametrize("fail", [False, True])
@pytest.mark.parametrize("inner", [None, "inner"])
def test_nested_scan_context_resets_after_success_or_exception(adapter, fail, inner):
    outer = {"security_scan_context": '{"run_id":"outer"}'}
    inner_scope = {"run_id": inner} if inner else {}
    inner_metadata = {"security_scan_context": json.dumps(inner_scope)} if inner else {}
    with _bind_tool_workspace_root(adapter, outer, ()):
        assert _execute(adapter) == {"run_id": "outer"}
        with (
            pytest.raises(RuntimeError, match="turn failed") if fail else nullcontext()
        ):
            with _bind_tool_workspace_root(adapter, inner_metadata, ()):
                assert _execute(adapter) == inner_scope
                if fail:
                    raise RuntimeError("turn failed")
        assert _execute(adapter) == {"run_id": "outer"}
    assert _execute(adapter) == {}
    assert "security_scan_context" not in adapter.policy.raw["context_metadata"]


def test_scan_context_isolated_across_concurrent_worker_threads(adapter):
    barrier = Barrier(2)

    def execute_together():
        barrier.wait(timeout=5)
        return _execute(adapter)

    async def turn(run_id):
        metadata = {"security_scan_context": json.dumps({"run_id": run_id})}
        with _bind_tool_workspace_root(adapter, metadata, ()):
            return await asyncio.to_thread(execute_together)

    async def parallel_turns():
        return await asyncio.gather(turn("first"), turn("second"))

    assert asyncio.run(parallel_turns()) == [{"run_id": "first"}, {"run_id": "second"}]
    assert _execute(adapter) == {}
    assert "security_scan_context" not in adapter.policy.raw["context_metadata"]
