"""Segment-building and rendering helpers for context assembly."""

import json
from dataclasses import dataclass, field as _dc_field
from typing import Any, Callable

from ..constants import (
    ACTIVE_STATE_MAX_CHARS,
    ARTIFACT_PER_ITEM_MAX_TOKENS,
    ARTIFACT_PREVIEW_MAX_BULLETS,
    ARTIFACT_PREVIEW_MAX_CHARS,
    CONTEXT_BUCKET_RECENT_WINDOW,
    CONTEXT_DROP_VISIBILITY_BUCKET_LABELS,
    CONTEXT_DROP_VISIBILITY_NOTE_MAX_CHARS,
    CONTEXT_PURPOSE_DECIDE,
    PINNED_BUCKETS,
    RECENT_TURN_ASSISTANT_MAX_TOKENS,
)
from ..input_boundaries import route_and_ledger as _pidf_route_and_ledger
from ..mode_ranking import (
    _MODE_ACT,
    _MODE_PLAN,
    _MODE_RESPOND,
    normalize_mode_name,
)
from ..render.sections import (
    _render_active_plan,
    _render_task_digest,
    _render_trailer_feedback,
    judge_context_section,
    plan_context_section,
    reflect_context_section,
    response_instructions,
    task_header,
    validate_context_section,
)
from ..schemas import (
    ArtifactDigest,
    BuildConstraints,
    BuildPackRequest,
    ContextBudgets,
    ContextSegment,
    EvidenceItem,
    RecentSessionArtifactRef,
    RenderMessage,
    SessionSlice,
    SessionTurn,
    bucket_caps_for,
)
from .cache import segment_cache_fields, segment_render_cache_metadata
from .self_awareness import render_self_awareness_block


