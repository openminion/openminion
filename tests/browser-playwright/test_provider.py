from __future__ import annotations

from pathlib import Path
import threading

import pytest

from openminion.tools.browser import BrowserProviderContext
from openminion.tools.browser.models import (
    BrowserAction,
    InstanceSpec,
    NavigateOptions,
    OutputOptions,
    SnapshotOptions,
    TextOptions,
)
from openminion.tools.browser.providers.playwright.config import (
    provider_config_from_mapping,
)
from openminion.tools.browser.providers.playwright.provider import (
    BrowserTabLockedError,
    PlaywrightProvider,
)


class _FakeResponse:
    def __init__(self, status: int = 200) -> None:
        self.status = status


class _FakeKeyboard:
    def __init__(self, page) -> None:
        self.page = page

    def press(self, key: str) -> None:
        self.page.events.append(("keyboard.press", key))


class _FakeMouse:
    def __init__(self, page) -> None:
        self.page = page

    def wheel(self, dx: int, dy: int) -> None:
        self.page.events.append(("mouse.wheel", dx, dy))


class _FakeLocator:
    def __init__(self, page, selector: str) -> None:
        self.page = page
        self.selector = selector

    def click(self, timeout: int | None = None) -> None:
        self.page.events.append(("click", self.selector, timeout))

    def fill(self, value: str, timeout: int | None = None) -> None:
        self.page.events.append(("fill", self.selector, value, timeout))

    def press(self, key: str, timeout: int | None = None) -> None:
        self.page.events.append(("press", self.selector, key, timeout))

    def select_option(self, value=None, timeout: int | None = None) -> None:
        self.page.events.append(("select", self.selector, value, timeout))

    def scroll_into_view_if_needed(self, timeout: int | None = None) -> None:
        self.page.events.append(("scroll", self.selector, timeout))

    def wait_for(self, timeout: int | None = None, state: str | None = None) -> None:
        self.page.events.append(("wait_for", self.selector, timeout, state))

    def hover(self, timeout: int | None = None) -> None:
        self.page.events.append(("hover", self.selector, timeout))

    def set_input_files(self, files, timeout: int | None = None) -> None:
        self.page.events.append(("upload", self.selector, list(files), timeout))


class _FakeAccessibility:
    def snapshot(self, interesting_only: bool = True):
        del interesting_only
        return {
            "role": "document",
            "name": "Example",
            "children": [
                {"role": "button", "name": "Sign in", "hidden": False},
                {"role": "textbox", "name": "Email", "hidden": False},
            ],
        }


class _FakePage:
    def __init__(self) -> None:
        self.url = "about:blank"
        self._title = "Blank"
        self.events: list[tuple] = []
        self.keyboard = _FakeKeyboard(self)
        self.mouse = _FakeMouse(self)
        self.accessibility = _FakeAccessibility()

    def goto(self, url: str, timeout: int | None = None):
        self.url = url
        self._title = "Example"
        self.events.append(("goto", url, timeout))
        return _FakeResponse(status=200)

    def title(self) -> str:
        return self._title

    def inner_text(self, selector: str, timeout: int | None = None) -> str:
        self.events.append(("inner_text", selector, timeout))
        return "hello world"

    def screenshot(self, full_page: bool = True) -> bytes:
        self.events.append(("screenshot", full_page))
        return b"png"

    def pdf(self) -> bytes:
        self.events.append(("pdf",))
        return b"pdf"

    def evaluate(self, script: str):
        if "nodes:" in script:
            return {
                "nodes": [
                    {
                        "role": "button",
                        "name": "Continue",
                        "visible": True,
                        "hint": "button",
                    },
                ],
                "visible_text": "hello world",
                "title": "Example",
                "url": self.url,
            }
        return "hello world"

    def locator(self, selector: str) -> _FakeLocator:
        return _FakeLocator(self, selector)

    def get_by_role(self, role: str, **kwargs) -> _FakeLocator:
        return _FakeLocator(self, f"role:{role}:{kwargs.get('name', '')}")

    def wait_for_timeout(self, timeout: int) -> None:
        self.events.append(("wait_timeout", timeout))

    def close(self) -> None:
        self.events.append(("close",))


