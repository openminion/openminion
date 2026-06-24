from __future__ import annotations

import json
from pathlib import Path

import pytest

from openminion.cli.theme import (
    DARK,
    LIGHT,
    available_theme_names,
    lookup_theme,
    persisted_theme_path,
    read_persisted_theme,
    resolve_theme,
    write_persisted_theme,
)
from openminion.cli.theme.selection import PERSISTED_THEME_FILENAME


# ── Lookup helper ────────────────────────────────────────────────────────────


def test_lookup_theme_case_insensitive() -> None:
    assert lookup_theme("dark") is DARK
    assert lookup_theme("DARK") is DARK
    assert lookup_theme("  Light  ") is LIGHT


def test_lookup_theme_returns_none_for_unknown() -> None:
    assert lookup_theme("not-a-real-theme") is None
    assert lookup_theme("") is None
    assert lookup_theme(None) is None


def test_available_theme_names_sorted() -> None:
    names = available_theme_names()
    assert names == sorted(names)
    assert "dark" in names
    assert "light" in names


# ── Persistence ──────────────────────────────────────────────────────────────


def test_persisted_theme_path_is_under_data_root(tmp_path: Path) -> None:
    p = persisted_theme_path(tmp_path)
    assert p == tmp_path / "cli" / PERSISTED_THEME_FILENAME


def test_write_persisted_theme_creates_file_and_parent(tmp_path: Path) -> None:
    written = write_persisted_theme(tmp_path, "light")
    assert written.exists()
    payload = json.loads(written.read_text(encoding="utf-8"))
    assert payload == {"theme": "light"}


def test_write_persisted_theme_rejects_unknown_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError) as exc_info:
        write_persisted_theme(tmp_path, "neon")
    assert "neon" in str(exc_info.value)
    assert not persisted_theme_path(tmp_path).exists()


def test_read_persisted_theme_returns_none_when_missing(tmp_path: Path) -> None:
    assert read_persisted_theme(tmp_path) is None


def test_read_persisted_theme_returns_none_on_corrupt_file(tmp_path: Path) -> None:
    p = persisted_theme_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not valid json", encoding="utf-8")
    assert read_persisted_theme(tmp_path) is None


def test_read_persisted_theme_returns_none_on_wrong_shape(tmp_path: Path) -> None:
    p = persisted_theme_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("[]", encoding="utf-8")
    assert read_persisted_theme(tmp_path) is None


def test_round_trip_persistence(tmp_path: Path) -> None:
    write_persisted_theme(tmp_path, "light")
    assert read_persisted_theme(tmp_path) == "light"
    write_persisted_theme(tmp_path, "dark")
    assert read_persisted_theme(tmp_path) == "dark"


# ── Precedence ───────────────────────────────────────────────────────────────


def test_default_is_dark() -> None:
    assert resolve_theme(env_value="") is DARK


def test_cli_flag_wins_over_session_override() -> None:
    resolved = resolve_theme(
        cli_flag="dark",
        session_override="light",
        env_value="light",
    )
    assert resolved is DARK


def test_session_override_wins_over_env(tmp_path: Path) -> None:
    write_persisted_theme(tmp_path, "dark")
    resolved = resolve_theme(
        cli_flag=None,
        session_override="light",
        env_value="dark",
        data_root=tmp_path,
    )
    assert resolved is LIGHT


def test_env_wins_over_persisted(tmp_path: Path) -> None:
    write_persisted_theme(tmp_path, "dark")
    resolved = resolve_theme(
        env_value="light",
        data_root=tmp_path,
    )
    assert resolved is LIGHT


def test_persisted_wins_over_default(tmp_path: Path) -> None:
    write_persisted_theme(tmp_path, "light")
    resolved = resolve_theme(env_value="", data_root=tmp_path)
    assert resolved is LIGHT


def test_unknown_at_one_layer_falls_through(tmp_path: Path) -> None:
    write_persisted_theme(tmp_path, "light")
    resolved = resolve_theme(
        cli_flag="not-a-theme",
        session_override="also-bogus",
        env_value="still-bogus",
        data_root=tmp_path,
    )
    assert resolved is LIGHT


def test_env_var_read_through_shared_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openminion.cli.constants import OPENMINION_THEME_VARIANT_ENV

    monkeypatch.setenv(OPENMINION_THEME_VARIANT_ENV, "light")
    resolved = resolve_theme(data_root=tmp_path)
    assert resolved is LIGHT


def test_env_value_empty_string_treated_as_unset() -> None:
    resolved = resolve_theme(env_value="")
    assert resolved is DARK
