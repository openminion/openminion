from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

from openminion.cli.chat.commands import handle_chat_command
from openminion.cli.chat.commands.base import ChatCommandResult
from openminion.cli.chat.runner import ChatRunnerDeps, run_chat as run_chat_runner
from openminion.cli.chat.runtime import (
    ChatRuntimeState,
    TurnExecutionDeps,
    execute_turn,
)
from openminion.modules.session.todo import (
    get_default_todo_store as get_default_plan_store,
    reset_default_todo_store_for_tests as reset_default_plan_store_for_tests,
)


def _plan_metadata(
    *,
    tool_name: str = "plan.update",
    summary: str = "1/2 done, 1 in progress",
) -> dict[str, str]:
    return {
        "tool_results": json.dumps(
            [
                {
                    "tool_name": tool_name,
                    "data": {
                        "plan": {
                            "session_id": "sess-plan",
                            "items": [
                                {"index": 0, "text": "Read config", "status": "done"},
                                {
                                    "index": 1,
                                    "text": "Edit handler",
                                    "status": "in_progress",
                                },
                            ],
                            "summary": summary,
                        },
                        "message": "Item 0 -> done.",
                    },
                }
            ],
            sort_keys=True,
        )
    }


def _runtime_state(*, endpoint: object | None = None) -> ChatRuntimeState:
    return ChatRuntimeState(
        endpoint=endpoint,
        transport="daemon" if endpoint is not None else "in-process",
        inproc_runtime=None,
        mode="single-process",
        auto_start=False,
        show_progress=False,
        show_activity_indicator=False,
        quiet=False,
    )


def _turn_deps(
    *,
    request_inproc_turn=None,
    request_daemon_turn=None,
) -> TurnExecutionDeps:
    return TurnExecutionDeps(
        request_daemon_turn=request_daemon_turn
        or (lambda **_: (_ for _ in ()).throw(AssertionError("daemon not expected"))),
        ensure_inproc_runtime=lambda *_args, **_kwargs: object(),  # pragma: no cover
        set_quiet_log_level=lambda: None,
        request_inproc_turn=request_inproc_turn
        or (lambda **_: (_ for _ in ()).throw(AssertionError("inproc not expected"))),
        format_api_error=lambda payload, status: f"{status}:{payload}",
        is_retryable_turn_error=lambda _error: False,
        print_fallback_notice=lambda _exc: None,
        print_turn_error=lambda _exc: None,
        print_assistant_text=lambda **kwargs: print(f"ASSISTANT: {kwargs['text']}"),
        print_turn_usage_summary=lambda _summary: None,
        emit_session_event_safe=lambda **_kwargs: None,
        build_run_profile_override_payload=lambda _args: {},
    )


def test_handle_chat_command_routes_plan_via_dispatcher() -> None:
    reset_default_plan_store_for_tests()
    get_default_plan_store().set_plan("sess-dispatch", ["Read config"])

    output = io.StringIO()
    with redirect_stdout(output):
        result = handle_chat_command(
            line="/plan",
            args=SimpleNamespace(config=None),
            config=SimpleNamespace(runtime=SimpleNamespace(env={})),
            agent_id="agent-plan",
            session_id="sess-dispatch",
            transport="in-process",
            mode="single-process",
            runtime_state=SimpleNamespace(),
            last_artifacts=[],
            last_turn_debug={},
        )

    assert result.handled is True
    rendered = output.getvalue()
    assert "Plan (" in rendered
    assert "[ ] Read config" in rendered


def test_handle_chat_command_routes_goal_via_dispatcher(monkeypatch) -> None:
    seen: list[tuple[str, str, object | None]] = []

    def _fake_handle_goal_command(
        line: str,
        *,
        session_id: str,
        config_path: object | None = None,
    ) -> bool:
        seen.append((line, session_id, config_path))
        print("goal handled")
        return True

    monkeypatch.setattr(
        "openminion.cli.chat.commands.goal.handle_goal_command",
        _fake_handle_goal_command,
    )
    output = io.StringIO()
    with redirect_stdout(output):
        result = handle_chat_command(
            line="/goal list",
            args=SimpleNamespace(config="/tmp/test-config.toml"),
            config=SimpleNamespace(runtime=SimpleNamespace(env={})),
            agent_id="agent-goal",
            session_id="sess-goal",
            transport="in-process",
            mode="single-process",
            runtime_state=SimpleNamespace(),
            last_artifacts=[],
            last_turn_debug={},
        )

    assert result.handled is True
    assert seen == [("/goal list", "sess-goal", "/tmp/test-config.toml")]
    assert "goal handled" in output.getvalue()


