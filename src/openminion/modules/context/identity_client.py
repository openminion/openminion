from __future__ import annotations

from typing import Any

from .schemas import IdentitySnippet


class ContextIdentityClient:
    """Adapt canonical identity storage to ContextCtl without creating profiles."""

    contract_version = "v1"

    def __init__(self, identityctl: Any | None) -> None:
        self._identityctl = identityctl

    def render(
        self,
        *,
        agent_id: str,
        purpose: str,
        max_tokens: int,
        provider_pref: str | None = None,
        query_text: str | None = None,
    ) -> IdentitySnippet:
        if self._identityctl is not None and self._identityctl.get_profile(agent_id):
            return self._identityctl.render(
                agent_id=agent_id,
                purpose=purpose,
                max_tokens=max_tokens,
                provider_pref=provider_pref,
                query_text=query_text,
            )
        return IdentitySnippet(
            agent_id=agent_id,
            purpose=purpose,
            profile_version="unconfigured:v1",
            render_version="unconfigured:v1",
            text=f"Agent: {agent_id}",
        )


__all__ = ["ContextIdentityClient"]
