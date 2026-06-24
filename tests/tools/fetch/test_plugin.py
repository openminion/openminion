from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from openminion.modules.tool.runtime.policy import Policy
from openminion.modules.tool.runtime import RuntimeContext
from openminion.modules.tool.registry import ToolRegistry

from openminion.tools.fetch.plugin import (
    _choose_provider_name,
    _h_get,
    _h_head,
    _h_providers,
    register,
)


def test_register_adds_fetch_tools() -> None:
    registry = ToolRegistry()
    register(registry)
    names = registry.list()
    assert "fetch.get" in names
    assert "fetch.head" in names
    assert "fetch.providers" in names


class _FakeProvider:
    name = "core-http"
    capabilities = {
        "render": ["none"],
        "extract": ["none", "text", "auto"],
        "formats": ["text/html"],
    }

    def fetch(self, request: dict[str, Any], _ctx: Any) -> dict[str, Any]:
        method = str(request.get("method", "GET")).upper()
        if method == "HEAD":
            return {
                "ok": True,
                "final_url": str(request.get("url", "")),
                "status_code": 200,
                "headers": {"content-length": "12"},
                "content_type": "text/html; charset=utf-8",
                "content_bytes": 0,
                "raw_body": b"",
                "warnings": [],
                "backend": "core-http",
            }
        return {
            "ok": True,
            "final_url": str(request.get("url", "")),
            "status_code": 200,
            "headers": {"content-type": "text/html; charset=utf-8"},
            "content_type": "text/html; charset=utf-8",
            "content_bytes": 22,
            "raw_body": b"<title>Example</title>",
            "extracted_text": "Example",
            "title": "Example",
            "warnings": [],
            "backend": "core-http",
        }


class _FakeScraplingProvider(_FakeProvider):
    name = "scrapling"

    def fetch(self, request: dict[str, Any], _ctx: Any) -> dict[str, Any]:
        payload = super().fetch(request, _ctx)
        payload["backend"] = "scrapling:static"
        payload["warnings"] = ["DOWNGRADED_TO_STATIC"]
        return payload


class _FakeRegistry:
    def __init__(self) -> None:
        self._provider = _FakeProvider()

    def list_names(self) -> list[str]:
        return ["core-http"]

    def get(self, name: str) -> Any:
        assert name == "core-http"
        return self._provider

    def list(self) -> list[Any]:
        return [self._provider]


class _FakeRegistryWithScrapling(_FakeRegistry):
    def __init__(self) -> None:
        self._provider = _FakeProvider()
        self._scrapling = _FakeScraplingProvider()

    def list_names(self) -> list[str]:
        return ["core-http", "scrapling"]

    def get(self, name: str) -> Any:
        if name == "scrapling":
            return self._scrapling
        assert name == "core-http"
        return self._provider

    def list(self) -> list[Any]:
        return [self._provider, self._scrapling]


class _FakeTinyFishProvider(_FakeProvider):
    name = "tinyfish"

    def fetch(self, request: dict[str, Any], _ctx: Any) -> dict[str, Any]:
        payload = super().fetch(request, _ctx)
        payload["backend"] = "tinyfish"
        return payload


class _FakeRegistryWithTinyFish(_FakeRegistry):
    def __init__(self) -> None:
        self._provider = _FakeProvider()
        self._tinyfish = _FakeTinyFishProvider()

    def list_names(self) -> list[str]:
        return ["core-http", "tinyfish"]

    def get(self, name: str) -> Any:
        if name == "tinyfish":
            return self._tinyfish
        assert name == "core-http"
        return self._provider

    def list(self) -> list[Any]:
        return [self._provider, self._tinyfish]


class _FailingProvider(_FakeProvider):
    def __init__(self, *, name: str) -> None:
        self.name = name
        self.calls = 0

    def fetch(self, request: dict[str, Any], _ctx: Any) -> dict[str, Any]:
        del request
        self.calls += 1
        return {
            "ok": False,
            "error": {
                "code": "UPSTREAM_ERROR",
                "message": f"{self.name} failed",
            },
            "backend": self.name,
        }


