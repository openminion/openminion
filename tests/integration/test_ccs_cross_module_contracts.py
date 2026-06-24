from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from openminion.base.config.contracts import (
    CONTRACT_REGISTRY,
    check_contract_registry_health,
)
from openminion.modules.context.contracts import (
    CONTEXT_CLIENT_INTERFACE_VERSION,
    ContextBuilder,
    DataContext,
    SessionContext,
    ensure_context_client_compatibility,
)
from openminion.modules.context.schemas import (
    ArtifactDigest,
    BuildPackRequest,
    FactRecord,
    IdentitySnippet,
    MemoryCard,
    RecentSessionArtifactRef,
    SessionSlice,
)
from openminion.modules.context.service import ContextCtlService


class _IdentityStub:
    contract_version = CONTEXT_CLIENT_INTERFACE_VERSION

    def render(
        self,
        *,
        agent_id: str,
        purpose: str,
        max_tokens: int,
        provider_pref: Optional[str] = None,
    ) -> IdentitySnippet:
        del purpose, max_tokens, provider_pref
        return IdentitySnippet(
            agent_id=agent_id,
            profile_version="prof:v1",
            render_version="rend:v1",
            text=f"Identity:{agent_id}",
        )


class _SessionStub:
    contract_version = CONTEXT_CLIENT_INTERFACE_VERSION

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def get_slice(
        self,
        *,
        session_id: str,
        purpose: str,
        limits: Dict[str, int],
    ) -> SessionSlice:
        del limits
        self.calls.append((session_id, purpose))
        return SessionSlice(
            session_id=session_id,
            slice_version="slice:v1",
            summary_short="",
        )


class _MemoryStub:
    contract_version = CONTEXT_CLIENT_INTERFACE_VERSION

    def query_facts(
        self,
        *,
        session_id: str,
        agent_id: str,
        query: str,
        limit: int,
        mode_name: str | None = None,
    ) -> List[FactRecord]:
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
    ) -> List[MemoryCard]:
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
    ) -> List[MemoryCard]:
        del session_id, agent_id, query, turn_index, limit, mode_name
        return []

    def recall_mid_session_memory(self, **_: Any) -> List[MemoryCard]:
        return []

    def recall_recent_session_artifacts(
        self,
        *,
        session_id: str,
        agent_id: str,
        max_results: int,
        max_session_age: int,
        mode_name: str | None = None,
    ) -> List[RecentSessionArtifactRef]:
        del session_id, agent_id, max_results, max_session_age, mode_name
        return []

    def get_procedure(self, *, procedure_id: str) -> Optional[Any]:
        del procedure_id
        return None


class _ArtifactStub:
    contract_version = CONTEXT_CLIENT_INTERFACE_VERSION

    def query_digests(
        self,
        *,
        session_id: str,
        agent_id: str,
        query: str,
        limit: int,
    ) -> List[ArtifactDigest]:
        del session_id, agent_id, query, limit
        return []


def test_context_pack_build_under_canonical_contracts() -> None:
    session = _SessionStub()
    service = ContextCtlService(
        identityctl=_IdentityStub(),
        sessctl=session,
        memctl=_MemoryStub(),
        artifactctl=_ArtifactStub(),
    )
    request = BuildPackRequest(
        session_id="s1",
        agent_id="a1",
        purpose="act",
        query="ping",
    )
    pack = service.build_pack(request)
    assert pack.session_id == "s1"
    assert pack.token_budget_report is not None
    assert (
        not hasattr(pack, "budget_report")
        or pack.model_dump().get("budget_report") is None
    )


def test_session_slice_via_canonical_get_slice() -> None:
    session = _SessionStub()
    slice_obj = session.get_slice(
        session_id="s2", purpose="act", limits={"max_turns": 5}
    )
    assert slice_obj.session_id == "s2"
    assert slice_obj.slice_version == "slice:v1"
    assert session.calls == [("s2", "act")]
    assert not hasattr(session, "get_slice_v15")


def test_brain_session_adapter_calls_canonical_get_slice() -> None:
    session = _SessionStub()
    assert isinstance(session, SessionContext)
    slice_obj = session.get_slice(
        session_id="brain-1", purpose="decide", limits={"max_turns": 3}
    )
    assert slice_obj.session_id == "brain-1"


def test_missing_method_fails_compatibility_check() -> None:
    class _BrokenSession:
        contract_version = CONTEXT_CLIENT_INTERFACE_VERSION

    with pytest.raises(TypeError) as excinfo:
        ensure_context_client_compatibility(_BrokenSession(), client_type="session")
    assert "get_slice" in str(excinfo.value)


def test_version_mismatch_fails_compatibility_check() -> None:
    class _StaleSession:
        contract_version = "v0"  # <- mismatch

        def get_slice(self, *, session_id, purpose, limits):
            del session_id, purpose, limits
            return SessionSlice(session_id="x", slice_version="x", summary_short="")

    with pytest.raises(TypeError) as excinfo:
        ensure_context_client_compatibility(_StaleSession(), client_type="session")
    assert "contract_version" in str(excinfo.value)


def test_contract_registry_aligned_after_reset() -> None:
    result = check_contract_registry_health()
    assert result["aligned"] is True
    assert set(CONTRACT_REGISTRY) == {"context", "session", "memory", "brain"}


def test_canonical_protocols_are_runtime_checkable() -> None:
    session = _SessionStub()
    assert isinstance(session, SessionContext)
    assert getattr(DataContext, "_is_runtime_protocol", True)
    assert getattr(ContextBuilder, "_is_runtime_protocol", True)
