from __future__ import annotations

import json
from pathlib import Path

from openminion.modules.telemetry.trace.structured import (
    trace_context_payload,
    write_structured_trace,
)
from openminion.modules.telemetry.trace.layout import (
    build_trace_file_path,
    resolve_trace_root,
)
from openminion.modules.telemetry.trace.metadata import merge_trace_metadata


def test_resolve_trace_root_prefers_explicit_trace_requests_dir(
    monkeypatch, tmp_path: Path
) -> None:
    explicit_trace_root = tmp_path / "trace-root"
    monkeypatch.setenv("OPENMINION_TRACE_REQUESTS_DIR", str(explicit_trace_root))
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / "data-root"))

    resolved = resolve_trace_root(home_root=tmp_path / "home-root")

    assert resolved == explicit_trace_root.resolve(strict=False)


def test_resolve_trace_root_prefers_data_root_over_home_root(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OPENMINION_TRACE_REQUESTS_DIR", raising=False)
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / "data-root"))

    resolved = resolve_trace_root(home_root=tmp_path / "home-root")

    assert resolved == (tmp_path / "data-root" / "traces").resolve(strict=False)


def test_resolve_trace_root_falls_back_to_explicit_home_root(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OPENMINION_TRACE_REQUESTS_DIR", raising=False)
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    resolved = resolve_trace_root(home_root=tmp_path / "home-root")

    assert resolved == (tmp_path / "home-root" / ".openminion" / "traces").resolve(
        strict=False
    )


def test_build_trace_file_path_uses_agent_and_run_layout(tmp_path: Path) -> None:
    trace_root = tmp_path / "traces"

    path, relative = build_trace_file_path(
        trace_root,
        session_id="agent.alpha::project-session",
        turn_id="turn_1712345678",
        inference_step=3,
        label="call03",
        suffix="-http.json",
    )

    assert path == (
        trace_root
        / "llm"
        / "agent.alpha"
        / "1712345678-project-session"
        / "step03-call03-http.json"
    )
    assert (
        relative == "llm/agent.alpha/1712345678-project-session/step03-call03-http.json"
    )


def test_agent_metadata_compatibility_surface_is_canonical() -> None:
    from openminion.services.agent.telemetry import merge_metadata

    assert merge_metadata is merge_trace_metadata


def test_write_structured_trace_merges_nested_dicts_and_keeps_existing_keys(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPENMINION_TRACE_REQUESTS", "1")
    monkeypatch.delenv("OPENMINION_TRACE_REQUESTS_DIR", raising=False)
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    trace_context = trace_context_payload(
        session_id="sess-structured",
        turn_id="turn-1",
        inference_step=1,
        label="call01",
        trace_id="trace-123",
        agent_id="agent-xyz",
        run_id="run-456",
        provider="openai",
        model="gpt-4.1-mini",
        home_root=tmp_path,
    )

    relative = write_structured_trace(
        trace_context=trace_context,
        patch={
            "response": {"ok": True, "finish_reason": "stop"},
            "state_snapshot": {"status": "active", "waiting_user": False},
        },
    )

    assert relative == trace_context["structured_trace_filename"]

    relative_again = write_structured_trace(
        trace_context=trace_context,
        patch={
            "response": {"output_text": "ok"},
            "state_snapshot": {"waiting_user": True},
        },
    )

    assert relative_again == trace_context["structured_trace_filename"]

    trace_path = resolve_trace_root(home_root=tmp_path) / str(
        trace_context["structured_trace_filename"]
    )
    payload = json.loads(trace_path.read_text(encoding="utf-8"))
    assert payload["trace"]["trace_id"] == "trace-123"
    assert payload["trace"]["agent_id"] == "agent-xyz"
    assert payload["provider"] == "openai"
    assert payload["model"] == "gpt-4.1-mini"
    assert payload["response"]["ok"] is True
    assert payload["response"]["finish_reason"] == "stop"
    assert payload["response"]["output_text"] == "ok"
    assert payload["state_snapshot"]["status"] == "active"
    assert payload["state_snapshot"]["waiting_user"] is True
