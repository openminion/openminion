from __future__ import annotations

import tempfile
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


from pydantic import BaseModel

from openminion.modules.brain.adapters.a2a import LocalA2AAdapter
from openminion.modules.brain.adapters.context import LocalContextAdapter
from openminion.modules.brain.adapters.llm import LocalLLMAdapter
from openminion.modules.brain.adapters.memory import LocalMemoryAdapter
from openminion.modules.brain.adapters.policy import LocalPolicyAdapter
from openminion.modules.brain.adapters.session import LocalSessionStore
from openminion.modules.brain.adapters.tool import LocalToolAdapter
from openminion.modules.brain.runner import RunnerOptions, BrainRunner
from openminion.modules.brain.schemas import (
    AgentBudgets,
    AgentDefaults,
    AgentProfile,
    AgentCommand,
    LLMProfiles,
    PolicyDecision,
    ToolCommand,
    ActionError,
    ActionResult,
)
from openminion.modules.brain.diagnostics.events import CanonicalEventLogger
from openminion.modules.brain.loop.tools.contracts import (
    PreparedToolDispatch,
    PrepareOutcome,
    RawToolResult,
)
from openminion.modules.brain.adapters.tool.permission_mode import (
    canonical_permission_mode,
)
from openminion.modules.brain.tools.executor import (
    RunnerCommandExecutor,
    execute_action,
)
from openminion.modules.tool.registry import ToolRegistry, ToolSpec
from openminion.modules.policy.adapters.brain import PolicyCtlBrainAdapter
from openminion.modules.policy.models import RiskSpec
from openminion.modules.policy.runtime.service import PolicyCtl
from tests.brain.runner_test_support import build_seeded_act_decision


def _profile() -> AgentProfile:
    budgets = AgentBudgets(
        max_ticks_per_user_turn=5,
        max_tool_calls=3,
        max_a2a_calls=1,
        max_total_llm_tokens=1000,
        max_elapsed_ms=10000,
    )
    llm_profiles = LLMProfiles(
        decide_model="decide-default",
        plan_model="plan-default",
        act_model=None,
        reflect_model="reflect-default",
        summarize_model="summarize-default",
    )
    return AgentProfile(
        agent_id="test-agent",
        role="general",
        llm_profiles=llm_profiles,
        budgets=budgets,
        defaults=AgentDefaults(),
    )


class ConfirmPolicyAdapter:
    contract_version = "v1"

    def evaluate(self, *, command, working_state, session_context):
        return PolicyDecision(
            outcome="REQUIRE_CONFIRMATION", explanation="Confirm required"
        )


class CustomTokenConfirmationPolicyAdapter:
    contract_version = "v1"

    def __init__(self) -> None:
        self._confirmed = False

    def parse_confirmation_response(self, text: str) -> str:
        normalized = str(text or "").strip().lower()
        if normalized == "absolutely":
            self._confirmed = True
            return "affirm"
        if normalized in {"no", "decline"}:
            return "deny"
        return "unclear"

    def evaluate(self, *, command, working_state, session_context):
        del working_state, session_context
        if command.kind == "tool" and not self._confirmed:
            return PolicyDecision(
                outcome="REQUIRE_CONFIRMATION",
                explanation="Confirm required",
            )
        self._confirmed = False
        return PolicyDecision(
            outcome="ALLOW", explanation="Allowed after confirmation."
        )


class NonNormalSafetyAdapter:
    contract_version = "v1"

    def is_normal(self) -> bool:
        return False


def _build_runner(
    tmp_path: Path, *, policy_api, safety_api=None
) -> tuple[BrainRunner, LocalSessionStore]:
    session = LocalSessionStore(tmp_path / "sessions")
    runner = BrainRunner(
        profile=_profile(),
        session_api=session,
        context_api=LocalContextAdapter(session_store=session),
        llm_api=LocalLLMAdapter(),
        tool_api=LocalToolAdapter(),
        a2a_api=LocalA2AAdapter(),
        memory_api=LocalMemoryAdapter(tmp_path / "memory"),
        policy_api=policy_api,
        safety_api=safety_api,
        options=RunnerOptions(metactl_enabled=False),
    )
    return runner, session


class _ReadFileArgs(BaseModel):
    path: str


class _WeatherArgs(BaseModel):
    location: str


def _read_file_tool_spec() -> ToolSpec:
    def _handler(args, ctx):
        return {"ok": True, "data": dict(args)}

    return ToolSpec(
        name="file.read",
        args_model=_ReadFileArgs,
        min_scope="READ_ONLY",
        handler=_handler,
    )


def _weather_tool_spec() -> ToolSpec:
    def _handler(args, ctx):
        return {"ok": True, "data": dict(args)}

    return ToolSpec(
        name="weather",
        args_model=_WeatherArgs,
        min_scope="READ_ONLY",
        handler=_handler,
    )


def _build_runner_with_registry(
    tmp_path: Path, *, policy_api, registry: ToolRegistry
) -> tuple[BrainRunner, LocalSessionStore]:
    session = LocalSessionStore(tmp_path / "sessions")
    tool_adapter = LocalToolAdapter()
    tool_adapter.registry = registry
    runner = BrainRunner(
        profile=_profile(),
        session_api=session,
        context_api=LocalContextAdapter(session_store=session),
        llm_api=LocalLLMAdapter(),
        tool_api=tool_adapter,
        a2a_api=LocalA2AAdapter(),
        memory_api=LocalMemoryAdapter(tmp_path / "memory"),
        policy_api=policy_api,
        options=RunnerOptions(metactl_enabled=False),
    )
    return runner, session


