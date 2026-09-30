from typing import Any

from openminion.modules.context.schemas import IdentitySnippet

from .shared import (
    BRAIN_ADAPTER_INTERFACE_VERSION,
    _IDENTITY_BRIDGE_FALLBACK_VERSION,
    _LOGGER,
)


class BridgeIdentityClient:
    contract_version = BRAIN_ADAPTER_INTERFACE_VERSION

    def __init__(self, *, backing_store: Any, system_prompt: str | None = None) -> None:
        self._backing_store = backing_store
        self._system_prompt = str(system_prompt or "").strip()
        self._identity_ctl: Any | None = None
        self._skill_client: Any | None = None

    def _compose_identity_text(
        self, *, base_text: str, agent_id: str, purpose: str
    ) -> str:
        return base_text.strip() or f"agent_id={agent_id}\npurpose={purpose}"

    def _resolve_identityctl(self) -> Any | None:
        if self._identity_ctl is not None:
            return self._identity_ctl
        imported = _import_identity_dependencies()
        if imported is None:
            return None
        self._identity_ctl = self._build_identity_ctl(imported)
        return self._identity_ctl

    def _build_identity_ctl(self, imported: tuple[Any, Any]) -> Any | None:
        identity_ctl_cls, sqlite_identity_store_cls = imported
        from openminion.modules.identity.config import load_config
        from .skill import BridgeSkillClient

        identity_db = load_config().storage.db_path
        self._skill_client = BridgeSkillClient(self._backing_store)
        return identity_ctl_cls(
            store=sqlite_identity_store_cls(identity_db),
            skillctl=self._skill_client,
        )

    def close(self) -> None:
        identity_ctl = self._identity_ctl
        self._identity_ctl = None
        if identity_ctl is not None:
            identity_ctl.close()
        skill_client = self._skill_client
        self._skill_client = None
        if skill_client is not None:
            skill_client.close()

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
            if identity_ctl.get_profile(agent_id) is not None:
                snippet = identity_ctl.render(
                    agent_id=agent_id,
                    purpose=purpose,
                    max_tokens=max(1, int(max_tokens)),
                    provider_pref=provider_pref,
                    query_text=query_text,
                )
                text = str(getattr(snippet, "text", "") or "").strip()
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
                    included_fields=list(getattr(snippet, "included_fields", []) or []),
                    omitted_fields=list(getattr(snippet, "omitted_fields", []) or []),
                    warnings=list(getattr(snippet, "warnings", []) or []),
                )
            _LOGGER.warning(
                "identity.bridge_fallback reason=profile_absent agent_id=%s purpose=%s sentinel=%s",
                agent_id,
                purpose,
                _IDENTITY_BRIDGE_FALLBACK_VERSION,
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
    except ImportError:
        return None
    return IdentityCtl, SQLiteIdentityStore


__all__ = ["BridgeIdentityClient"]
