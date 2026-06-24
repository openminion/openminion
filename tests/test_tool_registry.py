import importlib
import json
import os
import subprocess
import sys
import unittest
from unittest import mock

from pydantic import BaseModel, ConfigDict

from openminion.modules.llm.providers.base import ProviderToolCall
from openminion.modules.tool.base import (
    Tool,
    ToolExecutionContext,
    ToolExecutionPolicy,
    ToolExecutionResult,
)
from openminion.modules.tool import (
    build_default_tool_registry,
    build_default_tool_registry_debug_report,
)
from openminion.modules.tool.registry import ToolRegistry


class _EchoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str


class _PolicyTool(Tool):
    name = "policy_tool"
    description = "policy test tool"
    policy = ToolExecutionPolicy(
        required_scopes_all=("tool.execute", "tool.policy.run"),
        risk="high",
        budget_cost=3,
    )

    def execute(self, arguments, context: ToolExecutionContext) -> ToolExecutionResult:
        del arguments, context
        return ToolExecutionResult(
            tool_name=self.name, ok=True, content="ok", verified=True
        )


class _DagTool(Tool):
    name = "dag.echo"
    description = "dag test tool"
    execution_log: list[str] = []

    def execute(self, arguments, context: ToolExecutionContext) -> ToolExecutionResult:
        del context
        label = str((arguments or {}).get("label", "") or "")
        self.execution_log.append(label)
        if bool((arguments or {}).get("fail", False)):
            return ToolExecutionResult(
                tool_name=self.name,
                ok=False,
                content="",
                verified=False,
                error="forced failure",
                data={"error_code": "forced_failure"},
            )
        return ToolExecutionResult(
            tool_name=self.name,
            ok=True,
            content=label or "ok",
            verified=True,
        )


class _UnmappedTool(Tool):
    name = "custom.unmapped"
    description = "tool without canonical model binding"

    def execute(self, arguments, context: ToolExecutionContext) -> ToolExecutionResult:
        del arguments, context
        return ToolExecutionResult(
            tool_name=self.name, ok=True, content="ok", verified=True
        )


class _WeatherCurrentTool(Tool):
    name = "weather"
    description = "weather current"
    parameters = {
        "type": "object",
        "properties": {"location": {"type": "string"}},
        "required": ["location"],
        "additionalProperties": False,
    }

    def execute(self, arguments, context: ToolExecutionContext) -> ToolExecutionResult:
        del context
        return ToolExecutionResult(
            tool_name=self.name,
            ok=True,
            content=f"weather:{arguments.get('location', '')}",
            verified=True,
        )


