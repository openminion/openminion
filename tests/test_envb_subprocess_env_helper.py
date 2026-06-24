from __future__ import annotations

import os
from unittest import mock

import pytest

from openminion.base.config.env.subprocess import (
    SUBPROCESS_ENV_ALLOWLIST_ENV,
    build_subprocess_env,
)


def test_inherits_allowlisted_parent_env_when_no_overlay():
    with mock.patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=False):
        env = build_subprocess_env()
    assert env.get("PATH") == "/usr/bin"


def test_does_not_inherit_unlisted_parent_secret():
    with mock.patch.dict(
        os.environ,
        {"AWS_SECRET_ACCESS_KEY": "parent-secret"},
        clear=False,
    ):
        env = build_subprocess_env()
    assert "AWS_SECRET_ACCESS_KEY" not in env


def test_operator_allowlist_can_admit_extra_parent_key():
    with mock.patch.dict(
        os.environ,
        {
            SUBPROCESS_ENV_ALLOWLIST_ENV: "OPENMINION_TEST_PARENT",
            "OPENMINION_TEST_PARENT": "allowed",
        },
        clear=False,
    ):
        env = build_subprocess_env()
    assert env["OPENMINION_TEST_PARENT"] == "allowed"


def test_returns_fresh_dict_not_os_environ_alias():
    env = build_subprocess_env()
    assert env is not os.environ
    env["ENVB03_MUTATED"] = "should_not_leak"
    assert "ENVB03_MUTATED" not in os.environ


def test_overlay_wins_over_allowlisted_parent_env():
    with mock.patch.dict(os.environ, {"PATH": "/parent/bin"}, clear=False):
        env = build_subprocess_env(overlay={"PATH": "/child/bin"})
    assert env["PATH"] == "/child/bin"


def test_overlay_adds_keys_not_in_parent():
    with mock.patch.dict(os.environ, {}, clear=False):
        env = build_subprocess_env(overlay={"ENVB03_NEW_KEY": "fresh"})
    assert env["ENVB03_NEW_KEY"] == "fresh"


def test_inherit_parent_false_drops_parent_env():
    with mock.patch.dict(os.environ, {"ENVB03_PARENT_ONLY": "parent"}, clear=False):
        env = build_subprocess_env(inherit_parent=False)
    assert "ENVB03_PARENT_ONLY" not in env
    assert env == {}


def test_inherit_parent_false_with_overlay_yields_overlay_only():
    with mock.patch.dict(os.environ, {"ENVB03_PARENT_ONLY": "parent"}, clear=False):
        env = build_subprocess_env(
            overlay={"ENVB03_ONLY_OVERLAY": "child"}, inherit_parent=False
        )
    assert env == {"ENVB03_ONLY_OVERLAY": "child"}


def test_overlay_keys_and_values_are_coerced_to_str():
    env = build_subprocess_env(
        overlay={"ENVB03_INT_VALUE": 42, 123: "key_was_int"},  # type: ignore[dict-item]
        inherit_parent=False,
    )
    assert env["ENVB03_INT_VALUE"] == "42"
    assert env["123"] == "key_was_int"
    assert all(isinstance(k, str) for k in env)
    assert all(isinstance(v, str) for v in env.values())


def test_none_overlay_is_equivalent_to_no_overlay():
    with mock.patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=False):
        env_none = build_subprocess_env(overlay=None)
        env_omitted = build_subprocess_env()
    assert env_none == env_omitted


def test_repeated_calls_produce_independent_dicts():
    env_a = build_subprocess_env(overlay={"ENVB03_K": "a"}, inherit_parent=False)
    env_b = build_subprocess_env(overlay={"ENVB03_K": "b"}, inherit_parent=False)
    env_a["ENVB03_K"] = "mutated"
    assert env_b["ENVB03_K"] == "b"


@pytest.mark.parametrize(
    "overlay,inherit,expected_extra",
    [
        ({"A": "1"}, True, "A=1"),
        ({"A": "1", "B": "2"}, False, "B=2"),
        ({}, True, None),
        ({}, False, None),
    ],
)
def test_parametrized_combinations(overlay, inherit, expected_extra):
    env = build_subprocess_env(overlay=overlay or None, inherit_parent=inherit)
    if expected_extra is None:
        # Just verify structure is dict[str,str]
        assert isinstance(env, dict)
    else:
        key, value = expected_extra.split("=")
        assert env[key] == value
