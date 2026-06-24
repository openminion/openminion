from __future__ import annotations

import importlib
import sys
from types import ModuleType
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from openminion.modules.tool.runtime.manager import ToolRegistryManager
from openminion.modules.tool.runtime.registrar import (
    ToolRegisterContext,
    ToolModuleRegistrar,
)
from openminion.modules.tool.registry import ToolRegistry

# File-role contract reference for the tool-plugin layout consistency audit rule.


@dataclass
class FakeManifest:
    module_id: str
    model_tools: tuple = ()
    runtime_bindings: tuple = ()


class FakeRegistrar:
    module_id = "fake_module"

    def get_manifest(self, ctx: ToolRegisterContext) -> FakeManifest:
        return FakeManifest(module_id=self.module_id)

    def register(self, registry: ToolRegistry, ctx: ToolRegisterContext) -> None:
        pass


def test_tool_register_context_has_required_fields() -> None:
    ctx = ToolRegisterContext(
        module_id="test",
        config={"key": "value"},
        workspace_root=Path("/workspace"),
        run_root=Path("/run"),
        prepared_state={"dynamic": True},
        strict=True,
    )
    assert ctx.module_id == "test"
    assert ctx.config == {"key": "value"}
    assert ctx.workspace_root == Path("/workspace")
    assert ctx.run_root == Path("/run")
    assert ctx.prepared_state == {"dynamic": True}
    assert ctx.strict is True


def test_tool_register_context_optional_fields_default() -> None:
    ctx = ToolRegisterContext(module_id="test")
    assert ctx.config is None
    assert ctx.workspace_root is None
    assert ctx.run_root is None
    assert ctx.prepared_state is None
    assert ctx.strict is True


def test_register_module_manifest_tracks_source() -> None:
    manager = ToolRegistryManager()
    manifest = FakeManifest(module_id="test_module")
    manager.register_module_manifest(manifest, source_module="test_module")
    assert len(manager._manifests) == 1
    assert manager._manifests[0].module_id == "test_module"


def test_runtime_bootstrap_returns_all_components() -> None:
    from openminion.modules.tool import RuntimeBootstrap, build_runtime_bootstrap

    bootstrap = build_runtime_bootstrap()

    assert isinstance(bootstrap, RuntimeBootstrap)
    assert isinstance(bootstrap.registry, ToolRegistry)
    assert hasattr(bootstrap, "policy_manager")
    assert hasattr(bootstrap, "manager")
    assert hasattr(bootstrap, "config")
    assert hasattr(bootstrap, "bootstrap_records")


def test_runtime_bootstrap_with_config() -> None:
    from openminion.base.config import ToolSelectionConfig
    from openminion.modules.tool import build_runtime_bootstrap

    config = ToolSelectionConfig(
        runtime_fallback_on=["timeout", "custom_error"],
        runtime_no_fallback_on=["policy_denied", "auth"],
    )

    class FakeRuntimeConfig:
        tool_selection = config

    bootstrap = build_runtime_bootstrap(config=FakeRuntimeConfig())

    payload = bootstrap.policy_manager.metadata_payload()
    assert payload["runtime_fallback_on"] == ["timeout", "custom_error"]
    assert payload["runtime_no_fallback_on"] == ["policy_denied", "auth"]


def test_runtime_bootstrap_without_config_uses_defaults() -> None:
    from openminion.modules.tool import build_runtime_bootstrap

    bootstrap = build_runtime_bootstrap()

    payload = bootstrap.policy_manager.metadata_payload()
    assert "runtime_fallback_on" in payload
    assert "runtime_no_fallback_on" in payload


def test_runtime_bootstrap_handles_missing_optional_modules() -> None:
    from openminion.modules.tool import build_runtime_bootstrap

    bootstrap = build_runtime_bootstrap(strict=False)

    assert bootstrap.registry is not None
    assert bootstrap.policy_manager is not None
    assert isinstance(bootstrap.bootstrap_records, list)


