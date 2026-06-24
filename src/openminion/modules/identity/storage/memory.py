from dataclasses import replace

from openminion.modules.identity.models import AgentProfile
from openminion.modules.identity.storage.base import (
    CachedSnippet,
    IdentityStore,
    StoredProfile,
)

from openminion.base.time import utc_now_iso as _iso_now


class InMemoryIdentityStore(IdentityStore):
    def __init__(self) -> None:
        self._profiles: dict[str, StoredProfile] = {}
        self._cache: dict[str, CachedSnippet] = {}

    def get_profile(self, agent_id: str) -> StoredProfile | None:
        return self._profiles.get(agent_id)

    def list_profiles(self) -> list[StoredProfile]:
        return sorted(self._profiles.values(), key=lambda row: row.agent_id)

    def upsert_profile(self, profile: AgentProfile, profile_version: str) -> None:
        self._profiles[profile.agent_id] = StoredProfile(
            agent_id=profile.agent_id,
            profile=profile,
            profile_revision=profile.profile_revision,
            profile_version=profile_version,
            updated_at=_iso_now(),
        )

    def update_profile_version(self, agent_id: str, profile_version: str) -> None:
        current = self._profiles.get(agent_id)
        if current is None:
            return
        self._profiles[agent_id] = replace(
            current,
            profile_version=profile_version,
            updated_at=_iso_now(),
        )

    def delete_profile(self, agent_id: str) -> None:
        self._profiles.pop(agent_id, None)

    def get_cached_snippet(self, cache_key: str) -> CachedSnippet | None:
        return self._cache.get(cache_key)

    def upsert_cached_snippet(
        self,
        *,
        cache_key: str,
        snippet_text: str,
        used_tokens: int,
        used_chars: int,
        sections: dict[str, str] | None,
        included_fields: list[str],
        omitted_fields: list[str],
        warnings: list[str],
    ) -> None:
        self._cache[cache_key] = CachedSnippet(
            cache_key=cache_key,
            snippet_text=snippet_text,
            used_tokens=used_tokens,
            used_chars=used_chars,
            sections=dict(sections) if sections else None,
            included_fields=list(included_fields),
            omitted_fields=list(omitted_fields),
            warnings=list(warnings),
            updated_at=_iso_now(),
        )

    def clear_cache(self, agent_id: str | None = None) -> None:
        if agent_id is None:
            self._cache.clear()
            return
        prefix = f"{agent_id}|"
        for key in [item for item in self._cache if item.startswith(prefix)]:
            self._cache.pop(key, None)

    def close(self) -> None:
        pass
