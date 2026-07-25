from __future__ import annotations

from types import SimpleNamespace

from openminion.cli.presentation import browser as browser_ui


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
        lambda: SimpleNamespace(list_provider_ids=lambda: ["pinchtab"]),
    )
    monkeypatch.setattr(
        browser_ui,
        "default_sidecar_manager",
        lambda: SimpleNamespace(status=lambda _name: {"ready": True}),
    )

    body = browser_ui.render_browser_command("status")

    assert "providers=pinchtab" in body
    assert "sidecar=ready" in body


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
        lambda: SimpleNamespace(
            stop=lambda *, name, kill: (
                stop_calls.append((name, kill)) or {"stopped": True}
            )
        ),
    )

    body = browser_ui.render_browser_command("stop kill=1")

    assert "pinchtab sidecar stop requested stopped=True" in body
    assert stop_calls == [("pinchtab", True)]
