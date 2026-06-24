import json
import logging
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Optional
from tests._csc_fixtures import _csc_install_default_agent


from openminion.api.runtime import APIRuntime
from openminion.base.config import OpenMinionConfig, save_config
from openminion.services.runtime.plugins import Plugin, PluginContext
from openminion.services.runtime.plugins import PluginRegistry
from openminion.modules.llm.providers.base import ProviderError
from openminion.modules.tool import build_default_tool_registry


class _FailingExtensionPlugin(Plugin):
    name = "failing-extension"

    def register_tools(self, registry, context: PluginContext) -> None:
        del registry, context
        raise RuntimeError("tool-registration-failure")

    def register_providers(self, registry, context: PluginContext) -> None:
        del registry, context
        raise RuntimeError("provider-registration-failure")


class PluginExtensionTests(unittest.TestCase):
    def test_plugin_tool_registration_is_failure_tolerant(self) -> None:
        registry = PluginRegistry([_FailingExtensionPlugin()])
        context = _plugin_context()

        tools = build_default_tool_registry()
        registry.register_tool_extensions(tools, context)
        tool_names = [item.name for item in tools.provider_specs()]
        self.assertIn("weather", tool_names)
        self.assertIn("search.dispatch", tool_names)
        self.assertTrue(
            any(name in tool_names for name in ("web.fetch", "fetch.get", "fetch.head"))
        )
        self.assertIn("utility.utc_now", tool_names)
        self.assertIn("utility.calculate_expression", tool_names)
        self.assertIn("utility.text_stats", tool_names)
        self.assertIn("time.now", tool_names)
        self.assertIn("time.convert", tool_names)

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
                self.assertIn("plugin_echo_tool", tool_names)
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
                with self.assertRaises(RuntimeError) as context:
                    APIRuntime.from_config_path(str(config_path))
            self.assertIn(
                "security policy blocked plugin activation", str(context.exception)
            )

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
                with self.assertRaises(RuntimeError) as context:
                    APIRuntime.from_config_path(str(config_path))
            self.assertIn(
                "plugin trust policy blocked activation", str(context.exception)
            )

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


def _write_extension_plugin(
    root: Path,
    *,
    requested_capabilities: Optional[list[str]] = None,
    trust_tier: str = "local-dev",
    provenance: Optional[dict[str, object]] = None,
    register_provider_extension: bool = False,
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

    (root / "extension.py").write_text(
        "from openminion.services.runtime.plugins import Plugin\n"
        + provider_import_block
        + "from openminion.modules.tool.base import Tool, ToolExecutionContext, ToolExecutionResult\n\n"
        + provider_class_block
        + "class PluginEchoTool(Tool):\n"
        "    name = 'plugin_echo_tool'\n"
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
        "        )\n\n"
        "class PluginExtension(Plugin):\n"
        "    name = 'plugin-extension'\n\n"
        + provider_register_block
        + "    def register_tools(self, registry, context):\n"
        "        del context\n"
        "        registry.register(PluginEchoTool())\n",
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
