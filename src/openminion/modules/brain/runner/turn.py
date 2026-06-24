"""Turn interpretation and command semantics for the brain runner."""

from __future__ import annotations

import hashlib
import time
from typing import TYPE_CHECKING, Any

from ..runtime import tokens
from . import call_order

from openminion.modules.tool.contracts.model_ids import (
    MODEL_IP_LOCAL,
    MODEL_IP_PUBLIC,
    MODEL_LOCATION,
    MODEL_TIME,
    MODEL_WEATHER,
    MODEL_WEB_SEARCH,
)
from openminion.tools.exec.command_parser import is_read_only_exec_command
from openminion.tools.exec.process import resolve_shell_family

from ..constants import (
    BRAIN_COMMAND_KIND_AGENT,
    BRAIN_COMMAND_KIND_TOOL,
    BRAIN_CONFIRM_RESPONSE_AFFIRM,
    BRAIN_CONFIRM_RESPONSE_DENY,
    BRAIN_CONFIRM_RESPONSE_UNCLEAR,
)
from ..tools.parser import normalize_tool_name_for_brain

from ..config import ClarifyConfig
from ..execution.mission import (
    apply_turn_reset_policy,
    llm_calls_max_from_runner,
    reset_policy_for,
)
from ..schemas import BrainMode, ClarifyPolicy, Command, Decision, WorkingState

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..diagnostics.events import CanonicalEventLogger
    from .coordinator import BrainRunner


def interpret(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    user_input: str,
    logger: "CanonicalEventLogger",
    reset_policy_name: str | None = None,
) -> None:
    state.phase = "INTERPRET"
    stripped = user_input.strip()
    state.open_questions = []
    is_confirmation_turn = False
    if state.pending_confirmation_command is not None:
        parser = None
        policy_api = getattr(runner, "policy_api", None)
        if policy_api is not None:
            parser = getattr(policy_api, "parse_confirmation_response", None)
        if callable(parser):
            try:
                parsed = str(parser(stripped) or "").strip().lower()
            except Exception:
                parsed = BRAIN_CONFIRM_RESPONSE_UNCLEAR
        else:
            try:
                from openminion.modules.policy.runtime.service import (
                    parse_confirmation_response,
                )

                parsed = str(parse_confirmation_response(stripped)).strip().lower()
            except Exception:
                parsed = BRAIN_CONFIRM_RESPONSE_UNCLEAR
        is_confirmation_turn = parsed in {
            BRAIN_CONFIRM_RESPONSE_AFFIRM,
            BRAIN_CONFIRM_RESPONSE_DENY,
        }

    if not is_confirmation_turn:
        state.last_user_input = stripped

    if not is_confirmation_turn:
        route_action = ""
        if reset_policy_name in {
            "mission_start",
            "mission_revise",
            "mission_continue",
            "mission_finish",
            "mission_fork",
        }:
            route_action = reset_policy_name.replace("mission_", "", 1)
        policy = reset_policy_for(
            route_action=route_action,
            is_confirmation_turn=False,
        )
        if reset_policy_name is not None and reset_policy_name != policy.name:
            policy = type(policy)(
                name=str(reset_policy_name),
                overwrite_goal=policy.overwrite_goal,
                clear_step_outputs=policy.clear_step_outputs,
                clear_adaptive_state=policy.clear_adaptive_state,
                reset_failure_counters=policy.reset_failure_counters,
                reset_checkpoint_cursor=policy.reset_checkpoint_cursor,
                reset_llm_calls=policy.reset_llm_calls,
                refresh_budgets=policy.refresh_budgets,
                clear_open_questions=policy.clear_open_questions,
            )
        apply_turn_reset_policy(
            state=state,
            policy=policy,
            next_goal=stripped,
            turn_budget=state.budgets_remaining if policy.refresh_budgets else None,
            llm_calls_max=llm_calls_max_from_runner(runner),
        )
        if state.mission is not None:
            state.mission.latest_reset_policy = policy.name
    logger.emit(
        "brain.interpret",
        {
            "goal": state.goal,
            "constraints_count": len(state.constraints),
            "open_questions_count": len(state.open_questions),
        },
        trace_id=state.trace_id,
    )


