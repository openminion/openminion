import re
from typing import Any

from openminion.base.types import Message
from openminion.modules.context.input_boundaries import (
    emit_boundary_event as _pidf_emit_boundary_event,
)
from openminion.modules.brain.loop.context.pending_turn import (
    pending_turn_context_for_prompt,
)
from openminion.modules.brain.runner import BrainRunner
from openminion.modules.memory.runtime.consolidation import (
    collect_memory_consolidation_candidates,
)
from openminion.base.config.core import resolve_default_agent_id
from openminion.services.agent import _history_role, _resolve_system_prompt
from openminion.services.agent.constants import PRIOR_TURN_CONTEXT_CHAR_LIMIT
from openminion.services.agent.turn_context import (
    append_grounding_blocks,
    build_grounding_facts,
)
from openminion.tools.task.constants import WATCH_TURN_KIND_CHECK
from openminion.modules.brain.constants import STATE_KEY_MODULE_STATE

_TURN_SIGNATURE_WINDOW = 16


def _resolve_turn_session_ids(*, message: Message) -> tuple[str, str]:
    runtime_session_id = str(message.metadata.get("session_id", "default")).strip()
    if not runtime_session_id:
        runtime_session_id = "default"
    explicit_brain_session_id = str(
        message.metadata.get("brain_session_id", "")
    ).strip()
    if explicit_brain_session_id:
        return runtime_session_id, explicit_brain_session_id
    conversation_id = str(message.metadata.get("conversation_id", "")).strip()
    if not conversation_id:
        conversation_id = str(message.metadata.get("thread_id", "")).strip()
    if not conversation_id:
        return runtime_session_id, runtime_session_id
    return runtime_session_id, f"{runtime_session_id}::conv:{conversation_id}"


def _normalize_llm_purpose(purpose: str) -> str | None:
    normalized = str(purpose or "").strip().lower()
    if normalized in {"decide", "plan", "reflect"}:
        return normalized
    if normalized in {"respond_followup", "follow_up"}:
        return "follow_up"
    return None


def _collect_llm_call_counts_by_purpose(
    *,
    runner: BrainRunner,
    session_id: str,
    trace_id: str | None,
    fallback_total_llm_calls: int,
) -> dict[str, int]:
    counts: dict[str, int] = {
        "decide": 0,
        "plan": 0,
        "reflect": 0,
        "follow_up": 0,
    }
    try:
        events = runner.session_api.list_events(session_id)
    except Exception:  # noqa: BLE001
        events = []

    normalized_trace = str(trace_id or "").strip()
    for event in events:
        if not isinstance(event, dict):
            continue
        if str(event.get("type", "")).strip() != "llm.call.completed":
            continue
        if normalized_trace:
            event_trace = str(event.get("trace_id", "")).strip()
            if event_trace and event_trace != normalized_trace:
                continue
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            continue
        normalized = _normalize_llm_purpose(str(payload.get("purpose", "")))
        if normalized is None:
            continue
        counts[normalized] += 1

    if sum(counts.values()) == 0 and fallback_total_llm_calls > 0:
        counts["decide"] = max(0, int(fallback_total_llm_calls))
    return counts


def _inject_gateway_system_context(
    self,
    *,
    runner: BrainRunner,
    session_id: str,
    gateway_system_context: str,
) -> None:
    ctx = str(gateway_system_context or "").strip()
    if not ctx:
        return
    # PIDF: route gateway_system_context through the typed boundary owner.
    _pidf_emit_boundary_event(
        "gateway_system_context",
        ctx,
        seam_id="services.brain.post_execution.context.inject_gateway_system_context",
    )
    state_inline = self._latest_working_state_inline(
        runner=runner,
        session_id=session_id,
    )
    if state_inline is None:
        return
    state_inline["gateway_system_context"] = ctx
    self._write_working_state_inline(
        runner=runner,
        session_id=session_id,
        state_inline=state_inline,
    )


def _history_has_memory_context(*, history: list[Message]) -> bool:
    for item in history:
        metadata = getattr(item, "metadata", {}) or {}
        if str(metadata.get("memory_scope", "") or "").strip():
            return True
        if (
            str(metadata.get("context_memory_merged", "") or "").strip().lower()
            == "true"
        ):
            return True
        if (
            str(metadata.get("context_memory_dynamic", "") or "").strip().lower()
            == "true"
        ):
            return True
    return False