def test_file_module_registrar_returns_manifest() -> None:
    from openminion.modules.tool.runtime.registrar import ToolRegisterContext
    from openminion.tools.file import REGISTRAR

    ctx = ToolRegisterContext(module_id="file_test")
    manifest = REGISTRAR.get_manifest(ctx)

    assert manifest.module_id == "file"
    expected_tool_ids = {
        "file.list_dir",
        "file.read",
        "file.read_range",
        "file.write",
        "file.find",
        "file.trash",
        "file.search",
        "file.edit",
    }
    model_ids = {t.model_tool_id for t in manifest.model_tools}
    binding_tool_ids = {binding.model_tool_id for binding in manifest.runtime_bindings}

    assert model_ids == expected_tool_ids
    assert binding_tool_ids == expected_tool_ids


def test_exec_module_registrar_returns_manifest() -> None:
    from openminion.modules.tool.runtime.registrar import ToolRegisterContext
    from openminion.tools.exec import REGISTRAR

    ctx = ToolRegisterContext(module_id="exec_test")
    manifest = REGISTRAR.get_manifest(ctx)

    assert manifest.module_id == "exec"
    assert len(manifest.model_tools) == 8
    assert len(manifest.runtime_bindings) == 8

    model_ids = {t.model_tool_id for t in manifest.model_tools}
    assert "exec.run" in model_ids
    assert "exec.poll" in model_ids
    assert "exec.kill" in model_ids


def test_file_and_exec_manifest_descriptions_encode_scaffolding_boundary() -> None:
    from openminion.modules.tool.runtime.registrar import ToolRegisterContext
    from openminion.tools.exec import REGISTRAR as EXEC_REGISTRAR
    from openminion.tools.file import REGISTRAR as FILE_REGISTRAR

    file_manifest = FILE_REGISTRAR.get_manifest(ToolRegisterContext(module_id="file"))
    exec_manifest = EXEC_REGISTRAR.get_manifest(ToolRegisterContext(module_id="exec"))

    file_write = next(
        item for item in file_manifest.model_tools if item.model_tool_id == "file.write"
    )
    exec_run = next(
        item for item in exec_manifest.model_tools if item.model_tool_id == "exec.run"
    )

    assert "parent directories" in file_write.description
    assert "scaffolding" in file_write.description
    assert "structured file tools" in exec_run.description
    assert "scaffolding" in exec_run.description


def test_pilot_modules_register_via_bootstrap() -> None:
    from openminion.modules.tool import build_runtime_bootstrap

    bootstrap = build_runtime_bootstrap()

    registry_tools = set(bootstrap.registry.list().keys())

    assert "file.read" in registry_tools
    assert "file.write" in registry_tools
    assert "exec.run" in registry_tools
    assert "exec.poll" in registry_tools


def test_bootstrap_registrar_module_id_matches_manifest_module_id() -> None:
    from openminion.modules.tool.bootstrap import _TOOL_BOOTSTRAP_ENTRIES

    mismatches: list[str] = []

    for entry in _TOOL_BOOTSTRAP_ENTRIES:
        if entry.kind != "tool":
            continue

        module = importlib.import_module(entry.module_name)
        registrar = getattr(module, "REGISTRAR", None)
        assert registrar is not None, f"{entry.module_name} missing REGISTRAR"

        registrar_module_id = getattr(registrar, "module_id", None)
        assert isinstance(registrar_module_id, str) and registrar_module_id.strip(), (
            f"{entry.module_name} REGISTRAR.module_id must be a non-empty string"
        )

        ctx = ToolRegisterContext(module_id=registrar_module_id, strict=False)
        manifest = registrar.get_manifest(ctx)
        if manifest is None:
            assert bool(getattr(registrar, "is_provider_only", False)) is True, (
                f"{entry.module_name} returned None manifest but is not provider-only"
            )
            continue
        manifest_module_id = getattr(manifest, "module_id", None)

        if registrar_module_id != manifest_module_id:
            mismatches.append(
                f"{entry.module_name}: REGISTRAR.module_id={registrar_module_id!r} "
                f"!= manifest.module_id={manifest_module_id!r}"
            )

    assert not mismatches, "Module ID mismatches:\n" + "\n".join(mismatches)