class _RuntimeConfigRegistry:
    def __init__(self) -> None:
        self._core = _FakeProvider()
        self._scrapling = _FakeScraplingProvider()

    def list_names(self) -> list[str]:
        return ["core-http", "scrapling"]

    def get(self, name: str) -> Any:
        if name == "scrapling":
            return self._scrapling
        assert name == "core-http"
        return self._core

    def list(self) -> list[Any]:
        return [self._core, self._scrapling]


class _FallbackRegistry:
    def __init__(self, *, allow_scrapling_success: bool) -> None:
        self._core = _FailingProvider(name="core-http")
        self._scrapling = (
            _FakeScraplingProvider()
            if allow_scrapling_success
            else _FailingProvider(name="scrapling")
        )

    def list_names(self) -> list[str]:
        return ["core-http", "scrapling"]

    def get(self, name: str) -> Any:
        if name == "scrapling":
            return self._scrapling
        assert name == "core-http"
        return self._core

    def list(self) -> list[Any]:
        return [self._core, self._scrapling]


class _CASArtifactRef:
    def __init__(self, ref: str, sha256: str) -> None:
        self.ref = ref
        self.sha256 = sha256


class _RecordingArtifactCtl:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def ingest_bytes(self, **kwargs: Any) -> _CASArtifactRef:
        self.calls.append(dict(kwargs))
        return _CASArtifactRef(
            ref="artifact://sha256/" + ("c" * 64),
            sha256="c" * 64,
        )


class _FailingArtifactCtl:
    def ingest_bytes(self, **kwargs: Any) -> Any:
        raise RuntimeError("artifactctl unavailable")


def _runtime_ctx(*, runtime_tools: dict[str, Any] | None = None) -> Any:
    if runtime_tools is None:
        return None
    return SimpleNamespace(
        policy=SimpleNamespace(
            raw={"context_metadata": {"runtime_tools": runtime_tools}}
        )
    )


def test_get_and_head_use_provider_dispatch(monkeypatch) -> None:
    monkeypatch.setattr(
        "openminion.tools.fetch.plugin._ensure_provider_registry",
        lambda: _FakeRegistry(),
    )

    get_payload = _h_get({"url": "https://example.com"}, None)
    head_payload = _h_head({"url": "https://example.com"}, None)

    assert get_payload["ok"] is True
    assert head_payload["ok"] is True
    assert get_payload["data"]["backend"] == "core-http"
    assert head_payload["data"]["backend"] == "core-http"
    assert "artifacts" in get_payload["data"]
    assert "raw_body" in get_payload["data"]["artifacts"]


def test_get_returns_backend_not_available_when_explicit_backend_missing(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "openminion.tools.fetch.plugin._ensure_provider_registry",
        lambda: _FakeRegistry(),
    )
    payload = _h_get(
        {"url": "https://example.com", "prefer_backend": "scrapling"}, None
    )
    assert payload["ok"] is False
    assert payload["error"]["code"] == "BACKEND_NOT_AVAILABLE"


def test_providers_lists_core_http_capability() -> None:
    payload = _h_providers({}, None)
    assert payload["ok"] is True
    providers = payload["data"]["providers"]
    assert any(item.get("name") == "core-http" for item in providers)


def test_choose_provider_name_normalizes_core_alias() -> None:
    chosen = _choose_provider_name(
        {"url": "https://example.com", "prefer_backend": "core"},
        available={"core-http", "scrapling"},
    )
    assert chosen == "core-http"


def test_choose_provider_name_uses_scrapling_mode_hint() -> None:
    chosen = _choose_provider_name(
        {
            "url": "https://example.com",
            "prefer_backend": "auto",
            "provider_options": {"scrapling": {"mode": "dynamic"}},
        },
        available={"core-http", "scrapling"},
    )
    assert chosen == "scrapling"


