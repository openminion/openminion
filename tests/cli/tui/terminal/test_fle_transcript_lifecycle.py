from __future__ import annotations

import asyncio
import io

from rich.console import Console

import openminion.cli.tui.terminal.transcript as transcript_module
from openminion.cli.tui.terminal.transcript import TerminalTranscript
from openminion.cli.tui.presentation.models import (
    ChatMessage,
    MessageKind,
    ToolEvent,
)


def _make(verbosity: str = "normal") -> tuple[TerminalTranscript, io.StringIO]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=160)
    return TerminalTranscript(console, verbosity=verbosity), buf


def test_transcript_can_hide_response_time() -> None:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=160)
    transcript = TerminalTranscript(console, show_response_time=False)
    handle = transcript.begin_turn()
    handle.append_token("hello")
    handle.complete()
    assert "Done in" not in buf.getvalue()


def test_started_prints_yellow_narration() -> None:
    t, buf = _make("normal")
    t.handle_tool_started({"call_id": "c1", "tool_name": "Bash", "args": {"cmd": "ls"}})
    out = buf.getvalue()
    assert "Running" in out
    assert "Bash(ls)" in out


def test_started_records_call_id_in_dedup_set() -> None:
    t, _ = _make("normal")
    t.handle_tool_started({"call_id": "c1", "tool_name": "Bash", "args": {"cmd": "ls"}})
    assert "c1" in t._live_narrated_call_ids


def test_started_idempotent_on_duplicate_call_id() -> None:
    t, buf = _make("normal")
    t.handle_tool_started({"call_id": "c1", "tool_name": "Bash", "args": {"cmd": "ls"}})
    pre_len = len(buf.getvalue())
    t.handle_tool_started({"call_id": "c1", "tool_name": "Bash", "args": {"cmd": "ls"}})
    assert len(buf.getvalue()) == pre_len


def test_started_handles_empty_call_id() -> None:
    t, buf = _make("normal")
    t.handle_tool_started({"tool_name": "Bash", "args": {"cmd": "ls"}})
    assert "Running" in buf.getvalue()
    assert "" not in t._live_narrated_call_ids


def test_started_falls_back_when_live_mount_rejects_block() -> None:
    t, buf = _make("normal")

    class _BrokenHandle:
        def set_active_tool(self, **_kwargs) -> None:
            raise ValueError("bad live mount")

    t._active_handle = _BrokenHandle()
    t.handle_tool_started({"call_id": "c1", "tool_name": "Bash", "args": {"cmd": "ls"}})

    assert "Running" in buf.getvalue()
    assert "c1" in t._live_narrated_call_ids


def test_started_quiet_mode_hides_but_counts() -> None:
    t, buf = _make("quiet")
    t.handle_tool_started({"call_id": "c1", "tool_name": "Bash", "args": {"cmd": "ls"}})
    assert "Running" not in buf.getvalue()
    assert t._hidden_tool_count == 1
    assert "c1" in t._live_narrated_call_ids


def test_completed_prints_final_block_normal_mode() -> None:
    t, buf = _make("normal")
    t.handle_tool_started({"call_id": "c1", "tool_name": "Bash", "args": {"cmd": "ls"}})
    t.handle_tool_completed(
        {
            "call_id": "c1",
            "tool_name": "Bash",
            "args": {"cmd": "ls"},
            "content": "file1\nfile2\n",
            "exit_code": 0,
        }
    )
    out = buf.getvalue()
    assert "Running" in out
    assert "file1" in out
    assert "file2" in out


def test_completed_records_call_id_in_dedup_set() -> None:
    t, _ = _make("normal")
    t.handle_tool_completed(
        {"call_id": "c1", "tool_name": "Bash", "args": {"cmd": "ls"}, "content": "ok"}
    )
    assert "c1" in t._live_narrated_call_ids


def test_completed_failure_shows_exit_suffix() -> None:
    t, buf = _make("normal")
    t.handle_tool_completed(
        {
            "call_id": "c1",
            "tool_name": "Bash",
            "args": {"cmd": "ls"},
            "content": "error",
            "exit_code": 1,
        }
    )
    out = buf.getvalue()
    assert "✗ (exit 1)" in out


