from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import openminion.modules.brain.bootstrap.freshness_classify as freshness_classify_module
import openminion.modules.brain.execution.closure as closure_module
import openminion.modules.brain.runtime.verification.policy as policy_verify_module
from openminion.modules.brain.bootstrap.freshness_classify import (
    build_freshness_hints,
    classify_request_freshness,
    map_freshness_obligations,
)
from openminion.modules.brain.execution.closure import final_close_message
from openminion.modules.brain.runtime.verification.policy import verify_freshness_answer
from openminion.modules.brain.schemas import (
    ActionResult,
    BudgetCounters,
    FreshnessContract,
    FreshnessDiagnostics,
    FreshnessObligations,
    WorkingState,
)

_SAMPLE_README_PATH = "/workspace/README.md"


def _runner() -> SimpleNamespace:
    return SimpleNamespace(
        llm_api=object(),
        profile=SimpleNamespace(
            llm_profiles=SimpleNamespace(decide_model="test-model", act_model="")
        ),
    )


def _state() -> WorkingState:
    return WorkingState(
        session_id="freshness-session",
        agent_id="freshness-agent",
        trace_id="trace-freshness",
        budgets_remaining=BudgetCounters(
            ticks=10,
            tool_calls=5,
            a2a_calls=2,
            tokens=2000,
            time_ms=60000,
        ),
    )


def test_semantic_freshness_request_can_require_live_data_without_keyword_gate() -> (
    None
):
    payload = {
        "intent": "market_momentum_snapshot",
        "domain": "finance",
        "time_sensitive": True,
        "needs_live_data": True,
        "needs_sources": True,
        "needs_exact_date": True,
        "answer_mode": "browse_then_answer",
        "reason": "The request depends on rapidly changing market facts.",
        "confidence": 0.94,
    }
    with patch(
        "openminion.modules.brain.bootstrap.freshness_classify.call_structured_with_retry",
        return_value=payload,
    ):
        contract, obligations, diagnostics = classify_request_freshness(
            _runner(),
            state=_state(),
            user_input="what's hot in the market right now?",
            logger=SimpleNamespace(),
        )

    assert contract.time_sensitive is True
    assert contract.needs_live_data is True
    assert obligations.require_live_data is True
    assert obligations.require_sources is True
    assert diagnostics.classifier_mode == "llm"


def test_lexical_false_positive_can_stay_local_only_when_classifier_says_no() -> None:
    payload = {
        "intent": "python_learning_note",
        "domain": "general",
        "time_sensitive": False,
        "needs_live_data": False,
        "needs_sources": False,
        "needs_exact_date": False,
        "answer_mode": "local_only",
        "reason": "The request is reflective, not current-events dependent.",
        "confidence": 0.88,
    }
    with patch(
        "openminion.modules.brain.bootstrap.freshness_classify.call_structured_with_retry",
        return_value=payload,
    ):
        contract, obligations, _diagnostics = classify_request_freshness(
            _runner(),
            state=_state(),
            user_input="today I learned about Python lists",
            logger=SimpleNamespace(),
        )

    assert contract.time_sensitive is False
    assert obligations.require_live_data is False
    assert obligations.require_sources is False


def test_direct_tool_request_skips_freshness_classifier_call() -> None:
    with patch(
        "openminion.modules.brain.bootstrap.freshness_classify.call_structured_with_retry",
        side_effect=AssertionError(
            "classifier should be skipped for direct tool request"
        ),
    ):
        contract, obligations, diagnostics = classify_request_freshness(
            _runner(),
            state=_state(),
            user_input=(
                f"use file.read on {_SAMPLE_README_PATH} "
                "and reply with the first sentence only"
            ),
            logger=SimpleNamespace(),
        )

    assert contract.intent == "file.read"
    assert contract.time_sensitive is False
    assert obligations.require_live_data is False
    assert diagnostics.classifier_mode == "skipped_direct_tool_request"