def test_choose_provider_name_uses_tinyfish_hint() -> None:
    chosen = _choose_provider_name(
        {
            "url": "https://example.com",
            "prefer_backend": "auto",
            "provider_options": {"tinyfish": {"format": "markdown"}},
        },
        available={"core-http", "tinyfish"},
    )
    assert chosen == "tinyfish"


def test_choose_provider_name_ignores_empty_tinyfish_hint() -> None:
    chosen = _choose_provider_name(
        {
            "url": "https://example.com",
            "prefer_backend": "auto",
            "provider_options": {"tinyfish": {}},
        },
        available={"core-http", "tinyfish"},
    )
    assert chosen == "core-http"


def test_runtime_tools_fetch_order_overrides_legacy_scrapling_hint(monkeypatch) -> None:
    monkeypatch.setattr(
        "openminion.tools.fetch.plugin._ensure_provider_registry",
        lambda: _RuntimeConfigRegistry(),
    )

    payload = _h_get(
        {
            "url": "https://example.com",
            "provider_options": {"scrapling": {"mode": "dynamic"}},
        },
        _runtime_ctx(
            runtime_tools={
                "fetch": {
                    "enabled_providers": ["core-http", "scrapling"],
                    "default_provider": "core-http",
                    "provider_order": ["core-http", "scrapling"],
                    "allow_fallback": True,
                }
            }
        ),
    )

    assert payload["ok"] is True
    assert payload["data"]["backend"] == "core-http"


def test_explicit_tinyfish_head_does_not_fallback(monkeypatch) -> None:
    class _HeadRejectingTinyFish(_FakeTinyFishProvider):
        def fetch(self, request: dict[str, Any], _ctx: Any) -> dict[str, Any]:
            if str(request.get("method", "")).upper() == "HEAD":
                return {
                    "ok": False,
                    "error": {
                        "code": "INVALID_ARGUMENT",
                        "message": "TinyFish fetch does not support HEAD requests",
                    },
                    "backend": "tinyfish",
                }
            return super().fetch(request, _ctx)

    class _HeadRegistry(_FakeRegistryWithTinyFish):
        def __init__(self) -> None:
            self._provider = _FakeProvider()
            self._tinyfish = _HeadRejectingTinyFish()

    monkeypatch.setattr(
        "openminion.tools.fetch.plugin._ensure_provider_registry",
        lambda: _HeadRegistry(),
    )

    payload = _h_head(
        {"url": "https://example.com", "prefer_backend": "tinyfish"},
        None,
    )

    assert payload["ok"] is False
    assert payload["error"]["code"] == "INVALID_ARGUMENT"


def test_runtime_tools_fetch_can_fallback_after_tinyfish_head_failure(
    monkeypatch,
) -> None:
    class _HeadRejectingTinyFish(_FakeTinyFishProvider):
        def fetch(self, request: dict[str, Any], _ctx: Any) -> dict[str, Any]:
            if str(request.get("method", "")).upper() == "HEAD":
                return {
                    "ok": False,
                    "error": {
                        "code": "INVALID_ARGUMENT",
                        "message": "TinyFish fetch does not support HEAD requests",
                    },
                    "backend": "tinyfish",
                }
            return super().fetch(request, _ctx)

    class _RuntimeTinyFishRegistry(_FakeRegistryWithTinyFish):
        def __init__(self) -> None:
            self._provider = _FakeProvider()
            self._tinyfish = _HeadRejectingTinyFish()

        def list_names(self) -> list[str]:
            return ["tinyfish", "core-http"]

        def list(self) -> list[Any]:
            return [self._tinyfish, self._provider]

    monkeypatch.setattr(
        "openminion.tools.fetch.plugin._ensure_provider_registry",
        lambda: _RuntimeTinyFishRegistry(),
    )

    payload = _h_head(
        {"url": "https://example.com"},
        _runtime_ctx(
            runtime_tools={
                "fetch": {
                    "enabled_providers": ["tinyfish", "core-http"],
                    "default_provider": "tinyfish",
                    "provider_order": ["tinyfish", "core-http"],
                    "allow_fallback": True,
                }
            }
        ),
    )

    assert payload["ok"] is True
    assert payload["data"]["backend"] == "core-http"


