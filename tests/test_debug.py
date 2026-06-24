from __future__ import annotations

import io
import json
import sys
import tempfile
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from openminion.cli.commands.debug import run_debug
from openminion.services.diagnostics.debug import (
    DebugRegistry,
    DebugProvider,
    DebugStatus,
    ModuleDebugPayload,
    WiringSource,
    get_debug_registry,
    is_debug_surface_enabled,
    load_debug_providers,
    set_debug_registry,
)


class TestDebugPayloadSchema(unittest.TestCase):
    def test_module_debug_payload_to_dict(self):
        payload = ModuleDebugPayload(
            module="test-module",
            status=DebugStatus.OK,
            mode="runtime",
            wiring_source=WiringSource.REAL,
            fallback=None,
            last_error=None,
            last_success_at=None,
            evidence_refs={"key": "value"},
            dependency_failures=[],
            details={"version": "1.0"},
        )
        result = payload.to_dict()
        self.assertEqual(result["module"], "test-module")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["mode"], "runtime")
        self.assertEqual(result["wiring_source"], "real")
        self.assertEqual(result["details"]["version"], "1.0")

    def test_debug_status_enum(self):
        self.assertEqual(DebugStatus.OK.value, "ok")
        self.assertEqual(DebugStatus.WARN.value, "warn")
        self.assertEqual(DebugStatus.FAIL.value, "fail")
        self.assertEqual(DebugStatus.UNKNOWN.value, "unknown")

    def test_wiring_source_enum(self):
        self.assertEqual(WiringSource.REAL.value, "real")
        self.assertEqual(WiringSource.STUB.value, "stub")
        self.assertEqual(WiringSource.FALLBACK.value, "fallback")
        self.assertEqual(WiringSource.DISABLED.value, "disabled")
        self.assertEqual(WiringSource.UNKNOWN.value, "unknown")


class TestDebugProvider(unittest.TestCase):
    def test_debug_provider_get_debug(self):
        def probe_fn():
            return ModuleDebugPayload(
                module="test",
                status=DebugStatus.OK,
                mode="test",
                wiring_source=WiringSource.REAL,
            )

        provider = DebugProvider(
            module_name="test",
            probe_fn=probe_fn,
            wiring_check_fn=None,
        )
        result = provider.get_debug()
        self.assertEqual(result.module, "test")
        self.assertEqual(result.status, DebugStatus.OK)

    def test_debug_provider_get_wiring(self):
        def wiring_fn():
            return WiringSource.REAL

        provider = DebugProvider(
            module_name="test",
            probe_fn=lambda: None,
            wiring_check_fn=wiring_fn,
        )
        self.assertEqual(provider.get_wiring(), WiringSource.REAL)

    def test_debug_provider_get_wiring_default(self):
        provider = DebugProvider(
            module_name="test",
            probe_fn=lambda: None,
            wiring_check_fn=None,
        )
        self.assertEqual(provider.get_wiring(), WiringSource.UNKNOWN)


class TestDebugRegistry(unittest.TestCase):
    def setUp(self):
        self.registry = DebugRegistry()

    def test_register_provider(self):
        provider = DebugProvider(
            module_name="test-module",
            probe_fn=lambda: ModuleDebugPayload(
                module="test-module",
                status=DebugStatus.OK,
                mode="test",
                wiring_source=WiringSource.REAL,
            ),
            wiring_check_fn=None,
        )
        self.registry.register(provider)
        self.assertIn("test-module", self.registry.list_modules())

    def test_unregister_provider(self):
        provider = DebugProvider(
            module_name="test-module",
            probe_fn=lambda: ModuleDebugPayload(
                module="test-module",
                status=DebugStatus.OK,
                mode="test",
                wiring_source=WiringSource.REAL,
            ),
            wiring_check_fn=None,
        )
        self.registry.register(provider)
        self.registry.unregister("test-module")
        self.assertNotIn("test-module", self.registry.list_modules())

    def test_get_module(self):
        provider = DebugProvider(
            module_name="test-module",
            probe_fn=lambda: ModuleDebugPayload(
                module="test-module",
                status=DebugStatus.OK,
                mode="test",
                wiring_source=WiringSource.REAL,
            ),
            wiring_check_fn=None,
        )
        self.registry.register(provider)
        retrieved = self.registry.get_module("test-module")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.module_name, "test-module")

    def test_get_module_not_found(self):
        retrieved = self.registry.get_module("nonexistent")
        self.assertIsNone(retrieved)

    def test_get_all_debug(self):
        def make_probe(module_name, status):
            def probe():
                return ModuleDebugPayload(
                    module=module_name,
                    status=status,
                    mode="test",
                    wiring_source=WiringSource.REAL,
                )

            return probe

        self.registry.register(
            DebugProvider("mod1", make_probe("mod1", DebugStatus.OK), None)
        )
        self.registry.register(
            DebugProvider("mod2", make_probe("mod2", DebugStatus.FAIL), None)
        )

        results = self.registry.get_all_debug()
        self.assertEqual(len(results), 2)
        modules = [r.module for r in results]
        self.assertIn("mod1", modules)
        self.assertIn("mod2", modules)

    def test_get_all_debug_propagates_errors(self):
        def failing_probe():
            raise RuntimeError("probe failed")

        self.registry.register(DebugProvider("failing", failing_probe, None))
        results = self.registry.get_all_debug()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, DebugStatus.FAIL)
        self.assertIn("probe failed", results[0].last_error)

    def test_debug_provider_loads_execution_boundary_policy(self):
        registry = DebugRegistry()
        set_debug_registry(registry)
        load_debug_providers()
        self.assertIn("execution.boundary.policy", registry.list_modules())


