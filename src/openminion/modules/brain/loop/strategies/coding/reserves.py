from pathlib import Path
from typing import Any

from openminion.modules.brain.constants import (
    BRAIN_ACT_PROFILE_CODING,
    BRAIN_DECISION_ROUTE_ACT,
    CODING_PUBLIC_TAG as _CODING_PUBLIC_TAG,
)
from openminion.modules.brain.execution.loop_contracts import ExecutionContext
from openminion.modules.brain.loop.tools import (
    ADAPTIVE_TERM_BUDGET_EXHAUSTED,
    ADAPTIVE_TERM_CIRCULAR_PATTERN,
    ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS,
    AdaptiveToolLoopOutcome,
)
from openminion.modules.brain.loop.tools.iteration.helpers import _MUTATING_FILE_TOOLS
from openminion.modules.brain.schemas import ArtifactRef
from openminion.modules.llm.schemas import Message

from .contracts import CODING_TERM_FINAL_TEXT, CODING_TERM_TOOL_FAILURE

_CODING_VERIFY_ALLOWED_TOOLS: frozenset[str] = frozenset(
    {
        "file.list_dir",
        "file.read",
        "file.read_range",
        "file.find",
        "code.grep",
        "code.repo_index",
        "code.repo_map",
        "code.symbol_find",
        "exec.run",
        "exec.poll",
        "exec.list",
    }
)

_CODING_VERIFY_RESERVE_TOOLS: frozenset[str] = frozenset(
    {
        "file.read",
        "file.read_range",
        "exec.run",
    }
)

_RESERVE_TERMINATION_REASONS = frozenset(
    {
        ADAPTIVE_TERM_BUDGET_EXHAUSTED,
        ADAPTIVE_TERM_CIRCULAR_PATTERN,
        ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS,
    }
)


def _mutating_result_path(item: dict[str, Any]) -> str | None:
    mappings = [item]
    if isinstance(item.get("data"), dict):
        mappings.append(item["data"])
    for mapping in mappings:
        for key in ("path", "file_path", "final_path", "target", "target_path"):
            value = mapping.get(key)
            if value is None:
                continue
            rendered = str(value).strip()
            if rendered:
                return rendered
    return None


def _workspace_roots_for_mutating_result(runner: Any) -> tuple[Path, ...]:
    scratchpad = getattr(getattr(runner, "_loop_state", None), "scratchpad", {}) or {}
    roots: list[Path] = []
    for key in (
        "coding.workspace_root",
        "coding.cwd",
        "workspace_root",
        "cwd",
        "tool.workspace_root",
    ):
        value = scratchpad.get(key)
        if value is None:
            continue
        rendered = str(value).strip()
        if not rendered:
            continue
        root = Path(rendered).expanduser()
        if root not in roots:
            roots.append(root)
    return tuple(roots)


