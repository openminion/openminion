from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from openminion.modules.brain.constants import BRAIN_FRESHNESS_POLICY_CONSTRAINT
from openminion.modules.brain.runner import BrainRunner
from openminion.modules.brain.schemas import BudgetCounters, WorkingState
from openminion.modules.llm.schemas import LLMResponse, Message, ToolCall, UsageInfo


def _retired_symbol(*parts: str) -> str:
    return "".join(parts)


def _runner_with_llm_result(result: dict[str, object]) -> BrainRunner:
    llm_api = SimpleNamespace(
        call=MagicMock(return_value=_llm_response_for_result(result)),
        estimate_tokens=MagicMock(return_value=1),
    )
    context_api = SimpleNamespace(build=MagicMock(return_value={}))
    profile = SimpleNamespace(
        agent_id="freshness-guard-agent",
        llm_profiles=SimpleNamespace(decide_model="test-model"),
        defaults=SimpleNamespace(
            auto_save_lessons=False, auto_stage_policy_candidates=False
        ),
        budgets=SimpleNamespace(
            max_ticks_per_user_turn=10,
            max_tool_calls=5,
            max_a2a_calls=3,
            max_total_llm_tokens=1000,
            max_elapsed_ms=60000,
        ),
    )
    return BrainRunner(
        profile=profile,
        session_api=MagicMock(),
        llm_api=llm_api,
        context_api=context_api,
        tool_api=SimpleNamespace(registry=SimpleNamespace(_tools={})),
    )


def _llm_response_for_result(result: dict[str, object]) -> LLMResponse:
    respond_kind = str(result.get("respond_kind", "") or "").strip()
    if respond_kind == "clarify":
        question = str(result.get("question", "") or "").strip()
        return LLMResponse(
            ok=True,
            provider="test",
            model="test-model",
            tool_calls=[
                ToolCall(
                    id="clarify-1",
                    name="clarify",
                    arguments={"question": question},
                    status="requested",
                )
            ],
            usage=UsageInfo(input_tokens=1, output_tokens=1, total_tokens=2),
            finish_reason="tool_calls",
            provider_raw={},
            telemetry={},
        )
    answer = str(result.get("answer", "") or "").strip()
    return LLMResponse(
        ok=True,
        provider="test",
        model="test-model",
        output_text=answer,
        assistant_messages=[Message(role="assistant", content=answer)],
        usage=UsageInfo(input_tokens=1, output_tokens=1, total_tokens=2),
        finish_reason="stop",
        provider_raw={},
        telemetry={},
    )


def _state() -> WorkingState:
    return WorkingState(
        session_id="freshness-guard-session",
        agent_id="freshness-guard-agent",
        budgets_remaining=BudgetCounters(
            ticks=10,
            tool_calls=5,
            a2a_calls=3,
            tokens=1000,
            time_ms=60000,
        ),
    )


def test_decide_does_not_keyword_gate_latest_news_prompt() -> None:
    runner = _runner_with_llm_result(
        {
            "mode": "respond",
            "confidence": 0.8,
            "reason_code": "llm_answer",
            "respond_kind": "answer",
            "answer": "Here is a model-routed answer.",
        }
    )

    with (
        patch.object(runner, "_estimate_tokens", return_value=1),
        patch.object(runner, "_debit_tokens", return_value=None),
    ):
        decision = runner._decide(
            state=_state(),
            user_input="check latest news on iran and summarize briefly",
            logger=MagicMock(),
        )

    assert decision.mode == "respond"
    assert decision.respond_kind == "answer"
    assert decision.reason_code == "entry_text_response"
    assert decision.answer == "Here is a model-routed answer."


def test_decide_does_not_keyword_gate_today_prompt() -> None:
    runner = _runner_with_llm_result(
        {
            "mode": "respond",
            "confidence": 0.7,
            "reason_code": "fallback_unstructured_response",
            "respond_kind": "answer",
            "answer": "The model answered without a runtime keyword gate.",
        }
    )

    with (
        patch.object(runner, "_estimate_tokens", return_value=1),
        patch.object(runner, "_debit_tokens", return_value=None),
    ):
        decision = runner._decide(
            state=_state(),
            user_input="what happened today in tech?",
            logger=MagicMock(),
        )

    assert decision.mode == "respond"
    assert decision.respond_kind == "answer"
    assert decision.reason_code == "entry_text_response"
    assert "runtime keyword gate" in str(decision.answer or "")


def test_freshness_policy_constraint_constant_is_still_available() -> None:
    assert isinstance(BRAIN_FRESHNESS_POLICY_CONSTRAINT, str)
    assert "FRESHNESS_POLICY" in BRAIN_FRESHNESS_POLICY_CONSTRAINT


def test_decision_source_no_longer_contains_runtime_freshness_gate() -> None:
    # phases/decision.py was split into a phases/decision/ package.
    decision_pkg = Path("src/openminion/modules/brain/phases/decision")
    sources_combined = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(decision_pkg.rglob("*.py"))
    )
    retired_reason_code = _retired_symbol(
        "freshness", "_required", "_no_tool", "_called"
    )
    retired_event_name = _retired_symbol(
        "brain", ".", "decide", ".", "freshness_gate_triggered"
    )
    assert retired_reason_code not in sources_combined
    assert retired_event_name not in sources_combined


def test_execution_source_no_longer_contains_freshness_keyword_helper() -> None:
    retired_helper_name = _retired_symbol("is", "_freshness", "_sensitive", "_request")
    execution_root = Path("src/openminion/modules/brain/execution")
    sources = [
        path.read_text(encoding="utf-8") for path in execution_root.rglob("*.py")
    ]
    assert sources
    assert all(retired_helper_name not in source for source in sources)