def test_runtime_tools_fetch_falls_back_to_next_backend(monkeypatch) -> None:
    monkeypatch.setattr(
        "openminion.tools.fetch.plugin._ensure_provider_registry",
        lambda: _FallbackRegistry(allow_scrapling_success=True),
    )

    payload = _h_get(
        {"url": "https://example.com"},
        _runtime_ctx(
            runtime_tools={
                "fetch": {
                    "enabled_providers": ["core-http", "scrapling"],
                    "default_provider": "core-http",
                    "provider_order": ["core-http", "scrapling"],
                    "allow_fallback": True,
                }
            }
        ),
    )

    assert payload["ok"] is True
    assert payload["data"]["backend"] == "scrapling:static"
    assert any(
        "fell back to 'scrapling:static'" in warning
        for warning in payload["data"]["warnings"]
    )


def test_runtime_tools_fetch_can_disable_fallback(monkeypatch) -> None:
    registry = _FallbackRegistry(allow_scrapling_success=True)
    monkeypatch.setattr(
        "openminion.tools.fetch.plugin._ensure_provider_registry",
        lambda: registry,
    )

    payload = _h_get(
        {"url": "https://example.com"},
        _runtime_ctx(
            runtime_tools={
                "fetch": {
                    "enabled_providers": ["core-http", "scrapling"],
                    "default_provider": "core-http",
                    "provider_order": ["core-http", "scrapling"],
                    "allow_fallback": False,
                }
            }
        ),
    )

    assert payload["ok"] is False
    assert payload["error"]["code"] == "UPSTREAM_ERROR"
    assert registry._core.calls == 1


def test_explicit_backend_bypasses_runtime_tools_enabled_backend_filter(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "openminion.tools.fetch.plugin._ensure_provider_registry",
        lambda: _RuntimeConfigRegistry(),
    )

    payload = _h_get(
        {
            "url": "https://example.com",
            "prefer_backend": "scrapling",
        },
        _runtime_ctx(
            runtime_tools={
                "fetch": {
                    "enabled_providers": ["core-http"],
                    "default_provider": "core-http",
                    "provider_order": ["core-http"],
                    "allow_fallback": True,
                }
            }
        ),
    )

    assert payload["ok"] is True
    assert payload["data"]["backend"] == "scrapling:static"


def test_get_emits_provider_selected_and_completed_events(monkeypatch) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        "openminion.tools.fetch.plugin._ensure_provider_registry",
        lambda: _FakeRegistry(),
    )
    monkeypatch.setattr(
        "openminion.tools.fetch.plugin.emit_family_event",
        lambda _ctx, *, event, payload=None: events.append(
            (str(event), dict(payload or {}))
        ),
    )
    monkeypatch.setattr(
        "openminion.modules.tool.family.runtime.emit_family_event",
        lambda _ctx, *, event, payload=None: events.append(
            (str(event), dict(payload or {}))
        ),
    )

    payload = _h_get({"url": "https://example.com"}, None)
    assert payload["ok"] is True
    names = [name for name, _ in events]
    assert "fetch.provider.selected" in names
    assert "fetch.completed" in names


