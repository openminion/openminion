from __future__ import annotations

from pathlib import Path

from openminion.modules.context.schemas import (
    BuildConstraints,
    BuildPackRequest,
    ContextBudgets,
    IdentitySnippet,
    SessionSlice,
)
from openminion.modules.context.schemas import default_budgets_for
from openminion.modules.context.service import (
    ContextCtlService,
    _apply_mode_budget_bias,
)
from openminion.modules.skill.runtime.skill import Skill


_SKILL_MARKDOWN = """
---
name: Deploy Checker
id: deploy_checker
status: verified
tags: [deploy, ops]
tools: [tool.shell]
risk: low
applies_to:
  intents: [check deploy status, verify deployment]
---

## Summary
Check deployment status on production.

## Procedure
- tool.shell run "kubectl get pods -n production"
- tool.shell run "kubectl rollout status deployment/app -n production"

## Verification
- tool.shell run "curl -s http://localhost:8080/health"
""".strip()


def _skill_cfg(tmp_path: Path) -> dict:
    return {
        "skill": {
            "sqlite_path": str(tmp_path / "skill-test.db"),
            "wal": False,
            "default_status_filter": ["draft", "verified", "blessed"],
            "high_risk_status_filter": ["blessed", "verified", "draft"],
            "known_tools": ["tool.shell"],
        }
    }


class _DecideIdentityClient:
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


class _DecideSessionClient:
    contract_version = "v1"

    def get_slice(self, *, session_id, purpose, limits) -> SessionSlice:
        del purpose, limits
        return SessionSlice(
            session_id=session_id,
            slice_version="slice:v1",
            last_event_id="evt-001",
            summary_short="short summary",
        )


class _DecideMemoryClient:
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


class _DecideArtifactClient:
    contract_version = "v1"

    def query_digests(self, *, session_id, agent_id, query, limit):
        del session_id, agent_id, query, limit
        return []


def test_default_decide_budget_allocates_more_skill_tokens() -> None:
    budgets = default_budgets_for("decide")
    assert budgets.skills_tokens == 120


def test_act_mode_bias_bumps_skill_budget() -> None:
    budgets = ContextBudgets(
        total_max_tokens=1200,
        identity_tokens=160,
        summary_tokens=200,
        recent_turn_tokens=450,
        facts_tokens=150,
        memory_tokens=150,
        skills_tokens=120,
        artifact_tokens=50,
        instructions_tokens=80,
    )

    biased = _apply_mode_budget_bias(budgets, mode_name="act")

    assert biased.skills_tokens >= 240


def test_decide_build_pack_includes_skill_snippet_segment(tmp_path: Path) -> None:
    skillctl = Skill(_skill_cfg(tmp_path))
    try:
        skill_id, version_hash, _ = skillctl.ingest_text(
            name="Deploy Checker",
            markdown=_SKILL_MARKDOWN,
        )
        service = ContextCtlService(
            identityctl=_DecideIdentityClient(),
            sessctl=_DecideSessionClient(),
            memctl=_DecideMemoryClient(),
            artifactctl=_DecideArtifactClient(),
            skillctl=skillctl,
        )

        pack = service.build_pack(
            BuildPackRequest(
                session_id="sess-decide",
                agent_id="agent-test",
                purpose="decide",
                query="check the deploy status",
                constraints=BuildConstraints(
                    skill_id=skill_id,
                    skill_version_hash=version_hash,
                ),
            )
        )

        skill_segments = [
            segment for segment in pack.segments if "[SKILL SNIPPET]" in segment.content
        ]

        assert skill_segments
        assert any(
            "Skill: Deploy Checker" in segment.content for segment in skill_segments
        )
        assert any("[SKILL SNIPPET]" in message.content for message in pack.messages)
    finally:
        skillctl.close()
