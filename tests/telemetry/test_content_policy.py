from __future__ import annotations

import asyncio

import pytest

from openminion.base.config import OTELExporterConfig
from openminion.modules.telemetry.schemas import TelemetryEvent
from openminion.modules.telemetry.export.otel import (
    OpenTelemetryTraceExporter,
    RecordingOTELTraceSink,
)
from openminion.modules.telemetry.service import TelemetryService
from openminion.modules.telemetry.trace.metadata import apply_content_policy


class _Exporter:
    def __init__(self) -> None:
        self.events = []

    def export(self, event: TelemetryEvent) -> bool:
        self.events.append(event)
        return True

    def close(self) -> None:
        return

    def delete_pending_invocation(self, invocation_id: str) -> int:
        return 0


_EXTERNAL_CONTENT = {
    "gen_ai.input.messages": [
        {
            "role": "user",
            "content": "canonical-input-content",
            "text": "canonical-input-text",
            "final_text": "nested-output-under-canonical-input",
        }
    ],
    "gen_ai.output.messages": [
        {
            "role": "assistant",
            "content": "canonical-output-content",
            "text": "canonical-output-text",
            "user_message": "nested-input-under-canonical-output",
        }
    ],
    "user_message": "input-user",
    "input_messages": [
        {
            "role": "user",
            "content": "input-list",
            "assistant_text": "nested-output-under-input-list",
        }
    ],
    "prompt": {
        "content": "input-prompt",
        "final_text": "nested-output-under-input",
        "response": "nested-output-under-input",
        "result": {"content": "nested-tool-under-input"},
        "system_prompt": "nested-system-under-input",
    },
    "raw_prompt": "input-raw-prompt",
    "output_text": "output-text",
    "output_messages": [
        {
            "role": "assistant",
            "content": "output-list",
            "user_message": "nested-input-under-output-list",
        }
    ],
    "response": {
        "arguments": {"query": "nested-tool-under-output"},
        "content": "output-response",
        "history": "nested-input-under-output",
        "system_prompt": "nested-system-under-output",
    },
    "raw_response": "output-raw-response",
    "content": "assistant-body",
    "result": {
        "content": "tool-result",
        "reasoning_content": "never-reasoning",
        "credentials": "never-credentials",
    },
    "arguments": {"query": "tool-arguments"},
    "api_key": "never-api-key",
    "reasoning_summary": "never-summary",
}


@pytest.mark.parametrize("via_service", [False, True])
@pytest.mark.parametrize(
    ("flag", "expected", "excluded"),
    [
        (
            "include_input_messages",
            (
                "canonical-input-content",
                "canonical-input-text",
                "input-user",
                "input-list",
                "input-prompt",
                "input-raw-prompt",
            ),
            (
                "canonical-output-content",
                "canonical-output-text",
                "nested-output-under-canonical-input",
                "output-text",
                "output-list",
                "output-response",
                "output-raw-response",
                "nested-output-under-input",
                "nested-output-under-input-list",
                "nested-tool-under-input",
                "nested-system-under-input",
                "assistant-body",
                "tool-result",
            ),
        ),
        (
            "include_output_messages",
            (
                "canonical-output-content",
                "canonical-output-text",
                "output-text",
                "output-list",
                "output-response",
                "output-raw-response",
            ),
            (
                "canonical-input-content",
                "canonical-input-text",
                "input-user",
                "input-list",
                "input-prompt",
                "input-raw-prompt",
                "nested-input-under-output",
                "nested-input-under-output-list",
                "nested-input-under-canonical-output",
                "nested-tool-under-output",
                "nested-system-under-output",
                "assistant-body",
                "tool-result",
            ),
        ),
        (
            "include_tool_content",
            ("tool-result", "tool-arguments"),
            ("input-user", "output-text", "assistant-body"),
        ),
        (
            "include_assistant_body",
            ("assistant-body",),
            ("input-user", "output-text", "tool-result"),
        ),
        (None, (), ("input-user", "output-text", "assistant-body", "tool-result")),
    ],
)
def test_external_content_flags_are_independent_through_exporter(
    tmp_path,
    via_service: bool,
    flag: str | None,
    expected: tuple[str, ...],
    excluded: tuple[str, ...],
) -> None:
    options = {flag: True} if flag else {}
    config = OTELExporterConfig(
        enabled=True,
        endpoint="http://collector:4318",
        noncritical_queue_capacity=0,
        **options,
    )
    sink = RecordingOTELTraceSink()
    exporter = OpenTelemetryTraceExporter(config, sink=sink)
    event = TelemetryEvent(
        session_id="session-1",
        turn_id="turn-1",
        event_type="policy.applied",
        data=dict(_EXTERNAL_CONTENT),
    )
    if via_service:
        service = TelemetryService(
            str(tmp_path / ".openminion" / "telemetry.db"),
            otel_exporter_config=config,
            external_exporter=exporter,
        )
        service.record_event_sync(event)
        service.close_sync()
    else:
        exporter.export(event)
        exporter.close()

    exported = str(sink.records[0].attributes)
    assert all(value in exported for value in expected)
    assert all(value not in exported for value in excluded)
    assert "never-reasoning" not in exported
    assert "never-credentials" not in exported
    assert "never-api-key" not in exported
    assert "never-summary" not in exported


