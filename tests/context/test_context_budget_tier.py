from __future__ import annotations

from openminion.modules.context.schemas import (
    BuildConstraints,
    BuildPackRequest,
    ContextBudgets,
    IdentitySnippet,
    SessionSlice,
)
from openminion.modules.context.service import ContextCtlService


class _BudgetTierIdentityClient:
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


class _BudgetTierSessionClient:
    contract_version = "v1"

    def get_slice(self, *, session_id, purpose, limits) -> SessionSlice:
        del purpose, limits
        return SessionSlice(
            session_id=session_id,
            slice_version="slice:v1",
            summary_short="summary",
            recent_turns=[],
        )


class _BudgetTierMemoryClient:
    contract_version = "v1"

    def query_facts(self, *, session_id, agent_id, query, limit, mode_name=None):
        del session_id, agent_id, query, limit, mode_name
        return []

    def query_memory_cards(self, *, session_id, agent_id, query, limit, mode_name=None):
        del session_id, agent_id, query, limit, mode_name
        return []

    def recall_session_start_memory(
        self, *, session_id, agent_id, query, turn_index, limit, mode_name=None
    ):
        del session_id, agent_id, query, turn_index, limit, mode_name
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


class _BudgetTierArtifactClient:
    contract_version = "v1"

    def query_digests(self, *, session_id, agent_id, query, limit):
        del session_id, agent_id, query, limit
        return []


def _budget_tier_service() -> ContextCtlService:
    return ContextCtlService(
        identityctl=_BudgetTierIdentityClient(),
        sessctl=_BudgetTierSessionClient(),
        memctl=_BudgetTierMemoryClient(),
        artifactctl=_BudgetTierArtifactClient(),
    )


def _budget_tier_request(
    *, tier: str | None, mode_name: str = "respond"
) -> BuildPackRequest:
    return BuildPackRequest(
        session_id="sess-budget",
        agent_id="agent-budget",
        purpose="decide",
        mode_name=mode_name,
        query="continue prior debugging work",
        budgets_override=ContextBudgets(
            total_max_tokens=2000,
            identity_tokens=180,
            summary_tokens=240,
            recent_turn_tokens=400,
            facts_tokens=10,
            memory_tokens=400,
            skills_tokens=20,
            artifact_tokens=100,
            instructions_tokens=80,
        ),
        constraints=BuildConstraints(context_budget_tier=tier),
    )


def test_medium_context_budget_tier_matches_baseline_after_mode_bias() -> None:
    service = _budget_tier_service()
    baseline_pack = service.build_pack(_budget_tier_request(tier=None))
    medium_pack = service.build_pack(_budget_tier_request(tier="medium"))

    assert (
        medium_pack.token_budget_report.buckets["recent_window"].cap_tokens
        == baseline_pack.token_budget_report.buckets["recent_window"].cap_tokens
    )
    assert (
        medium_pack.token_budget_report.buckets["retrieval"].cap_tokens
        == baseline_pack.token_budget_report.buckets["retrieval"].cap_tokens
    )
    assert (
        medium_pack.token_budget_report.buckets["evidence_refs"].cap_tokens
        == baseline_pack.token_budget_report.buckets["evidence_refs"].cap_tokens
    )
    assert medium_pack.context_manifest.context_budget_tier == "medium"


def test_short_context_budget_tier_applies_locked_formulas_after_mode_bias() -> None:
    service = _budget_tier_service()
    pack = service.build_pack(_budget_tier_request(tier="short"))

    assert pack.token_budget_report.buckets["recent_window"].cap_tokens == 160
    assert pack.token_budget_report.buckets["retrieval"].cap_tokens == 320
    assert pack.token_budget_report.buckets["evidence_refs"].cap_tokens == 75
    assert pack.token_budget_report.total_cap_tokens == 2000
    assert pack.context_manifest.context_budget_tier == "short"


def test_full_context_budget_tier_applies_locked_formulas_after_mode_bias() -> None:
    service = _budget_tier_service()
    pack = service.build_pack(_budget_tier_request(tier="full"))

    assert pack.token_budget_report.buckets["recent_window"].cap_tokens == 300
    assert pack.token_budget_report.buckets["retrieval"].cap_tokens == 500
    assert pack.token_budget_report.buckets["evidence_refs"].cap_tokens == 125
    assert pack.token_budget_report.total_cap_tokens == 2000
    assert pack.context_manifest.context_budget_tier == "full"
