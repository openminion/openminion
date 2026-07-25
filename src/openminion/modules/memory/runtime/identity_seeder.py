import logging
from typing import Any

from openminion.base.time import utc_now_iso as _utc_now_iso

_log = logging.getLogger(__name__)


def _write_pin(
    *,
    memory_service: Any,
    agent_id: str,
    key: str,
    title: str,
    content: str,
) -> None:
    """Write (or overwrite) a pin record in agent:<agent_id> scope.

    Uses the public memory service upsert API so repeated calls update the
    record instead of duplicating it.
    """
    scope = f"agent:{agent_id}"
    try:
        upsert_record = getattr(memory_service, "upsert_record", None)
        record_patch = {
            "title": title,
            "content": content,
            "tags": ["identity"],
            "source": "imported",
            "confidence": 1.0,
            "visibility": "shared",
            "updated_at": _utc_now_iso(),
        }
        if callable(upsert_record):
            memory_service.upsert_record(
                scope=scope,
                record_type="pin",
                key=key,
                record_patch=record_patch,
                agent_id=agent_id,
            )
        else:
            memory_service.write_record(
                scope=scope,
                record_type="pin",
                title=title,
                content=content,
                tags=["identity"],
            )
    except Exception as exc:
        _log.warning(
            "identity_seeder: failed to write pin key=%s agent_id=%s error=%s",
            key,
            agent_id,
            exc,
        )
        raise


def _get_current_version(memory_service: Any, agent_id: str) -> str | None:
    """Return the content of the identity_profile_version pin, or None if absent."""
    from openminion.modules.memory.storage.base import SearchQueryOptions

    scope = f"agent:{agent_id}"
    try:
        results = memory_service.search(
            SearchQueryOptions(
                query="identity_profile_version",
                scopes=[scope],
                types=["pin"],
                limit=5,
            )
        )
        for rec in results:
            key = getattr(rec, "key", None) or ""
            title = str(getattr(rec, "title", "") or "")
            if key == "identity_profile_version" or title == "identity_profile_version":
                content = getattr(rec, "content", None)
                if isinstance(content, str):
                    return content
                if isinstance(content, dict):
                    return str(content.get("value", content.get("text", "")))
    except Exception:
        pass
    return None


def _write_scalar_pin(
    *,
    memory_service: Any,
    agent_id: str,
    section: str,
    field: str,
    value: object,
) -> int:
    content = str(value or "").strip()
    if not content:
        return 0
    key = f"identity_{section}_{field}"
    _write_pin(
        memory_service=memory_service,
        agent_id=agent_id,
        key=key,
        title=key,
        content=content,
    )
    return 1


def _write_list_pin(
    *,
    memory_service: Any,
    agent_id: str,
    section: str,
    field: str,
    values: object,
) -> int:
    items = [str(item).strip() for item in list(values or []) if str(item).strip()]
    if not items:
        return 0
    key = f"identity_{section}_{field}"
    _write_pin(
        memory_service=memory_service,
        agent_id=agent_id,
        key=key,
        title=key,
        content="\n".join(f"- {item}" for item in items),
    )
    return 1


def _write_legacy_pin(
    *,
    memory_service: Any,
    agent_id: str,
    key: str,
    content: str,
) -> int:
    if not content:
        return 0
    _write_pin(
        memory_service=memory_service,
        agent_id=agent_id,
        key=key,
        title=key,
        content=content,
    )
    return 1


def _seed_role_pins(*, profile: Any, memory_service: Any, agent_id: str) -> int:
    role = getattr(profile, "role", None)
    if role is None:
        return 0

    written = 0
    mission = str(getattr(role, "mission", "") or "").strip()
    written += _write_legacy_pin(
        memory_service=memory_service,
        agent_id=agent_id,
        key="identity_mission",
        content=mission,
    )
    written += _write_scalar_pin(
        memory_service=memory_service,
        agent_id=agent_id,
        section="role",
        field="mission",
        value=mission,
    )

    role_lists = (
        ("responsibilities", "identity_responsibilities"),
        ("hard_constraints", "identity_constraints"),
        ("domain", "identity_domain"),
    )
    for field, legacy_key in role_lists:
        values = list(getattr(role, field, None) or [])
        legacy_content = "\n".join(f"- {item}" for item in values)
        written += _write_legacy_pin(
            memory_service=memory_service,
            agent_id=agent_id,
            key=legacy_key,
            content=legacy_content,
        )
        written += _write_list_pin(
            memory_service=memory_service,
            agent_id=agent_id,
            section="role",
            field=field,
            values=values,
        )
    written += _write_list_pin(
        memory_service=memory_service,
        agent_id=agent_id,
        section="role",
        field="escalation_rules",
        values=getattr(role, "escalation_rules", []),
    )
    return written


