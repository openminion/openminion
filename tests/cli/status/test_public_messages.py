from typing import get_args

import pytest

from openminion.cli.status.public_messages import (
    DETAIL_MESSAGES_EN,
    STATUS_MESSAGES_EN,
    format_public_status_text,
)
from openminion.modules.brain.diagnostics.status import PhaseStatus, StatusKey


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