def _build_runtime_memory_context(
    self,
    *,
    runner: BrainRunner,
    session_id: str,
    user_message: str,
    history: list[Message],
) -> str:
    if _history_has_memory_context(history=history):
        try:
            runner._pending_memory_context_meta = {}
        except Exception:  # noqa: BLE001
            pass
        return ""
    memory_api = getattr(runner, "memory_api", None)
    if memory_api is None:
        try:
            runner._pending_memory_context_meta = {}
        except Exception:  # noqa: BLE001
            pass
        return ""
    try:
        build_context_with_meta = getattr(
            memory_api, "build_context_with_metadata", None
        )
        if callable(build_context_with_meta):
            context, meta = build_context_with_meta(
                session_id=session_id,
                user_message=user_message,
            )
        else:
            build_context = getattr(memory_api, "build_context", None)
            if not callable(build_context):
                try:
                    runner._pending_memory_context_meta = {}
                except Exception:  # noqa: BLE001
                    pass
                return ""
            context = build_context(
                session_id=session_id,
                user_message=user_message,
            )
            meta = {}
    except Exception:  # noqa: BLE001
        try:
            runner._pending_memory_context_meta = {}
        except Exception:  # noqa: BLE001
            pass
        return ""
    try:
        runner._pending_memory_context_meta = dict(meta or {})
    except Exception:  # noqa: BLE001
        pass
    return str(context or "").strip()


def _inject_resume_task_hints(
    self,
    *,
    runner: BrainRunner,
    session_id: str,
    inbound_metadata: dict[str, str],
) -> None:
    task_id = str(inbound_metadata.get("linked_task_id", "") or "").strip()
    cron_job_id = str(inbound_metadata.get("cron_job_id", "") or "").strip()
    has_watch_context = _metadata_bool(inbound_metadata, "watch_job")
    has_consolidation_context = _metadata_bool(
        inbound_metadata, "memory_consolidation_job"
    )
    has_delegation_context = _metadata_bool(inbound_metadata, "a2a_delegated_child")
    if (
        not task_id
        and not cron_job_id
        and not has_watch_context
        and not has_consolidation_context
        and not has_delegation_context
    ):
        return
    state_inline = self._latest_working_state_inline(
        runner=runner,
        session_id=session_id,
    )
    if state_inline is None:
        return
    if task_id:
        state_inline["resume_task_id_hint"] = task_id
    if cron_job_id:
        state_inline["resume_cron_job_id_hint"] = cron_job_id
    if has_watch_context:
        _attach_watch_resume_context(
            state_inline=state_inline,
            inbound_metadata=inbound_metadata,
            cron_job_id=cron_job_id,
        )
    if has_consolidation_context:
        _attach_memory_consolidation_resume_context(
            state_inline=state_inline,
            inbound_metadata=inbound_metadata,
            runner=runner,
        )
    if has_delegation_context:
        _attach_delegation_resume_context(
            state_inline=state_inline,
            inbound_metadata=inbound_metadata,
        )
    self._write_working_state_inline(
        runner=runner,
        session_id=session_id,
        state_inline=state_inline,
    )


def _metadata_bool(metadata: dict[str, str], key: str) -> bool:
    return str(metadata.get(key, "") or "").strip().lower() == "true"


def _module_state(state_inline: dict[str, Any]) -> dict[str, Any]:
    module_state = state_inline.get(STATE_KEY_MODULE_STATE)
    return module_state if isinstance(module_state, dict) else {}


