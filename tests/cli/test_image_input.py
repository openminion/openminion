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
        lambda paths: [(b"first", "image/png"), (b"second", "image/png")],
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
