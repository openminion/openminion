from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import time

import pytest
from rich.console import Console

from tests.e2e.cli.focus.harness import FocusProbe, PtySession
from tests.e2e.cli.focus.harness.assertions import visible_text
from tests.e2e.cli.focus.harness.ollama_fixture import ollama_fixture_server
from tests.e2e.cli.focus.harness.probe import (
    approval_prompt_needs_reply,
    inline_approval_menu,
)


pytestmark = [pytest.mark.e2e, pytest.mark.timeout(120)]


def test_approval_and_resume_overlays_share_the_live_composer_theme() -> None:
    from openminion.cli.interactive.terminal.composer import TerminalComposer
    from openminion.cli.interactive.terminal.overlays import TerminalOverlayPresenter
    from openminion.cli.presentation.styles import set_active_theme, set_color_mode
    from openminion.cli.theme import DARK, LIGHT

    set_color_mode("always")
    set_active_theme(DARK)
    try:
        composer = TerminalComposer(color=True)
        overlay = TerminalOverlayPresenter(
            console=Console(force_terminal=False),
            prompt_session=composer.prompt_session,
        )

        set_active_theme(LIGHT)
        composer.apply_theme()

        assert overlay._session is composer.prompt_session
        rules = dict(overlay._session.style._style_rules)
        assert LIGHT.surface_panel_bg in rules["bottom-toolbar"]
        assert DARK.surface_panel_bg not in rules["bottom-toolbar"]
    finally:
        set_active_theme(DARK)
        set_color_mode(None)