class TestGlobalRegistry(unittest.TestCase):
    def test_get_debug_registry_singleton(self):
        reg1 = get_debug_registry()
        reg2 = get_debug_registry()
        self.assertIs(reg1, reg2)


class TestDebugSurfaceConfig(unittest.TestCase):
    def setUp(self) -> None:
        current = sys.modules.get("openminion.cli.commands.debug")
        if current is not None:
            globals()["debug_command"] = current
            globals()["run_debug"] = current.run_debug


class TestToolDebugProviders(unittest.TestCase):
    def test_weather_debug_provider_exposes_source_details(self):
        from openminion.cli.commands.debug import OpenMinionWeatherDebugProvider

        payload = OpenMinionWeatherDebugProvider()._probe()
        self.assertEqual(payload.module, "openminion-tool-weather-openmeteo")
        self.assertIn(
            payload.status,
            [DebugStatus.OK, DebugStatus.WARN, DebugStatus.FAIL],
        )
        self.assertIn("weather_source", payload.details)
        self.assertIn("canonical_registered", payload.details)

    def test_tavily_debug_provider_exposes_source_details(self):
        from openminion.cli.commands.debug import OpenMinionTavilyDebugProvider

        payload = OpenMinionTavilyDebugProvider()._probe()
        self.assertEqual(payload.module, "openminion-tool-search-tavily")
        self.assertIn(
            payload.status,
            [DebugStatus.OK, DebugStatus.WARN, DebugStatus.FAIL],
        )
        self.assertIn("tavily_source", payload.details)
        self.assertIn("canonical_registered", payload.details)

    def test_reactions_debug_provider_exposes_plugin_state(self):
        from openminion.cli.commands.debug import OpenMinionReactionsDebugProvider

        payload = OpenMinionReactionsDebugProvider()._probe()
        self.assertEqual(payload.module, "openminion-tool-reactions")
        self.assertIn(
            payload.status,
            [DebugStatus.OK, DebugStatus.WARN, DebugStatus.FAIL],
        )
        self.assertIn("plugin_installed", payload.details)

    def test_debug_surface_defaults_enabled(self):
        config = SimpleNamespace(runtime=SimpleNamespace())
        self.assertTrue(is_debug_surface_enabled(config, surface="cli"))
        self.assertTrue(is_debug_surface_enabled(config, surface="api"))
        self.assertTrue(is_debug_surface_enabled(config, surface="chat"))

    def test_global_debug_flag_disables_all_surfaces(self):
        config = {
            "runtime": {
                "debug_enabled": False,
                "debug_cli_enabled": True,
                "debug_api_enabled": True,
                "debug_chat_enabled": True,
            }
        }
        self.assertFalse(is_debug_surface_enabled(config, surface="cli"))
        self.assertFalse(is_debug_surface_enabled(config, surface="api"))
        self.assertFalse(is_debug_surface_enabled(config, surface="chat"))

    def test_surface_specific_flag_disables_one_surface(self):
        config = {
            "runtime": {
                "debug_enabled": True,
                "debug_cli_enabled": False,
                "debug_api_enabled": True,
                "debug_chat_enabled": True,
            }
        }
        self.assertFalse(is_debug_surface_enabled(config, surface="cli"))
        self.assertTrue(is_debug_surface_enabled(config, surface="api"))
        self.assertTrue(is_debug_surface_enabled(config, surface="chat"))

    def test_run_debug_returns_error_when_cli_debug_disabled(self):
        args = Namespace(config="test-configs.json", debug_command="modules", json=True)
        disabled_config = SimpleNamespace(
            runtime=SimpleNamespace(debug_enabled=False, debug_cli_enabled=True)
        )
        with patch(
            "openminion.cli.commands.debug.load_config", return_value=disabled_config
        ):
            self.assertEqual(run_debug(args), 1)


