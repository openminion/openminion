from __future__ import annotations

import json

import pytest

from openminion.services.agent.execution.finalization import (
    FINAL_ANSWER_ENVELOPE_ALLOWED_STATUS,
    FINAL_ANSWER_ENVELOPE_REQUIRED_KEYS,
    unwrap_final_answer_envelope,
)


def _make_envelope(status: str, summary: str, output: str) -> str:
    return json.dumps({"status": status, "summary": summary, "output": output})


def test_unwrap_final_answer_envelope_returns_output_for_exact_schema() -> None:
    body = _make_envelope(
        status="final_answer",
        summary="Resolved location-based weather lookup with stored default.",
        output="It is 68°F and partly cloudy in Austin today.",
    )

    result = unwrap_final_answer_envelope(body)

    assert result is not None
    output_text, payload = result
    assert output_text == "It is 68°F and partly cloudy in Austin today."
    assert payload == {
        "status": "final_answer",
        "summary": "Resolved location-based weather lookup with stored default.",
        "output": "It is 68°F and partly cloudy in Austin today.",
    }


@pytest.mark.parametrize("status", sorted(FINAL_ANSWER_ENVELOPE_ALLOWED_STATUS))
def test_unwrap_final_answer_envelope_accepts_each_allowed_status(
    status: str,
) -> None:
    body = _make_envelope(status=status, summary="ok", output="answer body")

    result = unwrap_final_answer_envelope(body)

    assert result is not None
    output_text, payload = result
    assert output_text == "answer body"
    assert payload["status"] == status


def test_unwrap_final_answer_envelope_preserves_nonmatching_json() -> None:
    assert unwrap_final_answer_envelope("It is 68°F in Austin.") is None
    assert unwrap_final_answer_envelope('["final_answer", "summary"]') is None
    assert unwrap_final_answer_envelope('"final_answer"') is None
    assert (
        unwrap_final_answer_envelope(
            'Sure! {"status":"final_answer","summary":"x","output":"y"}'
        )
        is None
    )


def test_unwrap_final_answer_envelope_preserves_matching_status_with_empty_output() -> (
    None
):
    body = json.dumps({"status": "final_answer", "summary": "ok", "output": "   "})

    assert unwrap_final_answer_envelope(body) is None


def test_unwrap_final_answer_envelope_preserves_extra_keys() -> None:
    body = json.dumps(
        {
            "status": "final_answer",
            "summary": "ok",
            "output": "answer",
            "tool_calls": [],
        }
    )

    assert unwrap_final_answer_envelope(body) is None


def test_unwrap_final_answer_envelope_preserves_missing_keys() -> None:
    body = json.dumps({"status": "final_answer", "output": "answer"})

    assert unwrap_final_answer_envelope(body) is None


def test_unwrap_final_answer_envelope_preserves_unknown_status() -> None:
    body = _make_envelope(status="needs_clarification", summary="ok", output="answer")

    assert unwrap_final_answer_envelope(body) is None


def test_unwrap_final_answer_envelope_rejects_non_string_fields() -> None:
    body = json.dumps({"status": "final_answer", "summary": 42, "output": "answer"})
    assert unwrap_final_answer_envelope(body) is None

    body = json.dumps(
        {
            "status": "final_answer",
            "summary": "ok",
            "output": {"nested": "object"},
        }
    )
    assert unwrap_final_answer_envelope(body) is None


def test_unwrap_final_answer_envelope_handles_whitespace_padding() -> None:
    # Leading/trailing whitespace around the envelope must still unwrap; the
    # CLI surface should never see leaked envelopes regardless of padding.
    body = (
        "\n  "
        + _make_envelope(
            status="incomplete",
            summary="Awaiting tool data.",
            output="Still working on the weather lookup.",
        )
        + "  \n"
    )

    result = unwrap_final_answer_envelope(body)

    assert result is not None
    output_text, payload = result
    assert output_text == "Still working on the weather lookup."
    assert payload["status"] == "incomplete"


@pytest.mark.parametrize(
    "body",
    (
        '<respond({"answer": "Groq smoke OK", "freshness": {"answer_mode": "local_only"}})',
        '<respond({"answer": "Groq smoke OK", "summary": "completed"})>',
        '<respond>{"answer": "Groq smoke OK", "freshness": {"answer_mode": "local_only"}}</respond>',
    ),
)
def test_unwrap_final_answer_envelope_accepts_respond_wrapper(body: str) -> None:
    result = unwrap_final_answer_envelope(body)

    assert result is not None
    output_text, payload = result
    assert output_text == "Groq smoke OK"
    assert payload["status"] == "final_answer"
    assert payload["output"] == "Groq smoke OK"


def test_unwrap_final_answer_envelope_preserves_non_exact_respond_wrapper() -> None:
    assert (
        unwrap_final_answer_envelope(
            'Sure: <respond({"answer": "Groq smoke OK"})>'
        )
        is None
    )
    assert unwrap_final_answer_envelope("<respond({not-json})>") is None
    assert unwrap_final_answer_envelope('<respond({"answer": ""})>') is None


def test_unwrap_final_answer_envelope_returns_none_for_empty_text() -> None:
    assert unwrap_final_answer_envelope("") is None
    assert unwrap_final_answer_envelope("   ") is None


def test_unwrap_final_answer_envelope_schema_constants_are_frozensets() -> None:
    # Pin the structural invariants so future edits cannot quietly turn these
    # into mutable sets or expand the status vocabulary by accident.
    assert isinstance(FINAL_ANSWER_ENVELOPE_REQUIRED_KEYS, frozenset)
    assert FINAL_ANSWER_ENVELOPE_REQUIRED_KEYS == frozenset(
        {"status", "summary", "output"}
    )
    assert isinstance(FINAL_ANSWER_ENVELOPE_ALLOWED_STATUS, frozenset)
    assert FINAL_ANSWER_ENVELOPE_ALLOWED_STATUS == frozenset(
        {"final_answer", "incomplete", "blocked"}
    )
