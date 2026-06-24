from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable

import pytest

from openminion.modules.brain.diagnostics.telemetry import emit_brain_operation
from openminion.modules.context.compress.events import emit_compress_operation
from openminion.modules.llm.diagnostics.events import emit_llm_operation
from openminion.modules.memory.diagnostics.events import emit_memory_operation
from openminion.modules.retrieve.diagnostics.events import (
    emit_retrieve_operation,
)
from openminion.services.runtime.events import emit_runtime_operation
from openminion.modules.session.diagnostics.events import (
    emit_session_operation,
)
from openminion.modules.skill.diagnostics.events import emit_skill_operation
from openminion.modules.telemetry.service import TelemetryCtl, TelemetryService
from openminion.modules.tool.diagnostics.events import (
    emit_tool_exec_operation,
    emit_tool_invoke_operation,
)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


HELPER_CASES: list[tuple[str, Callable[[Any], bool], Callable[[Any], bool]]] = [
    (
        "openminion-llm",
        lambda ctl: emit_llm_operation(
            telemetryctl=ctl,
            session_id="sess-compat",
            turn_id="turn-1",
            operation="request",
            provider="local",
            model="test",
        ),
        lambda ctl: emit_llm_operation(
            telemetryctl=ctl,
            session_id="sess-compat",
            turn_id="turn-1",
            operation="",
            provider="local",
            model="test",
        ),
    ),
    (
        "openminion-tool",
        lambda ctl: emit_tool_exec_operation(
            telemetryctl=ctl,
            session_id="sess-compat",
            turn_id="turn-1",
            operation="run",
            tool_name="echo",
        ),
        lambda ctl: emit_tool_invoke_operation(
            telemetryctl=ctl,
            session_id="sess-compat",
            turn_id="turn-1",
            operation="",
            tool_name="echo",
        ),
    ),
    (
        "context.compress",
        lambda ctl: emit_compress_operation(
            telemetryctl=ctl,
            session_id="sess-compat",
            turn_id="turn-1",
            operation="summary_create",
        ),
        lambda ctl: emit_compress_operation(
            telemetryctl=ctl,
            session_id="sess-compat",
            turn_id="turn-1",
            operation="",
        ),
    ),
    (
        "openminion-skill",
        lambda ctl: emit_skill_operation(
            telemetryctl=ctl,
            session_id="sess-compat",
            turn_id="turn-1",
            operation="shortlist",
        ),
        lambda ctl: emit_skill_operation(
            telemetryctl=ctl,
            session_id="sess-compat",
            turn_id="turn-1",
            operation="",
        ),
    ),
    (
        "openminion-memory",
        lambda ctl: emit_memory_operation(
            telemetryctl=ctl,
            session_id="sess-compat",
            turn_id="turn-1",
            operation="query",
        ),
        lambda ctl: emit_memory_operation(
            telemetryctl=ctl,
            session_id="sess-compat",
            turn_id="turn-1",
            operation="",
        ),
    ),
    (
        "openminion-retrieve",
        lambda ctl: emit_retrieve_operation(
            telemetryctl=ctl,
            session_id="sess-compat",
            turn_id="turn-1",
            operation="query",
        ),
        lambda ctl: emit_retrieve_operation(
            telemetryctl=ctl,
            session_id="sess-compat",
            turn_id="turn-1",
            operation="",
        ),
    ),
    (
        "openminion-brain",
        lambda ctl: emit_brain_operation(
            telemetryctl=ctl,
            session_id="sess-compat",
            turn_id="turn-1",
            operation="turn_start",
        ),
        lambda ctl: emit_brain_operation(
            telemetryctl=ctl,
            session_id="sess-compat",
            turn_id="turn-1",
            operation="",
        ),
    ),
    (
        "openminion-runtime",
        lambda ctl: emit_runtime_operation(
            telemetryctl=ctl,
            session_id="sess-compat",
            turn_id="turn-1",
            operation="turn_start",
        ),
        lambda ctl: emit_runtime_operation(
            telemetryctl=ctl,
            session_id="sess-compat",
            turn_id="turn-1",
            operation="",
        ),
    ),
    (
        "openminion-session",
        lambda ctl: emit_session_operation(
            telemetryctl=ctl,
            session_id="sess-compat",
            turn_id="turn-1",
            operation="turn_start",
        ),
        lambda ctl: emit_session_operation(
            telemetryctl=ctl,
            session_id="sess-compat",
            turn_id="turn-1",
            operation="",
        ),
    ),
]


@pytest.mark.parametrize(("module_id", "valid_call", "invalid_call"), HELPER_CASES)
def test_module_operation_helpers_accept_valid_payloads_and_reject_invalid_names(
    module_id: str,
    valid_call: Callable[[Any], bool],
    invalid_call: Callable[[Any], bool],
    tmp_path: Path,
) -> None:
    service = TelemetryService(str(tmp_path / ".openminion" / "telemetry.db"))
    ctl = TelemetryCtl(service)
    assert valid_call(ctl) is True
    assert invalid_call(ctl) is False
    summary = _run(service.get_module_summary("sess-compat"))
    assert module_id in summary
    _run(service.close())


def test_module_counter_adapter_rejects_negative_value_consistently(
    tmp_path: Path,
) -> None:
    service = TelemetryService(str(tmp_path / ".openminion" / "telemetry.db"))
    ctl = TelemetryCtl(service)
    with pytest.raises(ValueError, match="non-negative"):
        _run(
            ctl.emit_module_counter(
                "sess-compat",
                "turn-1",
                "openminion-memory",
                "returned_items",
                -1.0,
            )
        )
    _run(service.close())
