from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from openminion.modules.tool.runtime.policy import Policy
from openminion.modules.tool.registry import ToolRegistry
from openminion.modules.tool.runtime import RuntimeContext
from openminion.tools.ip.plugin import _h_local, _h_public, register
from openminion.tools.ip.providers import (
    _reset_provider_registry_for_tests,
    register_provider,
)


@pytest.fixture(autouse=True)
def _reset_provider_registry() -> None:
    _reset_provider_registry_for_tests()
    yield
    _reset_provider_registry_for_tests()


def _ctx(
    tmp_path: Path,
    *,
    ip_cfg: dict[str, Any] | None = None,
) -> RuntimeContext:
    run_root = tmp_path / "run"
    run_root.mkdir(parents=True, exist_ok=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    policy = Policy(
        raw={
            "workspace_root": str(workspace),
            "paths": {
                "read_allow": [str(workspace)],
                "write_allow": [str(workspace)],
                "deny": [],
            },
            "commands": {"mode": "allowlist", "allow": ["echo"]},
            "tools": {
                "allow_prefix": [""],
                "ip": dict(ip_cfg or {}),
            },
        }
    )
    return RuntimeContext(
        policy=policy,
        workspace=workspace,
        run_root=run_root,
        scope="READ_ONLY",
        confirm=False,
    )


class _Response:
    def __init__(self, payload: str) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload.encode("utf-8")

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


def test_register_adds_ip_public_tool() -> None:
    registry = ToolRegistry()
    register(registry)
    assert "ip.public" in registry.list()
    assert "ip.local" in registry.list()


def test_h_public_returns_ip_when_provider_responds(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "openminion.tools.ip.plugin.urllib_request.urlopen",
        lambda *_args, **_kwargs: _Response('{"ip":"8.8.8.8"}'),
    )
    payload = _h_public({}, _ctx(tmp_path))
    assert payload["ok"] is True
    assert payload["data"]["ip"] == "8.8.8.8"
    assert payload["content"].startswith("Public IP:")


def test_h_public_rejects_private_ip_payload(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "openminion.tools.ip.plugin.urllib_request.urlopen",
        lambda *_args, **_kwargs: _Response('{"ip":"192.168.1.50"}'),
    )
    payload = _h_public({}, _ctx(tmp_path))
    assert payload["ok"] is False
    assert payload["error"]["code"] == "NON_PUBLIC_IP"


def test_h_public_returns_policy_denied_when_disabled(tmp_path: Path) -> None:
    payload = _h_public({}, _ctx(tmp_path, ip_cfg={"enabled": False}))
    assert payload["ok"] is False
    assert payload["error"]["code"] == "POLICY_DENIED"


def test_h_local_returns_error_when_no_candidates(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "openminion.tools.ip.plugin._collect_local_candidates",
        lambda: [],
    )
    payload = _h_local({}, _ctx(tmp_path))
    assert payload["ok"] is False
    assert payload["error"]["code"] == "LOCAL_IP_UNAVAILABLE"


def test_h_local_can_include_loopback(monkeypatch, tmp_path: Path) -> None:
    import ipaddress

    monkeypatch.setattr(
        "openminion.tools.ip.plugin._collect_local_candidates",
        lambda: [
            ipaddress.ip_address("127.0.0.1"),
            ipaddress.ip_address("192.168.1.20"),
        ],
    )
    payload = _h_local({"include_loopback": True}, _ctx(tmp_path))
    assert payload["ok"] is True
    assert payload["data"]["primary_ip"] == "192.168.1.20"
    assert len(payload["data"]["addresses"]) == 2


def test_h_public_dispatches_to_registered_provider(tmp_path: Path) -> None:
    class _CustomPublicProvider:
        provider_id = "custom"

        def resolve_public(self, *, args, ctx):
            del args, ctx
            return {
                "ok": True,
                "content": "Public IP: 9.9.9.9",
                "data": {
                    "source": "custom-ip-provider",
                    "method": "ip.public",
                    "ip": "9.9.9.9",
                    "version": 4,
                },
                "verified": True,
            }

        def resolve_local(self, *, args, ctx):
            del args, ctx
            return {"ok": False, "error": {"code": "UNSUPPORTED"}}

        def healthcheck(self) -> bool:
            return True

    register_provider(_CustomPublicProvider())
    payload = _h_public({}, _ctx(tmp_path))
    assert payload["ok"] is True
    assert payload["data"]["source"] == "custom-ip-provider"
    assert payload["data"]["ip"] == "9.9.9.9"


def test_h_public_falls_back_to_builtin_provider_chain(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class _FailingProvider:
        provider_id = "failing"

        def resolve_public(self, *, args, ctx):
            del args, ctx
            return {
                "ok": False,
                "error": {"code": "UPSTREAM_ERROR", "message": "failing provider"},
                "data": {"method": "ip.public"},
            }

        def resolve_local(self, *, args, ctx):
            del args, ctx
            return {"ok": False, "error": {"code": "UNSUPPORTED"}}

        def healthcheck(self) -> bool:
            return True

    register_provider(_FailingProvider())
    monkeypatch.setattr(
        "openminion.tools.ip.plugin.urllib_request.urlopen",
        lambda *_args, **_kwargs: _Response('{"ip":"8.8.8.8"}'),
    )
    payload = _h_public({}, _ctx(tmp_path))
    assert payload["ok"] is True
    assert payload["data"]["ip"] == "8.8.8.8"
    assert any("failing" in str(item) for item in payload.get("warnings", []))


def test_h_local_dispatches_to_registered_provider(tmp_path: Path) -> None:
    class _CustomLocalProvider:
        provider_id = "custom-local"

        def resolve_public(self, *, args, ctx):
            del args, ctx
            return {"ok": False, "error": {"code": "UNSUPPORTED"}}

        def resolve_local(self, *, args, ctx):
            include_loopback = bool(args.get("include_loopback", False))
            del ctx
            return {
                "ok": True,
                "content": "Local IP: 10.0.0.42",
                "data": {
                    "source": "custom-local-provider",
                    "method": "ip.local",
                    "primary_ip": "10.0.0.42",
                    "addresses": [
                        {"ip": "10.0.0.42", "version": 4, "scope": "private"},
                    ],
                    "include_loopback": include_loopback,
                },
                "verified": True,
            }

        def healthcheck(self) -> bool:
            return True

    register_provider(_CustomLocalProvider())
    payload = _h_local({"include_loopback": True}, _ctx(tmp_path))
    assert payload["ok"] is True
    assert payload["data"]["source"] == "custom-local-provider"
    assert payload["data"]["primary_ip"] == "10.0.0.42"
