from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any
from collections.abc import Mapping, Sequence

from openminion.modules.context.constants import PRIOR_TURN_CONTEXT_CHAR_LIMIT
from openminion.services.config import resolve_services_roots
from openminion.modules.prompting.context_blocks import (
    GROUNDING_BLOCK_HEADER,
    PENDING_TURN_BLOCK_HEADER,
    PRIOR_TURN_BLOCK_HEADER,
)

_GROUNDING_BLOCK_HEADER = GROUNDING_BLOCK_HEADER
_PENDING_TURN_BLOCK_HEADER = PENDING_TURN_BLOCK_HEADER
_PRIOR_TURN_BLOCK_HEADER = PRIOR_TURN_BLOCK_HEADER
_GROUNDING_BLOCK_BUDGET_TOKENS = 150


@dataclass(frozen=True)
class GroundingFacts:
    cwd: str
    workspace_root: str
    current_session_history_available: bool
    prior_session_history_available: bool
    prior_context_present: bool
    prior_turn_present: bool
    session_working_state_available: bool
    recent_artifacts: tuple["RecentArtifactFact", ...]
    recalled_memory_count: int = 0


@dataclass(frozen=True)
class RecentArtifactFact:
    ref: str
    path: str
    kind: str


def build_grounding_facts(
    *,
    runtime_env: Mapping[str, object] | None,
    home_root: str | Path | None,
    workspace_root: str | None,
    inbound_metadata: Mapping[str, Any] | None,
    tools: Any,
    include_session_working_state: bool,
    recalled_memory_count: int = 0,
    prior_context_present: bool = False,
    prior_turn_present: bool = False,
) -> GroundingFacts:
    metadata = dict(inbound_metadata or {})
    roots = resolve_services_roots(
        runtime_env=runtime_env,
        home_root=home_root,
        fallback_to_cwd=True,
    )
    resolved_workspace_root = _resolve_path_text(
        metadata.get("workspace_root"),
        fallback=workspace_root or str(roots.home_root),
    )
    resolved_cwd = _resolve_path_text(
        metadata.get("cwd"),
        fallback=resolved_workspace_root,
    )
    del tools
    has_recalled_memory = recalled_memory_count > 0
    return GroundingFacts(
        cwd=resolved_cwd,
        workspace_root=resolved_workspace_root,
        current_session_history_available=True,
        prior_session_history_available=has_recalled_memory,
        prior_context_present=prior_context_present,
        prior_turn_present=prior_turn_present,
        session_working_state_available=include_session_working_state,
        recent_artifacts=_recent_artifact_facts(
            metadata.get("recent_artifacts"),
        ),
        recalled_memory_count=recalled_memory_count,
    )


def append_grounding_blocks(
    *,
    system_prompt: str,
    facts: GroundingFacts,
    pending_turn_context: Mapping[str, Any] | None = None,
    prior_turn_hint: Mapping[str, Any] | str | None = None,
) -> str:
    sections = [str(system_prompt or "").strip(), _render_grounding_block(facts=facts)]
    pending_block = _render_pending_turn_context_block(
        pending_turn_context=pending_turn_context
    )
    if pending_block:
        sections.append(pending_block)
    prior_turn_block = _render_prior_turn_context_block(prior_turn_hint=prior_turn_hint)
    if prior_turn_block:
        sections.append(prior_turn_block)
    return "\n\n".join(section for section in sections if section).strip()


def grounding_block_budget_tokens() -> int:
    return _GROUNDING_BLOCK_BUDGET_TOKENS


