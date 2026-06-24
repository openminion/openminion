from __future__ import annotations

from pathlib import Path

import pytest

from openminion.base.config.env import resolve_environment_config
from openminion.modules.tool.runtime.policy import Policy
from openminion.modules.tool.runtime import RuntimeContext
from openminion.tools.browser import BrowserProviderRegistry
from openminion.tools.browser import BrowserProviderContext
from openminion.tools.browser.providers.pinchtab.client import (
    PinchTabClient,
    PinchTabClientConfig,
    PinchTabClientError,
)
from openminion.tools.browser.providers.pinchtab.plugin import register_browser_provider
from openminion.tools.browser.providers.pinchtab.provider import (
    PinchTabProvider,
    PinchTabProviderConfig,
)
from openminion.tools.browser.providers.pinchtab import (
    provider as pinchtab_provider_module,
)


def test_tab_new_uses_canonical_route_only(monkeypatch):
    client = PinchTabClient(PinchTabClientConfig())
    calls: list[str] = []

    def fake_request(method, path, **kwargs):
        del method, kwargs
        calls.append(path)
        if path == "/tabs/new":
            return {"tabId": "t1", "url": "https://example.com"}
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(client, "_request", fake_request)
    payload = client.tab_new(instance_id="i1", url="https://example.com")

    assert payload["tabId"] == "t1"
    assert calls == ["/tabs/new"]


def test_tab_new_falls_back_to_navigate_when_tab_routes_missing(monkeypatch):
    client = PinchTabClient(PinchTabClientConfig())
    calls: list[str] = []

    def fake_request(method, path, **kwargs):
        del method, kwargs
        calls.append(path)
        if path in {"/tabs/new", "/tabs/open"}:
            raise PinchTabClientError("missing route", status=404)
        if path == "/navigate":
            return {"tabId": "t-nav", "url": "https://example.com"}
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(client, "_request", fake_request)
    payload = client.tab_new(instance_id="i1", url="https://example.com")

    assert payload["tabId"] == "t-nav"
    assert payload["tab_id"] == "t-nav"
    assert calls == ["/tabs/new", "/tabs/open", "/navigate"]


def test_headers_include_bearer_token():
    client = PinchTabClient(PinchTabClientConfig(token="abc123"))
    headers = client._headers()
    assert headers["Authorization"] == "Bearer abc123"


def test_provider_resource_selectors_include_target_and_token_env():
    provider = PinchTabProvider(
        PinchTabProviderConfig(
            base_url="http://127.0.0.1:9867", token_ref="secret:PINCHTAB_BRIDGE_TOKEN"
        )
    )
    selectors = provider.resource_selectors({})

    assert selectors.hosts == ("127.0.0.1",)
    assert selectors.ports == (9867,)
    assert selectors.protocols == ("http",)
    assert selectors.env_keys_requested == ("PINCHTAB_BRIDGE_TOKEN",)


def test_provider_rejects_remote_base_url_without_opt_in():
    with pytest.raises(ValueError):
        PinchTabProvider(PinchTabProviderConfig(base_url="https://bridge.example.com"))


def test_provider_registration_hook_registers_pinchtab():
    registry = BrowserProviderRegistry()
    register_browser_provider(registry)
    assert "pinchtab" in registry.list_provider_ids()


def test_provider_tab_list_accepts_plain_list_payload(monkeypatch):
    provider = PinchTabProvider(
        PinchTabProviderConfig(base_url="http://127.0.0.1:9867")
    )

    class _Client:
        def tab_list(self, *, instance_id=None):
            assert instance_id == "i1"
            return [
                {"tabId": "t1", "url": "https://example.com", "title": "Example"},
                {"id": "t2", "url": "about:blank", "title": "about:blank"},
            ]

    monkeypatch.setattr(provider, "_client", lambda **kwargs: _Client())

    payload = provider.tab_list(instance_id="i1")
    assert [row["id"] for row in payload["tabs"]] == ["t1", "t2"]


def test_client_instance_kill_uses_kill_route_only(monkeypatch):
    client = PinchTabClient(PinchTabClientConfig())
    calls: list[str] = []

    def fake_request(method, path, **kwargs):
        del method, kwargs
        calls.append(path)
        if path.endswith("/kill"):
            return {"killed": True}
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(client, "_request", fake_request)
    payload = client.instance_kill(instance_id="i1")

    assert payload["killed"] is True
    assert calls == ["/instances/i1/kill"]


def test_provider_instance_list_accepts_plain_list_payload(monkeypatch):
    provider = PinchTabProvider(
        PinchTabProviderConfig(base_url="http://127.0.0.1:9867")
    )

    class _Client:
        def instance_list(self):
            return [
                {
                    "instanceId": "i1",
                    "profileId": "auth",
                    "mode": "headed",
                    "status": "running",
                },
                {
                    "id": "i2",
                    "profile": "default",
                    "mode": "headless",
                    "status": "stopped",
                },
            ]

    monkeypatch.setattr(provider, "_client", lambda **kwargs: _Client())

    payload = provider.instance_list()
    assert [row["id"] for row in payload["instances"]] == ["i1", "i2"]


def test_provider_instance_kill_maps_killed_flag(monkeypatch):
    provider = PinchTabProvider(
        PinchTabProviderConfig(base_url="http://127.0.0.1:9867")
    )

    class _Client:
        def instance_kill(self, *, instance_id):
            assert instance_id == "i1"
            return {"stopped": True}

    monkeypatch.setattr(provider, "_client", lambda **kwargs: _Client())
    payload = provider.instance_kill(instance_id="i1")
    assert payload["killed"] is True


def test_provider_config_resolves_data_root_from_openminion_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home_root = tmp_path / "home-root"
    workspace_root = tmp_path / "workspace"
    home_root.mkdir(parents=True, exist_ok=True)
    workspace_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OPENMINION_HOME", str(home_root))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    cfg = PinchTabProviderConfig.from_env(workspace_root=str(workspace_root))
    expected_data_root = (home_root / ".openminion").resolve()

    assert Path(cfg.outputs_root_dir) == (expected_data_root / "browser").resolve()
    assert (
        Path(cfg.home_root_dir)
        == (expected_data_root / "tool-runtime" / "pinchtab").resolve()
    )


def test_provider_uses_browser_context_env_for_secret_ref(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, str] = {}

    class _FakeClient:
        def __init__(self, config: PinchTabClientConfig) -> None:
            captured["token"] = str(config.token or "")

        def health(self) -> dict[str, bool]:
            return {"ok": True}

    monkeypatch.setattr(pinchtab_provider_module, "PinchTabClient", _FakeClient)
    provider = PinchTabProvider(
        PinchTabProviderConfig(
            base_url="http://127.0.0.1:9867",
            token_ref="secret:PINCHTAB_CTX_TOKEN",
        )
    )
    runtime_ctx = RuntimeContext(
        policy=Policy(raw={}),
        workspace=tmp_path,
        run_root=tmp_path,
        scope="UI_AUTOMATION",
        confirm=False,
        env=resolve_environment_config(
            env={"PINCHTAB_CTX_TOKEN": "ctx-token-from-runtime"}
        ),
    )

    payload = provider.ensure_ready(BrowserProviderContext(tool_context=runtime_ctx))

    assert payload["ok"] is True
    assert captured["token"] == "ctx-token-from-runtime"
