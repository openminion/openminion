from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from openminion.modules.brain.schemas import (
    ActionResult,
    BudgetCounters,
    WorkingState,
    new_uuid,
)
from openminion.modules.brain.loop.tools import (
    ADAPTIVE_TERM_FINAL_TEXT,
    AdaptiveToolLoopProfile,
    run_adaptive_tool_loop,
)
from openminion.modules.brain.loop.tools.contracts import AdaptiveToolLoopState
from openminion.modules.brain.tools.executor import CommandExecutionOutcome
from openminion.modules.llm.schemas import LLMResponse, Message, ToolCall, ToolSpec


@dataclass
class _RecordingRuntime:
    responses: list[LLMResponse] = field(default_factory=list)
    calls: list[list[Any]] = field(default_factory=list)
    _index: int = 0

    def complete(
        self,
        *,
        messages,
        tools,
        model,
        tool_choice="auto",
        max_output_tokens=None,
        metadata=None,
    ):
        self.calls.append(list(messages))
        if self._index < len(self.responses):
            response = self.responses[self._index]
            self._index += 1
            return response
        return LLMResponse(
            ok=True,
            provider="fake",
            model=model,
            output_text="final answer",
        )


@dataclass
class _LoopContext:
    state: WorkingState
    outcomes: list[CommandExecutionOutcome] = field(default_factory=list)
    statuses: list[dict[str, Any]] = field(default_factory=list)
    _index: int = 0

    def execute_command(self, *, command, include_reflect: bool = False):
        del include_reflect
        outcome = self.outcomes[self._index]
        self._index += 1
        return outcome

    def emit_status(self, **kwargs) -> None:
        self.statuses.append(dict(kwargs))


def _working_state() -> WorkingState:
    return WorkingState(
        session_id="s-scr13",
        agent_id="agent-scr13",
        budgets_remaining=BudgetCounters(
            ticks=10,
            tool_calls=10,
            a2a_calls=0,
            tokens=50000,
            time_ms=120000,
        ),
        llm_calls_max=10,
    )


def _tool_specs(*names: str) -> list[ToolSpec]:
    return [
        ToolSpec(
            name=name,
            description=name,
            input_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": True,
            },
        )
        for name in names
    ]


def _error_result(summary: str = "Permission denied: cannot read file") -> ActionResult:
    return ActionResult(
        command_id=new_uuid(),
        status="failed",
        summary=summary,
    )


def _ok_result(summary: str = "file content here") -> ActionResult:
    return ActionResult(command_id=new_uuid(), status="success", summary=summary)


def _profile_with_correction(
    *, max_macro_corrections: int = 0
) -> AdaptiveToolLoopProfile:
    return AdaptiveToolLoopProfile(
        profile_name="scr13_profile",
        mode_name="scr13_mode",
        allowed_tools=frozenset({"file.read"}),
        max_iterations=5,
        max_macro_corrections=max_macro_corrections,
        macro_correction_cooldown=1,
        allow_llm_recovery_after_tool_failure=True,
    )


class TestLayer1EnrichmentFullPath:
    def test_tool_error_triggers_layer1_enrichment_message_in_transcript(self):
        responses = [
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="c1", name="file.read", arguments={"path": "secret.txt"}
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="I couldn't read the file due to permission issues.",
                finish_reason="stop",
            ),
        ]
        runtime = _RecordingRuntime(responses=responses)
        loop_ctx = _LoopContext(
            state=_working_state(),
            outcomes=[
                CommandExecutionOutcome(
                    approved_command=SimpleNamespace(),
                    action_result=_error_result("Permission denied: cannot read file"),
                ),
            ],
        )

        outcome = run_adaptive_tool_loop(
            loop_ctx,
            profile=_profile_with_correction(),
            runtime=runtime,
            model="fake-model",
            initial_messages=[Message(role="user", content="Read secret.txt for me")],
            tool_specs=_tool_specs("file.read"),
        )

        assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT

        assert outcome.state.scratchpad.get("micro_correction_count", 0) >= 1

        assert len(runtime.calls) >= 2, "Expected at least 2 LLM calls"
        second_call_messages = runtime.calls[1]
        enrichment_found = any(
            "[system]" in str(getattr(m, "content", "") or "")
            and "file.read" in str(getattr(m, "content", "") or "")
            and "anomalous" in str(getattr(m, "content", "") or "")
            for m in second_call_messages
        )
        assert enrichment_found, (
            "Expected enrichment message in second LLM call messages. "
            f"Messages seen: {[getattr(m, 'content', '') for m in second_call_messages]}"
        )

    def test_telemetry_shows_micro_corrections_after_tool_error(self):
        responses = [
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="c2", name="file.read", arguments={"path": "a.py"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="All done.",
                finish_reason="stop",
            ),
        ]
        runtime = _RecordingRuntime(responses=responses)
        loop_ctx = _LoopContext(
            state=_working_state(),
            outcomes=[
                CommandExecutionOutcome(
                    approved_command=SimpleNamespace(),
                    action_result=_error_result("file not found"),
                ),
            ],
        )

        outcome = run_adaptive_tool_loop(
            loop_ctx,
            profile=_profile_with_correction(),
            runtime=runtime,
            model="fake-model",
            initial_messages=[Message(role="user", content="read a.py")],
            tool_specs=_tool_specs("file.read"),
        )

        assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT

        all_payloads = [
            s.get("payload", {}) for s in loop_ctx.statuses if s.get("payload")
        ]
        micro_vals = [
            p["loop.micro_corrections"]
            for p in all_payloads
            if "loop.micro_corrections" in p
        ]
        assert len(micro_vals) > 0, "No status events had loop.micro_corrections field"
        assert any(v >= 1 for v in micro_vals), (
            f"Expected at least one status with micro_corrections>=1, got {micro_vals}"
        )

    def test_successful_tool_call_does_not_trigger_layer1(self):
        responses = [
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(id="c3", name="file.read", arguments={"path": "clean.py"})
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="Here is the file content.",
                finish_reason="stop",
            ),
        ]
        runtime = _RecordingRuntime(responses=responses)
        loop_ctx = _LoopContext(
            state=_working_state(),
            outcomes=[
                CommandExecutionOutcome(
                    approved_command=SimpleNamespace(),
                    action_result=_ok_result("def main(): pass"),
                ),
            ],
        )

        outcome = run_adaptive_tool_loop(
            loop_ctx,
            profile=_profile_with_correction(),
            runtime=runtime,
            model="fake-model",
            initial_messages=[Message(role="user", content="read clean.py")],
            tool_specs=_tool_specs("file.read"),
        )

        assert outcome.termination_reason == ADAPTIVE_TERM_FINAL_TEXT
        assert outcome.state.scratchpad.get("micro_correction_count", 0) == 0

        if len(runtime.calls) >= 2:
            second_call_messages = runtime.calls[1]
            enrichment_found = any(
                "anomalous" in str(getattr(m, "content", "") or "")
                for m in second_call_messages
            )
            assert not enrichment_found, (
                "Enrichment message injected for a successful tool call — unexpected"
            )