def test_completed_quiet_mode_counts_failed() -> None:
    t, _ = _make("quiet")
    t.handle_tool_started({"call_id": "c1", "tool_name": "Bash", "args": {"cmd": "ls"}})
    t.handle_tool_completed(
        {
            "call_id": "c1",
            "tool_name": "Bash",
            "args": {},
            "content": "",
            "exit_code": 1,
        }
    )
    assert t._hidden_tool_count == 1
    assert t._hidden_failed_count == 1


def test_completed_verbose_mode_shows_full_body() -> None:
    t, buf = _make("verbose")
    long_body = "\n".join(f"line {i}" for i in range(20))
    t.handle_tool_completed(
        {
            "call_id": "c1",
            "tool_name": "Bash",
            "args": {"cmd": "ls"},
            "content": long_body,
            "exit_code": 0,
        }
    )
    out = buf.getvalue()
    assert "line 0" in out
    assert "line 19" in out
    assert "… +" not in out


def test_completed_normal_mode_truncates_long_body() -> None:
    t, buf = _make("normal")
    long_body = "\n".join(f"line {i}" for i in range(30))
    t.handle_tool_completed(
        {
            "call_id": "c1",
            "tool_name": "Bash",
            "args": {"cmd": "ls"},
            "content": long_body,
            "exit_code": 0,
        }
    )
    out = buf.getvalue()
    assert "line 0" in out
    assert "… +" in out


def test_completed_fdr_diff_dispatch_for_edit_tool() -> None:
    t, buf = _make("normal")
    diff_body = "--- a/foo.py\n+++ b/foo.py\n@@ -1,2 +1,3 @@\n ctx\n-old\n+new\n"
    t.handle_tool_completed(
        {
            "call_id": "c1",
            "tool_name": "Edit",
            "args": {"path": "foo.py"},
            "content": diff_body,
            "exit_code": 0,
        }
    )
    out = buf.getvalue()
    assert "old" in out
    assert "new" in out


def test_completed_without_prior_started_still_renders() -> None:
    t, buf = _make("normal")
    t.handle_tool_completed(
        {"call_id": "c1", "tool_name": "Bash", "args": {}, "content": "ok"}
    )
    out = buf.getvalue()
    assert "ok" in out


def test_completed_during_live_turn_appends_via_handle() -> None:
    t, buf = _make("normal")

    class _CapturingHandle:
        def __init__(self) -> None:
            self.cleared: list[str] = []
            self.renderables: list[object] = []

        def clear_active_tool(self, *, call_id: str = "") -> None:
            self.cleared.append(call_id)

        def append_renderable(self, renderable: object) -> None:
            self.renderables.append(renderable)

    handle = _CapturingHandle()
    t._active_handle = handle

    t.handle_tool_completed(
        {
            "call_id": "c1",
            "tool_name": "file.list_dir",
            "args": {"path": "."},
            "content": "first-marker",
        }
    )
    t.handle_tool_completed(
        {
            "call_id": "c2",
            "tool_name": "file.read",
            "args": {"path": "README.md"},
            "content": "second-marker",
        }
    )

    assert handle.cleared == ["c1", "c2"]
    assert len(handle.renderables) == 2
    assert "first-marker" not in buf.getvalue()
    assert "second-marker" not in buf.getvalue()

    rendered = io.StringIO()
    console = Console(file=rendered, force_terminal=False, width=160)
    for renderable in handle.renderables:
        console.print(renderable)
    out = rendered.getvalue()
    assert "first-marker" in out
    assert "second-marker" in out


