from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from openminion.base.config.runtime.tool_family import ToolFamilyRuntimeConfig
from openminion.cli.presentation import browser as browser_ui
from openminion.tools.browser.constants import (
    OPENMINION_BROWSER_PLAYWRIGHT_LOCALE_ENV,
    OPENMINION_BROWSER_PLAYWRIGHT_TIMEZONE_ENV,
)


class _Tool:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def execute(self, payload, ctx):
        self.calls.append(dict(payload))
        data = {"data": {"op": payload["op"]}}
        if payload["op"] == "tab.list":
            data["tabs"] = [{"id": "t1", "title": "Example", "url": "https://e.test"}]
        elif payload["op"] == "tab.navigate":
            data["tab"] = {"id": payload.get("tab_id", "t1"), "url": payload["url"]}
        return SimpleNamespace(ok=True, error="", data=data)


def test_render_browser_status_uses_provider_and_sidecar_status(monkeypatch) -> None:
    monkeypatch.setattr(
        browser_ui,
        "provider_registry",
        lambda: SimpleNamespace(
            list_provider_ids=lambda: ["pinchtab"],
            get=lambda _provider_id: SimpleNamespace(provider_id="pinchtab"),
        ),
    )
    monkeypatch.setattr(
        browser_ui,
        "default_sidecar_manager",
        lambda **_kwargs: SimpleNamespace(status=lambda _name: {"ready": True}),
    )

    body = browser_ui.render_browser_command("status")

    assert "selected=pinchtab status=ready" in body
    assert "Providers: pinchtab" in body


def test_browser_status_reports_selected_playwright_context(monkeypatch) -> None:
    playwright = SimpleNamespace(
        provider_id="playwright",
        config=SimpleNamespace(locale="en-US", timezone_id="America/Los_Angeles"),
        ensure_ready=lambda _ctx: {"ok": True},
    )
    registry = SimpleNamespace(
        list_provider_ids=lambda: ["pinchtab", "playwright"],
        get=lambda provider_id: (
            playwright
            if provider_id == "playwright"
            else SimpleNamespace(provider_id="pinchtab")
        ),
    )
    monkeypatch.setattr(browser_ui, "provider_registry", lambda: registry)
    monkeypatch.setattr(
        browser_ui,
        "default_sidecar_manager",
        lambda **_kwargs: SimpleNamespace(status=lambda _name: {"ready": False}),
    )

    payload = browser_ui.browser_command_payload(
        "status",
        runtime_env={
            OPENMINION_BROWSER_PLAYWRIGHT_LOCALE_ENV: "ja-JP",
            OPENMINION_BROWSER_PLAYWRIGHT_TIMEZONE_ENV: "Asia/Tokyo",
        },
        browser_config=ToolFamilyRuntimeConfig(
            enabled_providers=["playwright"],
            default_provider="playwright",
            provider_order=["playwright"],
        ),
    )

    assert payload["selected_provider"] == "playwright"
    assert payload["selected_ready"] is True
    assert payload["context"] == {
        "locale": "ja-JP",
        "timezone_id": "Asia/Tokyo",
    }


def test_browser_status_keeps_unavailable_selected_provider_visible(
    monkeypatch,
) -> None:
    playwright = SimpleNamespace(
        provider_id="playwright",
        config=SimpleNamespace(locale="ja-JP", timezone_id="Asia/Tokyo"),
        ensure_ready=lambda _ctx: {
            "ok": False,
            "error": {"remediation": ["Install browser binaries"]},
        },
    )
    registry = SimpleNamespace(
        list_provider_ids=lambda: ["playwright"],
        get=lambda _provider_id: playwright,
    )
    monkeypatch.setattr(browser_ui, "provider_registry", lambda: registry)
    monkeypatch.setattr(
        browser_ui,
        "default_sidecar_manager",
        lambda **_kwargs: SimpleNamespace(status=lambda _name: {}),
    )

    body = browser_ui.render_browser_command(
        "status",
        payload=browser_ui.browser_command_payload(
            "status",
            browser_config=ToolFamilyRuntimeConfig(
                default_provider="playwright",
                provider_order=["playwright"],
            ),
        ),
    )

    assert "selected=playwright status=not ready" in body
    assert "Repair: Install browser binaries" in body


def test_browser_tabs_and_navigate_use_browser_tool(monkeypatch, tmp_path) -> None:
    tool = _Tool()
    monkeypatch.setattr(browser_ui, "default_browser_tool", lambda: tool)

    tabs = browser_ui.render_browser_command("tabs", working_dir=str(tmp_path))
    navigated = browser_ui.render_browser_command(
        "navigate https://example.com tab=t1",
        working_dir=str(tmp_path),
    )

    assert "Browser tabs" in tabs
    assert "navigated tab=t1" in navigated
    assert tool.calls == [
        {"op": "tab.list"},
        {"op": "tab.navigate", "url": "https://example.com", "tab_id": "t1"},
    ]


def test_browser_stop_reuses_sidecar_manager(monkeypatch) -> None:
    stop_calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        browser_ui,
        "default_sidecar_manager",
        lambda **_kwargs: SimpleNamespace(
            stop=lambda *, name, kill: (
                stop_calls.append((name, kill)) or {"stopped": True}
            )
        ),
    )

    body = browser_ui.render_browser_command("stop kill=1")

    assert "pinchtab sidecar stop requested stopped=True" in body
    assert stop_calls == [("pinchtab", True)]


def test_browser_cli_bootstraps_builtin_providers_and_reports_failures(
    tmp_path,
) -> None:
    env = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).resolve().parents[3] / "src"),
    }
    status = subprocess.run(
        [sys.executable, "-m", "openminion", "browser", "status", "--json"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)["providers"] == ["pinchtab", "playwright"]

    failed = subprocess.run(
        [
            sys.executable,
            "-m",
            "openminion",
            "browser",
            "navigate",
            "data:text/html,<h1>test</h1>",
            "--provider",
            "missing",
            "--json",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert failed.returncode == 1
    failure = json.loads(failed.stdout)
    assert failure["ok"] is False
    assert "available: pinchtab, playwright" in failure["error"]