def test_bootstrap_registrar_protocol_and_provider_flag() -> None:
    from openminion.modules.tool.bootstrap import _TOOL_BOOTSTRAP_ENTRIES

    for entry in _TOOL_BOOTSTRAP_ENTRIES:
        if entry.kind != "tool":
            continue
        module = importlib.import_module(entry.module_name)
        registrar = getattr(module, "REGISTRAR", None)
        assert isinstance(registrar, ToolModuleRegistrar), (
            f"{entry.module_name} REGISTRAR must conform to ToolModuleRegistrar"
        )
        assert isinstance(registrar.is_provider_only, bool), (
            f"{entry.module_name} REGISTRAR.is_provider_only must be bool"
        )


def test_plugin_layout_has_required_registrar_files() -> None:
    from openminion.modules.tool.bootstrap import _TOOL_BOOTSTRAP_ENTRIES

    for entry in _TOOL_BOOTSTRAP_ENTRIES:
        if entry.kind != "tool":
            continue
        module = importlib.import_module(entry.module_name)
        pkg_path = Path(getattr(module, "__file__", "")).resolve().parent
        assert (pkg_path / "__init__.py").is_file(), (
            f"{entry.module_name} missing __init__.py"
        )
        assert (pkg_path / "registrar.py").is_file(), (
            f"{entry.module_name} missing registrar.py"
        )
        assert any((pkg_path / name).is_file() for name in ("plugin.py", "tool.py")), (
            f"{entry.module_name} must include plugin.py or tool.py"
        )


def test_plugin_packages_export_typed_registrar_annotation() -> None:
    from openminion.modules.tool.bootstrap import _TOOL_BOOTSTRAP_ENTRIES

    for entry in _TOOL_BOOTSTRAP_ENTRIES:
        if entry.kind != "tool":
            continue
        module = importlib.import_module(entry.module_name)
        annotations = getattr(module, "__annotations__", {})
        assert "REGISTRAR" in annotations, (
            f"{entry.module_name} __init__.py must type-annotate REGISTRAR"
        )


def test_runtime_bootstrap_raises_type_error_for_nonconforming_registrar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openminion.modules.tool import bootstrap as bootstrap_module

    fake_module_name = "openminion.tools._tpfr_nonconforming"
    fake_module = ModuleType(fake_module_name)
    fake_module.REGISTRAR = object()
    monkeypatch.setitem(sys.modules, fake_module_name, fake_module)
    monkeypatch.setattr(
        bootstrap_module,
        "_TOOL_BOOTSTRAP_ENTRIES",
        (
            bootstrap_module._ToolBootstrapEntry(
                kind="tool",
                module_name=fake_module_name,
                label="Fake Nonconforming",
                required=True,
            ),
        ),
    )

    with pytest.raises(TypeError, match="missing required attribute 'module_id'"):
        bootstrap_module.build_runtime_bootstrap(strict=True)


def test_runtime_bootstrap_allows_provider_only_registrar_none_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openminion.modules.tool import bootstrap as bootstrap_module

    class _ProviderOnlyNoneRegistrar:
        module_id = "provider.none"
        is_provider_only = True

        def get_manifest(self, ctx: ToolRegisterContext) -> Any | None:
            del ctx
            return None

        def register(self, registry: ToolRegistry, ctx: ToolRegisterContext) -> None:
            del registry, ctx

    fake_module_name = "openminion.tools._tpcm_provider_none_manifest"
    fake_module = ModuleType(fake_module_name)
    fake_module.REGISTRAR = _ProviderOnlyNoneRegistrar()
    monkeypatch.setitem(sys.modules, fake_module_name, fake_module)
    monkeypatch.setattr(
        bootstrap_module,
        "_TOOL_BOOTSTRAP_ENTRIES",
        (
            bootstrap_module._ToolBootstrapEntry(
                kind="tool",
                module_name=fake_module_name,
                label="Provider None",
                required=True,
            ),
        ),
    )

    records = bootstrap_module._bootstrap_default_registry(
        ToolRegistry(),
        ToolRegistryManager(),
    )
    assert len(records) == 1
    assert records[0].status == "registered"


