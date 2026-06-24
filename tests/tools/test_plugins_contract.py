from __future__ import annotations

from dataclasses import dataclass

from openminion.modules.tool.runtime.plugins import load_plugins
from openminion.modules.tool.registry import ToolRegistry
from openminion.modules.tool.contracts.schemas import SysInfoArgs


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
        class _Plugin:
            tool_id = "bad.plugin"
            contract_version = "v1"
            capabilities = ("read_only",)

        return _Plugin


@dataclass
class _EntryPointWithManifest:
    name: str = "ok_plugin"
    module: str = "fake.ok_module"

    def load(self):  # noqa: D401
        from openminion.modules.tool.registry import ToolSpec

        class _Plugin:
            tool_id = "sys.info"
            contract_version = "v1"
            capabilities = ("read_only",)
            TOOL_MANIFEST = [{"name": "sys.info"}]

            def register(self, registry):
                registry.add(
                    ToolSpec("sys.info.plugin", SysInfoArgs, "READ_ONLY", _noop_handler)
                )

            def healthcheck(self):
                return {"ok": True}

        return _Plugin


@dataclass
class _EntryPointMissingContractVersion:
    name: str = "missing_contract"
    module: str = "fake.missing_contract"

    def load(self):  # noqa: D401
        class _Plugin:
            tool_id = "missing.contract"
            capabilities = ("read_only",)

            def register(self, registry):
                del registry

            def healthcheck(self):
                return {"ok": True}

        return _Plugin


def test_plugin_without_register_is_marked_unhealthy(monkeypatch):
    monkeypatch.setattr(
        "openminion.modules.tool.runtime.plugins._plugin_entry_points",
        lambda: [_EntryPointMissingRegister()],
    )

    registry = ToolRegistry()
    statuses = load_plugins(registry, _PolicyStub())

    assert len(statuses) == 1
    status = statuses[0]
    assert status["name"] == "bad_plugin"
    assert status["loaded"] is False
    assert status["healthy"] is False
    assert "register" in status.get("error", "")


def test_plugin_missing_contract_version_is_marked_unhealthy(monkeypatch):
    monkeypatch.setattr(
        "openminion.modules.tool.runtime.plugins._plugin_entry_points",
        lambda: [_EntryPointMissingContractVersion()],
    )

    registry = ToolRegistry()
    statuses = load_plugins(registry, _PolicyStub())

    assert len(statuses) == 1
    status = statuses[0]
    assert status["name"] == "missing_contract"
    assert status["loaded"] is False
    assert status["healthy"] is False
    assert "contract_version" in status.get("error", "")


def test_plugin_manifest_metadata_is_reported(monkeypatch):
    monkeypatch.setattr(
        "openminion.modules.tool.runtime.plugins._plugin_entry_points",
        lambda: [_EntryPointWithManifest()],
    )

    registry = ToolRegistry()
    statuses = load_plugins(registry, _PolicyStub())

    assert len(statuses) == 1
    status = statuses[0]
    assert status["name"] == "ok_plugin"
    assert status["loaded"] is True
    assert status["healthy"] is True
    assert status["manifest_count"] == 1


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
