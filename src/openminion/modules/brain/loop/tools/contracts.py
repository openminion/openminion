from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from openminion.modules.brain.schemas import (
    ActionResult,
    AdaptiveBudgetConfig,
    Command,
    JobHandle,
    ReflectReport,
    WorkingState,
)
from openminion.modules.brain.runtime.improvement.contracts import (
    SelfImprovementPolicy,
)
from openminion.modules.llm.schemas import LLMResponse, Message, ToolCall, ToolSpec


ADAPTIVE_TOOL_EXPOSURE_EXPLICIT_ALLOWLIST = "explicit_allowlist"
ADAPTIVE_TOOL_EXPOSURE_RUNTIME_EXPOSED = "runtime_exposed"
ADAPTIVE_TOOL_EXPOSURE_POLICIES = frozenset(
    {
        ADAPTIVE_TOOL_EXPOSURE_EXPLICIT_ALLOWLIST,
        ADAPTIVE_TOOL_EXPOSURE_RUNTIME_EXPOSED,
    }
)

ADAPTIVE_CLOSURE_MODE_OWNED = "mode_owned"
ADAPTIVE_CLOSURE_ENGINE_SINGLE_PASS = "engine_single_pass"
ADAPTIVE_CLOSURE_POLICIES = frozenset(
    {
        ADAPTIVE_CLOSURE_MODE_OWNED,
        ADAPTIVE_CLOSURE_ENGINE_SINGLE_PASS,
    }
)

ADAPTIVE_TERM_FINAL_TEXT = "final_text"
ADAPTIVE_TERM_CONFIDENT_COMPLETE = "confident_complete"
ADAPTIVE_TERM_REQUESTED_TOOL_NOT_EXECUTED = "requested_tool_not_executed"
ADAPTIVE_TERM_FINALIZATION_BLOCKED = "finalization_blocked"
ADAPTIVE_TERM_FINALIZATION_INCOMPLETE = "finalization_incomplete"
ADAPTIVE_TERM_FINALIZATION_CONTRACT_MISSING = "finalization_contract_missing"
ADAPTIVE_TERM_NEEDS_USER = "needs_user"
ADAPTIVE_TERM_JOB_PENDING = "job_pending"
ADAPTIVE_TERM_BUDGET_EXHAUSTED = "budget_exhausted"
ADAPTIVE_TERM_ITERATION_CAP = "iteration_cap"
ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS = "duplicate_tool_calls"
ADAPTIVE_TERM_DIRECT_TOOL_CLOSURE_FAILED = "direct_tool_closure_failed"
ADAPTIVE_TERM_DISALLOWED_TOOL = "disallowed_tool"
ADAPTIVE_TERM_LLM_ERROR = "llm_error"
ADAPTIVE_TERM_TOOL_FAILURE_NO_RECOVERY = "tool_failure_no_recovery"
ADAPTIVE_TERM_DECOMPOSE_REQUESTED = "decompose_requested"
ADAPTIVE_TERM_DECOMPOSE_INVALID = "decompose_invalid"
_SEMANTIC_EMPTY_ARGUMENT_TOKENS = frozenset({"none", "null"})
ADAPTIVE_TERM_CORRECTION_BUDGET_EXHAUSTED = "correction_budget_exhausted"
ADAPTIVE_TERM_CIRCULAR_PATTERN = "circular_pattern"

