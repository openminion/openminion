from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from openminion.base.config.env import (
    resolve_environment_config_with_explicit_env,
)
from openminion.modules.tool.runtime.resource_selectors import ResourceSelectors
from openminion.modules.tool.runtime.policy import Policy
from openminion.modules.tool.runtime import RuntimeContext
from openminion.tools.browser import (
    BrowserCapabilities,
    BrowserProviderRegistry,
    BrowserRouter,
    BrowserRoutingConfig,
)
from openminion.tools.browser.models import (
    BrowserAction,
    InstanceSpec,
    NavigateOptions,
    OutputOptions,
    SnapshotOptions,
    TextOptions,
)
from openminion.tools.browser.session_state import (
    BrowserSessionStateStore,
    SessionBrowserState,
)
from openminion.tools.browser.tool import BrowserTool
from openminion.tools.browser import tool as browser_tool_module


@dataclass
class ToolContext:
    runtime: object | None = None
    trace_id: str = ""
    session_id: str = ""
    extras: dict[str, object] = field(default_factory=dict)


@dataclass
class _Provider:
    provider_id: str = "mock"
    capabilities: BrowserCapabilities = field(
        default_factory=lambda: BrowserCapabilities(
            snapshot_refs=True,
            selector_actions=True,
            batch_actions=True,
            pdf_export=True,
            tab_locking=True,
        )
    )
    provider_version: str = "test"
    snapshot_calls: list[str] = field(default_factory=list)
    instance_start_calls: int = 0
    tab_new_calls: list[tuple[str, str | None]] = field(default_factory=list)
    tab_navigate_calls: list[tuple[str, str]] = field(default_factory=list)
    tab_action_calls: list[tuple[str, str]] = field(default_factory=list)
    tabs_payload: list[dict] = field(
        default_factory=lambda: [
            {"id": "t1", "url": "https://example.com", "title": "Example"}
        ]
    )

    def resource_selectors(self, args):
        del args
        return ResourceSelectors()

    def ensure_ready(self, ctx):
        del ctx
        return {"ok": True}

    def instance_start(self, ctx, spec: InstanceSpec):
        del ctx
        self.instance_start_calls += 1
        return {
            "instance": {
                "id": "i1",
                "profile": spec.profile,
                "mode": spec.mode or "default",
            }
        }

    def instance_list(self, ctx):
        del ctx
        return {"instances": [{"id": "i1", "profile": "default", "mode": "headed"}]}

    def instance_stop(self, ctx, instance_id: str):
        del ctx
        return {"instance": {"id": instance_id, "profile": None, "mode": None}}

    def instance_kill(self, ctx, instance_id: str):
        del ctx
        return {
            "instance": {"id": instance_id, "profile": None, "mode": None},
            "killed": True,
        }

    def tab_new(self, ctx, instance_id: str, url: str | None = None):
        del ctx
        self.tab_new_calls.append((instance_id, url))
        return {"tab": {"id": "t1", "url": url or "about:blank", "title": "new"}}

    def tab_list(self, ctx, instance_id: str | None = None):
        del ctx, instance_id
        return {"tabs": [dict(row) for row in self.tabs_payload]}

    def tab_close(self, ctx, tab_id: str):
        del ctx
        return {"tab": {"id": tab_id, "url": "", "title": ""}}

    def tab_navigate(
        self, ctx, tab_id: str, url: str, options: NavigateOptions | None = None
    ):
        del ctx, options
        self.tab_navigate_calls.append((tab_id, url))
        return {"tab": {"id": tab_id, "url": url, "title": "Updated"}}

    def tab_snapshot(self, ctx, tab_id: str, options: SnapshotOptions | None = None):
        del ctx, options
        self.snapshot_calls.append(tab_id)
        return {
            "snapshot": {
                "format": "refs",
                "nodes": [{"kind": "doc"}],
                "interactive_refs": ["e5"],
                "meta": {"compact": True},
            }
        }

    def tab_text(self, ctx, tab_id: str, options: TextOptions | None = None):
        del ctx, tab_id, options
        return {"text": {"content": "hello", "truncated": False, "chars": 5}}

    def tab_screenshot(self, ctx, tab_id: str, options: OutputOptions | None = None):
        del ctx, tab_id, options
        return {"kind": "screenshot", "content": b"abc"}

    def tab_pdf(self, ctx, tab_id: str, options: OutputOptions | None = None):
        del ctx, tab_id, options
        return {"kind": "pdf", "content": b"def"}

    def tab_action(self, ctx, tab_id: str, action: BrowserAction):
        del ctx
        self.tab_action_calls.append((tab_id, action.kind))
        return {"tab": {"id": tab_id, "url": "", "title": ""}}

    def tab_actions(self, ctx, tab_id: str, actions: list[BrowserAction]):
        del ctx
        return {
            "tab": {"id": tab_id, "url": "", "title": ""},
            "actions": [a.model_dump(exclude_none=True) for a in actions],
        }

    def tab_lock(
        self, ctx, tab_id: str, owner: str | None = None, ttl_s: int | None = None
    ):
        del ctx, owner, ttl_s
        return {"tab": {"id": tab_id, "url": "", "title": ""}, "locked": True}

    def tab_unlock(self, ctx, tab_id: str, owner: str | None = None):
        del ctx, owner
        return {"tab": {"id": tab_id, "url": "", "title": ""}, "locked": False}


