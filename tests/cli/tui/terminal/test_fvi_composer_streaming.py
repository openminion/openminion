from __future__ import annotations

import asyncio
import sys

import pytest


def test_prompt_toolkit_frame_importable() -> None:
    from prompt_toolkit.widgets import Frame

    assert Frame is not None


def test_application_supports_full_screen_false() -> None:
    import inspect

    from prompt_toolkit import Application

    sig = inspect.signature(Application.__init__)
    assert "full_screen" in sig.parameters
    assert sig.parameters["full_screen"].default is False


def test_prompt_session_placeholder_supported() -> None:
    import inspect

    from prompt_toolkit import PromptSession

    sig = inspect.signature(PromptSession.prompt_async)
    assert "placeholder" in sig.parameters


# ── The load-bearing verification ────────────────────────────────


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="create_pipe_input + DummyOutput driving an Application is unstable on Windows",
)
def test_bordered_application_runs_to_completion_via_pipe_input() -> None:
    from prompt_toolkit import Application
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.input.defaults import create_pipe_input
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import HSplit, Window
    from prompt_toolkit.layout.controls import BufferControl
    from prompt_toolkit.layout.dimension import Dimension
    from prompt_toolkit.output import DummyOutput
    from prompt_toolkit.widgets import Frame

    with create_pipe_input() as pipe:
        buf = Buffer()
        input_window = Window(BufferControl(buffer=buf), height=Dimension.exact(1))
        frame = Frame(input_window)
        kb = KeyBindings()

        @kb.add(Keys.Enter)
        def _(event) -> None:
            event.app.exit(result=buf.text)

        app = Application(
            layout=Layout(HSplit([frame])),
            key_bindings=kb,
            full_screen=False,
            input=pipe,
            output=DummyOutput(),
        )
        pipe.send_text("hello\n")
        result = app.run()

    assert result == "hello"
    assert buf.text == "hello"


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="create_pipe_input is unstable on Windows",
)
def test_bordered_application_under_patch_stdout_during_streaming() -> None:
    from prompt_toolkit import Application
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.input.defaults import create_pipe_input
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import HSplit, Window
    from prompt_toolkit.layout.controls import BufferControl
    from prompt_toolkit.layout.dimension import Dimension
    from prompt_toolkit.output import DummyOutput
    from prompt_toolkit.patch_stdout import patch_stdout
    from prompt_toolkit.widgets import Frame

    async def _run() -> tuple[str, bool]:
        with create_pipe_input() as pipe:
            buf = Buffer()
            input_window = Window(BufferControl(buffer=buf), height=Dimension.exact(1))
            frame = Frame(input_window)
            kb = KeyBindings()

            @kb.add(Keys.Enter)
            def _(event) -> None:
                event.app.exit(result=buf.text)

            app = Application(
                layout=Layout(HSplit([frame])),
                key_bindings=kb,
                full_screen=False,
                input=pipe,
                output=DummyOutput(),
            )

            crashed = False

            async def _emit_stdout_writes() -> None:
                """Simulate streaming agent output while the
                prompt is open. Under `patch_stdout()`, these
                writes should be buffered/redrawn above the
                prompt rather than corrupt the input."""
                nonlocal crashed
                try:
                    for i in range(5):
                        await asyncio.sleep(0.01)
                        print(f"streamed line {i}")
                except Exception:
                    crashed = True

            async def _drive_input() -> None:
                await asyncio.sleep(0.005)
                pipe.send_text("hello world\n")

            with patch_stdout():
                writer_task = asyncio.create_task(_emit_stdout_writes())
                input_task = asyncio.create_task(_drive_input())
                result_text = await app.run_async()
                await writer_task
                await input_task

            return result_text, crashed

    result, writer_crashed = asyncio.run(_run())
    assert result == "hello world", (
        f"FVI-01 verification FAILED: bordered Application under patch_stdout() "
        f"with concurrent streaming did NOT preserve typed text. "
        f"Got result={result!r}. Falling back to placeholder-only per FVI tracker fallback rule."
    )
    assert not writer_crashed, (
        "FVI-01 verification FAILED: streamed stdout writes raised under "
        "patch_stdout() with bordered Application. Falling back to "
        "placeholder-only per FVI tracker fallback rule."
    )


def test_fallback_placeholder_only_path_works() -> None:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.input.defaults import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    with create_pipe_input() as pipe:
        session = PromptSession(
            input=pipe,
            output=DummyOutput(),
        )
        pipe.send_text("fallback ok\n")
        result = asyncio.run(session.prompt_async(placeholder="dim hint"))
    assert result == "fallback ok"
