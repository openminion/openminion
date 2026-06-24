from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace
from unittest.mock import MagicMock

from rich.console import Console

from openminion.cli.tui.terminal.shell import _SLASH_COMMANDS, _handle_slash
from openminion.cli.tui.providers.runtime import OpenMinionRuntime


def _make_runtime() -> OpenMinionRuntime:
    rt = OpenMinionRuntime.__new__(OpenMinionRuntime)
    rt._rt = SimpleNamespace(config=SimpleNamespace(providers=SimpleNamespace()))
    rt._agent_id_override = "default-agent"
    rt._agent_id = "default-agent"
    rt._channel = "cli"
    rt._target = "tui"
    rt._history_limit = 200
    rt._working_dir = ""
    rt._gateway = object()
    rt._session_id = "sess-1"
    rt._prompt_on_resume = False
    rt._project_context = None
    rt._project_context_pending = False
    rt._model_override_provider = ""
    rt._model_override_model = ""
    rt._permission_mode = ""
    rt._permission_overrides = {}
    rt._read_only_mode = False
    rt._pending_candidate_session = None
    return rt


# ── State + toggle ───────────────────────────────────────────────


def test_read_only_mode_default_false() -> None:
    rt = _make_runtime()
    assert rt.read_only_mode is False


def test_set_read_only_mode_true() -> None:
    rt = _make_runtime()
    result = rt.set_read_only_mode(True)
    assert result is True
    assert rt.read_only_mode is True


def test_set_read_only_mode_false_after_true() -> None:
    rt = _make_runtime()
    rt.set_read_only_mode(True)
    result = rt.set_read_only_mode(False)
    assert result is False
    assert rt.read_only_mode is False


def test_set_read_only_mode_coerces_truthy_values() -> None:
    rt = _make_runtime()
    # Any truthy value flips on; falsy flips off. Matches the
    # bool() coercion contract.
    rt.set_read_only_mode(1)  # type: ignore[arg-type]
    assert rt.read_only_mode is True
    rt.set_read_only_mode(0)  # type: ignore[arg-type]
    assert rt.read_only_mode is False


def test_permission_mode_cycle_uses_three_modes() -> None:
    rt = _make_runtime()
    assert rt.permission_mode == "default"
    assert rt.cycle_permission_mode() == "readonly"
    assert rt.cycle_permission_mode() == "bypass"
    assert rt.cycle_permission_mode() == "default"


def test_set_permission_mode_rejects_unknown_mode() -> None:
    rt = _make_runtime()
    try:
        rt.set_permission_mode("garbage")
    except ValueError as exc:
        assert "valid modes" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected ValueError")


# ── Slash catalog ────────────────────────────────────────────────


def test_readonly_in_slash_catalog() -> None:
    assert "/readonly" in _SLASH_COMMANDS


def test_permissions_in_slash_catalog() -> None:
    assert "/permissions" in _SLASH_COMMANDS


# ── /readonly dispatch ───────────────────────────────────────────


def _dispatch(runtime, text: str) -> str:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    asyncio.run(
        _handle_slash(
            text,
            runtime=runtime,
            console=console,
            transcript=MagicMock(),
            overlay=MagicMock(),
            status_line=MagicMock(),
            working_dir="/tmp",
        )
    )
    return buf.getvalue()


def test_readonly_bare_toggles_on_from_off() -> None:
    rt = _make_runtime()
    out = _dispatch(rt, "/readonly")
    assert rt.read_only_mode is True
    assert "read-only mode: ON" in out


def test_readonly_bare_toggles_off_from_on() -> None:
    rt = _make_runtime()
    rt.set_read_only_mode(True)
    out = _dispatch(rt, "/readonly")
    assert rt.read_only_mode is False
    assert "read-only mode: OFF" in out


def test_readonly_on_sets_explicit() -> None:
    rt = _make_runtime()
    out = _dispatch(rt, "/readonly on")
    assert rt.read_only_mode is True
    assert "ON" in out


def test_readonly_off_sets_explicit() -> None:
    rt = _make_runtime()
    rt.set_read_only_mode(True)
    out = _dispatch(rt, "/readonly off")
    assert rt.read_only_mode is False
    assert "OFF" in out


def test_readonly_toggle_alias_works() -> None:
    rt = _make_runtime()
    _dispatch(rt, "/readonly toggle")
    assert rt.read_only_mode is True


def test_readonly_unknown_arg_surfaces_error() -> None:
    rt = _make_runtime()
    out = _dispatch(rt, "/readonly garbage")
    assert "unknown arg" in out
    # State unchanged on invalid arg.
    assert rt.read_only_mode is False


def test_readonly_runtime_without_setter_surfaces_error() -> None:
    out = _dispatch(SimpleNamespace(), "/readonly")
    assert "set_read_only_mode" in out


def test_permissions_bare_shows_current_mode() -> None:
    rt = _make_runtime()
    out = _dispatch(rt, "/permissions")
    assert "permissions: default" in out


def test_permissions_sets_readonly_mode() -> None:
    rt = _make_runtime()
    out = _dispatch(rt, "/permissions readonly")
    assert rt.permission_mode == "readonly"
    assert rt.read_only_mode is True
    assert "permissions: readonly" in out


def test_permissions_cycle_advances_mode() -> None:
    rt = _make_runtime()
    out = _dispatch(rt, "/permissions cycle")
    assert rt.permission_mode == "readonly"
    assert "permissions: readonly" in out


def test_permissions_unknown_arg_surfaces_error() -> None:
    rt = _make_runtime()
    out = _dispatch(rt, "/permissions garbage")
    assert "unknown permission mode" in out
    assert rt.permission_mode == "default"


def test_permissions_sets_per_tool_override() -> None:
    rt = _make_runtime()
    out = _dispatch(rt, "/permissions file.write bypass")
    assert rt.permission_overrides == {"file.write": "bypass"}
    assert "file.write" in out
    assert "bypass" in out


def test_permissions_default_clears_per_tool_override() -> None:
    rt = _make_runtime()
    rt.set_permission_override("file.write", "bypass")
    out = _dispatch(rt, "/permissions file.write default")
    assert rt.permission_overrides == {}
    assert "cleared override" in out