def test_started_during_live_turn_appends_running_block_via_handle() -> None:
    t, buf = _make("normal")

    class _CapturingHandle:
        def __init__(self) -> None:
            self.active: dict[str, object] | None = None
            self.renderables: list[object] = []

        def set_active_tool(self, **kwargs: object) -> None:
            self.active = dict(kwargs)

        def append_renderable(self, renderable: object) -> None:
            self.renderables.append(renderable)

    handle = _CapturingHandle()
    t._active_handle = handle

    t.handle_tool_started(
        {
            "call_id": "c1",
            "tool_name": "file.list_dir",
            "args": {"path": "."},
        }
    )

    assert handle.active is not None
    assert handle.active["call_id"] == "c1"
    assert len(handle.renderables) == 1
    assert "Running" not in buf.getvalue()
    assert "c1" in t._live_narrated_call_ids

    rendered = io.StringIO()
    console = Console(file=rendered, force_terminal=False, width=160)
    console.print(handle.renderables[0])
    out = rendered.getvalue()
    assert "Running" in out
    assert "file.list_dir(.)" in out
    assert "0s" not in out


def test_completed_ignores_live_clear_failure() -> None:
    t, buf = _make("normal")

    class _BrokenHandle:
        def clear_active_tool(self, **_kwargs) -> None:
            raise ValueError("cannot clear")

    t._active_handle = _BrokenHandle()
    t.handle_tool_completed(
        {"call_id": "c1", "tool_name": "Bash", "args": {}, "content": "ok"}
    )

    assert "ok" in buf.getvalue()


def test_agent_render_uses_prompt_safe_terminal_hook_when_app_running(
    monkeypatch,
) -> None:
    async def _case() -> None:
        t, buf = _make("normal")

        class _App:
            is_running = True

        calls: list[bool] = []

        monkeypatch.setattr(transcript_module, "get_app_or_none", lambda: _App())

        def _fake_run_in_terminal(func, render_cli_done=False):
            assert render_cli_done is False
            calls.append(True)
            func()

            async def _done():
                return None

            return asyncio.create_task(_done())

        monkeypatch.setattr(
            transcript_module,
            "run_in_terminal",
            _fake_run_in_terminal,
        )

        t.push_message(
            ChatMessage(kind=MessageKind.AGENT, sender="assistant", body="hello"),
        )
        await asyncio.sleep(0)

        assert calls
        assert "hello" in buf.getvalue()

    asyncio.run(_case())


def test_terminal_writer_overrides_direct_console_print() -> None:
    t, buf = _make("normal")
    rendered: list[str] = []

    def _writer(render) -> None:
        render()
        rendered.append(buf.getvalue())

    t.set_terminal_writer(_writer)
    t.push_message(
        ChatMessage(kind=MessageKind.AGENT, sender="assistant", body="hello")
    )

    assert rendered
    assert "hello" in rendered[0]


def test_post_turn_render_skips_already_narrated_call_id() -> None:
    t, buf = _make("normal")
    t.handle_tool_completed(
        {"call_id": "c1", "tool_name": "Bash", "args": {}, "content": "live"}
    )
    pre_len = len(buf.getvalue())
    event = ToolEvent(
        tool_name="Bash",
        args={},
        content="post-turn",
        call_id="c1",
    )
    t._render(
        ChatMessage(
            kind=MessageKind.TOOL,
            sender="tool:Bash",
            body="",
            tool_event=event,
        )
    )
    assert len(buf.getvalue()) == pre_len


def test_post_turn_render_proceeds_when_call_id_empty() -> None:
    t, buf = _make("normal")
    event = ToolEvent(
        tool_name="Bash",
        args={},
        content="post-turn output",
        call_id="",  # explicit empty
    )
    pre_len = len(buf.getvalue())
    t._render(
        ChatMessage(
            kind=MessageKind.TOOL,
            sender="tool:Bash",
            body="",
            tool_event=event,
        )
    )
    out = buf.getvalue()[pre_len:]
    # Existing render fired — content visible.
    assert "post-turn output" in out


def test_post_turn_render_proceeds_when_call_id_not_in_set() -> None:
    t, buf = _make("normal")
    # Simulate a different call_id in the dedup set.
    t._live_narrated_call_ids.add("other-id")
    event = ToolEvent(
        tool_name="Bash",
        args={},
        content="new content",
        call_id="c1",
    )
    t._render(
        ChatMessage(
            kind=MessageKind.TOOL,
            sender="tool:Bash",
            body="",
            tool_event=event,
        )
    )
    out = buf.getvalue()
    assert "new content" in out


