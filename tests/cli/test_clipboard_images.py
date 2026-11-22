"""Clipboard image capture through the existing selected-image bounds."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from openminion.cli.commands import run as run_command
from openminion.cli.interactive.terminal.composer import TerminalComposer
from openminion.cli.interactive.terminal.shell.images import paste_clipboard_image
from openminion.cli.presentation.image_paste import (
    detect_image_bytes,
    store_image_bytes,
)
from openminion.cli.transport import images as images_mod
from openminion.modules.artifact.constants import MAX_IMAGE_BYTES, MAX_TURN_IMAGES

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class _FakeStdout:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self.read_sizes: list[int] = []

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        return self._data[:size] if size and size > 0 else self._data

    def close(self) -> None:
        pass


class _FakeProcess:
    def __init__(
        self,
        data: bytes = b"",
        returncode: int = 0,
        missing: bool = False,
        finished: bool = True,
    ) -> None:
        if missing:
            raise FileNotFoundError
        self.returncode = returncode
        self.stdout = _FakeStdout(data)
        self.finished = finished
        self.killed = False
        self.waited = 0

    def poll(self):
        if self.killed:
            return -9
        return self.returncode if self.finished else None

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        self.waited += 1
        return self.returncode


def _popen(monkeypatch, behaviours) -> list[tuple[str, ...]]:
    commands: list[tuple[str, ...]] = []

    def fake_popen(command, **kwargs):
        commands.append(tuple(command))
        outcome = behaviours.get(tuple(command), _FakeProcess(returncode=1))
        return outcome() if callable(outcome) else outcome

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    return commands


def _write(path: Path, data: bytes = PNG) -> Path:
    path.write_bytes(data)
    return path


def test_png_fixture_is_a_supported_image() -> None:
    assert detect_image_bytes(PNG) == "png"


def test_every_tool_is_invoked_with_its_required_arguments() -> None:
    assert images_mod._CLIPBOARD_IMAGE_COMMANDS == (
        ("wl-paste", "--type", "image/png", "--no-newline"),
        ("xclip", "-selection", "clipboard", "-t", "image/png", "-o"),
        ("pngpaste", "-"),
    )


def test_read_clipboard_image_skips_missing_and_failed_tools(monkeypatch) -> None:
    commands = _popen(
        monkeypatch,
        {
            images_mod._WL_PASTE: lambda: _FakeProcess(missing=True),
            images_mod._XCLIP: _FakeProcess(b"", returncode=1),
            images_mod._PNGPASTE: _FakeProcess(PNG),
        },
    )

    assert images_mod.read_clipboard_image() == PNG
    assert commands == list(images_mod._CLIPBOARD_IMAGE_COMMANDS)


def test_capture_reads_at_most_one_byte_past_the_limit(monkeypatch) -> None:
    process = _FakeProcess(PNG)
    _popen(monkeypatch, {images_mod._WL_PASTE: process})

    assert images_mod.read_clipboard_image() == PNG
    assert process.stdout.read_sizes == [MAX_IMAGE_BYTES + 1]


def test_read_clipboard_image_rejects_an_oversized_payload(monkeypatch) -> None:
    oversized = PNG + b"\x00" * MAX_IMAGE_BYTES
    _popen(monkeypatch, {images_mod._WL_PASTE: _FakeProcess(oversized)})

    with pytest.raises(ValueError, match="10 MiB"):
        images_mod.read_clipboard_image()


def test_read_clipboard_image_reports_missing_tools(monkeypatch) -> None:
    _popen(
        monkeypatch,
        {
            command: (lambda: _FakeProcess(missing=True))
            for command in images_mod._CLIPBOARD_IMAGE_COMMANDS
        },
    )

    with pytest.raises(ValueError, match="No clipboard image tool found"):
        images_mod.read_clipboard_image()


def test_a_present_but_failing_tool_is_not_reported_as_missing(monkeypatch) -> None:
    _popen(
        monkeypatch,
        {
            command: _FakeProcess(b"", returncode=1)
            for command in images_mod._CLIPBOARD_IMAGE_COMMANDS
        },
    )

    with pytest.raises(ValueError, match="did not return an image"):
        images_mod.read_clipboard_image()


@pytest.mark.parametrize("fault", [KeyboardInterrupt, OSError])
def test_an_interrupted_or_failed_read_kills_and_reaps_the_child(
    monkeypatch, fault
) -> None:
    process = _FakeProcess(PNG, finished=False)
    process.stdout.read = lambda size=-1: (_ for _ in ()).throw(fault())
    _popen(monkeypatch, {images_mod._WL_PASTE: process})

    with pytest.raises(fault):
        images_mod._clipboard_command_output(images_mod._WL_PASTE)

    assert process.killed is True
    assert process.waited >= 1


def test_a_child_that_closes_stdout_before_exiting_is_not_killed(monkeypatch) -> None:
    monkeypatch.setattr(images_mod, "_CLIPBOARD_TIMEOUT_SECONDS", 3.0)
    script = (
        "import os, sys, time\n"
        "os.write(1, b'PAYLOAD')\n"
        "os.close(1)\n"
        "time.sleep(0.15)\n"
        "sys.exit(0)\n"
    )

    data, status = images_mod._clipboard_command_output((sys.executable, "-c", script))

    assert status == "ok"
    assert data == b"PAYLOAD"


def test_a_hung_tool_is_killed_and_the_timeout_surfaces(monkeypatch) -> None:
    monkeypatch.setattr(images_mod, "_CLIPBOARD_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(
        images_mod,
        "_CLIPBOARD_IMAGE_COMMANDS",
        ((sys.executable, "-c", "import time; time.sleep(30)"),),
    )

    started = time.monotonic()
    with pytest.raises(ValueError, match="failed or timed out"):
        images_mod.read_clipboard_image()
    assert time.monotonic() - started < 10


def test_a_spawn_fault_that_is_not_a_missing_binary_escapes(monkeypatch) -> None:
    def denied(command, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(subprocess, "Popen", denied)

    with pytest.raises(PermissionError):
        images_mod.read_clipboard_image()


def test_include_clipboard_appends_the_clipboard_image(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(images_mod, "read_clipboard_image", lambda: PNG)
    path = _write(tmp_path / "shot.png")

    images = images_mod.read_selected_images([str(path)], include_clipboard=True)

    assert [mime for _, mime in images] == ["image/png", "image/png"]


def test_include_clipboard_keeps_the_four_image_bound(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(images_mod, "read_clipboard_image", lambda: PNG)
    paths = [str(_write(tmp_path / f"{index}.png")) for index in range(MAX_TURN_IMAGES)]

    with pytest.raises(ValueError, match="at most four"):
        images_mod.read_selected_images(paths, include_clipboard=True)


def test_include_clipboard_keeps_the_combined_byte_bound(monkeypatch, tmp_path) -> None:
    payload = _large_png()
    monkeypatch.setattr(images_mod, "read_clipboard_image", lambda: payload)
    first = _write(tmp_path / "a.png", payload)
    second = _write(tmp_path / "b.png", payload)

    with pytest.raises(ValueError, match="20 MiB"):
        images_mod.read_selected_images(
            [str(first), str(second)], include_clipboard=True
        )


def test_include_clipboard_rejects_a_non_image(monkeypatch) -> None:
    monkeypatch.setattr(images_mod, "read_clipboard_image", lambda: b"not an image")

    with pytest.raises(ValueError, match="PNG, JPEG or WebP"):
        images_mod.read_selected_images([], include_clipboard=True)


def test_run_parser_accepts_the_clipboard_flag() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    run_command.register(subparsers)

    parsed = parser.parse_args(["run", "--image-clipboard"])

    assert parsed.image_clipboard is True


def test_cli_reads_the_clipboard_before_the_turn_starts(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_read(paths, *, include_clipboard=False):
        seen["include"] = include_clipboard
        raise RuntimeError("stop-after-read")

    monkeypatch.setattr(images_mod, "read_selected_images", fake_read)
    monkeypatch.setattr(
        run_command, "run_turn", lambda **kwargs: pytest.fail("the turn must not start")
    )
    args = SimpleNamespace(prompt="describe", file="", image=None, image_clipboard=True)

    with pytest.raises(RuntimeError, match="stop-after-read"):
        run_command.run_openminion(args)

    assert seen == {"include": True}


def test_cli_without_a_prompt_or_image_stops_before_reading_the_clipboard(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        images_mod, "read_selected_images", lambda *a, **k: pytest.fail("must not read")
    )
    args = SimpleNamespace(prompt="", file="", image=None, image_clipboard=False)

    with pytest.raises(RuntimeError, match="Provide a prompt"):
        run_command.run_openminion(args)


def _runtime(tmp_path: Path) -> Any:
    return SimpleNamespace(
        api_runtime=SimpleNamespace(data_root=tmp_path), session_id="s-1"
    )


def _large_png() -> bytes:
    return PNG + b"\x00" * (MAX_IMAGE_BYTES - 1024 - len(PNG))


def test_focus_paste_stores_one_image_and_reports_the_draft(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(
        "openminion.cli.interactive.terminal.shell.images.read_clipboard_image",
        lambda: PNG,
    )
    composer = TerminalComposer()

    message = paste_clipboard_image(
        _runtime(tmp_path), composer, working_dir=str(tmp_path)
    )

    stored = Path(composer.image_paths[0])
    assert message == f"Selected images: 1. {stored.name}"
    assert stored.parent == tmp_path / "images" / "s-1"


def test_focus_paste_removes_its_capture_when_the_draft_is_full(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(
        "openminion.cli.interactive.terminal.shell.images.read_clipboard_image",
        lambda: PNG,
    )
    composer = TerminalComposer()
    composer.image_paths = [
        str(_write(tmp_path / f"{index}.png")) for index in range(4)
    ]

    with pytest.raises(ValueError, match="at most four"):
        paste_clipboard_image(_runtime(tmp_path), composer, working_dir=str(tmp_path))

    assert not (tmp_path / "images" / "s-1").exists()


def test_focus_paste_removes_its_capture_when_the_batch_is_too_large(
    monkeypatch, tmp_path
) -> None:
    payload = _large_png()
    monkeypatch.setattr(
        "openminion.cli.interactive.terminal.shell.images.read_clipboard_image",
        lambda: payload,
    )
    composer = TerminalComposer()
    composer.image_paths = [
        str(_write(tmp_path / "a.png", payload)),
        str(_write(tmp_path / "b.png", payload)),
    ]

    with pytest.raises(ValueError, match="20 MiB"):
        paste_clipboard_image(_runtime(tmp_path), composer, working_dir=str(tmp_path))

    assert not list((tmp_path / "images" / "s-1").glob("*.png"))


def test_focus_paste_cleans_up_when_a_previous_selection_disappeared(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(
        "openminion.cli.interactive.terminal.shell.images.read_clipboard_image",
        lambda: PNG,
    )
    composer = TerminalComposer()
    gone = tmp_path / "gone.png"
    composer.image_paths = [str(gone)]

    with pytest.raises(FileNotFoundError):
        paste_clipboard_image(_runtime(tmp_path), composer, working_dir=str(tmp_path))

    assert not list((tmp_path / "images" / "s-1").glob("*.png"))
    assert composer.image_paths == [str(gone)]


def test_two_focus_pastes_keep_distinct_captures(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "openminion.cli.interactive.terminal.shell.images.read_clipboard_image",
        lambda: PNG,
    )
    composer = TerminalComposer()

    paste_clipboard_image(_runtime(tmp_path), composer, working_dir=str(tmp_path))
    paste_clipboard_image(_runtime(tmp_path), composer, working_dir=str(tmp_path))

    first, second = (Path(path) for path in composer.image_paths)
    assert first != second
    assert first.is_file() and second.is_file()


def test_pasted_bytes_round_trip_through_the_draft_bounds(tmp_path) -> None:
    stored = store_image_bytes(
        PNG, data_root=tmp_path, session_id="s-1", name="clipboard-1"
    )

    assert stored.is_file()
    assert images_mod.read_selected_images([str(stored)])[0][1] == "image/png"
