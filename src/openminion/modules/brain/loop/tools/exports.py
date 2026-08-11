"""Lazy export table for the brain loop tools package."""

from __future__ import annotations

from typing import Any

LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "engine": (".engine", "__module__"),
    "loop_dispatch": (".iteration.dispatch", "__module__"),
    "loop_execution": (".postprocess.engine", "__module__"),
    "ADAPTIVE_CLOSURE_ENGINE_SINGLE_PASS": (
        ".contracts",
        "ADAPTIVE_CLOSURE_ENGINE_SINGLE_PASS",
    ),
    "AdaptiveLoopIterationEvent": (".events", "AdaptiveLoopIterationEvent"),
    "IterationToolCallRecord": (".events", "IterationToolCallRecord"),
    "LoopProfiler": (".profiler", "LoopProfiler"),
    "LoopTemplate": (".memory_templates", "LoopTemplate"),
    "PLAN_TOOL_NAME": (".plan_control", "PLAN_TOOL_NAME"),
    "PLAN_TOOL_ACTIONS_SCRATCHPAD_KEY": (
        ".plan_control",
        "PLAN_TOOL_ACTIONS_SCRATCHPAD_KEY",
    ),
    "PLAN_TOOL_ATTEMPTED_SCRATCHPAD_KEY": (
        ".plan_control",
        "PLAN_TOOL_ATTEMPTED_SCRATCHPAD_KEY",
    ),
    "PLAN_TOOL_USED_SCRATCHPAD_KEY": (".plan_control", "PLAN_TOOL_USED_SCRATCHPAD_KEY"),
    "PrefetchPredictor": (".prefetch", "PrefetchPredictor"),
    "build_template_hint": (".memory_templates", "build_template_hint"),
    "match_templates": (".memory_templates", "match_templates"),
    "ADAPTIVE_CLOSURE_MODE_OWNED": (".contracts", "ADAPTIVE_CLOSURE_MODE_OWNED"),
    "ADAPTIVE_TERM_BUDGET_EXHAUSTED": (".contracts", "ADAPTIVE_TERM_BUDGET_EXHAUSTED"),
    "ADAPTIVE_TERM_CIRCULAR_PATTERN": (".contracts", "ADAPTIVE_TERM_CIRCULAR_PATTERN"),
    "ADAPTIVE_TERM_CORRECTION_BUDGET_EXHAUSTED": (
        ".contracts",
        "ADAPTIVE_TERM_CORRECTION_BUDGET_EXHAUSTED",
    ),
    "ADAPTIVE_TERM_DECOMPOSE_INVALID": (
        ".contracts",
        "ADAPTIVE_TERM_DECOMPOSE_INVALID",
    ),
    "ADAPTIVE_TERM_DECOMPOSE_REQUESTED": (
        ".contracts",
        "ADAPTIVE_TERM_DECOMPOSE_REQUESTED",
    ),
    "ADAPTIVE_TERM_DIRECT_TOOL_CLOSURE_FAILED": (
        ".contracts",
        "ADAPTIVE_TERM_DIRECT_TOOL_CLOSURE_FAILED",
    ),
    "ADAPTIVE_TERM_DISALLOWED_TOOL": (".contracts", "ADAPTIVE_TERM_DISALLOWED_TOOL"),
    "ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS": (
        ".contracts",
        "ADAPTIVE_TERM_DUPLICATE_TOOL_CALLS",
    ),
    "ADAPTIVE_TERM_CONFIDENT_COMPLETE": (
        ".contracts",
        "ADAPTIVE_TERM_CONFIDENT_COMPLETE",
    ),
    "ADAPTIVE_TERM_FINALIZATION_BLOCKED": (
        ".contracts",
        "ADAPTIVE_TERM_FINALIZATION_BLOCKED",
    ),
    "ADAPTIVE_TERM_FINALIZATION_CONTRACT_MISSING": (
        ".contracts",
        "ADAPTIVE_TERM_FINALIZATION_CONTRACT_MISSING",
    ),
    "ADAPTIVE_TERM_FINALIZATION_INCOMPLETE": (
        ".contracts",
        "ADAPTIVE_TERM_FINALIZATION_INCOMPLETE",
    ),
    "ADAPTIVE_TERM_FINAL_TEXT": (".contracts", "ADAPTIVE_TERM_FINAL_TEXT"),
    "ADAPTIVE_TERM_ITERATION_CAP": (".contracts", "ADAPTIVE_TERM_ITERATION_CAP"),
    "ADAPTIVE_TERM_JOB_PENDING": (".contracts", "ADAPTIVE_TERM_JOB_PENDING"),
    "ADAPTIVE_TERM_LLM_ERROR": (".contracts", "ADAPTIVE_TERM_LLM_ERROR"),
    "ADAPTIVE_TERM_NEEDS_USER": (".contracts", "ADAPTIVE_TERM_NEEDS_USER"),
    "ADAPTIVE_TERM_REQUESTED_TOOL_NOT_EXECUTED": (
        ".contracts",
        "ADAPTIVE_TERM_REQUESTED_TOOL_NOT_EXECUTED",
    ),
    "ADAPTIVE_TERM_TOOL_FAILURE_NO_RECOVERY": (
        ".contracts",
        "ADAPTIVE_TERM_TOOL_FAILURE_NO_RECOVERY",
    ),
    "AnomalyScore": (".reflection", "AnomalyScore"),
    "CausalBatch": (".causal", "CausalBatch"),
    "CorrectionHistory": (".correction", "CorrectionHistory"),
    "CorrectionPlan": (".correction", "CorrectionPlan"),
    "CorrectionRecord": (".correction", "CorrectionRecord"),
    "AdaptiveToolLoopContext": (".contracts", "AdaptiveToolLoopContext"),
    "DirectToolTurnContext": (".contracts", "DirectToolTurnContext"),
    "AdaptiveToolLoopError": (".contracts", "AdaptiveToolLoopError"),
    "AdaptiveToolLoopLLMRuntime": (".contracts", "AdaptiveToolLoopLLMRuntime"),
    "AdaptiveToolLoopOutcome": (".contracts", "AdaptiveToolLoopOutcome"),
    "AdaptiveToolLoopProfile": (".contracts", "AdaptiveToolLoopProfile"),
    "AdaptiveToolLoopRuntimeUnavailableError": (
        ".contracts",
        "AdaptiveToolLoopRuntimeUnavailableError",
    ),
    "AdaptiveToolLoopState": (".contracts", "AdaptiveToolLoopState"),
    "CommandExecutionOutcome": (".contracts", "CommandExecutionOutcome"),
    "DefaultAdaptiveToolLoopLLMRuntime": (
        ".runtime",
        "DefaultAdaptiveToolLoopLLMRuntime",
    ),
    "TOOL_REQUEST_TOOL_NAME": (".shortlisting", "TOOL_REQUEST_TOOL_NAME"),
    "TOOL_SCHEMA_SHORTLIST_MAX_ACTIVE": (
        ".shortlisting",
        "TOOL_SCHEMA_SHORTLIST_MAX_ACTIVE",
    ),
    "TOOL_SCHEMA_SHORTLIST_THRESHOLD": (
        ".shortlisting",
        "TOOL_SCHEMA_SHORTLIST_THRESHOLD",
    ),
    "ToolSchemaShortlistResult": (".shortlisting", "ToolSchemaShortlistResult"),
    "LoopCache": (".cache", "LoopCache"),
    "LoopSnapshot": (".snapshot", "LoopSnapshot"),
    "LoopToolCallRecord": (".snapshot", "LoopToolCallRecord"),
    "ParallelDispatchResult": (".parallel", "ParallelDispatchResult"),
    "PreparedToolDispatch": (".contracts", "PreparedToolDispatch"),
    "PrepareOutcome": (".contracts", "PrepareOutcome"),
    "RawToolResult": (".contracts", "RawToolResult"),
    "action_result_to_tool_message": (".messages", "action_result_to_tool_message"),
    "adaptive_outcome_payload": (".status", "adaptive_outcome_payload"),
    "adaptive_status_payload": (".status", "adaptive_status_payload"),
    "build_loop_thinking_metadata": (".runtime", "build_loop_thinking_metadata"),
    "build_inactive_tool_directory_message": (
        ".shortlisting",
        "build_inactive_tool_directory_message",
    ),
    "build_tool_request_spec": (".shortlisting", "build_tool_request_spec"),
    "build_runtime_tool_specs": (".runtime", "build_runtime_tool_specs"),
    "build_plan_tool_spec": (".plan_control", "build_plan_tool_spec"),
    "canonical_tool_arguments": (".contracts", "canonical_tool_arguments"),
    "canonical_tool_batch_signature": (".contracts", "canonical_tool_batch_signature"),
    "canonical_tool_call_signature": (".contracts", "canonical_tool_call_signature"),
    "classify_batch": (".causal", "classify_batch"),
    "compress_transcript": (".snapshot", "compress_transcript"),
    "detect_anomaly": (".reflection", "detect_anomaly"),
    "emit_adaptive_status": (".status", "emit_adaptive_status"),
    "execute_parallel_tool_batch": (".parallel", "execute_parallel_tool_batch"),
    "handle_plan_tool_call": (".plan_control", "handle_plan_tool_call"),
    "format_blocking_tool_message": (".messages", "format_blocking_tool_message"),
    "hash_args": (".snapshot", "hash_args"),
    "loop_parallel_payload": (".contracts", "loop_parallel_payload"),
    "loop_reflection_payload": (".status", "loop_reflection_payload"),
    "loop_resume_payload": (".status", "loop_resume_payload"),
    "profile_include_reflect": (".contracts", "profile_include_reflect"),
    "resolve_allowed_tools": (".contracts", "resolve_allowed_tools"),
    "resolve_loop_model": (".runtime", "resolve_loop_model"),
    "run_adaptive_tool_loop": (".engine", "run_adaptive_tool_loop"),
    "semantic_batch_signature": (".contracts", "semantic_batch_signature"),
    "should_shortlist_tool_schemas": (".shortlisting", "should_shortlist_tool_schemas"),
    "shortlist_tool_schemas": (".shortlisting", "shortlist_tool_schemas"),
    "with_tool_request_spec": (".shortlisting", "with_tool_request_spec"),
    "with_plan_tool_spec": (".plan_control", "with_plan_tool_spec"),
}

PUBLIC_EXPORTS = [
    name
    for name in LAZY_EXPORTS
    if name not in {"engine", "loop_dispatch", "loop_execution"}
]


def resolve_lazy_export(*, package_name: str, name: str) -> Any:
    target = LAZY_EXPORTS.get(name)
    if not target:
        raise AttributeError(name)
    module_name, attr_name = target
    module = __import__(package_name + module_name, fromlist=[attr_name])
    if attr_name == "__module__":
        return module
    return getattr(module, attr_name)
