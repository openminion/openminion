from __future__ import annotations

from openminion.services.gateway.turn.route_classification import (
    classify_setup_cost_route,
)


def test_route_classifier_marks_short_plain_prompt_as_no_tool_answer():
    route = classify_setup_cost_route(message="say hi in one sentence")

    assert route.label == "no_tool_answer"
    assert route.reason == "short_plain_prompt"


def test_route_classifier_marks_multiple_forced_tools_as_multi_tool_request():
    route = classify_setup_cost_route(
        message="check this",
        forced_tools=["host.metrics", "web.search"],
    )

    assert route.label == "multi_tool_request"


def test_route_classifier_marks_file_context_without_changing_behavior():
    route = classify_setup_cost_route(message="summarize @src/openminion/api/runtime.py")

    assert route.label == "file_context_request"


def test_route_classifier_marks_file_edit_as_code_edit():
    route = classify_setup_cost_route(message="fix src/openminion/api/runtime.py")

    assert route.label == "code_edit_request"


def test_route_classifier_marks_research_category_without_text_guessing():
    route = classify_setup_cost_route(
        message="compare options",
        capability_category="research",
    )

    assert route.label == "research_request"
    assert route.reason == "capability_category"


def test_route_classifier_marks_local_status_request():
    route = classify_setup_cost_route(message="what is my disk usage?")

    assert route.label == "local_status_request"


def test_route_classifier_falls_back_to_ambiguous_for_unclear_long_prompt():
    route = classify_setup_cost_route(
        message=(
            "I have a broad idea with several possible directions and I want you "
            "to decide how to proceed after thinking through the tradeoffs."
        )
    )

    assert route.label == "ambiguous_request"
