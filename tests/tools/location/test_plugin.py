from __future__ import annotations

import json
from email.message import Message
from pathlib import Path

import pytest

from openminion.modules.identity.models import (
    AgentProfile,
    PersonalitySpec,
    RiskSpec,
    RoleSpec,
    ToolPostureSpec,
)
from openminion.modules.identity.runtime.service import IdentityCtl
from openminion.modules.identity.storage.store import SQLiteIdentityStore
from openminion.modules.tool.runtime.policy import Policy
from openminion.modules.tool.registry import ToolRegistry
from openminion.modules.tool.runtime import RuntimeContext
from openminion.tools.location import plugin as location_plugin
from openminion.tools.location.plugin import _h_get, _h_get_ip, _h_set_default, register


def _ctx(
    tmp_path: Path,
    *,
    context_metadata: dict[str, object] | None = None,
    confirm: bool = False,
    location_cfg: dict[str, object] | None = None,
    scope: str = "READ_ONLY",
) -> RuntimeContext:
    run_root = tmp_path / "run"
    run_root.mkdir(parents=True, exist_ok=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    tools_cfg: dict[str, object] = {
        "allow_prefix": [""],
        "location": dict(location_cfg or {}),
    }
    policy = Policy(
        raw={
            "workspace_root": str(workspace),
            "context_metadata": dict(context_metadata or {}),
            "paths": {
                "read_allow": [str(workspace)],
                "write_allow": [str(workspace)],
                "deny": [],
            },
            "commands": {"mode": "allowlist", "allow": ["echo"]},
            "tools": tools_cfg,
        }
    )
    return RuntimeContext(
        policy=policy,
        workspace=workspace,
        run_root=run_root,
        scope=scope,
        confirm=confirm,
    )


def _sample_profile(agent_id: str) -> AgentProfile:
    return AgentProfile(
        agent_id=agent_id,
        display_name=f"Agent {agent_id}",
        profile_revision=1,
        role=RoleSpec(
            mission="Provide accurate assistant responses.",
            responsibilities=["Help users with tasks"],
            hard_constraints=["Do not fabricate"],
            domain=["general"],
            escalation_rules=[],
        ),
        personality=PersonalitySpec(
            tone="technical",
            verbosity="normal",
            formatting=[],
            interaction_style=[],
        ),
        risk=RiskSpec(
            risk_level="medium",
            confirm_before=["destructive_actions"],
            auto_proceed_rules=[],
        ),
        tool_posture=ToolPostureSpec(
            tool_use="allowed",
            blocked_patterns=[],
            allowed_tools=[],
            sandbox_root=None,
        ),
        meta={},
    )


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload
        self.headers = Message()

    def read(self) -> bytes:
        return json.dumps(self._payload, ensure_ascii=True).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_register_adds_location_tools() -> None:
    registry = ToolRegistry()
    register(registry)
    names = registry.list()
    assert "location.get" in names
    assert "location.set_default" in names
    assert "location.get_ip" in names


def test_location_get_provider_spec_describes_zero_arg_current_location() -> None:
    registry = ToolRegistry()
    register(registry)

    provider_specs = {spec.name: spec for spec in registry.provider_specs()}
    spec = provider_specs["location.get"]

    assert "current location" in spec.description.lower()
    assert "no arguments" in spec.description.lower()
    assert spec.parameters["type"] == "object"
    properties = spec.parameters["properties"]
    assert "prefer" in properties
    assert "max_privacy" in properties
    assert (
        "current-location" in str(properties["prefer"].get("description", "")).lower()
    )
    assert (
        "privacy cap" in str(properties["max_privacy"].get("description", "")).lower()
    )


def test_get_prefers_session_override_over_identity_and_ip(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        location_plugin,
        "_location_from_identity_profile",
        lambda _ctx: {"city": "Identity City", "country": "US", "timezone": "UTC"},
    )
    monkeypatch.setattr(
        location_plugin,
        "_lookup_ip_location",
        lambda _ctx, **_kwargs: {"city": "IP City", "country": "US", "timezone": "UTC"},
    )
    payload = _h_get(
        {},
        _ctx(
            tmp_path,
            context_metadata={
                "location_override": {
                    "city": "Session City",
                    "region": "CA",
                    "country": "US",
                    "timezone": "America/Los_Angeles",
                }
            },
        ),
    )
    assert payload["ok"] is True
    assert payload["data"]["location_source"] == "session.override"
    assert payload["data"]["city"] == "Session City"


def test_get_prefers_identity_default_over_ip_when_no_session(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        location_plugin,
        "_location_from_identity_profile",
        lambda _ctx: {"city": "Identity City", "country": "US", "timezone": "UTC"},
    )
    monkeypatch.setattr(
        location_plugin,
        "_lookup_ip_location",
        lambda _ctx, **_kwargs: {"city": "IP City", "country": "US", "timezone": "UTC"},
    )
    payload = _h_get({}, _ctx(tmp_path))
    assert payload["ok"] is True
    assert payload["data"]["location_source"] == "identity.default"
    assert payload["data"]["city"] == "Identity City"


def test_get_prefers_ip_when_requested(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        location_plugin,
        "_location_from_identity_profile",
        lambda _ctx: {"city": "Identity City", "country": "US", "timezone": "UTC"},
    )
    monkeypatch.setattr(
        location_plugin,
        "_lookup_ip_location",
        lambda _ctx, **_kwargs: {"city": "IP City", "country": "US", "timezone": "UTC"},
    )
    payload = _h_get(
        {"prefer": "ip"},
        _ctx(
            tmp_path,
            context_metadata={
                "location_override": {"city": "Session City", "country": "US"}
            },
        ),
    )
    assert payload["ok"] is True
    assert payload["data"]["location_source"] == "ip.geo"
    assert payload["data"]["city"] == "IP City"


def test_get_returns_source_none_when_unavailable(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        location_plugin, "_location_from_identity_profile", lambda _ctx: None
    )
    monkeypatch.setattr(
        location_plugin, "_lookup_ip_location", lambda _ctx, **_kwargs: None
    )
    payload = _h_get({}, _ctx(tmp_path))
    assert payload["ok"] is True
    assert payload["data"]["location_source"] == "none"
    assert "LOCATION_UNAVAILABLE" in payload["warnings"]


def test_get_returns_setup_hint_when_unconfigured(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        location_plugin, "_location_from_session_override", lambda _ctx: None
    )
    monkeypatch.setattr(
        location_plugin, "_location_from_identity_profile", lambda _ctx: None
    )
    monkeypatch.setattr(
        location_plugin, "_lookup_ip_location", lambda _ctx, **_kwargs: None
    )

    payload = _h_get({}, _ctx(tmp_path))

    assert payload["ok"] is True
    assert payload["data"]["location_source"] == "none"
    warnings = payload["warnings"]
    assert "LOCATION_NOT_CONFIGURED" in warnings
    assert "LOCATION_UNAVAILABLE" in warnings


def test_get_omits_setup_hint_when_identity_default_present(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        location_plugin, "_location_from_session_override", lambda _ctx: None
    )
    monkeypatch.setattr(
        location_plugin,
        "_location_from_identity_profile",
        lambda _ctx: {"city": "Identity City", "country": "US"},
    )
    monkeypatch.setattr(
        location_plugin, "_lookup_ip_location", lambda _ctx, **_kwargs: None
    )

    monkeypatch.setattr(
        location_plugin,
        "_resolve_location",
        lambda **_kwargs: None,
    )

    payload = _h_get({}, _ctx(tmp_path))

    assert payload["ok"] is True
    assert payload["data"]["location_source"] == "none"
    warnings = payload["warnings"]
    assert "LOCATION_NOT_CONFIGURED" not in warnings
    assert "LOCATION_UNAVAILABLE" in warnings


def test_get_omits_setup_hint_when_session_override_present(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        location_plugin,
        "_location_from_session_override",
        lambda _ctx: {"city": "Session City", "country": "US"},
    )
    monkeypatch.setattr(
        location_plugin, "_location_from_identity_profile", lambda _ctx: None
    )
    monkeypatch.setattr(
        location_plugin, "_lookup_ip_location", lambda _ctx, **_kwargs: None
    )
    monkeypatch.setattr(
        location_plugin,
        "_resolve_location",
        lambda **_kwargs: None,
    )

    payload = _h_get({}, _ctx(tmp_path))

    assert payload["ok"] is True
    assert payload["data"]["location_source"] == "none"
    warnings = payload["warnings"]
    assert "LOCATION_NOT_CONFIGURED" not in warnings
    assert "LOCATION_UNAVAILABLE" in warnings


def test_get_ip_returns_error_when_backend_unavailable(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        location_plugin,
        "_lookup_ip_location_with_error",
        lambda _ctx, **_kwargs: (None, None),
    )
    payload = _h_get_ip({}, _ctx(tmp_path))
    assert payload["ok"] is False
    assert payload["error"]["code"] == "IP_GEO_UNAVAILABLE"


def test_get_ip_success_returns_ip_source_and_warning(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        location_plugin,
        "_lookup_ip_location_with_error",
        lambda _ctx, **_kwargs: (
            {
                "city": "IP City",
                "country": "US",
                "timezone": "UTC",
                "warnings": ["IP_GEO_IMPRECISE"],
            },
            None,
        ),
    )
    payload = _h_get_ip({}, _ctx(tmp_path))
    assert payload["ok"] is True
    assert payload["data"]["location_source"] == "ip.geo"
    assert payload["data"]["city"] == "IP City"
    assert "IP_GEO_IMPRECISE" in payload["warnings"]


def test_get_respects_location_tool_policy_disable(tmp_path: Path) -> None:
    payload = _h_get({}, _ctx(tmp_path, location_cfg={"enabled": False}))
    assert payload["ok"] is False
    assert payload["error"]["code"] == "POLICY_DENIED"


def test_get_denies_when_scope_below_read_only(tmp_path: Path) -> None:
    payload = _h_get({}, _ctx(tmp_path, scope="NONE"))
    assert payload["ok"] is False
    assert payload["error"]["code"] == "POLICY_DENIED"


def test_get_ip_respects_policy_disable(tmp_path: Path) -> None:
    payload = _h_get_ip({}, _ctx(tmp_path, location_cfg={"ip_lookup_enabled": False}))
    assert payload["ok"] is False
    assert payload["error"]["code"] == "POLICY_DENIED"


def test_set_default_requires_runtime_context() -> None:
    payload = _h_set_default({"city": "SF"}, None)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "RUNTIME_CONTEXT_REQUIRED"


def test_set_default_requires_confirmation(tmp_path: Path) -> None:
    payload = _h_set_default(
        {"city": "SF"}, _ctx(tmp_path, confirm=False, scope="WRITE_SAFE")
    )
    assert payload["ok"] is False
    assert payload["error"]["code"] == "CONFIRM_REQUIRED"


def test_set_default_requires_write_scope(tmp_path: Path) -> None:
    payload = _h_set_default(
        {"city": "SF"}, _ctx(tmp_path, confirm=True, scope="READ_ONLY")
    )
    assert payload["ok"] is False
    assert payload["error"]["code"] == "POLICY_DENIED"


def test_set_default_persists_identity_home_location(
    monkeypatch, tmp_path: Path
) -> None:
    db_path = tmp_path / "identity.db"
    ctl = IdentityCtl(store=SQLiteIdentityStore(sqlite_path=str(db_path)))
    ctl.upsert_profile(_sample_profile("agent-location"))
    ctl.close()
    monkeypatch.setenv("OPENMINION_IDENTITY_DB", str(db_path))

    payload = _h_set_default(
        {
            "city": "San Francisco",
            "region": "CA",
            "country": "US",
            "timezone": "America/Los_Angeles",
            "privacy_level": "city",
        },
        _ctx(
            tmp_path,
            confirm=True,
            scope="WRITE_SAFE",
            context_metadata={"agent_id": "agent-location"},
        ),
    )
    assert payload["ok"] is True
    assert payload["data"]["location_source"] == "identity.default"
    assert payload["data"]["location"]["city"] == "San Francisco"
    assert payload["data"]["identity_hash"]
    assert payload["data"]["identity_version"] >= 1

    verify_ctl = IdentityCtl(store=SQLiteIdentityStore(sqlite_path=str(db_path)))
    try:
        profile = verify_ctl.get_profile("agent-location")
    finally:
        verify_ctl.close()
    assert profile is not None
    assert profile.meta is not None
    assert profile.meta.get("home_location", {}).get("city") == "San Francisco"
    assert profile.meta.get("location_privacy_level") == "city"


def test_set_default_maps_dependency_unconfigured(monkeypatch, tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, confirm=True, scope="WRITE_SAFE")
    ctx.repositories.identity_path = None
    monkeypatch.setattr(
        location_plugin, "resolve_identity_repository", lambda _ctx: None
    )

    payload = _h_set_default({"city": "Seattle"}, ctx)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "DEPENDENCY_MISSING"
    assert payload["data"]["reason_code"] == "storage_unconfigured"


def test_set_default_maps_dependency_unavailable(monkeypatch, tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, confirm=True, scope="WRITE_SAFE")
    ctx.repositories.identity_path = tmp_path / "identity.db"
    monkeypatch.setattr(
        location_plugin, "resolve_identity_repository", lambda _ctx: None
    )

    payload = _h_set_default({"city": "Seattle"}, ctx)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "DEPENDENCY_MISSING"
    assert payload["data"]["reason_code"] == "storage_unavailable"


def test_set_default_maps_missing_record(monkeypatch, tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, confirm=True, scope="WRITE_SAFE")

    class _MissingRepo:
        def get_profile(self, _agent_id: str):
            return None

        def upsert_profile(self, _profile):
            return "sha256:missing"

    monkeypatch.setattr(
        location_plugin, "resolve_identity_repository", lambda _ctx: _MissingRepo()
    )
    payload = _h_set_default({"city": "Seattle"}, ctx)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "NOT_FOUND"
    assert payload["data"]["reason_code"] == "record_not_found"


def test_set_default_maps_unexpected_storage_error(monkeypatch, tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, confirm=True, scope="WRITE_SAFE")

    class _BrokenRepo:
        def get_profile(self, _agent_id: str):
            raise RuntimeError("boom")

    monkeypatch.setattr(
        location_plugin, "resolve_identity_repository", lambda _ctx: _BrokenRepo()
    )
    payload = _h_set_default({"city": "Seattle"}, ctx)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "EXEC_ERROR"
    assert payload["data"]["reason_code"] == "storage_exec_error"


def test_get_ip_returns_network_denied_for_blocked_backend(tmp_path: Path) -> None:
    payload = _h_get_ip(
        {},
        _ctx(tmp_path, location_cfg={"ip_lookup_url": "https://127.0.0.1/json/"}),
    )
    assert payload["ok"] is False
    assert payload["error"]["code"] == "NETWORK_DENIED"


def test_ip_lookup_uses_cache_and_skips_raw_ip_storage(
    monkeypatch, tmp_path: Path
) -> None:
    location_plugin._IP_CACHE["record"] = None
    location_plugin._IP_CACHE["expires_at"] = 0.0
    calls = {"count": 0}

    def _fake_urlopen(_request, timeout):
        del timeout
        calls["count"] += 1
        return _Response(
            {
                "ip": "203.0.113.5",
                "city": "San Francisco",
                "region": "California",
                "country_name": "United States",
                "timezone": "America/Los_Angeles",
                "latitude": 37.7749,
                "longitude": -122.4194,
            }
        )

    monkeypatch.setattr(
        "openminion.tools.location.plugin.urllib.request.urlopen", _fake_urlopen
    )
    ctx = _ctx(
        tmp_path, location_cfg={"ip_cache_ttl_seconds": 3600, "allow_precise": False}
    )

    first = location_plugin._lookup_ip_location(ctx)
    second = location_plugin._lookup_ip_location(ctx)
    assert first is not None and second is not None
    assert calls["count"] == 1
    assert "ip" not in first
    assert "ip" not in second
    cached = location_plugin._IP_CACHE.get("record")
    assert isinstance(cached, dict)
    assert "ip" not in cached
    assert first.get("lat") is None
    assert first.get("lon") is None


def test_ip_lookup_cache_ttl_expiry_triggers_refresh(
    monkeypatch, tmp_path: Path
) -> None:
    location_plugin._IP_CACHE["record"] = None
    location_plugin._IP_CACHE["expires_at"] = 0.0
    calls = {"count": 0}
    clock = {"now": 1000.0}

    def _fake_time() -> float:
        return float(clock["now"])

    def _fake_urlopen(_request, timeout):
        del timeout
        calls["count"] += 1
        return _Response(
            {
                "city": "San Francisco",
                "region": "California",
                "country_name": "United States",
                "timezone": "America/Los_Angeles",
            }
        )

    monkeypatch.setattr("openminion.tools.location.plugin.time.time", _fake_time)
    monkeypatch.setattr(
        "openminion.tools.location.plugin.urllib.request.urlopen", _fake_urlopen
    )

    ctx = _ctx(tmp_path, location_cfg={"ip_cache_ttl_seconds": 10})
    first = location_plugin._lookup_ip_location(ctx)
    clock["now"] = 1005.0
    second = location_plugin._lookup_ip_location(ctx)
    clock["now"] = 1012.0
    third = location_plugin._lookup_ip_location(ctx)

    assert first is not None and second is not None and third is not None
    assert calls["count"] == 2


def test_ip_lookup_retries_and_refresh_forces_network(
    monkeypatch, tmp_path: Path
) -> None:
    location_plugin._IP_CACHE["record"] = None
    location_plugin._IP_CACHE["expires_at"] = 0.0
    calls = {"count": 0}

    def _fake_urlopen(_request, timeout):
        del timeout
        calls["count"] += 1
        if calls["count"] == 1:
            raise OSError("transient failure")
        return _Response(
            {
                "city": "Seattle",
                "region": "Washington",
                "country_name": "United States",
                "timezone": "America/Los_Angeles",
                "latitude": 47.6062,
                "longitude": -122.3321,
            }
        )

    monkeypatch.setattr(
        "openminion.tools.location.plugin.urllib.request.urlopen", _fake_urlopen
    )
    monkeypatch.setattr("openminion.tools.location.plugin.time.sleep", lambda _s: None)
    ctx = _ctx(tmp_path, location_cfg={"ip_lookup_retries": 1, "allow_precise": True})

    payload = _h_get_ip({"refresh": True, "max_privacy": "precise"}, ctx)
    assert payload["ok"] is True
    assert payload["data"]["location_source"] == "ip.geo"
    assert payload["data"]["lat"] == pytest.approx(47.6062)
    assert payload["data"]["lon"] == pytest.approx(-122.3321)
    assert calls["count"] == 2
