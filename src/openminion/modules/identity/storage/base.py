from abc import ABC, abstractmethod
from dataclasses import dataclass

from openminion.modules.identity.models import AgentProfile


@dataclass(frozen=True)
class StoredProfile:
    agent_id: str
    profile: AgentProfile
    profile_revision: int
    profile_version: str
    updated_at: str


@dataclass(frozen=True)
class CachedSnippet:
    cache_key: str
    snippet_text: str
    used_tokens: int
    used_chars: int
    sections: dict[str, str] | None
    included_fields: list[str]
    omitted_fields: list[str]
    warnings: list[str]
    updated_at: str


class IdentityStore(ABC):
    @abstractmethod
    def get_profile(self, agent_id: str) -> StoredProfile | None:
        raise NotImplementedError

    @abstractmethod
    def list_profiles(self) -> list[StoredProfile]:
        raise NotImplementedError

    @abstractmethod
    def upsert_profile(self, profile: AgentProfile, profile_version: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def update_profile_version(self, agent_id: str, profile_version: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def delete_profile(self, agent_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_cached_snippet(self, cache_key: str) -> CachedSnippet | None:
        raise NotImplementedError

    @abstractmethod
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
        raise NotImplementedError

    @abstractmethod
    def clear_cache(self, agent_id: str | None = None) -> None:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError
