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

    Uses upsert so repeated calls only update the record, not duplicate it.
    Falls back to write_record if upsert is unavailable.
    """
    scope = f"agent:{agent_id}"
    try:
        upsert = getattr(memory_service, "_store", None) and getattr(
            memory_service._store, "upsert", None
        )
        if callable(upsert):
            memory_service._store.upsert(
                scope,
                "pin",
                key,
                {
                    "title": title,
                    "content": content,
                    "tags": ["identity"],
                    "source": "imported",
                    "confidence": 1.0,
                    "visibility": "shared",
                    "updated_at": _utc_now_iso(),
                },
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

    role = getattr(profile, "role", None)
    if role is None:
        return 0

    written = 0

    # mission
    mission = str(getattr(role, "mission", "") or "").strip()
    if mission:
        _write_pin(
            memory_service=memory_service,
            agent_id=agent_id,
            key="identity_mission",
            title="identity_mission",
            content=mission,
        )
        written += 1

    # responsibilities
    responsibilities = list(getattr(role, "responsibilities", None) or [])
    if responsibilities:
        content = "\n".join(f"- {r}" for r in responsibilities)
        _write_pin(
            memory_service=memory_service,
            agent_id=agent_id,
            key="identity_responsibilities",
            title="identity_responsibilities",
            content=content,
        )
        written += 1

    # hard_constraints
    hard_constraints = list(getattr(role, "hard_constraints", None) or [])
    if hard_constraints:
        content = "\n".join(f"- {c}" for c in hard_constraints)
        _write_pin(
            memory_service=memory_service,
            agent_id=agent_id,
            key="identity_constraints",
            title="identity_constraints",
            content=content,
        )
        written += 1

    # domain
    domain = list(getattr(role, "domain", None) or [])
    if domain:
        content = "\n".join(f"- {d}" for d in domain)
        _write_pin(
            memory_service=memory_service,
            agent_id=agent_id,
            key="identity_domain",
            title="identity_domain",
            content=content,
        )
        written += 1

    # sentinel: identity_profile_version — must be last
    _write_pin(
        memory_service=memory_service,
        agent_id=agent_id,
        key="identity_profile_version",
        title="identity_profile_version",
        content=current_version,
    )
    written += 1

    _log.info(
        "identity_seeder: seeded agent_id=%s profile_revision=%s pins=%d",
        agent_id,
        current_version,
        written,
    )
    return written


__all__ = ["seed_identity_pins"]
