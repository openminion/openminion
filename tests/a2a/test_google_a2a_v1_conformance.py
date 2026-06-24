from __future__ import annotations

import json

import pytest

from openminion.modules.a2a.wire.google_a2a_v1 import (
    AGENT_CARD_WELL_KNOWN_PATH,
    A2A_PROTOCOL_VERSION,
    AgentCapabilities,
    AgentSkill,
    JSONRPC_VERSION,
    JsonRpcError,
    JsonRpcErrorCode,
    JsonRpcResponse,
    TASK_STATES,
    TaskMessage,
    TaskPart,
    TaskState,
    build_agent_card,
    parse_jsonrpc_request,
    serialize_jsonrpc_response,
)


def test_agent_card_well_known_path_is_canonical() -> None:
    assert AGENT_CARD_WELL_KNOWN_PATH == "/.well-known/agent.json"


def test_a2a_protocol_version_is_v1() -> None:
    assert A2A_PROTOCOL_VERSION == "1.0"


def test_agent_card_serialization_has_required_v1_fields() -> None:
    card = build_agent_card(
        name="openminion-test-agent",
        description="Test agent for ISAP-12 wire conformance.",
        url="https://agent.example.com/a2a/v1",
        version="0.0.1",
        skills=[
            AgentSkill(
                id="summarize",
                name="Summarize",
                description="Summarize a document in 1-3 sentences.",
                tags=["text"],
                examples=["Summarize the latest release notes."],
            ),
        ],
    )
    payload = card.to_jsonable()
    # Spec-required top-level fields.
    for required in (
        "name",
        "description",
        "url",
        "version",
        "protocolVersion",
        "capabilities",
        "defaultInputModes",
        "defaultOutputModes",
        "skills",
    ):
        assert required in payload, f"missing required AgentCard field: {required}"
    assert payload["protocolVersion"] == "1.0"
    caps = payload["capabilities"]
    assert "streaming" in caps and "pushNotifications" in caps
    skill0 = payload["skills"][0]
    assert "inputModes" in skill0 and "outputModes" in skill0


def test_agent_card_round_trips_to_json_string() -> None:
    card = build_agent_card(
        name="t",
        description="t",
        url="https://x",
        version="0.0.1",
    )
    text = json.dumps(card.to_jsonable())
    re_parsed = json.loads(text)
    assert re_parsed["name"] == "t"
    assert re_parsed["protocolVersion"] == "1.0"


def test_agent_card_optional_fields_omitted_when_unset() -> None:
    card = build_agent_card(
        name="t",
        description="t",
        url="https://x",
        version="0.0.1",
        documentation_url=None,
        provider_organization=None,
        provider_url=None,
    )
    payload = card.to_jsonable()
    assert "documentationUrl" not in payload
    assert "providerOrganization" not in payload
    assert "providerUrl" not in payload


def test_agent_capabilities_defaults_to_streaming_on() -> None:
    caps = AgentCapabilities()
    assert caps.streaming is True
    assert caps.push_notifications is False


def test_task_state_values_match_a2a_spec() -> None:
    expected = {
        "submitted",
        "working",
        "input-required",
        "completed",
        "failed",
        "canceled",
    }
    assert TASK_STATES == expected


def test_task_message_part_serialization_camel_cases_file_fields() -> None:
    msg = TaskMessage(
        role="user",
        parts=[
            TaskPart(kind="text", text="hello"),
            TaskPart(
                kind="file", file_url="https://x/y.pdf", file_mime="application/pdf"
            ),
        ],
    )
    payload = msg.to_jsonable()
    assert payload["role"] == "user"
    file_part = payload["parts"][1]
    assert file_part["fileUrl"] == "https://x/y.pdf"
    assert file_part["fileMime"] == "application/pdf"


def test_jsonrpc_parse_valid_request() -> None:
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "method": "task/submit",
            "params": {"message": "hi"},
            "id": "req-1",
        }
    )
    req = parse_jsonrpc_request(body)
    assert req.method == "task/submit"
    assert req.params == {"message": "hi"}
    assert req.id == "req-1"


def test_jsonrpc_parse_rejects_wrong_version() -> None:
    body = {"jsonrpc": "1.0", "method": "x", "id": 1}
    with pytest.raises(ValueError, match="INVALID_REQUEST"):
        parse_jsonrpc_request(body)


def test_jsonrpc_parse_rejects_missing_method() -> None:
    body = {"jsonrpc": "2.0", "id": 1}
    with pytest.raises(ValueError, match="INVALID_REQUEST"):
        parse_jsonrpc_request(body)


def test_jsonrpc_parse_rejects_malformed_json_body() -> None:
    with pytest.raises(ValueError, match="PARSE_ERROR"):
        parse_jsonrpc_request("not valid json")


def test_jsonrpc_parse_rejects_positional_params() -> None:
    # A2A v1 spec uses by-name params only; positional list should fail.
    body = {"jsonrpc": "2.0", "method": "x", "params": [1, 2], "id": 1}
    with pytest.raises(ValueError, match="INVALID_REQUEST"):
        parse_jsonrpc_request(body)


def test_jsonrpc_response_serialize_success() -> None:
    resp = JsonRpcResponse(id="req-1", result={"taskId": "t-1", "state": "completed"})
    text = serialize_jsonrpc_response(resp)
    parsed = json.loads(text)
    assert parsed["jsonrpc"] == "2.0"
    assert parsed["id"] == "req-1"
    assert parsed["result"]["state"] == "completed"
    assert "error" not in parsed


def test_jsonrpc_response_serialize_error() -> None:
    resp = JsonRpcResponse(
        id=1,
        error=JsonRpcError(
            code=JsonRpcErrorCode.TASK_NOT_FOUND,
            message="task missing",
            data={"task_id": "x"},
        ),
    )
    parsed = json.loads(serialize_jsonrpc_response(resp))
    assert parsed["error"]["code"] == -32001
    assert parsed["error"]["message"] == "task missing"
    assert parsed["error"]["data"] == {"task_id": "x"}
    assert "result" not in parsed


def test_jsonrpc_version_constant_is_2_0() -> None:
    assert JSONRPC_VERSION == "2.0"


def test_task_state_enum_matches_string_values() -> None:
    assert TaskState.SUBMITTED.value == "submitted"
    assert TaskState.INPUT_REQUIRED.value == "input-required"
