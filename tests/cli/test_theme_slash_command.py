from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from openminion.cli.chat.ui import handle_theme
from openminion.cli.presentation import styles
from openminion.cli.theme import DARK
from openminion.cli.theme import (
    persisted_theme_path,
    read_persisted_theme,
)


@pytest.fixture(autouse=True)
def _restore_active_theme():
    original_codes = dict(styles._ANSI_CODES)
    original_name = styles.get_active_theme_name()
    styles.set_active_theme(DARK)
    yield
    styles._ANSI_CODES.clear()
    styles._ANSI_CODES.update(original_codes)
    styles._ACTIVE_THEME_NAME = original_name


def _capture_handle_theme(*, line: str, data_root: Path | None) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        handle_theme(line=line, data_root=data_root)
    return buf.getvalue()


def test_bare_theme_shows_active_name(tmp_path: Path) -> None:
    out = _capture_handle_theme(line="/theme", data_root=tmp_path)
    assert "Active Theme" in out
    assert "dark" in out.lower()


def test_theme_list_enumerates_shipped_themes(tmp_path: Path) -> None:
    out = _capture_handle_theme(line="/theme list", data_root=tmp_path)
    assert "dark" in out
    assert "light" in out
    assert "(active)" in out


def test_theme_switch_session_local(tmp_path: Path) -> None:
    out = _capture_handle_theme(line="/theme light", data_root=tmp_path)
    assert "session-local" in out
    assert styles.get_active_theme_name() == "light"
    assert not persisted_theme_path(tmp_path).exists()


def test_theme_save_persists_and_switches(tmp_path: Path) -> None:
    out = _capture_handle_theme(line="/theme save light", data_root=tmp_path)
    assert "saved" in out.lower()
    assert styles.get_active_theme_name() == "light"
    persisted = read_persisted_theme(tmp_path)
    assert persisted == "light"


def test_theme_save_without_name_prints_usage(tmp_path: Path) -> None:
    out = _capture_handle_theme(line="/theme save", data_root=tmp_path)
    assert "usage" in out.lower()
    assert styles.get_active_theme_name() == "dark"
    assert not persisted_theme_path(tmp_path).exists()


def test_theme_save_without_data_root_errors_cleanly(tmp_path: Path) -> None:
    out = _capture_handle_theme(line="/theme save light", data_root=None)
    assert "data_root" in out or "cannot save" in out.lower()
    assert styles.get_active_theme_name() == "dark"


def test_unknown_theme_name_errors_no_swap(tmp_path: Path) -> None:
    out = _capture_handle_theme(line="/theme neon", data_root=tmp_path)
    assert "unknown" in out.lower()
    assert "neon" in out
    assert styles.get_active_theme_name() == "dark"


def test_theme_reset_rewalks_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openminion.cli.constants import OPENMINION_THEME_VARIANT_ENV

    handle_theme(line="/theme light", data_root=tmp_path)
    assert styles.get_active_theme_name() == "light"

    monkeypatch.setenv(OPENMINION_THEME_VARIANT_ENV, "dark")
    out = _capture_handle_theme(line="/theme reset", data_root=tmp_path)
    assert "reset" in out.lower()
    assert styles.get_active_theme_name() == "dark"


def test_save_writes_canonical_json_payload(tmp_path: Path) -> None:
    handle_theme(line="/theme save light", data_root=tmp_path)
    payload = json.loads(persisted_theme_path(tmp_path).read_text(encoding="utf-8"))
    assert payload == {"theme": "light"}


def test_theme_status_falls_back_when_active_theme_getter_errors(
    tmp_path: Path,
) -> None:
    out = _capture_handle_theme(line="/theme list", data_root=tmp_path)
    assert "(active)" in out

    buf = io.StringIO()
    with redirect_stdout(buf):
        handle_theme(
            line="/theme list",
            data_root=tmp_path,
            active_theme_name_getter=lambda: (_ for _ in ()).throw(
                ValueError("bad theme name")
            ),
        )
    out = buf.getvalue()
    assert "(active)" in out


def test_theme_switch_reports_applier_failure(tmp_path: Path) -> None:
    out = _capture_handle_theme(line="/theme light", data_root=tmp_path)
    assert "session-local" in out

    buf = io.StringIO()
    with redirect_stdout(buf):
        handle_theme(
            line="/theme light",
            data_root=tmp_path,
            theme_applier=lambda _theme: (_ for _ in ()).throw(
                RuntimeError("cannot apply")
            ),
        )
    out = buf.getvalue()
    assert "Theme switch failed" in out