class _Runtime:
    def __init__(self) -> None:
        self.writes: list[dict] = []

    def fs_write(self, spec):
        self.writes.append(dict(spec))
        return {"ok": True}

    def exec(self, spec):
        del spec
        return {}

    def fs_delete(self, spec):
        del spec
        return {}

    def net_fetch(self, spec):
        del spec
        return {}

    def reaction_add(self, spec):
        del spec
        return {}

    def reaction_remove(self, spec):
        del spec
        return {}

    def cron_add(self, spec):
        del spec
        return {}

    def cron_list(self, spec):
        del spec
        return {}

    def cron_run(self, spec):
        del spec
        return {}

    def cron_runs(self, spec):
        del spec
        return {}

    def cron_remove(self, spec):
        del spec
        return {}

    def cron_enable(self, spec):
        del spec
        return {}

    def cron_disable(self, spec):
        del spec
        return {}


@pytest.fixture(autouse=True)
def _isolate_browser_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    browser_tool_module._SESSION_STATE.clear()
    browser_tool_module._SESSION_STATE_LOADED_ROOTS.clear()
    monkeypatch.setenv("OPENMINION_WORKSPACE_ROOT", str(tmp_path))
    yield
    browser_tool_module._SESSION_STATE.clear()
    browser_tool_module._SESSION_STATE_LOADED_ROOTS.clear()


def test_browser_tool_dispatch_and_normalize() -> None:
    reg = BrowserProviderRegistry()
    reg.register(_Provider())
    tool = BrowserTool(
        router=BrowserRouter(reg, config=BrowserRoutingConfig(default_provider="mock"))
    )

    result = tool.execute({"op": "tab.text", "tab_id": "t1"}, ToolContext())
    assert result.ok is True
    assert result.data["text"]["content"] == "hello"
    assert result.data["provider"] == "mock"


def test_browser_tool_writes_output_via_runtime(tmp_path: Path) -> None:
    reg = BrowserProviderRegistry()
    reg.register(_Provider())
    tool = BrowserTool(
        router=BrowserRouter(reg, config=BrowserRoutingConfig(default_provider="mock"))
    )
    runtime = _Runtime()

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = tool.execute(
        {
            "op": "tab.screenshot",
            "tab_id": "t1",
            "output": {"path": "artifacts/example.jpg"},
        },
        ToolContext(runtime=runtime, extras={"workspace_root": str(workspace)}),
    )

    assert result.ok is True
    expected = str((workspace / "artifacts/example.jpg").resolve())
    assert result.data["artifact"]["path"] == expected
    assert runtime.writes and runtime.writes[0]["path"] == expected


