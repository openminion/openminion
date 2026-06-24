from __future__ import annotations

import pytest

from openminion.modules.context.contracts import (
    CONTEXT_CLIENT_INTERFACE_VERSION,
    PluginRegistry,
    ensure_context_client_compatibility,
)
from openminion.modules.context.schemas import (
    BuildPackRequest,
    EvidenceItem,
    IdentitySnippet,
    RecentSessionArtifactRef,
    SessionSlice,
)
from openminion.modules.context.service import ContextCtlService


class _IdentityClient:
    contract_version = CONTEXT_CLIENT_INTERFACE_VERSION

    def render(
        self, *, agent_id: str, purpose: str, max_tokens: int, provider_pref=None
    ) -> IdentitySnippet:
        del purpose, max_tokens, provider_pref
        return IdentitySnippet(
            agent_id=agent_id,
            profile_version="pv1",
            render_version="rv1",
            text=f"Identity:{agent_id}",
        )


class _SessionClient:
    contract_version = CONTEXT_CLIENT_INTERFACE_VERSION

    def get_slice(
        self, *, session_id: str, purpose: str, limits: dict[str, int]
    ) -> SessionSlice:
        del purpose, limits
        return SessionSlice(
            session_id=session_id, slice_version="slice:v1", summary_short=""
        )


class _MemoryClient:
    contract_version = CONTEXT_CLIENT_INTERFACE_VERSION

    def query_facts(
        self,
        *,
        session_id: str,
        agent_id: str,
        query: str,
        limit: int,
        mode_name: str | None = None,
    ):
        del session_id, agent_id, query, limit, mode_name
        return []

    def query_memory_cards(
        self,
        *,
        session_id: str,
        agent_id: str,
        query: str,
        limit: int,
        mode_name: str | None = None,
    ):
        del session_id, agent_id, query, limit, mode_name
        return []

    def recall_session_start_memory(
        self,
        *,
        session_id: str,
        agent_id: str,
        query: str,
        turn_index: int,
        limit: int,
        mode_name: str | None = None,
    ):
        del session_id, agent_id, query, turn_index, limit, mode_name
        return []

    def recall_mid_session_memory(
        self,
        *,
        session_id: str,
        agent_id: str,
        turn_index: int,
        latest_user_message: str,
        intent_ids: list[str],
        intent_statuses: list[str],
        active_skill_id: str | None,
        resolved_skill_ids: list[str],
        plan_cursor: int,
        plan_step_ids: list[str],
        recent_tool_families: list[str],
        limit: int,
        mode_name: str | None = None,
    ):
        del (
            session_id,
            agent_id,
            turn_index,
            latest_user_message,
            intent_ids,
            intent_statuses,
            active_skill_id,
            resolved_skill_ids,
            plan_cursor,
            plan_step_ids,
            recent_tool_families,
            limit,
            mode_name,
        )
        return []

    def recall_recent_session_artifacts(
        self,
        *,
        session_id: str,
        agent_id: str,
        max_results: int,
        max_session_age: int,
        mode_name: str | None = None,
    ):
        del session_id, agent_id, max_results, max_session_age, mode_name
        return [
            RecentSessionArtifactRef(
                record_id="artifact-ref-1",
                artifact_type="file",
                artifact_path="/tmp/server.py",
                artifact_digest="sha256:abc123",
                session_id="sess-prev",
                turn_index=2,
                tool_name="file.write",
            )
        ]

    def get_procedure(self, *, procedure_id: str):
        del procedure_id
        return None


class _ArtifactClient:
    contract_version = CONTEXT_CLIENT_INTERFACE_VERSION

    def query_digests(self, *, session_id: str, agent_id: str, query: str, limit: int):
        del session_id, agent_id, query, limit
        return []


class _Retriever:
    contract_version = CONTEXT_CLIENT_INTERFACE_VERSION
    name = "retriever"

    def retrieve(self, *, session_id: str, query: str, k: int, filters: dict):
        del session_id, query, k, filters
        return [EvidenceItem(ref="r1", content="c1", score=1.0, source=self.name)]


class _Compressor:
    contract_version = CONTEXT_CLIENT_INTERFACE_VERSION
    name = "compressor"

    def compress(self, *, query: str, items: list[EvidenceItem], budget_tokens: int):
        del query, budget_tokens
        return items[:1]


def test_context_clients_and_plugins_satisfy_contracts() -> None:
    service = ContextCtlService(
        identityctl=_IdentityClient(),
        sessctl=_SessionClient(),
        memctl=_MemoryClient(),
        artifactctl=_ArtifactClient(),
    )
    pack = service.build_pack(
        BuildPackRequest(session_id="s1", agent_id="a1", purpose="act", query="hello")
    )
    assert pack.session_id == "s1"

    reg = PluginRegistry()
    reg.register_retriever(_Retriever())
    reg.register_compressor(_Compressor())


def test_context_validator_rejects_incompatible_client() -> None:
    class _Broken:
        contract_version = "v1"

    with pytest.raises(TypeError):
        ensure_context_client_compatibility(_Broken(), client_type="identity")


def test_context_memory_client_requires_session_start_recall_method() -> None:
    class _BrokenMemory:
        contract_version = CONTEXT_CLIENT_INTERFACE_VERSION

        def query_facts(self, **kwargs):
            del kwargs
            return []

        def query_memory_cards(self, **kwargs):
            del kwargs
            return []

        def recall_mid_session_memory(self, **kwargs):
            del kwargs
            return []

        def get_procedure(self, **kwargs):
            del kwargs
            return None

    with pytest.raises(TypeError, match="recall_session_start_memory"):
        ensure_context_client_compatibility(_BrokenMemory(), client_type="memory")
