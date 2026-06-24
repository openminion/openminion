from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openminion.base.constants import STATE_KEY_FINALIZATION_STATUS
from openminion.services.agent.execution.required_lane.post_execution import (
    _is_empty_provider_response,
)


@dataclass
class _FakeResponse:
    text: str = ""
    tool_calls: list[Any] = field(default_factory=list)
    # Production code sets this sidecar via setattr.
    finalization_status: Any = None


def _with_finalization(response: _FakeResponse, payload: Any) -> _FakeResponse:
    setattr(response, STATE_KEY_FINALIZATION_STATUS, payload)
    return response


def test_empty_text_no_tool_calls_no_finalization_is_empty():
    response = _FakeResponse(text="", tool_calls=[])
    assert _is_empty_provider_response(response) is True


def test_whitespace_only_text_treated_as_empty():
    response = _FakeResponse(text="   \n\t  ", tool_calls=[])
    assert _is_empty_provider_response(response) is True


def test_non_empty_text_is_not_empty():
    response = _FakeResponse(text="here is your answer", tool_calls=[])
    assert _is_empty_provider_response(response) is False


def test_tool_calls_present_is_not_empty():
    response = _FakeResponse(text="", tool_calls=[object()])
    assert _is_empty_provider_response(response) is False


def test_finalization_status_payload_is_not_empty():
    response = _FakeResponse(text="", tool_calls=[])
    _with_finalization(response, {"status": "complete"})
    assert _is_empty_provider_response(response) is False


def test_finalization_status_falsy_payload_treated_as_absent():
    response = _FakeResponse(text="", tool_calls=[])
    _with_finalization(response, None)
    assert _is_empty_provider_response(response) is True

    response2 = _FakeResponse(text="", tool_calls=[])
    _with_finalization(response2, {})
    assert _is_empty_provider_response(response2) is True


def test_missing_attributes_default_to_empty():

    class _Bare:
        text = ""

    assert _is_empty_provider_response(_Bare()) is True
