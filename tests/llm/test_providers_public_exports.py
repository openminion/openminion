from __future__ import annotations

from openminion.modules.llm.contracts import (
    ProviderResponse,
    ProviderToolSpec,
    detect_raw_tool_payload_json,
    extract_fallback_tool_calls_from_text_with_metadata,
)


def test_provider_public_exports_include_tool_calling_helpers() -> None:
    text = '{"tool":"file.read","path":"/tmp/demo.txt"}'

    calls, metadata = extract_fallback_tool_calls_from_text_with_metadata(
        text,
        allowed_tool_names=["file.read"],
    )

    assert calls
    assert calls[0].name == "file.read"
    assert calls[0].arguments == {"path": "/tmp/demo.txt"}
    assert metadata["fallback_parse_mode"] == "json_payload"
    assert detect_raw_tool_payload_json(text)


def test_provider_public_exports_include_response_and_tool_contracts() -> None:
    response = ProviderResponse(text="done", model="demo")
    spec = ProviderToolSpec(
        name="file.read",
        description="Read a file",
        parameters={"type": "object"},
    )

    assert response.text == "done"
    assert spec.name == "file.read"
