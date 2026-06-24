from __future__ import annotations

from openminion.modules.memory.runtime.normalized_keys import (
    BOUNDED_CATEGORIES,
    build_normalized_key,
    is_valid_normalized_key,
    normalize_slug,
    parse_normalized_key,
)


def test_bounded_categories_are_afe_kinds() -> None:
    assert BOUNDED_CATEGORIES == {"fact", "user_preference", "task"}


def test_build_normalized_key_common_cases() -> None:
    for kind, slug, expected in (
        ("fact", "user name", "fact:user_name"),
        ("user_preference", "Response Style!!", "user_preference:response_style"),
        ("fact", "", "fact:unspecified"),
    ):
        assert build_normalized_key(kind=kind, slug=slug) == expected


def test_build_normalized_key_for_unknown_kind_uses_custom_prefix() -> None:
    key = build_normalized_key(kind="github_handle", slug="octocat")
    assert key.startswith("fact:custom:")
    assert "github_handle" in key
    assert "octocat" in key


def test_build_normalized_key_truncates_long_slugs() -> None:
    key = build_normalized_key(kind="fact", slug="a" * 200)
    assert len(key) <= 128
    assert key.startswith("fact:")


def test_is_valid_normalized_key_cases() -> None:
    for key, expected in (
        ("fact:user_name", True),
        ("user_preference:response_style", True),
        ("task:deploy_auth", True),
        ("", False),
        ("unknown:x", False),
        ("NOT A KEY", False),
        ("fact:", False),
        (":slug", False),
    ):
        assert is_valid_normalized_key(key) is expected


def test_parse_normalized_key_returns_none_for_invalid() -> None:
    assert parse_normalized_key("not-valid") is None
    assert parse_normalized_key("") is None


def test_parse_normalized_key_splits_bounded() -> None:
    assert parse_normalized_key("fact:user_name") == ("fact", "user_name")


def test_normalize_slug_collapses_separators() -> None:
    assert normalize_slug("Hello  --  World!!") == "hello_world"


def test_build_normalized_key_is_deterministic() -> None:
    first = build_normalized_key(kind="fact", slug="User Email")
    second = build_normalized_key(kind="fact", slug="user email")
    assert first == second
