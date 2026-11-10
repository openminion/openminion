"""Real signal delivery: the entry point cancels; an imported main() leaves signals alone."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SRC = str(Path(__file__).resolve().parents[2] / "src")

CHILD = textwrap.dedent(
    """
    import signal
    import sys
    import time
    from types import SimpleNamespace

    import openminion.cli.main as m

    command = sys.argv[1]
    mode = sys.argv[2]

    def handler(args):
        print("ready", flush=True)
        time.sleep(20)
        return 0

    def parser_error(*args, **kwargs):
        raise SystemExit(2)

    parser = SimpleNamespace(
        parse_args=lambda argv=None: SimpleNamespace(
            command=command,
            handler=handler,
            add_dir=None,
            allow_unsandboxed_exec=False,
            config=None,
            home_root=None,
            data_root=None,
        ),
        error=parser_error,
    )
    m.build_parser = lambda: parser
    m._prepare_runtime_roots = lambda *a, **k: (None, None, None, None, None)

    if mode == "host":
        signal.signal(
            signal.SIGTERM,
            lambda *a: (print("host-cleanup", flush=True), sys.exit(7)),
        )
    elif mode == "ignore-int":
        # A shell can launch a child with SIGINT ignored; owning cancellation must not
        # depend on that inherited disposition.
        signal.signal(signal.SIGINT, signal.SIG_IGN)

    if mode in {"entry", "ignore-int"}:
        raise SystemExit(m.entrypoint())
    try:
        raise SystemExit(m.main([command]))
    finally:
        print("host-cleanup", flush=True)
    """
)


def _spawn(
    tmp_path: Path, command: str, *, mode: str = "host"
) -> subprocess.Popen[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC
    env["HOME"] = str(tmp_path)
    env["NO_COLOR"] = "1"
    return subprocess.Popen(
        [sys.executable, "-c", CHILD, command, mode],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )


def _deliver(tmp_path: Path, command: str, *, mode: str, sig: int):
    process = _spawn(tmp_path, command, mode=mode)
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "ready"
        process.send_signal(sig)
        out, err = process.communicate(timeout=30)
    finally:
        if process.poll() is None:
            process.kill()
    return process.returncode, out, err


@pytest.mark.parametrize("command", ["status", "service", "run"])
def test_embedded_call_keeps_the_host_signal_handler(
    tmp_path: Path, command: str
) -> None:
    """An imported main() never takes a host handler, whatever the command."""
    code, out, err = _deliver(tmp_path, command, mode="host", sig=signal.SIGTERM)

    assert code == 7
    assert "host-cleanup" in out
    assert "openminion: terminated" not in err


def test_embedded_run_with_default_dispositions_runs_host_cleanup(
    tmp_path: Path,
) -> None:
    """Default dispositions unwind as KeyboardInterrupt, so the host's cleanup runs."""
    code, out, _err = _deliver(tmp_path, "run", mode="plain", sig=signal.SIGINT)

    assert "host-cleanup" in out
    assert code == 130


def test_entry_point_exits_143_on_sigterm(tmp_path: Path) -> None:
    code, out, err = _deliver(tmp_path, "run", mode="entry", sig=signal.SIGTERM)

    assert code == 143
    assert "openminion: terminated" in err
    assert "host-cleanup" not in out


def test_entry_point_exits_130_on_sigint(tmp_path: Path) -> None:
    code, _out, err = _deliver(tmp_path, "run", mode="entry", sig=signal.SIGINT)

    assert code == 130
    assert "openminion: cancelled" in err


def test_entry_point_cancels_even_when_sigint_was_ignored(tmp_path: Path) -> None:
    """An inherited SIG_IGN must not disable cancellation in the entry point."""
    code, _out, err = _deliver(tmp_path, "run", mode="ignore-int", sig=signal.SIGTERM)

    assert code == 143
    assert "openminion: terminated" in err
