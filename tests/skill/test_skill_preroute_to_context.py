from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from openminion.modules.brain.bootstrap.skill.hints import resolve_skill_hints
from openminion.modules.brain.bootstrap.skill.pipeline import _load_catalog
from openminion.modules.context.schemas import (
    BuildConstraints,
    BuildPackRequest,
    IdentitySnippet,
    SessionSlice,
)
from openminion.modules.context.service import ContextCtlService
from openminion.modules.skill.runtime.skill import Skill


PROCEDURAL_SKILL = """
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

# Summary
Check deployment status on production.

# Procedure
- tool.shell run "kubectl get pods -n production"
- tool.shell run "kubectl rollout status deployment/app -n production"

# Verification
- tool.shell run "curl -s http://localhost:8080/health"
""".strip()

SECONDARY_SKILL = """
---
name: Rollback Guide
id: rollback_guide
status: verified
tags: [deploy, rollback]
tools: [tool.shell]
risk: medium
applies_to:
  intents: [rollback deployment]
---

# Summary
Rollback the deployment safely.

# Procedure
- tool.shell run "kubectl rollout undo deployment/app -n production"
""".strip()


def _cfg(tmp_path: Path) -> dict:
    return {
        "skill": {
            "sqlite_path": str(tmp_path / "skill-test.db"),
            "wal": False,
            "default_status_filter": ["draft", "verified", "blessed"],
            "high_risk_status_filter": ["blessed", "verified", "draft"],
            "known_tools": ["tool.shell"],
        }
    }


class _IdentityClient:
    contract_version = "v1"

    def render(
        self,
        *,
        agent_id: str,
        purpose: str,
        max_tokens: int,
        provider_pref=None,
    ) -> IdentitySnippet:
        del purpose, max_tokens, provider_pref
        return IdentitySnippet(
            agent_id=agent_id,
            profile_version="prof:v1",
            render_version="render:v1",
            text=f"Identity for {agent_id}",
        )


class _SessionClient:
    contract_version = "v1"

    def get_slice(self, *, session_id, purpose, limits) -> SessionSlice:
        del purpose, limits
        return SessionSlice(
            session_id=session_id,
            slice_version="slice:v1",
            last_event_id="evt-001",
            summary_short="recent deploy summary",
        )


class _MemoryClient:
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


class _ArtifactClient:
    contract_version = "v1"

    def query_digests(self, *, session_id, agent_id, query, limit):
        del session_id, agent_id, query, limit
        return []


def test_catalog_loads_ingested_skills(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path))
    try:
        ctl.ingest_text(name="Deploy Checker", markdown=PROCEDURAL_SKILL)
        catalog = _load_catalog(skill_api=ctl, agent_id="test-agent")
        assert len(catalog) == 1
        assert catalog[0]["id"] == "deploy_checker"
        assert catalog[0]["version_hash"]
    finally:
        ctl.close()


def test_resolve_skill_hints_direct_selects_single_catalog_skill_without_llm(
    tmp_path: Path,
) -> None:
    ctl = Skill(_cfg(tmp_path))
    try:
        _skill_id, version_hash, _ = ctl.ingest_text(
            name="Deploy Checker",
            markdown=PROCEDURAL_SKILL,
        )

        mock_runner = MagicMock()
        mock_runner.skill_api = ctl
        mock_runner.llm_api = MagicMock()
        mock_runner.profile = SimpleNamespace(
            skill=None,
            skill_catalog=[],
            llm_profiles=SimpleNamespace(act_model="", summarize_model="test-model"),
        )
        mock_runner.session_api = MagicMock()
        mock_runner.session_api.get_slice.return_value = {
            "recent_turns": [],
            "open_tasks": [],
            "recent_tool_events": [],
            "summary_short": "",
        }

        state = SimpleNamespace(
            agent_id="test-agent",
            session_id="session-1",
            trace_id="trace-1",
            active_skill_id=None,
            active_skill_version_hash=None,
            resolved_skill_ids=[],
            resolved_skill_versions={},
            session_skill_loaded=[],
            session_skill_unloaded=[],
            skill_selection_mode=None,
        )

        hints = resolve_skill_hints(
            mock_runner,
            intent="check the deploy status",
            purpose="plan",
            state=state,
            logger=MagicMock(),
        )

        assert hints["skill_id"] == "deploy_checker"
        assert hints["primary_skill_id"] == "deploy_checker"
        assert hints["skill_version_hash"] == version_hash
        assert hints["skill_selection_mode"] == "direct"
        assert hints["skill_effective_count"] == 1
        assert hints["resolved_skill_ids"] == ["deploy_checker"]
        assert state.active_skill_id == "deploy_checker"
        assert state.active_skill_version_hash == version_hash
        assert state.resolved_skill_ids == ["deploy_checker"]
        mock_runner.llm_api.call_structured.assert_not_called()
    finally:
        ctl.close()


def test_blank_intent_skips_skill_hints() -> None:
    mock_runner = MagicMock()
    state = SimpleNamespace(
        agent_id="test-agent",
        session_id="session-1",
        trace_id="trace-1",
        active_skill_id="deploy_checker",
        active_skill_version_hash="abc123",
    )

    hints = resolve_skill_hints(
        mock_runner,
        intent="   ",
        purpose="plan",
        state=state,
        logger=MagicMock(),
    )

    assert hints == {}
    assert state.active_skill_id == "deploy_checker"
    assert state.active_skill_version_hash == "abc123"


def test_multi_skill_refs_render_multiple_snippets(tmp_path: Path) -> None:
    ctl = Skill(_cfg(tmp_path))
    try:
        deploy_id, deploy_hash, _ = ctl.ingest_text(
            name="Deploy Checker",
            markdown=PROCEDURAL_SKILL,
        )
        rollback_id, rollback_hash, _ = ctl.ingest_text(
            name="Rollback Guide",
            markdown=SECONDARY_SKILL,
        )

        service = ContextCtlService(
            identityctl=_IdentityClient(),
            sessctl=_SessionClient(),
            memctl=_MemoryClient(),
            artifactctl=_ArtifactClient(),
            skillctl=ctl,
        )

        pack = service.build_pack(
            BuildPackRequest(
                session_id="sess-skill-pack",
                agent_id="router-agent",
                purpose="plan",
                query="help me recover the deployment",
                constraints=BuildConstraints(
                    skill_refs=[
                        {
                            "skill_id": deploy_id,
                            "version_hash": deploy_hash,
                        },
                        {
                            "skill_id": rollback_id,
                            "version_hash": rollback_hash,
                        },
                    ]
                ),
            )
        )

        rendered = "\n".join(str(message.content) for message in pack.messages)
        assert "[SKILL SNIPPET]" in rendered
        assert "Deploy Checker" in rendered
        assert "Rollback Guide" in rendered
    finally:
        ctl.close()