def _memory_entries(runner: BrainRunner) -> list[dict[str, object]]:
    path = getattr(runner.memory_api, "path", None)
    if path is None or not Path(path).exists():
        return []
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_tool_allow_path_emits_tool_events() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, session = _build_runner(Path(tmp), policy_api=LocalPolicyAdapter())
        output = runner.step(
            session_id="s-tool",
            user_input='tool echo {"msg":"hi"}',
            trace_id="t-tool",
        )

        assert output.status == "done"
        assert output.message == "Echo tool executed."

        types = [event["type"] for event in session.list_events("s-tool")]
        assert "tool.request" in types
        assert "tool.completed" in types
        assert types.index("tool.request") < types.index("tool.completed")


def test_prepare_tool_dispatch_confirm_short_circuits_without_tool_request() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, session = _build_runner(Path(tmp), policy_api=ConfirmPolicyAdapter())
        state = runner._load_or_init_state("s-prepare-confirm")
        initial_tool_budget = state.budgets_remaining.tool_calls
        logger = CanonicalEventLogger(
            session_api=session,
            session_id="s-prepare-confirm",
            agent_id=runner.profile.agent_id,
        )
        executor = RunnerCommandExecutor(runner)
        command = ToolCommand(
            title="Tool call: echo",
            tool_name="echo",
            args={"msg": "hi"},
            success_criteria={"status": "success"},
        )

        prepared = executor.prepare_tool_dispatch(
            state=state,
            command=command,
            logger=logger,
        )

        assert isinstance(prepared, PrepareOutcome)
        assert prepared.disposition == "ask_user"
        assert prepared.action_result.status == "needs_user"
        assert state.budgets_remaining.tool_calls == initial_tool_budget
        types = [event["type"] for event in session.list_events("s-prepare-confirm")]
        assert "policy.applied" in types
        assert "tool.request" not in types


def test_prepare_tool_dispatch_returns_tool_api_unavailable_outcome() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, session = _build_runner(Path(tmp), policy_api=LocalPolicyAdapter())
        runner.tool_api = None
        state = runner._load_or_init_state("s-prepare-no-tool-api")
        initial_tool_budget = state.budgets_remaining.tool_calls
        logger = CanonicalEventLogger(
            session_api=session,
            session_id="s-prepare-no-tool-api",
            agent_id=runner.profile.agent_id,
        )
        executor = RunnerCommandExecutor(runner)
        command = ToolCommand(
            title="Tool call: echo",
            tool_name="echo",
            args={"msg": "hi"},
            success_criteria={"status": "success"},
        )

        prepared = executor.prepare_tool_dispatch(
            state=state,
            command=command,
            logger=logger,
        )

        assert isinstance(prepared, PrepareOutcome)
        assert prepared.disposition == "tool_api_unavailable"
        assert prepared.action_result.error is not None
        assert prepared.action_result.error.code == "TOOL_API_UNAVAILABLE"
        assert state.budgets_remaining.tool_calls == initial_tool_budget
        types = [
            event["type"] for event in session.list_events("s-prepare-no-tool-api")
        ]
        assert "tool.request" not in types


def test_prepare_tool_dispatch_marks_authorized_watch_action_inputs() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, session = _build_runner(Path(tmp), policy_api=LocalPolicyAdapter())
        state = runner._load_or_init_state("s-watch-write")
        state.module_state = {
            "watch_subscription": {
                "enabled": True,
                "turn_kind": "action",
                "write_authorized": True,
            }
        }
        logger = CanonicalEventLogger(
            session_api=session,
            session_id="s-watch-write",
            agent_id=runner.profile.agent_id,
        )
        executor = RunnerCommandExecutor(runner)
        command = ToolCommand(
            title="Tool call: file.write",
            tool_name="file.write",
            args={"path": "probe.txt", "content": "hello"},
            success_criteria={"path": "probe.txt"},
        )

        prepared = executor.prepare_tool_dispatch(
            state=state,
            command=command,
            logger=logger,
        )

        assert isinstance(prepared, PreparedToolDispatch)
        assert prepared.payload["inputs"]["background_write_authorized"] is True
        assert (
            prepared.payload["inputs"]["background_write_authorization_source"]
            == "watch_subscription"
        )


def test_prepared_tool_dispatch_round_trip_emits_tool_events_once() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        registry = ToolRegistry([])
        registry.add(_read_file_tool_spec())
        runner, session = _build_runner_with_registry(
            Path(tmp),
            policy_api=LocalPolicyAdapter(),
            registry=registry,
        )
        state = runner._load_or_init_state("s-prepare-roundtrip")
        initial_tool_budget = state.budgets_remaining.tool_calls
        logger = CanonicalEventLogger(
            session_api=session,
            session_id="s-prepare-roundtrip",
            agent_id=runner.profile.agent_id,
        )
        executor = RunnerCommandExecutor(runner)
        command = ToolCommand(
            title="Tool call: file.read",
            tool_name="file.read",
            args={"path": "/tmp/example.txt"},
            success_criteria={"status": "success"},
        )

        prepared = executor.prepare_tool_dispatch(
            state=state,
            command=command,
            logger=logger,
        )

        assert isinstance(prepared, PreparedToolDispatch)
        raw = executor.execute_prepared_tool_dispatch(prepared_dispatch=prepared)
        outcome = executor.finalize_tool_result(
            state=state,
            prepared_dispatch=prepared,
            raw_result=raw,
            logger=logger,
        )

        assert outcome.action_result is not None
        assert outcome.action_result.status == "success"
        assert state.budgets_remaining.tool_calls == initial_tool_budget - 1
        types = [event["type"] for event in session.list_events("s-prepare-roundtrip")]
        assert "tool.request" in types
        assert "tool.completed" in types
        assert types.index("tool.request") < types.index("tool.completed")
        assert [
            entry
            for entry in _memory_entries(runner)
            if entry.get("kind") == "candidate"
        ] == []


