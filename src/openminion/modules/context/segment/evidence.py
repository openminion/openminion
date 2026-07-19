"""Evidence and final user-turn segment assembly."""

from __future__ import annotations

from typing import Any, Callable

from ..constants import (
    ARTIFACT_PER_ITEM_MAX_TOKENS,
    ARTIFACT_PREVIEW_MAX_BULLETS,
    ARTIFACT_PREVIEW_MAX_CHARS,
)
from ..input_boundaries import route_and_ledger as _pidf_route_and_ledger
from ..schemas import (
    ArtifactDigest,
    BuildPackRequest,
    ContextSegment,
    EvidenceItem,
    RecentSessionArtifactRef,
    SessionSlice,
)
from .runtime import _SegmentAssemblyRuntime


def _recent_artifact_segments(
    runtime: _SegmentAssemblyRuntime,
    recent_session_artifact_refs: list[RecentSessionArtifactRef],
) -> list[ContextSegment]:
    if not recent_session_artifact_refs:
        return []
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
    if not recent_artifact_text.strip():
        return []
    return [
        runtime.make(
            "evidence:recent_session_artifacts",
            "evidence_refs",
            f"[RECENT SESSION ARTIFACTS]\n{recent_artifact_text}",
            refs=[item.record_id for item in recent_session_artifact_refs],
            is_artifact_preview=True,
        )
    ]


def _tool_event_segments(runtime: _SegmentAssemblyRuntime, session_slice: SessionSlice) -> list[ContextSegment]:
    segments: list[ContextSegment] = []
    for tool_event in session_slice.recent_tool_events[:3]:
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
            segments.append(
                runtime.make(
                    f"toolsum:{tool_event.event_id}",
                    "evidence_refs",
                    tool_text,
                    refs=[tool_event.event_id, *tool_event.artifact_refs[:3]],
                    is_artifact_preview=True,
                )
            )
    return segments


def _artifact_digest_segments(runtime: _SegmentAssemblyRuntime, artifact_digests: list[ArtifactDigest]) -> list[ContextSegment]:
    segments: list[ContextSegment] = []
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
            segments.append(
                runtime.make(
                    f"evidence:{artifact.ref}",
                    "evidence_refs",
                    preview_text,
                    refs=[artifact.ref],
                    is_artifact_preview=True,
                )
            )
    return segments


def _plugin_evidence_segments(
    runtime: _SegmentAssemblyRuntime,
    *,
    request: BuildPackRequest,
    plugin_registry: Any,
    existing_segments: list[ContextSegment],
    run_plugin_evidence_pipeline: Callable[..., list[EvidenceItem]],
) -> list[ContextSegment]:
    if not plugin_registry.retriever_names:
        return []
    segments: list[ContextSegment] = []
    plugin_items = run_plugin_evidence_pipeline(request=request, query=request.query, k=10)
    runtime.bucket_stats["evidence_refs"]["total_available"] += len(plugin_items)
    for item in plugin_items:
        seg_id = f"plugin_ev:{item.ref}"
        if any(seg_id == segment.id for segment in [*existing_segments, *segments]):
            continue
        item_text = runtime.fit_section(
            "evidence_plugin",
            item.content,
            ARTIFACT_PER_ITEM_MAX_TOKENS,
        )
        if item_text.strip():
            segments.append(
                runtime.make(
                    seg_id,
                    "evidence_refs",
                    item_text,
                    refs=[item.ref],
                    is_artifact_preview=True,
                )
            )
    return segments


def _append_turn_input(runtime: _SegmentAssemblyRuntime, request: BuildPackRequest) -> None:
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
    evidence_segments = [
        *_recent_artifact_segments(runtime, recent_session_artifact_refs),
        *_tool_event_segments(runtime, session_slice),
        *_artifact_digest_segments(runtime, artifact_digests),
    ]
    evidence_segments.extend(
        _plugin_evidence_segments(
            runtime,
            request=request,
            plugin_registry=plugin_registry,
            existing_segments=evidence_segments,
            run_plugin_evidence_pipeline=run_plugin_evidence_pipeline,
        )
    )
    runtime.segments.extend(evidence_segments)
    _append_turn_input(runtime, request)