def should_ask(
    *,
    mode: BrainMode,
    policy: ClarifyPolicy,
    confidence: float,
    config: ClarifyConfig,
) -> bool:
    if mode == BrainMode.BATCH:
        return False

    if policy == ClarifyPolicy.ALWAYS_ASK:
        return True
    if policy == ClarifyPolicy.ASSUME_DEFAULTS:
        return False

    if mode == BrainMode.COMMAND:
        threshold = 0.95
    elif mode == BrainMode.GUIDED:
        threshold = config.ask_threshold
    elif mode == BrainMode.AUTONOMOUS:
        threshold = 0.4
    else:
        threshold = config.ask_threshold

    return confidence < threshold


def autonomous_requires_confirmation(
    *,
    state: WorkingState | None = None,
    user_input: str | None = None,
) -> bool:
    del user_input
    if state is None or state.plan is None:
        return False
    if state.cursor >= len(state.plan.steps):
        return False
    command = state.plan.steps[state.cursor]
    return str(getattr(command, "risk_level", "low") or "low").strip().lower() == "high"


def memory_policy_snapshot(*, options: Any) -> dict[str, Any]:
    snapshot = getattr(options, "memory_policy_snapshot", {}) or {}
    if isinstance(snapshot, dict):
        return dict(snapshot)
    return {}


_TIME_SENSITIVE_TOOL_CAPABILITY = "time_sensitive"


def _capability_tokens(raw_capabilities: Any) -> set[str]:
    if isinstance(raw_capabilities, str):
        raw_iterable = (raw_capabilities,)
    elif isinstance(raw_capabilities, tuple | list | set | frozenset):
        raw_iterable = raw_capabilities
    else:
        raw_iterable = ()
    return {
        str(item or "").strip().lower()
        for item in raw_iterable
        if str(item or "").strip()
    }


def is_time_sensitive_tool_command(runner: "BrainRunner", *, command: Command) -> bool:
    if command.kind != "tool":
        return False
    tool_name = getattr(command, "tool_name", "")
    if not tool_name:
        return False
    candidate_names: list[str] = []
    raw_name = str(tool_name or "").strip()
    if raw_name:
        candidate_names.append(raw_name)
    normalized = normalize_tool_name_for_brain(raw_name)
    if normalized:
        normalized_name = normalized.strip()
        if normalized_name and normalized_name not in candidate_names:
            candidate_names.append(normalized_name)

    registry = getattr(getattr(runner, "tool_api", None), "registry", None)
    tools = getattr(registry, "_tools", None)
    if isinstance(tools, dict):
        for candidate_name in candidate_names:
            tool_spec = tools.get(candidate_name)
            if tool_spec is None:
                continue
            raw_capabilities = ()
            resolved_capabilities = getattr(tool_spec, "resolved_capabilities", None)
            if callable(resolved_capabilities):
                raw_capabilities = resolved_capabilities()
            else:
                raw_capabilities = getattr(tool_spec, "capabilities", ())
            if _TIME_SENSITIVE_TOOL_CAPABILITY in _capability_tokens(raw_capabilities):
                return True

    collect_runtime_tool_schemas = getattr(
        runner, "_collect_runtime_tool_schemas", None
    )
    if callable(collect_runtime_tool_schemas):
        try:
            schemas = collect_runtime_tool_schemas()
        except Exception:
            schemas = []
        if isinstance(schemas, list):
            for schema in schemas:
                if not isinstance(schema, dict):
                    continue
                schema_name = str(schema.get("name", "") or "").strip()
                if schema_name not in candidate_names:
                    continue
                if _TIME_SENSITIVE_TOOL_CAPABILITY in _capability_tokens(
                    schema.get("capabilities", ())
                ):
                    return True

    return False


def direct_response(*, user_input: str | None, decision: Decision) -> str:
    if decision.answer:
        return decision.answer
    if decision.question:
        return decision.question
    if user_input:
        return "I'm here. What can I help you with?"
    return "I'm here. What should we work on next?"