def test_execute_turn_inproc_renders_plan_before_assistant_text() -> None:
    output = io.StringIO()
    with redirect_stdout(output):
        result = execute_turn(
            runtime_state=_runtime_state(),
            args=SimpleNamespace(config=None),
            payload={"idempotency_key": "turn-1"},
            inbound_metadata={},
            line="finish task",
            agent_id="agent-plan",
            session_id="sess-plan",
            lifecycle_payload={},
            chat_turn_timeout=30.0,
            attempt=1,
            chat_turn_max_attempts=1,
            deps=_turn_deps(
                request_inproc_turn=lambda **_: {
                    "body": "Finished the change.",
                    "run_id": "run-1",
                    "metadata": _plan_metadata(),
                }
            ),
        )

    assert result["retry"] is False
    rendered = output.getvalue()
    assert "Plan (1/2 done, 1 in progress):" in rendered
    assert "ASSISTANT: Finished the change." in rendered
    assert rendered.index("Plan (1/2 done, 1 in progress):") < rendered.index(
        "ASSISTANT: Finished the change."
    )


def test_execute_turn_daemon_renders_plan_before_assistant_text() -> None:
    output = io.StringIO()
    with redirect_stdout(output):
        result = execute_turn(
            runtime_state=_runtime_state(endpoint=object()),
            args=SimpleNamespace(config=None),
            payload={"idempotency_key": "turn-2"},
            inbound_metadata={},
            line="finish task",
            agent_id="agent-plan",
            session_id="sess-plan",
            lifecycle_payload={},
            chat_turn_timeout=30.0,
            attempt=1,
            chat_turn_max_attempts=1,
            deps=_turn_deps(
                request_daemon_turn=lambda **_: (
                    200,
                    {
                        "ok": True,
                        "turn": {
                            "final_text": "Finished via daemon.",
                            "trace_id": "trace-plan",
                            "metadata": _plan_metadata(),
                            "artifacts": [],
                            "tool_calls_summary": [],
                            "errors": [],
                        },
                    },
                )
            ),
        )

    assert result["retry"] is False
    rendered = output.getvalue()
    assert "Plan (1/2 done, 1 in progress):" in rendered
    assert "ASSISTANT: Finished via daemon." in rendered
    assert rendered.index("Plan (1/2 done, 1 in progress):") < rendered.index(
        "ASSISTANT: Finished via daemon."
    )


def test_execute_turn_inproc_renders_family_wrapper_plan_results() -> None:
    output = io.StringIO()
    with redirect_stdout(output):
        result = execute_turn(
            runtime_state=_runtime_state(),
            args=SimpleNamespace(config=None),
            payload={"idempotency_key": "turn-3"},
            inbound_metadata={},
            line="plan a fix",
            agent_id="agent-plan",
            session_id="sess-plan",
            lifecycle_payload={},
            chat_turn_timeout=30.0,
            attempt=1,
            chat_turn_max_attempts=1,
            deps=_turn_deps(
                request_inproc_turn=lambda **_: {
                    "body": "The first step is underway.",
                    "run_id": "run-2",
                    "metadata": _plan_metadata(tool_name="plan"),
                }
            ),
        )

    assert result["retry"] is False
    rendered = output.getvalue()
    assert "Plan (1/2 done, 1 in progress):" in rendered
    assert "ASSISTANT: The first step is underway." in rendered


def test_execute_turn_inproc_renders_when_store_changes_without_tool_metadata() -> None:
    reset_default_plan_store_for_tests()

    def _request_inproc_turn(**_kwargs):
        store = get_default_plan_store()
        store.set_plan("sess-plan", ["Read config", "Edit handler"])
        store.update_item_status("sess-plan", 0, "in_progress")
        return {
            "body": "The first step is underway.",
            "run_id": "run-store",
            "metadata": {"trace_id": "trace-store"},
        }

    output = io.StringIO()
    with redirect_stdout(output):
        result = execute_turn(
            runtime_state=_runtime_state(),
            args=SimpleNamespace(config=None),
            payload={"idempotency_key": "turn-4"},
            inbound_metadata={},
            line="plan a fix",
            agent_id="agent-plan",
            session_id="sess-plan",
            lifecycle_payload={},
            chat_turn_timeout=30.0,
            attempt=1,
            chat_turn_max_attempts=1,
            deps=_turn_deps(request_inproc_turn=_request_inproc_turn),
        )

    assert result["retry"] is False
    rendered = output.getvalue()
    assert "Plan (0/2 done, 1 in progress):" in rendered
    assert "[→] Read config" in rendered
    assert "ASSISTANT: The first step is underway." in rendered