def test_finalize_tool_result_failure_stages_tool_outcome_candidate() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, session = _build_runner(Path(tmp), policy_api=LocalPolicyAdapter())
        state = runner._load_or_init_state("s-tool-outcome-failure")
        logger = CanonicalEventLogger(
            session_api=session,
            session_id="s-tool-outcome-failure",
            agent_id=runner.profile.agent_id,
        )
        executor = RunnerCommandExecutor(runner)
        command = ToolCommand(
            command_id="cmd-tool-failure",
            title="Tool call: file.read",
            tool_name="file.read",
            args={"path": "/tmp/missing.txt"},
            success_criteria={"status": "success"},
            sub_intent_ids=["intent-file-read"],
        )
        prepared = PreparedToolDispatch(
            approved_command=command,
            original_command=command,
            command_id=command.command_id,
            tool_name=command.tool_name,
            validated_args=dict(command.args),
            session_id=state.session_id,
            trace_id=str(state.trace_id or ""),
            agent_id=runner.profile.agent_id,
            lineage={},
            permission_mode="ask",
            payload={},
        )
        failed = ActionResult(
            command_id=command.command_id,
            status="failed",
            summary="read failed",
            error=ActionError(code="READ_ERROR", message="boom"),
        )
        with patch.object(
            runner, "_normalize_execution_result", return_value=(failed, None)
        ):
            outcome = executor.finalize_tool_result(
                state=state,
                prepared_dispatch=prepared,
                raw_result=RawToolResult(
                    command_id=command.command_id,
                    tool_name=command.tool_name,
                    raw_output={"ok": False},
                ),
                logger=logger,
            )

        assert outcome.action_result is not None
        entries = [
            entry
            for entry in _memory_entries(runner)
            if entry.get("kind") == "candidate"
        ]
        assert len(entries) == 1
        assert entries[0]["record_type"] == "tool_outcome"
        assert entries[0]["title"] == "tool_outcome:file.read:failure:READ_ERROR"
        assert entries[0]["content"]["outcome"] == "failure"
        assert entries[0]["content"]["error_code"] == "READ_ERROR"
        assert entries[0]["content"]["intent_id"] == "intent-file-read"
        assert entries[0]["content"]["args_signature"] == '{"path":"/tmp/missing.txt"}'
        assert entries[0]["confidence"] == 0.4
        assert (
            entries[0]["meta"]["source_args_signature"] == '{"path":"/tmp/missing.txt"}'
        )


def test_finalize_tool_result_timeout_stages_tool_outcome_candidate() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, session = _build_runner(Path(tmp), policy_api=LocalPolicyAdapter())
        state = runner._load_or_init_state("s-tool-outcome-timeout")
        logger = CanonicalEventLogger(
            session_api=session,
            session_id="s-tool-outcome-timeout",
            agent_id=runner.profile.agent_id,
        )
        executor = RunnerCommandExecutor(runner)
        command = ToolCommand(
            command_id="cmd-tool-timeout",
            title="Tool call: weather",
            tool_name="weather",
            args={"location": "SF"},
            success_criteria={"status": "success"},
        )
        prepared = PreparedToolDispatch(
            approved_command=command,
            original_command=command,
            command_id=command.command_id,
            tool_name=command.tool_name,
            validated_args=dict(command.args),
            session_id=state.session_id,
            trace_id=str(state.trace_id or ""),
            agent_id=runner.profile.agent_id,
            lineage={},
            permission_mode="ask",
            payload={},
        )
        timeout_result = ActionResult(
            command_id=command.command_id,
            status="timeout",
            summary="timed out",
            error=ActionError(code="TOOL_TIMEOUT", message="timed out"),
        )
        with patch.object(
            runner,
            "_normalize_execution_result",
            return_value=(timeout_result, None),
        ):
            executor.finalize_tool_result(
                state=state,
                prepared_dispatch=prepared,
                raw_result=RawToolResult(
                    command_id=command.command_id,
                    tool_name=command.tool_name,
                    raw_output={"ok": False},
                ),
                logger=logger,
            )

        entries = [
            entry
            for entry in _memory_entries(runner)
            if entry.get("kind") == "candidate"
        ]
        assert len(entries) == 1
        assert entries[0]["content"]["outcome"] == "timeout"
        assert entries[0]["content"]["error_code"] == "TOOL_TIMEOUT"
        assert entries[0]["content"]["args_signature"] == '{"location":"SF"}'
        assert entries[0]["confidence"] == 0.4