def test_runtime_bootstrap_allows_provider_only_registrar_empty_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openminion.modules.tool import bootstrap as bootstrap_module
    from openminion.modules.tool.contracts import ToolBindingManifest

    class _ProviderOnlyEmptyRegistrar:
        module_id = "provider.empty"
        is_provider_only = True

        def get_manifest(self, ctx: ToolRegisterContext) -> Any:
            del ctx
            return ToolBindingManifest(
                module_id=self.module_id,
                model_tools=(),
                runtime_bindings=(),
            )

        def register(self, registry: ToolRegistry, ctx: ToolRegisterContext) -> None:
            del registry, ctx

    fake_module_name = "openminion.tools._tpcm_provider_empty_manifest"
    fake_module = ModuleType(fake_module_name)
    fake_module.REGISTRAR = _ProviderOnlyEmptyRegistrar()
    monkeypatch.setitem(sys.modules, fake_module_name, fake_module)
    monkeypatch.setattr(
        bootstrap_module,
        "_TOOL_BOOTSTRAP_ENTRIES",
        (
            bootstrap_module._ToolBootstrapEntry(
                kind="tool",
                module_name=fake_module_name,
                label="Provider Empty",
                required=True,
            ),
        ),
    )

    records = bootstrap_module._bootstrap_default_registry(
        ToolRegistry(),
        ToolRegistryManager(),
    )
    assert len(records) == 1
    assert records[0].status == "registered"


def test_runtime_bootstrap_rejects_non_provider_none_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openminion.modules.tool import bootstrap as bootstrap_module

    class _NonProviderNoneRegistrar:
        module_id = "core.none"
        is_provider_only = False

        def get_manifest(self, ctx: ToolRegisterContext) -> Any | None:
            del ctx
            return None

        def register(self, registry: ToolRegistry, ctx: ToolRegisterContext) -> None:
            del registry, ctx

    fake_module_name = "openminion.tools._tpcm_non_provider_none_manifest"
    fake_module = ModuleType(fake_module_name)
    fake_module.REGISTRAR = _NonProviderNoneRegistrar()
    monkeypatch.setitem(sys.modules, fake_module_name, fake_module)
    monkeypatch.setattr(
        bootstrap_module,
        "_TOOL_BOOTSTRAP_ENTRIES",
        (
            bootstrap_module._ToolBootstrapEntry(
                kind="tool",
                module_name=fake_module_name,
                label="Core None",
                required=True,
            ),
        ),
    )

    with pytest.raises(
        TypeError, match="non-provider REGISTRAR.get_manifest\\(\\) returned None"
    ):
        bootstrap_module._bootstrap_default_registry(
            ToolRegistry(),
            ToolRegistryManager(),
        )


def test_runtime_bootstrap_rejects_registrar_with_empty_module_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openminion.modules.tool import bootstrap as bootstrap_module

    class _InvalidModuleIdRegistrar:
        module_id = ""
        is_provider_only = False

        def get_manifest(self, ctx: ToolRegisterContext) -> Any | None:
            del ctx
            return None

        def register(self, registry: ToolRegistry, ctx: ToolRegisterContext) -> None:
            del registry, ctx

    fake_module_name = "openminion.tools._tpcm_invalid_module_id"
    fake_module = ModuleType(fake_module_name)
    fake_module.REGISTRAR = _InvalidModuleIdRegistrar()
    monkeypatch.setitem(sys.modules, fake_module_name, fake_module)
    monkeypatch.setattr(
        bootstrap_module,
        "_TOOL_BOOTSTRAP_ENTRIES",
        (
            bootstrap_module._ToolBootstrapEntry(
                kind="tool",
                module_name=fake_module_name,
                label="Invalid Module ID",
                required=True,
            ),
        ),
    )

    with pytest.raises(
        TypeError, match="REGISTRAR.module_id must be a non-empty string"
    ):
        bootstrap_module._bootstrap_default_registry(
            ToolRegistry(),
            ToolRegistryManager(),
        )