def test_browser_tool_enforces_workspace_outputs(tmp_path: Path) -> None:
    reg = BrowserProviderRegistry()
    reg.register(_Provider())
    tool = BrowserTool(
        router=BrowserRouter(reg, config=BrowserRoutingConfig(default_provider="mock"))
    )
    runtime = _Runtime()

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.jpg"

    result = tool.execute(
        {"op": "tab.screenshot", "tab_id": "t1", "output": {"path": str(outside)}},
        ToolContext(runtime=runtime, extras={"workspace_root": str(workspace)}),
    )

    assert result.ok is False
    assert result.error
    assert result.data.get("error", {}).get("code") == "INVALID_ARGUMENT"


def test_browser_tool_enforces_workspace_profile_dirs(tmp_path: Path) -> None:
    reg = BrowserProviderRegistry()
    reg.register(_Provider())
    tool = BrowserTool(
        router=BrowserRouter(reg, config=BrowserRoutingConfig(default_provider="mock"))
    )

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside-profile"

    result = tool.execute(
        {
            "op": "instance.start",
            "instance": {"user_data_dir": str(outside), "downloads_path": str(outside)},
        },
        ToolContext(extras={"workspace_root": str(workspace)}),
    )

    assert result.ok is False
    assert result.error
    assert result.data.get("error", {}).get("code") == "INVALID_ARGUMENT"


def test_browser_tool_capability_gating() -> None:
    reg = BrowserProviderRegistry()
    reg.register(_Provider(capabilities=BrowserCapabilities(tab_locking=False)))
    tool = BrowserTool(
        router=BrowserRouter(reg, config=BrowserRoutingConfig(default_provider="mock"))
    )

    result = tool.execute({"op": "tab.lock", "tab_id": "t1"}, ToolContext())
    assert result.ok is False
    assert result.error
    assert result.data.get("error", {}).get("code") == "capability_not_supported"


def test_browser_tool_affinity_routing() -> None:
    pinchtab = _Provider(provider_id="pinchtab")
    playwright = _Provider(provider_id="playwright")

    def _play_tab_new(ctx, instance_id: str, url: str | None = None):
        del ctx, instance_id, url
        return {"tab": {"id": "pw-tab-1", "url": "https://example.com", "title": "PW"}}

    playwright.tab_new = _play_tab_new  # type: ignore[method-assign]

    reg = BrowserProviderRegistry()
    reg.register(pinchtab)
    reg.register(playwright)
    tool = BrowserTool(
        router=BrowserRouter(
            reg, config=BrowserRoutingConfig(default_provider="pinchtab")
        )
    )

    created = tool.execute(
        {"op": "tab.new", "provider": "playwright", "instance_id": "i1"}, ToolContext()
    )
    assert created.ok is True

    snapshot = tool.execute({"op": "tab.snapshot", "tab_id": "pw-tab-1"}, ToolContext())
    assert snapshot.ok is True
    assert snapshot.data["provider"] == "playwright"
    assert playwright.snapshot_calls == ["pw-tab-1"]


def test_browser_tool_runtime_tools_default_provider_override() -> None:
    pinchtab = _Provider(provider_id="pinchtab")
    playwright = _Provider(provider_id="playwright")
    reg = BrowserProviderRegistry()
    reg.register(pinchtab)
    reg.register(playwright)
    tool = BrowserTool(
        router=BrowserRouter(reg, config=BrowserRoutingConfig(default_provider=""))
    )
    runtime = type(
        "_RuntimePolicy",
        (),
        {
            "policy": type(
                "_Policy",
                (),
                {
                    "raw": {
                        "context_metadata": {
                            "runtime_tools": {
                                "browser": {
                                    "default_provider": "playwright",
                                    "provider_order": ["playwright", "pinchtab"],
                                }
                            }
                        }
                    }
                },
            )()
        },
    )()

    result = tool.execute(
        {"op": "tab.text", "tab_id": "t1"}, ToolContext(runtime=runtime)
    )

    assert result.ok is True
    assert result.data["provider"] == "playwright"


