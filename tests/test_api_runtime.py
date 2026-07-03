import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
from unittest.mock import patch
from tests._csc_fixtures import _csc_install_default_agent


from openminion.api.runtime import APIRuntime
from openminion.api.server import dispatch_request
from openminion.base.config import ConfigError, OpenMinionConfig, save_config
from openminion.modules.storage.runtime.migrations import DEFAULT_MIGRATIONS
from openminion.api.queries.sessions import list_session_messages
from openminion.api.turns import run_turn
from openminion.services.agent.memory.hello_world import (
    HelloWorldMemoryService,
)
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter
from openminion.services.tool.exposure import get_model_exposure_specs
from tests.helpers import (
    extract_runtime_info_from_agent_service,
    extract_runtime_info_from_api_runtime,
)


class APIRuntimeTests(unittest.TestCase):
    def test_runtime_reuse_across_turn_and_session_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            import os as _os_module

            _os_module.environ["OPENMINION_DATA_ROOT"] = str(Path(tmp) / ".openminion")
            config_path = _write_echo_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                self.assertTrue(runtime.self_improvement.enabled)
                self.assertEqual(
                    runtime.gateway._session_context._keep_recent_messages, 6
                )
                self.assertEqual(
                    runtime.gateway._session_context._max_compact_per_turn, 11
                )
                self.assertEqual(
                    runtime.gateway._session_context._summary_max_chars, 4096
                )
                self.assertTrue(runtime.gateway._session_context._archive_enabled)
                self.assertEqual(runtime.gateway._session_context._archive_ref_limit, 4)
                self.assertEqual(
                    runtime.gateway._session_context._archive_root,
                    (config_path.parent / "session-context-archive").resolve(),
                )
                self.assertIsInstance(
                    runtime.gateway._agent_memory, MemoryServiceGatewayAdapter
                )  # noqa: SLF001
                self.assertTrue(hasattr(runtime.gateway._agent_memory, "build_context"))  # noqa: SLF001
                self.assertTrue(hasattr(runtime.gateway._agent_memory, "record_turn"))  # noqa: SLF001
                tool_names = [
                    spec.name for spec in get_model_exposure_specs(runtime.tools)
                ]
                self.assertIn("weather", tool_names)
                _assert_search_exposure_matches_provider_env(self, tool_names)
                self.assertIn("web.fetch", tool_names)
                with redirect_stdout(io.StringIO()):
                    run_turn(
                        str(config_path),
                        {"message": "first", "session_id": "runtime-session"},
                        runtime=runtime,
                    )
                    run_turn(
                        str(config_path),
                        {"message": "second", "session_id": "runtime-session"},
                        runtime=runtime,
                    )
                transcript = list_session_messages(
                    str(config_path),
                    session_id="runtime-session",
                    runtime=runtime,
                )
                self.assertEqual(transcript["session"]["id"], "runtime-session")
                self.assertEqual(len(transcript["messages"]), 4)
            finally:
                runtime.close()

    def test_runtime_close_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_echo_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            runtime.close()
            runtime.close()

    def test_runtime_close_shuts_down_manager_before_lifecycle_bridge(self) -> None:
        runtime = object.__new__(APIRuntime)
        order: list[str] = []

        class FakeManager:
            def shutdown(self, *, grace_s: int) -> None:
                order.append(f"manager.shutdown:{grace_s}")

        class FakeLifecycleBridge:
            def close(self) -> None:
                order.append("lifecycle_bridge.close")

        class FakeClosable:
            def __init__(self, name: str) -> None:
                self._name = name

            def close(self) -> None:
                order.append(f"{self._name}.close")

        runtime._closed = False
        runtime._lifecycle_event_bridge = FakeLifecycleBridge()
        runtime.retrieve_ctl = FakeClosable("retrieve_ctl")
        runtime.action_policy = FakeClosable("action_policy")
        runtime.runtime_manager = FakeManager()
        runtime.runtime_storage = FakeClosable("runtime_storage")

        runtime.close()

        self.assertEqual(
            order,
            [
                "retrieve_ctl.close",
                "action_policy.close",
                "manager.shutdown:2",
                "lifecycle_bridge.close",
                "runtime_storage.close",
            ],
        )
        self.assertTrue(runtime._closed)

    def test_runtime_close_detaches_registered_finalizer(self) -> None:
        runtime = object.__new__(APIRuntime)
        order: list[str] = []

        class FakeManager:
            def shutdown(self, *, grace_s: int) -> None:
                order.append(f"manager.shutdown:{grace_s}")

        class FakeClosable:
            def __init__(self, name: str) -> None:
                self._name = name

            def close(self) -> None:
                order.append(f"{self._name}.close")

        runtime._closed = False
        runtime.retrieve_ctl = FakeClosable("retrieve_ctl")
        runtime.action_policy = FakeClosable("action_policy")
        runtime.runtime_manager = FakeManager()
        runtime.runtime_storage = FakeClosable("runtime_storage")
        runtime.tools = type(
            "FakeTools", (), {"mcp_manager": FakeClosable("mcp_manager")}
        )()

        APIRuntime.__post_init__(runtime)
        self.assertIsNotNone(runtime._finalizer)
        assert runtime._finalizer is not None
        self.assertTrue(runtime._finalizer.alive)

        runtime.close()

        self.assertFalse(runtime._finalizer.alive)
        self.assertEqual(
            order,
            [
                "retrieve_ctl.close",
                "action_policy.close",
                "manager.shutdown:2",
                "mcp_manager.close",
                "runtime_storage.close",
            ],
        )

    def test_runtime_injects_single_action_policy_service_into_brain_runner(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_echo_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                self.assertIsNotNone(runtime.action_policy)
                if not hasattr(runtime.agent, "_get_runner"):
                    self.skipTest(
                        "Brain bridge runtime is not active in this environment"
                    )
                runner = runtime.agent._get_runner()  # type: ignore[attr-defined]
                self.assertIsNotNone(getattr(runner, "policy_api", None))
                self.assertIs(
                    getattr(runner.policy_api, "_ctl", None), runtime.action_policy
                )
            finally:
                runtime.close()

    def test_runtime_action_policy_auto_registers_risk_for_registered_tools(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_echo_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                policy_ctl = runtime.action_policy
                self.assertIsNotNone(policy_ctl)
                risk_registry = getattr(policy_ctl, "_risk_registry", {})
                self.assertIsInstance(risk_registry, dict)
                self.assertGreater(len(risk_registry), 0)
                for tool_name in runtime.tools.list().keys():
                    key = tool_name if "." in tool_name else f"{tool_name}.default"
                    self.assertIn(key, risk_registry)
            finally:
                runtime.close()

    def test_runtime_action_policy_uses_configured_confirmation_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_echo_config(Path(tmp))
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            payload["action_policy"] = {
                "affirmative_tokens": ["absolutely"],
                "negative_tokens": ["decline"],
            }
            config_path.write_text(json.dumps(payload), encoding="utf-8")
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                policy_ctl = runtime.action_policy
                self.assertIsNotNone(policy_ctl)
                self.assertEqual(
                    policy_ctl.parse_confirmation_response("absolutely"), "affirm"
                )
                self.assertEqual(
                    policy_ctl.parse_confirmation_response("decline"), "deny"
                )
                self.assertEqual(
                    policy_ctl.parse_confirmation_response("yes"), "unclear"
                )
            finally:
                runtime.close()

    def test_runtime_wires_retrieve_ctl_into_gateway_memory_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_echo_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                self.assertIsNotNone(getattr(runtime, "retrieve_ctl", None))
                self.assertIsInstance(
                    runtime.gateway._agent_memory,  # noqa: SLF001
                    MemoryServiceGatewayAdapter,
                )
                self.assertIs(
                    runtime.gateway._agent_memory._retrieve_ctl,  # noqa: SLF001
                    runtime.retrieve_ctl,
                )
            finally:
                runtime.close()

    def test_tool_catalog_convergence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_echo_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                tool_names = [
                    spec.name for spec in get_model_exposure_specs(runtime.tools)
                ]
                legacy_baseline_count = 16
                self.assertGreater(
                    len(tool_names),
                    legacy_baseline_count,
                    f"Tool catalog should exceed legacy baseline ({legacy_baseline_count})",
                )
                self.assertIn("weather", tool_names)
                _assert_search_exposure_matches_provider_env(self, tool_names)
            finally:
                runtime.close()

    def test_runtime_mode_tracking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_echo_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                self.assertIn(runtime._runtime_mode, ["brain", "legacy"])
                self.assertIsInstance(runtime._brain_bridge_active, bool)
                self.assertIsInstance(runtime._last_bridge_fallback_reason, str)
            finally:
                runtime.close()

    def test_runtime_uses_direct_llm_runtime_handle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_echo_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                self.assertTrue(hasattr(runtime, "llm_runtime"))
                self.assertEqual(runtime.llm.name, runtime.provider.name)
                self.assertTrue(callable(getattr(runtime.llm.client, "complete", None)))
                self.assertFalse(callable(getattr(runtime.provider, "generate", None)))
                active_agent = runtime.resolve_agent_service()
                self.assertIsNotNone(getattr(active_agent, "_llm_runtime", None))
            finally:
                runtime.close()

    def test_runtime_from_preloaded_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = _write_echo_config(tmp_path)
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.runtime.log_level = "ERROR"
            _csc_install_default_agent(config, provider="echo")
            config.storage.path = str(tmp_path / "state" / "api.db")
            data_root = tmp_path / "data"
            data_root.mkdir(parents=True, exist_ok=True)
            runtime = APIRuntime.from_config(
                config=config,
                home_root=tmp_path,
                data_root=data_root,
                config_path=config_path,
            )
            try:
                self.assertEqual(
                    runtime.config.agents[
                        next(iter(runtime.config.agents.keys()))
                    ].provider,
                    "echo",
                )
                self.assertEqual(Path(runtime.config_path), config_path)
            finally:
                runtime.close()

    def test_run_turn_memory_policy_question_no_longer_uses_runtime_shortcut(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_echo_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                session = runtime.sessions.resolve_session(
                    agent_id=runtime.config.agents[
                        next(iter(runtime.config.agents.keys()))
                    ].name,
                    channel="console",
                    target="api-user",
                    session_id="memory-policy-session",
                )
                with redirect_stdout(io.StringIO()):
                    result = run_turn(
                        str(config_path),
                        {
                            "message": "what is your memory retention and refresh policy across sessions?",
                            "session_id": session.id,
                            "channel": "console",
                            "target": "api-user",
                        },
                        runtime=runtime,
                    )

                metadata = result.get("metadata") or {}
                self.assertFalse(metadata.get("memory_policy_route"))
                self.assertFalse(metadata.get("memory_policy_source"))
                self.assertFalse(metadata.get("memory_policy_version"))

                events = runtime.sessions.list_events(session_id=session.id, limit=10)
                policy_events = [
                    event
                    for event in events
                    if event.event_type == "memory.policy.snapshot"
                ]
                self.assertEqual(len(policy_events), 0)
            finally:
                runtime.close()

    def test_run_turn_memory_policy_question_no_longer_uses_policy_unavailable_shortcut(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_echo_config(Path(tmp))
            with patch(
                "openminion.services.brain.service.build_memory_policy_snapshot",
                side_effect=RuntimeError("snapshot unavailable"),
            ):
                runtime = APIRuntime.from_config_path(str(config_path))
                try:
                    session = runtime.sessions.resolve_session(
                        agent_id=runtime.config.agents[
                            next(iter(runtime.config.agents.keys()))
                        ].name,
                        channel="console",
                        target="api-user",
                        session_id="memory-policy-unavailable",
                    )
                    with redirect_stdout(io.StringIO()):
                        result = run_turn(
                            str(config_path),
                            {
                                "message": "do you remember across sessions and what is your policy?",
                                "session_id": session.id,
                                "channel": "console",
                                "target": "api-user",
                            },
                            runtime=runtime,
                        )

                    metadata = result.get("metadata") or {}
                    self.assertFalse(metadata.get("reason_code"))
                    self.assertFalse(metadata.get("memory_policy_route"))

                    events = runtime.sessions.list_events(
                        session_id=session.id, limit=10
                    )
                    policy_events = [
                        event
                        for event in events
                        if event.event_type == "memory.policy.snapshot"
                    ]
                    self.assertEqual(len(policy_events), 0)
                finally:
                    runtime.close()

    def test_run_turn_memory_policy_question_still_creates_session_without_event(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_echo_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                with redirect_stdout(io.StringIO()):
                    result = run_turn(
                        str(config_path),
                        {
                            "message": "what is your memory retention policy?",
                            "session_id": "memory-policy-auto-session",
                            "channel": "console",
                            "target": "api-user",
                        },
                        runtime=runtime,
                    )

                self.assertEqual(result.get("session_id"), "memory-policy-auto-session")
                session = runtime.sessions.get_session("memory-policy-auto-session")
                self.assertIsNotNone(session)
                events = runtime.sessions.list_events(
                    session_id="memory-policy-auto-session",
                    limit=10,
                )
                policy_events = [
                    event
                    for event in events
                    if event.event_type == "memory.policy.snapshot"
                ]
                self.assertEqual(len(policy_events), 0)
            finally:
                runtime.close()

    def test_memory_policy_question_no_longer_shortcuts_with_hello_world_provider(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_echo_config(
                Path(tmp),
                memory_provider="memory_v2_hello_world",
            )
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                with redirect_stdout(io.StringIO()):
                    result = run_turn(
                        str(config_path),
                        {
                            "message": "what is your memory retention and refresh policy?",
                            "session_id": "mv2hw-memory-policy",
                            "channel": "console",
                            "target": "api-user",
                        },
                        runtime=runtime,
                    )
                metadata = result.get("metadata") or {}
                self.assertFalse(metadata.get("memory_policy_route"))
                self.assertFalse(metadata.get("memory_policy_source"))
                self.assertFalse(metadata.get("memory_policy_version"))

                events = runtime.sessions.list_events(
                    session_id="mv2hw-memory-policy",
                    limit=20,
                )
                policy_events = [
                    event
                    for event in events
                    if event.event_type == "memory.policy.snapshot"
                ]
                self.assertEqual(len(policy_events), 0)
            finally:
                runtime.close()

    def test_runtime_uses_hello_world_memory_provider_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            import os as _os_module

            _os_module.environ["OPENMINION_DATA_ROOT"] = str(Path(tmp) / ".openminion")
            config_path = _write_echo_config(
                Path(tmp),
                memory_provider="memory_v2_hello_world",
            )
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                self.assertIsInstance(
                    runtime.gateway._agent_memory, HelloWorldMemoryService
                )  # noqa: SLF001
                with redirect_stdout(io.StringIO()):
                    result = run_turn(
                        str(config_path),
                        {
                            "message": "remember: hello world provider fact",
                            "session_id": "mv2hw-provider-config",
                            "channel": "console",
                            "target": "api-user",
                        },
                        runtime=runtime,
                    )
                self.assertEqual(
                    result.get("metadata", {}).get("memory_enabled"), "true"
                )
            finally:
                runtime.close()

    def test_runtime_defaults_to_memory_v2_provider_when_unset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_echo_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                # Default provider is now memory_v2 (SQLite-backed V2 adapter)
                self.assertIsInstance(
                    runtime.gateway._agent_memory, MemoryServiceGatewayAdapter
                )  # noqa: SLF001
            finally:
                runtime.close()

    def test_runtime_rejects_legacy_memory_provider_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["OPENMINION_DATA_ROOT"] = str(Path(tmp) / ".openminion")
            config_path = Path(tmp) / "config.json"
            config_path.write_text(
                (
                    '{"agents":{"openminion":{"name":"openminion","provider":"echo"}},'
                    '"default_agent":"openminion",'
                    '"runtime":{"memory_provider":"agent_memory"},'
                    '"storage":{"path":"state/api.db"}}'
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ConfigError) as ctx:
                APIRuntime.from_config_path(str(config_path))
            self.assertIn("Invalid runtime.memory_provider", str(ctx.exception))

    def test_runtime_memory_provider_env_override_takes_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_echo_config(Path(tmp))
            with patch.dict(
                os.environ,
                {"OPENMINION_MEMORY_PROVIDER": "memory_v2_hello_world"},
                clear=False,
            ):
                runtime = APIRuntime.from_config_path(str(config_path))
                try:
                    self.assertIsInstance(
                        runtime.gateway._agent_memory, HelloWorldMemoryService
                    )  # noqa: SLF001
                finally:
                    runtime.close()

    def test_runtime_rejects_legacy_memory_provider_alias_from_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            import os as _os_module

            _os_module.environ["OPENMINION_DATA_ROOT"] = str(Path(tmp) / ".openminion")
            config_path = _write_echo_config(Path(tmp))
            with patch.dict(
                os.environ,
                {"OPENMINION_MEMORY_PROVIDER": "agent_memory"},
                clear=False,
            ):
                with self.assertRaises(ValueError) as ctx:
                    APIRuntime.from_config_path(str(config_path))
                self.assertIn("Unsupported runtime.memory_provider", str(ctx.exception))

    def test_resolve_agent_service_returns_object_not_tuple(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_echo_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                for agent_id in runtime.list_registered_agents():
                    svc = runtime.resolve_agent_service(agent_id)
                    # Must NOT be a tuple
                    self.assertNotIsInstance(
                        svc,
                        tuple,
                        f"resolve_agent_service({agent_id}) returned tuple instead of AgentService",
                    )
                    # Must have run_turn method
                    self.assertTrue(
                        hasattr(svc, "run_turn"),
                        f"resolve_agent_service({agent_id}) missing run_turn attribute",
                    )
                    info = runtime.get_agent_runtime_info(agent_id)
                    self.assertIn("runtime_mode", info)
                    self.assertIn("fallback_reason", info)
            finally:
                runtime.close()

    def test_from_config_path_resolves_relative_config_from_cwd_even_with_env_home_root(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            env_home = tmp_path / "env-home"
            env_home.mkdir(parents=True, exist_ok=True)
            workspace = tmp_path / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)

            config_path = workspace / "test-configs" / "config.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.runtime.log_level = "ERROR"
            _csc_install_default_agent(config, provider="echo")
            config.storage.path = "state/openminion.db"
            save_config(config, str(config_path))

            original_cwd = Path.cwd()
            try:
                os.chdir(workspace)
                with patch.dict(
                    os.environ,
                    {
                        "OPENMINION_HOME": str(env_home),
                        "OPENMINION_DATA_ROOT": "",
                    },
                    clear=False,
                ):
                    runtime = APIRuntime.from_config_path("test-configs/config.json")
                    try:
                        self.assertEqual(runtime.config_path, config_path.resolve())
                        self.assertEqual(runtime.home_root, env_home.resolve())
                        self.assertEqual(
                            runtime.data_root, (env_home / ".openminion").resolve()
                        )
                        self.assertEqual(
                            runtime.memory_root,
                            (env_home / ".openminion" / "memory").resolve(),
                        )
                        self.assertEqual(
                            runtime.storage_path,
                            (
                                env_home / ".openminion" / "state" / "openminion.db"
                            ).resolve(),
                        )
                    finally:
                        runtime.close()
            finally:
                os.chdir(original_cwd)

    def test_capability_report_includes_inventory_and_blocked_mode_details(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_echo_config(Path(tmp))
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            raw["system"] = {
                "runtime": {
                    "provider_policy": {
                        "enabled": ["echo"],
                        "default_provider": "echo",
                    },
                    "modes": {
                        "delegate": {"enabled": False},
                    },
                    "plugins": {
                        "blocked": ["example.disabled"],
                    },
                }
            }
            raw["enabled_plugins"] = ["example.disabled"]
            # Per-agent mode overrides live on agents.<id>.modes.
            from openminion.base.config.core import resolve_default_agent_id as _rda

            _default_agent_id = _rda(OpenMinionConfig.from_dict(raw))
            raw["agents"][_default_agent_id]["modes"] = {"delegate": {"enabled": True}}
            save_config(OpenMinionConfig.from_dict(raw), str(config_path))

            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                payload = runtime.capability_report()
            finally:
                runtime.close()

            self.assertEqual(payload["providers"]["selected"], "echo")
            provider_items = {
                item["name"]: item for item in payload["providers"]["items"]
            }
            self.assertTrue(provider_items["echo"]["enabled"])
            self.assertFalse(provider_items["openrouter"]["enabled"])
            mode_items = {item["name"]: item for item in payload["modes"]["items"]}
            self.assertEqual(
                mode_items["delegate"]["blocked_reason"],
                payload["modes"]["blocked_reasons"]["delegate"],
            )
            self.assertEqual(
                mode_items["respond"]["registration_source"]["category"],
                "essential_builtin",
            )
            self.assertEqual(mode_items["delegate"]["registration_source"], {})
            self.assertEqual(
                mode_items["respond"]["thinking_policy"]["default_reasoning_profile"],
                "off",
            )
            plugin_items = {item["name"]: item for item in payload["plugins"]["items"]}
            self.assertEqual(
                plugin_items["example.disabled"]["blocked_reason"],
                "blocked by runtime plugin policy",
            )
            self.assertGreater(payload["tools"]["counts"]["total"], 0)
            self.assertEqual(
                payload["thinking"]["effective"]["reasoning_profile"],
                "minimal",
            )

    def test_runtime_posture_includes_shared_runtime_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_echo_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                payload = runtime.runtime_posture()
            finally:
                runtime.close()

            self.assertEqual(payload["runtime_mode"], "brain")
            self.assertTrue(payload["brain_bridge_active"])
            self.assertTrue(payload["canonical_turn_path"])
            self.assertIn(
                "execution-boundary",
                payload["execution_boundary_policy"]["adapter"],
            )
            self.assertEqual(
                payload["capability_layering"]["ref"],
                "openminion.api.queries.runtime_reports.build_runtime_posture_report",
            )


def _write_echo_config(
    tmp_path: Path,
    *,
    memory_provider: str | None = None,
) -> Path:
    config_path = tmp_path / "config.json"
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.runtime.log_level = "ERROR"
    config.runtime.session_keep_recent_messages = 6
    config.runtime.session_max_compact_per_turn = 11
    config.runtime.session_summary_max_chars = 4096
    config.runtime.session_archive_enabled = True
    config.runtime.session_archive_root_path = str(tmp_path / "session-context-archive")
    config.runtime.session_archive_ref_limit = 4
    config.runtime.memory_enabled = True
    config.runtime.memory_retrieval_max_chars = 1500
    config.runtime.memory_log_retention_days = 12
    config.runtime.memory_max_facts = 70
    config.runtime.memory_max_todos = 80
    if memory_provider is not None:
        config.runtime.memory_provider = memory_provider
    _csc_install_default_agent(config, provider="echo")
    config.storage.path = str(tmp_path / "state" / "api.db")
    save_config(config, str(config_path))
    return config_path


def _assert_search_exposure_matches_provider_env(
    case: unittest.TestCase,
    tool_names: list[str],
) -> None:
    # The shared search facade stays registered independent of provider-key
    # presence; provider env only affects runtime usability, not catalog
    # exposure.
    case.assertIn("web.search", tool_names)


class APIRuntimeBootstrapTests(unittest.TestCase):
    def test_runtime_bootstraps_storage_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = tmp_path / "config.json"
            database_path = tmp_path / "state" / "runtime.db"

            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.runtime.log_level = "ERROR"
            config.runtime.session_keep_recent_messages = 7
            config.runtime.session_max_compact_per_turn = 9
            config.runtime.session_summary_max_chars = 4321
            config.runtime.session_archive_enabled = True
            config.runtime.session_archive_root_path = str(
                tmp_path / "session-context-archive"
            )
            config.runtime.session_archive_ref_limit = 5
            config.runtime.memory_enabled = True
            config.runtime.memory_retrieval_max_chars = 1234
            config.runtime.memory_log_retention_days = 15
            config.runtime.memory_max_facts = 33
            config.runtime.memory_max_todos = 44
            _csc_install_default_agent(config, provider="echo")
            config.storage.path = str(database_path)
            save_config(config, str(config_path))

            app = APIRuntime.from_config_path(str(config_path))
            self.assertEqual(app.storage_path, database_path.resolve())
            self.assertTrue(database_path.exists())
            self.assertIsNotNone(app.sessions)
            self.assertIsNotNone(app.idempotency)
            self.assertTrue(app.self_improvement.enabled)
            self.assertEqual(app.gateway._session_context._keep_recent_messages, 7)
            self.assertEqual(app.gateway._session_context._max_compact_per_turn, 9)
            self.assertEqual(app.gateway._session_context._summary_max_chars, 4321)
            self.assertTrue(app.gateway._session_context._archive_enabled)
            self.assertEqual(app.gateway._session_context._archive_ref_limit, 5)
            self.assertEqual(
                app.gateway._session_context._archive_root,
                (tmp_path / "session-context-archive").resolve(),
            )
            self.assertTrue(app.gateway._agent_memory.enabled)
            self.assertEqual(app.gateway._agent_memory._retrieval_max_chars, 1234)
            self.assertEqual(app.gateway._agent_memory._log_retention_days, 15)
            self.assertEqual(app.gateway._agent_memory._max_facts, 33)
            self.assertEqual(app.gateway._agent_memory._max_todos, 44)
            self.assertEqual(app.memory_root, (app.data_root / "memory").resolve())

            with sqlite3.connect(str(database_path)) as conn:
                version = conn.execute(
                    "SELECT MAX(version) FROM migrations"
                ).fetchone()[0]
                expected_head = max(
                    migration.version for migration in DEFAULT_MIGRATIONS
                )
                self.assertEqual(version, expected_head)


def _make_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.json"
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.runtime.log_level = "ERROR"
    _csc_install_default_agent(config, provider="echo")
    config.storage.path = str(tmp_path / "state" / "runtime.db")
    save_config(config, str(config_path))
    return config_path


def test_runtime_bootstrap_is_deterministic() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config_path = _make_config(Path(tmp))
        runtime1 = APIRuntime.from_config_path(str(config_path))
        runtime2 = APIRuntime.from_config_path(str(config_path))
        try:
            info1 = extract_runtime_info_from_api_runtime(runtime1)
            info2 = extract_runtime_info_from_api_runtime(runtime2)

            assert info1["runtime_mode"] == info2["runtime_mode"]
            assert info1["fallback_reason"] == info2["fallback_reason"]
            assert info1["brain_bridge_active"] == info2["brain_bridge_active"]

            agent_info1 = extract_runtime_info_from_agent_service(runtime1.agent)
            agent_info2 = extract_runtime_info_from_agent_service(runtime2.agent)
            assert agent_info1["is_bridge_service"] is True
            assert agent_info2["is_bridge_service"] is True
            assert agent_info1["service_type"] == agent_info2["service_type"]

            tool_names1 = {spec.name for spec in runtime1.tools.provider_specs()}
            tool_names2 = {spec.name for spec in runtime2.tools.provider_specs()}
            assert tool_names1 == tool_names2

            assert type(runtime1.provider) is type(runtime2.provider)
            assert bool(runtime1.security_policy) == bool(runtime2.security_policy)
        finally:
            runtime1.close()
            runtime2.close()


def _write_runtimectl_echo_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.json"
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    os.environ["OPENMINION_DATA_ROOT"] = str(tmp_path / ".openminion")
    config.runtime.log_level = "ERROR"
    _csc_install_default_agent(config, provider="echo")
    config.storage.path = str(tmp_path / "state" / "api-runtimectl.db")
    save_config(config, str(config_path))
    return config_path


class APIRuntimectlTests(unittest.TestCase):
    def test_v1_turn_and_agent_reuse_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_runtimectl_echo_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                status, payload = dispatch_request(
                    "POST",
                    "/v1/turn",
                    str(config_path),
                    body={
                        "trace_id": "trace-v1-a",
                        "agent_id": "openminion",
                        "session_id": "session-v1",
                        "input_text": "hello runtime",
                    },
                    runtime=runtime,
                )
                self.assertEqual(int(status), 200)
                self.assertTrue(payload["ok"])
                self.assertEqual(payload["turn"]["trace_id"], "trace-v1-a")
                self.assertIn("final_text", payload["turn"])

                status_agents, agents_payload = dispatch_request(
                    "GET",
                    "/v1/agents",
                    str(config_path),
                    runtime=runtime,
                )
                self.assertEqual(int(status_agents), 200)
                self.assertTrue(agents_payload["ok"])
                self.assertTrue(agents_payload["agents"])
                agent_ids = {entry["agent_id"] for entry in agents_payload["agents"]}
                self.assertIn("openminion", agent_ids)

                status_stream, stream_payload = dispatch_request(
                    "POST",
                    "/v1/turn/stream",
                    str(config_path),
                    body={
                        "trace_id": "trace-v1-b",
                        "agent_id": "openminion",
                        "session_id": "session-v1",
                        "input_text": "stream this",
                        "stream": True,
                    },
                    runtime=runtime,
                )
                self.assertEqual(int(status_stream), 200)
                self.assertTrue(stream_payload["ok"])
                self.assertIsInstance(stream_payload["chunks"], list)
                self.assertTrue(stream_payload["chunks"])

                status_evict, evict_payload = dispatch_request(
                    "POST",
                    "/v1/agents/openminion/evict",
                    str(config_path),
                    body={"reason": "test"},
                    runtime=runtime,
                )
                self.assertEqual(int(status_evict), 200)
                self.assertTrue(evict_payload["ok"])
                self.assertTrue(evict_payload["evicted"])
            finally:
                runtime.close()

    def test_v1_cancel_unknown_trace_and_kill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_runtimectl_echo_config(Path(tmp))
            runtime = APIRuntime.from_config_path(str(config_path))
            try:
                status_cancel, cancel_payload = dispatch_request(
                    "POST",
                    "/v1/turn/trace-missing/cancel",
                    str(config_path),
                    body={},
                    runtime=runtime,
                )
                self.assertEqual(int(status_cancel), 404)
                self.assertFalse(cancel_payload["ok"])
                self.assertEqual(cancel_payload["error"]["code"], "trace_not_found")

                status_kill, kill_payload = dispatch_request(
                    "POST",
                    "/v1/admin/kill",
                    str(config_path),
                    body={},
                    runtime=runtime,
                )
                self.assertEqual(int(status_kill), 200)
                self.assertTrue(kill_payload["ok"])
                self.assertEqual(kill_payload["status"], "stopped")
            finally:
                runtime.close()