def test_get_audit_records_selected_backend_with_orchestration_metadata(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "openminion.tools.fetch.plugin._ensure_provider_registry",
        lambda: _FakeRegistry(),
    )
    workspace = tmp_path / "workspace"
    run_root = tmp_path / "run"
    workspace.mkdir(parents=True, exist_ok=True)
    run_root.mkdir(parents=True, exist_ok=True)
    ctx = RuntimeContext(
        policy=Policy(
            raw={
                "workspace_root": str(workspace),
                "context_metadata": {
                    "orchestration": {
                        "mode_name": "act_single",
                        "workflow_name": None,
                        "workflow_kind": None,
                        "command_id": "cmd-fetch-123",
                    }
                },
            }
        ),
        workspace=workspace,
        run_root=run_root,
        scope="READ_ONLY",
        confirm=False,
    )

    payload = _h_get({"url": "https://example.com"}, ctx)

    assert payload["ok"] is True
    records = [
        json.loads(line)
        for line in (run_root / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(
        row.get("event") == "fetch.provider.selected"
        and row.get("selected_backend") == "core-http"
        and row.get("mode_name") == "act_single"
        and row.get("workflow_name") is None
        and row.get("workflow_kind") is None
        and row.get("command_id") == "cmd-fetch-123"
        for row in records
    )


def test_get_emits_degraded_event_for_scrapling_fallback(monkeypatch) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        "openminion.tools.fetch.plugin._ensure_provider_registry",
        lambda: _FakeRegistryWithScrapling(),
    )
    monkeypatch.setattr(
        "openminion.tools.fetch.plugin.emit_family_event",
        lambda _ctx, *, event, payload=None: events.append(
            (str(event), dict(payload or {}))
        ),
    )

    payload = _h_get(
        {
            "url": "https://example.com",
            "provider_options": {"scrapling": {"mode": "dynamic"}},
        },
        None,
    )
    assert payload["ok"] is True
    assert any(name == "fetch.degraded" for name, _ in events)


def test_get_maps_needs_approval_to_blocked_event(monkeypatch) -> None:
    class _NeedsApprovalProvider(_FakeProvider):
        def fetch(self, request: dict[str, Any], _ctx: Any) -> dict[str, Any]:
            del request
            return {
                "ok": False,
                "error": {
                    "code": "NEEDS_APPROVAL",
                    "message": "approval required",
                    "details": {"required_scope": "tool.fetch.browser"},
                },
                "backend": "scrapling",
            }

    class _NeedsApprovalRegistry(_FakeRegistry):
        def __init__(self) -> None:
            self._provider = _NeedsApprovalProvider()

    events: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        "openminion.tools.fetch.plugin._ensure_provider_registry",
        lambda: _NeedsApprovalRegistry(),
    )
    monkeypatch.setattr(
        "openminion.tools.fetch.plugin.emit_family_event",
        lambda _ctx, *, event, payload=None: events.append(
            (str(event), dict(payload or {}))
        ),
    )

    payload = _h_get({"url": "https://example.com"}, None)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "NEEDS_APPROVAL"
    assert any(name == "fetch.blocked" for name, _ in events)


def test_fetch_uses_shared_emit_family_event_helper(monkeypatch) -> None:
    from unittest.mock import patch

    monkeypatch.setattr(
        "openminion.tools.fetch.plugin._ensure_provider_registry",
        lambda: _FakeRegistry(),
    )
    with patch("openminion.tools.fetch.plugin.emit_family_event") as mock_emit:
        result = _h_get({"url": "https://example.com"}, None)

    assert result["ok"] is True
    assert mock_emit.called, "emit_family_event must be called by fetch plugin"
    emitted_events = [call.kwargs.get("event") for call in mock_emit.call_args_list]
    assert "fetch.requested" in emitted_events


def test_fetch_uses_shared_run_provider_chain_helper(monkeypatch) -> None:
    from unittest.mock import patch

    monkeypatch.setattr(
        "openminion.tools.fetch.plugin._ensure_provider_registry",
        lambda: _FakeRegistry(),
    )
    with patch(
        "openminion.tools.fetch.plugin.run_provider_chain",
        wraps=__import__(
            "openminion.modules.tool.family.runtime",
            fromlist=["run_provider_chain"],
        ).run_provider_chain,
    ) as mock_run:
        result = _h_get({"url": "https://example.com"}, None)

    assert result["ok"] is True
    assert mock_run.called, "run_provider_chain must be called by fetch plugin"


