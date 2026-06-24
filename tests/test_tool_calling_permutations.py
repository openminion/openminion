from __future__ import annotations

import itertools

import pytest

from openminion.modules.tool.bootstrap import build_runtime_bootstrap
from openminion.modules.tool.contracts import normalize_raw_model_tool_name
from openminion.modules.tool.dispatch import resolve_binding_for_call


def _case_variants(token: str) -> tuple[str, ...]:
    return (
        token,
        token.upper(),
        token.title(),
    )


def _prefixed_variants(token: str) -> tuple[str, ...]:
    prefixes = ("", "tool.", "tools.", "function.", "functions.", "TOOL.", "FUNCTIONS.")
    return tuple(f"{prefix}{token}" for prefix in prefixes)


BASE_ALIAS_CASES: dict[str, str] = {
    "file.read": "file.read",
    "file.list_dir": "file.list_dir",
    "file.find": "file.find",
    "exec.run": "exec.run",
    "exec.poll": "exec.poll",
    "exec.kill": "exec.kill",
    "web.search": "web.search",
    "web.fetch": "web.fetch",
    "weather": "weather",
    "time": "time",
    "location": "location",
    "browser": "browser",
}


NORMALIZATION_CASES = [
    (raw_name, expected)
    for alias, expected in BASE_ALIAS_CASES.items()
    for variant in _case_variants(alias)
    for raw_name in _prefixed_variants(variant)
]


@pytest.fixture(scope="module")
def bootstrap_manager():
    return build_runtime_bootstrap(
        config=None,
        workspace_root=None,
        run_root=None,
        strict=False,
    ).manager


@pytest.mark.parametrize(("raw_name", "expected"), NORMALIZATION_CASES)
def test_normalize_raw_model_tool_name_permutations(
    raw_name: str, expected: str
) -> None:
    assert normalize_raw_model_tool_name(raw_name) == expected


@pytest.mark.parametrize(
    (
        "raw_name",
        "available",
        "expected_runtime_tool",
        "expected_model_tool",
        "expected_binding",
    ),
    [
        (
            "functions.file.read",
            ("file.read",),
            "file.read",
            "file.read",
            "runtime.file.read",
        ),
        (
            "FUNCTION.file.read",
            ("file.read",),
            "file.read",
            "file.read",
            "runtime.file.read",
        ),
        (
            "tool.web.search",
            ("search.tavily.search",),
            "search.tavily.search",
            "web.search",
            "runtime.web.search",
        ),
        (
            "tool.web.search",
            ("search.dispatch", "search.tavily.search"),
            "search.dispatch",
            "web.search",
            "runtime.web.search",
        ),
        (
            "functions.web.fetch",
            ("fetch.get",),
            "fetch.get",
            "web.fetch",
            "runtime.web.fetch",
        ),
        (
            "tool.browser",
            ("browser",),
            "browser",
            "browser",
            "runtime.browser",
        ),
        (
            "browser",
            ("browser",),
            "browser",
            "browser",
            "runtime.browser",
        ),
        (
            "tool.list",
            ("tool.list", "tool.search"),
            "tool.list",
            "tool.list",
            "runtime.tool.list",
        ),
        (
            "tool.search",
            ("tool.list", "tool.search"),
            "tool.search",
            "tool.list",
            "runtime.tool.list",
        ),
    ],
)
def test_resolve_binding_for_call_permutations(
    bootstrap_manager,
    raw_name: str,
    available: tuple[str, ...],
    expected_runtime_tool: str,
    expected_model_tool: str,
    expected_binding: str,
) -> None:
    resolution = resolve_binding_for_call(
        raw_tool_name=raw_name,
        available_tool_names=available,
        manager=bootstrap_manager,
    )
    assert resolution is not None
    assert resolution.runtime_tool_name == expected_runtime_tool
    assert resolution.model_tool_id == expected_model_tool
    assert resolution.runtime_binding_id == expected_binding


def test_resolution_fallback_chain_is_stable_for_permuted_aliases(
    bootstrap_manager,
) -> None:
    aliases = ("file.read", "FILE.READ", "functions.file.read")
    available = ("file.read",)
    for raw_name in aliases:
        resolution = resolve_binding_for_call(
            raw_tool_name=raw_name,
            available_tool_names=available,
            manager=bootstrap_manager,
        )
        assert resolution is not None
        assert resolution.runtime_fallback_chain == ("file.read",)
        assert resolution.runtime_tool_name == "file.read"


def test_unknown_permutations_do_not_resolve(bootstrap_manager) -> None:
    for raw_name in itertools.product(
        ("tool.", "functions.", ""), ("nonexistent", "totally.unknown", "x_y_z")
    ):
        candidate = "".join(raw_name)
        resolution = resolve_binding_for_call(
            raw_tool_name=candidate,
            available_tool_names=("file.read", "web.search"),
            manager=bootstrap_manager,
        )
        assert resolution is None
