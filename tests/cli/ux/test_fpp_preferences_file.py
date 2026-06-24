from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterator

import pytest

from openminion.cli.ux import verbosity as v
from openminion.cli.ux.verbosity import (
    _read_preferences_file,
    _resolve_preferences_file_path,
    resolve_progress,
    resolve_verbosity,
)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for key in (
        "OPENMINION_VERBOSITY",
        "OPENMINION_FOCUS_VERBOSITY",
        "OPENMINION_PROGRESS",
        "OPENMINION_FOCUS_PLAIN_SPINNER",
        "NO_COLOR",
        "OPENMINION_DATA_ROOT",
    ):
        monkeypatch.delenv(key, raising=False)
    yield


@pytest.fixture
def temp_prefs_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    candidate = tmp_path / "focus_prefs.toml"
    monkeypatch.setattr(v, "_resolve_preferences_file_path", lambda: candidate)
    yield candidate


def _args(
    *, verbosity: str | None = None, progress: str | None = None
) -> argparse.Namespace:
    return argparse.Namespace(verbosity=verbosity, progress=progress)


def test_resolve_verbosity_file_absent_returns_default(
    clean_env: None,
    temp_prefs_file: Path,
) -> None:
    assert not temp_prefs_file.exists()
    assert resolve_verbosity(_args()) == "normal"


def test_resolve_progress_file_absent_returns_default(
    clean_env: None,
    temp_prefs_file: Path,
) -> None:
    assert not temp_prefs_file.exists()
    assert resolve_progress(_args(), default="full") == "full"


def test_read_preferences_file_absent_returns_empty(
    clean_env: None,
    temp_prefs_file: Path,
) -> None:
    assert _read_preferences_file() == {}


def test_file_verbosity_quiet_resolves(
    clean_env: None,
    temp_prefs_file: Path,
) -> None:
    temp_prefs_file.write_text('verbosity = "quiet"\n')
    assert resolve_verbosity(_args()) == "quiet"


def test_file_verbosity_verbose_resolves(
    clean_env: None,
    temp_prefs_file: Path,
) -> None:
    temp_prefs_file.write_text('verbosity = "verbose"\n')
    assert resolve_verbosity(_args()) == "verbose"


def test_file_progress_off_resolves(
    clean_env: None,
    temp_prefs_file: Path,
) -> None:
    temp_prefs_file.write_text('progress = "off"\n')
    assert resolve_progress(_args(), default="full") == "off"


def test_file_progress_minimal_resolves(
    clean_env: None,
    temp_prefs_file: Path,
) -> None:
    temp_prefs_file.write_text('progress = "minimal"\n')
    assert resolve_progress(_args(), default="full") == "minimal"


def test_file_both_keys_resolve(
    clean_env: None,
    temp_prefs_file: Path,
) -> None:
    temp_prefs_file.write_text('verbosity = "quiet"\nprogress = "minimal"\n')
    assert resolve_verbosity(_args()) == "quiet"
    assert resolve_progress(_args(), default="full") == "minimal"


def test_file_case_insensitive(
    clean_env: None,
    temp_prefs_file: Path,
) -> None:
    temp_prefs_file.write_text('verbosity = "VERBOSE"\n')
    assert resolve_verbosity(_args()) == "verbose"


def test_flag_beats_file_verbosity(
    clean_env: None,
    temp_prefs_file: Path,
) -> None:
    temp_prefs_file.write_text('verbosity = "quiet"\n')
    assert resolve_verbosity(_args(verbosity="verbose")) == "verbose"


def test_flag_beats_file_progress(
    clean_env: None,
    temp_prefs_file: Path,
) -> None:
    temp_prefs_file.write_text('progress = "off"\n')
    assert resolve_progress(_args(progress="full"), default="full") == "full"


