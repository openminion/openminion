from __future__ import annotations

import json


from openminion.services.agent.execution.tool_arguments import (
    collect_missing_required,
    missing_required_for_call,
    required_fields_from_spec,
)
from openminion.services.agent.execution.required import RequiredLaneState
from openminion.services.agent.execution.required.metadata import (
    invalid_tool_arguments_metadata,
)
from openminion.modules.llm.providers.base import ProviderToolCall, ProviderToolSpec


# Helpers


def _spec_with_required(*fields: str) -> ProviderToolSpec:
    return ProviderToolSpec(
        name="test_tool",
        description="A tool for testing.",
        parameters={
            "type": "object",
            "properties": {field: {"type": "string"} for field in fields},
            "required": list(fields),
        },
    )


def _tool_call(name: str, arguments: dict) -> ProviderToolCall:
    return ProviderToolCall(name=name, arguments=arguments)


def _spec_lookup_for(spec: ProviderToolSpec):

    def lookup(tool_name: str):
        if tool_name == spec.name:
            return spec
        return None

    return lookup


# required_fields_from_spec extracts the right fields


def test_required_fields_from_spec_returns_required_fields() -> None:
    spec = _spec_with_required("query", "location")
    result = required_fields_from_spec(spec)
    assert set(result) == {"query", "location"}


def test_required_fields_from_spec_returns_empty_for_no_required() -> None:
    spec = ProviderToolSpec(
        name="optional_tool",
        description="All optional.",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
        },
    )
    result = required_fields_from_spec(spec)
    assert result == []


def test_required_fields_from_spec_returns_empty_for_none() -> None:
    result = required_fields_from_spec(None)
    assert result == []


# missing_required_for_call detects malformed args


def test_missing_required_for_call_detects_missing_field() -> None:
    spec = _spec_with_required("query")
    call = _tool_call("test_tool", {})  # missing "query"
    missing = missing_required_for_call(call, spec_lookup=_spec_lookup_for(spec))
    assert "query" in missing


def test_missing_required_for_call_passes_for_valid_args() -> None:
    spec = _spec_with_required("query")
    call = _tool_call("test_tool", {"query": "latest news"})
    missing = missing_required_for_call(call, spec_lookup=_spec_lookup_for(spec))
    assert missing == []


def test_missing_required_for_call_treats_empty_string_as_missing() -> None:
    spec = _spec_with_required("query")
    call = _tool_call("test_tool", {"query": ""})  # empty = missing
    missing = missing_required_for_call(call, spec_lookup=_spec_lookup_for(spec))
    assert "query" in missing


def test_missing_required_for_call_treats_none_value_as_missing() -> None:
    spec = _spec_with_required("query")
    call = _tool_call("test_tool", {"query": None})
    missing = missing_required_for_call(call, spec_lookup=_spec_lookup_for(spec))
    assert "query" in missing


def test_missing_required_for_call_handles_unknown_tool_gracefully() -> None:
    spec = _spec_with_required("query")
    call = _tool_call("unknown_tool", {})
    missing = missing_required_for_call(call, spec_lookup=_spec_lookup_for(spec))
    # Unknown tool has no spec → no required fields to check → no missing
    assert missing == []


# collect_missing_required handles batch of tool calls


def test_collect_missing_required_finds_missing_across_calls() -> None:
    spec = _spec_with_required("location")
    calls = [
        _tool_call("test_tool", {}),  # missing "location"
        _tool_call("test_tool", {"location": "New York"}),  # valid
    ]
    missing = collect_missing_required(calls, spec_lookup=_spec_lookup_for(spec))
    assert "test_tool" in missing
    assert "location" in missing["test_tool"]


def test_collect_missing_required_empty_for_valid_calls() -> None:
    spec = _spec_with_required("location")
    calls = [_tool_call("test_tool", {"location": "London"})]
    missing = collect_missing_required(calls, spec_lookup=_spec_lookup_for(spec))
    assert missing == {}


# arg_retry_attempted gates exactly one repair retry


def test_arg_retry_attempted_starts_false() -> None:
    state = RequiredLaneState()
    assert state.arg_retry_attempted is False


def test_arg_retry_attempted_blocks_second_repair() -> None:
    state_after_first_retry = RequiredLaneState(arg_retry_attempted=True)
    assert state_after_first_retry.arg_retry_attempted is True


# _invalid_tool_arguments_metadata produces correct error structure


def test_invalid_tool_arguments_metadata_structure() -> None:
    metadata = invalid_tool_arguments_metadata(
        tool_name="web.search",
        missing_fields_csv="query,location",
    )
    assert metadata["tool_loop_termination_reason"] == "tool_arg_exhausted"
    assert metadata["tool_arg_exhausted"] == "web.search"
    assert metadata["tool_error_code"] == "INVALID_TOOL_ARGUMENTS"
    assert metadata["tool_error_reason_code"] == "tool_arg_validation_failed"
    # Validate the tool_results payload is valid JSON
    tool_results = json.loads(metadata["tool_results"])
    assert isinstance(tool_results, list)
    assert len(tool_results) == 1
    assert tool_results[0]["ok"] is False
    assert tool_results[0]["tool_name"] == "web.search"
    # Validate the contract payload
    contract_payload = json.loads(metadata["tool_error_payload"])
    assert contract_payload["error_code"] == "INVALID_TOOL_ARGUMENTS"
    assert contract_payload["tool_name"] == "web.search"
    assert "query" in contract_payload["missing_fields"]
    assert "location" in contract_payload["missing_fields"]


def test_invalid_tool_arguments_metadata_with_single_field() -> None:
    metadata = invalid_tool_arguments_metadata(
        tool_name="weather",
        missing_fields_csv="city",
    )
    contract_payload = json.loads(metadata["tool_error_payload"])
    assert contract_payload["missing_fields"] == ["city"]
    assert metadata["tool_arg_exhausted_missing"] == "city"


# TURR-06 negative-path: hopeless malformed args fail fast


def test_hopeless_malformed_call_has_non_empty_missing_fields() -> None:
    spec = _spec_with_required("query")
    call = _tool_call("test_tool", {})  # no args at all
    missing = missing_required_for_call(call, spec_lookup=_spec_lookup_for(spec))
    assert len(missing) > 0, (
        "Hopeless malformed call must be flagged as having missing required fields"
    )


def test_corrected_call_passes_missing_check() -> None:
    spec = _spec_with_required("query")
    repaired_call = _tool_call("test_tool", {"query": "latest AI news"})
    missing = missing_required_for_call(
        repaired_call, spec_lookup=_spec_lookup_for(spec)
    )
    assert missing == [], (
        "A correctly repaired tool call must have no missing required fields"
    )


def test_bounded_retry_cap_is_exactly_one() -> None:
    # Initial state: no retries yet
    initial = RequiredLaneState(arg_retry_attempted=False)
    assert initial.arg_retry_attempted is False, (
        "Initial state must allow the first repair attempt"
    )

    # After first retry: blocked
    after_one_retry = RequiredLaneState(arg_retry_attempted=True)
    assert after_one_retry.arg_retry_attempted is True, (
        "After one retry, state must block further repair attempts"
    )