class ToolRegistryTests(unittest.TestCase):
    def test_policy_for_known_tool_reads_execution_policy(self) -> None:
        registry = ToolRegistry([_PolicyTool()])
        policy = registry.policy_for("policy_tool")
        self.assertEqual(policy.tool_name, "policy_tool")
        self.assertEqual(
            policy.required_scopes_all, frozenset({"tool.execute", "tool.policy.run"})
        )
        self.assertEqual(policy.risk, "high")
        self.assertEqual(policy.budget_cost, 3)

    def test_policy_for_unknown_tool_returns_safe_default(self) -> None:
        registry = ToolRegistry([])
        policy = registry.policy_for("missing_tool")
        self.assertEqual(policy.tool_name, "missing_tool")
        self.assertEqual(policy.required_scopes_all, frozenset({"tool.execute"}))
        self.assertEqual(policy.risk, "medium")
        self.assertEqual(policy.budget_cost, 1)

    def test_default_registry_contains_local_ops_tools(self) -> None:
        registry = build_default_tool_registry()
        names = {spec.name for spec in registry.provider_specs()}
        self.assertIn("file.list_dir", names)
        self.assertIn("file.find", names)
        self.assertIn("file.read", names)
        self.assertIn("file.write", names)
        self.assertIn("exec.run", names)
        self.assertIn("exec.kill", names)
        self.assertIn("exec.poll", names)
        self.assertIn("fetch.get", names)
        self.assertIn("skill.ingest_url", names)
        self.assertIn("time.now", names)
        self.assertIn("time.convert", names)
        self.assertIn("time.diff", names)
        self.assertIn("location.get", names)

    def test_legacy_tavily_source_is_rejected_in_module_only_runtime(self) -> None:
        import openminion.modules.tool as tools_module

        with mock.patch.dict(
            os.environ,
            {
                "OPENMINION_MODULES_ONLY": "true",
                "OPENMINION_TAVILY_SOURCE": "legacy",
            },
            clear=False,
        ):
            tools_module = importlib.reload(tools_module)
            registry = tools_module.build_default_tool_registry()
            names = {spec.name for spec in registry.provider_specs()}
            self.assertIn("search.tavily.search", names)
            self.assertIn("search.serpapi.search", names)
            self.assertIn("search.firecrawl.search", names)
            self.assertIn("search.serper.search", names)
            self.assertIn("search.tinyfish.search", names)
        importlib.reload(tools_module)

    def test_module_registry_contains_module_search_without_legacy_alias(self) -> None:
        registry = build_default_tool_registry()
        names = {spec.name for spec in registry.provider_specs()}
        self.assertIn("search.tavily.search", names)
        self.assertIn("search.serpapi.search", names)
        self.assertIn("search.firecrawl.search", names)
        self.assertIn("search.serper.search", names)
        self.assertIn("search.tinyfish.search", names)
        self.assertNotIn("web.search", names)

    def test_default_bootstrap_registers_serper_after_firecrawl(self) -> None:
        from openminion.tools.search import plugin as search_plugin

        search_plugin._PROVIDERS.clear()
        search_plugin._PROVIDER_ORDER.clear()
        try:
            build_default_tool_registry()
            self.assertEqual(
                search_plugin.list_provider_ids(),
                ("tavily", "brave", "serpapi", "firecrawl", "serper", "tinyfish"),
            )
        finally:
            search_plugin._PROVIDERS.clear()
            search_plugin._PROVIDER_ORDER.clear()

    def test_model_provider_specs_fails_closed_when_canonical_exposure_empty(
        self,
    ) -> None:
        registry = ToolRegistry([_UnmappedTool()])
        with self.assertRaises(RuntimeError) as context:
            registry.model_provider_specs()
        self.assertIn("Canonical model tool exposure is empty", str(context.exception))

    def test_model_provider_specs_can_opt_in_to_legacy_fallback_via_env(self) -> None:
        registry = ToolRegistry([_UnmappedTool()])
        with mock.patch.dict(
            os.environ,
            {"OPENMINION_ALLOW_MODEL_EXPOSURE_PROVIDER_FALLBACK": "1"},
            clear=False,
        ):
            specs = registry.model_provider_specs()
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0].name, "custom.unmapped")

    def test_execute_calls_supports_openminion_tool_toolspec(self) -> None:
        from openminion.modules.tool.registry import ToolSpec

        registry = ToolRegistry([])
        registry.add(
            ToolSpec(
                name="echo.toolspec",
                args_model=_EchoArgs,
                min_scope="READ_ONLY",
                handler=lambda args, _ctx: {
                    "ok": True,
                    "content": f"echo:{args['name']}",
                    "verified": True,
                    "data": {"name": args["name"]},
                },
            )
        )

        batch = registry.execute_calls(
            [ProviderToolCall(name="echo.toolspec", arguments={"name": "ok"})],
            context=ToolExecutionContext(
                channel="console", target="test", session_id="tests-toolspec"
            ),
        )
        self.assertEqual(len(batch.results), 1)
        self.assertTrue(batch.results[0].ok)
        self.assertEqual(batch.results[0].content, "echo:ok")
        self.assertEqual(batch.results[0].data.get("name"), "ok")

    def test_model_origin_disables_runtime_direct_for_unmapped_toolspec(self) -> None:
        from openminion.modules.tool.registry import ToolSpec

        registry = ToolRegistry([])
        registry.add(
            ToolSpec(
                name="echo.toolspec",
                args_model=_EchoArgs,
                min_scope="READ_ONLY",
                handler=lambda args, _ctx: {
                    "ok": True,
                    "content": f"echo:{args['name']}",
                    "verified": True,
                    "data": {"name": args["name"]},
                },
            )
        )
        batch = registry.execute_calls(
            [ProviderToolCall(name="echo.toolspec", arguments={"name": "ok"})],
            context=ToolExecutionContext(
                channel="console",
                target="test",
                session_id="tests-toolspec",
                metadata={"tool_call_origin": "model"},
            ),
        )
        self.assertEqual(len(batch.results), 1)
        self.assertFalse(batch.results[0].ok)
        self.assertIn("unknown tool", str(batch.results[0].error))

    def test_model_origin_can_explicitly_enable_runtime_direct(self) -> None:
        from openminion.modules.tool.registry import ToolSpec

        registry = ToolRegistry([])
        registry.add(
            ToolSpec(
                name="echo.toolspec",
                args_model=_EchoArgs,
                min_scope="READ_ONLY",
                handler=lambda args, _ctx: {
                    "ok": True,
                    "content": f"echo:{args['name']}",
                    "verified": True,
                    "data": {"name": args["name"]},
                },
            )
        )
        batch = registry.execute_calls(
            [ProviderToolCall(name="echo.toolspec", arguments={"name": "ok"})],
            context=ToolExecutionContext(
                channel="console",
                target="test",
                session_id="tests-toolspec",
                metadata={
                    "tool_call_origin": "model",
                    "allow_runtime_direct": "true",
                },
            ),
        )
        self.assertEqual(len(batch.results), 1)
        self.assertTrue(batch.results[0].ok)
        self.assertEqual(batch.results[0].content, "echo:ok")

    def test_model_origin_rejects_runtime_alias_even_when_binding_exists(self) -> None:
        from openminion.modules.tool.bootstrap import wire_default_tool_registry_manager

        wire_default_tool_registry_manager()
        registry = ToolRegistry([_WeatherCurrentTool()])
        batch = registry.execute_calls(
            [
                ProviderToolCall(
                    name="weather.openmeteo.current", arguments={"location": "sf"}
                )
            ],
            context=ToolExecutionContext(
                channel="console",
                target="test",
                session_id="tests-weather",
                metadata={"tool_call_origin": "model"},
            ),
        )
        self.assertEqual(len(batch.results), 1)
        self.assertFalse(batch.results[0].ok)
        self.assertIn("unknown tool", str(batch.results[0].error))

    def test_model_origin_accepts_canonical_model_tool_id(self) -> None:
        from openminion.modules.tool.bootstrap import wire_default_tool_registry_manager

        wire_default_tool_registry_manager()
        registry = ToolRegistry([_WeatherCurrentTool()])
        batch = registry.execute_calls(
            [ProviderToolCall(name="weather", arguments={"location": "sf"})],
            context=ToolExecutionContext(
                channel="console",
                target="test",
                session_id="tests-weather",
                metadata={"tool_call_origin": "model"},
            ),
        )
        self.assertEqual(len(batch.results), 1)
        self.assertTrue(batch.results[0].ok)
        self.assertEqual(batch.results[0].content, "weather:sf")

    def test_execute_calls_toolspec_validation_error_is_reported(self) -> None:
        from openminion.modules.tool.registry import ToolSpec

        registry = ToolRegistry([])
        registry.add(
            ToolSpec(
                name="echo.toolspec",
                args_model=_EchoArgs,
                min_scope="READ_ONLY",
                handler=lambda args, _ctx: {
                    "ok": True,
                    "content": f"echo:{args['name']}",
                },
            )
        )

        batch = registry.execute_calls(
            [ProviderToolCall(name="echo.toolspec", arguments={})],
            context=ToolExecutionContext(
                channel="console", target="test", session_id="tests-toolspec"
            ),
        )
        self.assertEqual(len(batch.results), 1)
        self.assertFalse(batch.results[0].ok)
        self.assertIn("invalid tool arguments", batch.results[0].error.lower())
        self.assertEqual(
            (batch.results[0].data or {}).get("error_code"), "invalid_arguments"
        )
        self.assertEqual(
            (batch.results[0].data or {}).get("reason_code"),
            "tool_arg_validation_failed",
        )

    def test_execute_calls_toolspec_non_object_arguments_report_contract_payload(
        self,
    ) -> None:
        from openminion.modules.tool.registry import ToolSpec

        registry = ToolRegistry([])
        registry.add(
            ToolSpec(
                name="echo.toolspec",
                args_model=_EchoArgs,
                min_scope="READ_ONLY",
                handler=lambda args, _ctx: {
                    "ok": True,
                    "content": f"echo:{args['name']}",
                },
            )
        )

        batch = registry.execute_calls(
            [ProviderToolCall(name="echo.toolspec", arguments="not-an-object")],
            context=ToolExecutionContext(
                channel="console", target="test", session_id="tests-toolspec"
            ),
        )
        self.assertEqual(len(batch.results), 1)
        self.assertFalse(batch.results[0].ok)
        self.assertIn("invalid tool arguments", batch.results[0].error.lower())
        self.assertEqual(
            (batch.results[0].data or {}).get("error_code"), "invalid_arguments"
        )
        self.assertEqual(
            (batch.results[0].data or {}).get("reason_code"),
            "tool_arg_validation_failed",
        )

    def test_provider_specs_include_toolspec_schema(self) -> None:
        from openminion.modules.tool.registry import ToolSpec

        registry = ToolRegistry([])
        registry.add(
            ToolSpec(
                name="echo.toolspec",
                args_model=_EchoArgs,
                min_scope="READ_ONLY",
                handler=lambda args, _ctx: {
                    "ok": True,
                    "content": f"echo:{args['name']}",
                },
            )
        )

        specs = {item.name: item for item in registry.provider_specs()}
        self.assertIn("echo.toolspec", specs)
        params = specs["echo.toolspec"].parameters
        self.assertIsInstance(params, dict)
        self.assertEqual(params.get("type"), "object")
        self.assertIn("name", (params.get("properties") or {}))

    def test_execute_calls_unwraps_nested_tool_envelope_arguments(self) -> None:
        from openminion.modules.tool.registry import ToolSpec

        registry = ToolRegistry([])
        registry.add(
            ToolSpec(
                name="echo.toolspec",
                args_model=_EchoArgs,
                min_scope="READ_ONLY",
                handler=lambda args, _ctx: {
                    "ok": True,
                    "content": f"echo:{args['name']}",
                },
            )
        )

        nested_call = ProviderToolCall(
            name="echo.toolspec",
            arguments={
                "name": "echo.toolspec",
                "arguments": {"name": "nested-ok"},
            },
        )
        batch = registry.execute_calls(
            [nested_call],
            context=ToolExecutionContext(
                channel="console", target="test", session_id="tests-toolspec"
            ),
        )
        self.assertEqual(len(batch.results), 1)
        self.assertTrue(batch.results[0].ok)
        self.assertEqual(batch.results[0].content, "echo:nested-ok")

    def test_execute_calls_honors_depends_on_ordering(self) -> None:
        _DagTool.execution_log = []
        registry = ToolRegistry([_DagTool()])
        batch = registry.execute_calls(
            [
                ProviderToolCall(
                    name="dag.echo",
                    id="c",
                    depends_on=["a", "b"],
                    arguments={"label": "c"},
                ),
                ProviderToolCall(name="dag.echo", id="a", arguments={"label": "a"}),
                ProviderToolCall(name="dag.echo", id="b", arguments={"label": "b"}),
            ],
            context=ToolExecutionContext(
                channel="console", target="test", session_id="tests-dag"
            ),
        )
        self.assertEqual(len(batch.results), 3)
        self.assertTrue(all(item.ok for item in batch.results))
        self.assertEqual(_DagTool.execution_log, ["a", "b", "c"])

    def test_execute_calls_depends_on_cycle_reports_error(self) -> None:
        _DagTool.execution_log = []
        registry = ToolRegistry([_DagTool()])
        batch = registry.execute_calls(
            [
                ProviderToolCall(
                    name="dag.echo",
                    id="a",
                    depends_on=["b"],
                    arguments={"label": "a"},
                ),
                ProviderToolCall(
                    name="dag.echo",
                    id="b",
                    depends_on=["a"],
                    arguments={"label": "b"},
                ),
            ],
            context=ToolExecutionContext(
                channel="console", target="test", session_id="tests-dag"
            ),
        )
        self.assertEqual(len(batch.results), 2)
        self.assertTrue(all(not item.ok for item in batch.results))
        self.assertTrue(
            all(
                (item.data or {}).get("reason_code") == "DEPENDENCY_CYCLE"
                for item in batch.results
            )
        )
        self.assertEqual(_DagTool.execution_log, [])

    def test_execute_calls_skips_when_dependency_failed(self) -> None:
        _DagTool.execution_log = []
        registry = ToolRegistry([_DagTool()])
        batch = registry.execute_calls(
            [
                ProviderToolCall(
                    name="dag.echo",
                    id="a",
                    arguments={"label": "a", "fail": True},
                ),
                ProviderToolCall(
                    name="dag.echo",
                    id="b",
                    depends_on=["a"],
                    arguments={"label": "b"},
                ),
            ],
            context=ToolExecutionContext(
                channel="console", target="test", session_id="tests-dag"
            ),
        )
        self.assertEqual(len(batch.results), 2)
        self.assertFalse(batch.results[0].ok)
        self.assertFalse(batch.results[1].ok)
        self.assertEqual(
            (batch.results[1].data or {}).get("reason_code"), "DEPENDENCY_FAILED"
        )
        self.assertIn("failed dependency", batch.results[1].error.lower())
        self.assertEqual(_DagTool.execution_log, ["a"])

    def test_execute_calls_rejects_duplicate_call_ids(self) -> None:

        _DagTool.execution_log = []
        registry = ToolRegistry([_DagTool()])
        batch = registry.execute_calls(
            [
                ProviderToolCall(
                    name="dag.echo",
                    id="dup",
                    arguments={"label": "first"},
                ),
                ProviderToolCall(
                    name="dag.echo",
                    id="dup",
                    arguments={"label": "second"},
                ),
            ],
            context=ToolExecutionContext(
                channel="console", target="test", session_id="tests-dag-dup"
            ),
        )
        self.assertEqual(len(batch.results), 2)

        self.assertTrue(batch.results[0].ok)
        self.assertEqual(batch.results[0].content, "first")

        duplicate_result = batch.results[1]
        self.assertFalse(duplicate_result.ok)
        duplicate_data = duplicate_result.data or {}
        self.assertEqual(duplicate_data.get("reason_code"), "DUPLICATE_CALL_ID")
        self.assertEqual(duplicate_data.get("error_code"), "invalid_dependency_graph")
        self.assertEqual(duplicate_data.get("duplicate_call_id"), "dup")
        self.assertIn("duplicate tool call id", duplicate_result.error.lower())

        self.assertEqual(_DagTool.execution_log, ["first"])

    def test_execute_calls_rejects_duplicate_call_ids_with_depends_on_present(
        self,
    ) -> None:
        _DagTool.execution_log = []
        registry = ToolRegistry([_DagTool()])
        batch = registry.execute_calls(
            [
                ProviderToolCall(
                    name="dag.echo",
                    id="dup",
                    arguments={"label": "first"},
                ),
                ProviderToolCall(
                    name="dag.echo",
                    id="dup",
                    depends_on=["dup"],
                    arguments={"label": "second"},
                ),
            ],
            context=ToolExecutionContext(
                channel="console", target="test", session_id="tests-dag-dup-dep"
            ),
        )
        self.assertEqual(len(batch.results), 2)
        self.assertTrue(batch.results[0].ok)
        self.assertFalse(batch.results[1].ok)
        duplicate_data = batch.results[1].data or {}
        self.assertEqual(duplicate_data.get("reason_code"), "DUPLICATE_CALL_ID")
        self.assertEqual(duplicate_data.get("error_code"), "invalid_dependency_graph")
        self.assertEqual(duplicate_data.get("duplicate_call_id"), "dup")
        self.assertEqual(_DagTool.execution_log, ["first"])


