from __future__ import annotations

import pytest

from openminion.base.config.parse import (
    _as_bool,
    _as_float,
    _as_int,
    _as_int_list,
    _as_optional_float,
    as_bool,
    as_float,
    as_int,
    as_int_list,
    as_optional_float,
    positive_int,
    split_comma_tokens,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", []),
        (None, []),
        ("alpha", ["alpha"]),
        (" alpha, beta ,,gamma ", ["alpha", "beta", "gamma"]),
        (123, ["123"]),
    ],
)
def test_split_comma_tokens_trims_and_drops_empty_tokens(value, expected) -> None:
    assert split_comma_tokens(value) == expected


def test_split_comma_tokens_can_feed_set_callers() -> None:
    assert set(split_comma_tokens(" alpha, beta,alpha ,, ")) == {"alpha", "beta"}


@pytest.mark.parametrize(
    ("value", "default", "expected"),
    [("7", 1, 7), (None, 3, 3), ("bad", 4, 4)],
)
def test_as_int_matches_legacy_config_alias(value, default, expected) -> None:
    assert as_int(value, default) == expected
    assert _as_int(value, default) == expected


@pytest.mark.parametrize(
    ("value", "default", "expected"),
    [("1.5", 0.0, 1.5), (None, 2.0, 2.0), ("bad", 3.0, 3.0)],
)
def test_as_float_matches_legacy_config_alias(value, default, expected) -> None:
    assert as_float(value, default) == expected
    assert _as_float(value, default) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1.5", 1.5), (None, None), ("bad", None)],
)
def test_as_optional_float_matches_legacy_config_alias(value, expected) -> None:
    assert as_optional_float(value) == expected
    assert _as_optional_float(value) == expected


@pytest.mark.parametrize(
    ("value", "default", "expected"),
    [
        (True, False, True),
        ("yes", False, True),
        ("off", True, False),
        (2, False, True),
        ("maybe", True, True),
    ],
)
def test_as_bool_matches_legacy_config_alias(value, default, expected) -> None:
    assert as_bool(value, default) == expected
    assert _as_bool(value, default) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1, 2, bad, 0, -3, 4", [1, 2, 4]), (["5", "bad", 6], [5, 6]), (None, [])],
)
def test_as_int_list_matches_legacy_config_alias(value, expected) -> None:
    assert as_int_list(value) == expected
    assert _as_int_list(value) == expected


@pytest.mark.parametrize(("value", "expected"), [("3", 3), ("0", None), ("bad", None)])
def test_positive_int_matches_legacy_config_alias(value, expected) -> None:
    assert positive_int(value) == expected
