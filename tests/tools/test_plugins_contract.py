from __future__ import annotations

from dataclasses import dataclass

import pytest

from openminion.modules.tool.bootstrap import build_runtime_bootstrap
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.runtime.plugins import (
    PluginRegistrarDiscoveryError,
    discover_plugin_registrars,
)
from openminion.modules.tool.registry import ToolRegistry
from openminion.modules.tool.contracts import (
    ModelToolDef,
    RuntimeBindingDef,
    ToolBindingManifest,
)
from openminion.modules.tool.contracts.schemas import SysInfoArgs
from openminion.modules.tool.registry import ToolSpec


def _noop_handler(args, ctx):  # noqa: ANN001, ANN202
    del args, ctx
    return {"ok": True}


@dataclass
class _PolicyStub:
    def is_plugin_enabled(self, _name: str) -> bool:
        return True


@dataclass
class _EntryPointMissingRegister:
    name: str = "bad_plugin"
    module: str = "fake.module"

    def load(self):  # noqa: D401
        class _Registrar:
            module_id = "bad_plugin"
            is_provider_only = False

            def get_manifest(self, ctx):
                del ctx
                return ToolBindingManifest(module_id=self.module_id)

        return _Registrar()


class _PluginRegistrar:
    module_id = "ok_plugin"
    is_provider_only = False

    def get_manifest(self, ctx):
        del ctx
        return ToolBindingManifest(
            module_id=self.module_id,
            model_tools=(
                ModelToolDef(
                    model_tool_id="plugin.ok_plugin.sys_info",
                    description="Plugin system information.",
                    parameters={},
                ),
            ),
            runtime_bindings=(
                RuntimeBindingDef(
                    runtime_binding_id="runtime.plugin.ok_plugin.sys_info",
                    model_tool_id="plugin.ok_plugin.sys_info",
                    runtime_candidates=("plugin.ok_plugin.sys_info",),
                ),
            ),
        )

    def register(self, registry, ctx):
        del ctx
        registry.add(
            ToolSpec(
                "plugin.ok_plugin.sys_info",
                SysInfoArgs,
                "READ_ONLY",
                _noop_handler,
            )
        )


class _OwnerMismatchRegistrar(_PluginRegistrar):
    module_id = "another_owner"


class _MissingCandidateRegistrar(_PluginRegistrar):
    def register(self, registry, ctx):
        del registry, ctx


class _ExplodingRegistrar(_PluginRegistrar):
    def register(self, registry, ctx):
        del registry, ctx
        raise ValueError("fixture exploded")


@dataclass
class _EntryPointWithManifest:
    name: str = "ok_plugin"
    module: str = "fake.ok_module"

    def load(self):  # noqa: D401
        return _PluginRegistrar()


@dataclass
class _EntryPointMissingContractVersion:
    name: str = "missing_contract"
    module: str = "fake.missing_contract"

    def load(self):  # noqa: D401
        return object()


def test_plugin_without_register_fails_closed(monkeypatch):
    monkeypatch.setattr(
        "openminion.modules.tool.runtime.plugins._plugin_entry_points",
        lambda: [_EntryPointMissingRegister()],
    )

    with pytest.raises(PluginRegistrarDiscoveryError) as exc_info:
        discover_plugin_registrars(_PolicyStub())

    assert exc_info.value.plugin_id == "bad_plugin"
    assert exc_info.value.reason_code == "registrar_invalid"
    assert isinstance(exc_info.value.__cause__, TypeError)


def test_plugin_without_registrar_contract_fails_closed(monkeypatch):
    monkeypatch.setattr(
        "openminion.modules.tool.runtime.plugins._plugin_entry_points",
        lambda: [_EntryPointMissingContractVersion()],
    )

    with pytest.raises(PluginRegistrarDiscoveryError) as exc_info:
        discover_plugin_registrars(_PolicyStub())

    assert exc_info.value.plugin_id == "missing_contract"
    assert exc_info.value.reason_code == "registrar_invalid"
    assert isinstance(exc_info.value.__cause__, TypeError)