def _render_grounding_block(*, facts: GroundingFacts) -> str:
    lines = [_GROUNDING_BLOCK_HEADER]
    lines.extend(
        [
            "facts:",
            f"- cwd: {facts.cwd}",
            f"- workspace_root: {facts.workspace_root}",
            "- current_session_history_available: "
            f"{_bool_text(facts.current_session_history_available)}",
            "- prior_session_history_available: "
            f"{_bool_text(facts.prior_session_history_available)}",
            f"- prior_context_present: {_bool_text(facts.prior_context_present)}",
            f"- prior_turn_present: {_bool_text(facts.prior_turn_present)}",
            "- session_working_state_available: "
            f"{_bool_text(facts.session_working_state_available)}",
        ]
    )
    if facts.recalled_memory_count > 0:
        lines.append(f"- recalled_memory_cards: {facts.recalled_memory_count}")
    rendered_recent_artifacts = _render_recent_artifacts(facts.recent_artifacts)
    if rendered_recent_artifacts:
        lines.append("- recent_artifacts: " + rendered_recent_artifacts)
    lines.append("")
    lines.append(_render_memory_capability_text(facts))
    if facts.prior_context_present:
        lines.append(
            "prior_context_block_present: A recalled memory summary block is "
            "present in your current grounding context from the OpenMinion "
            "runtime."
        )
    if facts.prior_turn_present:
        lines.append(
            "prior_turn_block_present: A 'Prior Turn Context' block is present "
            "with the immediately preceding completed user/assistant turn from "
            "this live session."
        )
    return "\n".join(lines).strip()


def _render_memory_capability_text(facts: GroundingFacts) -> str:
    base = ""
    if facts.recalled_memory_count > 0:
        base = (
            "memory_capability: You have cross-session memory provided by the "
            "OpenMinion runtime. Recalled preferences, lessons, tool outcomes, "
            "and prior work are included in your context. When asked what you "
            "remember or what kind of memory you have, describe the recalled "
            "facts currently in your context — do not say you have no memory."
        )
    else:
        base = (
            "memory_capability: You have session-scoped memory for this "
            "conversation. Cross-session memory may be available if durable "
            "records exist from prior sessions. When asked about your memory, "
            "describe what is currently in your context."
        )
    return (
        base + "\n"
        "interaction_policy: When asked about your own capabilities, memory, "
        "or how you work, answer conversationally from the grounding context "
        "above. Do not call system tools (exec.run, file.read, etc.) to check "
        "hardware memory or system specs — the question is about your AI "
        "capabilities, not the machine."
    )


def _render_pending_turn_context_block(
    *, pending_turn_context: Mapping[str, Any] | None
) -> str:
    if not isinstance(pending_turn_context, Mapping) or not pending_turn_context:
        return ""
    lines = [
        _PENDING_TURN_BLOCK_HEADER,
        "This is model-authored carry-forward from the previous turn. Use it as context only.",
    ]
    original_user_request = _bounded_text(
        pending_turn_context.get("original_user_request"),
        limit=200,
    )
    if original_user_request:
        lines.append(f"- original_user_request: {original_user_request}")
    active_work_summary = _bounded_text(
        pending_turn_context.get("active_work_summary"),
        limit=240,
    )
    if active_work_summary:
        lines.append(f"- active_work_summary: {active_work_summary}")
    known_context = _normalize_mapping(pending_turn_context.get("known_context"))
    if known_context:
        lines.append(
            "- known_context: "
            + ", ".join(
                f"{key}={value}" for key, value in list(known_context.items())[:4]
            )
        )
    missing_fields = _normalize_list(pending_turn_context.get("missing_fields"))
    if missing_fields:
        lines.append("- missing_fields: " + ", ".join(missing_fields[:4]))
    artifact_refs = _normalize_list(pending_turn_context.get("artifact_refs"))
    if artifact_refs:
        lines.append("- artifact_refs: " + ", ".join(artifact_refs[:4]))
    response_preferences = _normalize_mapping(
        pending_turn_context.get("response_preferences")
    )
    if response_preferences:
        lines.append(
            "- response_preferences: "
            + ", ".join(
                f"{key}={value}"
                for key, value in list(response_preferences.items())[:4]
            )
        )
    return "\n".join(lines).strip()