def test_finalize_tool_result_success_stages_allowlisted_tool_outcome_candidate() -> (
    None
):
    with tempfile.TemporaryDirectory() as tmp:
        runner, session = _build_runner(Path(tmp), policy_api=LocalPolicyAdapter())
        state = runner._load_or_init_state("s-tool-outcome-success")
        logger = CanonicalEventLogger(
            session_api=session,
            session_id="s-tool-outcome-success",
            agent_id=runner.profile.agent_id,
        )
        executor = RunnerCommandExecutor(runner)
        command = ToolCommand(
            command_id="cmd-tool-success",
            title="Tool call: web.fetch",
            tool_name="web.fetch",
            args={"url": "https://example.com"},
            success_criteria={"status": "success"},
            sub_intent_ids=["intent-fetch"],
        )
        prepared = PreparedToolDispatch(
            approved_command=command,
            original_command=command,
            command_id=command.command_id,
            tool_name=command.tool_name,
            validated_args=dict(command.args),
            session_id=state.session_id,
            trace_id=str(state.trace_id or ""),
            agent_id=runner.profile.agent_id,
            lineage={},
            permission_mode="ask",
            payload={},
        )
        success_result = ActionResult(
            command_id=command.command_id,
            status="success",
            summary="fetched page",
        )
        with patch.object(
            runner,
            "_normalize_execution_result",
            return_value=(success_result, None),
        ):
            executor.finalize_tool_result(
                state=state,
                prepared_dispatch=prepared,
                raw_result=RawToolResult(
                    command_id=command.command_id,
                    tool_name=command.tool_name,
                    raw_output={"ok": True},
                ),
                logger=logger,
            )

        entries = [
            entry
            for entry in _memory_entries(runner)
            if entry.get("kind") == "candidate"
        ]
        assert len(entries) == 1
        assert entries[0]["title"] == "tool_outcome:web.fetch:success"
        assert entries[0]["content"]["outcome"] == "success"
        assert entries[0]["content"]["intent_id"] == "intent-fetch"
        assert (
            entries[0]["content"]["args_signature"] == '{"url":"https://example.com"}'
        )
        assert entries[0]["confidence"] == 0.7
        assert entries[0]["meta"]["source_success_path"] is True
        assert entries[0]["meta"]["source_negative_outcome"] is False


def test_finalize_tool_result_success_does_not_stage_non_allowlisted_tool_outcome() -> (
    None
):
    with tempfile.TemporaryDirectory() as tmp:
        runner, session = _build_runner(Path(tmp), policy_api=LocalPolicyAdapter())
        state = runner._load_or_init_state("s-tool-outcome-success-file")
        logger = CanonicalEventLogger(
            session_api=session,
            session_id="s-tool-outcome-success-file",
            agent_id=runner.profile.agent_id,
        )
        executor = RunnerCommandExecutor(runner)
        command = ToolCommand(
            command_id="cmd-tool-success-file",
            title="Tool call: file.read",
            tool_name="file.read",
            args={"path": "/tmp/example.txt"},
            success_criteria={"status": "success"},
        )
        prepared = PreparedToolDispatch(
            approved_command=command,
            original_command=command,
            command_id=command.command_id,
            tool_name=command.tool_name,
            validated_args=dict(command.args),
            session_id=state.session_id,
            trace_id=str(state.trace_id or ""),
            agent_id=runner.profile.agent_id,
            lineage={},
            permission_mode="ask",
            payload={},
        )
        success_result = ActionResult(
            command_id=command.command_id,
            status="success",
            summary="read ok",
        )
        with patch.object(
            runner,
            "_normalize_execution_result",
            return_value=(success_result, None),
        ):
            executor.finalize_tool_result(
                state=state,
                prepared_dispatch=prepared,
                raw_result=RawToolResult(
                    command_id=command.command_id,
                    tool_name=command.tool_name,
                    raw_output={"ok": True},
                ),
                logger=logger,
            )

        entries = [
            entry
            for entry in _memory_entries(runner)
            if entry.get("kind") == "candidate"
        ]
        assert entries == []


def test_prepare_tool_dispatch_confirmation_stages_policy_denied_tool_outcome() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, session = _build_runner(Path(tmp), policy_api=ConfirmPolicyAdapter())
        state = runner._load_or_init_state("s-tool-outcome-confirm")
        logger = CanonicalEventLogger(
            session_api=session,
            session_id="s-tool-outcome-confirm",
            agent_id=runner.profile.agent_id,
        )
        executor = RunnerCommandExecutor(runner)
        command = ToolCommand(
            command_id="cmd-tool-confirm",
            title="Tool call: echo",
            tool_name="echo",
            args={"msg": "hi"},
            success_criteria={"status": "success"},
        )

        prepared = executor.prepare_tool_dispatch(
            state=state,
            command=command,
            logger=logger,
        )

        assert isinstance(prepared, PrepareOutcome)
        assert prepared.action_result.status == "needs_user"
        entries = [
            entry
            for entry in _memory_entries(runner)
            if entry.get("kind") == "candidate"
        ]
        assert len(entries) == 1
        assert entries[0]["content"]["outcome"] == "policy_denied"
        assert entries[0]["title"] == "tool_outcome:echo:policy_denied"
        assert entries[0]["content"]["args_signature"] == '{"msg":"hi"}'
        assert entries[0]["confidence"] == 0.4