def _attach_watch_resume_context(
    *,
    state_inline: dict[str, Any],
    inbound_metadata: dict[str, str],
    cron_job_id: str,
) -> None:
    module_state = _module_state(state_inline)
    raw_tools = str(inbound_metadata.get("watch_allowed_tools", "") or "").strip()
    watch_turn_kind = (
        str(inbound_metadata.get("watch_turn_kind", "") or "").strip()
        or WATCH_TURN_KIND_CHECK
    )
    module_state["watch_subscription"] = {
        "enabled": True,
        "turn_kind": watch_turn_kind,
        "description": str(inbound_metadata.get("watch_description", "") or "").strip(),
        "alert_condition": str(
            inbound_metadata.get("watch_alert_condition", "") or ""
        ).strip(),
        "allowed_tools": [
            item.strip() for item in raw_tools.split(",") if item.strip()
        ],
        "max_iterations": int(inbound_metadata.get("watch_max_iterations", 3) or 3),
        "timeout_seconds": int(inbound_metadata.get("watch_timeout_seconds", 60) or 60),
        "write_authorized": _metadata_bool(inbound_metadata, "watch_write_authorized"),
        "write_authorization_scope": str(
            inbound_metadata.get("watch_write_authorization_scope", "") or ""
        ).strip(),
        "cron_job_id": cron_job_id,
        "cron_run_id": str(inbound_metadata.get("cron_run_id", "") or "").strip(),
    }
    state_inline[STATE_KEY_MODULE_STATE] = module_state


def _attach_memory_consolidation_resume_context(
    *,
    state_inline: dict[str, Any],
    inbound_metadata: dict[str, str],
    runner: BrainRunner,
) -> None:
    module_state = _module_state(state_inline)
    target_scope = str(
        inbound_metadata.get("memory_consolidation_target_scope", "") or ""
    ).strip()
    batch_limit = int(
        inbound_metadata.get("memory_consolidation_batch_limit", 12) or 12
    )
    module_state["memory_consolidation"] = {
        "enabled": True,
        "target_scope": target_scope,
        "batch_limit": batch_limit,
        "max_iterations": int(
            inbound_metadata.get("memory_consolidation_max_iterations", 2) or 2
        ),
        "timeout_seconds": int(
            inbound_metadata.get("memory_consolidation_timeout_seconds", 30) or 30
        ),
        "candidates": collect_memory_consolidation_candidates(
            getattr(runner, "memory_api", None),
            proposed_scope=target_scope,
            limit=batch_limit,
        ),
    }
    state_inline[STATE_KEY_MODULE_STATE] = module_state


def _attach_delegation_resume_context(
    *,
    state_inline: dict[str, Any],
    inbound_metadata: dict[str, str],
) -> None:
    module_state = _module_state(state_inline)
    artifacts = [
        item.strip()
        for item in str(
            inbound_metadata.get("delegation_context_artifacts", "")
            or inbound_metadata.get("delegation_context_artifact_refs", "")
            or ""
        ).split(",")
        if item.strip()
    ]
    module_state["delegation"] = {
        "enabled": True,
        "parent_context": {
            "summary": str(
                inbound_metadata.get("delegation_context_summary", "") or ""
            ).strip(),
            "artifacts": artifacts,
            "intent_id": str(
                inbound_metadata.get("delegation_context_intent_id", "") or ""
            ).strip(),
        },
    }
    state_inline[STATE_KEY_MODULE_STATE] = module_state


def _pending_history_turns_to_hydrate(
    self,
    *,
    runner: BrainRunner,
    session_id: str,
    history: list[Message],
) -> list[tuple[str, str]]:
    existing_signatures = self._runner_turn_signatures(
        runner=runner,
        session_id=session_id,
    )
    pending: list[tuple[str, str]] = []
    for item in history:
        role = _history_role(item.metadata.get("role", ""))
        if role not in {"user", "assistant"}:
            continue
        content = str(item.body or "").strip()
        if not content:
            continue
        if role == "assistant" and self._is_state_machine_error_text(content):
            continue
        signature = self._turn_signature(role=role, content=content)
        if signature in existing_signatures:
            continue
        pending.append((role, content))
        existing_signatures.add(signature)
    return pending


def _hydrate_runner_session_context(
    self,
    *,
    runner: BrainRunner,
    session_id: str,
    history: list[Message],
    system_prompt: str | None = None,
) -> None:
    del system_prompt
    for role, content in self._pending_history_turns_to_hydrate(
        runner=runner,
        session_id=session_id,
        history=history,
    ):
        try:
            runner.session_api.append_turn(
                session_id=session_id,
                role=role,
                content=content,
                meta={"source": "gateway_history_bridge"},
            )
        except Exception:  # noqa: BLE001
            break