def test_fetch_uses_shared_is_tool_disabled_by_policy(
    monkeypatch, tmp_path: Path
) -> None:
    from unittest.mock import patch

    monkeypatch.setattr(
        "openminion.tools.fetch.plugin._ensure_provider_registry",
        lambda: _FakeRegistry(),
    )
    workspace = tmp_path / "workspace"
    run_root = tmp_path / "run"
    workspace.mkdir()
    run_root.mkdir()
    ctx = RuntimeContext(
        policy=Policy(
            raw={
                "workspace_root": str(workspace),
                "tools": {"fetch": {"enabled": False}},
            }
        ),
        workspace=workspace,
        run_root=run_root,
        scope="READ_ONLY",
        confirm=False,
    )
    with patch(
        "openminion.tools.fetch.plugin.is_tool_disabled_by_policy",
        wraps=__import__(
            "openminion.modules.tool.family.policy",
            fromlist=["is_tool_disabled_by_policy"],
        ).is_tool_disabled_by_policy,
    ) as mock_check:
        result = _h_get({"url": "https://example.com"}, ctx)

    assert mock_check.called, (
        "is_tool_disabled_by_policy must be called by fetch plugin"
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "POLICY_DENIED"


def test_fetch_durable_artifacts_emit_canonical_refs_when_cas_available(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "openminion.tools.fetch.plugin._ensure_provider_registry",
        lambda: _FakeRegistry(),
    )
    workspace = tmp_path / "workspace"
    run_root = tmp_path / "run"
    workspace.mkdir()
    run_root.mkdir()
    artifactctl = _RecordingArtifactCtl()
    ctx = RuntimeContext(
        policy=Policy(raw={"workspace_root": str(workspace)}),
        workspace=workspace,
        run_root=run_root,
        scope="READ_ONLY",
        confirm=False,
        artifactctl=artifactctl,
    )
    ctx.session_id = "sess-fetch"
    ctx.trace_id = "trace-fetch"
    ctx.tool_name = "fetch.get"

    payload = _h_get({"url": "https://example.com"}, ctx)

    assert payload["ok"] is True
    artifacts = payload["data"]["artifacts"]
    assert artifacts["raw_body"] == "artifact://sha256/" + ("c" * 64)
    assert artifacts["extracted_text"] == "artifact://sha256/" + ("c" * 64)
    assert artifacts["metadata_json"] == "artifact://sha256/" + ("c" * 64)
    assert len(artifactctl.calls) == 3


def test_fetch_durable_artifacts_fall_back_to_local_paths_when_cas_fails(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "openminion.tools.fetch.plugin._ensure_provider_registry",
        lambda: _FakeRegistry(),
    )
    workspace = tmp_path / "workspace"
    run_root = tmp_path / "run"
    workspace.mkdir()
    run_root.mkdir()
    ctx = RuntimeContext(
        policy=Policy(raw={"workspace_root": str(workspace)}),
        workspace=workspace,
        run_root=run_root,
        scope="READ_ONLY",
        confirm=False,
        artifactctl=_FailingArtifactCtl(),
    )
    ctx.session_id = "sess-fetch"
    ctx.trace_id = "trace-fetch"
    ctx.tool_name = "fetch.get"

    payload = _h_get({"url": "https://example.com"}, ctx)

    assert payload["ok"] is True
    artifacts = payload["data"]["artifacts"]
    assert artifacts["raw_body"].startswith("artifacts/fetch/")
    assert artifacts["extracted_text"].startswith("artifacts/fetch/")
    assert artifacts["metadata_json"].startswith("artifacts/fetch/")
    assert not any(
        str(value).startswith("artifact://sha256/") for value in artifacts.values()
    )