def test_browser_tool_runtime_tools_do_not_override_tab_affinity() -> None:
    pinchtab = _Provider(provider_id="pinchtab")
    playwright = _Provider(provider_id="playwright")

    def _play_tab_new(ctx, instance_id: str, url: str | None = None):
        del ctx, instance_id, url
        return {"tab": {"id": "pw-tab-1", "url": "https://example.com", "title": "PW"}}

    playwright.tab_new = _play_tab_new  # type: ignore[method-assign]

    reg = BrowserProviderRegistry()
    reg.register(pinchtab)
    reg.register(playwright)
    tool = BrowserTool(
        router=BrowserRouter(reg, config=BrowserRoutingConfig(default_provider=""))
    )
    runtime = type(
        "_RuntimePolicy",
        (),
        {
            "policy": type(
                "_Policy",
                (),
                {
                    "raw": {
                        "context_metadata": {
                            "runtime_tools": {
                                "browser": {
                                    "default_provider": "pinchtab",
                                    "provider_order": ["pinchtab", "playwright"],
                                }
                            }
                        }
                    }
                },
            )()
        },
    )()

    created = tool.execute(
        {"op": "tab.new", "provider": "playwright", "instance_id": "i1"},
        ToolContext(runtime=runtime),
    )
    assert created.ok is True

    snapshot = tool.execute(
        {"op": "tab.snapshot", "tab_id": "pw-tab-1"},
        ToolContext(runtime=runtime),
    )

    assert snapshot.ok is True
    assert snapshot.data["provider"] == "playwright"
    assert playwright.snapshot_calls == ["pw-tab-1"]


def test_browser_tool_reuses_session_instance_for_tab_new() -> None:
    provider = _Provider()
    reg = BrowserProviderRegistry()
    reg.register(provider)
    tool = BrowserTool(
        router=BrowserRouter(reg, config=BrowserRoutingConfig(default_provider="mock"))
    )

    started = tool.execute(
        {"op": "instance.start", "mode": "headed"},
        ToolContext(session_id="reuse-instance"),
    )
    assert started.ok is True

    created = tool.execute(
        {"op": "tab.new", "url": "https://example.com"},
        ToolContext(session_id="reuse-instance"),
    )
    assert created.ok is True
    assert provider.tab_new_calls
    assert provider.tab_new_calls[-1][0] == "i1"


def test_browser_tool_reuses_session_tab_for_navigate_and_action() -> None:
    provider = _Provider()
    reg = BrowserProviderRegistry()
    reg.register(provider)
    tool = BrowserTool(
        router=BrowserRouter(reg, config=BrowserRoutingConfig(default_provider="mock"))
    )

    created = tool.execute(
        {"op": "tab.new", "instance_id": "i1", "url": "https://example.com"},
        ToolContext(session_id="reuse-tab"),
    )
    assert created.ok is True

    navigated = tool.execute(
        {"op": "tab.navigate", "url": "https://example.com/docs"},
        ToolContext(session_id="reuse-tab"),
    )
    assert navigated.ok is True
    assert provider.tab_navigate_calls[-1][0] == "t1"

    acted = tool.execute(
        {"op": "tab.action", "action": {"kind": "click", "target": {"ref": "e5"}}},
        ToolContext(session_id="reuse-tab"),
    )
    assert acted.ok is True
    assert provider.tab_action_calls[-1] == ("t1", "click")


def test_browser_tool_bootstraps_navigate_when_no_instance_or_tab() -> None:
    provider = _Provider(tabs_payload=[])
    reg = BrowserProviderRegistry()
    reg.register(provider)
    tool = BrowserTool(
        router=BrowserRouter(reg, config=BrowserRoutingConfig(default_provider="mock"))
    )

    result = tool.execute(
        {"op": "tab.navigate", "url": "https://example.com"},
        ToolContext(session_id="bootstrap-nav"),
    )
    assert result.ok is True
    # Bootstrap may create a tab or navigate a discovered/recovered tab directly.
    assert provider.tab_new_calls or provider.tab_navigate_calls
    assert result.data["tab"]["id"] == "t1"