def test_tool_outcome_staging_is_bounded_per_turn() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, session = _build_runner(Path(tmp), policy_api=LocalPolicyAdapter())
        state = runner._load_or_init_state("s-tool-outcome-bounded")
        logger = CanonicalEventLogger(
            session_api=session,
            session_id="s-tool-outcome-bounded",
            agent_id=runner.profile.agent_id,
        )
        executor = RunnerCommandExecutor(runner)
        failure_result = ActionResult(
            command_id="cmd-tool-bounded",
            status="failed",
            summary="read failed",
            error=ActionError(code="READ_ERROR", message="boom"),
        )

        for index in range(4):
            command = ToolCommand(
                command_id=f"cmd-tool-bounded-{index}",
                title="Tool call: file.read",
                tool_name="file.read",
                args={"path": f"/tmp/missing-{index}.txt"},
                success_criteria={"status": "success"},
            )
            prepared = PreparedToolDispatch(
                approved_command=command,
                original_command=command,
                command_id=command.command_id,
                tool_name=command.tool_name,
                validated_args=dict(command.args),
                session_id=state.session_id,
                trace_id=str(state.trace_id or ""),
                agent_id=runner.profile.agent_id,
                lineage={},
                permission_mode="ask",
                payload={},
            )
            failed = failure_result.model_copy(
                update={"command_id": command.command_id}
            )
            with patch.object(
                runner,
                "_normalize_execution_result",
                return_value=(failed, None),
            ):
                executor.finalize_tool_result(
                    state=state,
                    prepared_dispatch=prepared,
                    raw_result=RawToolResult(
                        command_id=command.command_id,
                        tool_name=command.tool_name,
                        raw_output={"ok": False},
                    ),
                    logger=logger,
                )

        entries = [
            entry
            for entry in _memory_entries(runner)
            if entry.get("kind") == "candidate"
        ]
        assert len(entries) == 3
        assert state.module_state["tool_outcome_memory"]["staged_count"] == 3


def test_tool_outcome_staging_bound_counts_successes_and_failures_together() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, session = _build_runner(Path(tmp), policy_api=LocalPolicyAdapter())
        state = runner._load_or_init_state("s-tool-outcome-bounded-mixed")
        logger = CanonicalEventLogger(
            session_api=session,
            session_id="s-tool-outcome-bounded-mixed",
            agent_id=runner.profile.agent_id,
        )
        executor = RunnerCommandExecutor(runner)
        success = ActionResult(
            command_id="cmd-tool-success",
            status="success",
            summary="fetched ok",
        )
        failure = ActionResult(
            command_id="cmd-tool-failure",
            status="failed",
            summary="fetch failed",
            error=ActionError(code="FETCH_FAILED", message="boom"),
        )

        for index, result in enumerate([success, failure, success, failure], start=1):
            tool_name = "web.fetch"
            command = ToolCommand(
                command_id=f"cmd-tool-mixed-{index}",
                title=f"Tool call: {tool_name}",
                tool_name=tool_name,
                args={"url": f"https://example.com/{index}"},
                success_criteria={"status": "success"},
            )
            prepared = PreparedToolDispatch(
                approved_command=command,
                original_command=command,
                command_id=command.command_id,
                tool_name=command.tool_name,
                validated_args=dict(command.args),
                session_id=state.session_id,
                trace_id=str(state.trace_id or ""),
                agent_id=runner.profile.agent_id,
                lineage={},
                permission_mode="ask",
                payload={},
            )
            normalized = result.model_copy(update={"command_id": command.command_id})
            with patch.object(
                runner,
                "_normalize_execution_result",
                return_value=(normalized, None),
            ):
                executor.finalize_tool_result(
                    state=state,
                    prepared_dispatch=prepared,
                    raw_result=RawToolResult(
                        command_id=command.command_id,
                        tool_name=command.tool_name,
                        raw_output={"ok": normalized.status == "success"},
                    ),
                    logger=logger,
                )

        entries = [
            entry
            for entry in _memory_entries(runner)
            if entry.get("kind") == "candidate"
        ]
        assert len(entries) == 3
        assert state.module_state["tool_outcome_memory"]["staged_count"] == 3


def test_permission_mode_legacy_aliases_map_to_canonical() -> None:
    assert canonical_permission_mode("default") == "ask"
    assert canonical_permission_mode("plan") == "ask"
    assert canonical_permission_mode("acceptEdits") == "auto"
    assert canonical_permission_mode("bypassPermissions") == "bypass"


def test_tool_deny_path_blocks_execution() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, session = _build_runner(Path(tmp), policy_api=LocalPolicyAdapter())
        output = runner.step(
            session_id="s-deny",
            user_input='tool rm {"path":"/tmp/x"}',
            trace_id="t-deny",
        )

        assert output.status == "waiting_user"
        assert output.message is not None

        types = [event["type"] for event in session.list_events("s-deny")]
        assert "tool.request" not in types


def test_tool_confirm_path_requests_user_input() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, session = _build_runner(Path(tmp), policy_api=ConfirmPolicyAdapter())
        output = runner.step(
            session_id="s-confirm",
            user_input='tool echo {"msg":"hi"}',
            trace_id="t-confirm",
        )

        assert output.status == "waiting_user"
        assert "confirm" in output.message.lower()

        types = [event["type"] for event in session.list_events("s-confirm")]
        assert "policy.applied" in types
        assert "tool.request" not in types


def test_confirmation_replay_uses_policy_parser_tokens() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, _session = _build_runner(
            Path(tmp), policy_api=CustomTokenConfirmationPolicyAdapter()
        )
        first = runner.step(
            session_id="s-custom-confirm",
            user_input='tool echo {"msg":"hi"}',
            trace_id="t-custom-confirm-1",
        )
        assert first.status == "waiting_user"
        assert "confirm" in (first.message or "").lower()

        second = runner.step(
            session_id="s-custom-confirm",
            user_input="absolutely",
            trace_id="t-custom-confirm-2",
        )
        assert second.status == "done"
        assert "echo tool executed" in (second.message or "").lower()