class TestLayer1ToLayer2EscalationPath:
    def test_repeated_anomaly_escalates_to_layer2_when_budget_allows(self):
        responses = [
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="file.read",
                        arguments={"path": "locked.txt"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text='{"diagnosis": "file is locked", "correction_type": "accept_partial", "confidence": 0.7}',
                finish_reason="stop",
            ),
        ]
        runtime = _RecordingRuntime(responses=responses)

        loop_ctx = _LoopContext(
            state=_working_state(),
            outcomes=[
                CommandExecutionOutcome(
                    approved_command=SimpleNamespace(),
                    action_result=_error_result("file is locked"),
                ),
            ],
        )

        from openminion.modules.brain.loop.tools.contracts import (
            canonical_tool_call_signature,
        )
        from openminion.modules.llm.schemas import ToolCall as _TC

        _pre_sig = canonical_tool_call_signature(
            _TC(id="cx", name="file.read", arguments={"path": "locked.txt"})
        )
        initial_state = AdaptiveToolLoopState(
            messages=[Message(role="user", content="read locked.txt")],
            scratchpad={
                "last_anomalous_signature": _pre_sig,
            },
        )

        profile = _profile_with_correction(max_macro_corrections=2)
        outcome = run_adaptive_tool_loop(
            loop_ctx,
            profile=profile,
            runtime=runtime,
            model="fake-model",
            initial_messages=[Message(role="user", content="read locked.txt")],
            tool_specs=_tool_specs("file.read"),
            initial_state=initial_state,
        )

        macro_count = outcome.state.scratchpad.get("macro_correction_count", 0)
        assert macro_count >= 1, (
            f"Expected macro_correction_count >= 1, got {macro_count}"
        )

        history = outcome.state.scratchpad.get("correction_history", [])
        assert len(history) >= 1, "Expected at least one correction record in history"

        from openminion.modules.brain.loop.tools.status import loop_correction_payload

        final_payload = loop_correction_payload(outcome.state.scratchpad)
        assert final_payload["loop.macro_corrections"] >= 1, (
            f"Expected loop_correction_payload to show macro_corrections>=1, "
            f"got {final_payload}"
        )

    def test_correction_type_appears_in_telemetry_type_counts(self):
        responses = [
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="file.read",
                        arguments={"path": "locked.txt"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text=(
                    '{"diagnosis": "wrong path", "correction_type": "retry_same",'
                    ' "confidence": 0.8}'
                ),
                finish_reason="stop",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="",
                tool_calls=[
                    ToolCall(
                        id="c2",
                        name="file.read",
                        arguments={"path": "locked.txt"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LLMResponse(
                ok=True,
                provider="fake",
                model="fake-model",
                output_text="done",
                finish_reason="stop",
            ),
        ]
        runtime = _RecordingRuntime(responses=responses)

        loop_ctx = _LoopContext(
            state=_working_state(),
            outcomes=[
                CommandExecutionOutcome(
                    approved_command=SimpleNamespace(),
                    action_result=_error_result("locked"),
                ),
                CommandExecutionOutcome(
                    approved_command=SimpleNamespace(),
                    action_result=_error_result("locked again"),
                ),
                CommandExecutionOutcome(
                    approved_command=SimpleNamespace(),
                    action_result=_ok_result("content"),
                ),
            ],
        )

        profile = _profile_with_correction(max_macro_corrections=2)
        outcome = run_adaptive_tool_loop(
            loop_ctx,
            profile=profile,
            runtime=runtime,
            model="fake-model",
            initial_messages=[Message(role="user", content="read locked.txt")],
            tool_specs=_tool_specs("file.read"),
        )

        history = outcome.state.scratchpad.get("correction_history", [])
        if history:
            from openminion.modules.brain.loop.tools.status import (
                loop_correction_payload,
            )

            payload = loop_correction_payload(outcome.state.scratchpad)
            assert len(payload["loop.correction_types"]) >= 1