def test_browser_tool_resolves_tab_by_url_contains_option() -> None:
    provider = _Provider(
        tabs_payload=[
            {"id": "tab-home", "url": "https://example.com", "title": "Home"},
            {
                "id": "tab-docs",
                "url": "https://docs.example.com/start",
                "title": "Docs",
            },
        ]
    )
    reg = BrowserProviderRegistry()
    reg.register(provider)
    tool = BrowserTool(
        router=BrowserRouter(reg, config=BrowserRoutingConfig(default_provider="mock"))
    )

    result = tool.execute(
        {
            "op": "tab.navigate",
            "url": "https://docs.example.com/next",
            "options": {"tab_url_contains": "docs.example.com"},
        },
        ToolContext(session_id="resolve-tab"),
    )
    assert result.ok is True
    assert provider.tab_navigate_calls[-1][0] == "tab-docs"


def test_browser_tool_reuses_single_meaningful_tab_without_session_state() -> None:
    provider = _Provider(
        tabs_payload=[
            {"id": "tab-blank", "url": "about:blank", "title": "about:blank"},
            {
                "id": "tab-auth",
                "url": "https://app.example.com/dashboard",
                "title": "Dashboard",
            },
        ]
    )
    reg = BrowserProviderRegistry()
    reg.register(provider)
    tool = BrowserTool(
        router=BrowserRouter(reg, config=BrowserRoutingConfig(default_provider="mock"))
    )

    result = tool.execute(
        {"op": "tab.navigate", "url": "https://news.ycombinator.com"},
        ToolContext(),
    )

    assert result.ok is True
    assert provider.tab_navigate_calls[-1][0] == "tab-auth"
    assert provider.tab_new_calls == []
    assert result.data["data"]["resolution"]["strategy"] == "single_non_blank"


def test_browser_tool_tab_select_sets_active_tab_for_session() -> None:
    provider = _Provider(
        tabs_payload=[
            {"id": "tab-home", "url": "https://example.com", "title": "Home"},
            {
                "id": "tab-docs",
                "url": "https://docs.example.com/start",
                "title": "Docs",
            },
        ]
    )
    reg = BrowserProviderRegistry()
    reg.register(provider)
    tool = BrowserTool(
        router=BrowserRouter(reg, config=BrowserRoutingConfig(default_provider="mock"))
    )

    selected = tool.execute(
        {"op": "tab.select", "options": {"tab_url_contains": "docs.example.com"}},
        ToolContext(session_id="select-tab"),
    )
    assert selected.ok is True
    assert selected.data["tab"]["id"] == "tab-docs"

    acted = tool.execute(
        {"op": "tab.action", "action": {"kind": "click", "target": {"ref": "e5"}}},
        ToolContext(session_id="select-tab"),
    )
    assert acted.ok is True
    assert provider.tab_action_calls[-1] == ("tab-docs", "click")


def test_browser_tool_tab_list_can_filter_and_select() -> None:
    provider = _Provider(
        tabs_payload=[
            {"id": "tab-home", "url": "https://example.com", "title": "Home"},
            {
                "id": "tab-docs",
                "url": "https://docs.example.com/start",
                "title": "Docs",
            },
        ]
    )
    reg = BrowserProviderRegistry()
    reg.register(provider)
    tool = BrowserTool(
        router=BrowserRouter(reg, config=BrowserRoutingConfig(default_provider="mock"))
    )

    result = tool.execute(
        {"op": "tab.list", "options": {"tab_title_contains": "docs", "select": True}},
        ToolContext(session_id="list-filter"),
    )

    assert result.ok is True
    assert [tab["id"] for tab in result.data["tabs"]] == ["tab-docs"]
    assert result.data["tab"]["id"] == "tab-docs"
    assert result.data["data"]["resolution"]["filtered_count"] == 1