def test_runtime_bootstrap_rejects_registrar_with_non_bool_provider_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openminion.modules.tool import bootstrap as bootstrap_module

    class _InvalidProviderFlagRegistrar:
        module_id = "invalid.provider.flag"
        is_provider_only = "yes"

        def get_manifest(self, ctx: ToolRegisterContext) -> Any | None:
            del ctx
            return None

        def register(self, registry: ToolRegistry, ctx: ToolRegisterContext) -> None:
            del registry, ctx

    fake_module_name = "openminion.tools._tpcm_invalid_provider_flag"
    fake_module = ModuleType(fake_module_name)
    fake_module.REGISTRAR = _InvalidProviderFlagRegistrar()
    monkeypatch.setitem(sys.modules, fake_module_name, fake_module)
    monkeypatch.setattr(
        bootstrap_module,
        "_TOOL_BOOTSTRAP_ENTRIES",
        (
            bootstrap_module._ToolBootstrapEntry(
                kind="tool",
                module_name=fake_module_name,
                label="Invalid Provider Flag",
                required=True,
            ),
        ),
    )

    with pytest.raises(TypeError, match="REGISTRAR.is_provider_only must be bool"):
        bootstrap_module._bootstrap_default_registry(
            ToolRegistry(),
            ToolRegistryManager(),
        )


