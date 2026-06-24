from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from openminion.tools.browser import (
    BrowserProviderRegistry,
    BrowserRouter,
    BrowserRoutingConfig,
)
from openminion.tools.browser.tool import BrowserTool
from openminion.tools.browser.providers.playwright.provider import PlaywrightProvider
from openminion.tools.browser.providers.playwright.config import (
    provider_config_from_mapping,
)

# Re-use the fake playwright from test_provider if possible, or redefine it here.
# Since we want a self-contained test, let's redefine a minimal version.


@dataclass
class ToolContext:
    runtime: object | None = None
    trace_id: str = ""
    session_id: str = ""
    extras: dict[str, object] = field(default_factory=dict)


class _FakeBrowser:
    def __init__(self, headless: bool) -> None:
        self.headless = headless
        self.contexts = []

    def new_context(self, **kwargs):
        ctx = _FakeContext(browser=self)
        self.contexts.append(ctx)
        return ctx

    def close(self) -> None:
        pass


class _FakeContext:
    def __init__(self, browser=None) -> None:
        self.browser = browser
        self.pages = []
        self.default_timeout = None
        self.default_nav_timeout = None

    def set_default_timeout(self, timeout: int) -> None:
        self.default_timeout = timeout

    def set_default_navigation_timeout(self, timeout: int) -> None:
        self.default_nav_timeout = timeout

    def new_page(self):
        return None

    def close(self) -> None:
        pass


class _FakeBrowserType:
    def __init__(self) -> None:
        self.launch_calls = []

    def launch(self, **kwargs) -> _FakeBrowser:
        self.launch_calls.append(kwargs)
        return _FakeBrowser(headless=kwargs.get("headless", True))

    def launch_persistent_context(self, **kwargs) -> _FakeContext:
        self.launch_calls.append(kwargs)
        return _FakeContext(browser=None)


class _FakePlaywright:
    def __init__(self) -> None:
        self.chromium = _FakeBrowserType()
        self.firefox = _FakeBrowserType()
        self.webkit = _FakeBrowserType()

    def start(self):
        return self


def test_canonical_invocation_reaches_playwright_provider_with_mode(
    tmp_path: Path,
) -> None:
    # 1. Setup real PlaywrightProvider with fake playwright
    fake_pw = _FakePlaywright()
    cfg = provider_config_from_mapping(
        {
            "workspace_root": str(tmp_path),
            "browser": "chromium",
            "persistent": {"enabled": False},
            "headless_default": True,
        }
    )
    provider = PlaywrightProvider(cfg, playwright_factory=lambda: fake_pw)

    # 2. Setup real BrowserTool with the real provider registered
    reg = BrowserProviderRegistry()
    reg.register(provider)
    tool = BrowserTool(
        router=BrowserRouter(
            reg, config=BrowserRoutingConfig(default_provider="playwright")
        )
    )

    # 3. Target canonical invocation: mode="headed"
    ctx = ToolContext(extras={"workspace_root": str(tmp_path)})
    result_headed = tool.execute({"op": "instance.start", "mode": "headed"}, ctx)

    assert result_headed.ok is True
    assert result_headed.data["instance"]["mode"] == "headed"
    assert fake_pw.chromium.launch_calls[-1]["headless"] is False

    # 4. Target canonical invocation: mode="headless"
    result_headless = tool.execute({"op": "instance.start", "mode": "headless"}, ctx)

    assert result_headless.ok is True
    assert result_headless.data["instance"]["mode"] == "headless"
    assert fake_pw.chromium.launch_calls[-1]["headless"] is True

    # 5. Target canonical invocation: mode="ui" (alias)
    result_ui = tool.execute({"op": "instance.start", "mode": "ui"}, ctx)

    assert result_ui.ok is True
    assert result_ui.data["instance"]["mode"] == "headed"
    assert fake_pw.chromium.launch_calls[-1]["headless"] is False
