"""Cache invalidation through the real owners.

`execute_iteration_results` is the production seam between a tool outcome and
`LoopCache.invalidate_for_tool`; the path-identity controls call the real file
handlers in an isolated workspace.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from openminion.modules.brain.loop.tools.cache import LoopCache
from openminion.modules.brain.loop.tools.causal import classify_batch
from openminion.modules.brain.loop.tools.contracts import (
    AdaptiveToolLoopContext,
    AdaptiveToolLoopProfile,
    AdaptiveToolLoopState,
    CommandExecutionOutcome,
)
from openminion.modules.brain.loop.tools.iteration.execution import (
    execute_iteration_results,
)
from openminion.modules.brain.schemas import ActionResult
from openminion.modules.llm.schemas import ToolCall
from openminion.tools.file.plugin import _h_read_file, _h_write_file
from tests.test_file_tool_characterization import _ctx


def _noop(*args: Any, **kwargs: Any) -> None:
    return None


def _outcome(status: str) -> CommandExecutionOutcome:
    return CommandExecutionOutcome(
        approved_command=cast("Any", None),
        action_result=ActionResult(
            command_id="write", status=cast("Any", status), summary="write", outputs={}
        ),
        job=None,
        tool_budget_debited=True,
    )


def _outcome_from(payload: dict[str, Any]) -> CommandExecutionOutcome:
    return CommandExecutionOutcome(
        approved_command=cast("Any", None),
        action_result=ActionResult(
            command_id=str(payload.get("path", "")),
            status=cast("Any", "success" if payload.get("ok") else "failed"),
            summary=str(payload.get("error", "")),
            outputs=dict(payload),
        ),
        job=None,
        tool_budget_debited=True,
    )


def _run(tool_name: str, arguments: dict[str, str], status: str) -> LoopCache:
    cache = LoopCache()
    cache.put(
        "file.read",
        {"path": "a.py"},
        SimpleNamespace(
            action_result=ActionResult(
                command_id="seed", status="success", summary="ok", outputs={}
            ),
            job=None,
        ),
    )

    execute_iteration_results(
        cast(
            "AdaptiveToolLoopContext",
            SimpleNamespace(
                prepared_parallel_dispatch_supported=False,
                provider_retry_max_attempts=0,
                session_api=None,
                state=SimpleNamespace(trace_id="trace-cache"),
                emit_status=_noop,
            ),
        ),
        profile=AdaptiveToolLoopProfile(
            profile_name="cache-probe",
            mode_name="act",
            allowed_tools=frozenset({tool_name}),
        ),
        loop_state=AdaptiveToolLoopState(),
        runtime=None,
        model="probe",
        max_output_tokens=None,
        metadata=None,
        allowed_tools=frozenset({tool_name}),
        public_mode_tag="Act",
        signature="probe",
        ordered_tool_results=[
            (
                ToolCall(id="call-1", name=tool_name, arguments=arguments),
                _outcome(status),
            )
        ],
        cached_indices=frozenset(),
        iter_batch_parallel_count=0,
        initial_batch_had_progress=False,
        loop_cache=cache,
        loop_profiler=SimpleNamespace(record_tool_call=_noop),
        on_tool_result=None,
        iter_tool_records=[],
        append_tool_result_payload=_noop,
        set_turn_progress=_noop,
        effective_cap=lambda *args, **kwargs: 10,
        debit_tool_budget=_noop,
        profile_budget_exhausted=lambda *args, **kwargs: False,
        tool_budget_exhausted_for_answer_only=lambda *args, **kwargs: False,
        force_budget_answer_only_finalization=_noop,
        build_missing_action_result=lambda name: ActionResult(
            command_id="missing", status="failed", summary="", outputs={}
        ),
        build_tool_failure_recovery_message=_noop,
        build_enrichment_message=_noop,
        direct_tool_turn_active=lambda *args, **kwargs: False,
        trigger_macro_correction=_noop,
        dispatch_correction_plan=_noop,
    )
    return cache


@pytest.mark.parametrize("status", ["success", "failed", "timeout"])
def test_iteration_invalidates_cached_read_for_any_effectful_outcome(
    status: str,
) -> None:
    cache = _run("file.write", {"path": "a.py"}, status)

    assert cache.get("file.read", {"path": "a.py"}) is None


@pytest.mark.parametrize("status", ["blocked", "needs_user"])
def test_iteration_keeps_cached_read_for_a_pre_dispatch_denial(status: str) -> None:
    cache = _run("file.write", {"path": "a.py"}, status)

    assert cache.get("file.read", {"path": "a.py"}) is not None


def test_iteration_keeps_cached_read_for_an_unrelated_read_follow_up() -> None:
    cache = _run("file.read", {"path": "b.py"}, "success")

    assert cache.get("file.read", {"path": "a.py"}) is not None


def _seed_cached_read(tmp_path: Any) -> tuple[LoopCache, dict[str, str], Any]:
    ctx = _ctx(tmp_path)
    (ctx.workspace / "sub").mkdir()
    assert _h_write_file({"path": "a.py", "content": "old"}, ctx)["ok"]
    args = {"path": "a.py"}
    cache = LoopCache()
    cache.put("file.read", args, _outcome_from(_h_read_file(args, ctx)))
    assert cache.get("file.read", args) is not None
    return cache, args, ctx


@pytest.mark.parametrize("write_path", ["a.py", "./a.py"])
def test_comparable_write_spellings_invalidate_the_cached_read(
    tmp_path: Any, write_path: str
) -> None:
    cache, args, ctx = _seed_cached_read(tmp_path)

    cache.invalidate_for_tool(
        "file.write",
        {"path": write_path},
        _outcome_from(_h_write_file({"path": write_path, "content": "new"}, ctx)),
    )

    assert cache.get("file.read", args) is None
    assert _h_read_file(args, ctx)["content"] == "new"


def test_a_parent_segment_write_spelling_invalidates_the_cached_read(
    tmp_path: Any,
) -> None:
    cache, args, ctx = _seed_cached_read(tmp_path)

    cache.invalidate_for_tool(
        "file.write",
        {"path": "sub/../a.py"},
        _outcome_from(_h_write_file({"path": "sub/../a.py", "content": "new"}, ctx)),
    )

    assert cache.get("file.read", args) is None
    assert _h_read_file(args, ctx)["content"] == "new"


def test_an_absolute_workspace_write_spelling_invalidates_the_cached_read(
    tmp_path: Any,
) -> None:
    cache, args, ctx = _seed_cached_read(tmp_path)
    absolute = str(ctx.workspace / "a.py")

    cache.invalidate_for_tool(
        "file.write",
        {"path": absolute},
        _outcome_from(_h_write_file({"path": absolute, "content": "new"}, ctx)),
    )

    assert cache.get("file.read", args) is None
    assert _h_read_file(args, ctx)["content"] == "new"


def test_a_different_file_keeps_the_cached_read(tmp_path: Any) -> None:
    cache, args, ctx = _seed_cached_read(tmp_path)

    cache.invalidate_for_tool(
        "file.write",
        {"path": "b.py"},
        _outcome_from(_h_write_file({"path": "b.py", "content": "other"}, ctx)),
    )

    assert cache.get("file.read", args) is not None


def _seed_cached_sub_read(tmp_path: Any) -> tuple[LoopCache, dict[str, str], Any]:
    ctx = _ctx(tmp_path)
    (ctx.workspace / "sub").mkdir()
    assert _h_write_file({"path": "sub/a.py", "content": "old"}, ctx)["ok"]
    args = {"path": "sub/a.py"}
    cache = LoopCache()
    cache.put("file.read", args, _outcome_from(_h_read_file(args, ctx)))
    assert cache.get("file.read", args) is not None
    return cache, args, ctx


@pytest.mark.parametrize("write_path", ["sub/./a.py", "sub//a.py"])
def test_dot_and_repeated_separator_spellings_invalidate_the_cached_read(
    tmp_path: Any, write_path: str
) -> None:
    cache, args, ctx = _seed_cached_sub_read(tmp_path)

    cache.invalidate_for_tool(
        "file.write",
        {"path": write_path},
        _outcome_from(_h_write_file({"path": write_path, "content": "new"}, ctx)),
    )

    assert cache.get("file.read", args) is None
    assert _h_read_file(args, ctx)["content"] == "new"


@pytest.mark.parametrize("write_path", ["sub/./a.py", "sub//a.py"])
def test_batch_ordering_recognises_dot_and_repeated_separator_spellings(
    write_path: str,
) -> None:
    batch = classify_batch(
        [
            ToolCall(id="call-read", name="file.read", arguments={"path": "sub/a.py"}),
            ToolCall(
                id="call-write", name="file.write", arguments={"path": write_path}
            ),
        ]
    )

    assert batch.ordered_pairs == ((0, 1),)
    assert batch.groups == ((0,), (1,))


def test_batch_ordering_keeps_distinct_files_independent() -> None:
    batch = classify_batch(
        [
            ToolCall(id="call-read", name="file.read", arguments={"path": "sub/a.py"}),
            ToolCall(
                id="call-write", name="file.write", arguments={"path": "sub/b.py"}
            ),
        ]
    )

    assert batch.ordered_pairs == ()
    assert batch.groups == ((0, 1),)


def test_an_absolute_root_read_is_invalidated_by_a_write_below_it() -> None:
    cache = LoopCache()
    root_args = {"path": "/"}
    cache.put("file.find", root_args, _outcome("success"))
    assert cache.get("file.find", root_args) is not None

    cache.invalidate_for_tool(
        "file.write", {"path": "/tmp/pr-review/a.txt"}, _outcome("success")
    )

    assert cache.get("file.find", root_args) is None


def test_batch_ordering_places_an_absolute_root_read_before_a_child_write() -> None:
    batch = classify_batch(
        [
            ToolCall(id="call-find", name="file.find", arguments={"path": "/"}),
            ToolCall(
                id="call-write",
                name="file.write",
                arguments={"path": "/tmp/pr-review/a.txt"},
            ),
        ]
    )

    assert batch.ordered_pairs == ((0, 1),)
    assert batch.groups == ((0,), (1,))


def test_batch_ordering_keeps_a_child_directory_and_its_file_ordered() -> None:
    batch = classify_batch(
        [
            ToolCall(
                id="call-find", name="file.find", arguments={"path": "/tmp/pr-review"}
            ),
            ToolCall(
                id="call-write",
                name="file.write",
                arguments={"path": "/tmp/pr-review/a.txt"},
            ),
        ]
    )

    assert batch.ordered_pairs == ((0, 1),)
    assert batch.groups == ((0,), (1,))


def test_relative_root_read_is_invalidated_by_a_write_below_it() -> None:
    cache = LoopCache()
    root_args = {"path": "."}
    cache.put("file.find", root_args, _outcome("success"))

    cache.invalidate_for_tool("file.write", {"path": "sub/a.py"}, _outcome("success"))

    assert cache.get("file.find", root_args) is None