def test_default_policy_omits_content_and_prohibited_fields() -> None:
    cleaned = apply_content_policy(
        {
            "gen_ai.input.messages": [{"content": "hello"}],
            "gen_ai.tool.call.arguments": {"query": "private"},
            "path": "/private/repository/file.py",
            "diff_body": "private patch",
            "itinerary_details": "private itinerary",
            "raw_source": "private source",
            "prompt": "private prompt",
            "raw_prompt": "private raw prompt",
            "response": "private response",
            "raw_response": "private raw response",
            "api_key": "secret",
            "headers": {"Authorization": "secret"},
            "endpoint": "https://collector.invalid",
            "hostname": "private-host",
            "username": "private-user",
            "process_id": 1234,
            "hidden_chain_of_thought": "never",
            "input_tokens": 12,
        },
        allow_sensitive_content=False,
    )

    assert cleaned["input_tokens"] == 12
    assert "gen_ai.input.messages" not in cleaned
    assert "gen_ai.tool.call.arguments" not in cleaned
    assert "path" not in cleaned
    assert "diff_body" not in cleaned
    assert "itinerary_details" not in cleaned
    assert "raw_source" not in cleaned
    assert "prompt" not in cleaned
    assert "raw_prompt" not in cleaned
    assert "response" not in cleaned
    assert "raw_response" not in cleaned
    assert "api_key" not in cleaned
    assert "headers" not in cleaned
    assert "endpoint" not in cleaned
    assert "hostname" not in cleaned
    assert "username" not in cleaned
    assert "process_id" not in cleaned
    assert "hidden_chain_of_thought" not in cleaned


def test_sensitive_opt_in_never_overrides_prohibited_fields() -> None:
    cleaned = apply_content_policy(
        {
            "content": "allowed locally",
            "reasoning_summary": "prohibited",
            "approval_token": "prohibited",
        },
        allow_sensitive_content=True,
    )
    assert cleaned["content"] == "allowed locally"
    assert "reasoning_summary" not in cleaned
    assert "approval_token" not in cleaned


def test_error_prose_requires_local_sensitive_content_opt_in() -> None:
    payload = {
        "error": {
            "code": "PROVIDER_ERROR",
            "type": "provider_error",
            "category": "availability",
            "message": "Bearer private-error",
            "details": {"response": "private-details"},
        },
        "error_text": "private scalar error",
    }

    default = apply_content_policy(payload, allow_sensitive_content=False)
    opted_in = apply_content_policy(payload, allow_sensitive_content=True)

    assert default["error"] == {
        "code": "PROVIDER_ERROR",
        "type": "provider_error",
        "category": "availability",
    }
    assert "error_text" not in default
    assert opted_in["error"]["message"] == "Bearer private-error"
    assert opted_in["error"]["details"] == {"response": "private-details"}
    assert opted_in["error_text"] == "private scalar error"


def test_truncation_records_original_and_retained_size() -> None:
    cleaned = apply_content_policy(
        {"status": "x" * 20},
        allow_sensitive_content=False,
        max_string_length=8,
    )
    assert cleaned["status"] == "x" * 8
    assert cleaned["_telemetry_policy"]["truncations"] == [
        {"field": "status", "original_size": 20, "retained_size": 8}
    ]


