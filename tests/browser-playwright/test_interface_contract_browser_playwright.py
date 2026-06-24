from __future__ import annotations

from openminion.tools.browser.providers.playwright.artifacts import ArtifactWriter
from openminion.tools.browser.providers.playwright.provider import PlaywrightProvider


def test_playwright_provider_exposes_browser_lifecycle_contract() -> None:
    required_methods = (
        "instance_start",
        "instance_list",
        "instance_stop",
        "instance_kill",
        "tab_new",
        "tab_list",
        "tab_navigate",
        "tab_action",
    )
    for method_name in required_methods:
        assert callable(getattr(PlaywrightProvider, method_name, None))


def test_artifact_writer_keeps_browser_outputs_workspace_local(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    writer = ArtifactWriter(
        workspace_root=str(workspace),
        downloads_dir=str(workspace / "downloads"),
        screenshots_dir=str(workspace / "screenshots"),
        pdf_dir=str(workspace / "pdf"),
        traces_dir=str(workspace / "traces"),
    )
    writer.ensure_dirs()

    artifact = writer.write_screenshot(b"png-data", output_path="screenshots/test.png")

    assert artifact["path"] == "screenshots/test.png"
    assert artifact["kind"] == "screenshot"
    assert "ref" not in artifact
    assert not artifact["path"].startswith("artifact://sha256/")
