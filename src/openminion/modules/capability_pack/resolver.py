from __future__ import annotations

from collections.abc import Callable, Iterable

from .schemas import (
    ActiveCapabilityPack,
    CapabilityPackAuditEvent,
    CapabilityPackEventType,
    CapabilityPackManifest,
)

AuditSink = Callable[[CapabilityPackAuditEvent], None]


def activate_pack(
    manifest: CapabilityPackManifest,
    *,
    session_id: str,
    available_tools: Iterable[str],
    available_skills: Iterable[str],
    override_tools: Iterable[str] = (),
    audit_sink: AuditSink | None = None,
) -> ActiveCapabilityPack:
    available_tool_set = set(available_tools)
    available_skill_set = set(available_skills)
    declared_tools = {item.tool_id for item in manifest.tools}
    declared_skills = {item.skill_id for item in manifest.skills}
    overrides = set(override_tools)
    required_tools = declared_tools | set(manifest.baseline_tools) | overrides
    missing_tools = required_tools - available_tool_set
    missing_skills = declared_skills - available_skill_set
    if missing_tools or missing_skills:
        reason = (
            "capability pack dependencies unavailable: "
            f"tools={sorted(missing_tools)!r}, skills={sorted(missing_skills)!r}"
        )
        if audit_sink is not None:
            audit_sink(
                CapabilityPackAuditEvent(
                    event_type="capability_pack.activation_denied",
                    pack_id=manifest.pack_id,
                    session_id=session_id,
                    reason=reason,
                )
            )
        raise ValueError(reason)
    visible_tools = tuple(sorted(required_tools))
    visible_skills = tuple(sorted(declared_skills))
    event_type: CapabilityPackEventType = (
        "capability_pack.override_applied" if overrides else "capability_pack.activated"
    )
    event = CapabilityPackAuditEvent(
        event_type=event_type,
        pack_id=manifest.pack_id,
        session_id=session_id,
        visible_tools=visible_tools,
        visible_skills=visible_skills,
        override_tools=tuple(sorted(overrides)),
    )
    if audit_sink is not None:
        audit_sink(event)
    return ActiveCapabilityPack(
        pack_id=manifest.pack_id,
        version=manifest.version,
        session_id=session_id,
        visible_tools=visible_tools,
        visible_skills=visible_skills,
        policy_profile=manifest.policy_profile,
        audit_event=event,
    )
