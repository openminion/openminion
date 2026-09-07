import stat

from openminion.modules.llm.providers.transport.trace import trace_http_json_request


def test_provider_trace_uses_private_directory_and_file_modes(tmp_path) -> None:
    trace_http_json_request(
        trace_metadata={"home_root": str(tmp_path), "session_id": "session"},
        provider_name="test",
        url="https://example.invalid",
        body_json="{}",
        payload={},
        headers={"Authorization": "secret"},
        timeout_seconds=1,
        transport="test",
        env={"OPENMINION_TRACE_REQUESTS": "1"},
    )

    trace_file = next(tmp_path.rglob("*.json"))
    assert stat.S_IMODE(trace_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(trace_file.parent.stat().st_mode) == 0o700
    assert "secret" not in trace_file.read_text(encoding="utf-8")
