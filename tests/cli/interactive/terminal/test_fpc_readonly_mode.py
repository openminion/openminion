from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from rich.console import Console

from openminion.cli.interactive.terminal.shell import _SLASH_COMMANDS, _handle_slash
from openminion.cli.interactive.terminal.shell.permissions import _list_grants
from openminion.base.config.action_policy import ACTION_POLICY_SESSION_OVERRIDE_KEY
from openminion.cli.interactive.runtime import OpenMinionRuntime


def _make_runtime() -> OpenMinionRuntime:
    rt = OpenMinionRuntime.__new__(OpenMinionRuntime)
    rt._rt = SimpleNamespace(
        config=SimpleNamespace(providers=SimpleNamespace()),
        sessions=SimpleNamespace(update_session_metadata=lambda **_kwargs: None),
    )
    rt._agent_id_override = "default-agent"
    rt._agent_id = "default-agent"
    rt._channel = "cli"
    rt._conversation_id = ""
    rt._target = "tui"
    rt._history_limit = 200
    rt._working_dir = ""
    rt._added_workspace_roots = ()
    rt._gateway = object()
    rt._session_id = "sess-1"
    rt._prompt_on_resume = False
    rt._project_context = None
    rt._project_context_pending = False
    rt._model_override_connection = ""
    rt._model_override_provider = ""
    rt._model_override_model = ""
    rt._action_policy_mode_override = ""
    rt._permission_mode = ""
    rt._permission_overrides = {}
    rt._permission_overrides_explicit = False
    rt._read_only_mode = False
    rt._effort_level = ""
    rt._pending_candidate_session = None
    return rt


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
    assert rt.cycle_permission_mode() == "auto"
    assert rt.cycle_permission_mode() == "default"


def test_permission_posture_persists_as_one_metadata_patch() -> None:
    class _Sessions:
        def __init__(self) -> None:
            self.patches: list[tuple[str, dict]] = []

        def update_session_metadata(self, *, session_id: str, patch: dict) -> None:
            self.patches.append((session_id, dict(patch)))

    rt = _make_runtime()
    rt._rt.sessions = _Sessions()

    rt.set_permission_posture(permission_mode="auto", action_policy_mode="auto")

    assert rt.permission_mode == "auto"
    assert rt.action_policy_mode_override == "auto"
    assert rt._rt.sessions.patches == [
        (
            "sess-1",
            {
                "permission_mode": "auto",
                ACTION_POLICY_SESSION_OVERRIDE_KEY: "auto",
                "permission_overrides": "{}",
            },
        )
    ]


def test_permission_posture_write_failure_keeps_prior_memory() -> None:
    rt = _make_runtime()
    rt._rt.sessions.update_session_metadata = MagicMock(
        side_effect=RuntimeError("write failed")
    )

    try:
        rt.set_permission_posture(permission_mode="auto", action_policy_mode="auto")
    except RuntimeError:
        pass
    else:  # pragma: no cover - assertion branch
        raise AssertionError("expected metadata write failure")

    assert rt.permission_mode == "default"
    assert rt.action_policy_mode_override == ""


def test_permission_posture_restores_all_axes_from_session_metadata() -> None:
    rt = _make_runtime()
    rt.restore_session_permission_posture(
        SimpleNamespace(
            metadata={
                "permission_mode": "auto",
                ACTION_POLICY_SESSION_OVERRIDE_KEY: "auto",
                "permission_overrides": {"file.copy": "auto"},
            }
        )
    )

    assert rt.permission_mode == "auto"
    assert rt.action_policy_mode_override == "auto"
    assert rt.permission_overrides == {"file.copy": "auto"}


def test_malformed_historical_permission_metadata_uses_defaults_and_diagnostic() -> (
    None
):
    rt = _make_runtime()
    rt.restore_session_permission_posture(
        SimpleNamespace(
            metadata={
                "permission_mode": "auto",
                ACTION_POLICY_SESSION_OVERRIDE_KEY: "auto",
                "permission_overrides": ["not", "a", "mapping"],
            }
        )
    )

    assert rt.permission_mode == "default"
    assert rt.action_policy_mode_override == ""
    assert rt.permission_overrides == {}
    assert "Malformed saved permission posture" in rt._permission_posture_diagnostic


def test_session_action_policy_mode_override_persists_to_session_metadata() -> None:
    class _Sessions:
        def __init__(self) -> None:
            self.patch = None

        def update_session_metadata(self, *, session_id: str, patch: dict) -> None:
            self.patch = (session_id, dict(patch))

    rt = _make_runtime()
    sessions = _Sessions()
    rt._rt.sessions = sessions

    assert rt.set_session_action_policy_mode("auto") == "auto"

    assert rt.action_policy_mode_override == "auto"
    assert sessions.patch == (
        "sess-1",
        {
            "permission_mode": "default",
            ACTION_POLICY_SESSION_OVERRIDE_KEY: "auto",
            "permission_overrides": "{}",
        },
    )


