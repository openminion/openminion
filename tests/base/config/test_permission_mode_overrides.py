from __future__ import annotations

import pytest

from openminion.base.config.runtime.profile import (
    PERMISSION_MODE_BYPASS,
    PERMISSION_MODE_CYCLE,
    PERMISSION_MODE_DEFAULT,
    PERMISSION_MODE_READONLY,
    PERMISSION_MODE_VALUES,
    RunProfileOverrides,
    combine_run_profile_overrides,
    next_permission_mode,
    run_profile_overrides_from_mapping,
)
from openminion.modules.brain.adapters.tool.permission_mode import (
    PERMISSION_MODE_ALIASES,
    canonical_permission_mode,
    is_tool_blocked_by_readonly,
    readonly_blocked_tool_names,
)
from openminion.modules.tool import build_default_tool_registry


def test_three_permission_modes_exposed() -> None:
    assert PERMISSION_MODE_VALUES == frozenset(
        {PERMISSION_MODE_DEFAULT, PERMISSION_MODE_READONLY, PERMISSION_MODE_BYPASS}
    )
    assert PERMISSION_MODE_DEFAULT == "default"
    assert PERMISSION_MODE_READONLY == "readonly"
    assert PERMISSION_MODE_BYPASS == "bypass"


def test_cycle_order_matches_codex() -> None:
    assert PERMISSION_MODE_CYCLE == (
        PERMISSION_MODE_DEFAULT,
        PERMISSION_MODE_READONLY,
        PERMISSION_MODE_BYPASS,
    )


@pytest.mark.parametrize(
    ("raw_mode", "expected"),
    [
        (PERMISSION_MODE_DEFAULT, PERMISSION_MODE_READONLY),
        (PERMISSION_MODE_READONLY, PERMISSION_MODE_BYPASS),
        (PERMISSION_MODE_BYPASS, PERMISSION_MODE_DEFAULT),
        ("", PERMISSION_MODE_DEFAULT),
        ("garbage", PERMISSION_MODE_DEFAULT),
        (None, PERMISSION_MODE_DEFAULT),
        ("DEFAULT", PERMISSION_MODE_READONLY),
        ("ReadOnly", PERMISSION_MODE_BYPASS),
    ],
)
def test_next_permission_mode_cases(raw_mode: str | None, expected: str) -> None:
    assert next_permission_mode(raw_mode) == expected  # type: ignore[arg-type]


def test_overrides_default_empty_permission_mode() -> None:
    o = RunProfileOverrides()
    assert o.permission_mode == ""
    assert o.is_empty()


def test_overrides_with_permission_mode_not_empty() -> None:
    o = RunProfileOverrides(permission_mode="readonly")
    assert not o.is_empty()
    assert o.permission_mode == "readonly"


def test_overrides_cache_key_includes_permission_mode() -> None:
    o = RunProfileOverrides(permission_mode="bypass")
    other = RunProfileOverrides(permission_mode="readonly")
    assert o.cache_key() != other.cache_key()
    empty = RunProfileOverrides()
    assert empty.cache_key() == "none"


def test_combine_run_profile_overrides_merges_permission_mode() -> None:
    base = RunProfileOverrides(model="opus")
    extra = RunProfileOverrides(permission_mode="readonly")
    merged = combine_run_profile_overrides(base, extra)
    assert merged.model == "opus"
    assert merged.permission_mode == "readonly"


def test_combine_overrides_extra_permission_mode_wins() -> None:
    base = RunProfileOverrides(permission_mode="bypass")
    extra = RunProfileOverrides(permission_mode="readonly")
    merged = combine_run_profile_overrides(base, extra)
    assert merged.permission_mode == "readonly"


def test_mapping_parser_reads_permission_mode() -> None:
    overrides = run_profile_overrides_from_mapping({"permission_mode": "readonly"})
    assert overrides.permission_mode == "readonly"


def test_mapping_parser_reads_hyphenated_permission_mode() -> None:
    overrides = run_profile_overrides_from_mapping({"permission-mode": "bypass"})
    assert overrides.permission_mode == "bypass"


def test_mapping_parser_treats_default_permission_mode_as_empty() -> None:
    overrides = run_profile_overrides_from_mapping({"permission_mode": "default"})
    assert overrides.permission_mode == ""


def test_overrides_frozen_dataclass() -> None:
    o = RunProfileOverrides(permission_mode="readonly")
    with pytest.raises((AttributeError, TypeError)):
        o.permission_mode = "bypass"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("raw_mode", "expected"),
    [
        ("readonly", "readonly"),
        ("readOnly", "readonly"),
        ("read_only", "readonly"),
        ("read-only", "readonly"),
        ("ReadOnly", "readonly"),
        ("default", "ask"),
        ("plan", "ask"),
        ("acceptEdits", "auto"),
        ("bypassPermissions", "bypass"),
        ("bypass", "bypass"),
        ("totally_unknown", "ask"),
        ("", "ask"),
        (None, "ask"),
    ],
)
def test_canonical_permission_mode_cases(raw_mode: str | None, expected: str) -> None:
    assert canonical_permission_mode(raw_mode) == expected


