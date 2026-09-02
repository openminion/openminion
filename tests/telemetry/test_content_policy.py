from __future__ import annotations

import asyncio

from openminion.base.config import OTELExporterConfig
from openminion.modules.telemetry.schemas import TelemetryEvent
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


def test_default_policy_omits_content_and_prohibited_fields() -> None:
    cleaned = apply_content_policy(
        {
            "gen_ai.input.messages": [{"content": "hello"}],
            "gen_ai.tool.call.arguments": {"query": "private"},
            "path": "/private/repository/file.py",
            "diff_body": "private patch",
            "itinerary_details": "private itinerary",
            "raw_source": "private source",
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
    service.close_sync()
