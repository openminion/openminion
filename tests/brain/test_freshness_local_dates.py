"""Local policy chronology must not require live freshness metadata."""

from types import SimpleNamespace

import pytest

from openminion.modules.brain.bootstrap.freshness_classify import (
    build_freshness_hints,
    map_freshness_obligations,
)
from openminion.modules.brain.execution.closure import final_close_message
from openminion.modules.brain.execution.runtime.closure.evaluator import (
    _apply_freshness_guard,
)
from openminion.modules.brain.runtime.verification.policy import verify_freshness_answer
from openminion.modules.brain.schemas import ActionResult, FreshnessContract
from openminion.modules.brain.schemas.closure import ClosureJudgment


def _contract(*, live: bool = False) -> FreshnessContract:
    return FreshnessContract(
        domain="regulation",
        time_sensitive=False,
        needs_sources=True,
        needs_exact_date=True,
        needs_live_data=live,
        answer_mode="local_only",
    )


@pytest.mark.parametrize("request_date", ["2026-02-01", "2026-11-11", "2026-12-05"])
@pytest.mark.parametrize("date_metadata", [None, "2026-01-01", "2026-12-01"])
def test_local_as_of_answer_is_delivered_without_live_date_metadata(
    request_date: str, date_metadata: str | None
) -> None:
    contract = _contract()
    obligations = map_freshness_obligations(contract)
    data = {"content": "Effective 2026-01-01. Routing: OPS."}
    if date_metadata is not None:
        data["evidence_date"] = date_metadata
    result = ActionResult(
        command_id="read-policy",
        status="success",
        outputs={"tool_results": [{"tool_name": "file.read", "data": data}]},
    )
    answer = f"As of {request_date}, routing is OPS [policy.md]."
    state = SimpleNamespace(
        freshness_contract=contract,
        freshness_obligations=obligations,
        freshness_diagnostics=None,
    )

    assert contract.time_sensitive
    assert obligations.require_exact_date
    assert not obligations.require_live_data
    assert (
        final_close_message(
            state=state,
            judgment=ClosureJudgment(
                satisfied=True, next_action="close", final_answer=answer
            ),
            action_result=result,
            fallback_message="Completed.",
        )
        == answer
    )


@pytest.mark.parametrize(
    "answer",
    [
        "The effective date is absent; I cannot determine the applicable routing.",
        "The date 01/02/2026 is ambiguous; please confirm its format.",
        "Two amendments conflict on routing with no precedence; I cannot resolve it.",
        "As of 2026-12-05, routing changes to STOCK; approvals and retention remain.",
    ],
)
def test_local_freshness_guard_preserves_semantic_judgment(answer: str) -> None:
    contract = _contract()
    state = SimpleNamespace(
        freshness_contract=contract,
        freshness_obligations=map_freshness_obligations(contract),
        freshness_diagnostics=None,
    )
    judgment = ClosureJudgment(
        satisfied=False,
        next_action="continue",
        reason="needs_review",
        final_answer=answer,
    )

    _apply_freshness_guard(state=state, action_result=None, judgment=judgment)

    assert not judgment.satisfied
    assert judgment.next_action == "continue"
    assert judgment.reason == "needs_review"
    assert judgment.final_answer == answer


@pytest.mark.parametrize("outputs", [{}, {"content": "Effective 2026-01-01."}])
def test_explicit_live_contract_still_requires_structured_dates(outputs: dict) -> None:
    contract = _contract(live=True)
    result = ActionResult(command_id="lookup", status="success", outputs=outputs)

    reasons = verify_freshness_answer(
        contract=contract,
        obligations=map_freshness_obligations(contract),
        answer="As of 2026-11-11, routing is OPS [policy.md].",
        action_result=result,
    )

    assert "Missing exact-date evidence for an exact-date freshness answer." in reasons


@pytest.mark.parametrize("live", [False, True])
def test_date_hints_distinguish_requested_chronology_from_live_time(live: bool) -> None:
    contract = _contract(live=live)
    hints = build_freshness_hints(
        contract=contract, obligations=map_freshness_obligations(contract)
    )
    hint = hints["style_overrides"]["freshness_exact_date_contract"]

    if live:
        assert "current_datetime" in hint
        assert "evidence_date" in hint
    else:
        assert "requested date" in hint
        assert "effective dates" in hint
        assert "current_datetime" not in hint
