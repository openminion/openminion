from pathlib import Path
from types import SimpleNamespace

import pytest

from openminion.api.routes.contracts import APIRouteContext
from openminion.api.routes.custom_commands import handle_request
from openminion.api.server import dispatch_request


class _Sessions:
    def __init__(self, workspace: Path | None) -> None:
        self._session = SimpleNamespace(
            metadata={"workspace_root": str(workspace) if workspace else ""}
        )

    def get_session(self, session_id: str):
        return self._session if session_id == "session-1" else None


def _runtime(tmp_path: Path):
    session_workspace = tmp_path / "session-workspace"
    session_workspace.mkdir()
    runtime_workspace = tmp_path / "runtime-workspace"
    runtime_workspace.mkdir()
    data_root = tmp_path / "data"
    data_root.mkdir()
    return SimpleNamespace(
        sessions=_Sessions(session_workspace),
        session_workspace=session_workspace,
        tool_workspace_root=runtime_workspace,
        data_root=data_root,
    )


def _context(runtime) -> APIRouteContext:
    return APIRouteContext(
        config_path=None,
        runtime=runtime,
        runtime_bootstrap_error=None,
        request_headers=None,
        request_id="custom-command-test",
    )


def test_custom_command_list_uses_session_workspace_and_project_precedence(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    user_dir = runtime.data_root / "commands"
    project_dir = runtime.session_workspace / ".openminion" / "commands"
    runtime_dir = runtime.tool_workspace_root / ".openminion" / "commands"
    user_dir.mkdir()
    project_dir.mkdir(parents=True)
    runtime_dir.mkdir(parents=True)
    (user_dir / "teamnote.md").write_text("user", encoding="utf-8")
    (runtime_dir / "teamnote.md").write_text("runtime", encoding="utf-8")
    (project_dir / "teamnote.md").write_text(
        "---\ndescription: Project note\nusage: /teamnote topic\n---\nproject $ARGUMENTS",
        encoding="utf-8",
    )
    (project_dir / "help.md").write_text("collision", encoding="utf-8")

    result = handle_request(
        _context(runtime),
        method_name="GET",
        path="/v1/sessions/session-1/custom-commands",
        body=None,
        query=None,
    )

    assert result is not None and result.status == 200
    commands = {item["name"]: item for item in result.payload["commands"]}
    assert commands["/teamnote"] == {
        "name": "/teamnote",
        "description": "Project note",
        "usage": "/teamnote topic",
        "source": "project",
    }
    assert "/help" not in commands
    assert "path" not in commands["/teamnote"]
    assert "frontmatter" not in commands["/teamnote"]


def test_custom_command_render_only_expands_arguments(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    project_dir = runtime.session_workspace / ".openminion" / "commands"
    project_dir.mkdir(parents=True)
    (project_dir / "teamnote.md").write_text(
        "first=$1 all=$ARGUMENTS", encoding="utf-8"
    )

    result = handle_request(
        _context(runtime),
        method_name="POST",
        path="/v1/sessions/session-1/custom-commands/teamnote/render",
        body={"arguments": '"two words" tail'},
        query=None,
    )

    assert result is not None and result.status == 200
    assert result.payload["name"] == "/teamnote"
    assert result.payload["prompt"] == 'first=two words all="two words" tail'


def test_custom_command_render_rejects_local_expansion(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    project_dir = runtime.session_workspace / ".openminion" / "commands"
    project_dir.mkdir(parents=True)
    (project_dir / "unsafe.md").write_text(
        "inspect @secret.txt and !`uname -a`", encoding="utf-8"
    )

    result = handle_request(
        _context(runtime),
        method_name="POST",
        path="/v1/sessions/session-1/custom-commands/unsafe/render",
        body={"arguments": ""},
        query=None,
    )

    assert result is not None and result.status == 400
    assert result.payload["error"]["code"] == ("unsupported_custom_command_expansion")


def test_custom_command_routes_report_missing_session_and_name(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)

    missing_session = handle_request(
        _context(runtime),
        method_name="GET",
        path="/v1/sessions/missing/custom-commands",
        body=None,
        query=None,
    )
    missing_name = handle_request(
        _context(runtime),
        method_name="POST",
        path="/v1/sessions/session-1/custom-commands/missing/render",
        body={"arguments": ""},
        query=None,
    )

    assert missing_session is not None and missing_session.status == 404
    assert missing_name is not None and missing_name.status == 404


def test_custom_command_list_falls_back_to_runtime_workspace(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.sessions = _Sessions(None)
    command_dir = runtime.tool_workspace_root / ".openminion" / "commands"
    command_dir.mkdir(parents=True)
    (command_dir / "fallback.md").write_text("runtime", encoding="utf-8")

    result = handle_request(
        _context(runtime),
        method_name="GET",
        path="/v1/sessions/session-1/custom-commands",
        body=None,
        query=None,
    )

    assert result is not None and result.status == 200
    commands = {item["name"]: item for item in result.payload["commands"]}
    assert commands["/fallback"] == {
        "name": "/fallback",
        "description": "custom command",
        "usage": "/fallback",
        "source": "project",
    }


@pytest.mark.parametrize("arguments", [None, 7, ["topic"], {"topic": "value"}])
def test_custom_command_render_rejects_non_string_arguments(
    tmp_path: Path, arguments: object
) -> None:
    runtime = _runtime(tmp_path)
    command_dir = runtime.session_workspace / ".openminion" / "commands"
    command_dir.mkdir(parents=True)
    (command_dir / "teamnote.md").write_text("$ARGUMENTS", encoding="utf-8")

    result = handle_request(
        _context(runtime),
        method_name="POST",
        path="/v1/sessions/session-1/custom-commands/teamnote/render",
        body={"arguments": arguments},
        query=None,
    )

    assert result is not None and result.status == 400
    assert result.payload["error"]["code"] == "invalid_custom_command_arguments"


def test_custom_command_render_rejects_malformed_quotes(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    command_dir = runtime.session_workspace / ".openminion" / "commands"
    command_dir.mkdir(parents=True)
    (command_dir / "teamnote.md").write_text("$1", encoding="utf-8")

    result = handle_request(
        _context(runtime),
        method_name="POST",
        path="/v1/sessions/session-1/custom-commands/teamnote/render",
        body={"arguments": '"'},
        query=None,
    )

    assert result is not None and result.status == 400
    assert result.payload["error"]["code"] == "invalid_custom_command_arguments"


def test_custom_command_render_does_not_forward_builtin_slash(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    command_dir = runtime.session_workspace / ".openminion" / "commands"
    command_dir.mkdir(parents=True)
    (command_dir / "help.md").write_text("override", encoding="utf-8")

    result = handle_request(
        _context(runtime),
        method_name="POST",
        path="/v1/sessions/session-1/custom-commands/help/render",
        body={"arguments": ""},
        query=None,
    )

    assert result is not None and result.status == 404
    assert result.payload["error"]["code"] == "custom_command_not_found"


def test_custom_command_route_is_registered(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    command_dir = runtime.session_workspace / ".openminion" / "commands"
    command_dir.mkdir(parents=True)
    (command_dir / "teamnote.md").write_text("note", encoding="utf-8")

    status, payload = dispatch_request(
        "GET",
        "/v1/sessions/session-1/custom-commands",
        None,
        runtime=runtime,
    )

    assert status == 200
    assert "/teamnote" in {item["name"] for item in payload["commands"]}