def test_confirmation_replay_creates_once_grant_and_requires_reconfirm_after_use() -> (
    None
):
    with tempfile.TemporaryDirectory() as tmp:
        ctl = PolicyCtl.with_sqlite(Path(tmp) / "policy.db")
        ctl.set_mode("enforce")
        ctl.register_risk(
            "echo.default",
            RiskSpec(
                risk_class="write",
                side_effects="local",
                reversibility="reversible",
                default_confirm=True,
            ),
        )
        runner, _session = _build_runner(
            Path(tmp), policy_api=PolicyCtlBrainAdapter(ctl)
        )
        observed_permission_modes: list[str] = []
        original_execute = runner.tool_api.execute

        def _capture_execute(
            *, command: dict[str, object], session_id: str, trace_id: str
        ) -> dict[str, object]:
            inputs = command.get("inputs")
            if isinstance(inputs, dict):
                observed_permission_modes.append(str(inputs.get("permission_mode", "")))
            return original_execute(
                command=command,
                session_id=session_id,
                trace_id=trace_id,
            )

        runner.tool_api.execute = _capture_execute  # type: ignore[assignment]
        decision_command = ToolCommand(
            title="Tool call: echo",
            tool_name="echo",
            args={"msg": "hi"},
            success_criteria={"status": "success"},
            risk_level="high",
        )

        def _decide_high_risk(**_kwargs):
            return build_seeded_act_decision(
                confidence=1.0,
                reason_code="forced_high_risk",
                act_profile="general",
                execution_target={"kind": "local"},
                command=decision_command.model_copy(deep=True),
            )

        try:
            with patch.object(runner, "_decide", side_effect=_decide_high_risk):
                first = runner.step(
                    session_id="s-runtime-confirm",
                    user_input='tool echo {"msg":"hi"}',
                    trace_id="t-runtime-confirm-1",
                )
                assert first.status == "waiting_user"
                assert "confirm" in (first.message or "").lower()
                state_after_first = runner._load_or_init_state("s-runtime-confirm")
                assert state_after_first.pending_confirmation_command is not None

                second = runner.step(
                    session_id="s-runtime-confirm",
                    user_input="yes",
                    trace_id="t-runtime-confirm-2",
                )
                assert second.status == "done"
                assert "echo tool executed" in (second.message or "").lower()

                third = runner.step(
                    session_id="s-runtime-confirm",
                    user_input='tool echo {"msg":"hi"}',
                    trace_id="t-runtime-confirm-3",
                )
                assert third.status == "waiting_user"
                assert "confirm" in (third.message or "").lower()
                assert "bypass" not in observed_permission_modes
        finally:
            ctl.close()


def test_confirmation_replay_exec_run_preserves_quoted_semicolon_command() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, _session = _build_runner(
            Path(tmp), policy_api=CustomTokenConfirmationPolicyAdapter()
        )
        observed_commands: list[dict[str, object]] = []
        original_execute = runner.tool_api.execute

        def _capture_execute(
            *, command: dict[str, object], session_id: str, trace_id: str
        ) -> dict[str, object]:
            observed_commands.append(command)
            return original_execute(
                command=command,
                session_id=session_id,
                trace_id=trace_id,
            )

        runner.tool_api.execute = _capture_execute  # type: ignore[assignment]
        replay_command = ToolCommand(
            title="Tool call: exec.run",
            tool_name="exec.run",
            args={"command": 'echo "alpha;beta"'},
            success_criteria={"status": "success"},
            risk_level="high",
        )

        def _decide_exec_run(**_kwargs):
            return build_seeded_act_decision(
                confidence=1.0,
                reason_code="forced_exec_run",
                act_profile="general",
                execution_target={"kind": "local"},
                command=replay_command.model_copy(deep=True),
            )

        with patch.object(runner, "_decide", side_effect=_decide_exec_run):
            first = runner.step(
                session_id="s-confirm-replay-exec",
                user_input="run command",
                trace_id="t-confirm-replay-exec-1",
            )
            assert first.status == "waiting_user"
            assert "confirm" in (first.message or "").lower()
            pending = runner._load_or_init_state("s-confirm-replay-exec")
            assert pending.pending_confirmation_command is not None

            second = runner.step(
                session_id="s-confirm-replay-exec",
                user_input="absolutely",
                trace_id="t-confirm-replay-exec-2",
            )
            assert second.status == "done"
            assert "executed tool 'exec.run'" in (second.message or "").lower()

        assert len(observed_commands) == 1
        assert observed_commands[0].get("tool_name") == "exec.run"
        args = observed_commands[0].get("args")
        assert isinstance(args, dict)
        assert args.get("command") == 'echo "alpha;beta"'


def test_task_schedule_confirmation_prompt_includes_instruction_and_schedule() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, _session = _build_runner(Path(tmp), policy_api=ConfirmPolicyAdapter())
        decision_command = ToolCommand(
            title="Tool call: task.schedule",
            tool_name="task.schedule",
            args={
                "instruction": "check service health",
                "schedule": {"kind": "every", "every_ms": 7_200_000},
                "name": "Service Health Check",
            },
            success_criteria={"status": "success"},
            risk_level="high",
        )

        def _decide_task_schedule(**_kwargs):
            return build_seeded_act_decision(
                confidence=1.0,
                reason_code="forced_task_schedule",
                act_profile="general",
                execution_target={"kind": "local"},
                command=decision_command.model_copy(deep=True),
            )

        with patch.object(runner, "_decide", side_effect=_decide_task_schedule):
            response = runner.step(
                session_id="s-task-confirm",
                user_input="please schedule this",
                trace_id="t-task-confirm-1",
            )

        message = str(response.message or "").lower()
        assert response.status == "waiting_user"
        assert "task.schedule will be called with" in message
        assert "instruction" in message
        assert "check service health" in message
        assert "schedule: every 2 hours" in message