def test_explicit_tool_command_skips_freshness_classifier_call() -> None:
    with patch(
        "openminion.modules.brain.bootstrap.freshness_classify.call_structured_with_retry",
        side_effect=AssertionError(
            "classifier should be skipped for explicit tool command"
        ),
    ):
        contract, obligations, diagnostics = classify_request_freshness(
            _runner(),
            state=_state(),
            user_input='tool web.search {"query":"latest news on iran"}',
            logger=SimpleNamespace(),
        )

    assert contract.intent == "web.search"
    assert contract.time_sensitive is False
    assert obligations.require_live_data is False
    assert diagnostics.classifier_mode == "skipped_direct_tool_request"


def test_contract_to_obligation_mapping_is_structured_and_typed() -> None:
    contract = FreshnessContract(
        intent="latest_market_news_and_stock_list",
        domain="finance",
        time_sensitive=True,
        needs_live_data=True,
        needs_sources=True,
        needs_exact_date=True,
        answer_mode="browse_then_answer",
        reason="Latest finance information needs live evidence.",
        confidence=0.97,
    )
    obligations = map_freshness_obligations(contract)
    hints = build_freshness_hints(contract=contract, obligations=obligations)

    assert obligations.require_live_data is True
    assert obligations.require_sources is True
    assert obligations.require_exact_date is True
    assert hints["freshness_contract"]["domain"] == "finance"
    assert hints["freshness_obligations"]["require_sources"] is True
    assert "style_overrides" in hints
    assert "freshness_exact_date_contract" in hints["style_overrides"]


def test_freshness_runtime_does_not_parse_final_answer_prose() -> None:
    policy_source = Path(policy_verify_module.__file__).read_text(encoding="utf-8")
    closure_source = Path(closure_module.__file__).read_text(encoding="utf-8")
    freshness_source = Path(freshness_classify_module.__file__).read_text(
        encoding="utf-8"
    )

    banned_policy_markers = (
        "_URL_PATTERN",
        "_MARKDOWN_LINK_PATTERN",
        "_DATE_PATTERNS",
        "_CURRENT_CLAIM_PATTERN",
        "_FALLBACK_PATTERN",
        "_has_source_attribution",
        "_has_exact_date",
        "_has_current_claim_language",
        "_has_explicit_failure_wording",
    )
    for marker in banned_policy_markers:
        assert marker not in policy_source
        assert marker not in closure_source

    banned_hint_markers = (
        "freshness_route_rule",
        "freshness_live_data_rule",
        "freshness_sources_rule",
        "freshness_date_rule",
        "Do not answer from stale priors",
        "Use tool-backed live evidence",
    )
    for marker in banned_hint_markers:
        assert marker not in freshness_source
        assert marker not in closure_source


def test_final_answer_verifier_uses_structural_evidence_not_answer_prose() -> None:
    contract = FreshnessContract(
        intent="latest_market_news_and_stock_list",
        domain="finance",
        time_sensitive=True,
        needs_live_data=True,
        needs_sources=True,
        needs_exact_date=True,
        answer_mode="browse_then_answer",
        reason="Current finance claims require live evidence.",
        confidence=0.92,
    )
    obligations = FreshnessObligations(
        require_live_data=True,
        require_sources=True,
        require_exact_date=True,
        require_explicit_failure_wording=True,
        answer_mode="browse_then_answer",
    )

    reasons = verify_freshness_answer(
        contract=contract,
        obligations=obligations,
        answer="Latest market news is bullish today.",
        action_result=None,
    )

    assert reasons == [
        "Missing live-data evidence for a freshness-sensitive answer.",
        "Missing exact-date evidence for an exact-date freshness answer.",
    ]

    grounded_result = ActionResult(
        command_id="cmd-live",
        status="success",
        summary="Fetched current market data",
        outputs={
            "source_url": "https://example.com/markets",
            "observed_at": "2026-04-24",
        },
    )
    assert (
        verify_freshness_answer(
            contract=contract,
            obligations=obligations,
            answer="Latest market news is bullish today.",
            action_result=grounded_result,
        )
        == []
    )