def test_run_chat_evicts_plan_on_clean_exit(monkeypatch, tmp_path: Path) -> None:
    reset_default_plan_store_for_tests()
    get_default_plan_store().set_plan("sess-clean", ["temporary"])
    roots = SimpleNamespace(home_root=tmp_path, data_root=tmp_path / ".openminion")
    roots.data_root.mkdir(parents=True, exist_ok=True)
    config = SimpleNamespace(
        runtime=SimpleNamespace(
            session_stale_timeout_seconds=1,
            chat_turn_timeout_seconds=1,
            chat_turn_max_attempts=1,
        )
    )

    monkeypatch.setattr("builtins.input", lambda _prompt="": "/exit")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)

    def _handle_repl_command(**kwargs):
        command_result = kwargs["command_result"]
        assert isinstance(command_result, ChatCommandResult)
        return {
            "exit_clean": bool(command_result.exit),
            "agent_id": "agent-plan",
            "session_id": "sess-clean",
            "conversation_selection": {"source": "fresh"},
            "conversation_id": "conv-1",
            "thread_id": "thread-1",
            "attach_id": "attach-1",
            "lifecycle_payload": {},
            "resume_requested": False,
            "reset_requested": False,
        }

    deps = ChatRunnerDeps(
        resolve_chat_roots=lambda _args: (tmp_path / "missing-config.json", roots),
        load_config=lambda *_args, **_kwargs: config,
        inspect_chat_onboarding=lambda _args: (
            SimpleNamespace(
                action=SimpleNamespace(value="none"),
                state=SimpleNamespace(value="ready"),
            ),
            tmp_path / "missing-config.json",
            roots,
        ),
        print_onboarding_fail_fast=lambda _status: 1,
        run_inline_setup_for_chat=lambda _args: 1,
        materialize_demo_config_for_chat=lambda *_args, **_kwargs: (
            tmp_path / "demo.json"
        ),
        normalize_chat_args=lambda _args, _config: SimpleNamespace(
            session_id="sess-clean",
            session_name=None,
        ),
        perform_identity_sync=lambda **_kwargs: None,
        should_suppress_console_info_logs=lambda **_kwargs: False,
        set_quiet_log_level=lambda: None,
        init_runtime_state=lambda _args, _config: (_runtime_state(), None),
        mark_stale_cli_sessions=lambda **_kwargs: 0,
        resolve_initial_chat_agent_id=lambda *_args, **_kwargs: (
            "agent-plan",
            {"source": "test"},
        ),
        resolve_lifecycle_state=lambda *_args, **_kwargs: (
            {"source": "fresh"},
            "conv-1",
            "thread-1",
            "attach-1",
            {},
        ),
        session_profile_mismatch_message=lambda **_kwargs: "",
        print_chat_ready_banner=lambda **_kwargs: None,
        print_agent_resolution_notice=lambda **_kwargs: None,
        print_stale_session_notice=lambda **_kwargs: None,
        print_first_session_tip_if_requested=lambda _args: None,
        get_session_record=lambda **_kwargs: None,
        emit_session_open_events=lambda **_kwargs: None,
        set_session_name_if_missing=lambda **_kwargs: False,
        handle_chat_command=lambda **kwargs: ChatCommandResult(
            handled=True, exit=kwargs["line"] == "/exit"
        ),
        handle_repl_command=_handle_repl_command,
        local_human_post_block_reason=lambda **_kwargs: "",
        build_lifecycle_payload=lambda **_kwargs: {},
        build_inbound_metadata=lambda **_kwargs: {},
        build_turn_idempotency_key=lambda **_kwargs: "turn-key",
        build_run_profile_override_payload=lambda _args: {},
        execute_turn=lambda **_kwargs: {"stop": True},
        maybe_auto_name_session=lambda **_kwargs: False,
        emit_session_event_safe=lambda **_kwargs: None,
        close_runtime=lambda _state: None,
        chat_input_prompt=lambda **_kwargs: "[sess|agent] you> ",
        conversation_env_name="OPENMINION_CONVERSATION_ID",
        resolve_environment_config=lambda: {},
        stale_timeout_default=1,
        turn_timeout_default=1.0,
        turn_max_attempts_default=1,
    )

    assert get_default_plan_store().get_plan("sess-clean") is not None
    code = run_chat_runner(
        SimpleNamespace(config=None, demo=False, quiet=False), deps=deps
    )
    assert code == 0
    assert get_default_plan_store().get_plan("sess-clean") is None