def test_runtime_bootstrap_rejects_registrar_with_non_callable_register(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openminion.modules.tool import bootstrap as bootstrap_module

    class _InvalidRegisterRegistrar:
        module_id = "invalid.register"
        is_provider_only = False
        register = "not-callable"

        def get_manifest(self, ctx: ToolRegisterContext) -> Any | None:
            del ctx
            return None

    fake_module_name = "openminion.tools._tpcm_invalid_register"
    fake_module = ModuleType(fake_module_name)
    fake_module.REGISTRAR = _InvalidRegisterRegistrar()
    monkeypatch.setitem(sys.modules, fake_module_name, fake_module)
    monkeypatch.setattr(
        bootstrap_module,
        "_TOOL_BOOTSTRAP_ENTRIES",
        (
            bootstrap_module._ToolBootstrapEntry(
                kind="tool",
                module_name=fake_module_name,
                label="Invalid Register",
                required=True,
            ),
        ),
    )

    with pytest.raises(TypeError, match="REGISTRAR.register must be callable"):
        bootstrap_module._bootstrap_default_registry(
            ToolRegistry(),
            ToolRegistryManager(),
        )


def test_runtime_bootstrap_rejects_registrar_with_non_callable_get_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openminion.modules.tool import bootstrap as bootstrap_module

    class _InvalidGetManifestRegistrar:
        module_id = "invalid.get_manifest"
        is_provider_only = False
        get_manifest = "not-callable"

        def register(self, registry: ToolRegistry, ctx: ToolRegisterContext) -> None:
            del registry, ctx

    fake_module_name = "openminion.tools._tpcm_invalid_get_manifest"
    fake_module = ModuleType(fake_module_name)
    fake_module.REGISTRAR = _InvalidGetManifestRegistrar()
    monkeypatch.setitem(sys.modules, fake_module_name, fake_module)
    monkeypatch.setattr(
        bootstrap_module,
        "_TOOL_BOOTSTRAP_ENTRIES",
        (
            bootstrap_module._ToolBootstrapEntry(
                kind="tool",
                module_name=fake_module_name,
                label="Invalid Get Manifest",
                required=True,
            ),
        ),
    )

    with pytest.raises(TypeError, match="REGISTRAR.get_manifest must be callable"):
        bootstrap_module._bootstrap_default_registry(
            ToolRegistry(),
            ToolRegistryManager(),
        )


def test_runtime_bootstrap_rejects_non_provider_non_manifest_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openminion.modules.tool import bootstrap as bootstrap_module

    class _NonProviderWrongManifestRegistrar:
        module_id = "core.wrong_manifest"
        is_provider_only = False

        def get_manifest(self, ctx: ToolRegisterContext) -> Any:
            del ctx
            return {"module_id": self.module_id}

        def register(self, registry: ToolRegistry, ctx: ToolRegisterContext) -> None:
            del registry, ctx

    fake_module_name = "openminion.tools._tpcm_non_provider_wrong_manifest"
    fake_module = ModuleType(fake_module_name)
    fake_module.REGISTRAR = _NonProviderWrongManifestRegistrar()
    monkeypatch.setitem(sys.modules, fake_module_name, fake_module)
    monkeypatch.setattr(
        bootstrap_module,
        "_TOOL_BOOTSTRAP_ENTRIES",
        (
            bootstrap_module._ToolBootstrapEntry(
                kind="tool",
                module_name=fake_module_name,
                label="Core Wrong Manifest",
                required=True,
            ),
        ),
    )

    with pytest.raises(
        TypeError,
        match="non-provider REGISTRAR.get_manifest\\(\\) must return ToolBindingManifest",
    ):
        bootstrap_module._bootstrap_default_registry(
            ToolRegistry(),
            ToolRegistryManager(),
        )


def test_runtime_bootstrap_rejects_provider_only_non_empty_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openminion.modules.tool import bootstrap as bootstrap_module
    from openminion.modules.tool.contracts import (
        ModelToolDef,
        RuntimeBindingDef,
        ToolBindingManifest,
    )

    class _ProviderOnlyNonEmptyRegistrar:
        module_id = "provider.non_empty"
        is_provider_only = True

        def get_manifest(self, ctx: ToolRegisterContext) -> Any:
            del ctx
            return ToolBindingManifest(
                module_id=self.module_id,
                model_tools=(
                    ModelToolDef(
                        model_tool_id="weather",
                        description="weather",
                        parameters={},
                    ),
                ),
                runtime_bindings=(
                    RuntimeBindingDef(
                        runtime_binding_id="runtime.weather.current",
                        model_tool_id="weather",
                        runtime_candidates=("weather",),
                    ),
                ),
            )

        def register(self, registry: ToolRegistry, ctx: ToolRegisterContext) -> None:
            del registry, ctx

    fake_module_name = "openminion.tools._tpcm_provider_non_empty_manifest"
    fake_module = ModuleType(fake_module_name)
    fake_module.REGISTRAR = _ProviderOnlyNonEmptyRegistrar()
    monkeypatch.setitem(sys.modules, fake_module_name, fake_module)
    monkeypatch.setattr(
        bootstrap_module,
        "_TOOL_BOOTSTRAP_ENTRIES",
        (
            bootstrap_module._ToolBootstrapEntry(
                kind="tool",
                module_name=fake_module_name,
                label="Provider Non Empty",
                required=True,
            ),
        ),
    )

    with pytest.raises(
        TypeError,
        match="provider-only REGISTRAR.get_manifest\\(\\) must return None or empty ToolBindingManifest",
    ):
        bootstrap_module._bootstrap_default_registry(
            ToolRegistry(),
            ToolRegistryManager(),
        )


def test_runtime_bootstrap_raises_type_error_for_missing_registrar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openminion.modules.tool import bootstrap as bootstrap_module

    fake_module_name = "openminion.tools._tpcm_missing_registrar"
    fake_module = ModuleType(fake_module_name)
    monkeypatch.setitem(sys.modules, fake_module_name, fake_module)
    monkeypatch.setattr(
        bootstrap_module,
        "_TOOL_BOOTSTRAP_ENTRIES",
        (
            bootstrap_module._ToolBootstrapEntry(
                kind="tool",
                module_name=fake_module_name,
                label="Missing Registrar",
                required=True,
            ),
        ),
    )

    with pytest.raises(
        TypeError, match="missing REGISTRAR implementing ToolModuleRegistrar"
    ):
        bootstrap_module._bootstrap_default_registry(
            ToolRegistry(),
            ToolRegistryManager(),
        )


def test_provider_only_registrars_stay_on_tool_bootstrap_path() -> None:
    from openminion.modules.tool import bootstrap as bootstrap_module

    provider_only_entries: list[str] = []
    for entry in bootstrap_module._TOOL_BOOTSTRAP_ENTRIES:
        if entry.kind != "tool":
            continue
        module = importlib.import_module(entry.module_name)
        registrar, _ = bootstrap_module._resolve_module_registrar(
            entry.module_name,
            module,
        )
        if registrar is None:
            continue
        typed = bootstrap_module._require_registrar_protocol(
            module_name=entry.module_name,
            label=entry.label,
            registrar=registrar,
        )
        if typed.is_provider_only:
            provider_only_entries.append(entry.module_name)
            assert entry.kind == "tool"

    assert provider_only_entries, (
        "Expected at least one provider-only registrar on tool path"
    )