# ── FTV verbosity interaction ────────────────────────────────────


def test_quiet_lifecycle_increments_summary_counter() -> None:
    t, _ = _make("quiet")
    t.handle_tool_started({"call_id": "c1", "tool_name": "Bash", "args": {}})
    t.handle_tool_completed(
        {
            "call_id": "c1",
            "tool_name": "Bash",
            "args": {},
            "content": "",
            "exit_code": 0,
        }
    )
    t.handle_tool_started({"call_id": "c2", "tool_name": "Bash", "args": {}})
    t.handle_tool_completed(
        {
            "call_id": "c2",
            "tool_name": "Bash",
            "args": {},
            "content": "",
            "exit_code": 1,
        }
    )
    assert t._hidden_tool_count == 2
    assert t._hidden_failed_count == 1


# ── Existing contracts preserved ─────────────────────────────────


def test_existing_post_turn_render_unaffected_when_no_live_narration() -> None:
    t, buf = _make("normal")
    event = ToolEvent(
        tool_name="Bash",
        args={"cmd": "ls"},
        content="unchanged output",
        call_id="some-id",
    )
    t.push_message(
        ChatMessage(
            kind=MessageKind.TOOL,
            sender="tool:Bash",
            body="",
            tool_event=event,
        )
    )
    out = buf.getvalue()
    assert "unchanged output" in out


def test_live_narrated_call_ids_starts_empty() -> None:
    t, _ = _make("normal")
    assert t._live_narrated_call_ids == set()


# ── FLE-04: end-to-end integration ───────────────────────────────

from openminion.cli.tui.terminal.shell import _run_agent_turn  # noqa: E402


class _ScriptedLifecycleRuntime:
    def __init__(
        self,
        *,
        tool_name: str = "Bash",
        args: dict | None = None,
        content: str = "out",
        exit_code: int = 0,
        call_id: str = "c1",
    ) -> None:
        self._tool_name = tool_name
        self._args = dict(args or {"cmd": "ls"})
        self._content = content
        self._exit_code = exit_code
        self._call_id = call_id

    async def send_message(self, text, *, progress_callback=None, **kwargs):
        del text, kwargs
        # Fire tool lifecycle BEFORE any chunk yields (matches real
        # gateway behavior — tools complete before chunks stream).
        if progress_callback:
            progress_callback(
                {
                    "kind": "tool_started",
                    "call_id": self._call_id,
                    "tool_name": self._tool_name,
                    "args": self._args,
                }
            )
        await asyncio.sleep(0)
        if progress_callback:
            progress_callback(
                {
                    "kind": "tool_completed",
                    "call_id": self._call_id,
                    "tool_name": self._tool_name,
                    "args": self._args,
                    "content": self._content,
                    "exit_code": self._exit_code,
                }
            )
        await asyncio.sleep(0)
        # Now model produces text response.
        yield "Here is the answer: "
        yield "done."


class _ScriptedMultiLifecycleRuntime:
    async def send_message(self, text, *, progress_callback=None, **kwargs):
        del text, kwargs
        if progress_callback:
            progress_callback(
                {
                    "kind": "tool_started",
                    "call_id": "c1",
                    "tool_name": "file.list_dir",
                    "args": {"path": "."},
                }
            )
        await asyncio.sleep(0)
        if progress_callback:
            progress_callback(
                {
                    "kind": "tool_completed",
                    "call_id": "c1",
                    "tool_name": "file.list_dir",
                    "args": {"path": "."},
                    "content": "dir-marker",
                    "exit_code": 0,
                }
            )
        await asyncio.sleep(0)
        if progress_callback:
            progress_callback(
                {
                    "kind": "tool_started",
                    "call_id": "c2",
                    "tool_name": "file.read",
                    "args": {"path": "README.md"},
                }
            )
        await asyncio.sleep(0)
        if progress_callback:
            progress_callback(
                {
                    "kind": "tool_completed",
                    "call_id": "c2",
                    "tool_name": "file.read",
                    "args": {"path": "README.md"},
                    "content": "read-marker",
                    "exit_code": 0,
                }
            )
        await asyncio.sleep(0)
        yield "done."


