from __future__ import annotations

import importlib
from typing import NamedTuple

import pytest


_LIFECYCLE_TARGET = (
    "tests.test_conversation_lifecycle",
    "ConversationLifecycleTests",
)
_GATEWAY_TARGET = (
    "tests.services.gateway.test_gateway_service",
    "GatewayServiceCoreTests",
)


class SSCRCase(NamedTuple):
    case_id: str
    scenario: str
    expected: str
    # ``(module_path, class_name)`` locating the test class on which the
    # covering test functions live.
    test_target: tuple[str, str]
    # Test function names that cover this case end-to-end. Empty tuple
    # means this case has no end-to-end coverage today (tracked as a
    # here).
    covering_tests: tuple[str, ...]


# "Validation Matrix (SSCR-C)" section; drift between the two is
# itself a finding.
SSCR_MATRIX: tuple[SSCRCase, ...] = (
    SSCRCase(
        case_id="SSCR-C01",
        scenario="same session + same conversation resume",
        expected="prior context recalled",
        test_target=_LIFECYCLE_TARGET,
        covering_tests=(
            "test_routing_decision_resumes_for_explicit_thread",
            "test_routing_decision_prefers_replay_for_undelivered",
        ),
    ),
    SSCRCase(
        case_id="SSCR-C02",
        scenario="same session + explicit new conversation",
        expected="no stale carry-over",
        test_target=_LIFECYCLE_TARGET,
        covering_tests=("test_routing_decision_forks_settled_without_resume",),
    ),
    SSCRCase(
        case_id="SSCR-C03",
        scenario="restart during clarify/waiting state",
        expected="deterministic resume/reset decision",
        # Partial: `test_running_is_awaiting` covers the awaiting-state
        # resolver output, but the explicit restart-during-clarify
        test_target=_LIFECYCLE_TARGET,
        covering_tests=("test_running_is_awaiting",),
    ),
    SSCRCase(
        case_id="SSCR-C04",
        scenario="attach conflict (writer/observer)",
        expected="explicit role denial/allow metadata",
        test_target=_LIFECYCLE_TARGET,
        covering_tests=("test_writer_attach_role_ignores_observer",),
    ),
    SSCRCase(
        case_id="SSCR-C05",
        scenario="undelivered replay path",
        expected="replay only when lifecycle indicates pending outbound",
        test_target=_LIFECYCLE_TARGET,
        covering_tests=(
            "test_routing_decision_prefers_replay_for_undelivered",
            "test_delivery_state_ack_trumps_delivery",
            "test_cancel_requested_qualifier_on_undelivered_completion",
            "test_detached_qualifier_on_undelivered_completion",
        ),
    ),
    SSCRCase(
        case_id="SSCR-C06",
        scenario="--reset-session",
        expected="guaranteed fresh thread + conversation",
        test_target=_GATEWAY_TARGET,
        covering_tests=("test_gateway_reset_session_forks_thread_and_clears_history",),
    ),
)


def _load_test_class(module_path: str, class_name: str) -> object:

    module = importlib.import_module(module_path)
    test_class = getattr(module, class_name, None)
    if test_class is None:
        pytest.fail(
            f"SSCR-01: {module_path}.{class_name} is required for SSCR-C "
            "coverage but was not found. See the SSCR tracker's Current "
            "Live State table."
        )
    return test_class


@pytest.mark.parametrize("case", SSCR_MATRIX, ids=[c.case_id for c in SSCR_MATRIX])
def test_sscr_case_has_documented_coverage_shape(case: SSCRCase) -> None:

    assert case.case_id.startswith("SSCR-C"), (
        f"SSCR-01: case id {case.case_id!r} does not follow the SSCR-C pattern."
    )
    assert case.scenario, f"SSCR-01: {case.case_id} has empty scenario."
    assert case.expected, f"SSCR-01: {case.case_id} has empty expected outcome."
    assert isinstance(case.test_target, tuple) and len(case.test_target) == 2, (
        f"SSCR-01: {case.case_id}.test_target must be a (module, class) 2-tuple."
    )
    module_path, class_name = case.test_target
    assert isinstance(module_path, str) and module_path, (
        f"SSCR-01: {case.case_id}.test_target[0] must be a non-empty module path."
    )
    assert isinstance(class_name, str) and class_name, (
        f"SSCR-01: {case.case_id}.test_target[1] must be a non-empty class name."
    )
    assert isinstance(case.covering_tests, tuple), (
        f"SSCR-01: {case.case_id}.covering_tests must be a tuple."
    )


@pytest.mark.parametrize(
    "case",
    [c for c in SSCR_MATRIX if c.covering_tests],
    ids=[c.case_id for c in SSCR_MATRIX if c.covering_tests],
)
def test_sscr_case_covering_tests_exist_on_target_class(case: SSCRCase) -> None:

    module_path, class_name = case.test_target
    test_class = _load_test_class(module_path, class_name)
    for test_name in case.covering_tests:
        assert hasattr(test_class, test_name), (
            f"SSCR-01: {case.case_id} claims coverage by "
            f"{module_path}.{class_name}.{test_name} but that test no "
            "longer exists. Either update the SSCR_MATRIX covering_tests "
            "tuple or restore the test. Do not silently drop the claim."
        )
        assert callable(getattr(test_class, test_name)), (
            f"SSCR-01: {case.case_id} claims coverage by "
            f"{module_path}.{class_name}.{test_name} but that attribute "
            "is not callable."
        )


def test_sscr_matrix_has_no_uncovered_cases() -> None:

    uncovered = [case.case_id for case in SSCR_MATRIX if not case.covering_tests]
    assert uncovered == [], (
        "SSCR-01: the following SSCR-C cases have empty covering_tests "
        f"tuples: {uncovered}. Either add a covering test and update the "
        "tuple, or open a new SSCR residual task and explicitly amend "
        "this test to exempt the new case. Do not leave uncovered cases "
        "in the matrix silently."
    )