ADAPTIVE_TERMINATION_REASONS = frozenset(
    {
        ADAPTIVE_TERM_FINAL_TEXT,
        ADAPTIVE_TERM_CONFIDENT_COMPLETE,
        ADAPTIVE_TERM_REQUESTED_TOOL_NOT_EXECUTED,
        ADAPTIVE_TERM_FINALIZATION_BLOCKED,
        ADAPTIVE_TERM_FINALIZATION_INCOMPLETE,
        ADAPTIVE_TERM_FINALIZATION_CONTRACT_MISSING,
        ADAPTIVE_TERM_NEEDS_USER,
        ADAPTIVE_TERM_JOB_PENDING,
        ADAPTIVE_TERM_BUDGET_EXHAUSTED,
        ADAPTIVE_TERM_ITERATION_CAP,
        ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS,
        ADAPTIVE_TERM_DIRECT_TOOL_CLOSURE_FAILED,
        ADAPTIVE_TERM_DISALLOWED_TOOL,
        ADAPTIVE_TERM_LLM_ERROR,
        ADAPTIVE_TERM_TOOL_FAILURE_NO_RECOVERY,
        ADAPTIVE_TERM_DECOMPOSE_REQUESTED,
        ADAPTIVE_TERM_DECOMPOSE_INVALID,
        ADAPTIVE_TERM_CORRECTION_BUDGET_EXHAUSTED,
        ADAPTIVE_TERM_CIRCULAR_PATTERN,
    }
)

_SUPPORTED_REQUEST_OVERRIDE_KEYS = frozenset({"max_output_tokens", "metadata"})


class AdaptiveToolLoopError(ValueError):
    """Raised when the shared adaptive-loop contract is malformed."""


class AdaptiveToolLoopRuntimeUnavailableError(RuntimeError):
    """Raised when the local raw-LLM runtime cannot be obtained."""


@dataclass(slots=True)
class CommandExecutionOutcome:
    approved_command: Command
    action_result: ActionResult | None = None
    job: JobHandle | None = None
    reflect_report: ReflectReport | None = None


@dataclass(frozen=True, slots=True)
class PreparedToolDispatch:
    approved_command: Command
    original_command: Command
    command_id: str
    tool_name: str
    validated_args: dict[str, Any]
    session_id: str
    trace_id: str
    agent_id: str
    lineage: dict[str, Any]
    permission_mode: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RawToolResult:
    command_id: str
    tool_name: str
    raw_output: Any
    timing: dict[str, Any] = field(default_factory=dict)
    artifacts: tuple[Any, ...] = field(default_factory=tuple)
    error_payload: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class PrepareOutcome:
    approved_command: Command
    original_command: Command
    command_id: str
    tool_name: str
    disposition: str
    action_result: ActionResult


@dataclass(frozen=True, slots=True)
class DirectToolTurnContext:
    requested_tool_names: tuple[str, ...]
    requested_batch_signature: str
    requested_calls: tuple[ToolCall, ...] = field(default_factory=tuple)
    match_by_name_only: bool = False