def test_browser_tool_instance_list_and_kill_clear_session_state() -> None:
    provider = _Provider()
    reg = BrowserProviderRegistry()
    reg.register(provider)
    tool = BrowserTool(
        router=BrowserRouter(reg, config=BrowserRoutingConfig(default_provider="mock"))
    )

    started = tool.execute(
        {"op": "instance.start"}, ToolContext(session_id="inst-lifecycle")
    )
    assert started.ok is True
    assert provider.instance_start_calls == 1

    listed = tool.execute(
        {"op": "instance.list"}, ToolContext(session_id="inst-lifecycle")
    )
    assert listed.ok is True
    assert listed.data["instances"][0]["id"] == "i1"

    killed = tool.execute(
        {"op": "instance.kill"}, ToolContext(session_id="inst-lifecycle")
    )
    assert killed.ok is True

    created = tool.execute({"op": "tab.new"}, ToolContext(session_id="inst-lifecycle"))
    assert created.ok is True
    assert provider.instance_start_calls == 2


def test_browser_tool_tab_navigate_recovers_from_stale_tab_context() -> None:
    provider = _Provider(tabs_payload=[])
    reg = BrowserProviderRegistry()
    reg.register(provider)
    tool = BrowserTool(
        router=BrowserRouter(reg, config=BrowserRoutingConfig(default_provider="mock"))
    )

    def _stale_then_ok(
        ctx, tab_id: str, url: str, options: NavigateOptions | None = None
    ):
        del ctx, options
        if tab_id == "stale-tab":
            raise KeyError("tab not found: stale-tab")
        provider.tab_navigate_calls.append((tab_id, url))
        return {"tab": {"id": tab_id, "url": url, "title": "Recovered"}}

    provider.tab_navigate = _stale_then_ok  # type: ignore[method-assign]

    result = tool.execute(
        {"op": "tab.navigate", "url": "https://example.com"},
        ToolContext(
            session_id="stale-nav",
            extras={
                "session_browser_tab_id": "stale-tab",
                "session_browser_instance_id": "stale-inst",
            },
        ),
    )

    assert result.ok is True
    assert provider.tab_new_calls or provider.tab_navigate_calls
    strategy = str(result.data["data"]["resolution"]["strategy"])
    assert strategy in {"stale_recover_bootstrap", "call.tab_id"}


def test_browser_tool_persists_session_state_across_tool_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    browser_tool_module._SESSION_STATE.clear()
    browser_tool_module._SESSION_STATE_LOADED_ROOTS.clear()

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("OPENMINION_HOME", str(workspace))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    provider_1 = _Provider()
    reg_1 = BrowserProviderRegistry()
    reg_1.register(provider_1)
    tool_1 = BrowserTool(
        router=BrowserRouter(
            reg_1, config=BrowserRoutingConfig(default_provider="mock")
        )
    )

    created = tool_1.execute(
        {"op": "tab.new", "instance_id": "i1", "url": "https://example.com"},
        ToolContext(
            session_id="persist-session", extras={"workspace_root": str(workspace)}
        ),
    )
    assert created.ok is True

    state_path = workspace / ".openminion" / "browser" / "session_state.json"
    assert state_path.exists()
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["sessions"]["mock::persist-session"]["tab_id"] == "t1"

    browser_tool_module._SESSION_STATE.clear()
    browser_tool_module._SESSION_STATE_LOADED_ROOTS.clear()

    provider_2 = _Provider()
    reg_2 = BrowserProviderRegistry()
    reg_2.register(provider_2)
    tool_2 = BrowserTool(
        router=BrowserRouter(
            reg_2, config=BrowserRoutingConfig(default_provider="mock")
        )
    )

    acted = tool_2.execute(
        {"op": "tab.action", "action": {"kind": "click", "target": {"ref": "e5"}}},
        ToolContext(
            session_id="persist-session", extras={"workspace_root": str(workspace)}
        ),
    )
    assert acted.ok is True
    assert provider_2.tab_action_calls[-1] == ("t1", "click")


def test_browser_session_state_store_uses_injected_env_data_root(
    tmp_path: Path,
) -> None:
    store = BrowserSessionStateStore(state_relative_path="browser/session_state.json")
    workspace = (tmp_path / "workspace").resolve()
    data_root = (tmp_path / "data-root").resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    env = resolve_environment_config_with_explicit_env(
        {"OPENMINION_DATA_ROOT": str(data_root)}
    )

    store.session_state[(str(workspace), "mock", "session-1")] = SessionBrowserState(
        instance_id="inst-1", tab_id="tab-1"
    )
    store.persist_session_state(workspace_root=str(workspace), env=env)

    persisted = data_root / "browser" / "session_state.json"
    assert persisted.exists()
    payload = json.loads(persisted.read_text(encoding="utf-8"))
    assert payload["sessions"]["mock::session-1"]["tab_id"] == "tab-1"