class BrowserPinchtabToolRegistryTests(unittest.TestCase):
    def test_browser_tool_available_in_registry(self) -> None:
        registry = build_default_tool_registry()
        names = {spec.name for spec in registry.provider_specs()}
        self.assertIn("browser", names)

    def test_browser_pinchtab_provider_registered(self) -> None:
        from openminion.tools.browser.tool import provider_registry

        build_default_tool_registry()
        providers = provider_registry().list_provider_ids()
        self.assertIn("pinchtab", providers)

    def test_browser_pinchtab_legacy_tools_not_registered_by_default(self) -> None:
        registry = build_default_tool_registry()
        names = {spec.name for spec in registry.provider_specs()}
        pinchtab_tools = [n for n in names if n.startswith("browser.pinchtab.")]
        self.assertEqual(pinchtab_tools, [])


class BrowserGenericToolRegistryTests(unittest.TestCase):
    def test_provider_neutral_browser_tool_registered_when_module_available(
        self,
    ) -> None:
        try:
            import openminion.tools.browser  # noqa: F401
        except ImportError:
            self.skipTest("openminion.tools.browser module not available")

        registry = build_default_tool_registry()
        names = {spec.name for spec in registry.provider_specs()}
        self.assertIn("browser", names)

    def test_provider_neutral_browser_daemon_ensure_accepts_playwright_provider(
        self,
    ) -> None:
        try:
            import openminion.tools.browser  # noqa: F401
            import openminion.tools.browser.providers.playwright  # noqa: F401
        except ImportError:
            self.skipTest("browser tool modules not available")

        script = """
import json
from openminion.modules.tool import build_default_tool_registry
from openminion.modules.tool.base import ToolExecutionContext

registry = build_default_tool_registry()
tool = registry.get("browser")
result = tool.execute(
    {"op": "daemon.ensure", "provider": "playwright"},
    ToolExecutionContext(channel="test", target="test"),
)
print(json.dumps({"ok": result.ok, "error": result.error, "data": result.data}))
"""
        env = dict(os.environ)
        env["PYTHONPATH"] = "src"
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=os.getcwd(),
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        self.assertTrue(lines)
        payload = json.loads(lines[-1])
        self.assertNotIn("TypeError", str(payload.get("error", "")))
        self.assertNotIn("takes 1 positional argument", str(payload.get("error", "")))


