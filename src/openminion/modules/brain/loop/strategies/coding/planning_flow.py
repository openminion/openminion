import json
from typing import Any

from pydantic import ValidationError

from openminion.modules.brain.constants import (
    BRAIN_ACT_PROFILE_CODING,
    BRAIN_DECISION_ROUTE_ACT,
    BRAIN_STATE_ERROR,
    CODING_PUBLIC_TAG as _CODING_PUBLIC_TAG,
)
from openminion.modules.brain.execution.loop_contracts import (
    ExecutionContext,
    ExecutionResult,
)
from openminion.modules.brain.loop.tools import build_loop_thinking_metadata
from openminion.modules.llm.schemas import LLMResponse, Message

from .llm import DefaultCodingLLMRuntime
from .prompts import build_coding_plan_system_prompt
from .plan import CodingPlan
from .runtime import _build_error_result


class CodingPlanningMixin:
    def _initialize_plan(
        self: Any,
        ctx: ExecutionContext,
        *,
        runtime: DefaultCodingLLMRuntime,
        model: str,
    ) -> tuple[CodingPlan, LLMResponse | None] | ExecutionResult:
        goal = (
            str(
                ctx.user_input
                or ctx.state.goal
                or getattr(ctx.decision, "objective", "")
                or ""
            ).strip()
            or "Complete the coding task."
        )
        response = runtime.complete(
            messages=[
                Message(
                    role="system",
                    content=build_coding_plan_system_prompt(),
                ),
                Message(role="user", content=goal),
            ],
            tools=[],
            model=model,
            tool_choice="auto",
            metadata=build_loop_thinking_metadata(ctx, purpose="plan"),
        )
        plan = self._plan_from_response(response=response)
        if plan is not None:
            self._apply_plan_to_scratchpad(plan)
            return plan, None
        if response.ok and response.tool_calls:
            tool_plan = CodingPlan.fallback(goal)
            self._apply_plan_to_scratchpad(tool_plan)
            return tool_plan, response
        message = "Coding planner did not return a valid CodingPlan."
        return ExecutionResult(
            status=BRAIN_STATE_ERROR,
            working_state=ctx.state,
            message=message,
            action_result=_build_error_result(message, "coding_plan_invalid"),
        )

    def _plan_from_response(
        self: Any,
        *,
        response: LLMResponse,
    ) -> CodingPlan | None:
        if not response.ok:
            return None
        if response.tool_calls:
            return None
        raw_text = response.output_text.strip()
        if not raw_text:
            return None
        try:
            payload = json.loads(raw_text)
            return CodingPlan.model_validate(payload)
        except (json.JSONDecodeError, ValidationError):
            return None

    def _apply_plan_to_scratchpad(self: Any, plan: CodingPlan) -> None:
        self._loop_state.scratchpad["coding.plan_phases_executed"] = [
            plan.current_phase
        ]
        self._loop_state.scratchpad["coding.current_phase"] = plan.current_phase
        self._loop_state.scratchpad["coding.open_issues_count"] = len(plan.open_issues)
        self._loop_state.scratchpad["coding.requires_file_change"] = (
            plan.requires_file_change
        )

    def _sync_plan_telemetry(self: Any) -> None:
        if self._coding_plan is None:
            return
        self._loop_state.scratchpad["coding.current_phase"] = (
            self._coding_plan.current_phase
        )
        self._loop_state.scratchpad["coding.open_issues_count"] = len(
            self._coding_plan.open_issues
        )
        self._loop_state.scratchpad["coding.requires_file_change"] = (
            self._coding_plan.requires_file_change
        )

    def _emit_phase_status(self: Any, ctx: ExecutionContext) -> None:
        if self._coding_plan is None:
            return
        ctx.emit_status(
            source_phase="coding.plan",
            detail_text=f"{_CODING_PUBLIC_TAG} phase: {self._coding_plan.current_phase}",
            mode=BRAIN_DECISION_ROUTE_ACT,
            mode_state=self._coding_plan.current_phase,
            payload={
                "act.profile": BRAIN_ACT_PROFILE_CODING,
                "coding.current_phase": self._coding_plan.current_phase,
                "coding.plan_phases_executed": list(
                    self._loop_state.scratchpad.get("coding.plan_phases_executed", [])
                    or []
                ),
                **self._resume_marker_payload(ctx),
            },
        )

    def _append_phase_instruction(self: Any) -> None:
        if self._coding_plan is None:
            return
        phase = self._coding_plan.current_phase_entry()
        if phase.name == "verify":
            instruction = (
                f"Continue the coding task in phase '{phase.name}'. "
                f"Goal: {self._coding_plan.goal}. "
                "Verification is read-only: do not modify files or apply patches. "
                "Use `file.read` or `file.read_range` first for readback proof and "
                "use `exec.run` only when shell verification is actually required. "
                f"Open issues: {', '.join(self._coding_plan.open_issues) if self._coding_plan.open_issues else 'none'}."
            )
        else:
            write_requirement = (
                " This phase requires a mutating implementation tool before any "
                "final answer: call `file.write` or `code.patch` with concrete "
                "path/content, then verify with `file.read` or `file.read_range`."
                if (
                    phase.name == "implement"
                    and self._coding_plan.requires_file_change
                    and not self._has_successful_mutating_file_result()
                )
                else ""
            )
            instruction = (
                f"Continue the coding task in phase '{phase.name}'. "
                f"Goal: {self._coding_plan.goal}. "
                f"Steps: {', '.join(phase.steps) if phase.steps else 'advance this phase'}. "
                f"{write_requirement} "
                f"Open issues: {', '.join(self._coding_plan.open_issues) if self._coding_plan.open_issues else 'none'}."
            )
        self._loop_state.messages.append(
            Message(
                role="user",
                content=instruction,
            )
        )
