from __future__ import annotations

import pytest

from openminion.cli.status.tool_calls import (
    MARKER_FAIL,
    MARKER_OK,
    MARKER_RUNNING,
    format_tool_args_preview,
    format_tool_call_line,
    format_tool_fallback_marker,
    format_tool_provenance_marker,
)


def test_web_search_with_provenance_and_duration() -> None:
    line = format_tool_call_line(
        tool_name="web.search",
        args={"query": "latest Anthropic news"},
        state="ok",
        duration_ms=600,
        model_tool_name="web.search",
        runtime_tool_name="search.serper.search",
        family_has_multiple_providers=True,
    )
    assert line == '● web.search("latest Anthropic news") → serper 600ms'


def test_git_status_no_provenance_no_args() -> None:
    line = format_tool_call_line(
        tool_name="git.status",
        args={},
        state="ok",
        duration_ms=100,
        model_tool_name="git.status",
    )
    assert line == "● git.status() 100ms"


def test_exec_run_with_command_args() -> None:
    line = format_tool_call_line(
        tool_name="exec.run",
        args={"command": "npm install --silent"},
        state="ok",
        duration_ms=4200,
        model_tool_name="exec.run",
    )
    assert line == '● exec.run("npm install --silent") 4.2s'


def test_file_read_denied_with_extra_detail() -> None:
    line = format_tool_call_line(
        tool_name="file.read",
        args={"path": "/etc/shadow"},
        state="denied",
        duration_ms=None,
        model_tool_name="file.read",
        extra_detail="path outside workspace",
    )
    assert line == '✗ file.read("etc/shadow") denied (path outside workspace)'


def test_web_fetch_approving() -> None:
    line = format_tool_call_line(
        tool_name="web.fetch",
        args={"url": "https://example.com"},
        state="approving",
        model_tool_name="web.fetch",
    )
    assert "⏳" in line and "approving…" in line and "web.fetch" in line


def test_args_preview_empty_args() -> None:
    assert format_tool_args_preview("git.status", None) == ""
    assert format_tool_args_preview("git.status", {}) == ""


def test_args_preview_file_path_last_two_segments() -> None:
    assert (
        format_tool_args_preview(
            "file.read", {"path": "openminion/src/openminion/tools/exec/plugin.py"}
        )
        == '"exec/plugin.py"'
    )


def test_args_preview_command_truncation() -> None:
    long_command = "npm install " + ("--very-long-flag " * 20).strip()
    result = format_tool_args_preview("exec.run", {"command": long_command})
    assert result.startswith('"') and result.endswith('"')
    assert "…" in result
    assert len(result) <= 82  # 80 char body + 2 quotes


def test_args_preview_query_truncation() -> None:
    long_query = "find me everything about " + "x" * 100
    result = format_tool_args_preview("web.search", {"query": long_query})
    assert result.startswith('"') and result.endswith('"')
    assert "…" in result


def test_args_preview_unknown_falls_back_to_json() -> None:
    result = format_tool_args_preview("custom.tool", {"foo": 1, "bar": "x"})
    assert "foo" in result and "bar" in result


def test_provenance_shown_for_multi_provider_with_different_runtime() -> None:
    suffix = format_tool_provenance_marker(
        model_tool_name="web.search",
        runtime_tool_name="search.serper.search",
        family_has_multiple_providers=True,
    )
    assert suffix == " → serper"


@pytest.mark.parametrize(
    ("model_tool_name", "runtime_tool_name", "family_has_multiple_providers"),
    [
        ("git.status", "git.status", False),
        ("git.status", "git.status", True),
        ("web.search", "", True),
    ],
)
def test_provenance_omitted_cases(
    model_tool_name: str,
    runtime_tool_name: str,
    family_has_multiple_providers: bool,
) -> None:
    suffix = format_tool_provenance_marker(
        model_tool_name=model_tool_name,
        runtime_tool_name=runtime_tool_name,
        family_has_multiple_providers=family_has_multiple_providers,
    )
    assert suffix == ""


def test_fallback_marker_with_chain() -> None:
    marker = format_tool_fallback_marker(
        runtime_fallback_used=True,
        runtime_fallback_chain=["search.tavily.search", "search.serper.search"],
    )
    assert marker == " (fallback after tavily)"


def test_fallback_marker_omitted_when_not_used() -> None:
    marker = format_tool_fallback_marker(
        runtime_fallback_used=False,
        runtime_fallback_chain=["search.tavily.search"],
    )
    assert marker == ""


def test_fallback_marker_with_empty_chain_is_safe() -> None:
    marker = format_tool_fallback_marker(
        runtime_fallback_used=True,
        runtime_fallback_chain=None,
    )
    assert marker == " (fallback)"


def test_fallback_and_provenance_combined() -> None:
    line = format_tool_call_line(
        tool_name="web.search",
        args={"query": "current events"},
        state="ok",
        duration_ms=900,
        model_tool_name="web.search",
        runtime_tool_name="search.serper.search",
        runtime_fallback_used=True,
        runtime_fallback_chain=["search.tavily.search", "search.serper.search"],
        family_has_multiple_providers=True,
    )
    assert (
        line == '● web.search("current events") → serper (fallback after tavily) 900ms'
    )


@pytest.mark.parametrize(
    ("ms", "expected_suffix"),
    [
        (0, "0ms"),
        (50, "50ms"),
        (999, "999ms"),
        (1000, "1.0s"),
        (1500, "1.5s"),
        (42_300, "42.3s"),
    ],
)
def test_duration_formatting(ms: int, expected_suffix: str) -> None:
    line = format_tool_call_line(
        tool_name="git.status",
        args={},
        state="ok",
        duration_ms=ms,
        model_tool_name="git.status",
    )
    assert line.endswith(expected_suffix), line


def test_duration_omitted_when_none() -> None:
    line = format_tool_call_line(
        tool_name="git.status",
        args={},
        state="ok",
        duration_ms=None,
        model_tool_name="git.status",
    )
    assert line == "● git.status()"


# Negative paths ------------------------------------------------------------


def test_unknown_tool_uses_raw_canonical_name() -> None:
    line = format_tool_call_line(
        tool_name="custom.unknown",
        args={},
        state="ok",
        duration_ms=100,
        model_tool_name="custom.unknown",
    )
    assert "custom.unknown" in line


def test_state_markers_distinct() -> None:
    assert MARKER_OK != MARKER_FAIL
    assert MARKER_OK != MARKER_RUNNING
    assert MARKER_FAIL != MARKER_RUNNING
