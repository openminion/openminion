from __future__ import annotations

from openminion.cli.presentation.tool.progress import (
    build_tool_event_from_progress,
)


def test_provenance_fields_default_safe_when_payload_omits_them() -> None:

    event = build_tool_event_from_progress(
        {
            "tool_name": "exec.run",
            "args": {"command": "ls"},
            "content": "out",
            "duration_ms": 120,
        }
    )
    assert event.tool_name == "exec.run"
    assert event.duration_ms == 120
    assert event.state == ""
    assert event.model_tool_name == ""
    assert event.runtime_tool_name == ""
    assert event.runtime_binding_id == ""
    assert event.runtime_fallback_used is False
    assert event.runtime_fallback_chain is None
    assert event.runtime_resolution_source == ""
    assert event.fallback_index is None


def test_provenance_fields_lifted_from_payload() -> None:

    payload = {
        "tool_name": "web.search",
        "args": {"query": "x"},
        "content": "hit",
        "duration_ms": 600,
        "state": "ok",
        "model_tool_name": "web.search",
        "runtime_tool_name": "search.serper.search",
        "runtime_binding_id": "search.serper",
        "runtime_fallback_used": True,
        "runtime_fallback_chain": ["search.tavily.search"],
        "runtime_resolution_source": "registry",
        "fallback_index": 1,
        "call_id": "call-42",
    }
    event = build_tool_event_from_progress(payload)
    assert event.state == "ok"
    assert event.model_tool_name == "web.search"
    assert event.runtime_tool_name == "search.serper.search"
    assert event.runtime_binding_id == "search.serper"
    assert event.runtime_fallback_used is True
    assert event.runtime_fallback_chain == ["search.tavily.search"]
    assert event.runtime_resolution_source == "registry"
    assert event.fallback_index == 1
    assert event.call_id == "call-42"


def test_fallback_chain_filters_blank_entries() -> None:

    event = build_tool_event_from_progress(
        {
            "tool_name": "web.search",
            "runtime_fallback_used": True,
            "runtime_fallback_chain": ["", "search.tavily.search", ""],
        }
    )
    assert event.runtime_fallback_chain == ["search.tavily.search"]

    event_all_blank = build_tool_event_from_progress(
        {
            "tool_name": "web.search",
            "runtime_fallback_used": True,
            "runtime_fallback_chain": ["", "", None],
        }
    )
    assert event_all_blank.runtime_fallback_chain is None


def test_fallback_index_coerced_via_optional_int() -> None:

    event = build_tool_event_from_progress({"tool_name": "x", "fallback_index": "2"})
    assert event.fallback_index == 2

    event_bad = build_tool_event_from_progress(
        {"tool_name": "x", "fallback_index": "not-a-number"}
    )
    assert event_bad.fallback_index is None