@runtime_checkable
class AdaptiveToolLoopLLMRuntime(Protocol):
    def complete(
        self,
        *,
        messages: list[Message],
        tools: list[ToolSpec],
        model: str,
        tool_choice: str | dict[str, Any] = "auto",
        max_output_tokens: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LLMResponse: ...


@runtime_checkable
class AdaptiveToolLoopContext(Protocol):
    state: WorkingState

    def execute_command(
        self,
        *,
        command: Any,
        include_reflect: bool = False,
    ) -> CommandExecutionOutcome: ...

    def prepare_tool_dispatch(
        self,
        *,
        command: Any,
        include_reflect: bool = False,
    ) -> PreparedToolDispatch | PrepareOutcome: ...

    def execute_prepared_tool_dispatch(
        self,
        *,
        prepared_dispatch: PreparedToolDispatch,
    ) -> RawToolResult: ...

    def finalize_tool_result(
        self,
        *,
        prepared_dispatch: PreparedToolDispatch,
        raw_result: RawToolResult,
    ) -> CommandExecutionOutcome: ...

    def finalize_prepare_outcome(
        self,
        *,
        prepare_outcome: PrepareOutcome,
    ) -> CommandExecutionOutcome: ...

    def emit_status(
        self,
        *,
        source_phase: str | None = None,
        source_event: str | None = None,
        payload: dict[str, Any] | None = None,
        runtime_status: str | None = None,
        detail_text: str | None = None,
        terminal: bool | None = None,
        mode: str | None = None,
        mode_state: str | None = None,
        mode_label: str | None = None,
        mode_step_index: int | None = None,
        mode_step_total: int | None = None,
    ) -> None: ...

    def advance_after_action(
        self,
        *,
        action_result: ActionResult,
        force_replan: bool = False,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class AdaptiveToolLoopProfile:
    profile_name: str
    mode_name: str
    tool_exposure_policy: Literal[
        "explicit_allowlist",
        "runtime_exposed",
    ] = ADAPTIVE_TOOL_EXPOSURE_EXPLICIT_ALLOWLIST
    allowed_tools: frozenset[str] | None = None
    allow_plan_tool: bool = True
    max_iterations: int = 1
    max_tool_calls_per_loop: int | None = None
    max_llm_calls_per_loop: int | None = None
    reflection_policy: Literal["never", "always", "anomaly"] = "never"
    reflection_anomaly_threshold: float = 0.6
    max_macro_corrections: int = 0
    macro_correction_cooldown: int = 2
    reflection_model: str | None = None
    allow_llm_recovery_after_tool_failure: bool = True
    stop_on_needs_user: bool = True
    stop_on_job_pending: bool = True
    tool_choice: str | dict[str, Any] = "auto"
    llm_request_overrides: Mapping[str, Any] = field(default_factory=dict)
    final_closure_policy: Literal[
        "mode_owned",
        "engine_single_pass",
    ] = ADAPTIVE_CLOSURE_MODE_OWNED
    budget_conserve_threshold: float = 0.20
    speculative_prefetch: bool = False
    speculative_prefetch_threshold: float = 0.8
    provider_parallel_tool_capacity: int = 1
    use_memory_templates: bool = False
    adaptive_budget_config: AdaptiveBudgetConfig | None = None
    self_improvement_policy: SelfImprovementPolicy | None = None

    def __post_init__(self) -> None:
        if str(self.profile_name or "").strip() == "":
            raise AdaptiveToolLoopError("profile_name is required")
        if str(self.mode_name or "").strip() == "":
            raise AdaptiveToolLoopError("mode_name is required")
        if self.tool_exposure_policy not in ADAPTIVE_TOOL_EXPOSURE_POLICIES:
            raise AdaptiveToolLoopError(
                f"Unsupported tool_exposure_policy: {self.tool_exposure_policy!r}"
            )
        if self.final_closure_policy not in ADAPTIVE_CLOSURE_POLICIES:
            raise AdaptiveToolLoopError(
                f"Unsupported final_closure_policy: {self.final_closure_policy!r}"
            )
        if int(self.max_iterations or 0) < 1:
            raise AdaptiveToolLoopError("max_iterations must be >= 1")
        if (
            self.max_tool_calls_per_loop is not None
            and int(self.max_tool_calls_per_loop or 0) < 1
        ):
            raise AdaptiveToolLoopError("max_tool_calls_per_loop must be >= 1")
        if (
            self.max_llm_calls_per_loop is not None
            and int(self.max_llm_calls_per_loop or 0) < 1
        ):
            raise AdaptiveToolLoopError("max_llm_calls_per_loop must be >= 1")
        if (
            self.tool_exposure_policy == ADAPTIVE_TOOL_EXPOSURE_RUNTIME_EXPOSED
            and self.allowed_tools is not None
        ):
            raise AdaptiveToolLoopError(
                "runtime_exposed policy must not also provide allowed_tools"
            )
        if (
            self.tool_exposure_policy == ADAPTIVE_TOOL_EXPOSURE_EXPLICIT_ALLOWLIST
            and not self.allowed_tools
            and self.tool_choice != "none"
        ):
            raise AdaptiveToolLoopError(
                "explicit_allowlist policy requires non-empty allowed_tools unless tool_choice is none"
            )
        unknown_override_keys = sorted(
            set(dict(self.llm_request_overrides or {}))
            - _SUPPORTED_REQUEST_OVERRIDE_KEYS
        )
        if unknown_override_keys:
            raise AdaptiveToolLoopError(
                "Unsupported llm_request_overrides keys: "
                + ", ".join(unknown_override_keys)
            )


@dataclass(slots=True)
class AdaptiveToolLoopState:
    messages: list[Message] = field(default_factory=list)
    iteration: int = 0
    llm_calls: int = 0
    tool_calls_made: list[str] = field(default_factory=list)
    total_tool_calls: int = 0
    termination_reason: str = ""
    scratchpad: dict[str, Any] = field(default_factory=dict)
    seen_signatures: list[str] = field(default_factory=list)
    direct_tool_turn: DirectToolTurnContext | None = None
    direct_tool_requested_batch_satisfied: bool = False
    direct_tool_closure_consumed: bool = False
    effective_max_iterations: int = 0
    extensions_used: int = 0
    # Safety kill reset on progress.
    consecutive_noops: int = 0


@dataclass(slots=True)
class AdaptiveToolLoopOutcome:
    profile_name: str
    mode_name: str
    termination_reason: str
    state: AdaptiveToolLoopState
    allowed_tools: frozenset[str]
    final_text: str | None = None
    pending_turn_context: dict[str, Any] | None = None
    confident_complete_reasoning: str | None = None
    finalization_status: dict[str, Any] | None = None
    meta_rule_preference: dict[str, Any] | None = None
    memory_consolidation_decisions: list[dict[str, Any]] | None = None
    session_work_summary: str | None = None
    goal_declaration: dict[str, Any] | None = None
    # Structured-channel only; staged through runtime memory.
    goal_revision: dict[str, Any] | None = None
    delegation_context: dict[str, Any] | None = None
    delegation_result_summary: dict[str, Any] | None = None
    task_plan: dict[str, Any] | None = None
    task_plan_step_completed: dict[str, Any] | None = None
    task_plan_step_blocked: dict[str, Any] | None = None
    task_plan_revision: dict[str, Any] | None = None
    task_plan_abandoned: dict[str, Any] | None = None
    task_plan_completed: dict[str, Any] | None = None
    watch_condition_met: bool | None = None
    watch_summary: str | None = None
    action_result: Any | None = None
    job: JobHandle | None = None
    error_message: str | None = None
    tool_name: str | None = None
    mode_result: Any | None = None
    decompose_subtasks: list[dict[str, Any]] | None = None
    self_improvement_evaluation: dict[str, Any] | None = None
    self_improvement_decision: dict[str, Any] | None = None

    def telemetry_payload(self) -> dict[str, Any]:
        payload = {
            "adaptive.profile": self.profile_name,
            "adaptive.mode": self.mode_name,
            "adaptive.loop_iterations": self.state.iteration,
            "adaptive.llm_calls": self.state.llm_calls,
            "adaptive.tool_calls": list(self.state.tool_calls_made),
            "adaptive.tool_calls_total": self.state.total_tool_calls,
            "adaptive.termination_reason": self.termination_reason,
            "adaptive.allowed_tools": sorted(self.allowed_tools),
        }
        if str(self.confident_complete_reasoning or "").strip():
            payload["adaptive.confident_complete_reasoning"] = str(
                self.confident_complete_reasoning or ""
            ).strip()
        if isinstance(self.finalization_status, dict) and self.finalization_status:
            payload["adaptive.finalization_status"] = dict(self.finalization_status)
        if isinstance(self.pending_turn_context, dict) and self.pending_turn_context:
            payload["pending_turn_context"] = dict(self.pending_turn_context)
        if isinstance(self.meta_rule_preference, dict) and self.meta_rule_preference:
            payload["meta_rule_preference"] = dict(self.meta_rule_preference)
        if self.memory_consolidation_decisions:
            payload["memory_consolidation.decisions"] = list(
                self.memory_consolidation_decisions
            )
            payload["memory_consolidation.count"] = len(
                self.memory_consolidation_decisions
            )
        if str(self.session_work_summary or "").strip():
            payload["session_work_summary"] = str(
                self.session_work_summary or ""
            ).strip()
        if isinstance(self.delegation_context, dict) and self.delegation_context:
            payload["delegation_context"] = dict(self.delegation_context)
        if (
            isinstance(self.delegation_result_summary, dict)
            and self.delegation_result_summary
        ):
            payload["delegation_result_summary"] = dict(self.delegation_result_summary)
        if isinstance(self.task_plan, dict) and self.task_plan:
            payload["task_plan"] = dict(self.task_plan)
        if (
            isinstance(self.task_plan_step_completed, dict)
            and self.task_plan_step_completed
        ):
            payload["task_plan.step_completed"] = dict(self.task_plan_step_completed)
        if (
            isinstance(self.task_plan_step_blocked, dict)
            and self.task_plan_step_blocked
        ):
            payload["task_plan.step_blocked"] = dict(self.task_plan_step_blocked)
        if isinstance(self.task_plan_revision, dict) and self.task_plan_revision:
            payload["task_plan.revision"] = dict(self.task_plan_revision)
        if isinstance(self.task_plan_abandoned, dict) and self.task_plan_abandoned:
            payload["task_plan.abandoned"] = dict(self.task_plan_abandoned)
        if isinstance(self.task_plan_completed, dict) and self.task_plan_completed:
            payload["task_plan.completed"] = dict(self.task_plan_completed)
        if self.watch_condition_met is not None:
            payload["watch.condition_met"] = bool(self.watch_condition_met)
        if str(self.watch_summary or "").strip():
            payload["watch.summary"] = str(self.watch_summary or "").strip()
        if self.decompose_subtasks is not None:
            payload["adaptive.decompose_subtask_count"] = len(self.decompose_subtasks)
            payload["adaptive.decompose_subtask_ids"] = [
                str(item.get("subtask_id", "") or "").strip()
                for item in self.decompose_subtasks
                if str(item.get("subtask_id", "") or "").strip()
            ]
        if (
            isinstance(self.self_improvement_evaluation, dict)
            and self.self_improvement_evaluation
        ):
            payload["self_improvement.evaluation"] = dict(
                self.self_improvement_evaluation
            )
        if (
            isinstance(self.self_improvement_decision, dict)
            and self.self_improvement_decision
        ):
            payload["self_improvement.decision"] = dict(self.self_improvement_decision)
        tool_results = [
            item
            for item in list(
                self.state.scratchpad.get("adaptive.tool_results", []) or []
            )
            if isinstance(item, dict)
        ]
        if tool_results:
            payload["tool_results"] = tool_results
            payload["tool_calls_count"] = len(tool_results)
            payload["tool_execution_count"] = len(tool_results)
            payload["tool_verified"] = all(
                bool(item.get("verified")) for item in tool_results
            )
        payload.update(loop_parallel_payload(self.state.scratchpad))
        payload.update(loop_turn_progress_payload(self.state.scratchpad))
        return payload


def loop_parallel_payload(scratchpad: Mapping[str, Any] | None) -> dict[str, Any]:
    data = dict(scratchpad or {})
    return {
        "loop.parallel_fan_out_count": int(
            data.get("loop.parallel_fan_out_count", 0) or 0
        ),
        "loop.tool_calls_parallel": int(data.get("loop.tool_calls_parallel", 0) or 0),
        "loop.tool_calls_sequential": int(
            data.get("loop.tool_calls_sequential", 0) or 0
        ),
    }


def loop_turn_progress_payload(scratchpad: Mapping[str, Any] | None) -> dict[str, Any]:
    data = dict(scratchpad or {})
    payload = {
        "total_input_tokens_used": int(
            data.get("turn_progress_input_tokens_total", 0) or 0
        ),
        "total_output_tokens_used": int(
            data.get("turn_progress_output_tokens_total", 0) or 0
        ),
        "total_tokens_used": int(data.get("turn_progress_total_tokens_used", 0) or 0),
        "turn.llm_call_count": int(data.get("turn_progress_llm_call_count", 0) or 0),
        "turn.llm_call_limit": int(data.get("turn_progress_llm_call_limit", 0) or 0),
    }
    progress_phase = str(data.get("turn_progress_phase", "") or "").strip()
    if progress_phase:
        payload["turn.progress_phase"] = progress_phase
    tool_name = str(data.get("turn_progress_tool_name", "") or "").strip()
    if tool_name:
        payload["turn.tool_name"] = tool_name
    return payload


def profile_include_reflect(profile: AdaptiveToolLoopProfile) -> bool:
    return profile.reflection_policy == "always"


def resolve_allowed_tools(
    *,
    profile: AdaptiveToolLoopProfile,
    runtime_tool_names: Iterable[str],
) -> frozenset[str]:
    runtime_names = frozenset(
        str(name or "").strip()
        for name in runtime_tool_names
        if str(name or "").strip()
    )
    if profile.tool_exposure_policy == ADAPTIVE_TOOL_EXPOSURE_EXPLICIT_ALLOWLIST:
        return frozenset(profile.allowed_tools or ())
    if not runtime_names:
        raise AdaptiveToolLoopError(
            "runtime_exposed profile requires runtime_tool_names to be available"
        )
    return runtime_names


def canonical_tool_arguments(arguments: Any) -> str:
    if not isinstance(arguments, dict):
        raise AdaptiveToolLoopError("tool arguments must be a dict")
    return json.dumps(
        arguments,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _extract_tool_call_parts(tool_call: Any) -> tuple[str, dict[str, Any]]:
    if isinstance(tool_call, Mapping):
        name = str(tool_call.get("name", "") or "").strip()
        arguments = tool_call.get("arguments", {})
    else:
        name = str(getattr(tool_call, "name", "") or "").strip()
        arguments = getattr(tool_call, "arguments", {})
    if not name:
        raise AdaptiveToolLoopError("tool call name is required")
    if not isinstance(arguments, dict):
        raise AdaptiveToolLoopError("tool call arguments must be a dict")
    return name, dict(arguments)


def canonical_tool_call_signature(tool_call: Any) -> str:
    name, arguments = _extract_tool_call_parts(tool_call)
    return json.dumps(
        [name, json.loads(canonical_tool_arguments(arguments))],
        sort_keys=False,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def canonical_tool_batch_signature(tool_calls: Any) -> str:
    if not isinstance(tool_calls, Iterable) or isinstance(tool_calls, (str, bytes)):
        raise AdaptiveToolLoopError("tool_calls must be an iterable of tool calls")
    ordered_signatures = [canonical_tool_call_signature(item) for item in tool_calls]
    return json.dumps(
        ordered_signatures,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _normalize_arg_value(value: Any) -> Any:
    """Recursively normalize argument values for semantic comparison."""
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        if normalized.lower() in _SEMANTIC_EMPTY_ARGUMENT_TOKENS:
            return None
        return normalized
    if isinstance(value, dict):
        normalized_dict = {
            k: normalized
            for k, v in sorted(value.items())
            if (normalized := _normalize_arg_value(v)) is not None
        }
        return normalized_dict or None
    if isinstance(value, list):
        normalized_list = [
            normalized
            for item in value
            if (normalized := _normalize_arg_value(item)) is not None
        ]
        return normalized_list or None
    return value


def _semantic_tool_call_signature(tool_call: Any) -> str:
    name, arguments = _extract_tool_call_parts(tool_call)
    normalized = _normalize_arg_value(arguments) or {}
    return json.dumps(
        [name, normalized],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def semantic_batch_signature(tool_calls: Any) -> str:
    """Batch signature that normalizes args (sorted keys, stripped whitespace) before hashing.

    Two batches with the same tool names and semantically equivalent args (different
    key order or leading/trailing whitespace) will produce the same signature.
    """
    if not isinstance(tool_calls, Iterable) or isinstance(tool_calls, (str, bytes)):
        raise AdaptiveToolLoopError("tool_calls must be an iterable of tool calls")
    ordered_signatures = [_semantic_tool_call_signature(item) for item in tool_calls]
    return json.dumps(
        ordered_signatures,
        separators=(",", ":"),
        ensure_ascii=True,
    )