class BrowserPlaywrightToolRegistryTests(unittest.TestCase):
    def test_browser_playwright_legacy_tools_not_registered(self) -> None:
        registry = build_default_tool_registry()
        names = {spec.name for spec in registry.provider_specs()}
        playwright_tools = [n for n in names if n.startswith("browser.playwright.")]
        self.assertEqual(
            playwright_tools,
            [],
            "browser.playwright.* tools should not be model-facing",
        )

    def test_browser_playwright_provider_registered_with_browser_core(self) -> None:
        from openminion.tools.browser.tool import provider_registry

        build_default_tool_registry()
        providers = provider_registry().list_provider_ids()
        self.assertIn("playwright", providers)

    def test_browser_tools_are_indexed_under_browser_category(self) -> None:
        registry = build_default_tool_registry()
        browser_tools = registry.tools_by_category("browser")
        self.assertIn("browser", browser_tools)


class ReactionsToolRegistryTests(unittest.TestCase):
    def test_reactions_tools_available_in_registry(self) -> None:
        try:
            import openminion.tools.reaction  # noqa: F401
        except ImportError:
            self.skipTest("openminion.tools.reaction module not available")

        registry = build_default_tool_registry()
        names = {spec.name for spec in registry.provider_specs()}
        self.assertIn("reactions.set", names)
        self.assertIn("reactions.list", names)


def test_registration_debug_report_ok_semantics_match_required_failures() -> None:
    report = build_default_tool_registry_debug_report()
    required_failures = list(report.get("required_failures", []) or [])
    assert report.get("ok") is (len(required_failures) == 0)


def test_registration_debug_report_contract_shape() -> None:
    report = build_default_tool_registry_debug_report()

    assert "bootstrap_records" in report
    assert "required_failures" in report
    assert "registry_snapshot" in report

    records = list(report.get("bootstrap_records", []) or [])
    assert records
    first_record = records[0]
    for key in (
        "kind",
        "module_name",
        "label",
        "required",
        "gate",
        "enabled",
        "status",
        "added_runtime_tools",
        "error",
    ):
        assert key in first_record

    snapshot = report["registry_snapshot"]
    manager = snapshot.get("manager", {})
    for key in (
        "runtime_tool_count",
        "model_provider_spec_count",
        "runtime_tools",
        "untracked_runtime_tools",
    ):
        assert key in snapshot
    for key in (
        "runtime_binding_count",
        "unresolved_runtime_binding_ids",
        "runtime_bindings",
    ):
        assert key in manager
