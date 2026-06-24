from __future__ import annotations

from tests.services.agent._agent_service_support import (
    AgentService,
    AgentServiceTestCase,
    CapturingProvider,
    FakeChangingToolCallProvider,
    FakeNoToolCallProvider,
    FakeSubstantiveToolMissingFinalizationProvider,
    FakeSubstantiveToolThenFinalizationProvider,
    FakeTextOnlyToolCallProvider,
    FakeTextToolCallProvider,
    FakeToolCallProvider,
    FakeTwoStepToolThenFinalProvider,
    LLMProvider,
    Message,
    OpenMinionConfig,
    PluginRegistry,
    ProviderRequest,
    ProviderResponse,
    SecurityPolicyEngine,
    SecurityPolicyRule,
    Tool,
    ToolBudgetPolicy,
    ToolExecutionContext,
    ToolExecutionResult,
    ToolRegistry,
    _BudgetWeatherTool,
    _StubSearchTool,
    _StubWeatherTool,
    asyncio,
    json,
    logging,
)
from tests._csc_fixtures import _csc_install_default_agent
from openminion.modules.llm.providers.base import ProviderToolCall
from openminion.modules.tool.registry import ToolExecutionBatch


class _IdBearingToolCallProvider(LLMProvider):
    name = "fake-id-tools"

    def __init__(self, *calls: ProviderToolCall) -> None:
        self._calls = list(calls)

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        del request
        return ProviderResponse(
            text="",
            model="fake-model",
            tool_calls=list(self._calls),
            finish_reason="tool_calls",
        )


class _ReasonAliasFinalizationProvider(LLMProvider):
    name = "fake-reason-alias-finalization"

    def __init__(self) -> None:
        self.call_count = 0

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        del request
        self.call_count += 1
        if self.call_count == 1:
            return ProviderResponse(
                text="",
                model="fake-model",
                tool_calls=[
                    ProviderToolCall(
                        name="weather.openmeteo.current",
                        arguments={"city": "Tokyo"},
                        source="fallback",
                    ),
                    ProviderToolCall(
                        name="weather.openmeteo.current",
                        arguments={"city": "Osaka"},
                        source="fallback",
                    ),
                    ProviderToolCall(
                        name="weather.openmeteo.current",
                        arguments={"city": "Kyoto"},
                        source="fallback",
                    ),
                ],
                finish_reason="tool_calls",
            )
        return ProviderResponse(
            text=(
                "Delivered the final comparison.\n"
                '<finalization_status>{"status":"final_answer","reason":"alias accepted"}</finalization_status>'
            ),
            model="fake-model",
            finish_reason="stop",
        )


class _EmbeddedEnvelopeFollowUpProvider(LLMProvider):
    name = "fake-embedded-envelope-follow-up"

    def __init__(self) -> None:
        self.call_count = 0
        self.requests: list[ProviderRequest] = []

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.call_count += 1
        self.requests.append(request)
        if self.call_count == 1:
            return ProviderResponse(
                text="",
                model="fake-model",
                tool_calls=[
                    ProviderToolCall(
                        name="weather.openmeteo.current",
                        arguments={"city": "Tokyo"},
                        source="fallback",
                    ),
                    ProviderToolCall(
                        name="weather.openmeteo.current",
                        arguments={"city": "Osaka"},
                        source="fallback",
                    ),
                    ProviderToolCall(
                        name="weather.openmeteo.current",
                        arguments={"city": "Kyoto"},
                        source="fallback",
                    ),
                ],
                finish_reason="tool_calls",
            )
        if self.call_count == 2:
            return ProviderResponse(
                text="[system: UNEXECUTABLE_TOOL_ENVELOPE]\nblocked",
                model="fake-model",
                finish_reason="stop",
            )
        plain_text_retry_seen = (
            "plain-text answer only" in str(request.user_message or "").lower()
        )
        if not plain_text_retry_seen:
            for item in list(request.history or []):
                if (
                    "plain-text answer only"
                    in str(getattr(item, "content", "") or "").lower()
                ):
                    plain_text_retry_seen = True
                    break
        if not plain_text_retry_seen:
            return ProviderResponse(
                text="[system: UNEXECUTABLE_TOOL_ENVELOPE]\nblocked again",
                model="fake-model",
                finish_reason="stop",
            )
        return ProviderResponse(
            text=(
                "SOURCES\n- Tokyo, Osaka, and Kyoto weather snapshots.\n\n"
                "CHANGES\n- Used the existing tool results to answer in plain text.\n\n"
                "TESTS\n- No additional tests required.\n"
                '<finalization_status>{"status":"final_answer","reasoning":"The existing tool results fully answered the request.","remaining_work":"","blocking_reason":""}</finalization_status>'
            ),
            model="fake-model",
            finish_reason="stop",
        )


