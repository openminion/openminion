from typing import Any

from openminion.modules.brain.constants import (
    DEFAULT_IDENTITY_DB_FILENAME,
    DEFAULT_IDENTITY_DB_SUBPATH,
)
from openminion.modules.context.schemas import IdentitySnippet

from .shared import (
    BRAIN_ADAPTER_INTERFACE_VERSION,
    _IDENTITY_BRIDGE_FALLBACK_VERSION,
    _LOGGER,
    _lazy_resolve_service,
    _resolve_database_path,
)


class BridgeIdentityClient:
    contract_version = BRAIN_ADAPTER_INTERFACE_VERSION

    def __init__(self, *, backing_store: Any, system_prompt: str | None = None) -> None:
        self._backing_store = backing_store
        self._system_prompt = str(system_prompt or "").strip()
        self._identity_ctl: Any | None = None

    def _compose_identity_text(
        self, *, base_text: str, agent_id: str, purpose: str
    ) -> str:
        normalized = str(base_text or "").strip()
        if normalized:
            return normalized
        return f"agent_id={agent_id}\npurpose={purpose}"

    def _resolve_identityctl(self) -> Any | None:
        return _lazy_resolve_service(
            self,
            cache_attr="_identity_ctl",
            import_loader=_import_identity_dependencies,
            factory=self._build_identity_ctl,
        )

    def _build_identity_ctl(self, imported: tuple[Any, Any]) -> Any | None:
        identity_ctl_cls, sqlite_identity_store_cls = imported
        from .skill import BridgeSkillClient

        db_path = _resolve_database_path(self._backing_store)
        if db_path is None:
            return None
        identity_db = db_path.parent / DEFAULT_IDENTITY_DB_FILENAME
        for parent in (db_path.parent, *db_path.parents):
            if parent.name == ".openminion":
                identity_db = parent / DEFAULT_IDENTITY_DB_SUBPATH
                break
            if parent.name == "state":
                identity_db = parent.parent / DEFAULT_IDENTITY_DB_SUBPATH
                break
        return identity_ctl_cls(
            store=sqlite_identity_store_cls(identity_db),
            skillctl=BridgeSkillClient(self._backing_store),
        )

    def render(
        self,
        *,
        agent_id: str,
        purpose: str,
        max_tokens: int,
        provider_pref: str | None = None,
        query_text: str | None = None,
    ) -> IdentitySnippet:
        identity_ctl = self._resolve_identityctl()
        if identity_ctl is not None:
            try:
                _ensure_default_profile(
                    identity_ctl,
                    agent_id,
                    system_prompt=self._system_prompt,
                )
            except Exception:
                _LOGGER.debug(
                    "identity.bridge.ensure_default_profile_failed agent_id=%s",
                    agent_id,
                    exc_info=True,
                )
            try:
                snippet = identity_ctl.render(
                    agent_id=agent_id,
                    purpose=purpose,
                    max_tokens=max(1, int(max_tokens)),
                    provider_pref=provider_pref,
                    query_text=query_text,
                )
                text = str(getattr(snippet, "text", "") or "").strip()
                if text:
                    return IdentitySnippet(
                        agent_id=str(getattr(snippet, "agent_id", agent_id)),
                        purpose=purpose,
                        profile_version=str(
                            getattr(snippet, "profile_version", "identityctl:v1")
                        ),
                        render_version=str(
                            getattr(snippet, "render_version", "identityctl:v1")
                        ),
                        text=self._compose_identity_text(
                            base_text=text,
                            agent_id=agent_id,
                            purpose=purpose,
                        ),
                        sections=dict(getattr(snippet, "sections", {}) or {}) or None,
                        included_fields=list(
                            getattr(snippet, "included_fields", []) or []
                        ),
                        omitted_fields=list(
                            getattr(snippet, "omitted_fields", []) or []
                        ),
                        warnings=list(getattr(snippet, "warnings", []) or []),
                    )
            except Exception as exc:
                _LOGGER.warning(
                    "identity.bridge_fallback reason=identityctl_render_error agent_id=%s purpose=%s sentinel=%s error=%s",
                    agent_id,
                    purpose,
                    _IDENTITY_BRIDGE_FALLBACK_VERSION,
                    type(exc).__name__,
                )
        else:
            _LOGGER.warning(
                "identity.bridge_fallback reason=identityctl_unavailable agent_id=%s purpose=%s sentinel=%s",
                agent_id,
                purpose,
                _IDENTITY_BRIDGE_FALLBACK_VERSION,
            )

        return IdentitySnippet(
            agent_id=agent_id,
            purpose=purpose,
            profile_version=_IDENTITY_BRIDGE_FALLBACK_VERSION,
            render_version=_IDENTITY_BRIDGE_FALLBACK_VERSION,
            text=self._compose_identity_text(
                base_text=self._system_prompt,
                agent_id=agent_id,
                purpose=purpose,
            ),
        )


def _import_identity_dependencies() -> tuple[Any, Any] | None:
    try:
        from openminion.modules.identity.runtime.service import IdentityCtl
        from openminion.modules.identity.storage.store import SQLiteIdentityStore
    except Exception:
        return None
    return IdentityCtl, SQLiteIdentityStore


def _ensure_default_profile(
    identityctl: Any,
    agent_id: str,
    *,
    system_prompt: str = "",
) -> None:
    from openminion.modules.identity.runtime.defaults import default_mission

    existing_profile = identityctl.get_profile(agent_id)
    desired_mission = default_mission(agent_id=agent_id, system_prompt=system_prompt)
    if existing_profile is not None:
        _repair_legacy_default_profile(
            identityctl,
            existing_profile,
            desired_mission=desired_mission,
            legacy_mission=default_mission(agent_id=agent_id, system_prompt=""),
        )
        return

    from openminion.modules.identity.models import (
        AgentProfile,
        PersonalitySpec,
        RiskSpec,
        RoleSpec,
        ToolPostureSpec,
    )

    identityctl.upsert_profile(
        AgentProfile(
            agent_id=agent_id,
            display_name=agent_id,
            profile_revision=1,
            role=RoleSpec(
                mission=desired_mission, responsibilities=[], hard_constraints=[]
            ),
            personality=PersonalitySpec(tone="professional", verbosity="normal"),
            risk=RiskSpec(risk_level="medium", confirm_before=["destructive_actions"]),
            tool_posture=ToolPostureSpec(tool_use="allowed"),
            meta={"source": "default"},
        )
    )


def _repair_legacy_default_profile(
    identityctl: Any,
    profile: Any,
    *,
    desired_mission: str,
    legacy_mission: str,
) -> None:
    meta = dict(getattr(profile, "meta", {}) or {})
    if str(meta.get("source", "")).strip() != "default":
        return
    role = getattr(profile, "role", None)
    if role is None:
        return
    current_mission = str(getattr(role, "mission", "") or "").strip()
    if not current_mission or current_mission != legacy_mission:
        return
    if current_mission == desired_mission:
        return

    updated_role = role.model_copy(update={"mission": desired_mission})
    updated_profile = profile.model_copy(update={"role": updated_role})
    identityctl.upsert_profile(updated_profile)


__all__ = ["BridgeIdentityClient"]