def _seed_personality_pins(*, profile: Any, memory_service: Any, agent_id: str) -> int:
    personality = getattr(profile, "personality", None)
    if personality is None:
        return 0
    written = 0
    written += _write_scalar_pin(
        memory_service=memory_service,
        agent_id=agent_id,
        section="personality",
        field="tone",
        value=getattr(personality, "tone", ""),
    )
    written += _write_scalar_pin(
        memory_service=memory_service,
        agent_id=agent_id,
        section="personality",
        field="verbosity",
        value=getattr(personality, "verbosity", ""),
    )
    for field in ("formatting", "interaction_style"):
        written += _write_list_pin(
            memory_service=memory_service,
            agent_id=agent_id,
            section="personality",
            field=field,
            values=getattr(personality, field, []),
        )
    return written


def _seed_risk_pins(*, profile: Any, memory_service: Any, agent_id: str) -> int:
    risk = getattr(profile, "risk", None)
    if risk is None:
        return 0
    written = _write_scalar_pin(
        memory_service=memory_service,
        agent_id=agent_id,
        section="risk",
        field="risk_level",
        value=getattr(risk, "risk_level", ""),
    )
    for field in ("confirm_before", "auto_proceed_rules"):
        written += _write_list_pin(
            memory_service=memory_service,
            agent_id=agent_id,
            section="risk",
            field=field,
            values=getattr(risk, field, []),
        )
    return written


def _seed_tool_posture_pins(*, profile: Any, memory_service: Any, agent_id: str) -> int:
    tool_posture = getattr(profile, "tool_posture", None)
    if tool_posture is None:
        return 0
    written = _write_scalar_pin(
        memory_service=memory_service,
        agent_id=agent_id,
        section="tool_posture",
        field="tool_use",
        value=getattr(tool_posture, "tool_use", ""),
    )
    for field in ("blocked_patterns", "allowed_tools"):
        written += _write_list_pin(
            memory_service=memory_service,
            agent_id=agent_id,
            section="tool_posture",
            field=field,
            values=getattr(tool_posture, field, []),
        )
    return written


def _write_profile_version_pin(
    *, memory_service: Any, agent_id: str, current_version: str
) -> int:
    _write_pin(
        memory_service=memory_service,
        agent_id=agent_id,
        key="identity_profile_version",
        title="identity_profile_version",
        content=current_version,
    )
    return 1


def seed_identity_pins(
    *,
    profile: Any,
    memory_service: Any,
    agent_id: str,
    force: bool = False,
) -> int:
    """Seed AgentProfile fields as pin records in agent:<agent_id> scope."""
    if profile is None:
        return 0

    current_version = str(getattr(profile, "profile_revision", 0))
    if not force:
        stored = _get_current_version(memory_service, agent_id)
        if stored is not None and stored == current_version:
            _log.debug(
                "identity_seeder: skipping seed agent_id=%s profile_revision=%s (up to date)",
                agent_id,
                current_version,
            )
            return 0

    if getattr(profile, "role", None) is None:
        return 0

    written = _seed_role_pins(
        profile=profile, memory_service=memory_service, agent_id=agent_id
    )
    written += _seed_personality_pins(
        profile=profile, memory_service=memory_service, agent_id=agent_id
    )
    written += _seed_risk_pins(
        profile=profile, memory_service=memory_service, agent_id=agent_id
    )
    written += _seed_tool_posture_pins(
        profile=profile, memory_service=memory_service, agent_id=agent_id
    )
    written += _write_profile_version_pin(
        memory_service=memory_service,
        agent_id=agent_id,
        current_version=current_version,
    )

    _log.info(
        "identity_seeder: seeded agent_id=%s profile_revision=%s pins=%d",
        agent_id,
        current_version,
        written,
    )
    return written


__all__ = ["seed_identity_pins"]
