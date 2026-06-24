from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from openminion.base.config import SKILL_SELECTION_AUTO
import openminion.modules.brain.bootstrap.skill.pipeline as skill_pipeline
from openminion.modules.brain.bootstrap.skill.pipeline import (
    apply_skill_selection_to_state,
    describe_skill_catalog,
    resolve_skill_pipeline,
)


class _Logger:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def emit(
        self,
        event_type: str,
        payload: dict[str, object],
        **kwargs: object,
    ) -> None:
        self.events.append({"type": event_type, "payload": payload, "kwargs": kwargs})


class _LLM:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def estimate_tokens(self, *, model: str, context: dict[str, Any]) -> int:
        del model, context
        return 24

    def call_structured(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return dict(self.response)


class _SequenceLLM:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def estimate_tokens(self, *, model: str, context: dict[str, Any]) -> int:
        del model, context
        return 24

    def call_structured(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if not self.responses:
            return {"skill_ids": [], "intent": ""}
        return dict(self.responses.pop(0))


class _RetrieveAPI:
    contract_version = "v1"

    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.items = items
        self.ingested: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []

    def ingest_skill(
        self,
        *,
        skill_id: str,
        version_hash: str,
        source_ref: str,
        meta: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "skill_id": skill_id,
            "version_hash": version_hash,
            "source_ref": source_ref,
            "meta": dict(meta),
        }
        self.ingested.append(payload)
        return payload

    def retrieve(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.calls.append(dict(kwargs))
        return list(self.items)


def _catalog(*skill_ids: str) -> list[dict[str, str]]:
    return [
        {
            "id": skill_id,
            "name": skill_id.replace("-", " ").replace("_", " ").title(),
            "display_name": skill_id.replace("-", " ").replace("_", " ").title(),
            "canonical_name": skill_id,
            "short_description": f"{skill_id} helper description",
            "one_liner": f"{skill_id} helper",
            "version_hash": skill_id[0] * 64,
            "tags": [skill_id.split("-", 1)[0]],
            "tools": [f"tool.{skill_id.split('-', 1)[0]}"],
        }
        for skill_id in skill_ids
    ]


def _profile(
    *,
    skill: str | list[str] | None = None,
    skill_catalog: list[str] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        skill=skill,
        skill_catalog=list(skill_catalog or []),
        llm_profiles=SimpleNamespace(
            act_model="",
            summarize_model="summarize-default",
        ),
    )


def _state(
    *,
    loaded: list[str] | None = None,
    unloaded: list[str] | None = None,
    mode: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        agent_id="agent-1",
        session_id="session-1",
        trace_id="trace-1",
        session_skill_loaded=list(loaded or []),
        session_skill_unloaded=list(unloaded or []),
        skill_selection_mode=mode,
        active_skill_id=None,
        active_skill_version_hash=None,
        resolved_skill_ids=[],
        resolved_skill_versions={},
    )


def _runner(
    *,
    catalog: list[dict[str, str]],
    llm: _LLM | None = None,
    retrieve_api: _RetrieveAPI | None = None,
    profile: SimpleNamespace | None = None,
) -> SimpleNamespace:
    skill_api = MagicMock()
    skill_api.catalog_summaries.return_value = list(catalog)
    session_api = MagicMock()
    session_api.get_slice.return_value = {
        "recent_turns": [],
        "open_tasks": [],
        "recent_tool_events": [],
        "summary_short": "",
    }
    return SimpleNamespace(
        skill_api=skill_api,
        session_api=session_api,
        llm_api=llm,
        retrieve_api=retrieve_api,
        profile=profile or _profile(),
    )


def test_describe_skill_catalog_applies_session_loads_unloads() -> None:
    state = _state(loaded=["gamma"], unloaded=["alpha"])

    catalog_state = describe_skill_catalog(
        profile=_profile(
            skill=["alpha", "beta"], skill_catalog=["alpha", "beta", "gamma"]
        ),
        state=state,
        catalog=_catalog("alpha", "beta", "gamma"),
    )

    assert [entry["id"] for entry in catalog_state.effective_catalog] == [
        "beta",
        "gamma",
    ]
    assert catalog_state.sources == {"beta": "config", "gamma": "session"}
    assert catalog_state.auto_enabled is False


def test_resolve_skill_pipeline_direct_selects_single_catalog_skill_without_llm() -> (
    None
):
    runner = _runner(catalog=_catalog("deploy-checker"), llm=MagicMock())
    state = _state()
    logger = _Logger()

    result = resolve_skill_pipeline(
        runner,
        intent="check the deployment health",
        purpose="plan",
        state=state,
        logger=logger,
    )

    assert result.selection_mode == "direct"
    assert [ref.skill_id for ref in result.selected_refs] == ["deploy-checker"]
    assert result.context_budget == "medium"
    runner.llm_api.call_structured.assert_not_called()
    assert any(event["type"] == "skill.selected" for event in logger.events)


def test_load_catalog_preserves_existing_selection_metadata() -> None:
    skill_api = MagicMock()
    skill_api.catalog_summaries.return_value = [
        {
            "id": "news_digest",
            "name": "News Digest",
            "display_name": "News Digest",
            "canonical_name": "news_digest",
            "short_description": "Summarize news into a digest.",
            "one_liner": "Summarize news into a digest.",
            "version_hash": "n" * 64,
            "tags": ["news", "slack"],
            "tools": ["web.search", "slack.send"],
        }
    ]

    catalog = skill_pipeline._load_catalog(skill_api=skill_api, agent_id="agent-1")

    assert catalog == [
        {
            "id": "news_digest",
            "name": "News Digest",
            "display_name": "News Digest",
            "canonical_name": "news_digest",
            "short_description": "Summarize news into a digest.",
            "one_liner": "Summarize news into a digest.",
            "version_hash": "n" * 64,
            "tags": ["news", "slack"],
            "tools": ["web.search", "slack.send"],
            "reference_hints": [],
        }
    ]


def test_resolve_skill_pipeline_dense_catalog_uses_llm_selection() -> None:
    runner = _runner(
        catalog=_catalog("claude-api", "news_digest", "github_pr"),
        llm=_LLM({"skill_ids": ["news_digest"], "intent": "slack digest"}),
    )
    state = _state(mode=SKILL_SELECTION_AUTO)
    logger = _Logger()

    result = resolve_skill_pipeline(
        runner,
        intent="I need a Slack-ready digest of important AI policy news.",
        purpose="plan",
        state=state,
        logger=logger,
    )

    assert result.selection_mode == "llm-select"
    assert result.selection_reason == skill_pipeline._SKILL_SELECTION_REASON_LLM
    assert [ref.skill_id for ref in result.selected_refs] == ["news_digest"]
    assert len(runner.llm_api.calls) == 1
    selected = next(
        event for event in logger.events if event["type"] == "skill.selected"
    )
    assert selected["payload"]["skill_ref"]["id"] == "news_digest"
    assert selected["payload"]["selected_skill_ids"] == ["news_digest"]


def test_resolve_skill_pipeline_dense_catalog_selection_prefers_direct_named_match() -> (
    None
):
    runner = _runner(
        catalog=_catalog("claude-api", "news_digest", "github_pr"),
        llm=_LLM({"skill_ids": ["news_digest"], "intent": "news digest"}),
    )
    state = _state(mode=SKILL_SELECTION_AUTO)
    logger = _Logger()

    result = resolve_skill_pipeline(
        runner,
        intent="Use the News Digest skill to prepare a Slack-ready digest.",
        purpose="plan",
        state=state,
        logger=logger,
    )

    assert result.selection_mode == "direct"
    assert (
        result.selection_reason == skill_pipeline._SKILL_SELECTION_REASON_DIRECT_NAMED
    )
    assert [ref.skill_id for ref in result.selected_refs] == ["news_digest"]
    assert len(runner.llm_api.calls) == 0
    selected = next(
        event for event in logger.events if event["type"] == "skill.selected"
    )
    assert selected["payload"]["skill_ref"]["id"] == "news_digest"
    assert selected["payload"]["selected_skill_ids"] == ["news_digest"]


def test_skill_selection_prompt_requires_exact_named_skill_match_without_substitution() -> (
    None
):
    prompt = skill_pipeline._SKILL_SELECT_PROMPT

    assert "select exactly that skill id" in prompt
    assert "Do not substitute a different skill" in prompt
    assert "absent from the catalog" in prompt


def test_build_skill_selection_context_keeps_identifiers_prominent() -> None:
    context = skill_pipeline._build_skill_selection_context(
        intent="Use the News Digest skill to prepare a digest.",
        catalog=[
            {
                "id": "news_digest",
                "name": "News Digest",
                "display_name": "News Digest Skill",
                "canonical_name": "news-digest",
                "short_description": "Summarize news into a digest.",
                "one_liner": "Summarize news into a digest.",
                "version_hash": "n" * 64,
                "tags": ["news", "slack"],
                "tools": ["web.search", "slack.send"],
            }
        ],
        capacity=1,
    )

    messages = context["messages"]
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    user_prompt = str(messages[1]["content"])
    assert "prefer exact identifier alignment over semantic substitution" in user_prompt
    assert "return exactly that skill id" in user_prompt
    assert "id=news_digest" in user_prompt
    assert "canonical_name=news-digest" in user_prompt
    assert "display_name=News Digest Skill" in user_prompt
    assert "name=News Digest" in user_prompt
    assert "summary=Summarize news into a digest." in user_prompt


def test_resolve_skill_pipeline_legacy_skill_prefix_does_not_short_circuit_llm() -> (
    None
):
    runner = _runner(
        catalog=_catalog("claude-api", "news_digest"),
        llm=_LLM({"skill_ids": ["news_digest"], "intent": "news digest"}),
    )
    state = _state(mode=SKILL_SELECTION_AUTO)
    logger = _Logger()

    result = resolve_skill_pipeline(
        runner,
        intent="skill:news_digest I need a Slack-ready digest.",
        purpose="plan",
        state=state,
        logger=logger,
    )

    assert result.selection_mode == "llm-select"
    assert [ref.skill_id for ref in result.selected_refs] == ["news_digest"]
    assert len(runner.llm_api.calls) == 1


def test_resolve_skill_pipeline_empty_llm_selection_returns_no_skill() -> None:
    runner = _runner(
        catalog=_catalog("claude-api", "news_digest"),
        llm=_LLM({"skill_ids": [], "intent": "unknown workflow"}),
    )
    state = _state(mode=SKILL_SELECTION_AUTO)
    logger = _Logger()

    result = resolve_skill_pipeline(
        runner,
        intent="I need the first steps for a missing_skill workflow.",
        purpose="plan",
        state=state,
        logger=logger,
    )

    assert result.selection_mode == "llm-select"
    assert result.selected_refs == []
    assert result.fail_closed_reason is None
    assert len(runner.llm_api.calls) == 1
    assert not any(event["type"] == "skill.selected" for event in logger.events)


def test_describe_skill_catalog_auto_multi_skill_projects_narrowing() -> None:
    catalog_state = describe_skill_catalog(
        profile=_profile(skill=None, skill_catalog=["alpha", "beta"]),
        state=_state(),
        catalog=_catalog("alpha", "beta"),
    )

    assert catalog_state.auto_enabled is True
    assert catalog_state.projected_selection_mode == "llm-select"


def test_resolve_skill_pipeline_auto_multi_skill_under_capacity_uses_llm_not_direct(
    monkeypatch,
) -> None:
    monkeypatch.setattr(skill_pipeline, "_direct_capacity", lambda catalog: 10)
    llm = _LLM({"skill_ids": ["beta"], "intent": "use beta"})
    runner = _runner(catalog=_catalog("alpha", "beta"), llm=llm)
    state = _state()
    logger = _Logger()

    result = resolve_skill_pipeline(
        runner,
        intent="help with beta",
        purpose="plan",
        state=state,
        logger=logger,
    )

    assert result.selection_mode == "llm-select"
    assert [ref.skill_id for ref in result.selected_refs] == ["beta"]
    assert llm.calls


def test_skill_selection_event_reports_primary_and_full_selected_set() -> None:
    runner = _runner(
        catalog=_catalog("alpha", "beta"),
        llm=MagicMock(),
        profile=_profile(skill=["alpha", "beta"], skill_catalog=["alpha", "beta"]),
    )
    state = _state()
    logger = _Logger()

    result = resolve_skill_pipeline(
        runner,
        intent="help with the deployment",
        purpose="plan",
        state=state,
        logger=logger,
    )

    assert result.selection_mode == "direct"
    assert [ref.skill_id for ref in result.selected_refs] == ["alpha", "beta"]
    selected = next(
        event for event in logger.events if event["type"] == "skill.selected"
    )
    assert selected["payload"]["skill_ref"]["id"] == "alpha"
    assert selected["payload"]["primary_skill_id"] == "alpha"
    assert selected["payload"]["selected_skill_ids"] == ["alpha", "beta"]
    assert selected["payload"]["selected_skill_count"] == 2
    apply_skill_selection_to_state(state=state, result=result)
    assert state.active_skill_id == "alpha"
    assert state.active_skill_ids == ["alpha", "beta"]
    assert state.resolved_skill_ids == ["alpha", "beta"]


def test_describe_skill_catalog_respects_configured_session_skill_capacity(
    monkeypatch,
) -> None:
    monkeypatch.setattr(skill_pipeline, "_direct_capacity", lambda catalog: 10)
    profile = _profile(skill=["alpha", "beta"], skill_catalog=["alpha", "beta"])
    profile.max_skills_per_session = 1

    catalog_state = describe_skill_catalog(
        profile=profile,
        state=_state(),
        catalog=_catalog("alpha", "beta"),
    )

    assert catalog_state.capacity == 1
    assert catalog_state.projected_selection_mode == "llm-select"


def test_resolve_skill_pipeline_uses_llm_subset_selection_over_capacity(
    monkeypatch,
) -> None:
    monkeypatch.setattr(skill_pipeline, "_direct_capacity", lambda catalog: 1)
    llm = _LLM({"skill_ids": ["beta"], "intent": "use beta"})
    runner = _runner(catalog=_catalog("alpha", "beta"), llm=llm)
    state = _state(mode=SKILL_SELECTION_AUTO)
    logger = _Logger()

    result = resolve_skill_pipeline(
        runner,
        intent="help with beta",
        purpose="plan",
        state=state,
        logger=logger,
    )

    assert result.selection_mode == "llm-select"
    assert [ref.skill_id for ref in result.selected_refs] == ["beta"]
    assert llm.calls
    assert llm.calls[0]["schema"].__name__ == "SkillSubsetSelection"
    selected = next(
        event for event in logger.events if event["type"] == "skill.selected"
    )
    assert selected["payload"]["selection_mode"] == "llm-select"


def test_resolve_skill_pipeline_uses_retrieval_before_llm_confirm(monkeypatch) -> None:
    monkeypatch.setattr(skill_pipeline, "_direct_capacity", lambda catalog: 1)
    llm = _LLM({"skill_ids": ["gamma"], "intent": "use gamma"})
    retrieve_api = _RetrieveAPI([{"ref_id": f"skill:gamma@{'g' * 64}"}])
    runner = _runner(
        catalog=_catalog("alpha", "beta", "gamma"),
        llm=llm,
        retrieve_api=retrieve_api,
    )
    state = _state(mode=SKILL_SELECTION_AUTO)
    logger = _Logger()

    result = resolve_skill_pipeline(
        runner,
        intent="help with gamma",
        purpose="plan",
        state=state,
        logger=logger,
    )

    assert result.selection_mode == "retrieval-select"
    assert [ref.skill_id for ref in result.selected_refs] == ["gamma"]
    assert len(retrieve_api.ingested) == 3
    assert retrieve_api.ingested[2]["meta"]["title"] == "Gamma"
    assert "id=gamma" in retrieve_api.ingested[2]["meta"]["text"]
    assert "aliases=Gamma" in retrieve_api.ingested[2]["meta"]["text"]
    assert len(retrieve_api.calls) == 1
    assert retrieve_api.calls[0]["purpose"] == "plan"
    shortlisted = [
        event for event in logger.events if event["type"] == "skill.shortlisted"
    ]
    assert shortlisted


def test_resolve_skill_pipeline_falls_back_to_full_catalog_llm_when_retrieval_pick_is_empty(
    monkeypatch,
) -> None:
    monkeypatch.setattr(skill_pipeline, "_direct_capacity", lambda catalog: 1)
    llm = _SequenceLLM(
        [
            {"skill_ids": [], "intent": "no shortlist match"},
            {"skill_ids": ["mcp_builder"], "intent": "use mcp builder"},
        ]
    )
    retrieve_api = _RetrieveAPI(
        [
            {"ref_id": f"skill:figma@{'f' * 64}"},
            {"ref_id": f"skill:github_pr@{'g' * 64}"},
        ]
    )
    runner = _runner(
        catalog=_catalog("mcp_builder", "figma", "github_pr"),
        llm=llm,
        retrieve_api=retrieve_api,
    )
    state = _state(mode=SKILL_SELECTION_AUTO)
    logger = _Logger()

    result = resolve_skill_pipeline(
        runner,
        intent="I need the standard workflow for an mcp builder task.",
        purpose="plan",
        state=state,
        logger=logger,
    )

    assert result.selection_mode == "llm-select"
    assert [ref.skill_id for ref in result.selected_refs] == ["mcp_builder"]
    assert len(llm.calls) == 2
    prerouting_events = [
        event["payload"]
        for event in logger.events
        if event["type"] == "skill.prerouting"
    ]
    assert any(payload.get("strategy") == "llm" for payload in prerouting_events)
    assert prerouting_events[-1]["strategy"] == "llm-select"


def test_retrieval_ingest_text_humanizes_slug_style_skill_ids(monkeypatch) -> None:
    monkeypatch.setattr(skill_pipeline, "_direct_capacity", lambda catalog: 1)
    llm = _LLM({"skill_ids": ["mcp_builder"], "intent": "use mcp builder"})
    retrieve_api = _RetrieveAPI([{"ref_id": f"skill:mcp_builder@{'m' * 64}"}])
    runner = _runner(
        catalog=_catalog("mcp_builder", "claude-api", "linear"),
        llm=llm,
        retrieve_api=retrieve_api,
    )
    state = _state(mode=SKILL_SELECTION_AUTO)
    logger = _Logger()

    result = resolve_skill_pipeline(
        runner,
        intent="help with mcp builder",
        purpose="plan",
        state=state,
        logger=logger,
    )

    assert result.selection_mode == "retrieval-select"
    mcp_meta = next(
        item["meta"]
        for item in retrieve_api.ingested
        if item["skill_id"] == "mcp_builder"
    )
    assert mcp_meta["title"] == "Mcp Builder"
    assert "id=mcp_builder" in mcp_meta["text"]
    assert "aliases=Mcp Builder" in mcp_meta["text"]


def test_retrieval_shortlist_limit_scales_with_catalog_size() -> None:
    assert skill_pipeline._retrieval_shortlist_limit(catalog_size=0, capacity=1) == 6
    assert skill_pipeline._retrieval_shortlist_limit(catalog_size=5, capacity=1) == 6
    assert skill_pipeline._retrieval_shortlist_limit(catalog_size=10, capacity=1) == 10
    assert skill_pipeline._retrieval_shortlist_limit(catalog_size=20, capacity=1) == 20
    assert skill_pipeline._retrieval_shortlist_limit(catalog_size=30, capacity=1) == 24
    assert skill_pipeline._retrieval_shortlist_limit(catalog_size=10, capacity=12) == 12


def test_resolve_skill_pipeline_dense_catalog_exact_named_skill_prefers_direct_identity(
    monkeypatch,
) -> None:
    monkeypatch.setattr(skill_pipeline, "_direct_capacity", lambda catalog: 1)
    catalog = _catalog(
        "alpha",
        "beta",
        "gamma",
        "delta",
        "epsilon",
        "zeta",
        "eta",
        "theta",
        "iota",
        "claude-api",
    )
    llm = _LLM({"skill_ids": ["claude-api"], "intent": "use claude api"})
    retrieve_api = _RetrieveAPI([{"ref_id": f"skill:claude-api@{'c' * 64}"}])
    runner = _runner(
        catalog=catalog,
        llm=llm,
        retrieve_api=retrieve_api,
    )
    state = _state(mode=SKILL_SELECTION_AUTO)
    logger = _Logger()

    result = resolve_skill_pipeline(
        runner,
        intent="Use the claude-api skill to outline the first API steps.",
        purpose="plan",
        state=state,
        logger=logger,
    )

    assert result.selection_mode == "direct"
    assert [ref.skill_id for ref in result.selected_refs] == ["claude-api"]
    assert retrieve_api.ingested == []
    assert retrieve_api.calls == []
    assert llm.calls == []


def test_resolve_skill_pipeline_exact_named_skill_short_circuits_retrieval_and_llm(
    monkeypatch,
) -> None:
    monkeypatch.setattr(skill_pipeline, "_direct_capacity", lambda catalog: 1)
    llm = _LLM({"skill_ids": ["alpha"], "intent": "wrong fallback"})
    retrieve_api = _RetrieveAPI([{"ref_id": f"skill:alpha@{'a' * 64}"}])
    runner = _runner(
        catalog=_catalog(
            "alpha",
            "github_pr",
            "beta",
            "gamma",
            "delta",
            "epsilon",
            "zeta",
            "eta",
            "theta",
        ),
        llm=llm,
        retrieve_api=retrieve_api,
    )
    state = _state(mode=SKILL_SELECTION_AUTO)
    logger = _Logger()

    result = resolve_skill_pipeline(
        runner,
        intent="Use the github_pr skill to review a risky pull request.",
        purpose="plan",
        state=state,
        logger=logger,
    )

    assert result.selection_mode == "direct"
    assert [ref.skill_id for ref in result.selected_refs] == ["github_pr"]
    assert retrieve_api.calls == []
    assert llm.calls == []
    selected = next(
        event for event in logger.events if event["type"] == "skill.selected"
    )
    assert selected["payload"]["skill_ref"]["id"] == "github_pr"
    assert selected["payload"]["selected_skill_ids"] == ["github_pr"]


def test_resolve_skill_pipeline_exact_named_skill_matches_compact_identity(
    monkeypatch,
) -> None:
    monkeypatch.setattr(skill_pipeline, "_direct_capacity", lambda catalog: 1)
    llm = _LLM({"skill_ids": [], "intent": "no match"})
    retrieve_api = _RetrieveAPI([])
    runner = _runner(
        catalog=_catalog(
            "alpha",
            "figma_code_connect_components",
            "beta",
            "gamma",
            "delta",
            "epsilon",
            "zeta",
            "eta",
            "theta",
        ),
        llm=llm,
        retrieve_api=retrieve_api,
    )
    state = _state(mode=SKILL_SELECTION_AUTO)
    logger = _Logger()

    result = resolve_skill_pipeline(
        runner,
        intent=(
            "Use the figma code connect components skill to map Figma components "
            "to our React design system."
        ),
        purpose="plan",
        state=state,
        logger=logger,
    )

    assert result.selection_mode == "direct"
    assert [ref.skill_id for ref in result.selected_refs] == [
        "figma_code_connect_components"
    ]
    assert retrieve_api.calls == []
    assert llm.calls == []


def test_resolve_skill_pipeline_ambiguous_named_skill_fails_closed_to_llm(
    monkeypatch,
) -> None:
    monkeypatch.setattr(skill_pipeline, "_direct_capacity", lambda catalog: 1)
    llm = _LLM({"skill_ids": [], "intent": "ambiguous"})
    catalog = [
        {
            **_catalog("alpha")[0],
            "id": "alpha",
            "name": "Shared Skill",
            "display_name": "Shared Skill",
            "canonical_name": "alpha",
        },
        {
            **_catalog("beta")[0],
            "id": "beta",
            "name": "Shared Skill",
            "display_name": "Shared Skill",
            "canonical_name": "beta",
        },
        *_catalog("gamma", "delta", "epsilon", "zeta", "eta", "theta", "iota"),
    ]
    retrieve_api = _RetrieveAPI([])
    runner = _runner(catalog=catalog, llm=llm, retrieve_api=retrieve_api)
    state = _state(mode=SKILL_SELECTION_AUTO)
    logger = _Logger()

    result = resolve_skill_pipeline(
        runner,
        intent="Use the Shared Skill for this task.",
        purpose="plan",
        state=state,
        logger=logger,
    )

    assert result.selection_mode == "llm-select"
    assert result.selected_refs == []
    assert retrieve_api.calls
    assert llm.calls


def _is_explicit(intent: str, candidate: str) -> bool:
    return skill_pipeline._is_explicit_named_skill_request(
        intent_lower=intent.lower(),
        candidate_lower=candidate.lower(),
    )


def test_is_explicit_named_skill_request_matches_use_the_X_skill() -> None:
    assert _is_explicit("Use the github_pr skill to review a PR.", "github_pr")


def test_is_explicit_named_skill_request_matches_use_X_skill_without_the() -> None:
    assert _is_explicit("Use github_pr skill for this task.", "github_pr")


def test_is_explicit_named_skill_request_matches_use_the_exact_skill_X() -> None:
    assert _is_explicit("Use the exact skill github_pr now.", "github_pr")


def test_is_explicit_named_skill_request_matches_named_skill_X() -> None:
    assert _is_explicit(
        "Please apply the named skill github_pr to this issue.", "github_pr"
    )


def test_is_explicit_named_skill_request_matches_intent_equals_candidate() -> None:
    assert _is_explicit("github_pr", "github_pr")
    assert _is_explicit("github-pr", "github_pr")


def test_is_explicit_named_skill_request_normalizes_hyphen_and_underscore() -> None:
    assert _is_explicit(
        "Use the github_pr skill for reviewing risky changes.", "github-pr"
    )
    assert _is_explicit(
        "Use the figma code connect components skill.",
        "figma_code_connect_components",
    )


def test_is_explicit_named_skill_request_is_case_insensitive() -> None:
    assert skill_pipeline._is_explicit_named_skill_request(
        intent_lower="USE THE GITHUB_PR SKILL.".lower(),
        candidate_lower="GITHUB_PR".lower(),
    )


def test_is_explicit_named_skill_request_rejects_no_use_keyword() -> None:
    assert not _is_explicit("I want the github_pr skill for review.", "github_pr")
    assert not _is_explicit("With the github_pr skill we can review.", "github_pr")


def test_is_explicit_named_skill_request_rejects_no_trailing_skill_word() -> None:
    assert not _is_explicit("Please use github_pr to review.", "github_pr")
    assert not _is_explicit("Just use github_pr now.", "github_pr")


def test_is_explicit_named_skill_request_rejects_unrelated_intent() -> None:
    assert not _is_explicit("help me triage this pull request", "github_pr")
    assert not _is_explicit("review the open PR for me", "github_pr")


def test_is_explicit_named_skill_request_rejects_empty_inputs() -> None:
    assert not _is_explicit("", "github_pr")
    assert not _is_explicit("use the github_pr skill", "")
    assert not _is_explicit("   ", "github_pr")


def test_is_explicit_named_skill_request_respects_word_boundary_after_skill() -> None:
    assert not _is_explicit("use the skillset github_pr now", "github_pr")
    assert _is_explicit("use the github_pr skill, then close.", "github_pr")


def test_is_explicit_named_skill_request_does_not_partial_match_short_candidate() -> (
    None
):
    assert not _is_explicit("Use the github_pr skill.", "pr")
    assert _is_explicit("Use the pr skill.", "pr")


def test_resolve_unique_named_skill_refs_returns_single_match() -> None:
    catalog = _catalog("alpha", "github_pr", "beta")
    refs = skill_pipeline._resolve_unique_named_skill_refs(
        intent="Use the github_pr skill to review.",
        catalog=catalog,
    )
    assert len(refs) == 1
    assert refs[0].skill_id == "github_pr"
    assert refs[0].source == "direct-named"


def test_resolve_unique_named_skill_refs_fails_closed_on_ambiguity() -> None:
    catalog = [
        {
            **_catalog("alpha")[0],
            "id": "alpha",
            "display_name": "Shared Skill",
        },
        {
            **_catalog("beta")[0],
            "id": "beta",
            "display_name": "Shared Skill",
        },
    ]
    refs = skill_pipeline._resolve_unique_named_skill_refs(
        intent="Use the shared skill to do this.",
        catalog=catalog,
    )
    assert refs == []


def test_resolve_unique_named_skill_refs_returns_empty_when_no_match() -> None:
    catalog = _catalog("alpha", "beta", "gamma")
    refs = skill_pipeline._resolve_unique_named_skill_refs(
        intent="help me triage a pull request",
        catalog=catalog,
    )
    assert refs == []


def test_resolve_unique_named_skill_refs_handles_empty_inputs() -> None:
    assert (
        skill_pipeline._resolve_unique_named_skill_refs(intent="anything", catalog=[])
        == []
    )
    assert (
        skill_pipeline._resolve_unique_named_skill_refs(
            intent="", catalog=_catalog("alpha")
        )
        == []
    )
    assert (
        skill_pipeline._resolve_unique_named_skill_refs(
            intent="   ", catalog=_catalog("alpha")
        )
        == []
    )


def test_intent_matches_skill_identity_uses_display_name_after_normalization() -> None:
    entry = {
        "id": "figma_code_connect_components",
        "name": "Figma Code Connect Components",
        "display_name": "Figma Code Connect Components",
        "canonical_name": "figma_code_connect_components",
    }
    assert skill_pipeline._intent_matches_skill_identity(
        intent_lower=("use the figma code connect components skill to map components"),
        entry=entry,
    )


def test_intent_matches_skill_identity_rejects_unrelated_intent() -> None:
    entry = {
        "id": "github_pr",
        "name": "GitHub PR",
        "display_name": "GitHub PR",
        "canonical_name": "github_pr",
    }
    assert not skill_pipeline._intent_matches_skill_identity(
        intent_lower="please help me with code review",
        entry=entry,
    )


def test_resolve_skill_pipeline_direct_named_emits_direct_named_selection_reason() -> (
    None
):
    runner = _runner(
        catalog=_catalog("alpha", "github_pr", "beta"),
        llm=_LLM({"skill_ids": [], "intent": "ignored"}),
    )
    state = _state(mode=SKILL_SELECTION_AUTO)
    logger = _Logger()

    result = resolve_skill_pipeline(
        runner,
        intent="Use the github_pr skill to review.",
        purpose="plan",
        state=state,
        logger=logger,
    )

    assert result.selection_mode == "direct"
    assert (
        result.selection_reason == skill_pipeline._SKILL_SELECTION_REASON_DIRECT_NAMED
    )
    assert (
        skill_pipeline._SKILL_SELECTION_REASON_DIRECT_NAMED
        != skill_pipeline._SKILL_SELECTION_REASON_DIRECT_SINGLE_CATALOG
    )


def test_resolve_skill_pipeline_direct_single_catalog_emits_direct_single_catalog_reason() -> (
    None
):
    runner = _runner(
        catalog=_catalog("deploy-checker"),
        llm=_LLM({"skill_ids": [], "intent": "ignored"}),
    )
    state = _state(mode=SKILL_SELECTION_AUTO)
    logger = _Logger()

    result = resolve_skill_pipeline(
        runner,
        intent="please help me with something general",
        purpose="plan",
        state=state,
        logger=logger,
    )

    assert result.selection_mode == "direct", (
        f"expected direct path, got {result.selection_mode}; "
        f"selection_reason={result.selection_reason}"
    )
    assert (
        result.selection_reason
        == skill_pipeline._SKILL_SELECTION_REASON_DIRECT_SINGLE_CATALOG
    )


def test_prerouting_payload_includes_llm_pick_details_on_llm_select() -> None:
    runner = _runner(
        catalog=_catalog(
            "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta"
        ),
        llm=_LLM({"skill_ids": ["alpha", "ghost"], "intent": "mixed"}),
    )
    state = _state(mode=SKILL_SELECTION_AUTO)
    logger = _Logger()

    resolve_skill_pipeline(
        runner,
        intent="please help me with something general",
        purpose="plan",
        state=state,
        logger=logger,
    )

    prerouting_events = [
        event for event in logger.events if event["type"] == "skill.prerouting"
    ]
    assert prerouting_events, "expected at least one skill.prerouting event"
    last = prerouting_events[-1]
    assert "llm_pick_details" in last["payload"], (
        f"expected llm_pick_details in payload, got keys: "
        f"{list(last['payload'].keys())}"
    )
    details = last["payload"]["llm_pick_details"]
    assert details["raw_pick_ids"] == ["alpha", "ghost"]
    assert details["invalid_pick_ids"] == ["ghost"]
    assert details["clamped_pick_count"] == 0


def test_prerouting_payload_omits_llm_pick_details_on_direct_named_path() -> None:
    runner = _runner(
        catalog=_catalog("alpha", "github_pr", "beta"),
        llm=_LLM({"skill_ids": [], "intent": "ignored"}),
    )
    state = _state(mode=SKILL_SELECTION_AUTO)
    logger = _Logger()

    resolve_skill_pipeline(
        runner,
        intent="Use the github_pr skill to review.",
        purpose="plan",
        state=state,
        logger=logger,
    )

    prerouting_events = [
        event for event in logger.events if event["type"] == "skill.prerouting"
    ]
    assert prerouting_events
    assert "llm_pick_details" not in prerouting_events[-1]["payload"]


def test_no_magic_phrase_dense_prompt_does_not_trigger_fast_path() -> None:
    from tests.e2e.test_live_skill_dense_catalog_matrix import (
        _no_magic_phrase_dense_skill_prompt,
    )

    sample_skill_ids = [
        "github_pr",
        "linear",
        "playwright",
        "figma",
        "figma_code_connect_components",
        "webapp-testing",
        "data_export",
        "frontend-skill",
        "social_post",
        "claude-api",
        "news_digest",
    ]
    for skill_id in sample_skill_ids:
        prompt = _no_magic_phrase_dense_skill_prompt(skill_id=skill_id)
        entry = {
            "id": skill_id,
            "name": skill_id.replace("-", " ").replace("_", " ").title(),
            "display_name": skill_id.replace("-", " ").replace("_", " ").title(),
            "canonical_name": skill_id,
        }
        assert not skill_pipeline._intent_matches_skill_identity(
            intent_lower=prompt.lower(),
            entry=entry,
        ), (
            f"no-magic-phrase prompt for skill_id={skill_id!r} unexpectedly "
            f"matched the fast path: prompt={prompt!r}"
        )