def test_browser_tool_mode_forwarding() -> None:
    provider = _Provider()
    reg = BrowserProviderRegistry()
    reg.register(provider)
    tool = BrowserTool(
        router=BrowserRouter(reg, config=BrowserRoutingConfig(default_provider="mock"))
    )

    # Test explicit mode in top-level args
    result = tool.execute({"op": "instance.start", "mode": "headed"}, ToolContext())
    assert result.ok is True
    assert result.data["instance"]["mode"] == "headed"

    # Test explicit mode in instance object
    result = tool.execute(
        {"op": "instance.start", "instance": {"mode": "headless"}}, ToolContext()
    )
    assert result.ok is True
    assert result.data["instance"]["mode"] == "headless"

    # Test alias forwarding (tool doesn't normalize, provider does, but here we mock provider)
    # The tool should just pass whatever it gets.
    result = tool.execute({"op": "instance.start", "mode": "ui"}, ToolContext())
    assert result.ok is True
    assert result.data["instance"]["mode"] == "ui"


def test_browser_tool_audit_records_selected_provider(tmp_path: Path) -> None:
    provider = _Provider()
    reg = BrowserProviderRegistry()
    reg.register(provider)
    tool = BrowserTool(
        router=BrowserRouter(reg, config=BrowserRoutingConfig(default_provider="mock"))
    )
    workspace = tmp_path / "workspace"
    run_root = tmp_path / "run"
    workspace.mkdir(parents=True, exist_ok=True)
    run_root.mkdir(parents=True, exist_ok=True)
    runtime = RuntimeContext(
        policy=Policy(
            raw={
                "workspace_root": str(workspace),
                "paths": {
                    "read_allow": [str(workspace)],
                    "write_allow": [str(workspace)],
                    "deny": [],
                },
                "commands": {"mode": "allowlist", "allow": ["echo"]},
            }
        ),
        workspace=workspace,
        run_root=run_root,
        scope="UI_AUTOMATION",
        confirm=False,
    )

    result = tool.execute(
        {"op": "tab.text", "tab_id": "t1"}, ToolContext(runtime=runtime)
    )

    assert result.ok is True
    audit_path = run_root / "audit.jsonl"
    records = [
        json.loads(line)
        for line in audit_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(
        row.get("event") == "tool.browser.provider.selected"
        and row.get("selected_provider") == "mock"
        for row in records
    )


def test_browser_tool_uses_shared_emit_family_event_helper(tmp_path: Path) -> None:
    from unittest.mock import patch

    provider = _Provider()
    reg = BrowserProviderRegistry()
    reg.register(provider)
    tool = BrowserTool(
        router=BrowserRouter(reg, config=BrowserRoutingConfig(default_provider="mock"))
    )
    workspace = tmp_path / "workspace"
    run_root = tmp_path / "run"
    workspace.mkdir(parents=True, exist_ok=True)
    run_root.mkdir(parents=True, exist_ok=True)
    runtime = RuntimeContext(
        policy=Policy(
            raw={
                "workspace_root": str(workspace),
                "paths": {
                    "read_allow": [str(workspace)],
                    "write_allow": [str(workspace)],
                    "deny": [],
                },
            }
        ),
        workspace=workspace,
        run_root=run_root,
        scope="UI_AUTOMATION",
        confirm=False,
    )

    with patch("openminion.tools.browser.tool.emit_family_event") as mock_emit:
        result = tool.execute(
            {"op": "tab.text", "tab_id": "t1"}, ToolContext(runtime=runtime)
        )

    assert result.ok is True
    assert mock_emit.called, "emit_family_event must be called by browser tool"
    emitted_events = [call.kwargs.get("event") for call in mock_emit.call_args_list]
    assert "tool.browser.provider.selected" in emitted_events