def test_aliases_table_includes_readonly() -> None:
    assert PERMISSION_MODE_ALIASES.get("readonly") == "readonly"
    assert PERMISSION_MODE_ALIASES.get("readOnly") == "readonly"


def test_readonly_blocks_write_tools() -> None:
    assert is_tool_blocked_by_readonly("file.write")
    assert is_tool_blocked_by_readonly("file.edit")
    assert is_tool_blocked_by_readonly("file.trash")
    assert is_tool_blocked_by_readonly("code.patch")
    assert is_tool_blocked_by_readonly("exec.run")
    assert is_tool_blocked_by_readonly("exec.kill")
    assert is_tool_blocked_by_readonly("git.add")
    assert is_tool_blocked_by_readonly("git.commit")
    assert is_tool_blocked_by_readonly("git.reset")
    assert is_tool_blocked_by_readonly("git.stash")
    assert is_tool_blocked_by_readonly("memory.write")
    assert is_tool_blocked_by_readonly("memory.forget")
    assert is_tool_blocked_by_readonly("task.schedule")
    assert is_tool_blocked_by_readonly("task.cancel")
    assert is_tool_blocked_by_readonly("skill.ingest")
    assert is_tool_blocked_by_readonly("skill.remove")
    assert is_tool_blocked_by_readonly("tool.author")
    assert is_tool_blocked_by_readonly("tool.register")


def test_readonly_does_not_block_read_tools() -> None:
    assert not is_tool_blocked_by_readonly("file.read")
    assert not is_tool_blocked_by_readonly("file.read_range")
    assert not is_tool_blocked_by_readonly("file.list_dir")
    assert not is_tool_blocked_by_readonly("file.find")
    assert not is_tool_blocked_by_readonly("file.search")
    assert not is_tool_blocked_by_readonly("code.grep")
    assert not is_tool_blocked_by_readonly("code.repo_map")
    assert not is_tool_blocked_by_readonly("code.symbol_find")
    assert not is_tool_blocked_by_readonly("web.search")
    assert not is_tool_blocked_by_readonly("web.fetch")
    assert not is_tool_blocked_by_readonly("weather")
    assert not is_tool_blocked_by_readonly("time")
    assert not is_tool_blocked_by_readonly("git.status")
    assert not is_tool_blocked_by_readonly("git.diff")
    assert not is_tool_blocked_by_readonly("git.log")
    assert not is_tool_blocked_by_readonly("git.show")
    assert not is_tool_blocked_by_readonly("git.blame")
    assert not is_tool_blocked_by_readonly("memory.search")
    assert not is_tool_blocked_by_readonly("task.list")
    assert not is_tool_blocked_by_readonly("task.show")
    assert not is_tool_blocked_by_readonly("skill.list")
    assert not is_tool_blocked_by_readonly("skill.inspect")


def test_readonly_match_is_exact_or_dot_prefix() -> None:
    assert is_tool_blocked_by_readonly("file.write")
    assert is_tool_blocked_by_readonly("file.write.batch")
    assert not is_tool_blocked_by_readonly("file.writeable_check")
    assert not is_tool_blocked_by_readonly("xfile.write")


def test_readonly_empty_input_does_not_block() -> None:
    assert not is_tool_blocked_by_readonly("")
    assert not is_tool_blocked_by_readonly(None)  # type: ignore[arg-type]
    assert not is_tool_blocked_by_readonly("   ")


def test_readonly_blocked_tools_are_owned_by_tool_specs() -> None:
    expected = {
        "file.write",
        "file.edit",
        "file.trash",
        "code.patch",
        "exec.run",
        "exec.submit",
        "exec.send_keys",
        "exec.paste",
        "exec.kill",
        "git.add",
        "git.commit",
        "git.branch",
        "git.checkout",
        "git.reset",
        "git.stash",
        "memory.write",
        "memory.forget",
        "task.schedule",
        "task.cancel",
        "task.pause",
        "task.resume",
        "skill.ingest",
        "skill.ingest_url",
        "skill.remove",
        "tool.author",
        "tool.register",
        # task.delegate (sub-agent delegation) is gated under
        # readonly because the spawned turn may execute writes itself.
        "task.delegate",
    }
    assert readonly_blocked_tool_names() == expected

    registry = build_default_tool_registry()
    flagged = {
        name
        for name, spec in registry.list().items()
        if bool(getattr(spec, "block_under_readonly", False))
    }
    assert flagged == expected