class CodingReserveMixin:
    def _maybe_continue_with_verification_reserve(
        self: Any,
        ctx: ExecutionContext,
        *,
        outcome: AdaptiveToolLoopOutcome,
    ) -> bool:
        if outcome.termination_reason not in _RESERVE_TERMINATION_REASONS:
            return False
        if self._coding_plan is None:
            return False
        current_phase = self._coding_plan.current_phase
        next_phase = self._coding_plan.next_phase_name()
        if current_phase == "implement" and next_phase == "verify":
            pass
        elif current_phase == "verify" and next_phase is None:
            pass
        else:
            return False
        if bool(self._loop_state.scratchpad.get("coding.verification_reserve_used")):
            return False
        if self._has_verifier_candidate():
            return False
        if not self._has_successful_mutating_file_result():
            return False
        return self._queue_verification_reserve(
            ctx,
            restore_answer_only_state=True,
            ensure_tool_budget=getattr(ctx.state, "budgets_remaining", None)
            is not None,
        )

    def _maybe_continue_with_verify_closeout_reserve(
        self: Any,
        ctx: ExecutionContext,
        *,
        outcome: AdaptiveToolLoopOutcome,
    ) -> bool:
        if self._coding_plan is None:
            return False
        if self._coding_plan.current_phase != "implement":
            return False
        if self._coding_plan.next_phase_name() != "verify":
            return False
        if bool(self._loop_state.scratchpad.get("coding.final_answer_reserve_used")):
            return False
        if not self._has_verifier_candidate():
            return False
        if not self._has_successful_mutating_file_result():
            return False
        if outcome.termination_reason not in _RESERVE_TERMINATION_REASONS:
            return False

        self._coding_plan.advance_to_next_phase(output=str(outcome.final_text or ""))
        executed = list(
            self._loop_state.scratchpad.get("coding.plan_phases_executed", []) or []
        )
        if self._coding_plan.current_phase not in executed:
            executed.append(self._coding_plan.current_phase)
        self._loop_state.scratchpad["coding.plan_phases_executed"] = executed
        self._sync_plan_telemetry()
        self._emit_phase_status(ctx)
        return self._maybe_continue_with_final_answer_reserve(ctx, outcome=outcome)

    def _maybe_continue_with_final_answer_reserve(
        self: Any,
        ctx: ExecutionContext,
        *,
        outcome: AdaptiveToolLoopOutcome,
    ) -> bool:
        if self._coding_plan is None:
            return False
        if self._coding_plan.current_phase != "verify":
            return False
        if self._coding_plan.next_phase_name() is not None:
            return False
        if bool(self._loop_state.scratchpad.get("coding.final_answer_reserve_used")):
            return False
        if not self._has_verifier_candidate():
            return False
        if not self._should_reserve_final_answer(outcome):
            return False
        return self._queue_final_answer_reserve(
            ctx,
            restore_answer_only_state=True,
        )

    def _should_reserve_final_answer(
        self: Any,
        outcome: AdaptiveToolLoopOutcome,
    ) -> bool:
        if outcome.termination_reason in _RESERVE_TERMINATION_REASONS:
            return True
        if (
            outcome.termination_reason == CODING_TERM_TOOL_FAILURE
            and self._is_verifier_incomplete_failure(outcome)
        ):
            return True
        if outcome.termination_reason != CODING_TERM_FINAL_TEXT:
            return False
        return not bool(outcome.final_text.strip())

    def _is_verifier_incomplete_failure(
        self: Any,
        outcome: AdaptiveToolLoopOutcome,
    ) -> bool:
        action_result = outcome.action_result
        return bool(
            action_result is not None
            and action_result.error is not None
            and action_result.error.code == "coding_verifier_incomplete"
        )

    def _has_verifier_candidate(self: Any) -> bool:
        if self._loop_state.scratchpad.get("coding.pending_verifier_sessions"):
            return False
        if (
            self._coding_plan is not None
            and getattr(self._coding_plan, "verifier_goal", None) is not None
        ):
            return bool(self._bound_verifier_candidates())
        candidate = (
            self._last_verifier_candidate_payload
            or self._loop_state.scratchpad.get("coding.last_verifier_candidate")
        )
        return isinstance(candidate, dict) and bool(candidate)

    def _has_successful_mutating_file_result(self: Any) -> bool:
        for item in self._loop_state.scratchpad.get("adaptive.tool_results", []) or []:
            if not isinstance(item, dict):
                continue
            tool_name = str(item.get("tool_name", "") or "").strip()
            if tool_name not in _MUTATING_FILE_TOOLS:
                continue
            if bool(item.get("ok")) and self._mutating_result_has_durable_path(item):
                return True
        return False

    def _mutating_result_has_durable_path(self: Any, item: dict[str, Any]) -> bool:
        raw_path = _mutating_result_path(item)
        if raw_path is None:
            return True
        path = Path(raw_path).expanduser()
        if path.is_absolute():
            return path.exists()
        roots = _workspace_roots_for_mutating_result(self)
        if not roots:
            return True
        return any((root / path).exists() for root in roots)

    def _successful_mutating_artifact_refs(self: Any) -> list[ArtifactRef]:
        refs: list[ArtifactRef] = []
        seen: set[str] = set()
        for item in self._loop_state.scratchpad.get("adaptive.tool_results", []) or []:
            if not isinstance(item, dict) or not bool(item.get("ok")):
                continue
            if str(item.get("tool_name", "") or "").strip() not in _MUTATING_FILE_TOOLS:
                continue
            path = _mutating_result_path(item)
            if (
                not path
                or path in seen
                or not self._mutating_result_has_durable_path(item)
            ):
                continue
            seen.add(path)
            refs.append(ArtifactRef(ref=path))
        return refs

    def _allowed_tools_for_current_phase(
        self: Any,
        *,
        default_allowed_tools: frozenset[str],
    ) -> frozenset[str]:
        if self._coding_plan is None:
            return default_allowed_tools
        if self._coding_plan.current_phase != "verify":
            return default_allowed_tools
        return frozenset(
            tool
            for tool in default_allowed_tools
            if tool in _CODING_VERIFY_ALLOWED_TOOLS
        )

    def _verification_reserve_allowed_tools(self: Any) -> frozenset[str]:
        return _CODING_VERIFY_RESERVE_TOOLS

    def _queue_verification_reserve(
        self: Any,
        ctx: ExecutionContext,
        *,
        restore_answer_only_state: bool,
        ensure_tool_budget: bool,
    ) -> bool:
        self._loop_state.scratchpad.pop("coding.pending_continue", None)
        self._restore_answer_only_state_if_needed(restore_answer_only_state)
        budgets = getattr(ctx.state, "budgets_remaining", None)
        if ensure_tool_budget and budgets is not None:
            budgets.tool_calls = max(int(getattr(budgets, "tool_calls", 0) or 0), 1)
        self._loop_state.seen_signatures = []
        self._loop_state.termination_reason = ""
        self._loop_state.scratchpad["coding.verification_reserve_used"] = True
        pending_sessions = tuple(
            str(session_id)
            for session_id in dict(
                self._loop_state.scratchpad.get("coding.pending_verifier_sessions", {})
                or {}
            )
        )
        if ensure_tool_budget and budgets is not None and pending_sessions:
            budgets.tool_calls = max(
                int(getattr(budgets, "tool_calls", 0) or 0),
                len(pending_sessions),
            )
        instruction = (
            "Poll each pending verification process now with `exec.poll` using "
            f"these session IDs: {', '.join(pending_sessions)}. Do not start "
            "another command. Each poll inherits its original verification target. "
            "Then continue with the terminal verification results."
            if pending_sessions
            else (
                "Use the reserved final tool step for verification only. "
                "Verification is read-only. Run exactly one verification "
                "readback step now, preferring `file.read` when a structured "
                "reader can prove the change and using `exec.run` only when "
                "shell verification is actually needed. Bind the call to one "
                "listed verification target, then continue with the verified answer."
            )
        )
        self._loop_state.messages.append(
            Message(
                role="user",
                content=instruction,
            )
        )
        ctx.emit_status(
            source_phase="coding.loop",
            detail_text=f"{_CODING_PUBLIC_TAG} reserved verification step",
            mode=BRAIN_DECISION_ROUTE_ACT,
            mode_state="verification_reserve",
            payload={
                "act.profile": BRAIN_ACT_PROFILE_CODING,
                "coding.verification_reserve_used": True,
            },
        )
        return True

    def _queue_final_answer_reserve(
        self: Any,
        ctx: ExecutionContext,
        *,
        restore_answer_only_state: bool,
    ) -> bool:
        self._loop_state.scratchpad.pop("coding.pending_continue", None)
        self._restore_answer_only_state_if_needed(restore_answer_only_state)
        self._loop_state.seen_signatures = []
        self._loop_state.termination_reason = ""
        self._loop_state.scratchpad["coding.final_answer_reserve_used"] = True
        self._loop_state.messages.append(
            Message(
                role="user",
                content=(
                    "Use the reserved final response step now. Do not call any tools. "
                    "Base the answer on the files already written and the most recent "
                    "verification/readback evidence. Satisfy any explicit final-output "
                    "labels or result markers the user requested, include validation "
                    "status, and return only the final answer."
                ),
            )
        )
        ctx.emit_status(
            source_phase="coding.loop",
            detail_text=f"{_CODING_PUBLIC_TAG} reserved final answer step",
            mode=BRAIN_DECISION_ROUTE_ACT,
            mode_state="final_answer_reserve",
            payload={
                "act.profile": BRAIN_ACT_PROFILE_CODING,
                "coding.final_answer_reserve_used": True,
            },
        )
        return True

    def _restore_answer_only_state_if_needed(self: Any, enabled: bool) -> None:
        if not enabled:
            return
        restore_index = self._loop_state.scratchpad.pop(
            "budget_answer_only_restore_index",
            None,
        )
        self._loop_state.scratchpad.pop(
            "budget_answer_only_finalization_rejected_text",
            None,
        )
        self._loop_state.scratchpad.pop(
            "budget_answer_only_finalization_forced",
            None,
        )
        if isinstance(restore_index, int) and 0 <= restore_index <= len(
            self._loop_state.messages
        ):
            self._loop_state.messages = list(self._loop_state.messages[:restore_index])