def test_plugin_manifest_metadata_is_reported(monkeypatch):
    monkeypatch.setattr(
        "openminion.modules.tool.runtime.plugins._plugin_entry_points",
        lambda: [_EntryPointWithManifest()],
    )

    registrars, statuses = discover_plugin_registrars(_PolicyStub())

    assert len(statuses) == 1
    status = statuses[0]
    assert status["name"] == "ok_plugin"
    assert status["loaded"] is True
    assert status["healthy"] is True
    bootstrap = build_runtime_bootstrap(plugin_registrars=tuple(registrars))
    assert "plugin.ok_plugin.sys_info" in bootstrap.registry.list()


def test_external_plugin_owner_mismatch_is_classified() -> None:
    with pytest.raises(ToolRuntimeError) as exc_info:
        build_runtime_bootstrap(
            plugin_registrars=(("ok_plugin", _OwnerMismatchRegistrar()),)
        )

    assert exc_info.value.code == "PLUGIN_ACTIVATION_FAILED"
    assert exc_info.value.details["reason_code"] == "owner_mismatch"


def test_external_plugin_missing_runtime_candidate_is_classified() -> None:
    with pytest.raises(ToolRuntimeError) as exc_info:
        build_runtime_bootstrap(
            plugin_registrars=(("ok_plugin", _MissingCandidateRegistrar()),)
        )

    assert exc_info.value.code == "PLUGIN_ACTIVATION_FAILED"
    assert exc_info.value.details["reason_code"] == "candidate_missing"


def test_external_plugin_registration_failure_is_classified() -> None:
    with pytest.raises(ToolRuntimeError) as exc_info:
        build_runtime_bootstrap(
            plugin_registrars=(("ok_plugin", _ExplodingRegistrar()),)
        )

    assert exc_info.value.code == "PLUGIN_ACTIVATION_FAILED"
    assert exc_info.value.details["reason_code"] == "registration_failed"
    assert isinstance(exc_info.value.__cause__, ValueError)


def test_gws_plugin_registration_with_contract_compatibility():
    from openminion.tools.gws.plugin import GwsToolPlugin

    registry = ToolRegistry()
    plugin = GwsToolPlugin()

    assert hasattr(plugin, "contract_version")
    assert plugin.contract_version == "v1"

    plugin.register(registry)

    registered_tools = list(registry.list().keys())
    expected_tools = [
        "gws.call",
        "gws.schema",
        "gws.auth.setup",
        "gws.auth.login",
        "gws.auth.export",
    ]

    for tool in expected_tools:
        assert tool in registered_tools, f"Expected {tool} to be registered"

    for tool in expected_tools:
        tool_spec = registry.get(tool)
        assert tool_spec is not None


def test_default_bootstrap_can_build_twice_without_provider_failures() -> None:
    first = build_runtime_bootstrap()
    from openminion.tools.browser import provider_registry as browser_registry
    from openminion.tools.fetch.providers import provider_registry as fetch_registry
    from openminion.tools.search.providers import provider_registry as search_registry

    first_browser = [
        (name, id(provider)) for name, provider in browser_registry().items()
    ]
    first_fetch = [
        (provider.name, id(provider)) for provider in fetch_registry().list()
    ]
    first_search = [
        (provider.provider_id, id(provider)) for provider in search_registry().list()
    ]
    second = build_runtime_bootstrap()

    for bootstrap in (first, second):
        failed = [
            record.module_name
            for record in bootstrap.bootstrap_records or []
            if record.status in {"register_failed", "registrar_failed"}
        ]
        assert failed == []
    assert [
        (provider.name, id(provider)) for provider in fetch_registry().list()
    ] == first_fetch
    assert [
        (provider.provider_id, id(provider)) for provider in search_registry().list()
    ] == first_search
    assert [
        (name, id(provider)) for name, provider in browser_registry().items()
    ] == first_browser
