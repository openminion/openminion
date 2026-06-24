from __future__ import annotations

from openminion.tools.github.env import (
    get_github_api_base_url,
    get_github_timeout_seconds,
    get_github_token,
)


def test_get_github_token_reads_default_env_name() -> None:
    env = {"GITHUB_TOKEN": "tok-default"}
    assert get_github_token(env=env) == "tok-default"


def test_get_github_token_token_env_override_redirects_lookup() -> None:
    env = {"MY_PROFILE_TOKEN": "tok-override", "GITHUB_TOKEN": "tok-default"}
    assert get_github_token(token_env="MY_PROFILE_TOKEN", env=env) == "tok-override"


def test_get_github_token_missing_returns_empty_string() -> None:
    # Empty string lets the auth helper raise AUTH_REQUIRED deterministically.
    assert get_github_token(env={}) == ""


def test_default_api_base_url_resolves_when_unset() -> None:
    assert get_github_api_base_url(env={}) == "https://api.github.com"


def test_api_base_url_override_takes_effect() -> None:
    env = {"GITHUB_API_BASE_URL": "https://github.example.com/api/v3"}
    assert get_github_api_base_url(env=env) == "https://github.example.com/api/v3"


def test_default_timeout_seconds_resolves_when_unset() -> None:
    assert get_github_timeout_seconds(env={}) == 30.0


def test_timeout_seconds_override_takes_effect() -> None:
    env = {"GITHUB_TIMEOUT_SECONDS": "12.5"}
    assert get_github_timeout_seconds(env=env) == 12.5
