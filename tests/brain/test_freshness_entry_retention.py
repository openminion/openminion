"""`_apply_entry_freshness` on the entry steps of one turn."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import openminion.modules.brain.loop.orchestration as orchestration
from openminion.modules.brain.bootstrap.freshness_classify import (
    map_freshness_obligations,
)
from openminion.modules.brain.schemas import (
    BudgetCounters,
    FreshnessContract,
    FreshnessDiagnostics,
    WorkingState,
)

EXACT_DATE = {
    "domain": "shopping",
    "time_sensitive": True,
    "needs_live_data": True,
    "needs_sources": True,
    "needs_exact_date": True,
    "answer_mode": "browse_then_answer",
}
LIVE_NO_DATE = {**EXACT_DATE, "needs_exact_date": False}


class RecordingLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def emit(self, event_type: str, payload: dict[str, Any], **_: Any) -> None:
        self.events.append((event_type, dict(payload)))

    def types(self) -> list[str]:
        return [event_type for event_type, _ in self.events]

    def fallback_reasons(self) -> list[str]:
        return [
            payload["reason"]
            for event_type, payload in self.events
            if event_type == "brain.freshness.entry_fallback"
        ]


def _runner() -> SimpleNamespace:
    return SimpleNamespace(
        llm_api=object(),
        profile=SimpleNamespace(
            llm_profiles=SimpleNamespace(decide_model="test-model", act_model="")
        ),
    )


def _state(contract: dict[str, Any] | None = None) -> WorkingState:
    state = WorkingState(
        session_id="freshness-session",
        agent_id="freshness-agent",
        trace_id="trace-freshness",
        goal="Check order A-104",
        budgets_remaining=BudgetCounters(
            ticks=10, tool_calls=5, a2a_calls=2, tokens=2000, time_ms=60000
        ),
    )
    if contract is not None:
        typed = FreshnessContract.model_validate(
            {**contract, "intent": "order_status_inquiry"}
        )
        state.freshness_contract = typed
        state.freshness_obligations = map_freshness_obligations(typed)
        state.freshness_diagnostics = FreshnessDiagnostics(
            classifier_mode="llm",
            classifier_model="test-model",
            classified_at="2026-10-25T10:00:00+00:00",
            notes=["classified from the turn's user input"],
        )
    return state


def _text_response() -> SimpleNamespace:
    return SimpleNamespace(output_text="Order A-104 is ready.", tool_calls=[])


def _tool_response(arguments: dict[str, Any]) -> SimpleNamespace:
    call = SimpleNamespace(name="lookup_order", arguments=dict(arguments))
    return SimpleNamespace(output_text="", tool_calls=[call])


def _apply(
    state: WorkingState, response: Any, user_input: str | None
) -> RecordingLogger:
    logger = RecordingLogger()
    orchestration._apply_entry_freshness(
        runner=_runner(),
        state=state,
        logger=logger,  # type: ignore[arg-type]
        response=response,
        user_input=user_input,
        model="test-model",
        llm_call_id="entry-call",
    )
    return logger


@pytest.fixture
def classifier(monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
    """Record the inputs the fallback classifier gets.

    Empty input goes to the real classifier, which answers the default without
    a model call; any other input is answered with LIVE_NO_DATE.
    """
    inputs: list[str | None] = []
    real = orchestration.classify_request_freshness

    def classify(runner: Any, *, state: Any, user_input: Any, logger: Any) -> Any:
        inputs.append(user_input)
        if not str(user_input or "").strip():
            return real(runner, state=state, user_input=user_input, logger=logger)
        contract = FreshnessContract.model_validate(LIVE_NO_DATE)
        return (
            contract,
            map_freshness_obligations(contract),
            FreshnessDiagnostics(classifier_mode="llm", classifier_model="test-model"),
        )

    monkeypatch.setattr(orchestration, "classify_request_freshness", classify)
    return inputs


@pytest.mark.parametrize("user_input", [None, "", " \n\t"])
@pytest.mark.parametrize(
    "make_response",
    [
        _text_response,
        lambda: _tool_response({"order_id": "A-104"}),
        lambda: _tool_response({"order_id": "A-104", "freshness": {"domain": "nope"}}),
    ],
    ids=["text", "tool call without a contract", "tool call with an invalid contract"],
)
def test_step_without_input_or_contract_keeps_the_turn_contract(
    classifier: list[str | None], user_input: str | None, make_response: Any
) -> None:
    response = make_response()
    state = _state(EXACT_DATE)
    contract = state.freshness_contract
    obligations = state.freshness_obligations
    diagnostics = state.freshness_diagnostics
    before = (
        contract.model_dump(),  # type: ignore[union-attr]
        obligations.model_dump(),  # type: ignore[union-attr]
        diagnostics.model_dump(),  # type: ignore[union-attr]
    )

    logger = _apply(state, response, user_input)

    assert state.freshness_contract is contract
    assert state.freshness_obligations is obligations
    assert state.freshness_diagnostics is diagnostics
    assert (
        contract.model_dump(),
        obligations.model_dump(),
        diagnostics.model_dump(),
    ) == before  # type: ignore[union-attr]
    assert obligations.require_exact_date is True  # type: ignore[union-attr]
    assert classifier == []
    assert logger.types() == ["brain.freshness.entry_fallback"]
    assert logger.fallback_reasons() == ["retained_turn_contract"]


def test_valid_typed_contract_replaces_the_turn_contract(
    classifier: list[str | None],
) -> None:
    state = _state(EXACT_DATE)

    logger = _apply(
        state,
        _tool_response({"order_id": "A-104", "freshness": dict(LIVE_NO_DATE)}),
        None,
    )

    assert state.freshness_contract is not None
    assert state.freshness_contract.needs_exact_date is False
    assert state.freshness_obligations is not None
    assert state.freshness_obligations.require_exact_date is False
    assert state.freshness_diagnostics is not None
    assert state.freshness_diagnostics.classifier_mode == "entry_contract"
    assert classifier == []
    assert logger.types() == ["brain.freshness.classified"]


def test_new_input_is_classified_even_when_the_turn_has_a_contract(
    classifier: list[str | None],
) -> None:
    state = _state(EXACT_DATE)

    logger = _apply(state, _text_response(), "Check order B-205")

    assert classifier == ["Check order B-205"]
    assert state.freshness_contract is not None
    assert state.freshness_contract.needs_exact_date is False
    assert logger.fallback_reasons() == ["missing_or_invalid_contract"]
    assert "brain.freshness.classified" in logger.types()


def test_first_empty_step_without_a_contract_keeps_the_empty_input_default() -> None:
    state = _state()

    logger = _apply(state, _text_response(), None)

    assert state.freshness_contract == FreshnessContract()
    assert state.freshness_obligations is not None
    assert state.freshness_obligations.require_exact_date is False
    assert state.freshness_diagnostics is not None
    assert state.freshness_diagnostics.classifier_mode == "skipped_empty_request"
    assert logger.fallback_reasons() == ["missing_or_invalid_contract"]
    assert "brain.freshness.classified" in logger.types()
