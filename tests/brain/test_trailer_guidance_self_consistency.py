from __future__ import annotations


def _guidance_text() -> str:
    from openminion.modules.brain.adapters.llm.request import (
        _build_task_plan_guidance_message,
    )
    from openminion.modules.brain.schemas import DecisionAdapter

    return _build_task_plan_guidance_message(purpose="decide", schema=DecisionAdapter)


def test_guidance_is_non_empty_for_decide_decision() -> None:
    text = _guidance_text()
    assert text
    assert "plan loop-control tool" in text
    assert "route to act" in text


def test_guidance_does_not_teach_xml_task_plan_trailers() -> None:
    text = _guidance_text()
    assert "<task_plan>" not in text
    assert "<step_completed>" not in text
    assert "<plan_revision>" not in text
    assert "Do not emit task-plan XML trailers" in text


def test_guidance_names_all_plan_tool_actions() -> None:
    text = _guidance_text()
    for action in (
        "declare",
        "step_completed",
        "step_blocked",
        "revise",
        "abandon",
        "complete",
    ):
        assert action in text


def test_guidance_empty_outside_decision_decide_surface() -> None:
    from openminion.modules.brain.adapters.llm.request import (
        _build_task_plan_guidance_message,
    )
    from openminion.modules.brain.schemas import DecisionAdapter

    class NotDecision:
        pass

    assert (
        _build_task_plan_guidance_message(purpose="act", schema=DecisionAdapter) == ""
    )
    assert _build_task_plan_guidance_message(purpose="decide", schema=NotDecision) == ""