def _run_e2e(transcript: TerminalTranscript, runtime) -> None:
    asyncio.run(
        _run_agent_turn(
            text="x",
            runtime=runtime,
            transcript=transcript,
            status_line=None,
        )
    )


def test_e2e_normal_mode_renders_in_progress_and_final_blocks() -> None:
    transcript, buf = _make("normal")
    runtime = _ScriptedLifecycleRuntime(content="file1\nfile2\n")
    _run_e2e(transcript, runtime)
    out = buf.getvalue()
    assert "Bash(ls)" in out  # verb-form title on the final block
    assert "file1" in out  # final block body
    assert "file2" in out
    assert "done." in out  # agent reply streamed


def test_e2e_no_double_render_across_lifecycle() -> None:
    transcript, buf = _make("normal")
    runtime = _ScriptedLifecycleRuntime(
        content="unique-marker-xyz", call_id="dedup-test"
    )
    _run_e2e(transcript, runtime)
    out = buf.getvalue()
    # Content body appears EXACTLY once in the captured output.
    assert out.count("unique-marker-xyz") == 1
    # call_id was recorded in the dedup set.
    assert "dedup-test" in transcript._live_narrated_call_ids


def test_e2e_preserves_multiple_completed_tool_blocks() -> None:
    transcript, buf = _make("normal")
    _run_e2e(transcript, _ScriptedMultiLifecycleRuntime())
    out = buf.getvalue()
    assert out.count("dir-marker") == 1
    assert out.count("read-marker") == 1
    assert "c1" in transcript._live_narrated_call_ids
    assert "c2" in transcript._live_narrated_call_ids


def test_e2e_quiet_mode_hides_blocks_and_fires_summary() -> None:
    transcript, buf = _make("quiet")
    runtime = _ScriptedLifecycleRuntime(content="should-not-appear")
    _run_e2e(transcript, runtime)
    out = buf.getvalue()
    # Yellow narration suppressed.
    assert "Running" not in out
    # Body suppressed.
    assert "should-not-appear" not in out
    # Hidden-count summary printed at end-of-turn.
    assert "1 tool call hidden" in out


def test_e2e_quiet_mode_summary_counts_failures() -> None:
    transcript, buf = _make("quiet")
    runtime = _ScriptedLifecycleRuntime(
        content="error output", exit_code=2, call_id="failed-1"
    )
    _run_e2e(transcript, runtime)
    out = buf.getvalue()
    assert "1 failed" in out


def test_e2e_verbose_mode_shows_full_body() -> None:
    transcript, buf = _make("verbose")
    long_body = "\n".join(f"line-{i}" for i in range(25))
    runtime = _ScriptedLifecycleRuntime(content=long_body)
    _run_e2e(transcript, runtime)
    out = buf.getvalue()
    assert "line-0" in out
    assert "line-24" in out  # all 25 lines visible (well under 200 cap)


def test_e2e_fdr_diff_renderer_fires_for_edit_tool() -> None:
    transcript, buf = _make("normal")
    diff_body = (
        "--- a/foo.py\n+++ b/foo.py\n@@ -1,2 +1,3 @@\n ctx\n-old-line\n+new-line\n"
    )
    runtime = _ScriptedLifecycleRuntime(
        tool_name="Edit",
        args={"path": "foo.py"},
        content=diff_body,
    )
    _run_e2e(transcript, runtime)
    out = buf.getvalue()
    # FDR renders diff body lines.
    assert "old-line" in out
    assert "new-line" in out


def test_e2e_normal_mode_truncates_long_body() -> None:
    transcript, buf = _make("normal")
    long_body = "\n".join(f"row{i}" for i in range(40))
    runtime = _ScriptedLifecycleRuntime(content=long_body)
    _run_e2e(transcript, runtime)
    out = buf.getvalue()
    # First-row visible, truncation marker present.
    assert "row0" in out
    assert "… +" in out