class _FakeContext:
    def __init__(self, *, browser=None) -> None:
        self.browser = browser
        self.pages: list[_FakePage] = []
        self.default_timeout = None
        self.default_nav_timeout = None

    def set_default_timeout(self, timeout: int) -> None:
        self.default_timeout = timeout

    def set_default_navigation_timeout(self, timeout: int) -> None:
        self.default_nav_timeout = timeout

    def new_page(self) -> _FakePage:
        page = _FakePage()
        self.pages.append(page)
        return page

    def close(self) -> None:
        pass


class _FakeBrowser:
    def __init__(self) -> None:
        self.contexts: list[_FakeContext] = []

    def new_context(self, **kwargs) -> _FakeContext:
        del kwargs
        ctx = _FakeContext(browser=self)
        self.contexts.append(ctx)
        return ctx

    def close(self) -> None:
        pass


class _FakeBrowserType:
    def launch(self, **kwargs) -> _FakeBrowser:
        del kwargs
        return _FakeBrowser()

    def launch_persistent_context(self, **kwargs) -> _FakeContext:
        del kwargs
        return _FakeContext(browser=None)


class _FakePlaywright:
    def __init__(self) -> None:
        self.chromium = _FakeBrowserType()
        self.firefox = _FakeBrowserType()
        self.webkit = _FakeBrowserType()

    def start(self):
        return self


def _provider(tmp_path: Path, *, browser: str = "chromium") -> PlaywrightProvider:
    cfg = provider_config_from_mapping(
        {
            "workspace_root": str(tmp_path),
            "browser": browser,
            "persistent": {"enabled": False},
            "network": {"mode": "allow_all"},
        }
    )
    return PlaywrightProvider(cfg, playwright_factory=lambda: _FakePlaywright())


def test_lifecycle_snapshot_action_and_artifacts(tmp_path: Path) -> None:
    provider = _provider(tmp_path)

    ready = provider.ensure_ready()
    assert ready["ok"] is True

    instance = provider.instance_start(mode="headed")
    instance_id = instance["instance"]["id"]

    tab = provider.tab_new(instance_id=instance_id, url="https://example.com")
    tab_id = tab["tab"]["id"]

    snap = provider.snapshot(tab_id=tab_id, mode="a11y")
    assert snap["snapshot"]["nodes"]
    assert snap["snapshot"]["action_candidates"]

    node_id = snap["snapshot"]["action_candidates"][0]
    action = provider.action(
        tab_id=tab_id, action={"kind": "click", "node_id": node_id}
    )
    assert action["action"]["kind"] == "click"

    shot = provider.screenshot(tab_id=tab_id)
    assert shot["artifact"]["kind"] == "screenshot"
    assert shot["artifact"]["path"].startswith(".openminion")

    pdf = provider.pdf(tab_id=tab_id)
    assert pdf["artifact"]["kind"] == "pdf"

    upload_file = tmp_path / "upload.txt"
    upload_file.write_text("ok", encoding="utf-8")
    uploaded = provider.upload(
        tab_id=tab_id, files=[str(upload_file)], selector="#file"
    )
    assert uploaded["uploaded"] == ["upload.txt"]

    stopped = provider.instance_stop(instance_id=instance_id)
    assert stopped["stopped"] is True


