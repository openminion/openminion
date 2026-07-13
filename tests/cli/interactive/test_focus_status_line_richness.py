from __future__ import annotations

from pathlib import Path

import pytest
from textual.css.query import QueryError

from openminion.cli.interactive.widgets.status_line import FocusStatusLine
from openminion.cli.presentation.git.branch import detect_branch


def test_detect_branch_returns_none_for_missing_directory() -> None:
    assert detect_branch("/this/path/does/not/exist-xyz") is None


def test_detect_branch_returns_none_for_non_git_directory(tmp_path: Path) -> None:
    assert detect_branch(tmp_path) is None


def test_detect_branch_handles_empty_input() -> None:
    assert detect_branch("") is None
    assert detect_branch(None) is None  # type: ignore[arg-type]


def test_detect_branch_returns_branch_for_real_git_repo() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    branch = detect_branch(repo_root)
    assert isinstance(branch, str)
    assert len(branch) > 0


def _idle_text(line: FocusStatusLine) -> str:
    return line._text()


def test_idle_text_includes_all_segments_when_set() -> None:
    line = FocusStatusLine()
    line.set_state(
        state="idle",
        model="anthropic/claude",
        cwd="~/repos/foo",
        branch="main",
        tokens="1234/200000",
        cost="$0.05",
    )
    text = _idle_text(line)
    assert "model: anthropic/claude" in text
    assert "cwd: ~/repos/foo" in text
    assert "git: main" in text
    assert "tokens: 1234/200000" in text
    assert "cost: $0.05" in text
    assert " |  " not in text
    assert "||" not in text
    assert text.index("model:") < text.index("^P palette")


def test_idle_text_includes_goal_loop_segment() -> None:
    line = FocusStatusLine()
    line.set_state(
        state="idle",
        model="openai/gpt",
        goal_loop="goal: active turn 2 · tests still failing",
    )
    text = _idle_text(line)
    assert "goal: active turn 2" in text
    assert text.index("model:") < text.index("goal: active")


def test_idle_text_omits_empty_segments_cleanly() -> None:
    line = FocusStatusLine()
    line.set_state(
        state="idle",
        model="anthropic/claude",
        cwd="~/here",
        branch="",  # not a git dir → omitted
        tokens="100",
        cost="",
    )
    text = _idle_text(line)
    assert "model: anthropic/claude" in text
    assert "cwd: ~/here" in text
    assert "tokens: 100" in text
    assert "git:" not in text
    assert "cost:" not in text
    assert " |  | " not in text


def test_idle_text_renders_only_hints_when_no_segments() -> None:
    line = FocusStatusLine()
    line.set_state(state="idle")
    text = _idle_text(line)
    assert "model:" not in text
    assert "^P palette" in text


def test_responding_state_preserves_runtime_richness() -> None:
    line = FocusStatusLine()
    line.set_state(
        state="responding",
        elapsed_seconds=2.5,
        model="anthropic/claude",
        cwd="~/here",
        branch="main",
        tokens="100",
        custom="planning",
    )
    text = _idle_text(line)
    assert "responding" in text
    assert "2s" in text
    assert "Esc cancel" in text
    assert "model: anthropic/claude" in text
    assert "cwd: ~/here" in text
    assert "git: main" in text
    assert "tokens: 100" in text
    assert "status: planning" in text


def test_tool_state_preserves_runtime_richness() -> None:
    line = FocusStatusLine()
    line.set_state(
        state="tool",
        tool_name="exec.run",
        elapsed_seconds=10.0,
        model="anthropic/claude",
        cwd="~/here",
        tokens="200",
    )
    text = _idle_text(line)
    assert "exec.run" in text
    assert "Esc cancel" in text
    assert "model: anthropic/claude" in text
    assert "cwd: ~/here" in text
    assert "tokens: 200" in text


def test_busy_state_shows_queued_count() -> None:
    line = FocusStatusLine()
    line.set_state(state="responding", elapsed_seconds=1.0, queued_count=2)
    text = _idle_text(line)
    assert "queued: 2" in text
    assert "Esc cancel" in text


def test_initializing_state_is_explicit() -> None:
    line = FocusStatusLine()
    line.set_state(state="initializing")

    assert _idle_text(line) == "● starting session"


def test_set_state_partial_update_preserves_other_segments() -> None:
    line = FocusStatusLine()
    line.set_state(
        state="idle",
        model="m1",
        cwd="c1",
        branch="b1",
        tokens="t1",
    )
    line.set_state(tokens="t2")
    text = _idle_text(line)
    assert "model: m1" in text
    assert "cwd: c1" in text
    assert "git: b1" in text
    assert "tokens: t2" in text


def test_set_state_explicit_empty_clears_segment() -> None:
    line = FocusStatusLine()
    line.set_state(state="idle", model="m1", branch="main")
    assert "git: main" in _idle_text(line)
    line.set_state(branch="")
    assert "git:" not in _idle_text(line)


def test_idle_text_renders_combined_permission_posture() -> None:
    line = FocusStatusLine()
    line.set_state(
        state="idle",
        permission_mode="readonly",
        action_policy_mode="auto",
    )
    assert "permissions: read-only + auto" in _idle_text(line)


def test_idle_text_renders_full_access_as_warning_label() -> None:
    line = FocusStatusLine()
    line.set_state(
        state="idle",
        permission_mode="bypass",
        action_policy_mode="bypass",
    )
    assert "permissions: full access" in _idle_text(line)


def test_refresh_ignores_missing_status_label(monkeypatch: pytest.MonkeyPatch) -> None:
    line = FocusStatusLine()

    def _raise_query_error(*args, **kwargs):
        raise QueryError("missing status label")

    monkeypatch.setattr(line, "query_one", _raise_query_error)
    line._refresh()