def test_plan_mode_maps_to_ask_and_executes_tool() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, session = _build_runner(Path(tmp), policy_api=LocalPolicyAdapter())
        state = runner._load_or_init_state("s-plan")
        state.permission_mode = "plan"
        captured_permission_mode = {"value": ""}
        original_execute = runner.tool_api.execute

        def _capture_execute(*, command, session_id, trace_id):
            inputs = command.get("inputs", {})
            if isinstance(inputs, dict):
                captured_permission_mode["value"] = str(
                    inputs.get("permission_mode", "")
                )
            return original_execute(
                command=command,
                session_id=session_id,
                trace_id=trace_id,
            )

        runner.tool_api.execute = _capture_execute  # type: ignore[assignment]
        logger = CanonicalEventLogger(
            session_api=session,
            session_id="s-plan",
            agent_id=runner.profile.agent_id,
        )
        command = ToolCommand(
            title="Tool call: echo",
            tool_name="echo",
            args={"msg": "hi"},
            success_criteria={"status": "success"},
        )
        result, _ = execute_action(
            runner,
            state=state,
            command=command,
            logger=logger,
        )

        assert result.status == "success"
        assert captured_permission_mode["value"] == "ask"


def test_safety_preemption_returns_schema_valid_blocked_status() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, session = _build_runner(
            Path(tmp),
            policy_api=LocalPolicyAdapter(),
            safety_api=NonNormalSafetyAdapter(),
        )
        state = runner._load_or_init_state("s-safety")
        logger = CanonicalEventLogger(
            session_api=session,
            session_id="s-safety",
            agent_id=runner.profile.agent_id,
        )
        command = ToolCommand(
            title="Tool call: echo",
            tool_name="echo",
            args={"msg": "hi"},
            success_criteria={"status": "success"},
        )
        result, _ = execute_action(
            runner,
            state=state,
            command=command,
            logger=logger,
        )

        assert result.status == "blocked"
        assert result.error is not None
        assert result.error.code == "SAFETY_PREEMPTED"
        assert result.error.details.get("reason_code") == "safety_preempted"


def test_self_targeted_agent_respond_executes_locally_without_a2a_budget() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, session = _build_runner(Path(tmp), policy_api=LocalPolicyAdapter())
        state = runner._load_or_init_state("s-self-respond")
        state.budgets_remaining.a2a_calls = 0
        llm_api = MagicMock()
        llm_api.call_structured.return_value = {
            "response": "Hey there! How can I help?"
        }
        runner.llm_api = llm_api
        a2a_api = MagicMock()
        runner.a2a_api = a2a_api
        logger = CanonicalEventLogger(
            session_api=session,
            session_id="s-self-respond",
            agent_id=runner.profile.agent_id,
        )
        command = AgentCommand(
            title="Greet user",
            target_agent_id=runner.profile.agent_id,
            method="respond",
            inputs={"user_input": "hey"},
            success_criteria={"status": "success"},
            idempotency_key="self-respond-1",
        )

        result, job = execute_action(
            runner,
            state=state,
            command=command,
            logger=logger,
        )

        assert job is None
        assert result.status == "success"
        assert result.summary == "Hey there! How can I help?"
        assert state.budgets_remaining.a2a_calls == 0
        assert state.llm_calls_used == 1
        a2a_api.call.assert_not_called()
        llm_api.call_structured.assert_called_once()


def test_self_targeted_agent_tool_method_rewrites_to_local_tool_path() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, session = _build_runner(Path(tmp), policy_api=LocalPolicyAdapter())
        state = runner._load_or_init_state("s-self-tool")
        state.budgets_remaining.a2a_calls = 0
        tool_calls_before = state.budgets_remaining.tool_calls
        a2a_api = MagicMock()
        runner.a2a_api = a2a_api
        logger = CanonicalEventLogger(
            session_api=session,
            session_id="s-self-tool",
            agent_id=runner.profile.agent_id,
        )
        command = AgentCommand(
            title="Echo locally",
            target_agent_id=runner.profile.agent_id,
            method="echo",
            params={"msg": "hi"},
            success_criteria={"status": "success"},
            idempotency_key="self-tool-1",
        )

        result, job = execute_action(
            runner,
            state=state,
            command=command,
            logger=logger,
        )

        assert job is None
        assert result.status == "success"
        assert result.summary == "Echo tool executed."
        assert state.budgets_remaining.a2a_calls == 0
        assert state.budgets_remaining.tool_calls == tool_calls_before - 1
        a2a_api.call.assert_not_called()


