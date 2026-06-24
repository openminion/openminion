from __future__ import annotations

from openminion.cli.status.activity_ledger import CollapsedOutput, collapse_output


def test_collapse_output_short_body_returns_all_lines_not_truncated() -> None:
    result = collapse_output("line1\nline2\nline3", max_lines=6)
    assert isinstance(result, CollapsedOutput)
    assert result.visible_lines == ("line1", "line2", "line3")
    assert result.hidden_line_count == 0
    assert result.truncated is False
    assert result.expand_hint == ""


def test_collapse_output_long_body_truncates_with_hint() -> None:
    body = "\n".join(f"line{i}" for i in range(1, 11))  # 10 lines
    result = collapse_output(body, max_lines=6)
    assert result.visible_lines == tuple(f"line{i}" for i in range(1, 7))
    assert result.hidden_line_count == 4
    assert result.truncated is True
    assert result.expand_hint == "… +4 lines (use /expand to see all)"


def test_collapse_output_empty_body_renders_placeholder() -> None:
    result = collapse_output("", max_lines=6)
    assert result.visible_lines == ("(no output)",)
    assert result.hidden_line_count == 0
    assert result.truncated is False


def test_collapse_output_zero_max_lines_hides_everything() -> None:
    body = "a\nb\nc"
    result = collapse_output(body, max_lines=0)
    assert result.visible_lines == ()
    assert result.hidden_line_count == 3
    assert result.truncated is True
    assert "+3 lines" in result.expand_hint


def test_collapse_output_negative_max_lines_clamps_to_zero() -> None:
    result = collapse_output("a\nb", max_lines=-5)
    assert result.visible_lines == ()
    assert result.hidden_line_count == 2
    assert result.truncated is True


def test_collapse_output_custom_expand_label() -> None:
    body = "\n".join(f"l{i}" for i in range(1, 5))
    result = collapse_output(body, max_lines=1, expand_label="show more")
    assert result.expand_hint == "… +3 lines (show more)"


def test_collapse_output_strips_trailing_newlines() -> None:
    result = collapse_output("a\nb\nc\n\n\n", max_lines=10)
    assert result.visible_lines == ("a", "b", "c")
    assert result.truncated is False


def test_collapse_output_preserves_blank_lines_in_body() -> None:
    body = "a\n\nb\n\nc"
    result = collapse_output(body, max_lines=10)
    assert result.visible_lines == ("a", "", "b", "", "c")


def test_collapse_output_custom_empty_placeholder() -> None:
    result = collapse_output("", max_lines=6, empty_placeholder="(empty)")
    assert result.visible_lines == ("(empty)",)


def test_collapsed_output_dataclass_is_frozen_and_hashable() -> None:
    result = collapse_output("a\nb", max_lines=6)
    # Frozen → cannot mutate
    try:
        result.hidden_line_count = 99  # type: ignore[misc]
        raised = False
    except Exception:
        raised = True
    assert raised