def test_instance_list_and_kill(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    i1 = provider.instance_start(mode="headed")["instance"]["id"]
    i2 = provider.instance_start(mode="headless")["instance"]["id"]

    listed = provider.instance_list()
    assert [row["id"] for row in listed["instances"]] == [i1, i2]

    killed = provider.instance_kill(instance_id=i1)
    assert killed["killed"] is True
    assert killed["stopped"] is True


def test_lock_blocks_other_thread_actions(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    instance_id = provider.instance_start()["instance"]["id"]
    tab_id = provider.tab_new(instance_id=instance_id)["tab"]["id"]

    assert provider.lock(tab_id=tab_id)["locked"] is True

    got_lock_error = {"value": False}

    def _run_action() -> None:
        try:
            provider.action(
                tab_id=tab_id, action={"kind": "click", "selector": "button"}
            )
        except BrowserTabLockedError:
            got_lock_error["value"] = True

    thread = threading.Thread(target=_run_action)
    thread.start()
    thread.join(timeout=2)

    assert got_lock_error["value"] is True
    provider.unlock(tab_id=tab_id)


def test_rejects_output_paths_outside_workspace(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    instance_id = provider.instance_start()["instance"]["id"]
    tab_id = provider.tab_new(instance_id=instance_id)["tab"]["id"]

    with pytest.raises(PermissionError):
        provider.screenshot(tab_id=tab_id, output_path="/tmp/outside.png")


def test_pdf_not_supported_for_non_chromium(tmp_path: Path) -> None:
    provider = _provider(tmp_path, browser="firefox")
    instance_id = provider.instance_start()["instance"]["id"]
    tab_id = provider.tab_new(instance_id=instance_id)["tab"]["id"]

    with pytest.raises(RuntimeError, match="pdf_not_supported"):
        provider.pdf(tab_id=tab_id)


def test_resource_selectors_include_domain_paths_and_upload_reads(
    tmp_path: Path,
) -> None:
    provider = _provider(tmp_path)
    selectors = provider.resource_selectors(
        {
            "op": "tab.upload",
            "url": "https://example.com/login",
            "options": {"files": ["input/a.txt", "input/b.txt"]},
            "output": {"path": "artifacts/shot.png"},
        }
    )

    assert "example.com" in selectors.domains
    assert "https" in selectors.protocols
    assert "artifacts/shot.png" in selectors.paths_write
    assert selectors.paths_read == ("input/a.txt", "input/b.txt")


def test_headless_and_default_mode_behavior(tmp_path: Path) -> None:
    provider_default_true = _provider(tmp_path)
    assert provider_default_true.config.headless_default is True

    instance = provider_default_true.instance_start(mode="headless")
    assert instance["instance"]["mode"] == "headless"

    instance = provider_default_true.instance_start(mode="headed")
    assert instance["instance"]["mode"] == "headed"

    instance_no_mode = provider_default_true.instance_start()
    assert instance_no_mode["instance"]["mode"] == "headless"


def test_non_headless_default_configuration(tmp_path: Path) -> None:
    from openminion.tools.browser.providers.playwright.config import (
        provider_config_from_mapping,
    )

    cfg = provider_config_from_mapping(
        {"workspace_root": str(tmp_path), "headless_default": False}
    )
    provider = PlaywrightProvider(cfg, playwright_factory=lambda: _FakePlaywright())

    assert provider.config.headless_default is False

    instance = provider.instance_start(mode="headless")
    assert instance["instance"]["mode"] == "headless"

    instance = provider.instance_start(mode="headed")
    assert instance["instance"]["mode"] == "headed"

    instance_no_mode = provider.instance_start()
    assert instance_no_mode["instance"]["mode"] == "headed"


def test_provider_config_resolves_data_root_from_openminion_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home_root = tmp_path / "home-root"
    workspace_root = tmp_path / "workspace"
    home_root.mkdir(parents=True, exist_ok=True)
    workspace_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OPENMINION_HOME", str(home_root))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    cfg = provider_config_from_mapping({"workspace_root": str(workspace_root)})

    expected_data_root = (home_root / ".openminion").resolve()
    assert (
        Path(cfg.persistent.user_data_dir)
        == (
            expected_data_root / "browser-playwright" / "profiles" / "default"
        ).resolve()
    )
    expected_workspace_root = (workspace_root / ".openminion").resolve()
    assert (
        Path(cfg.downloads.dir)
        == (expected_workspace_root / "browser-playwright" / "downloads").resolve()
    )
    assert (
        Path(cfg.artifacts.root_dir)
        == (expected_workspace_root / "browser-playwright" / "artifacts").resolve()
    )


def test_provider_config_uses_injected_env_values(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace-from-env"
    data_root = tmp_path / "data-from-env"

    cfg = provider_config_from_mapping(
        {},
        env={
            "OPENMINION_WORKSPACE_ROOT": str(workspace_root),
            "OPENMINION_DATA_ROOT": str(data_root),
        },
    )

    assert Path(cfg.workspace_root) == workspace_root.resolve()
    assert (
        Path(cfg.persistent.user_data_dir)
        == (data_root / "browser-playwright" / "profiles" / "default").resolve()
    )


def test_mode_aliases_and_unknown_fallbacks(tmp_path: Path) -> None:
    cfg_true = provider_config_from_mapping(
        {"workspace_root": str(tmp_path), "headless_default": True}
    )
    provider_true = PlaywrightProvider(
        cfg_true, playwright_factory=lambda: _FakePlaywright()
    )

    for mode in ["headful", "ui"]:
        instance = provider_true.instance_start(mode=mode)
        assert instance["instance"]["mode"] == "headed"

    for mode in ["automation"]:
        instance = provider_true.instance_start(mode=mode)
        assert instance["instance"]["mode"] == "headless"

    instance = provider_true.instance_start(mode="unknown-token")
    assert instance["instance"]["mode"] == "headless"

    cfg_false = provider_config_from_mapping(
        {"workspace_root": str(tmp_path), "headless_default": False}
    )
    provider_false = PlaywrightProvider(
        cfg_false, playwright_factory=lambda: _FakePlaywright()
    )

    instance = provider_false.instance_start(mode="junk")
    assert instance["instance"]["mode"] == "headed"


def test_instance_start_does_not_pass_downloads_path_to_new_context(
    tmp_path: Path,
) -> None:

    class _StrictNewContext(_FakeContext):
        pass

    class _StrictBrowser(_FakeBrowser):
        def __init__(self) -> None:
            super().__init__()
            self.downloads_path: str | None = None

        def new_context(self, **kwargs):
            if "downloads_path" in kwargs:
                raise TypeError(
                    "Browser.new_context() got an unexpected keyword argument "
                    "'downloads_path'"
                )
            ctx = _StrictNewContext(browser=self)
            self.contexts.append(ctx)
            return ctx

    class _StrictBrowserType(_FakeBrowserType):
        def launch(self, **kwargs):
            del kwargs
            return _StrictBrowser()

    class _StrictPlaywright(_FakePlaywright):
        def __init__(self) -> None:
            self.chromium = _StrictBrowserType()
            self.firefox = _StrictBrowserType()
            self.webkit = _StrictBrowserType()

    cfg = provider_config_from_mapping(
        {
            "workspace_root": str(tmp_path),
            "browser": "chromium",
            "persistent": {"enabled": False},
            "network": {"mode": "allow_all"},
        }
    )
    provider = PlaywrightProvider(cfg, playwright_factory=lambda: _StrictPlaywright())

    instance = provider.instance_start(mode="headless")
    assert instance["instance"]["id"]


def test_provider_supports_canonical_browser_protocol_signatures(
    tmp_path: Path,
) -> None:
    provider = _provider(tmp_path)
    ctx = BrowserProviderContext(workspace_root=str(tmp_path))

    ensure = provider.ensure_ready(ctx)
    assert "ok" in ensure

    started = provider.instance_start(ctx, InstanceSpec(mode="headless"))
    instance_id = started["instance"]["id"]
    listed = provider.instance_list(ctx)
    assert listed["instances"] and listed["instances"][0]["id"] == instance_id

    tab = provider.tab_new(ctx, instance_id=instance_id, url="https://example.com")
    tab_id = tab["tab"]["id"]

    navigated = provider.tab_navigate(
        ctx,
        tab_id=tab_id,
        url="https://example.com/docs",
        options=NavigateOptions(timeout_ms=5000),
    )
    assert navigated["tab"]["id"] == tab_id

    snap = provider.tab_snapshot(
        ctx,
        tab_id=tab_id,
        options=SnapshotOptions(mode="a11y", max_nodes=200, max_text_chars=5000),
    )
    assert "snapshot" in snap

    text = provider.tab_text(
        ctx, tab_id=tab_id, options=TextOptions(mode="visible_text")
    )
    assert "text" in text and "content" in text["text"]

    shot = provider.tab_screenshot(
        ctx, tab_id=tab_id, options=OutputOptions(path="shots/example.png")
    )
    assert shot["artifact"]["kind"] == "screenshot"

    action = provider.tab_action(
        ctx,
        tab_id=tab_id,
        action=BrowserAction(kind="click", target={"selector": "button"}),
    )
    assert action["action"]["kind"] == "click"

    locked = provider.tab_lock(ctx, tab_id=tab_id)
    assert locked["locked"] is True
    unlocked = provider.tab_unlock(ctx, tab_id=tab_id)
    assert unlocked["locked"] is False

    killed = provider.instance_kill(ctx, instance_id=instance_id)
    assert killed["stopped"] is True