def test_env_beats_file_verbosity(
    clean_env: None,
    temp_prefs_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    temp_prefs_file.write_text('verbosity = "quiet"\n')
    monkeypatch.setenv("OPENMINION_VERBOSITY", "verbose")
    assert resolve_verbosity(_args()) == "verbose"


def test_env_beats_file_progress(
    clean_env: None,
    temp_prefs_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    temp_prefs_file.write_text('progress = "off"\n')
    monkeypatch.setenv("OPENMINION_PROGRESS", "minimal")
    assert resolve_progress(_args(), default="full") == "minimal"


def test_legacy_env_beats_file_verbosity(
    clean_env: None,
    temp_prefs_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    temp_prefs_file.write_text('verbosity = "quiet"\n')
    monkeypatch.setenv("OPENMINION_FOCUS_VERBOSITY", "verbose")
    assert resolve_verbosity(_args()) == "verbose"


def test_no_color_beats_file_progress(
    clean_env: None,
    temp_prefs_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    temp_prefs_file.write_text('progress = "full"\n')
    monkeypatch.setenv("NO_COLOR", "1")
    assert resolve_progress(_args(), default="full") == "minimal"


def test_malformed_toml_falls_through_with_warning(
    clean_env: None,
    temp_prefs_file: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    temp_prefs_file.write_text("this is not valid toml = = = =\n")
    result = resolve_verbosity(_args())
    assert result == "normal"
    err = capsys.readouterr().err
    assert "failed to read focus preferences" in err
    assert str(temp_prefs_file) in err


def test_malformed_toml_returns_empty(
    clean_env: None,
    temp_prefs_file: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    temp_prefs_file.write_text("garbage\n")
    assert _read_preferences_file() == {}
    assert "failed to read focus preferences" in capsys.readouterr().err


def test_garbage_verbosity_value_falls_through_with_warning(
    clean_env: None,
    temp_prefs_file: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    temp_prefs_file.write_text('verbosity = "loud"\n')
    result = resolve_verbosity(_args())
    assert result == "normal"
    err = capsys.readouterr().err
    assert "unrecognized verbosity='loud'" in err
    assert "quiet, normal, verbose" in err


def test_garbage_progress_value_falls_through_with_warning(
    clean_env: None,
    temp_prefs_file: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    temp_prefs_file.write_text('progress = "louder"\n')
    result = resolve_progress(_args(), default="full")
    assert result == "full"
    err = capsys.readouterr().err
    assert "unrecognized progress='louder'" in err
    assert "full, minimal, off" in err


def test_non_string_value_ignored(
    clean_env: None,
    temp_prefs_file: Path,
) -> None:
    temp_prefs_file.write_text("verbosity = 123\n")
    assert _read_preferences_file() == {}


def test_unknown_key_ignored_valid_resolves(
    clean_env: None,
    temp_prefs_file: Path,
) -> None:
    temp_prefs_file.write_text(
        'verbosity = "quiet"\ntheme = "dark"\nunknown_future_key = "x"\n'
    )
    assert resolve_verbosity(_args()) == "quiet"
    prefs = _read_preferences_file()
    assert prefs == {"verbosity": "quiet"}


def test_resolve_path_uses_openminion_data_root_env(
    clean_env: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_DATA_ROOT", str(tmp_path / ".openminion"))
    path = _resolve_preferences_file_path()
    assert path == tmp_path / ".openminion" / "focus_prefs.toml"


def test_resolve_path_falls_back_to_home_default(
    clean_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)
    path = _resolve_preferences_file_path()
    assert path.name == "focus_prefs.toml"
    assert ".openminion" in str(path)


def test_existing_flag_precedence_unaffected(
    clean_env: None,
    temp_prefs_file: Path,
) -> None:
    assert resolve_verbosity(_args(verbosity="verbose")) == "verbose"


def test_existing_env_precedence_unaffected(
    clean_env: None,
    temp_prefs_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_VERBOSITY", "quiet")
    assert resolve_verbosity(_args()) == "quiet"


def test_existing_no_color_precedence_unaffected(
    clean_env: None,
    temp_prefs_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    assert resolve_progress(_args(), default="full") == "minimal"