def _runtime_system_prompt(self, *, user_message: str) -> str:
    # the `self._self_improvement.build_guardrail_block(...)` call
    del user_message  # unused: SIRH-02 removed the sole consumer
    default_agent_id = resolve_default_agent_id(self._config)
    return _resolve_system_prompt(self._config.agents[default_agent_id].system_prompt)


def _pending_turn_context_for_prompt(
    self,
    *,
    runner: BrainRunner,
    session_id: str,
) -> dict[str, Any] | None:
    state_inline = self._latest_working_state_inline(
        runner=runner,
        session_id=session_id,
    )
    return pending_turn_context_for_prompt(state_inline=state_inline)


def _prior_turn_context_hint(
    self,
    *,
    runner: BrainRunner,
    session_id: str,
    history: list[Message],
) -> dict[str, str] | None:
    def _latest_pair_from_messages(
        items: list[Message],
    ) -> tuple[str, str] | None:
        latest_user = ""
        latest_assistant = ""
        for item in items:
            role = _history_role(item.metadata.get("role", ""))
            content = str(item.body or "").strip()
            if not content:
                continue
            if role == "assistant":
                if self._is_state_machine_error_text(content):
                    continue
                latest_assistant = content
            elif role == "user":
                latest_user = content
        if not latest_user and not latest_assistant:
            return None
        return latest_user, latest_assistant

    pair = _latest_pair_from_messages(history)
    if pair is None:
        try:
            turns = runner.session_api.list_turns(session_id)
        except Exception:  # noqa: BLE001
            turns = []
        latest_user = ""
        latest_assistant = ""
        for item in turns:
            if not isinstance(item, dict):
                continue
            role_raw = str(item.get("role", item.get("turn_type", ""))).strip().lower()
            content = str(item.get("content", item.get("text", ""))).strip()
            if not content:
                continue
            if role_raw in {"assistant", "outbound"}:
                if self._is_state_machine_error_text(content):
                    continue
                latest_assistant = content
            elif role_raw in {"user", "inbound"}:
                latest_user = content
        if latest_user or latest_assistant:
            pair = (latest_user, latest_assistant)
    if pair is None:
        return None
    user_message, assistant_message = pair
    payload: dict[str, str] = {}
    if user_message:
        payload["user_message"] = user_message[:PRIOR_TURN_CONTEXT_CHAR_LIMIT]
    if assistant_message:
        payload["assistant_message"] = assistant_message[:PRIOR_TURN_CONTEXT_CHAR_LIMIT]
    return payload or None


def _append_runtime_grounding_block(
    self,
    *,
    runner: BrainRunner,
    session_id: str,
    history: list[Message],
    inbound_metadata: dict[str, Any],
    system_prompt: str,
) -> str:
    config = getattr(self, "_config", None)
    recalled_memory_count = _recalled_memory_count_from_runner(
        runner=runner,
        session_id=session_id,
    )
    memory_context_meta = _pending_memory_context_meta_from_runner(runner=runner)
    prior_turn_hint = self._prior_turn_context_hint(
        runner=runner,
        session_id=session_id,
        history=history,
    )
    return append_grounding_blocks(
        system_prompt=system_prompt,
        facts=build_grounding_facts(
            runtime_env=getattr(getattr(config, "runtime", None), "env", None),
            home_root=(
                getattr(getattr(self, "_home_paths", None), "home_root", None)
                or getattr(self, "_home_root", None)
            ),
            workspace_root=getattr(self, "workspace_root", None),
            inbound_metadata=inbound_metadata,
            tools=getattr(self, "_tools", None),
            include_session_working_state=True,
            recalled_memory_count=recalled_memory_count,
            prior_context_present=(
                str(memory_context_meta.get("prior_context_present", "") or "")
                .strip()
                .lower()
                == "true"
            ),
            prior_turn_present=prior_turn_hint is not None,
        ),
        pending_turn_context=self._pending_turn_context_for_prompt(
            runner=runner,
            session_id=session_id,
        ),
        prior_turn_hint=prior_turn_hint,
    )


def _pending_memory_context_meta_from_runner(*, runner: Any) -> dict[str, Any]:
    raw = getattr(runner, "_pending_memory_context_meta", None)
    return dict(raw) if isinstance(raw, dict) else {}


