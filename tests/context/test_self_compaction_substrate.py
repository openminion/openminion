from __future__ import annotations

from openminion.modules.context.schemas import IdentitySnippet, SessionSlice
from openminion.modules.context.service import ContextCtlService


class _IdentityClient:
    contract_version = "v1"

    def render(
        self, *, agent_id: str, purpose: str, max_tokens: int, provider_pref=None
    ) -> IdentitySnippet:
        del purpose, max_tokens, provider_pref
        return IdentitySnippet(
            agent_id=agent_id,
            profile_version="prof:v1",
            render_version="rend:v1",
            text=f"Identity for {agent_id}",
        )


class _SessionClient:
    contract_version = "v1"

    def get_slice(self, *, session_id, purpose, limits) -> SessionSlice:
        del purpose, limits
        return SessionSlice(
            session_id=session_id,
            slice_version="slice:v1",
            summary_short="",
        )


class _MemoryClient:
    contract_version = "v1"

    def query_facts(self, *, session_id, agent_id, query, limit, mode_name=None):
        del session_id, agent_id, query, limit, mode_name
        return []

    def query_memory_cards(self, *, session_id, agent_id, query, limit, mode_name=None):
        del session_id, agent_id, query, limit, mode_name
        return []

    def recall_session_start_memory(self, **kwargs):
        del kwargs
        return []

    def recall_mid_session_memory(self, **kwargs):
        del kwargs
        return []

    def recall_recent_session_artifacts(self, **kwargs):
        del kwargs
        return []

    def get_procedure(self, *, procedure_id):
        del procedure_id
        return None


class _ArtifactClient:
    contract_version = "v1"

    def query_digests(self, *, session_id, agent_id, query, limit):
        del session_id, agent_id, query, limit
        return []


def _make_service() -> ContextCtlService:
    return ContextCtlService(
        identityctl=_IdentityClient(),
        sessctl=_SessionClient(),
        memctl=_MemoryClient(),
        artifactctl=_ArtifactClient(),
    )


def test_maybe_compact_with_state_skips_when_self_compaction_marker_is_fresh() -> None:
    service = _make_service()

    result = service.maybe_compact_with_state(
        "session-1",
        working_state=type(
            "_State",
            (),
            {
                "module_state": {
                    "memory_context_maintenance": {
                        "last_compaction_marker": "2026-05-22T12:00:00+00:00",
                    }
                }
            },
        )(),
    )

    assert result is False


def test_maybe_compact_with_state_preserves_legacy_behavior_without_marker() -> None:
    service = _make_service()
    service.make_delta(
        session_id="session-1",
        agent_id="agent-1",
        content="one",
    )
    service.make_delta(
        session_id="session-1",
        agent_id="agent-1",
        content="two",
    )

    result = service.maybe_compact_with_state(
        "session-1",
        working_state=type("_State", (), {"module_state": {}})(),
        threshold=2,
    )

    assert result is True