def test_local_and_external_content_controls_are_independent(tmp_path) -> None:
    exporter = _Exporter()
    service = TelemetryService(
        str(tmp_path / ".openminion" / "telemetry.db"),
        include_local_content=False,
        otel_exporter_config=OTELExporterConfig(include_assistant_body=True),
        external_exporter=exporter,
    )
    asyncio.run(
        service.record_event(
            TelemetryEvent(
                session_id="session-1",
                turn_id="turn-1",
                event_type="llm.call.completed",
                data={
                    "content": "external opt-in",
                    "api_key": "never",
                    "error": {
                        "code": "FAIL",
                        "type": "provider_error",
                        "category": "availability",
                        "message": "never export",
                        "details": "never export details",
                    },
                    "error_text": "never export scalar",
                },
            )
        )
    )

    local = service._store.fetch_session_events("session-1")[0]
    external = exporter.events[0]
    assert "content" not in local.data
    assert external.data["content"] == "external opt-in"
    assert external.data["error"] == {
        "code": "FAIL",
        "type": "provider_error",
        "category": "availability",
    }
    assert "error_text" not in external.data
    assert "api_key" not in local.data
    assert "api_key" not in external.data
    assert service.export_queue_stats() is None
    service.close_sync()


def test_default_local_and_external_policy_omits_common_prompt_aliases(
    tmp_path,
) -> None:
    config = OTELExporterConfig(
        enabled=True,
        endpoint="http://collector:4318",
        noncritical_queue_capacity=0,
    )
    sink = RecordingOTELTraceSink()
    exporter = OpenTelemetryTraceExporter(config, sink=sink)
    service = TelemetryService(
        str(tmp_path / ".openminion" / "telemetry.db"),
        otel_exporter_config=config,
        external_exporter=exporter,
    )
    secrets = ("prompt-secret", "response-secret", "nested-secret")
    service.record_event_sync(
        TelemetryEvent(
            session_id="session-1",
            turn_id="turn-1",
            event_type="llm.call.completed",
            data={
                "prompt": secrets[0],
                "raw_prompt": secrets[0],
                "response": secrets[1],
                "raw_response": secrets[1],
                "messages": [{"role": "user", "prompt": secrets[2]}],
                "usage": {"total_tokens": 3},
            },
        )
    )

    local = str(service._store.fetch_session_events("session-1")[0].data)
    external = str(sink.records[0].attributes)
    service.close_sync()

    assert all(secret not in local for secret in secrets)
    assert all(secret not in external for secret in secrets)
    assert "total_tokens" in local
    assert "total_tokens" in external


def test_security_researcher_telemetry_is_structural_even_with_local_content(
    tmp_path,
) -> None:
    service = TelemetryService(
        str(tmp_path / ".openminion" / "telemetry.db"),
        include_local_content=True,
    )
    service.record_event_sync(
        TelemetryEvent(
            session_id="security-session",
            turn_id="security-turn",
            event_type="brain.execution_status",
            agent_id="profile-with-structural-results",
            data={
                "structural_only": True,
                "goal": "private target and source",
                "title": "audit /private/customer/repo",
                "artifact_refs": [
                    "/private/customer/raw-report",
                    "artifact://sha256/" + "b" * 64,
                ],
                "adaptive.finalization_status": {
                    "status": "incomplete",
                    "remaining_work": "private path",
                },
                "adaptive.termination_reason": "finalization_incomplete",
                "status": "error",
                "status_key": "error",
                "tool_results": [
                    {
                        "structural_only": True,
                        "tool_name": "security.publish_report",
                        "ok": True,
                        "verified": True,
                        "data": {
                            "result_status": "partial",
                            "assessment_id": "a" * 32,
                            "artifact_refs": ["artifact://sha256/" + "b" * 64],
                            "private_body": "private finding prose",
                        },
                    }
                ],
                "terminal": True,
            },
        )
    )

    event = service._store.fetch_session_events("security-session")[0]
    service.close_sync()

    assert event.data["adaptive.termination_reason"] == "finalization_incomplete"
    assert event.data["status"] == "error"
    assert event.data["status_key"] == "error"
    assert event.data["report_published"] is True
    assert event.data["result_status"] == "partial"
    assert event.data["assessment_id"] == "a" * 32
    assert event.data["artifact_refs"] == ["artifact://sha256/" + "b" * 64]
    assert "goal" not in event.data
    assert "title" not in event.data
    assert "adaptive.finalization_status" not in event.data
    assert "private_body" not in str(event.data)
