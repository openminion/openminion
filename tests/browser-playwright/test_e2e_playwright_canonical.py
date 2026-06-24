from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from openminion.tools.browser import (
    BrowserProviderRegistry,
    BrowserRouter,
    BrowserRoutingConfig,
)
from openminion.tools.browser.tool import BrowserTool
from openminion.tools.browser import tool as browser_tool_module

playwright_sync = pytest.importorskip("playwright.sync_api")

from openminion.tools.browser.providers.playwright.config import (  # noqa: E402
    provider_config_from_mapping,
)
from openminion.tools.browser.providers.playwright.provider import (  # noqa: E402
    PlaywrightProvider,
)


_BPGE_HTML = """<!doctype html>
<html><head><title>BPGE Smoke</title></head>
<body><h1 id="hdr">BPGE-07 smoke heading</h1>
<p id="body">deterministic local content for canonical browser e2e</p>
</body></html>
"""


@dataclass
class _ToolContext:
    runtime: object | None = None
    trace_id: str = "bpge-07"
    session_id: str = "bpge-07-session"
    extras: dict[str, object] = field(default_factory=dict)


def _chromium_available() -> bool:
    result: dict[str, bool] = {"ok": False}

    def _probe() -> None:
        try:
            p = playwright_sync.sync_playwright().start()
        except Exception:
            return
        try:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception:
                return
            browser.close()
            result["ok"] = True
        finally:
            try:
                p.stop()
            except Exception:
                pass

    t = threading.Thread(target=_probe, daemon=True)
    t.start()
    t.join(timeout=30)
    return result["ok"]


@pytest.fixture(scope="module")
def _chromium_ready() -> bool:
    if not _chromium_available():
        pytest.skip("chromium browser binary not available in this environment")
    return True


@pytest.fixture
def _real_playwright_browser_tool(tmp_path: Path):
    cfg = provider_config_from_mapping(
        {
            "workspace_root": str(tmp_path),
            "browser": "chromium",
            "persistent": {"enabled": False},
            "network": {"mode": "allow_all"},
            "headless_default": True,
        }
    )
    provider = PlaywrightProvider(cfg)

    registry = BrowserProviderRegistry()
    registry.register(provider)
    router = BrowserRouter(
        registry,
        config=BrowserRoutingConfig(default_provider="playwright"),
    )

    browser_tool_module._SESSION_STATE.clear()
    browser_tool_module._SESSION_STATE_LOADED_ROOTS.clear()
    tool = BrowserTool(router=router)
    try:
        yield tool, tmp_path
    finally:
        browser_tool_module._SESSION_STATE.clear()
        browser_tool_module._SESSION_STATE_LOADED_ROOTS.clear()


def _run_in_worker_thread(fn):
    box: dict[str, object] = {}

    def _runner() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join(timeout=120)
    if "error" in box:
        raise box["error"]  # type: ignore[misc]
    return box.get("value")


def test_canonical_browser_navigate_text_screenshot_against_local_file(
    _chromium_ready: bool,
    _real_playwright_browser_tool,
) -> None:
    tool, tmp_path = _real_playwright_browser_tool

    page = tmp_path / "bpge-smoke.html"
    page.write_text(_BPGE_HTML, encoding="utf-8")
    page_url = page.resolve().as_uri()

    def _scenario() -> dict[str, object]:
        started = tool.execute(
            {"op": "instance.start", "instance_spec": {"mode": "headless"}},
            _ToolContext(),
        )
        assert started.ok is True, started.error
        assert started.data.get("provider") == "playwright"

        # 2. tab.new navigates to the local page.
        new_tab = tool.execute(
            {"op": "tab.new", "url": page_url},
            _ToolContext(),
        )
        assert new_tab.ok is True, new_tab.error
        assert new_tab.data["provider"] == "playwright"
        tab_id = new_tab.data["tab"]["id"]
        assert tab_id

        # 3. tab.text returns bounded summary content.
        text_result = tool.execute(
            {
                "op": "tab.text",
                "tab_id": tab_id,
                "options": {"mode": "visible_text", "max_chars": 200},
            },
            _ToolContext(),
        )
        assert text_result.ok is True, text_result.error
        content = text_result.data["text"]["content"]
        assert isinstance(content, str)
        assert len(content) <= 500  # bounded summary, not unbounded page dump
        assert "BPGE-07 smoke heading" in content

        # 4. tab.screenshot writes an artifact under the workspace.
        shot = tool.execute(
            {
                "op": "tab.screenshot",
                "tab_id": tab_id,
                "options": {"path": "artifacts/bpge-07-shot.png"},
            },
            _ToolContext(),
        )
        assert shot.ok is True, shot.error
        artifact = shot.data["artifact"]
        assert artifact["kind"] == "screenshot"
        return {"artifact_path": artifact["path"]}

    result = _run_in_worker_thread(_scenario)
    assert isinstance(result, dict)
    artifact_path = Path(str(result["artifact_path"]))
    assert artifact_path.parts, "artifact path must not be empty"
    assert (
        ".openminion" in artifact_path.parts
        or artifact_path.parts[0] in {"artifacts", ".openminion"}
        or (
            artifact_path.is_absolute()
            and (
                str(artifact_path).startswith(str(tmp_path.resolve()))
                or "browser-playwright" in artifact_path.parts
            )
        )
    ), (
        f"artifact path {artifact_path!s} escapes the configured workspace/artifacts tree"
    )
    # Provider returns the path string for the screenshot artifact; the
    # file should exist on disk so callers can read it.
    if artifact_path.is_absolute() and artifact_path.exists():
        assert artifact_path.stat().st_size > 0