class TestDebugE2EParity(unittest.TestCase):
    def test_timeline_detail_includes_mode_state_and_label(self):
        from openminion.cli.commands.debug.cli import _extract_details

        details = _extract_details(
            "brain.mode_status",
            {
                "mode": "plan",
                "mode_state": "execute_step",
                "mode_label": "Running step 2/3: search",
                "status": "executing",
            },
        )

        self.assertIn("status=executing", details)
        self.assertIn("mode=plan", details)
        self.assertIn("mode_state=execute_step", details)
        self.assertIn("Running step 2/3: search", details)

    def test_inprocess_daemon_parity_same_module_status(self):
        from openminion.cli.commands.debug import OpenMinionDebugProvider

        provider = OpenMinionDebugProvider()
        payload = provider._probe()

        self.assertIn("module", payload.to_dict())
        self.assertIn("status", payload.to_dict())
        self.assertIn("wiring_source", payload.to_dict())

        self.assertIn(
            payload.status,
            [DebugStatus.OK, DebugStatus.WARN, DebugStatus.FAIL, DebugStatus.UNKNOWN],
        )

    def test_inprocess_daemon_parity_dependency_check(self):
        from openminion.cli.commands.debug import OpenMinionDebugProvider

        provider = OpenMinionDebugProvider()

        deps = provider._check_dependencies()
        self.assertIsInstance(deps, list)

        for dep in deps:
            self.assertIn("module", dep)
            self.assertIn("type", dep)
            self.assertIn("error", dep)
            self.assertIn("impact", dep)

    def test_inprocess_daemon_parity_event_emission(self):
        from openminion.cli.commands.debug import OpenMinionDebugProvider

        provider = OpenMinionDebugProvider()

        try:
            provider._emit_debug_events(
                [
                    {
                        "module": "test-module",
                        "type": "import_error",
                        "error": "test",
                        "impact": "unavailable",
                    }
                ]
            )
        except Exception as exc:
            self.fail(f"Event emission should not raise: {exc}")

    def test_molr_03_legacy_blocked_field_present_in_debug_payload(self):
        from openminion.cli.commands.debug import OpenMinionDebugProvider

        provider = OpenMinionDebugProvider()
        payload = provider._probe()
        payload_dict = payload.to_dict()
        details = payload_dict.get("details", {})

        self.assertIn(
            "legacy_blocked",
            details,
            "MOLR-03: debug payload must expose `legacy_blocked` field",
        )
        value = details["legacy_blocked"]
        self.assertTrue(
            value is False or (isinstance(value, str) and value),
            f"MOLR-03: legacy_blocked must be False or non-empty string, got {value!r}",
        )
        if payload.status == DebugStatus.OK:
            self.assertEqual(
                value,
                False,
                "MOLR-03: clean OK status should report legacy_blocked=False",
            )

    def test_debug_trace_hides_thinking_by_default(self):
        from openminion.cli.commands.debug.cli import _debug_trace

        with tempfile.TemporaryDirectory() as tmpdir:
            trace_path = Path(tmpdir) / "step01-call01-structured.json"
            trace_path.write_text(
                json.dumps(
                    {
                        "provider": "openai",
                        "model": "o3",
                        "response": {
                            "finish_reason": "stop",
                            "output_text": "done",
                            "tool_calls": [],
                            "thinking_blocks": [
                                {"type": "thinking", "content": "inspect tool output"}
                            ],
                        },
                    }
                ),
                encoding="utf-8",
            )
            args = Namespace(path=str(trace_path), include_thinking=False, json=False)
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                self.assertEqual(_debug_trace(args), 0)
            output = stdout.getvalue()
            self.assertIn("Provider: openai", output)
            self.assertNotIn("inspect tool output", output)

    def test_debug_trace_renders_thinking_when_enabled(self):
        from openminion.cli.commands.debug.cli import _debug_trace

        with tempfile.TemporaryDirectory() as tmpdir:
            trace_path = Path(tmpdir) / "step01-call01-structured.json"
            trace_path.write_text(
                json.dumps(
                    {
                        "provider": "openai",
                        "model": "o3",
                        "response": {
                            "finish_reason": "stop",
                            "output_text": "done",
                            "tool_calls": [],
                            "thinking_blocks": [
                                {"type": "thinking", "content": "inspect tool output"}
                            ],
                        },
                    }
                ),
                encoding="utf-8",
            )
            args = Namespace(path=str(trace_path), include_thinking=True, json=False)
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                self.assertEqual(_debug_trace(args), 0)
            output = stdout.getvalue()
            self.assertIn("Thinking:", output)
            self.assertIn("inspect tool output", output)
