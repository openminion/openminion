from types import SimpleNamespace

import pytest

from openminion.base.config import AgentProfileConfig, OpenMinionConfig
from openminion.cli.commands import run


def test_rejected_image_batch_revokes_partial_upload_without_turn_or_local_retry(
    monkeypatch, tmp_path
):
    config = OpenMinionConfig(
        agents={"agent": AgentProfileConfig(name="agent", provider="echo")},
        default_agent="agent",
    )
    calls = []
    monkeypatch.setattr(run, "_load_run_config", lambda args: config)
    monkeypatch.setattr(
        "openminion.cli.transport.images.read_selected_images",
        lambda paths, **kwargs: [(b"first", "image/png"), (b"second", "image/png")],
    )
    monkeypatch.setattr(
        "openminion.cli.transport.runtime_source.ensure_daemon_running",
        lambda *a, **kw: object(),
    )
    monkeypatch.setattr(
        run,
        "_run_inproc",
        lambda *a, **kw: pytest.fail("Rejected image must not retry locally"),
    )

    def request(**kwargs):
        calls.append(kwargs)
        if kwargs["method"] == "DELETE":
            return 200, {"ok": True}
        if len(calls) == 1:
            return 201, {
                "image": {"ref": "image://00000000-0000-4000-8000-000000000001"}
            }
        return 400, {
            "ok": False,
            "error": {"code": "INVALID_IMAGE", "message": "Invalid selected image"},
        }

    monkeypatch.setattr(run, "daemon_request", request)
    args = SimpleNamespace(
        prompt="inspect",
        image=["one.png", "two.png"],
        runtime_source="auto",
        config=None,
        home_root=str(tmp_path),
        data_root=str(tmp_path / "data"),
    )
    with pytest.raises(RuntimeError, match="Invalid selected image"):
        run.run_openminion(args)
    assert [call["method"] for call in calls] == ["POST", "POST", "DELETE"]
    assert (
        calls[-1]["path"]
        .split("?")[0]
        .endswith("/images/00000000-0000-4000-8000-000000000001")
    )


def test_focus_selection_removal_and_lost_file_do_not_send(tmp_path, monkeypatch):
    import asyncio
    from PIL import Image
    from openminion.api.runtime import APIRuntime
    from openminion.base.config import save_config
    from openminion.cli.interactive.runtime import OpenMinionRuntime
    from openminion.cli.interactive.terminal.composer import TerminalComposer

    config = OpenMinionConfig(
        agents={"agent": AgentProfileConfig(name="agent", provider="echo")},
        default_agent="agent",
    )
    config.runtime.process_mode = "single-process"
    config.runtime.daemon_auto_start = False
    config.storage.path = str(tmp_path / "state.db")
    config_path = tmp_path / "agents.json"
    save_config(config, str(config_path))
    path = tmp_path / "image.png"
    Image.new("RGB", (4, 4), "blue").save(path)
    composer = TerminalComposer(color=False)
    assert "1. image.png" in composer.manage_images(
        "/image add image.png", working_dir=str(tmp_path)
    )
    assert (
        composer.manage_images("/image remove 1", working_dir=str(tmp_path))
        == "Selected images: none"
    )
    assert composer.image_paths == []
    composer.manage_images("/image add image.png", working_dir=str(tmp_path))
    path.unlink()
    with pytest.raises(FileNotFoundError):
        composer.manage_images("/image add absent.png", working_dir=str(tmp_path))
    api = APIRuntime.from_config_path(
        str(config_path), home_root=tmp_path, data_root=tmp_path / "data"
    )
    try:
        focus = OpenMinionRuntime(
            api, channel="console", target="test", agent_id="agent", session_id="images"
        )
        monkeypatch.setattr(
            focus,
            "_send_message_impl",
            lambda *a, **kw: pytest.fail("A lost image must not dispatch a turn"),
        )

        async def send():
            return [
                part
                async for part in focus.send_message(
                    "", image_paths=composer.image_paths
                )
            ]

        with pytest.raises(FileNotFoundError):
            asyncio.run(send())
    finally:
        api.close()
    assert composer.image_paths == [str(path)]

    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "openminion",
            "--config",
            str(config_path),
            "--home-root",
            str(tmp_path),
            "--data-root",
            str(tmp_path / "data"),
            "run",
            "--image",
            str(path),
            "",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert "image.png" in result.stderr
