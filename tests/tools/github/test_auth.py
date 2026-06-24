from __future__ import annotations

import pytest

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.tools.github.auth import auth_invalid_error, require_github_pat
from openminion.tools.github.config import GithubToolProfileConfig


def test_require_pat_default_env_success() -> None:
    env = {"GITHUB_TOKEN": "tok-default"}
    assert require_github_pat(env=env) == "tok-default"


def test_require_pat_missing_raises_auth_required() -> None:
    with pytest.raises(ToolRuntimeError) as exc:
        require_github_pat(env={})
    assert exc.value.code == "AUTH_REQUIRED"
    assert exc.value.details.get("reason_code") == "github_pat_missing"
    assert exc.value.details.get("env_name") == "GITHUB_TOKEN"


def test_require_pat_profile_token_env_override() -> None:
    profile = GithubToolProfileConfig(token_env="MY_PROFILE_PAT")
    env = {"MY_PROFILE_PAT": "tok-override", "GITHUB_TOKEN": "tok-default"}
    assert require_github_pat(profile=profile, env=env) == "tok-override"


def test_require_pat_explicit_token_env_beats_profile() -> None:
    profile = GithubToolProfileConfig(token_env="PROFILE_TOKEN")
    env = {
        "PROFILE_TOKEN": "tok-profile",
        "EXPLICIT_TOKEN": "tok-explicit",
        "GITHUB_TOKEN": "tok-default",
    }
    assert (
        require_github_pat(profile=profile, token_env="EXPLICIT_TOKEN", env=env)
        == "tok-explicit"
    )


def test_require_pat_profile_mapping_form_supported() -> None:
    env = {"FROM_MAP": "tok-from-map"}
    assert (
        require_github_pat(profile={"token_env": "FROM_MAP"}, env=env) == "tok-from-map"
    )


def test_require_pat_missing_with_profile_records_overridden_env_name() -> None:
    profile = GithubToolProfileConfig(token_env="MISSING_PROFILE_PAT")
    with pytest.raises(ToolRuntimeError) as exc:
        require_github_pat(profile=profile, env={})
    assert exc.value.code == "AUTH_REQUIRED"
    assert exc.value.details.get("env_name") == "MISSING_PROFILE_PAT"


def test_auth_invalid_error_shape() -> None:
    err = auth_invalid_error(status_code=401, body_excerpt="Bad credentials")
    assert err.code == "AUTH_INVALID"
    assert err.details.get("reason_code") == "github_pat_invalid"
    assert err.details.get("status_code") == 401
    assert err.details.get("body_excerpt") == "Bad credentials"


def test_profile_config_from_mapping_handles_none() -> None:
    cfg = GithubToolProfileConfig.from_mapping(None)
    assert cfg.token_env == ""
    assert cfg.resolved_token_env() is None


def test_profile_config_from_mapping_strips_whitespace() -> None:
    cfg = GithubToolProfileConfig.from_mapping({"token_env": "  X  "})
    assert cfg.token_env == "X"
    assert cfg.resolved_token_env() == "X"