def idempotency_key(*, session_id: str, trace_id: str, text: str) -> str:
    del trace_id
    # Explicit `tool ...` / `agent ...` commands should remain idempotent across
    digest = hashlib.sha256(f"{session_id}:{text}".encode("utf-8")).hexdigest()
    return digest[:32]


def now_ms() -> int:
    return int(time.monotonic() * 1000)


def build_memory_policy_snapshot_response(*, snapshot: dict[str, Any]) -> str:
    source = str(snapshot.get("policy_source", "runtime.config") or "runtime.config")
    version = str(
        snapshot.get("policy_version", "memory_policy_snapshot.v1")
        or "memory_policy_snapshot.v1"
    )
    memory_enabled = bool(snapshot.get("memory_enabled", False))
    capsule_strategy = str(
        snapshot.get("capsule_strategy", "dynamic_turn") or "dynamic_turn"
    )
    refresh_policy = str(
        snapshot.get("refresh_policy", "refresh_each_turn") or "refresh_each_turn"
    )
    retention_days = int(snapshot.get("retention_days", 30) or 30)
    posture = str(
        snapshot.get("session_vs_cross_session", "session_plus_cross_session")
        or "session_plus_cross_session"
    )
    dynamic = bool(snapshot.get("dynamic_retrieval_enabled", True))
    provider = str(snapshot.get("memory_provider", "memory_v2") or "memory_v2")
    return (
        "Memory policy snapshot:\n"
        f"- source: {source}\n"
        f"- version: {version}\n"
        f"- memory_enabled: {str(memory_enabled).lower()}\n"
        f"- memory_provider: {provider}\n"
        f"- capsule_strategy: {capsule_strategy}\n"
        f"- refresh_policy: {refresh_policy}\n"
        f"- dynamic_retrieval_enabled: {str(dynamic).lower()}\n"
        f"- retention_days: {retention_days}\n"
        f"- session_vs_cross_session: {posture}"
    )


def build_memory_policy_unavailable_response(*, snapshot: dict[str, Any]) -> str:
    source = str(snapshot.get("policy_source", "runtime.config") or "runtime.config")
    version = str(
        snapshot.get("policy_version", "memory_policy_snapshot.v1")
        or "memory_policy_snapshot.v1"
    )
    reason = str(
        snapshot.get("policy_error", "policy_unavailable") or "policy_unavailable"
    )
    return (
        "MEMORY_POLICY: policy_unavailable "
        f"(source={source} version={version} reason={reason})"
    )


def command_has_side_effects(
    runner: "BrainRunner",
    *,
    command: Command,
) -> bool:
    if command.kind == BRAIN_COMMAND_KIND_AGENT:
        return True
    if command.kind != BRAIN_COMMAND_KIND_TOOL:
        return False

    raw_tool_name = str(getattr(command, "tool_name", "")).strip().lower()
    tool_name = (
        (normalize_tool_name_for_brain(raw_tool_name) or raw_tool_name).strip().lower()
    )
    read_only_tools = {
        "file.list_dir",
        "file.read",
        "file.find",
        MODEL_WEB_SEARCH,
        MODEL_WEATHER,
        MODEL_TIME,
        MODEL_LOCATION,
        MODEL_IP_PUBLIC,
        MODEL_IP_LOCAL,
    }
    if tool_name in read_only_tools:
        return False

    if tool_name == "exec.run":
        args = getattr(command, "args", {}) or {}
        if isinstance(args, dict):
            raw_command = str(args.get("command", "")).strip()
            if is_read_only_exec_command(
                raw_command,
                shell_family=resolve_shell_family(),
            ):
                return False
        return True

    return True


def estimate_tokens(
    runner: "BrainRunner", *, model: str, context: dict[str, Any]
) -> int:
    return tokens.estimate_tokens(
        llm_api=runner.llm_api,
        model=model,
        context=context,
    )


track_call_started = call_order.track_call_started
track_manifest_emitted = call_order.track_manifest_emitted
track_call_completed = call_order.track_call_completed
validate_call_order = call_order.validate_call_order


def debit_tokens(state: WorkingState, response: dict[str, Any], logger) -> None:
    tokens.debit_tokens(state=state, response=response, logger=logger)