def _render_prior_turn_context_block(
    *, prior_turn_hint: Mapping[str, Any] | str | None
) -> str:
    if isinstance(prior_turn_hint, Mapping):
        user_text = _bounded_text(
            prior_turn_hint.get("user_message"),
            limit=PRIOR_TURN_CONTEXT_CHAR_LIMIT,
        )
        assistant_text = _bounded_text(
            prior_turn_hint.get("assistant_message"),
            limit=PRIOR_TURN_CONTEXT_CHAR_LIMIT,
        )
        tool_events = _normalize_list(
            prior_turn_hint.get("tool_events"),
            limit=PRIOR_TURN_CONTEXT_CHAR_LIMIT,
        )
        if not user_text and not assistant_text and not tool_events:
            return ""
        lines = [
            _PRIOR_TURN_BLOCK_HEADER,
            "Verbatim transcript from the immediately preceding turn. Use it as context only.",
        ]
        if user_text:
            lines.append(f"- user: {json.dumps(user_text)}")
        if assistant_text:
            lines.append(f"- assistant: {json.dumps(assistant_text)}")
        for event in tool_events[:3]:
            lines.append(f"- tool_event: {json.dumps(event)}")
        return "\n".join(lines).strip()
    text = _bounded_text(prior_turn_hint, limit=PRIOR_TURN_CONTEXT_CHAR_LIMIT)
    if not text:
        return ""
    return "\n".join(
        [
            _PRIOR_TURN_BLOCK_HEADER,
            f"- assistant: {json.dumps(text)}",
        ]
    ).strip()


def _resolve_path_text(value: Any, *, fallback: str) -> str:
    text = str(value or "").strip()
    if text:
        return str(Path(text).expanduser().resolve(strict=False))
    return str(Path(fallback).expanduser().resolve(strict=False))


def _normalize_mapping(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    normalized: dict[str, str] = {}
    for key, item in value.items():
        label = _bounded_text(key, limit=60)
        text = _bounded_text(item, limit=100)
        if label and text:
            normalized[label] = text
    return normalized


def _normalize_list(value: Any, *, limit: int = 100) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return []
    normalized: list[str] = []
    for item in value:
        text = _bounded_text(item, limit=limit)
        if text:
            normalized.append(text)
    return normalized


def _recent_artifact_facts(value: Any) -> tuple[RecentArtifactFact, ...]:
    parsed = value
    if isinstance(value, str):
        raw = str(value or "").strip()
        if not raw:
            return ()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return ()
    if not isinstance(parsed, Sequence) or isinstance(parsed, (str, bytes)):
        return ()

    normalized: list[RecentArtifactFact] = []
    for item in parsed:
        if not isinstance(item, Mapping):
            continue
        ref = _bounded_text(item.get("ref"), limit=80)
        path = _bounded_text(item.get("path"), limit=120)
        kind = _bounded_text(item.get("kind"), limit=60)
        if not any((ref, path, kind)):
            continue
        normalized.append(RecentArtifactFact(ref=ref, path=path, kind=kind))
    return tuple(normalized[:3])


def _render_recent_artifacts(
    recent_artifacts: Sequence[RecentArtifactFact],
) -> str:
    entries: list[str] = []
    for artifact in recent_artifacts[:3]:
        bits: list[str] = []
        if artifact.ref:
            bits.append(f"ref={artifact.ref}")
        if artifact.path:
            bits.append(f"path={artifact.path}")
        if artifact.kind:
            bits.append(f"kind={artifact.kind}")
        if bits:
            entries.append("{" + ", ".join(bits) + "}")
    return " | ".join(entries)


def _bounded_text(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").strip().split())
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3].rstrip() + "..."


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


__all__ = [
    "GroundingFacts",
    "RecentArtifactFact",
    "append_grounding_blocks",
    "build_grounding_facts",
    "grounding_block_budget_tokens",
]
