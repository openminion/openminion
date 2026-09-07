import json
import logging
import os
import tempfile
import unittest
from unittest import mock
from contextlib import contextmanager
from pathlib import Path
from typing import Optional
from tests._csc_fixtures import _csc_install_default_agent


from openminion.api.runtime import APIRuntime
from openminion.base.config import OpenMinionConfig, save_config
from openminion.services.runtime.plugins import Plugin, PluginContext
from openminion.services.runtime.plugins import PluginRegistry
from openminion.services.runtime.errors import PluginActivationError
from openminion.services.runtime.lifecycle import LifecycleService
from openminion.modules.policy import SecurityPolicyEngine
from openminion.modules.llm.providers.base import ProviderError
from openminion.modules.tool import build_default_tool_registry
from openminion.modules.tool.bootstrap import build_runtime_bootstrap
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.runtime.dispatch import get_registry, get_registry_manager


class _FailingExtensionPlugin(Plugin):
    name = "failing-extension"

    def register_tools(self, registry, context: PluginContext) -> None:
        del registry, context
        raise RuntimeError("tool-registration-failure")

    def register_providers(self, registry, context: PluginContext) -> None:
        del registry, context
        raise RuntimeError("provider-registration-failure")


class PluginExtensionTests(unittest.TestCase):
    def test_catalog_activation_failure_emits_safe_event(self) -> None:
        config = OpenMinionConfig()
        events: list[tuple[str, dict[str, object]]] = []
        error = PluginActivationError(
            plugin_id="broken.plugin",
            stage="checksum",
            reason_code="checksum_mismatch",
        )

        with mock.patch(
            "openminion.services.runtime.lifecycle.ExtensionCatalog.from_config",
            side_effect=error,
        ):
            with self.assertRaises(PluginActivationError):
                LifecycleService.from_config(
                    config,
                    event_sink=lambda event, payload: events.append((event, payload)),
                )

        self.assertEqual(
            events,
            [
                (
                    "ext.activation.error",
                    {
                        "plugin_id": "broken.plugin",
                        "stage": "checksum",
                        "reason_code": "checksum_mismatch",
                    },
                )
            ],
        )

    def test_policy_manager_failure_preserves_published_runtime(self) -> None:
        published_registry = build_default_tool_registry()
        published_manager = get_registry_manager()

        with mock.patch(
            "openminion.modules.tool.bootstrap.runtime_build.ToolBindingPolicyManager",
            side_effect=ValueError("invalid policy"),
        ):
            with self.assertRaises(ValueError, msg="invalid policy"):
                build_runtime_bootstrap()

        self.assertIs(get_registry(), published_registry)
        self.assertIs(get_registry_manager(), published_manager)

    def test_legacy_plugin_tool_registration_is_rejected(self) -> None:
        registry = PluginRegistry([_FailingExtensionPlugin()])
        context = _plugin_context()

        tools = build_default_tool_registry()
        with self.assertRaises(PluginActivationError, msg="module-level REGISTRAR"):
            registry.register_tool_extensions(tools, context)

    def test_loaded_plugin_can_register_tool_extensions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            plugin_root = tmp_path / "plugins"
            _write_extension_plugin(plugin_root)

            config_path = tmp_path / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.runtime.log_level = "ERROR"
            config.storage.path = str(tmp_path / "state" / "runtime.db")
            _csc_install_default_agent(config, provider="echo")
            config.enabled_plugins = ["validate", "example.extension"]
            save_config(config, str(config_path))

            with _plugin_paths_env([plugin_root]):
                app = APIRuntime.from_config_path(str(config_path))
            try:
                # Provider is now llmctl bridge (echo)
                self.assertEqual(app.provider.name, "echo")
                tool_names = [spec.name for spec in app.tools.provider_specs()]
                self.assertIn("weather", tool_names)
                self.assertIn("search.dispatch", tool_names)
                self.assertTrue(
                    any(
                        name in tool_names
                        for name in ("web.fetch", "fetch.get", "fetch.head")
                    )
                )
                self.assertIn("utility.utc_now", tool_names)
                self.assertIn("utility.calculate_expression", tool_names)
                self.assertIn("utility.text_stats", tool_names)
                self.assertIn("time.now", tool_names)
                self.assertIn("time.convert", tool_names)
                self.assertIn("plugin.example.extension.echo", tool_names)
                model_names = [spec.name for spec in app.tools.model_provider_specs()]
                self.assertIn("plugin.example.extension.echo", model_names)
                self.assertEqual(
                    app.tools.model_to_runtime_tool_map()[
                        "plugin.example.extension.echo"
                    ],
                    "plugin.example.extension.echo",
                )
            finally:
                app.close()

    def test_unknown_provider_raises_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            plugin_root = tmp_path / "plugins"
            _write_extension_plugin(plugin_root)

            config_path = tmp_path / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.runtime.log_level = "ERROR"
            config.storage.path = str(tmp_path / "state" / "runtime.db")
            _csc_install_default_agent(config, provider="plugin_echo")
            config.enabled_plugins = ["validate", "example.extension"]
            save_config(config, str(config_path))

            with _plugin_paths_env([plugin_root]):
                with self.assertRaises(ProviderError):
                    APIRuntime.from_config_path(str(config_path))

    def test_plugin_activation_blocked_for_critical_capability(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            plugin_root = tmp_path / "plugins"
            _write_extension_plugin(
                plugin_root,
                requested_capabilities=["tool.exec.shell"],
            )

            config_path = tmp_path / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.runtime.log_level = "ERROR"
            config.storage.path = str(tmp_path / "state" / "runtime.db")
            config.enabled_plugins = ["example.extension"]
            save_config(config, str(config_path))

            with _plugin_paths_env([plugin_root]):
                with self.assertRaises(PluginActivationError) as context:
                    APIRuntime.from_config_path(str(config_path))
            self.assertEqual(context.exception.plugin_id, "example.extension")
            self.assertEqual(context.exception.stage, "policy")
            self.assertTrue(context.exception.reason_code)
            self.assertEqual(str(context.exception), "Plugin activation failed.")

    def test_plugin_activation_blocked_for_verified_unverified_local_provenance(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            plugin_root = tmp_path / "plugins"
            _write_extension_plugin(
                plugin_root,
                trust_tier="verified",
                provenance={
                    "source": "local-path",
                    "uri": "",
                    "publisher": "",
                    "checksum": "",
                    "verified": False,
                },
            )

            config_path = tmp_path / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.runtime.log_level = "ERROR"
            config.storage.path = str(tmp_path / "state" / "runtime.db")
            config.enabled_plugins = ["example.extension"]
            save_config(config, str(config_path))

            with _plugin_paths_env([plugin_root]):
                with self.assertRaises(PluginActivationError) as context:
                    APIRuntime.from_config_path(str(config_path))
            self.assertEqual(context.exception.plugin_id, "example.extension")
            self.assertEqual(context.exception.stage, "policy")
            self.assertTrue(context.exception.reason_code)
            self.assertEqual(str(context.exception), "Plugin activation failed.")

    def test_plugin_provider_registration_extension_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            plugin_root = tmp_path / "plugins"
            _write_extension_plugin(
                plugin_root,
                register_provider_extension=True,
            )

            config_path = tmp_path / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.runtime.log_level = "ERROR"
            config.storage.path = str(tmp_path / "state" / "runtime.db")
            config.enabled_plugins = ["example.extension"]
            save_config(config, str(config_path))

            with _plugin_paths_env([plugin_root]):
                with self.assertRaises(RuntimeError) as context:
                    APIRuntime.from_config_path(str(config_path))
            self.assertIn(
                "Legacy provider extensions are no longer supported",
                str(context.exception),
            )

    def test_unbound_legacy_tool_plugin_is_activation_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            plugin_root = tmp_path / "plugins"
            _write_extension_plugin(plugin_root, include_registrar=False)
            config_path = tmp_path / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config, provider="echo")
            config.storage.path = str(tmp_path / "state" / "runtime.db")
            config.enabled_plugins = ["example.extension"]
            save_config(config, str(config_path))

            with _plugin_paths_env([plugin_root]):
                with self.assertRaises(PluginActivationError) as context:
                    APIRuntime.from_config_path(str(config_path))
            self.assertEqual(context.exception.reason_code, "unbound_tool_contribution")
            self.assertEqual(str(context.exception), "Plugin activation failed.")

    def test_plugin_tool_namespace_failure_does_not_replace_published_registry(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            plugin_root = tmp_path / "plugins"
            _write_extension_plugin(plugin_root, runtime_tool_name="time.now")
            config_path = tmp_path / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config, provider="echo")
            config.storage.path = str(tmp_path / "state" / "runtime.db")
            config.enabled_plugins = ["example.extension"]
            save_config(config, str(config_path))
            published = build_default_tool_registry()

            with _plugin_paths_env([plugin_root]):
                with self.assertRaises(PluginActivationError) as context:
                    APIRuntime.from_config_path(str(config_path))
            self.assertEqual(context.exception.plugin_id, "example.extension")
            self.assertEqual(context.exception.stage, "tool_registration")
            self.assertEqual(context.exception.reason_code, "namespace_invalid")
            self.assertEqual(str(context.exception), "Plugin activation failed.")
            self.assertIs(get_registry(), published)

    def test_plugin_tool_collision_emits_safe_activation_facts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            plugin_root = tmp_path / "plugins"
            _write_extension_plugin(plugin_root, register_twice=True)
            config_path = tmp_path / "config.json"
            config = OpenMinionConfig()
            _csc_install_default_agent(config, provider="echo")
            config.enabled_plugins = ["example.extension"]
            events: list[tuple[str, dict[str, object]]] = []
            lifecycle = LifecycleService.from_config(
                config,
                config_path=str(config_path),
                home_root=tmp_path,
                data_root=tmp_path / "data",
                event_sink=lambda event, payload: events.append((event, payload)),
            )

            with _plugin_paths_env([plugin_root]):
                with self.assertRaises(PluginActivationError) as context:
                    lifecycle.build(security_policy=SecurityPolicyEngine())

            self.assertEqual(context.exception.reason_code, "runtime_collision")
            self.assertIn(
                (
                    "ext.activation.error",
                    {
                        "plugin_id": "example.extension",
                        "stage": "tool_registration",
                        "reason_code": "runtime_collision",
                    },
                ),
                events,
            )

    def test_plugin_registration_failure_preserves_cause(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            plugin_root = tmp_path / "plugins"
            _write_extension_plugin(plugin_root, registration_error=True)
            config = OpenMinionConfig()
            _csc_install_default_agent(config, provider="echo")
            config.enabled_plugins = ["example.extension"]
            lifecycle = LifecycleService.from_config(
                config,
                config_path=str(tmp_path / "config.json"),
                home_root=tmp_path,
                data_root=tmp_path / "data",
            )

            with _plugin_paths_env([plugin_root]):
                with self.assertRaises(PluginActivationError) as context:
                    lifecycle.build(security_policy=SecurityPolicyEngine())

            self.assertEqual(context.exception.reason_code, "registration_failed")
            self.assertIsInstance(context.exception.__cause__, ToolRuntimeError)
            self.assertIsInstance(context.exception.__cause__.__cause__, ValueError)


def _write_extension_plugin(
    root: Path,
    *,
    requested_capabilities: Optional[list[str]] = None,
    trust_tier: str = "local-dev",
    provenance: Optional[dict[str, object]] = None,
    register_provider_extension: bool = False,
    include_registrar: bool = True,
    runtime_tool_name: str = "plugin.example.extension.echo",
    register_twice: bool = False,
    registration_error: bool = False,
) -> None:
    capabilities = list(requested_capabilities or [])
    provenance_payload = dict(
        provenance
        or {
            "source": "local-path",
            "uri": "",
            "publisher": "",
            "checksum": "",
            "verified": False,
        }
    )
    root.mkdir(parents=True, exist_ok=True)
    provider_import_block = ""
    provider_class_block = ""
    provider_register_block = ""
    if register_provider_extension:
        provider_import_block = "from openminion.modules.llm.providers.base import LLMProvider, ProviderRequest, ProviderResponse\n"
        provider_class_block = (
            "class PluginEchoProvider(LLMProvider):\n"
            "    name = 'plugin-echo'\n\n"
            "    async def generate(self, request: ProviderRequest) -> ProviderResponse:\n"
            "        return ProviderResponse(text='plugin-provider:' + request.user_message, model='plugin-model')\n\n"
        )
        provider_register_block = (
            "    def register_providers(self, registry, context):\n"
            "        del context\n"
            "        registry.register('plugin_echo', lambda config, logger: PluginEchoProvider())\n\n"
        )

    registrar_block = (
        "class PluginEchoRegistrar:\n"
        "    module_id = 'example.extension'\n"
        "    is_provider_only = False\n\n"
        "    def get_manifest(self, ctx):\n"
        "        del ctx\n"
        "        return ToolBindingManifest(\n"
        "            module_id=self.module_id,\n"
        "            model_tools=(ModelToolDef(\n"
        "                model_tool_id='plugin.example.extension.echo',\n"
        "                description='Echoes plugin text.',\n"
        "                parameters={},\n"
        "            ),),\n"
        "            runtime_bindings=(RuntimeBindingDef(\n"
        "                runtime_binding_id='runtime.plugin.example.extension.echo',\n"
        "                model_tool_id='plugin.example.extension.echo',\n"
        f"                runtime_candidates=({runtime_tool_name!r},),\n"
        "            ),),\n"
        "        )\n\n"
        "    def register(self, registry, ctx):\n"
        "        del ctx\n"
        + (
            "        raise ValueError('fixture exploded')\n"
            if registration_error
            else "        registry.register(PluginEchoTool())\n"
        )
        + ("        registry.register(PluginEchoTool())\n" if register_twice else "")
        + "\n"
        "REGISTRAR = PluginEchoRegistrar()\n\n"
        if include_registrar
        else ""
    )
    legacy_register_block = (
        "    def register_tools(self, registry, context):\n"
        "        del context\n"
        "        registry.register(PluginEchoTool())\n"
        if not include_registrar
        else ""
    )
    (root / "extension.py").write_text(
        "from openminion.services.runtime.plugins import Plugin\n"
        + provider_import_block
        + "from openminion.modules.tool.base import Tool, ToolExecutionContext, ToolExecutionResult\n\n"
        + "from openminion.modules.tool.contracts import ModelToolDef, RuntimeBindingDef, ToolBindingManifest\n\n"
        + provider_class_block
        + "class PluginEchoTool(Tool):\n"
        f"    name = {runtime_tool_name!r}\n"
        "    description = 'Echoes text from arguments.'\n"
        "    parameters = {\n"
        "        'type': 'object',\n"
        "        'properties': {'text': {'type': 'string'}},\n"
        "        'required': ['text'],\n"
        "    }\n\n"
        "    def execute(self, arguments, context: ToolExecutionContext) -> ToolExecutionResult:\n"
        "        del context\n"
        "        text = str(arguments.get('text', ''))\n"
        "        return ToolExecutionResult(\n"
        "            tool_name=self.name,\n"
        "            ok=True,\n"
        "            content=text,\n"
        "            verified=True,\n"
        "            data={'text': text},\n"
        "            source='plugin',\n"
        "        )\n\n" + registrar_block + "class PluginExtension(Plugin):\n"
        "    name = 'plugin-extension'\n\n"
        + provider_register_block
        + legacy_register_block,
        encoding="utf-8",
    )
    (root / "extension.manifest.json").write_text(
        json.dumps(
            {
                "id": "example.extension",
                "name": "example-extension",
                "version": "0.0.1",
                "description": "test extension plugin",
                "config_schema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                "trust_tier": trust_tier,
                "provenance": provenance_payload,
                "requested_capabilities": capabilities,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


@contextmanager
def _plugin_paths_env(paths: list[Path]):
    previous = os.environ.get("OPENMINION_PLUGIN_PATHS")
    os.environ["OPENMINION_PLUGIN_PATHS"] = os.pathsep.join(str(path) for path in paths)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("OPENMINION_PLUGIN_PATHS", None)
        else:
            os.environ["OPENMINION_PLUGIN_PATHS"] = previous


def _plugin_context() -> PluginContext:
    logger = logging.getLogger("openminion.tests.plugin-extensions")
    logger.handlers = [logging.NullHandler()]
    logger.propagate = False
    return PluginContext(config=OpenMinionConfig(), logger=logger)