def _tool_inventory_lines(
    *,
    constraints: BuildConstraints,
    prompt_tool_schemas: list[dict[str, Any]],
) -> list[str]:
    seen: set[str] = set()
    lines: list[str] = []
    for item in list(constraints.runtime_tool_schemas) + list(prompt_tool_schemas):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("tool_name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        lines.append(name)
    return lines


def _content_hash(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _render_budget_telemetry_block(request: BuildPackRequest) -> str:
    payload = (
        dict(request.budget_telemetry)
        if isinstance(request.budget_telemetry, dict)
        else {}
    )
    if not payload:
        return ""
    return "[BUDGET TELEMETRY]\n" + json.dumps(
        payload,
        sort_keys=True,
        indent=2,
        ensure_ascii=True,
    )


def make_segment(
    seg_id: str,
    bucket: str,
    content: str,
    *,
    role: str = "system",
    refs: list[str] | None = None,
    is_artifact_preview: bool = False,
    pinned: bool = False,
    estimate_tokens: Callable[[str], int],
) -> ContextSegment:
    is_cacheable = bucket == "static_prefix"
    content_hash = _content_hash(content) if content else ""
    return ContextSegment(
        id=seg_id,
        bucket=bucket,  # type: ignore[arg-type]
        role=role,  # type: ignore[arg-type]
        content=content,
        token_estimate=estimate_tokens(content) if content else 0,
        content_hash=content_hash,
        refs=refs or [],
        is_artifact_preview=is_artifact_preview,
        is_cacheable=is_cacheable,
        **segment_cache_fields(bucket, content_hash),
        pinned=pinned or bucket in PINNED_BUCKETS,
    )


def _candidate_label(count: int) -> str:
    return "candidate" if count == 1 else "candidates"


def render_context_drop_visibility_note(drop_counts: dict[str, int]) -> str:
    items = [
        (CONTEXT_DROP_VISIBILITY_BUCKET_LABELS.get(bucket, bucket), count)
        for bucket, count in drop_counts.items()
        if count > 0
    ]
    if not items:
        return ""

    parts = [
        f"{count} {label} {_candidate_label(count)}" for label, count in sorted(items)
    ]
    if len(parts) == 1:
        counts_text = parts[0]
    else:
        counts_text = ", ".join(parts[:-1]) + f", and {parts[-1]}"
    verb = "was" if len(items) == 1 and items[0][1] == 1 else "were"
    note = f"[context budget: {counts_text} {verb} not included due to budget.]"
    return note[:CONTEXT_DROP_VISIBILITY_NOTE_MAX_CHARS].rstrip()


def inject_context_drop_visibility_note(
    *,
    segments: list[ContextSegment],
    drop_counts: dict[str, int],
    estimate_tokens: Callable[[str], int],
) -> list[ContextSegment]:
    note = render_context_drop_visibility_note(drop_counts)
    if not note:
        return segments

    note_segment = make_segment(
        "context_drop_visibility",
        "static_prefix",
        note,
        pinned=True,
        estimate_tokens=estimate_tokens,
    )
    insert_at = next(
        (
            idx + 1
            for idx, segment in enumerate(segments)
            if segment.bucket == "static_prefix"
        ),
        0,
    )
    return [*segments[:insert_at], note_segment, *segments[insert_at:]]


def map_turn_role(role: str) -> str:
    normalized = role.strip().lower()
    if normalized in {"user", "assistant", "system", "tool"}:
        return normalized
    return (
        "user"
        if normalized in {"inbound"}
        else "assistant"
        if normalized == "outbound"
        else "user"
    )


def protected_decide_recent_turn_indexes(
    recent_turns: list[SessionTurn], *, purpose: str
) -> set[int]:
    if purpose == CONTEXT_PURPOSE_DECIDE:
        for idx in range(len(recent_turns) - 1, -1, -1):
            if map_turn_role(recent_turns[idx].role) != "assistant":
                continue
            protected = {idx}
            if idx > 0 and map_turn_role(recent_turns[idx - 1].role) == "user":
                protected.add(idx - 1)
            return protected
        return set()

    has_assistant = any(
        map_turn_role(turn.role) == "assistant" for turn in recent_turns
    )
    if has_assistant:
        return set()
    user_indexes = [
        idx
        for idx, turn in enumerate(recent_turns)
        if map_turn_role(turn.role) == "user"
    ]
    if not user_indexes:
        return set()
    protected: set[int] = {user_indexes[0]}
    if len(user_indexes) > 1:
        protected.add(user_indexes[-1])
    return protected


def _assistant_tail_for_recent_window(
    turn: SessionTurn,
    *,
    purpose: str,
    estimate_tokens: Callable[[str], int],
    preserve_full: bool = False,
) -> str:
    content = str(turn.content or "")
    if (
        preserve_full
        or purpose != CONTEXT_PURPOSE_DECIDE
        or map_turn_role(turn.role) != "assistant"
    ):
        return content
    if estimate_tokens(content) <= RECENT_TURN_ASSISTANT_MAX_TOKENS:
        return content
    marker = "[...truncated, full response available in session]\n"
    max_chars = RECENT_TURN_ASSISTANT_MAX_TOKENS * 4
    return marker + content[-max_chars:].lstrip()


def segments_to_messages(segments: list[ContextSegment]) -> list[RenderMessage]:
    bucket_order = [
        "static_prefix",
        "mission_snapshot",
        "budget_telemetry",
        "task_digest",
        "summaries",
        "conversation_summary",
        "active_plan",
        "trailer_feedback",
        CONTEXT_BUCKET_RECENT_WINDOW,
        "retrieval",
        "evidence_refs",
        "turn_input",
    ]
    ordered = sorted(
        segments,
        key=lambda segment: (
            bucket_order.index(segment.bucket) if segment.bucket in bucket_order else 99
        ),
    )

    merged_system: dict[str, list[str]] = {}
    merged_system_cache_control: dict[str, dict[str, Any]] = {}
    merged_system_segment_ids: dict[str, list[str]] = {}
    merged_system_refs: dict[str, list[str]] = {}
    for segment in ordered:
        if not segment.content.strip():
            continue
        role = segment.role
        if (
            role not in {"user", "assistant", "tool"}
            and segment.bucket != CONTEXT_BUCKET_RECENT_WINDOW
        ):
            merged_system.setdefault(segment.bucket, []).append(segment.content)
            merged_system_segment_ids.setdefault(segment.bucket, []).append(segment.id)
            refs = merged_system_refs.setdefault(segment.bucket, [])
            for ref in segment.refs:
                if ref not in refs:
                    refs.append(ref)
            if segment.is_cacheable:
                merged_system_cache_control.setdefault(
                    segment.bucket,
                    {"type": "ephemeral"},
                )

    result: list[RenderMessage] = []
    seen_buckets: set[str] = set()
    for segment in ordered:
        if not segment.content.strip():
            continue
        if segment.bucket in merged_system and segment.bucket not in seen_buckets:
            seen_buckets.add(segment.bucket)
            result.append(
                RenderMessage(
                    role="system",
                    content="\n\n".join(merged_system[segment.bucket]),
                    cache_control=merged_system_cache_control.get(segment.bucket),
                    meta={
                        "block_kind": segment.bucket,
                        "cache_eligible": bool(
                            segment.bucket in merged_system_cache_control
                        ),
                        "segment_ids": list(
                            merged_system_segment_ids.get(segment.bucket, [])
                        ),
                        "refs": list(merged_system_refs.get(segment.bucket, [])),
                        **segment_render_cache_metadata(segment),
                    },
                )
            )
        elif segment.bucket == CONTEXT_BUCKET_RECENT_WINDOW or segment.role in {
            "user",
            "assistant",
            "tool",
        }:
            result.append(
                RenderMessage(
                    role=segment.role,  # type: ignore[arg-type]
                    content=segment.content,
                    meta={
                        "block_kind": segment.bucket,
                        "cache_eligible": bool(segment.is_cacheable),
                        "segment_ids": [segment.id],
                        "refs": list(segment.refs),
                        **segment_render_cache_metadata(segment),
                    },
                )
            )

    return result


@dataclass
class _SegmentAssemblyRuntime:
    budgets: ContextBudgets
    fit_to_budget: Callable[[str, int], tuple[str, bool]]
    estimate_tokens: Callable[[str], int]
    segments: list[ContextSegment] = _dc_field(default_factory=list)
    bucket_stats: dict[str, Any] = _dc_field(default_factory=dict)
    truncation_stats: dict[str, int] = _dc_field(default_factory=dict)

    def fit_section(self, section: str, text: str, cap_tokens: int) -> str:
        fitted, truncated = self.fit_to_budget(text, cap_tokens)
        if truncated:
            self.truncation_stats[section] = self.truncation_stats.get(section, 0) + 1
        return fitted

    def make(
        self,
        seg_id: str,
        bucket: str,
        content: str,
        *,
        role: str = "system",
        refs: list[str] | None = None,
        is_artifact_preview: bool = False,
        pinned: bool = False,
    ) -> ContextSegment:
        return make_segment(
            seg_id,
            bucket,
            content,
            role=role,
            refs=refs,
            is_artifact_preview=is_artifact_preview,
            pinned=pinned,
            estimate_tokens=self.estimate_tokens,
        )


def append_prefix_and_mission_segments(
    runtime: _SegmentAssemblyRuntime,
    *,
    request: BuildPackRequest,
    constraints: BuildConstraints,
    prompt_tool_schemas: list[dict[str, Any]],
    identity_text: str,
    session_slice: SessionSlice,
    prefix_builder: Any,
    project_active_state_to_prompt_view: Callable[
        [dict[str, Any] | None], tuple[Any, dict[str, int]]
    ],
    build_clarify_digest: Callable[[dict[str, Any] | None], str],
    logger: Any | None,
) -> None:
    identity_block = runtime.fit_section(
        "identity", identity_text, runtime.budgets.identity_tokens
    )
    tool_schemas: list[Any] = []
    if constraints.output_schema is not None:
        tool_schemas.append(
            {"name": "output_schema", "schema": constraints.output_schema}
        )
    for item in prompt_tool_schemas:
        if item not in tool_schemas:
            tool_schemas.append(item)
    static_content = prefix_builder.build(
        identity_text=identity_block,
        tool_schemas=tool_schemas,
        policy_rules=[f"safety_tag:{tag}" for tag in sorted(constraints.safety_tags)],
    )
    runtime.bucket_stats["static_prefix"] = {"total_available": 1, "dropped": 0}
    runtime.segments.append(
        runtime.make("static_prefix", "static_prefix", static_content, pinned=True)
    )

    task_header_text = task_header(request, constraints)
    plan_context_text = plan_context_section(request)
    judge_context_text = judge_context_section(request)
    reflect_context_text = reflect_context_section(request)
    validate_context_text = validate_context_section(request)
    tool_inventory = _tool_inventory_lines(
        constraints=constraints,
        prompt_tool_schemas=prompt_tool_schemas,
    )
    state_lines: list[str] = []
    prompt_view, projection_metrics = project_active_state_to_prompt_view(
        session_slice.active_state
    )
    if prompt_view:
        view_json = prompt_view.model_dump_json()
        if len(view_json) > ACTIVE_STATE_MAX_CHARS:
            view_json = view_json[:ACTIVE_STATE_MAX_CHARS]
        state_lines.append("Active state: " + view_json)
    if projection_metrics["raw_chars"] > 0 and logger is not None:
        logger.info(
            "ASPM-05: active_state_prompt_composition metrics: raw_chars=%d projected_chars=%d chars_saved=%d",
            projection_metrics["raw_chars"],
            projection_metrics["projected_chars"],
            projection_metrics["chars_saved"],
        )
    if session_slice.open_tasks:
        state_lines.extend(f"Task: {task}" for task in session_slice.open_tasks)
    clarify_digest = build_clarify_digest(session_slice.active_state)
    clarify_block = (
        runtime.fit_section(
            "clarify_digest",
            clarify_digest,
            max(8, runtime.budgets.instructions_tokens // 2),
        )
        if clarify_digest
        else ""
    )
    constraints_text = response_instructions(constraints)
    constraints_block = runtime.fit_section(
        "constraints",
        constraints_text,
        runtime.budgets.instructions_tokens,
    )
    mode_context_lines: list[str] = []
    mode_name = normalize_mode_name(request.mode_name)
    if mode_name == _MODE_RESPOND:
        mode_context_lines.append(
            "respond mode: favor concise summaries and recent factual context. "
            "If the session already contains a recent greeting exchange, continue the "
            "conversation instead of restarting it with the same opener."
        )
    elif mode_name == _MODE_PLAN:
        mode_context_lines.append(
            "plan mode: preserve constraints, procedures, and available tools."
        )
        mode_context_lines.extend(f"- {name}" for name in tool_inventory[:12])
    elif mode_name == _MODE_ACT:
        mode_context_lines.append(
            "act mode: keep tactical context for the current execution loop."
        )
        mode_context_lines.extend(f"- {name}" for name in tool_inventory[:8])
    gateway_ctx = str(getattr(request, "gateway_system_context", "") or "").strip()
    gateway_block = ""
    if gateway_ctx:
        gateway_block = runtime.fit_section(
            "gateway_context",
            gateway_ctx,
            max(8, runtime.budgets.instructions_tokens),
        )
    mission_content = "\n\n".join(
        filter(
            None,
            [
                (
                    "[MODE CONTEXT]\n" + "\n".join(mode_context_lines)
                    if mode_context_lines
                    else ""
                ),
                (
                    f"[GATEWAY MEMORY CONTEXT]\n{gateway_block}"
                    if gateway_block.strip()
                    else ""
                ),
                f"[CLARIFY DIGEST]\n{clarify_block}" if clarify_block.strip() else "",
                f"[TASK HEADER]\n{task_header_text}",
                plan_context_text,
                judge_context_text,
                reflect_context_text,
                validate_context_text,
                "\n".join(state_lines) if state_lines else "",
                (
                    f"[CONSTRAINTS & POLICY]\n{constraints_block}"
                    if constraints_block.strip()
                    else ""
                ),
            ],
        )
    )
    if not mission_content.strip():
        raise RuntimeError("MISSION_CONTEXT_MISSING")
    runtime.bucket_stats["mission_snapshot"] = {"total_available": 1, "dropped": 0}
    runtime.segments.append(
        runtime.make(
            "mission_snapshot", "mission_snapshot", mission_content, pinned=True
        )
    )

    budget_telemetry_items = 0
    budget_telemetry_block = _render_budget_telemetry_block(request)
    if budget_telemetry_block.strip():
        runtime.segments.append(
            runtime.make(
                "budget_telemetry",
                "budget_telemetry",
                budget_telemetry_block,
                pinned=True,
            )
        )
        budget_telemetry_items = 1
    runtime.bucket_stats["budget_telemetry"] = {
        "total_available": budget_telemetry_items,
        "dropped": 0,
    }

    self_awareness_items = 0
    self_awareness_block = render_self_awareness_block(request)
    if self_awareness_block.strip():
        cap = max(
            32,
            bucket_caps_for(runtime.budgets).get(
                "self_awareness",
                runtime.budgets.instructions_tokens,
            ),
        )
        runtime.segments.append(
            runtime.make(
                "self_awareness",
                "self_awareness",
                runtime.fit_section("self_awareness", self_awareness_block, cap),
                pinned=True,
            )
        )
        self_awareness_items = 1
    runtime.bucket_stats["self_awareness"] = {
        "total_available": self_awareness_items,
        "dropped": 0,
    }

    task_digest_items = 0
    task_digest = session_slice.task_digest
    if (
        request.purpose == CONTEXT_PURPOSE_DECIDE
        and isinstance(task_digest, dict)
        and task_digest
        and runtime.budgets.task_digest_tokens > 0
    ):
        task_digest_block = runtime.fit_section(
            "task_digest",
            _render_task_digest(task_digest),
            runtime.budgets.task_digest_tokens,
        )
        if task_digest_block.strip():
            runtime.segments.append(
                runtime.make(
                    "task_digest",
                    "task_digest",
                    "[TASK DIGEST]\n" + task_digest_block,
                    pinned=True,
                )
            )
            task_digest_items = 1
    runtime.bucket_stats["task_digest"] = {
        "total_available": task_digest_items,
        "dropped": 0,
    }

    active_plan_items = 0
    active_plan = session_slice.active_task_plan
    if (
        request.purpose == CONTEXT_PURPOSE_DECIDE
        and active_plan is not None
        and active_plan.status == "active"
        and runtime.budgets.active_plan_tokens > 0
    ):
        active_plan_block = runtime.fit_section(
            "active_plan",
            _render_active_plan(active_plan),
            runtime.budgets.active_plan_tokens,
        )
        if active_plan_block.strip():
            runtime.segments.append(
                runtime.make(
                    "active_plan",
                    "active_plan",
                    "[ACTIVE PLAN]\n" + active_plan_block,
                    pinned=True,
                )
            )
            active_plan_items = 1
    runtime.bucket_stats["active_plan"] = {
        "total_available": active_plan_items,
        "dropped": 0,
    }

    trailer_feedback_items = 0
    pending_feedback = session_slice.pending_trailer_feedback
    if (
        request.purpose == CONTEXT_PURPOSE_DECIDE
        and isinstance(pending_feedback, dict)
        and pending_feedback
        and runtime.budgets.trailer_feedback_tokens > 0
    ):
        feedback_text = _render_trailer_feedback(pending_feedback)
        feedback_block = runtime.fit_section(
            "trailer_feedback",
            feedback_text,
            runtime.budgets.trailer_feedback_tokens,
        )
        if feedback_block.strip():
            runtime.segments.append(
                runtime.make(
                    "trailer_feedback",
                    "trailer_feedback",
                    "[TRAILER FEEDBACK]\n" + feedback_block,
                    pinned=True,
                )
            )
            trailer_feedback_items = 1
    runtime.bucket_stats["trailer_feedback"] = {
        "total_available": trailer_feedback_items,
        "dropped": 0,
    }


def append_recent_window_segments(
    runtime: _SegmentAssemblyRuntime,
    *,
    request: BuildPackRequest,
    session_slice: SessionSlice,
) -> None:
    recent_turns = list(session_slice.recent_turns)
    current_query = str(request.query or "").strip()
    while recent_turns:
        last_turn = recent_turns[-1]
        last_role = map_turn_role(last_turn.role)
        last_content = str(last_turn.content or "").strip()
        if last_role == "user" and current_query and last_content == current_query:
            recent_turns.pop()
            continue
        break
    protected_indexes = protected_decide_recent_turn_indexes(
        recent_turns, purpose=request.purpose
    )
    recent_turn_contents = [
        _assistant_tail_for_recent_window(
            turn,
            purpose=request.purpose,
            estimate_tokens=runtime.estimate_tokens,
            preserve_full=idx in protected_indexes,
        )
        for idx, turn in enumerate(recent_turns)
    ]
    turn_tokens = sum(
        runtime.estimate_tokens(content) for content in recent_turn_contents
    )
    recent_turn_budget = runtime.budgets.recent_turn_tokens
    mode_name = normalize_mode_name(request.mode_name)
    if mode_name == _MODE_RESPOND:
        recent_turn_budget = max(
            160,
            min(
                runtime.budgets.recent_turn_tokens,
                runtime.budgets.recent_turn_tokens // 2,
            ),
        )
    elif mode_name == _MODE_ACT:
        recent_turn_budget = max(
            220,
            min(
                runtime.budgets.recent_turn_tokens,
                runtime.budgets.recent_turn_tokens - 120,
            ),
        )
    runtime.bucket_stats[CONTEXT_BUCKET_RECENT_WINDOW] = {
        "total_available": len(recent_turns),
        "dropped": 0,
    }
    while recent_turns and turn_tokens > recent_turn_budget:
        protected_indexes = protected_decide_recent_turn_indexes(
            recent_turns, purpose=request.purpose
        )
        drop_index = next(
            (idx for idx in range(len(recent_turns)) if idx not in protected_indexes),
            None,
        )
        if drop_index is None:
            break
        recent_turns.pop(drop_index)
        recent_turn_contents.pop(drop_index)
        runtime.bucket_stats[CONTEXT_BUCKET_RECENT_WINDOW]["dropped"] += 1
        turn_tokens = sum(
            runtime.estimate_tokens(content) for content in recent_turn_contents
        )
    protected_indexes = protected_decide_recent_turn_indexes(
        recent_turns, purpose=request.purpose
    )
    for idx, (turn, content) in enumerate(zip(recent_turns, recent_turn_contents)):
        runtime.segments.append(
            runtime.make(
                f"turn:{turn.turn_id}",
                CONTEXT_BUCKET_RECENT_WINDOW,
                content,
                role=map_turn_role(turn.role),
                pinned=idx in protected_indexes,
            )
        )


def append_evidence_and_turn_input_segments(
    runtime: _SegmentAssemblyRuntime,
    *,
    request: BuildPackRequest,
    session_slice: SessionSlice,
    artifact_digests: list[ArtifactDigest],
    recent_session_artifact_refs: list[RecentSessionArtifactRef],
    plugin_registry: Any,
    run_plugin_evidence_pipeline: Callable[..., list[EvidenceItem]],
) -> None:
    tool_events = list(session_slice.recent_tool_events[:3])
    runtime.bucket_stats["evidence_refs"] = {
        "total_available": len(artifact_digests[:10])
        + len(tool_events)
        + len(recent_session_artifact_refs[:6]),
        "dropped": 0,
    }
    evidence_segments: list[ContextSegment] = []
    if recent_session_artifact_refs:
        recent_artifact_lines = ["Recent session artifacts:"]
        for item in recent_session_artifact_refs[:6]:
            metadata = [f"type={item.artifact_type}", f"path={item.artifact_path}"]
            if item.artifact_digest:
                metadata.append(f"digest={item.artifact_digest}")
            metadata.append(f"session={item.session_id}")
            metadata.append(f"turn={item.turn_index}")
            if item.tool_name:
                metadata.append(f"tool={item.tool_name}")
            recent_artifact_lines.append("- " + " | ".join(metadata))
        recent_artifact_text = runtime.fit_section(
            "evidence_recent_session_artifacts",
            "\n".join(recent_artifact_lines),
            runtime.budgets.artifact_tokens,
        )
        if recent_artifact_text.strip():
            evidence_segments.append(
                runtime.make(
                    "evidence:recent_session_artifacts",
                    "evidence_refs",
                    f"[RECENT SESSION ARTIFACTS]\n{recent_artifact_text}",
                    refs=[item.record_id for item in recent_session_artifact_refs],
                    is_artifact_preview=True,
                )
            )
    for tool_event in tool_events:
        excerpt = tool_event.excerpt.strip()
        if len(excerpt) > ARTIFACT_PREVIEW_MAX_CHARS:
            excerpt = excerpt[:ARTIFACT_PREVIEW_MAX_CHARS].rstrip() + "..."
        lines = [
            f"Tool summary: {tool_event.tool_name}",
            f"event_id: {tool_event.event_id}",
            f"excerpt: {excerpt}",
        ]
        if tool_event.artifact_refs:
            lines.append("artifact_refs: " + ", ".join(tool_event.artifact_refs[:3]))
        tool_text = runtime.fit_section(
            "evidence_tool",
            "\n".join(lines),
            ARTIFACT_PER_ITEM_MAX_TOKENS,
        )
        if tool_text.strip():
            evidence_segments.append(
                runtime.make(
                    f"toolsum:{tool_event.event_id}",
                    "evidence_refs",
                    tool_text,
                    refs=[tool_event.event_id, *tool_event.artifact_refs[:3]],
                    is_artifact_preview=True,
                )
            )
    for artifact in artifact_digests[:10]:
        preview_lines = [f"Artifact: {artifact.ref}"]
        if artifact.digest_hash:
            preview_lines.append(f"hash: {artifact.digest_hash}")
        preview_lines.extend(
            f"- {bullet}" for bullet in artifact.bullets[:ARTIFACT_PREVIEW_MAX_BULLETS]
        )
        if artifact.excerpt:
            excerpt = (
                artifact.excerpt[:ARTIFACT_PREVIEW_MAX_CHARS] + "..."
                if len(artifact.excerpt) > ARTIFACT_PREVIEW_MAX_CHARS
                else artifact.excerpt
            )
            preview_lines.append(f"excerpt: {excerpt}")
        preview_text = runtime.fit_section(
            "evidence_artifact",
            "\n".join(preview_lines),
            ARTIFACT_PER_ITEM_MAX_TOKENS,
        )
        if preview_text.strip():
            evidence_segments.append(
                runtime.make(
                    f"evidence:{artifact.ref}",
                    "evidence_refs",
                    preview_text,
                    refs=[artifact.ref],
                    is_artifact_preview=True,
                )
            )
    if plugin_registry.retriever_names:
        plugin_items = run_plugin_evidence_pipeline(
            request=request,
            query=request.query,
            k=10,
        )
        runtime.bucket_stats["evidence_refs"]["total_available"] += len(plugin_items)
        for item in plugin_items:
            seg_id = f"plugin_ev:{item.ref}"
            if any(seg_id == segment.id for segment in evidence_segments):
                continue
            item_text = runtime.fit_section(
                "evidence_plugin",
                item.content,
                ARTIFACT_PER_ITEM_MAX_TOKENS,
            )
            if item_text.strip():
                evidence_segments.append(
                    runtime.make(
                        seg_id,
                        "evidence_refs",
                        item_text,
                        refs=[item.ref],
                        is_artifact_preview=True,
                    )
                )
    runtime.segments.extend(evidence_segments)
    turn_rendered, _ = _pidf_route_and_ledger(
        "user_message",
        request.query.strip(),
        seam_id="modules.context.segment_assembly.turn_input",
    )
    runtime.segments.append(
        runtime.make(
            "turn_input",
            "turn_input",
            turn_rendered,
            role="user",
            pinned=True,
        )
    )