def _write_fixture_config(path: Path, base_url: str) -> None:
    path.write_text(
        json.dumps(
            {
                "default_agent": "fixture",
                "agents": {
                    "fixture": {
                        "name": "fixture",
                        "provider": "ollama",
                        "model": "qwen2.5:14b",
                    }
                },
                "providers": {
                    "ollama": {
                        "model": "qwen2.5:14b",
                        "base_url": base_url,
                    }
                },
                "runtime": {"demo_mode": False},
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("cols", "no_color"),
    ((80, False), (120, True)),
)
def test_codex_first_shell_journey_is_compact_truthful_and_persistent(
    focus_probe: FocusProbe,
    tmp_path,
    cols: int,
    no_color: bool,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    (project / "long.txt").write_text(
        "\n".join(f"line-{line:02d}-detail" for line in range(1, 41)),
        encoding="utf-8",
    )
    editor = tmp_path / "editor.py"
    editor.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "Path(sys.argv[1]).write_text('draft from editor\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    responses = (
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "read-a",
                    "function": {
                        "name": "file.read",
                        "arguments": {"path": "long.txt"},
                    },
                },
                {
                    "id": "read-b",
                    "function": {
                        "name": "file.read",
                        "arguments": {"path": "long.txt"},
                    },
                },
                {
                    "id": "missing",
                    "function": {
                        "name": "file.read",
                        "arguments": {"path": "missing.txt"},
                    },
                },
            ],
        },
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "denied-write",
                    "function": {
                        "name": "file.write",
                        "arguments": {
                            "path": "denied.txt",
                            "content": "must not be written",
                        },
                    },
                }
            ],
        },
        {
            "role": "assistant",
            "content": (
                "SCRIPTED_FIRST_DONE\n\n"
                '<finalization_status>{"status":"final_answer",'
                '"reasoning":"scripted first turn complete"}</finalization_status>'
            ),
        },
        {
            "role": "assistant",
            "content": (
                "SCRIPTED_QUEUE_DONE\n\n"
                '<finalization_status>{"status":"final_answer",'
                '"reasoning":"queued turn complete"}</finalization_status>'
            ),
        },
    )

    with ollama_fixture_server(
        responses,
        response_delay_seconds=1.5,
    ) as (base_url, requests):
        config = tmp_path / "config.json"
        _write_fixture_config(config, base_url)
        probe = FocusProbe(
            python_bin=focus_probe.python_bin,
            openminion_root=focus_probe.openminion_root,
            framework_root=focus_probe.framework_root,
            data_root=focus_probe.data_root,
            config_path=config,
            agent_id="fixture",
            workdir=project,
            session_id=f"codex-first-{cols}",
            include_project_context=False,
            allow_unsandboxed_exec=True,
        )
        environment = probe.environment()
        environment["OLLAMA_API_KEY"] = "fixture-key-not-for-network-use"
        environment["VISUAL"] = f"{sys.executable} {editor}"
        environment["NO_COLOR"] = "1" if no_color else ""

        with PtySession(
            argv=probe.command(),
            cwd=probe.openminion_root,
            env=environment,
            rows=34,
            cols=cols,
        ) as session:
            probe.wait_ready(session)
            startup = visible_text(session.visible_transcript)
            assert "model:" in startup
            assert "directory:" in startup
            assert "permissions:" in startup
            assert "provider:" not in startup

            turn_offset = len(session.visible_transcript)
            probe._submit_composer_line(session, "run the scripted checks")
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if "queue for the next turn" in session.screen_text:
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("scripted turn never entered the busy state")
            busy_rows = session.screen_lines
            tip_row = next(
                index for index, row in enumerate(busy_rows) if row.startswith("Tip: ")
            )
            echoed_row = next(
                index
                for index, row in enumerate(busy_rows)
                if row == "❯ run the scripted checks"
            )
            assert echoed_row == tip_row + 2, "\n".join(
                f"{index}: {row!r}" for index, row in enumerate(busy_rows)
            )
            assert busy_rows[-4].startswith("Status:")
            assert busy_rows[-3] == ""
            assert busy_rows[-2].startswith("❯ Type to queue")
            assert busy_rows[-1].startswith("◆ ")
            assert session.cursor_position[0] == len(busy_rows) - 1

            session.send("queued follow-up")
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if session.screen_lines[-2] == "❯ queued follow-up":
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("busy draft did not remain above the footer")
            busy_draft_rows = session.screen_lines
            assert busy_draft_rows[-4].startswith("Status:")
            assert busy_draft_rows[-3] == ""
            assert busy_draft_rows[-1].startswith("◆ ")
            assert session.cursor_position[0] == len(busy_draft_rows) - 1
            session.send("\r")
            session.wait_for_visible_match_after(
                r"Queued for next turn \(1 pending\)\.",
                offset=turn_offset,
                timeout=30,
            )

            approval_denied = False
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                transcript = session.visible_transcript
                screen = session.screen_text
                if not approval_denied and inline_approval_menu(screen) is not None:
                    probe._submit_inline_approval(session, "no")
                    approval_denied = True
                    continue
                if not approval_denied and approval_prompt_needs_reply(
                    transcript, offset=turn_offset
                ):
                    probe._submit_composer_line(session, "no")
                    approval_denied = True
                    continue
                if (
                    "SCRIPTED_FIRST_DONE" in transcript
                    and "SCRIPTED_QUEUE_DONE" in transcript
                    and transcript.count("Done in") >= 2
                ):
                    break
                time.sleep(0.05)
            else:
                raise AssertionError(
                    "scripted and queued turns did not complete\n"
                    f"{visible_text(session.visible_transcript)[-3000:]}"
                )

            turn = visible_text(session.visible_transcript[turn_offset:])
            assert approval_denied
            assert turn.count("Read a file.") >= 3
            assert "missing.txt" in turn
            assert "denied" in turn.lower()
            assert "Running queued message: queued follow-up" in turn
            assert not (project / "denied.txt").exists()

            expanded = visible_text(
                probe.run_slash(session, "/expand 1", marker="line-40-detail")
            )
            assert "line-40-detail" in expanded

            help_text = visible_text(
                probe.run_slash(session, "?", marker="Keyboard shortcuts:")
            )
            assert "Ctrl-L" in help_text
            assert "Shift-Tab" in help_text

            session.send("ab")
            session.send("\x1b[D")
            session.send("\x0c")
            session.send("X")
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if "aXb" in visible_text(session.read_screen()):
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("Ctrl-L did not preserve the draft and cursor")
            session.send("\x01\x0b")

            status = visible_text(probe.run_slash(session, "/status", marker="Status:"))
            assert "provider:" in status

            review = visible_text(
                probe.run_slash(
                    session,
                    "/review",
                    marker="Structural diff check not run",
                )
            )
            assert "Scope: working tree (unstaged changes)" in review
            assert "clean review" not in review.lower()

            probe.run_slash(
                session,
                "/theme light",
                marker="active theme is now 'light' (session-local)",
            )
            saved = visible_text(
                probe.run_slash(
                    session,
                    "/theme save light",
                    marker="active theme is now 'light'",
                )
            )
            assert "theme saved to" in saved

            editor_offset = len(session.visible_transcript)
            editor_result = visible_text(
                probe.run_slash(
                    session,
                    "/editor",
                    marker="Editor draft loaded for review",
                )
            )
            assert "draft from editor" in visible_text(session.screen_text)
            assert "Done in" not in visible_text(
                session.visible_transcript[editor_offset:]
            )
            assert "auto-submit" not in editor_result.lower()
            session.send("\x15")

        with PtySession(
            argv=probe.command(),
            cwd=probe.openminion_root,
            env=environment,
            rows=34,
            cols=cols,
        ) as session:
            probe.wait_ready(session)
            theme = visible_text(
                probe.run_slash(session, "/theme", marker="Active Theme: light")
            )
            assert "Active Theme: light" in theme

        assert requests