class AgentServiceExecutionTests(AgentServiceTestCase):
    def test_tool_batch_metadata_keeps_small_tool_output_inline(self) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config)  # type: ignore[attr-defined]
        service = AgentService(
            config,
            PluginRegistry([]),
            FakeNoToolCallProvider(),
            logging.getLogger("openminion.tests"),
        )
        batch = ToolExecutionBatch(
            results=[
                ToolExecutionResult(
                    tool_name="exec.run",
                    ok=True,
                    verified=True,
                    content="short output",
                    data={},
                    call_id="call-1",
                )
            ]
        )

        metadata = service._tool_batch_metadata(batch=batch, tool_calls_count=1)
        payload = json.loads(metadata["tool_results"])

        self.assertEqual(payload[0]["content"], "short output")
        self.assertNotIn("tool_output_frame", payload[0])

    def test_tool_batch_metadata_keeps_unbacked_large_tool_output_inline(self) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config)  # type: ignore[attr-defined]
        service = AgentService(
            config,
            PluginRegistry([]),
            FakeNoToolCallProvider(),
            logging.getLogger("openminion.tests"),
        )
        large_output = "x" * 9000
        batch = ToolExecutionBatch(
            results=[
                ToolExecutionResult(
                    tool_name="exec.run",
                    ok=True,
                    verified=True,
                    content=large_output,
                    data={},
                    call_id="call-1",
                )
            ]
        )

        metadata = service._tool_batch_metadata(batch=batch, tool_calls_count=1)
        payload = json.loads(metadata["tool_results"])

        self.assertEqual(payload[0]["content"], large_output)
        self.assertNotIn("tool_output_frame", payload[0])

    def test_tool_batch_metadata_frames_artifact_backed_large_tool_output(self) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config)  # type: ignore[attr-defined]
        service = AgentService(
            config,
            PluginRegistry([]),
            FakeNoToolCallProvider(),
            logging.getLogger("openminion.tests"),
        )
        large_output = "x" * 9000
        batch = ToolExecutionBatch(
            results=[
                ToolExecutionResult(
                    tool_name="exec.run",
                    ok=True,
                    verified=True,
                    content=large_output,
                    data={
                        "artifact_refs": [
                            {"ref": "artifact://sha256/abc", "role": "stdout"}
                        ]
                    },
                    call_id="call-1",
                )
            ]
        )

        metadata = service._tool_batch_metadata(batch=batch, tool_calls_count=1)
        payload = json.loads(metadata["tool_results"])

        self.assertLess(len(payload[0]["content"]), 100)
        frame = payload[0]["tool_output_frame"]
        self.assertEqual(frame["kind"], "artifact_backed_tool_output")
        self.assertEqual(frame["original_chars"], 9000)
        self.assertEqual(
            frame["artifact_refs"],
            [{"ref": "artifact://sha256/abc", "role": "stdout"}],
        )

    def test_tool_calls_are_projected_to_response_metadata(self) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config)  # type: ignore[attr-defined]
        registry = PluginRegistry([])
        service = AgentService(
            config,
            registry,
            FakeToolCallProvider(),
            logging.getLogger("openminion.tests"),
        )

        response = asyncio.run(
            service.run_turn(Message(channel="console", target="me", body="hello"))
        )
        self.assertIn("Tool call requested", response.text)
        self.assertEqual(response.metadata["provider"], "fake-tools")
        self.assertEqual(response.metadata["finish_reason"], "tool_calls")
        self.assertEqual(response.metadata["tool_calls_count"], "1")
        self.assertIn("weather.openmeteo.current", response.metadata["tool_calls"])

    def test_tool_calls_execute_with_tool_registry(self) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config)  # type: ignore[attr-defined]
        registry = PluginRegistry([])
        tools = ToolRegistry([_StubWeatherTool()])
        service = AgentService(
            config,
            registry,
            FakeToolCallProvider(),
            logging.getLogger("openminion.tests"),
            tools=tools,
        )

        response = asyncio.run(
            service.run_turn(
                Message(
                    channel="console",
                    target="me",
                    body="weather test for san francisco and tokyo",
                )
            )
        )
        self.assertIn("Tokyo weather now", response.text)
        self.assertEqual(response.metadata["tool_calls_count"], "1")
        self.assertEqual(response.metadata["tool_execution_count"], "1")
        self.assertEqual(response.metadata["tool_verified"], "true")
        self.assertIn("weather.openmeteo.current", response.metadata["tool_results"])

    def test_tool_calls_execute_when_provider_text_is_tool_call_envelope(self) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config)  # type: ignore[attr-defined]
        registry = PluginRegistry([])
        tools = ToolRegistry([_StubWeatherTool()])
        service = AgentService(
            config,
            registry,
            FakeTextToolCallProvider(),
            logging.getLogger("openminion.tests"),
            tools=tools,
        )

        response = asyncio.run(
            service.run_turn(
                Message(
                    channel="console",
                    target="me",
                    body="search current top trending news and summarize top 3",
                )
            )
        )
        self.assertIn("Tokyo weather now", response.text)
        self.assertNotIn('{"tool_calls"', response.text)
        self.assertEqual(response.metadata["tool_execution_count"], "1")

    def test_tool_calls_execute_when_provider_returns_text_only_tool_call(self) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config)  # type: ignore[attr-defined]
        registry = PluginRegistry([])
        tools = ToolRegistry([_StubWeatherTool()])
        service = AgentService(
            config,
            registry,
            FakeTextOnlyToolCallProvider(),
            logging.getLogger("openminion.tests"),
            tools=tools,
        )

        response = asyncio.run(
            service.run_turn(
                Message(
                    channel="console",
                    target="me",
                    body="what is weather in tokyo",
                )
            )
        )
        self.assertIn('"name":"weather.openmeteo.current"', response.text)
        self.assertNotIn("tool_execution_count", response.metadata)

    def test_tool_call_payload_shape_matches_between_native_and_fallback_paths(
        self,
    ) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config)  # type: ignore[attr-defined]
        registry = PluginRegistry([])
        tools = ToolRegistry([_StubWeatherTool()])

        native_service = AgentService(
            config,
            registry,
            FakeToolCallProvider(),
            logging.getLogger("openminion.tests"),
            tools=tools,
        )
        fallback_service = AgentService(
            config,
            registry,
            FakeTextOnlyToolCallProvider(),
            logging.getLogger("openminion.tests"),
            tools=tools,
        )

        native_response = asyncio.run(
            native_service.run_turn(
                Message(channel="console", target="me", body="weather in tokyo")
            )
        )
        fallback_response = asyncio.run(
            fallback_service.run_turn(
                Message(channel="console", target="me", body="weather in tokyo")
            )
        )

        native_calls = json.loads(native_response.metadata.get("tool_calls", "[]"))
        fallback_calls = json.loads(fallback_response.metadata.get("tool_calls", "[]"))
        self.assertEqual(len(native_calls), 1)
        self.assertEqual(len(fallback_calls), 0)
        call = native_calls[0]
        self.assertEqual(
            set(call.keys()),
            {"id", "name", "arguments", "source", "depends_on"},
        )
        self.assertIsInstance(call["arguments"], dict)
        self.assertIsInstance(call["depends_on"], list)
        self.assertEqual(native_calls[0]["name"], "weather.openmeteo.current")

    def test_malformed_tool_envelope_is_blocked_without_raw_markup_leak(self) -> None:
        class FakeMalformedEnvelopeProvider(LLMProvider):
            name = "fake-malformed-envelope"

            async def generate(self, request: ProviderRequest) -> ProviderResponse:
                del request
                return ProviderResponse(
                    text='<|start|>assistant<|channel|>commentary to=tool.not_allowed <|message|>{"q":"x"}<|call|>',
                    model="fake-model",
                    finish_reason="stop",
                )

        config = OpenMinionConfig()
        _csc_install_default_agent(config)  # type: ignore[attr-defined]
        registry = PluginRegistry([])
        tools = ToolRegistry([_StubWeatherTool()])
        service = AgentService(
            config,
            registry,
            FakeMalformedEnvelopeProvider(),
            logging.getLogger("openminion.tests"),
            tools=tools,
        )

        response = asyncio.run(
            service.run_turn(
                Message(
                    channel="console",
                    target="me",
                    body="show latest weather update",
                )
            )
        )
        self.assertIn("UNEXECUTABLE_TOOL_ENVELOPE", response.text)
        self.assertIn("Reason: unparseable", response.text)
        self.assertNotIn("<|start|>", response.text)
        self.assertNotIn("<|channel|>", response.text)
        self.assertEqual(response.metadata.get("provider"), "fake-malformed-envelope")

    def test_multi_step_loop_runs_follow_up_inference_after_tool_execution(
        self,
    ) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config)  # type: ignore[attr-defined]
        config.runtime.agent_loop_max_steps = 4
        registry = PluginRegistry([])
        tools = ToolRegistry([_StubWeatherTool()])
        provider = FakeTwoStepToolThenFinalProvider()
        service = AgentService(
            config,
            registry,
            provider,
            logging.getLogger("openminion.tests"),
            tools=tools,
        )

        response = asyncio.run(
            service.run_turn(Message(channel="console", target="me", body="do work"))
        )
        self.assertIn("Final answer after tool execution", response.text)
        self.assertEqual(provider.call_count, 2)
        self.assertEqual(response.metadata["inference_steps"], "2")
        self.assertEqual(response.metadata["tool_execution_count"], "1")
        self.assertEqual(
            response.metadata["tool_loop_termination_reason"], "model_final"
        )
        self.assertTrue(
            any(
                "Tool execution results" in history_item.content
                for request in provider.requests[1:]
                for history_item in request.history
            )
        )
        self.assertTrue(
            any(
                "same turn" in str(request.user_message).lower()
                for request in provider.requests[1:]
            )
        )
        self.assertTrue(
            any(
                "not the final answer" in history_item.content
                for request in provider.requests[1:]
                for history_item in request.history
                if str(getattr(history_item, "role", "")).lower() == "assistant"
            )
        )

    def test_multi_step_loop_records_typed_finalization_status_for_substantive_work(
        self,
    ) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config)  # type: ignore[attr-defined]
        config.runtime.agent_loop_max_steps = 4
        registry = PluginRegistry([])
        tools = ToolRegistry([_StubWeatherTool()])
        provider = FakeSubstantiveToolThenFinalizationProvider()
        service = AgentService(
            config,
            registry,
            provider,
            logging.getLogger("openminion.tests"),
            tools=tools,
        )

        response = asyncio.run(
            service.run_turn(Message(channel="console", target="me", body="do work"))
        )
        self.assertEqual(provider.call_count, 2)
        self.assertEqual(response.metadata["tool_execution_count"], "3")
        self.assertEqual(
            response.metadata["tool_loop_termination_reason"], "model_final"
        )
        self.assertIn("Delivered the final comparison.", response.text)
        self.assertIn(
            '"status": "final_answer"',
            response.metadata["finalization_status"],
        )

    def test_multi_step_loop_accepts_reason_alias_in_finalization_trailer(
        self,
    ) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config)  # type: ignore[attr-defined]
        config.runtime.agent_loop_max_steps = 4
        registry = PluginRegistry([])
        tools = ToolRegistry([_StubWeatherTool()])
        provider = _ReasonAliasFinalizationProvider()
        service = AgentService(
            config,
            registry,
            provider,
            logging.getLogger("openminion.tests"),
            tools=tools,
        )

        response = asyncio.run(
            service.run_turn(Message(channel="console", target="me", body="do work"))
        )

        self.assertEqual(provider.call_count, 2)
        self.assertEqual(response.metadata["tool_execution_count"], "3")
        self.assertEqual(
            response.metadata["tool_loop_termination_reason"], "model_final"
        )
        self.assertIn("Delivered the final comparison.", response.text)
        self.assertIn(
            '"status": "final_answer"', response.metadata["finalization_status"]
        )
        self.assertIn(
            '"reasoning": "alias accepted"', response.metadata["finalization_status"]
        )

    def test_multi_step_loop_retries_once_when_substantive_finalization_is_missing(
        self,
    ) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config)  # type: ignore[attr-defined]
        config.runtime.agent_loop_max_steps = 4
        registry = PluginRegistry([])
        tools = ToolRegistry([_StubWeatherTool()])
        provider = FakeSubstantiveToolMissingFinalizationProvider()
        service = AgentService(
            config,
            registry,
            provider,
            logging.getLogger("openminion.tests"),
            tools=tools,
        )

        response = asyncio.run(
            service.run_turn(Message(channel="console", target="me", body="do work"))
        )
        self.assertEqual(provider.call_count, 3)
        self.assertEqual(response.metadata["tool_execution_count"], "3")
        self.assertIn(
            '"status": "final_answer"',
            response.metadata["finalization_status"],
        )
        self.assertTrue(
            any(
                "required typed <finalization_status>" in request.user_message
                for request in provider.requests[1:]
            )
        )

    def test_multi_step_loop_retries_plain_text_when_follow_up_leaks_envelope(
        self,
    ) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config)  # type: ignore[attr-defined]
        config.runtime.agent_loop_max_steps = 4
        registry = PluginRegistry([])
        tools = ToolRegistry([_StubWeatherTool()])
        provider = _EmbeddedEnvelopeFollowUpProvider()
        service = AgentService(
            config,
            registry,
            provider,
            logging.getLogger("openminion.tests"),
            tools=tools,
        )

        response = asyncio.run(
            service.run_turn(Message(channel="console", target="me", body="do work"))
        )

        self.assertEqual(provider.call_count, 3)
        self.assertEqual(response.metadata["tool_execution_count"], "3")
        self.assertNotIn("UNEXECUTABLE_TOOL_ENVELOPE", response.text)
        self.assertIn("SOURCES", response.text)
        self.assertIn(
            '"status": "final_answer"', response.metadata["finalization_status"]
        )
        self.assertTrue(
            any(
                "plain-text answer only"
                in (
                    str(request.user_message or "").lower()
                    + "\n"
                    + str(getattr(history_item, "content", "") or "").lower()
                )
                for request in provider.requests[1:]
                for history_item in getattr(request, "history", []) or []
            )
        )

    def test_multi_step_loop_stops_on_duplicate_tool_calls(self) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config)  # type: ignore[attr-defined]
        config.runtime.agent_loop_max_steps = 4
        registry = PluginRegistry([])
        tools = ToolRegistry([_StubWeatherTool()])
        provider = FakeToolCallProvider()
        service = AgentService(
            config,
            registry,
            provider,
            logging.getLogger("openminion.tests"),
            tools=tools,
        )

        response = asyncio.run(
            service.run_turn(Message(channel="console", target="me", body="weather"))
        )
        self.assertEqual(response.metadata["tool_execution_count"], "1")
        # AR-05 gives the model one explicit replan opportunity before
        # preserving duplicate-tool-call termination.
        self.assertEqual(response.metadata["inference_steps"], "3")
        self.assertEqual(
            response.metadata["tool_loop_termination_reason"], "duplicate_tool_calls"
        )

    def test_multi_step_loop_carries_budget_across_steps(self) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config)  # type: ignore[attr-defined]
        config.runtime.agent_loop_max_steps = 4
        registry = PluginRegistry([])
        tools = ToolRegistry([_BudgetWeatherTool()])
        provider = FakeChangingToolCallProvider()
        policy = SecurityPolicyEngine(
            tool_budget_policy=ToolBudgetPolicy(
                max_calls_per_run=4,
                max_calls_per_tool=4,
                max_budget_cost_per_run=3,
            )
        )
        service = AgentService(
            config,
            registry,
            provider,
            logging.getLogger("openminion.tests"),
            tools=tools,
            security_policy=policy,
        )

        response = asyncio.run(
            service.run_turn(
                Message(channel="console", target="me", body="weather both")
            )
        )
        self.assertEqual(provider.call_count, 2)
        self.assertEqual(response.metadata["tool_execution_count"], "1")
        self.assertIn("tool_budget_cost_exceeded", response.metadata["tool_results"])
        self.assertIn('"tool_calls_total": 1', response.metadata["tool_budget"])
        self.assertEqual(
            response.metadata["tool_loop_termination_reason"], "tool_no_success"
        )

    def test_forced_tools_sets_provider_tool_choice_required(self) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config)  # type: ignore[attr-defined]
        registry = PluginRegistry([])
        provider = CapturingProvider()
        tools = ToolRegistry([_StubWeatherTool()])
        service = AgentService(
            config,
            registry,
            provider,
            logging.getLogger("openminion.tests"),
            tools=tools,
        )

        asyncio.run(
            service.run_turn(
                Message(channel="console", target="me", body="latest weather"),
                forced_tools=["weather.openmeteo.current"],
            )
        )
        if provider.last_request is None:
            self.fail("Expected request to be captured")
        self.assertEqual(provider.last_request.tool_choice, "required")
        self.assertEqual(
            [tool.name for tool in provider.last_request.tools],
            ["weather.openmeteo.current"],
        )

    def test_explicit_tool_syntax_sets_provider_tool_choice_required(self) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config)  # type: ignore[attr-defined]
        registry = PluginRegistry([])
        provider = CapturingProvider()
        tools = ToolRegistry([_StubSearchTool()])
        service = AgentService(
            config,
            registry,
            provider,
            logging.getLogger("openminion.tests"),
            tools=tools,
        )

        asyncio.run(
            service.run_turn(
                Message(
                    channel="console",
                    target="me",
                    body='tool web.search {"query":"latest news on korea"}',
                )
            )
        )
        if provider.last_request is None:
            self.fail("Expected request to be captured")
        self.assertEqual(provider.last_request.tool_choice, "required")
        self.assertEqual(
            [tool.name for tool in provider.last_request.tools], ["web.search"]
        )

    def test_forced_tools_blocks_when_tool_unavailable(self) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config)  # type: ignore[attr-defined]
        registry = PluginRegistry([])
        provider = CapturingProvider()
        tools = ToolRegistry([_StubWeatherTool()])
        service = AgentService(
            config,
            registry,
            provider,
            logging.getLogger("openminion.tests"),
            tools=tools,
        )

        response = asyncio.run(
            service.run_turn(
                Message(channel="console", target="me", body="latest weather"),
                forced_tools=["web.search"],
            )
        )
        self.assertEqual(response.text, "Required tool unavailable")
        self.assertEqual(
            response.metadata.get("tool_loop_termination_reason"),
            "forced_tool_unavailable",
        )
        self.assertIsNone(provider.last_request)

    def test_forced_search_synthesizes_tool_call_when_provider_returns_text_only(
        self,
    ) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config)  # type: ignore[attr-defined]
        config.runtime.tool_selection.allow_runtime_direct_fallback = True
        registry = PluginRegistry([])
        provider = FakeNoToolCallProvider()
        tools = ToolRegistry([_StubSearchTool()])
        service = AgentService(
            config,
            registry,
            provider,
            logging.getLogger("openminion.tests"),
            tools=tools,
        )

        response = asyncio.run(
            service.run_turn(
                Message(channel="console", target="me", body="latest news on iran"),
                forced_tools=["web.search"],
            )
        )
        self.assertEqual(response.text, "Required tool call missing")
        self.assertEqual(response.metadata.get("tool_execution_count"), "0")
        self.assertEqual(
            response.metadata.get("tool_loop_termination_reason"),
            "required_tool_call_missing",
        )

    def test_explicit_tool_syntax_no_longer_executes_runtime_cancel_when_provider_returns_no_tool_calls(
        self,
    ) -> None:
        class _StubTaskCancelTool(Tool):
            name = "task.cancel"
            description = "Cancel a scheduled task"
            parameters = {
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
            }

            def execute(
                self, arguments, context: ToolExecutionContext
            ) -> ToolExecutionResult:
                del context
                task_id = str(arguments.get("task_id", "") or "").strip()
                return ToolExecutionResult(
                    tool_name=self.name,
                    ok=True,
                    verified=True,
                    content=f"Cancelled task {task_id}",
                    data={
                        "task_id": task_id,
                        "cancelled": True,
                        "task_cancelled": True,
                    },
                    source="task-cancel-stub",
                )

        config = OpenMinionConfig()
        _csc_install_default_agent(config)  # type: ignore[attr-defined]
        config.runtime.tool_selection.allow_runtime_direct_fallback = True
        registry = PluginRegistry([])
        provider = FakeNoToolCallProvider()
        tools = ToolRegistry([_StubTaskCancelTool()])
        service = AgentService(
            config,
            registry,
            provider,
            logging.getLogger("openminion.tests"),
            tools=tools,
        )

        response = asyncio.run(
            service.run_turn(
                Message(
                    channel="console",
                    target="me",
                    body='tool task.cancel {"task_id":"job-123"}',
                )
            )
        )
        self.assertEqual(provider.call_count, 2)
        self.assertEqual(response.text, "Required tool call missing")
        self.assertEqual(response.metadata.get("tool_execution_count"), "0")
        self.assertEqual(
            response.metadata.get("tool_loop_termination_reason"),
            "required_tool_call_missing",
        )

    def test_tool_calls_policy_denied_returns_error_result(self) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config)  # type: ignore[attr-defined]
        registry = PluginRegistry([])
        tools = ToolRegistry([_StubWeatherTool()])
        restrictive_policy = SecurityPolicyEngine(
            rules={
                ("tool", "execute"): SecurityPolicyRule(
                    required_scopes_any=frozenset({"never.allow"}),
                )
            }
        )
        service = AgentService(
            config,
            registry,
            _IdBearingToolCallProvider(
                ProviderToolCall(
                    id="call-weather-1",
                    name="weather.openmeteo.current",
                    arguments={"city": "Tokyo"},
                    source="native",
                )
            ),
            logging.getLogger("openminion.tests"),
            tools=tools,
            security_policy=restrictive_policy,
        )

        response = asyncio.run(
            service.run_turn(
                Message(channel="console", target="me", body="weather please")
            )
        )
        self.assertIn("Tool `weather.openmeteo.current` was blocked", response.text)
        self.assertIn("missing_scope", response.text)
        self.assertEqual(response.metadata["tool_execution_count"], "1")
        self.assertEqual(response.metadata["tool_verified"], "false")
        self.assertIn("security_deny", response.metadata["tool_results"])
        tool_results = json.loads(response.metadata.get("tool_results", "[]"))
        self.assertTrue(tool_results)
        self.assertEqual(tool_results[0].get("id"), "call-weather-1")
        self.assertEqual(tool_results[0].get("call_id"), "call-weather-1")
        self.assertEqual(tool_results[0].get("name"), "weather.openmeteo.current")
        self.assertEqual(tool_results[0].get("tool_name"), "weather.openmeteo.current")
        self.assertEqual(tool_results[0].get("status"), "blocked")
        self.assertEqual(tool_results[0].get("error_code"), "missing_scope")
        self.assertEqual(tool_results[0].get("reason_code"), "missing_scope")
        self.assertEqual(
            tool_results[0].get("data", {}).get("reason_code"), "missing_scope"
        )
        security_events = json.loads(response.metadata.get("security_events", "[]"))
        self.assertEqual(security_events[0].get("call_id"), "call-weather-1")
        self.assertEqual(
            security_events[0].get("tool_name"), "weather.openmeteo.current"
        )
        self.assertEqual(security_events[0].get("reason_code"), "missing_scope")

    def test_tool_calls_budget_denied_returns_error_result(self) -> None:
        config = OpenMinionConfig()
        _csc_install_default_agent(config)  # type: ignore[attr-defined]
        registry = PluginRegistry([])
        tools = ToolRegistry([_BudgetWeatherTool()])
        policy = SecurityPolicyEngine(
            tool_budget_policy=ToolBudgetPolicy(
                max_calls_per_run=4,
                max_calls_per_tool=4,
                max_budget_cost_per_run=3,
            )
        )
        service = AgentService(
            config,
            registry,
            _IdBearingToolCallProvider(
                ProviderToolCall(
                    id="call-weather-1",
                    name="weather.openmeteo.current",
                    arguments={"city": "San Francisco"},
                    source="native",
                ),
                ProviderToolCall(
                    id="call-weather-2",
                    name="weather.openmeteo.current",
                    arguments={"city": "Tokyo"},
                    source="native",
                ),
            ),
            logging.getLogger("openminion.tests"),
            tools=tools,
            security_policy=policy,
        )

        response = asyncio.run(
            service.run_turn(
                Message(channel="console", target="me", body="weather both")
            )
        )
        self.assertIn("Tool `weather.openmeteo.current` was blocked", response.text)
        self.assertEqual(response.metadata["tool_execution_count"], "2")
        self.assertIn("tool_budget_cost_exceeded", response.metadata["tool_results"])
        self.assertIn("security_events", response.metadata)
        self.assertIn("policy_denied", response.metadata["security_events"])
        self.assertIn('"tool_calls_total": 1', response.metadata["tool_budget"])
        tool_results = json.loads(response.metadata.get("tool_results", "[]"))
        self.assertEqual(tool_results[1].get("id"), "call-weather-2")
        self.assertEqual(tool_results[1].get("status"), "blocked")
        self.assertEqual(tool_results[1].get("error_code"), "tool_budget_cost_exceeded")
        self.assertEqual(
            tool_results[1].get("reason_code"), "tool_budget_cost_exceeded"
        )
        security_events = json.loads(response.metadata.get("security_events", "[]"))
        self.assertEqual(security_events[0].get("call_id"), "call-weather-2")
        self.assertEqual(
            security_events[0].get("tool_name"), "weather.openmeteo.current"
        )
        self.assertEqual(
            security_events[0].get("reason_code"), "tool_budget_cost_exceeded"
        )
