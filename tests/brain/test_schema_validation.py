from openminion.modules.brain.schemas import (
    BrainMode,
    ClarifyPolicy,
    ClarifyQuestion,
    ClarifyRequest,
    ClarifyResponse,
)


def _assert_enum_values(enum_type, values: tuple[str, ...]) -> None:
    for value in values:
        assert enum_type(value).value == value


def test_brain_modes_enum_values() -> None:
    _assert_enum_values(BrainMode, ("command", "guided", "autonomous", "batch"))


def test_clarify_policies_enum_values() -> None:
    _assert_enum_values(
        ClarifyPolicy,
        (
            "always_ask",
            "ask_if_ambiguous",
            "ask_if_risky",
            "assume_defaults",
            "smart_assume",
        ),
    )


def test_clarify_question_creation() -> None:
    question = ClarifyQuestion(
        type="ambiguous_input",
        question="What is the target environment?",
        default_value="production",
        is_blocking=False,
    )

    assert question.id is not None
    assert question.type == "ambiguous_input"
    assert question.question == "What is the target environment?"
    assert question.default_value == "production"
    assert not question.is_blocking


def test_clarify_request_creation() -> None:
    question = ClarifyQuestion(
        type="missing_field", question="Which environment should I deploy to?"
    )

    request = ClarifyRequest(
        session_id="test_session_123",
        trace_id="test_trace_456",
        questions=[question],
        mode="guided",
        policy="ask_if_ambiguous",
        reason="Deployment target not specified",
    )

    assert request.session_id == "test_session_123"
    assert request.trace_id == "test_trace_456"
    assert len(request.questions) == 1
    assert request.mode == "guided"
    assert request.policy == "ask_if_ambiguous"
    assert request.reason == "Deployment target not specified"


def test_clarify_response_creation() -> None:
    response = ClarifyResponse(
        session_id="test_session_123",
        trace_id="test_trace_456",
        answers={"q1": "production"},
        unanswered_ids=["q2", "q3"],
    )

    assert response.session_id == "test_session_123"
    assert response.answers["q1"] == "production"
    assert "q2" in response.unanswered_ids
    assert "q3" in response.unanswered_ids
