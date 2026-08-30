from typing import get_args

import pytest

from openminion.cli.status.public_messages import (
    DETAIL_MESSAGES_EN,
    STATUS_MESSAGES_EN,
    format_public_status_text,
)
from openminion.modules.brain.diagnostics.status import (
    PHASE_STATUS_SCHEMA_VERSION,
    PhaseStatus,
    StatusKey,
    coerce_phase_status,
    phase_status_client_facts,
)


def test_primary_catalog_covers_every_status_key() -> None:
    assert set(STATUS_MESSAGES_EN) == set(get_args(StatusKey))


@pytest.mark.parametrize(
    ("detail_code", "expected"),
    [
        ("preparing_turn", "Getting ready..."),
        ("loading_memory_context", "Loading relevant context..."),
        ("loading_session_history", "Reviewing this conversation..."),
        ("thinking", "Thinking..."),
        ("composing_answer", "Preparing the answer..."),
    ],
)
def test_known_detail_code_uses_public_message(
    detail_code: str,
    expected: str,
) -> None:
    status = PhaseStatus(
        trace_id=detail_code,
        status_key="working",
        label="raw technical label",
        detail_code=detail_code,
        detail_text="raw internal detail",
    )
    assert format_public_status_text(status) == expected


def test_plan_checkpoint_uses_named_step_values() -> None:
    status = PhaseStatus(
        trace_id="plan",
        status_key="executing",
        label="raw",
        detail_code="plan_checkpoint",
        step_index=2,
        step_total=4,
    )
    assert format_public_status_text(status) == "Finished step 2 of 4."


@pytest.mark.parametrize(
    ("step_index", "step_total"),
    [(None, 4), (0, 4), (5, 4), (1, 0)],
)
def test_invalid_plan_checkpoint_uses_primary_fallback(
    step_index: int | None,
    step_total: int,
) -> None:
    status = PhaseStatus(
        trace_id="invalid-plan",
        status_key="executing",
        label="raw",
        detail_code="plan_checkpoint",
        step_index=step_index,
        step_total=step_total,
    )
    assert format_public_status_text(status) == "Working on it..."


def test_terminal_state_wins_over_detail_and_tool() -> None:
    status = PhaseStatus(
        trace_id="approval",
        status_key="awaiting_confirmation",
        label="raw",
        detail_code="thinking",
        tool_name="exec.run",
    )
    assert format_public_status_text(status) == "Waiting for your approval..."


def test_unknown_detail_and_tool_do_not_leak_raw_values() -> None:
    status = PhaseStatus(
        trace_id="unknown",
        status_key="executing",
        label="raw-label",
        detail_code="private_internal_phase",
        detail_text="private internal detail",
        tool_name="provider.private.command",
    )
    rendered = format_public_status_text(status)
    assert rendered == "Using a tool..."
    assert "private" not in rendered


def test_detail_catalog_is_the_bounded_v1_set() -> None:
    assert set(DETAIL_MESSAGES_EN) == {
        "plan_checkpoint",
        "preparing_turn",
        "loading_memory_context",
        "loading_session_history",
        "thinking",
        "composing_answer",
    }


def test_client_facts_are_language_neutral_and_versioned() -> None:
    facts = phase_status_client_facts(
        PhaseStatus(
            trace_id="client-facts",
            status_key="executing",
            label="Executing step 2 of 4...",
            mode_label="Coding mode",
            detail_text="private diagnostic text",
            detail_code="plan_checkpoint",
            step_index=2,
            step_total=4,
            tool_name="exec.run",
        )
    )

    assert facts["schema_version"] == PHASE_STATUS_SCHEMA_VERSION
    assert facts["status_key"] == "executing"
    assert facts["detail_code"] == "plan_checkpoint"
    assert facts["step_index"] == 2
    assert facts["tool_name"] == "exec.run"
    assert "label" not in facts
    assert "mode_label" not in facts
    assert "detail_text" not in facts


def test_unknown_status_key_preserves_structured_facts() -> None:
    status = coerce_phase_status(
        {
            "trace_id": "future-status",
            "status_key": "future_state",
            "label": "Future state",
            "step_index": 2,
            "step_total": 4,
            "tool_name": "exec.run",
            "detail_code": "plan_checkpoint",
        }
    )

    assert status.status_key == "working"
    assert status.step_index == 2
    assert status.step_total == 4
    assert status.tool_name == "exec.run"
    assert status.detail_code == "plan_checkpoint"
