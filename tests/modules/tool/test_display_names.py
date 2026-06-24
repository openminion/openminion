from __future__ import annotations

import pytest

from openminion.modules.tool.contracts.display_names import (
    MODEL_TOOL_DISPLAY_NAME_MAP,
    display_name_for_tool_name,
)
from openminion.modules.tool.contracts.model_ids import ALL_MODEL_TOOL_IDS_SET


def test_map_covers_every_canonical_model_tool_id() -> None:
    missing = sorted(ALL_MODEL_TOOL_IDS_SET - MODEL_TOOL_DISPLAY_NAME_MAP.keys())
    assert not missing, (
        f"missing display labels for canonical model tool ids: {missing}"
    )


def test_map_keys_are_canonical_model_tool_ids_only() -> None:
    extras = sorted(MODEL_TOOL_DISPLAY_NAME_MAP.keys() - ALL_MODEL_TOOL_IDS_SET)
    assert not extras, (
        "non-canonical keys in MODEL_TOOL_DISPLAY_NAME_MAP "
        f"(should only contain canonical model tool ids): {extras}"
    )


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("web.search", "Web Search"),
        ("web.fetch", "Web Fetch"),
        ("browser", "Browser"),
        ("exec.run", "Run Command"),
        ("exec.poll", "Check Command"),
        ("exec.kill", "Stop Command"),
        ("file.read", "Read File"),
        ("file.write", "Write File"),
        ("file.edit", "Edit File"),
        ("file.list_dir", "List Directory"),
        ("code.grep", "Search Code"),
        ("code.patch", "Apply Patch"),
        ("memory.search", "Search Memory"),
        ("task.schedule", "Schedule Task"),
    ],
)
def test_canonical_ids_resolve_to_friendly_labels(token: str, expected: str) -> None:
    assert display_name_for_tool_name(token) == expected


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("runtime.web.search", "Web Search"),
        ("runtime.web.fetch", "Web Fetch"),
        ("runtime.exec.run", "Run Command"),
        ("runtime.file.read", "Read File"),
        ("runtime.browser", "Browser"),
    ],
)
def test_runtime_binding_ids_resolve_via_prefix_strip(
    token: str, expected: str
) -> None:
    assert display_name_for_tool_name(token) == expected


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("tool.web.search", "Web Search"),
        ("tools.exec.run", "Run Command"),
        ("function.file.read", "Read File"),
        ("functions.web.fetch", "Web Fetch"),
    ],
)
def test_wrapper_prefixed_forms_resolve_via_normalizer(
    token: str, expected: str
) -> None:
    assert display_name_for_tool_name(token) == expected


@pytest.mark.parametrize(
    "token",
    [
        "custom.tool",
        "search.serper.search",  # runtime candidate — V1 falls back to raw
        "fetch.scrapling.fetch",
        "browser.playwright.dispatch",
        "weather.openmeteo.fetch",
    ],
)
def test_unknown_tokens_fall_back_to_raw(token: str) -> None:
    assert display_name_for_tool_name(token) == token


@pytest.mark.parametrize("token", ["", " ", "\t\n", None])
def test_empty_or_whitespace_returns_unchanged(token) -> None:
    result = display_name_for_tool_name(token)
    # Empty string normalization: input becomes empty after strip, returns ""
    assert result == "" or result == token


def test_helper_does_not_crash_on_runtime_only_prefix() -> None:
    assert display_name_for_tool_name("runtime.") == "runtime."


def test_helper_resolves_canonical_after_stripping_runtime_prefix_for_wrapper_case() -> (
    None
):
    # Strip runtime. -> "tool.web.search" -> normalize_raw_model_tool_name strips
    # tool. -> "web.search" -> Web Search
    assert display_name_for_tool_name("runtime.tool.web.search") == "Web Search"
