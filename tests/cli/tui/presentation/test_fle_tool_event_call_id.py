from __future__ import annotations

from openminion.cli.tui.presentation.models import ToolEvent


# ── Field presence + defaults ────────────────────────────────────


def test_tool_event_call_id_default_empty() -> None:
    event = ToolEvent(tool_name="Bash", args={"cmd": "ls"}, content="")
    assert event.call_id == ""


def test_tool_event_call_id_can_be_set() -> None:
    event = ToolEvent(
        tool_name="Bash",
        args={"cmd": "ls"},
        content="",
        call_id="call-abc-123",
    )
    assert event.call_id == "call-abc-123"


def test_tool_event_call_id_normalizes_via_strip() -> None:
    event = ToolEvent(
        tool_name="Bash",
        args={"cmd": "ls"},
        content="",
        call_id="  call-abc  ",
    )
    assert event.call_id == "call-abc"


def test_tool_event_call_id_handles_none() -> None:
    event = ToolEvent(
        tool_name="Bash",
        args={"cmd": "ls"},
        content="",
        call_id="",
    )
    assert event.call_id == ""


def test_existing_tool_event_callers_unchanged() -> None:
    event = ToolEvent(
        tool_name="Edit",
        args={"path": "foo.py"},
        content="diff content",
        full_content="diff full",
        exit_code=0,
        duration_ms=1234,
        truncated=False,
        content_type="text",
    )
    assert event.tool_name == "Edit"
    assert event.args == {"path": "foo.py"}
    assert event.content == "diff content"
    assert event.full_content == "diff full"
    assert event.exit_code == 0
    assert event.duration_ms == 1234
    assert event.call_id == ""  # default


# ── Runtime extraction ───────────────────────────────────────────


def _make_runtime_with_minimal_init():
    from openminion.cli.tui.providers.runtime import OpenMinionRuntime

    rt = OpenMinionRuntime.__new__(OpenMinionRuntime)
    rt._working_dir = "/tmp"  # required by _display_path
    return rt


def test_extract_call_id_from_payload() -> None:
    rt = _make_runtime_with_minimal_init()
    payload = {
        "tool_name": "Bash",
        "args": {"cmd": "ls"},
        "content": "file.txt",
        "call_id": "call-xyz-789",
    }
    event = rt._tool_event_from_payload(payload)
    assert event is not None
    assert event.call_id == "call-xyz-789"


def test_extract_call_id_from_payload_id_alias() -> None:
    rt = _make_runtime_with_minimal_init()
    payload = {
        "tool_name": "Bash",
        "args": {"cmd": "ls"},
        "content": "ok",
        "id": "call-fallback-123",
    }
    event = rt._tool_event_from_payload(payload)
    assert event is not None
    assert event.call_id == "call-fallback-123"


def test_extract_call_id_absent_returns_empty() -> None:
    rt = _make_runtime_with_minimal_init()
    payload = {
        "tool_name": "Bash",
        "args": {"cmd": "ls"},
        "content": "ok",
    }
    event = rt._tool_event_from_payload(payload)
    assert event is not None
    assert event.call_id == ""


def test_display_path_relativizes_to_working_dir() -> None:
    rt = _make_runtime_with_minimal_init()
    rt._working_dir = "/tmp/workspace"

    display = rt._display_path("/tmp/workspace/src/app.py")

    assert display == "src/app.py"


def test_decode_json_invalid_payload_returns_original_string() -> None:
    rt = _make_runtime_with_minimal_init()

    assert rt._decode_json("{not-json") == "{not-json"


def test_extract_call_id_from_metadata_direct_name_path() -> None:
    rt = _make_runtime_with_minimal_init()
    metadata: dict = {
        "tool_name": "Edit",
        "args": {"path": "foo.py"},
        "tool_result": "diff content",
        "call_id": "call-meta-456",
    }
    event = rt._tool_event_from_metadata(metadata)
    assert event is not None
    assert event.call_id == "call-meta-456"


def test_extract_call_id_from_metadata_direct_name_absent() -> None:
    rt = _make_runtime_with_minimal_init()
    metadata: dict = {
        "tool_name": "Edit",
        "args": {"path": "foo.py"},
        "tool_result": "diff content",
    }
    event = rt._tool_event_from_metadata(metadata)
    assert event is not None
    assert event.call_id == ""