def test_self_targeted_unknown_agent_method_fails_explicitly() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, session = _build_runner(Path(tmp), policy_api=LocalPolicyAdapter())
        state = runner._load_or_init_state("s-self-unsupported")
        state.budgets_remaining.a2a_calls = 0
        a2a_api = MagicMock()
        runner.a2a_api = a2a_api
        logger = CanonicalEventLogger(
            session_api=session,
            session_id="s-self-unsupported",
            agent_id=runner.profile.agent_id,
        )
        command = AgentCommand(
            title="Plan locally",
            target_agent_id=runner.profile.agent_id,
            method="plan",
            params={"goal": "draft itinerary"},
            success_criteria={"status": "success"},
            idempotency_key="self-unsupported-1",
        )

        result, job = execute_action(
            runner,
            state=state,
            command=command,
            logger=logger,
        )

        assert job is None
        assert result.status == "failed"
        assert result.error is not None
        assert result.error.code == "LOCAL_SELF_AGENT_METHOD_UNSUPPORTED"
        assert state.budgets_remaining.a2a_calls == 0
        a2a_api.call.assert_not_called()


def test_forced_tool_missing_args_executes_without_runtime_clarify_block() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        registry = ToolRegistry([])
        registry.add(_read_file_tool_spec())
        runner, _ = _build_runner_with_registry(
            Path(tmp),
            policy_api=LocalPolicyAdapter(),
            registry=registry,
        )
        output = runner.step(
            session_id="s-forced-missing",
            user_input="tool file.read {}",
            trace_id="t-forced-missing",
            forced_tools=["file.read"],
        )

        assert output.status == "done"
        assert output.message is not None
        assert "executed tool 'file.read'" in output.message.lower()


def test_forced_tool_missing_args_conversational_asks_user() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        registry = ToolRegistry([])
        registry.add(_read_file_tool_spec())
        runner, _ = _build_runner_with_registry(
            Path(tmp),
            policy_api=LocalPolicyAdapter(),
            registry=registry,
        )
        output = runner.step(
            session_id="s-forced-missing-convo",
            user_input="read",
            trace_id="t-forced-missing-convo",
            forced_tools=["file.read"],
        )

        assert output.status == "waiting_user"
        assert output.message is not None


def test_capability_missing_args_asks_user() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        registry = ToolRegistry([])
        registry.add(_read_file_tool_spec())
        runner, _ = _build_runner_with_registry(
            Path(tmp),
            policy_api=LocalPolicyAdapter(),
            registry=registry,
        )
        output = runner.step(
            session_id="s-capability-missing-args",
            user_input="read",
            trace_id="t-capability-missing-args",
            capability_category="file.read",
        )

        assert output.status == "waiting_user"
        assert output.message is not None


def test_capability_weather_missing_location_asks_user() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        registry = ToolRegistry([])
        registry.add(_weather_tool_spec())
        runner, _ = _build_runner_with_registry(
            Path(tmp),
            policy_api=LocalPolicyAdapter(),
            registry=registry,
        )
        output = runner.step(
            session_id="s-capability-weather-missing",
            user_input="what's weather?",
            trace_id="t-capability-weather-missing",
            capability_category="weather",
        )

        assert output.status == "waiting_user"
        assert output.message is not None


def test_capability_file_list_fallback_resolves_from_available_tools() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, _ = _build_runner(
            Path(tmp),
            policy_api=LocalPolicyAdapter(),
        )
        # When only a non-matching name is available, capability is unavailable.
        setattr(runner.tool_api, "registry", None)
        with patch.object(runner, "_available_tool_names", return_value={"list_files"}):
            tool_name, status = runner._resolve_forced_tool_name(
                forced_tools=None,
                capability_category="file.list_dir",
            )

        assert status == "capability_tool_unavailable"

        # When the canonical name is available, resolution succeeds.
        with patch.object(
            runner,
            "_collect_runtime_tool_schemas",
            return_value=[{"name": "file.list_dir"}],
        ):
            tool_name, status = runner._resolve_forced_tool_name(
                forced_tools=None,
                capability_category="file.list_dir",
            )

        assert status is None
        assert tool_name == "file.list_dir"


def test_capability_file_list_fallback_uses_builtin_when_catalog_unavailable() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, _ = _build_runner(
            Path(tmp),
            policy_api=LocalPolicyAdapter(),
        )
        # With no available tools, capability resolves as unavailable.
        setattr(runner.tool_api, "registry", None)
        with patch.object(runner, "_available_tool_names", return_value=set()):
            tool_name, status = runner._resolve_forced_tool_name(
                forced_tools=None,
                capability_category="file.list_dir",
            )

        assert status == "capability_tool_unavailable"


def test_capability_file_list_prefers_canonical_primary_over_registry_order() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, _ = _build_runner(
            Path(tmp),
            policy_api=LocalPolicyAdapter(),
        )
        runner.tool_api.registry = SimpleNamespace(
            tools_by_category=lambda _category: ["file.find", "file.list_dir"]
        )
        runner.tool_api.list_tools = lambda: [
            {"name": "file.find"},
            {"name": "file.list_dir"},
        ]

        tool_name, status = runner._resolve_forced_tool_name(
            forced_tools=None,
            capability_category="file.list_dir",
        )

        assert status is None
        assert tool_name == "file.list_dir"


def test_capability_web_fetch_fallback_requires_canonical_available_tool() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        runner, _ = _build_runner(
            Path(tmp),
            policy_api=LocalPolicyAdapter(),
        )
        setattr(runner.tool_api, "registry", None)
        with patch.object(
            runner,
            "_collect_runtime_tool_schemas",
            return_value=[{"name": "fetch.get"}],
        ):
            tool_name, status = runner._resolve_forced_tool_name(
                forced_tools=None,
                capability_category="web.fetch",
            )

        assert tool_name is None
        assert status == "capability_tool_unavailable"