def test_session_action_policy_mode_override_rejects_request_response_words() -> None:
    rt = _make_runtime()

    try:
        rt.set_session_action_policy_mode("allow_forever")
    except ValueError as exc:
        assert "valid modes" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected ValueError")


def test_permissions_bypass_surfaces_full_access_warning() -> None:
    rt = _make_runtime()
    out = _dispatch(rt, "/permissions bypass")

    assert rt.permission_mode == "bypass"
    assert "full access" in out


def test_permissions_auto_text_applies_workspace_auto_to_both_axes() -> None:
    rt = _make_runtime()

    out = _dispatch(rt, "/permissions auto")

    assert rt.permission_mode == "auto"
    assert rt.action_policy_mode_override == "auto"
    assert "permissions → auto" in out


def test_set_permission_mode_rejects_unknown_mode() -> None:
    rt = _make_runtime()
    try:
        rt.set_permission_mode("garbage")
    except ValueError as exc:
        assert "valid modes" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected ValueError")


def test_readonly_in_slash_catalog() -> None:
    assert "/readonly" in _SLASH_COMMANDS


def test_permissions_in_slash_catalog() -> None:
    assert "/permissions" in _SLASH_COMMANDS


def _dispatch(runtime, text: str, *, permission_choice: str | None = None) -> str:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120)
    overlay = MagicMock()
    overlay.present_permission_picker_async = AsyncMock(return_value=permission_choice)
    overlay.present_confirm_async = AsyncMock(return_value=True)
    asyncio.run(
        _handle_slash(
            text,
            runtime=runtime,
            console=console,
            transcript=MagicMock(),
            overlay=overlay,
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
    assert "remaining posture: ask" in out
    assert "all tools allowed" not in out


def test_readonly_toggle_alias_works() -> None:
    rt = _make_runtime()
    _dispatch(rt, "/readonly toggle")
    assert rt.read_only_mode is True


def test_readonly_unknown_arg_surfaces_error() -> None:
    rt = _make_runtime()
    out = _dispatch(rt, "/readonly garbage")
    assert "unknown arg" in out
    assert rt.read_only_mode is False


def test_readonly_runtime_without_setter_surfaces_error() -> None:
    out = _dispatch(SimpleNamespace(), "/readonly")
    assert "set_read_only_mode" in out


def test_permissions_bare_shows_current_mode() -> None:
    rt = _make_runtime()
    out = _dispatch(rt, "/permissions", permission_choice="ask")
    assert "permissions → ask" in out


def test_permissions_bare_shows_combined_runtime_posture() -> None:
    rt = _make_runtime()
    rt.set_session_action_policy_mode("bypass")
    rt.set_permission_mode("readonly")

    out = _dispatch(rt, "/permissions", permission_choice="readonly")

    assert "permissions → read-only" in out


def test_permissions_sets_readonly_mode() -> None:
    rt = _make_runtime()
    out = _dispatch(rt, "/permissions readonly")
    assert rt.permission_mode == "readonly"
    assert rt.read_only_mode is True
    assert "permissions → read-only + ask" in out


def test_permissions_cycle_advances_mode() -> None:
    rt = _make_runtime()
    out = _dispatch(rt, "/permissions cycle")
    assert rt.permission_mode == "readonly"
    assert "permissions: read-only" in out


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


def test_cleared_permission_override_is_forwarded_to_brain() -> None:
    rt = _make_runtime()
    rt.set_permission_override("file.write", "bypass")
    assert rt._turn_inbound_metadata(None)["permission_overrides"] == (
        '{"file.write": "bypass"}'
    )

    rt.set_permission_override("file.write", "default")

    assert rt._turn_inbound_metadata(None)["permission_overrides"] == "{}"


def test_permission_grant_listing_is_bounded_redacted_and_scoped() -> None:
    rt = _make_runtime()
    rt.list_permission_grants = lambda: [
        SimpleNamespace(
            grant_id="grant-1",
            tool="custom.send",
            method="invoke",
            target_json={"authorization": "Bearer abcdefghijklmnop", "path": "/tmp"},
            duration_type="session",
        )
    ]
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=300)

    _list_grants(rt, console)

    rendered = buf.getvalue()
    assert "grant-1  custom.send.invoke" in rendered
    assert 'scope={"authorization":"[REDACTED]","path":"/tmp"}' in rendered
    assert "duration=session" in rendered
    assert "abcdefghijklmnop" not in rendered
    assert max(map(len, rendered.splitlines())) <= 240
