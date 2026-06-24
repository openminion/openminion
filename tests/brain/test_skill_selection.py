from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from openminion.modules.brain.bootstrap import skill_selection
from openminion.modules.brain.bootstrap.skill.selection import (
    _catalog_retrieval_text,
    _catalog_retrieval_title,
    _classify_failure_reason,
    _select_skills_with_llm,
)
from openminion.modules.brain.constants import (
    SKILL_SELECTION_INVALID_SKILL_ID,
    SKILL_SELECTION_MODEL_UNAVAILABLE,
    SKILL_SELECTION_PARSE_ERROR,
    SKILL_SELECTION_RATE_LIMITED,
    SKILL_SELECTION_REASON_LLM,
    SKILL_SELECTION_REASON_RETRIEVAL,
    SKILL_SELECTION_TIMEOUT,
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


class _StubLLM:
    def __init__(
        self,
        response: dict[str, Any] | None = None,
        *,
        exception: Exception | None = None,
    ) -> None:
        self._response = response
        self._exception = exception
        self.calls: list[dict[str, Any]] = []

    def estimate_tokens(self, *, model: str, context: dict[str, Any]) -> int:
        del model, context
        return 16

    def call_structured(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self._exception is not None:
            raise self._exception
        return dict(self._response or {})


def _catalog(*skill_ids: str) -> list[dict[str, str]]:
    return [
        {
            "id": skill_id,
            "name": skill_id.replace("-", " ").replace("_", " ").title(),
            "display_name": skill_id.replace("-", " ").replace("_", " ").title(),
            "canonical_name": skill_id,
            "short_description": f"{skill_id} helper description",
            "version_hash": skill_id[0] * 64,
            "tags": [skill_id.split("-", 1)[0]],
            "tools": [f"tool.{skill_id.split('-', 1)[0]}"],
        }
        for skill_id in skill_ids
    ]


def _state() -> SimpleNamespace:
    return SimpleNamespace(
        agent_id="agent-1",
        session_id="session-1",
        trace_id="trace-1",
        session_skill_loaded=[],
        session_skill_unloaded=[],
        skill_selection_mode=None,
        active_skill_id=None,
        active_skill_version_hash=None,
        resolved_skill_ids=[],
        resolved_skill_versions={},
    )


def _runner(*, llm: _StubLLM | None) -> SimpleNamespace:
    return SimpleNamespace(
        llm_api=llm,
        profile=SimpleNamespace(
            llm_profiles=SimpleNamespace(
                act_model="stub-model",
                summarize_model="summarize-default",
            ),
        ),
    )


# Retrieval catalog helpers (SRSS-10/11 characterization support)


def test_catalog_retrieval_title_humanizes_slug_like_name_when_display_missing() -> (
    None
):
    entry = {
        "id": "mcp_builder",
        "name": "mcp_builder",
        "display_name": "",
        "canonical_name": "mcp_builder",
        "short_description": "Build and configure MCP server integrations.",
        "tags": ["mcp", "builder"],
        "tools": ["file"],
    }

    assert _catalog_retrieval_title(entry) == "mcp builder"


def test_catalog_retrieval_text_keeps_structured_line_and_aliases() -> None:
    entry = {
        "id": "github_pr",
        "name": "github_pr",
        "display_name": "",
        "canonical_name": "github_pr",
        "short_description": "Review a GitHub pull request and summarize risk.",
        "tags": ["github", "pull-request"],
        "tools": ["http_request", "file"],
    }

    text = _catalog_retrieval_text(entry)

    assert "id=github_pr" in text
    assert "summary=Review a GitHub pull request and summarize risk." in text
    assert "aliases=github_pr ; github pr" in text


def test_classify_failure_reason_recognizes_timeout_code() -> None:
    exc = Exception("anything")
    exc.code = "TIMEOUT"  # type: ignore[attr-defined]
    assert _classify_failure_reason(exc) == SKILL_SELECTION_TIMEOUT


def test_classify_failure_reason_recognizes_rate_limited_code() -> None:
    exc = Exception("anything")
    exc.code = "RATE_LIMITED"  # type: ignore[attr-defined]
    assert _classify_failure_reason(exc) == SKILL_SELECTION_RATE_LIMITED


def test_classify_failure_reason_routes_structured_output_to_parse_error() -> None:
    assert (
        _classify_failure_reason(Exception("structured output validation failed"))
        == SKILL_SELECTION_PARSE_ERROR
    )
    assert (
        _classify_failure_reason(Exception("submit_output rejected"))
        == SKILL_SELECTION_PARSE_ERROR
    )


def test_classify_failure_reason_fallback_message_classification() -> None:
    assert (
        _classify_failure_reason(Exception("rate limited by upstream"))
        == SKILL_SELECTION_RATE_LIMITED
    )
    assert (
        _classify_failure_reason(Exception("request timed out after 30s"))
        == SKILL_SELECTION_TIMEOUT
    )
    assert (
        _classify_failure_reason(Exception("provider returned HTTP 500"))
        == SKILL_SELECTION_MODEL_UNAVAILABLE
    )


def test_select_skills_with_llm_returns_model_unavailable_when_no_llm_api() -> None:
    runner = _runner(llm=None)
    result = _select_skills_with_llm(
        runner,
        intent="do a code review",
        state=_state(),
        catalog=_catalog("alpha", "beta"),
        capacity=2,
        logger=_Logger(),
        strategy="llm",
    )
    assert result.selected_refs == []
    assert result.fail_closed_reason == SKILL_SELECTION_MODEL_UNAVAILABLE
    assert result.selection_mode == "llm-select"
    assert result.routed_intent == "do a code review"


def test_select_skills_with_llm_rejects_invalid_skill_id_only() -> None:
    llm = _StubLLM(response={"skill_ids": ["not_in_catalog"], "intent": "ghost intent"})
    runner = _runner(llm=llm)
    result = _select_skills_with_llm(
        runner,
        intent="do something",
        state=_state(),
        catalog=_catalog("alpha", "beta"),
        capacity=2,
        logger=_Logger(),
        strategy="llm",
    )
    assert result.selected_refs == []
    assert result.fail_closed_reason == SKILL_SELECTION_INVALID_SKILL_ID
    assert result.routed_intent == "ghost intent"
    assert len(llm.calls) == 1


def test_select_skills_with_llm_returns_valid_skill_when_id_matches_catalog() -> None:
    llm = _StubLLM({"skill_ids": ["alpha"], "intent": "use alpha"})
    runner = _runner(llm=llm)
    result = _select_skills_with_llm(
        runner,
        intent="use alpha",
        state=_state(),
        catalog=_catalog("alpha", "beta"),
        capacity=2,
        logger=_Logger(),
        strategy="llm",
    )
    assert [ref.skill_id for ref in result.selected_refs] == ["alpha"]
    assert result.selected_refs[0].source == "llm-select"
    assert result.fail_closed_reason is None
    assert result.routed_intent == "use alpha"


def test_select_skills_with_llm_clamps_to_capacity() -> None:
    llm = _StubLLM({"skill_ids": ["alpha", "beta", "gamma", "delta"], "intent": "many"})
    runner = _runner(llm=llm)
    result = _select_skills_with_llm(
        runner,
        intent="use many",
        state=_state(),
        catalog=_catalog("alpha", "beta", "gamma", "delta"),
        capacity=2,
        logger=_Logger(),
        strategy="llm",
    )
    assert [ref.skill_id for ref in result.selected_refs] == ["alpha", "beta"]
    assert result.fail_closed_reason is None
    assert len(llm.calls) == 1


def test_select_skills_with_llm_keeps_valid_picks_when_mixed_with_invalid() -> None:
    llm = _StubLLM(
        {
            "skill_ids": ["alpha", "not_in_catalog", "beta"],
            "intent": "mixed",
        }
    )
    runner = _runner(llm=llm)
    result = _select_skills_with_llm(
        runner,
        intent="use mixed",
        state=_state(),
        catalog=_catalog("alpha", "beta", "gamma"),
        capacity=3,
        logger=_Logger(),
        strategy="llm",
    )
    assert [ref.skill_id for ref in result.selected_refs] == ["alpha", "beta"]
    assert result.fail_closed_reason is None


def test_select_skills_with_llm_returns_empty_when_llm_returns_no_ids() -> None:
    llm = _StubLLM({"skill_ids": [], "intent": "no skill needed"})
    runner = _runner(llm=llm)
    result = _select_skills_with_llm(
        runner,
        intent="just respond",
        state=_state(),
        catalog=_catalog("alpha", "beta"),
        capacity=2,
        logger=_Logger(),
        strategy="llm",
    )
    assert result.selected_refs == []
    assert result.fail_closed_reason is None
    assert result.routed_intent == "no skill needed"


def test_select_skills_with_llm_propagates_intent_when_present() -> None:
    llm = _StubLLM({"skill_ids": ["alpha"], "intent": "llm-routed intent"})
    runner = _runner(llm=llm)
    result = _select_skills_with_llm(
        runner,
        intent="original user intent",
        state=_state(),
        catalog=_catalog("alpha"),
        capacity=1,
        logger=_Logger(),
        strategy="llm",
    )
    assert result.routed_intent == "llm-routed intent"


def test_select_skills_with_llm_falls_back_to_input_intent_when_llm_intent_empty() -> (
    None
):
    llm = _StubLLM({"skill_ids": ["alpha"], "intent": "   "})
    runner = _runner(llm=llm)
    result = _select_skills_with_llm(
        runner,
        intent="original user intent",
        state=_state(),
        catalog=_catalog("alpha"),
        capacity=1,
        logger=_Logger(),
        strategy="llm",
    )
    assert result.routed_intent == "original user intent"


def test_select_skills_with_llm_deduplicates_repeated_picks() -> None:
    llm = _StubLLM({"skill_ids": ["alpha", "ALPHA", "alpha"], "intent": "dup"})
    runner = _runner(llm=llm)
    result = _select_skills_with_llm(
        runner,
        intent="dup",
        state=_state(),
        catalog=_catalog("alpha", "beta"),
        capacity=3,
        logger=_Logger(),
        strategy="llm",
    )
    assert [ref.skill_id for ref in result.selected_refs] == ["alpha"]


def test_select_skills_with_llm_returns_classified_failure_on_exception() -> None:
    exc = Exception("structured output validation failed")
    llm = _StubLLM(exception=exc)
    runner = _runner(llm=llm)
    result = _select_skills_with_llm(
        runner,
        intent="will fail",
        state=_state(),
        catalog=_catalog("alpha"),
        capacity=1,
        logger=_Logger(),
        strategy="llm",
    )
    assert result.selected_refs == []
    assert result.fail_closed_reason == SKILL_SELECTION_PARSE_ERROR
    assert result.routed_intent == "will fail"


def test_select_skills_with_llm_records_retrieval_selection_reason_when_strategy_is_retrieval() -> (
    None
):
    llm = _StubLLM({"skill_ids": ["alpha"], "intent": "use alpha"})
    runner = _runner(llm=llm)
    result_llm = _select_skills_with_llm(
        runner,
        intent="x",
        state=_state(),
        catalog=_catalog("alpha"),
        capacity=1,
        logger=_Logger(),
        strategy="llm",
    )
    assert result_llm.selection_reason == SKILL_SELECTION_REASON_LLM
    assert result_llm.selection_mode == "llm-select"

    llm2 = _StubLLM({"skill_ids": ["alpha"], "intent": "use alpha"})
    result_retrieval = _select_skills_with_llm(
        _runner(llm=llm2),
        intent="x",
        state=_state(),
        catalog=_catalog("alpha"),
        capacity=1,
        logger=_Logger(),
        strategy="retrieval",
    )
    assert result_retrieval.selection_reason == SKILL_SELECTION_REASON_RETRIEVAL
    assert result_retrieval.selection_mode == "retrieval-select"


def test_select_skills_with_llm_emits_shortlist_event_on_successful_pick() -> None:
    llm = _StubLLM({"skill_ids": ["alpha"], "intent": "ok"})
    runner = _runner(llm=llm)
    logger = _Logger()
    _select_skills_with_llm(
        runner,
        intent="x",
        state=_state(),
        catalog=_catalog("alpha"),
        capacity=1,
        logger=logger,
        strategy="llm",
    )
    shortlist_events = [
        event for event in logger.events if event["type"] == "skill.shortlisted"
    ]
    assert shortlist_events, (
        f"expected skill.shortlisted event, got events: "
        f"{[event['type'] for event in logger.events]}"
    )
    assert shortlist_events[0]["payload"]["strategy"] == "llm-llm"
    assert skill_selection._select_skills_with_llm is _select_skills_with_llm


def test_llm_pick_details_records_raw_picks_when_all_valid() -> None:
    llm = _StubLLM({"skill_ids": ["alpha", "beta"], "intent": "ok"})
    runner = _runner(llm=llm)
    result = _select_skills_with_llm(
        runner,
        intent="use these",
        state=_state(),
        catalog=_catalog("alpha", "beta", "gamma"),
        capacity=3,
        logger=_Logger(),
        strategy="llm",
    )
    assert result.llm_pick_details is not None
    assert result.llm_pick_details["raw_pick_ids"] == ["alpha", "beta"]
    assert result.llm_pick_details["invalid_pick_ids"] == []
    assert result.llm_pick_details["clamped_pick_count"] == 0


def test_llm_pick_details_records_invalid_picks() -> None:
    llm = _StubLLM(
        {"skill_ids": ["alpha", "ghost", "beta", "phantom"], "intent": "mixed"}
    )
    runner = _runner(llm=llm)
    result = _select_skills_with_llm(
        runner,
        intent="x",
        state=_state(),
        catalog=_catalog("alpha", "beta"),
        capacity=3,
        logger=_Logger(),
        strategy="llm",
    )
    assert result.llm_pick_details is not None
    assert result.llm_pick_details["raw_pick_ids"] == [
        "alpha",
        "ghost",
        "beta",
        "phantom",
    ]
    assert result.llm_pick_details["invalid_pick_ids"] == ["ghost", "phantom"]
    assert result.llm_pick_details["clamped_pick_count"] == 0
    assert [ref.skill_id for ref in result.selected_refs] == ["alpha", "beta"]


def test_llm_pick_details_records_clamped_count() -> None:
    llm = _StubLLM({"skill_ids": ["alpha", "beta", "gamma", "delta"], "intent": "many"})
    runner = _runner(llm=llm)
    result = _select_skills_with_llm(
        runner,
        intent="x",
        state=_state(),
        catalog=_catalog("alpha", "beta", "gamma", "delta"),
        capacity=2,
        logger=_Logger(),
        strategy="llm",
    )
    assert result.llm_pick_details is not None
    assert result.llm_pick_details["raw_pick_ids"] == [
        "alpha",
        "beta",
        "gamma",
        "delta",
    ]
    assert result.llm_pick_details["invalid_pick_ids"] == []
    assert result.llm_pick_details["clamped_pick_count"] == 2
    assert [ref.skill_id for ref in result.selected_refs] == ["alpha", "beta"]


def test_llm_pick_details_present_when_llm_returns_empty_list() -> None:
    llm = _StubLLM({"skill_ids": [], "intent": "no skill"})
    runner = _runner(llm=llm)
    result = _select_skills_with_llm(
        runner,
        intent="x",
        state=_state(),
        catalog=_catalog("alpha"),
        capacity=1,
        logger=_Logger(),
        strategy="llm",
    )
    assert result.llm_pick_details is None


def test_llm_pick_details_absent_when_llm_unavailable() -> None:
    result = _select_skills_with_llm(
        _runner(llm=None),
        intent="x",
        state=_state(),
        catalog=_catalog("alpha"),
        capacity=1,
        logger=_Logger(),
        strategy="llm",
    )
    assert result.llm_pick_details is None


def test_llm_pick_details_absent_on_exception_path() -> None:
    llm = _StubLLM(exception=Exception("structured output validation failed"))
    runner = _runner(llm=llm)
    result = _select_skills_with_llm(
        runner,
        intent="x",
        state=_state(),
        catalog=_catalog("alpha"),
        capacity=1,
        logger=_Logger(),
        strategy="llm",
    )
    assert result.llm_pick_details is None
    assert result.fail_closed_reason == SKILL_SELECTION_PARSE_ERROR