def test_final_answer_verifier_blocks_exact_date_answers_without_dated_evidence() -> (
    None
):
    contract = FreshnessContract(
        intent="latest_market_news_and_stock_list",
        domain="finance",
        time_sensitive=True,
        needs_live_data=True,
        needs_sources=True,
        needs_exact_date=True,
        answer_mode="browse_then_answer",
        reason="Current finance claims require exact dated evidence.",
        confidence=0.92,
    )
    obligations = FreshnessObligations(
        require_live_data=True,
        require_sources=True,
        require_exact_date=True,
        require_explicit_failure_wording=True,
        answer_mode="browse_then_answer",
    )

    action_result = ActionResult(
        command_id="cmd-live",
        status="success",
        summary="Fetched current market data",
        outputs={"source_url": "https://example.com/markets"},
    )

    reasons = verify_freshness_answer(
        contract=contract,
        obligations=obligations,
        answer="Latest market news is bullish today.",
        action_result=action_result,
    )

    assert reasons == [
        "Missing exact-date evidence for an exact-date freshness answer."
    ]


def test_final_answer_verifier_accepts_nested_tool_result_dates() -> None:
    contract = FreshnessContract(
        intent="latest_market_news_and_stock_list",
        domain="finance",
        time_sensitive=True,
        needs_live_data=True,
        needs_sources=True,
        needs_exact_date=True,
        answer_mode="browse_then_answer",
        reason="Current finance claims require exact dated evidence.",
        confidence=0.92,
    )
    obligations = FreshnessObligations(
        require_live_data=True,
        require_sources=True,
        require_exact_date=True,
        require_explicit_failure_wording=True,
        answer_mode="browse_then_answer",
    )

    action_result = ActionResult(
        command_id="cmd-live",
        status="success",
        summary="Fetched current market data",
        outputs={
            "tool_results": [
                {
                    "tool_name": "web.search",
                    "data": {"query_time": "2026-05-07T08:58:00Z"},
                }
            ]
        },
    )

    assert (
        verify_freshness_answer(
            contract=contract,
            obligations=obligations,
            answer="Latest market news is bullish today.",
            action_result=action_result,
        )
        == []
    )


def test_final_close_message_blocks_unsupported_current_answer() -> None:
    state = _state()
    state.freshness_contract = FreshnessContract(
        intent="latest_market_news_and_stock_list",
        domain="finance",
        time_sensitive=True,
        needs_live_data=True,
        needs_sources=True,
        needs_exact_date=True,
        answer_mode="browse_then_answer",
        reason="Current finance claims require live evidence.",
        confidence=0.92,
    )
    state.freshness_obligations = FreshnessObligations(
        require_live_data=True,
        require_sources=True,
        require_exact_date=True,
        require_explicit_failure_wording=True,
        answer_mode="browse_then_answer",
    )
    state.freshness_diagnostics = FreshnessDiagnostics(
        classifier_mode="llm",
        classifier_model="test-model",
        classified_at="2026-04-24T12:00:00Z",
    )

    message = final_close_message(
        state=state,
        judgment=SimpleNamespace(final_answer="Latest market news is bullish today."),
        action_result=None,
        fallback_message="Completed.",
    )

    assert "grounded current finance answer" in message


def test_final_close_message_allows_structurally_grounded_answer() -> None:
    state = _state()
    state.freshness_contract = FreshnessContract(
        intent="latest_market_news_and_stock_list",
        domain="finance",
        time_sensitive=True,
        needs_live_data=True,
        needs_sources=True,
        needs_exact_date=True,
        answer_mode="browse_then_answer",
        reason="Current finance claims require live evidence.",
        confidence=0.92,
    )
    state.freshness_obligations = FreshnessObligations(
        require_live_data=True,
        require_sources=True,
        require_exact_date=True,
        require_explicit_failure_wording=True,
        answer_mode="browse_then_answer",
    )

    action_result = ActionResult(
        command_id="cmd-live",
        status="success",
        summary="Fetched current market data",
        outputs={
            "source_url": "https://example.com/markets",
            "observed_at": "2026-04-24",
        },
    )
    answer = "Latest market breadth is positive today."

    message = final_close_message(
        state=state,
        judgment=SimpleNamespace(final_answer=answer),
        action_result=action_result,
        fallback_message="Completed.",
    )

    assert message == answer