def _recalled_memory_count_from_runner(
    *,
    runner: Any,
    session_id: str,
) -> int:
    try:
        state = runner.get_latest_working_state(session_id)
        if state is None:
            return 0
        state_inline = (
            state if isinstance(state, dict) else getattr(state, "state_inline", None)
        )
        if isinstance(state_inline, dict):
            refs = state_inline.get("decision_memory_refs")
            if isinstance(refs, list):
                return len(refs)
        refs = getattr(state, "decision_memory_refs", None)
        if isinstance(refs, list):
            return len(refs)
    except Exception:
        pass
    return 0


def _collect_system_history_context(*, history: list[Message]) -> str:
    chunks: list[str] = []
    seen: set[str] = set()
    for item in history:
        role = _history_role(item.metadata.get("role", ""))
        if role != "system":
            continue
        text = str(getattr(item, "body", "") or "").strip()
        if not text:
            continue
        if text in seen:
            continue
        seen.add(text)
        chunks.append(text)
    return "\n\n".join(chunks).strip()


def _apply_runtime_system_prompt_override(
    *,
    runner: BrainRunner,
    system_prompt: str,
) -> None:
    try:
        context_api = getattr(runner, "context_api", None)
        service = getattr(context_api, "service", None)
        identityctl = getattr(service, "_identityctl", None)
        if identityctl is not None and hasattr(identityctl, "_system_prompt"):
            setattr(identityctl, "_system_prompt", str(system_prompt or "").strip())
    except Exception:  # noqa: BLE001
        return


def _runner_turn_signatures(self, *, runner: BrainRunner, session_id: str) -> set[str]:
    signatures: set[str] = set()
    try:
        turns = runner.session_api.list_turns(session_id)
    except Exception:  # noqa: BLE001
        return signatures

    if isinstance(turns, list) and len(turns) > _TURN_SIGNATURE_WINDOW:
        turns = turns[-_TURN_SIGNATURE_WINDOW:]

    for item in turns:
        if not isinstance(item, dict):
            continue
        role_raw = str(item.get("role", item.get("turn_type", ""))).strip().lower()
        if role_raw in {"inbound", "user"}:
            role = "user"
        elif role_raw in {"outbound", "assistant"}:
            role = "assistant"
        elif role_raw == "system":
            role = "system"
        elif role_raw == "tool":
            role = "tool"
        else:
            role = "user"
        content = str(item.get("content", item.get("text", ""))).strip()
        if not content:
            continue
        signatures.add(self._turn_signature(role=role, content=content))
    return signatures


def _turn_signature(self, *, role: str, content: str) -> str:
    normalized_role = str(role).strip().lower() or "user"
    normalized_content = self._normalize_turn_content(
        role=normalized_role,
        content=content,
    )
    return f"{normalized_role}:{normalized_content}"


def _normalize_turn_content(self, *, role: str, content: str) -> str:
    text = str(content).strip()
    if not text:
        return ""
    if role != "assistant":
        return text

    prefixes = []
    try:
        default_agent_id = resolve_default_agent_id(self._config)
        agent_name = str(self._config.agents[default_agent_id].name or "").strip()
    except Exception:  # noqa: BLE001
        agent_name = ""
    if agent_name:
        prefixes.append(agent_name)
    prefixes.append("openminion")

    normalized = text
    for prefix in prefixes:
        pattern = re.compile(rf"^(?:{re.escape(prefix)}\s*:\s*)+", re.IGNORECASE)
        normalized = pattern.sub("", normalized).strip()
    return normalized or text


def _is_state_machine_error_text(value: str) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    return text.startswith("[system: unexecutable_tool_envelope]")


__all__ = [
    "_apply_runtime_system_prompt_override",
    "_collect_llm_call_counts_by_purpose",
    "_collect_system_history_context",
    "_hydrate_runner_session_context",
    "_inject_gateway_system_context",
    "_inject_resume_task_hints",
    "_is_state_machine_error_text",
    "_normalize_llm_purpose",
    "_normalize_turn_content",
    "_pending_history_turns_to_hydrate",
    "_resolve_turn_session_ids",
    "_runner_turn_signatures",
    "_runtime_system_prompt",
    "_turn_signature",
]
