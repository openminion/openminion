from __future__ import annotations

import pytest

from openminion.cli.tui.focus.tokens import (
    AtToken,
    active_at_token,
    cursor_offset_for_text_area,
)


def test_bare_at_at_cursor_returns_empty_query() -> None:
    result = active_at_token("@", cursor=1)
    assert result is not None
    assert result.text == "@"
    assert result.start == 0
    assert result.end == 1
    assert result.query == ""


def test_at_followed_by_chars_returns_query_text() -> None:
    result = active_at_token("@sr", cursor=3)
    assert result is not None
    assert result.text == "@sr"
    assert result.start == 0
    assert result.end == 3
    assert result.query == "sr"


def test_at_token_mid_text_after_whitespace() -> None:
    text = "please review @sr"
    cursor = len(text)
    result = active_at_token(text, cursor)
    assert result is not None
    assert result.text == "@sr"
    assert result.query == "sr"
    assert result.start == text.index("@")
    assert result.end == cursor


def test_email_address_does_not_trigger_overlay() -> None:
    text = "email@example.com"
    cursor = len(text)
    result = active_at_token(text, cursor)
    assert result is None, (
        f"`email@example.com` must NOT activate the @-overlay; got {result}"
    )


def test_completed_path_with_trailing_space_deactivates_overlay() -> None:
    text = "please review @src/foo.py "
    cursor = len(text)
    result = active_at_token(text, cursor)
    assert result is None


def test_active_at_token_in_middle_of_long_path() -> None:
    text = "please review @src/foo.py here"
    cursor = text.index("@") + len("@src/foo.py")
    result = active_at_token(text, cursor)
    assert result is not None
    assert result.text == "@src/foo.py"
    assert result.query == "src/foo.py"


def test_no_at_in_text_returns_none() -> None:
    result = active_at_token("hello world", cursor=11)
    assert result is None


def test_cursor_at_zero_returns_none() -> None:
    assert active_at_token("@hello", cursor=0) is None
    assert active_at_token("hello", cursor=0) is None
    assert active_at_token("", cursor=0) is None


def test_cursor_clamping_for_out_of_range_input() -> None:
    text = "@sr"
    big = active_at_token(text, cursor=99)
    assert big is not None and big.text == "@sr"
    assert active_at_token(text, cursor=-5) is None


def test_walk_back_stops_at_tab_and_newline_too() -> None:
    for sep in (" ", "\t", "\n"):
        text = f"prev{sep}@new"
        cursor = len(text)
        result = active_at_token(text, cursor)
        assert result is not None, f"sep={sep!r}"
        assert result.text == "@new", f"sep={sep!r}"


def test_at_token_is_frozen_dataclass() -> None:
    token = active_at_token("@sr", cursor=3)
    assert isinstance(token, AtToken)
    with pytest.raises(Exception):
        token.start = 99  # type: ignore[misc]


def test_text_area_cursor_offset_single_line() -> None:
    assert cursor_offset_for_text_area("hello world", 0, 0) == 0
    assert cursor_offset_for_text_area("hello world", 0, 5) == 5
    assert cursor_offset_for_text_area("hello world", 0, 11) == 11


def test_text_area_cursor_offset_multi_line() -> None:
    text = "line0\nline1\nline2"
    assert cursor_offset_for_text_area(text, 0, 5) == 5
    assert cursor_offset_for_text_area(text, 1, 0) == 6
    assert cursor_offset_for_text_area(text, 2, 0) == 12
    assert cursor_offset_for_text_area(text, 2, 5) == 17


def test_text_area_cursor_offset_clamps_oversized_line() -> None:
    text = "a\nb"
    assert cursor_offset_for_text_area(text, line=99, col=0) == 2
    assert cursor_offset_for_text_area(text, line=0, col=99) == 1


def test_text_area_cursor_offset_empty_text() -> None:
    assert cursor_offset_for_text_area("", line=0, col=0) == 0
    assert cursor_offset_for_text_area("", line=99, col=99) == 0


def test_textarea_at_token_works_through_offset_conversion() -> None:
    text = "first line\nplease review\n@sr"
    offset = cursor_offset_for_text_area(text, line=2, col=3)
    result = active_at_token(text, offset)
    assert result is not None
    assert result.text == "@sr"
    assert result.query == "sr"
