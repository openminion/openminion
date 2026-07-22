import asyncio
import json
import logging
import threading
import pytest

from openminion.base.config import OpenMinionConfig
from openminion.base.types import Message
from openminion.services.runtime.plugins import PluginRegistry
from openminion.modules.llm.providers.base import (
    LLMProvider,
    ProviderRequest,
    ProviderResponse,
    ProviderToolCall,
    ProviderToolSpec,
)
from openminion.services.agent import AgentService
from openminion.modules.tool.base import (
    Tool,
    ToolCategoryInfo,
    ToolExecutionContext,
    ToolExecutionResult,
)
from openminion.modules.tool import build_default_tool_registry
from openminion.modules.tool.registry import ToolRegistry
from tests._csc_fixtures import _csc_install_default_agent


def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict[str, object] = {}
    error: dict[str, BaseException] = {}

    def _worker() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:  # pragma: no cover - error forwarding
            error["exc"] = exc

    worker = threading.Thread(target=_worker, daemon=True)
    worker.start()
    worker.join()
    if "exc" in error:
        raise error["exc"]
    return result.get("value")


class _WeatherTool(Tool):
    name = "weather.openmeteo.current"
    description = "Get current weather by location."
    parameters = {
        "type": "object",
        "properties": {"location": {"type": "string"}},
        "required": ["location"],
        "additionalProperties": False,
    }
    categories = ToolCategoryInfo(primary_category="weather")

    def execute(self, arguments, context: ToolExecutionContext) -> ToolExecutionResult:
        del arguments, context
        return ToolExecutionResult(
            tool_name=self.name,
            ok=True,
            content="Sunny",
            verified=True,
        )


class _MissingArgProvider(LLMProvider):
    name = "missing-arg-provider"

    def __init__(self) -> None:
        self.calls: list[ProviderRequest] = []

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        # Always omit required "location" to force retry and then exhaustion.
        return ProviderResponse(
            text="",
            model="fake-model",
            tool_calls=[
                ProviderToolCall(
                    name="weather.openmeteo.current",
                    arguments={},
                    source="native",
                )
            ],
            finish_reason="tool_calls",
        )


class _MissingArgThenTextProvider(LLMProvider):
    name = "missing-arg-then-text-provider"

    def __init__(self) -> None:
        self.calls: list[ProviderRequest] = []

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        if request.tools and len(self.calls) <= 2:
            return ProviderResponse(
                text="",
                model="fake-model",
                tool_calls=[
                    ProviderToolCall(
                        name="weather.openmeteo.current",
                        arguments={},
                        source="native",
                    )
                ],
                finish_reason="tool_calls",
            )
        return ProviderResponse(
            text="should-not-be-used", model="fake-model", finish_reason="stop"
        )


class _InvalidArgProvider(LLMProvider):
    def __init__(
        self,
        *,
        provider_name: str,
        arguments,
        tool_name: str = "weather.openmeteo.current",
    ) -> None:
        self.name = provider_name
        self.calls: list[ProviderRequest] = []
        self._arguments = arguments
        self._tool_name = tool_name

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        return ProviderResponse(
            text="",
            model=f"{self.name}-model",
            tool_calls=[
                ProviderToolCall(
                    name=self._tool_name,
                    arguments=self._arguments,
                    source="native",
                )
            ],
            finish_reason="tool_calls",
        )


class _TwoStepSearchProvider(LLMProvider):
    name = "two-step-search"

    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.calls += 1
        if self.calls == 1:
            return ProviderResponse(
                text="",
                model="fake-model",
                tool_calls=[
                    ProviderToolCall(
                        name="search.dispatch",
                        arguments={"query": "latest news on iran"},
                        source="native",
                    )
                ],
                finish_reason="tool_calls",
            )
        return ProviderResponse(
            text="Here are the latest headlines.",
            model="fake-model",
            finish_reason="stop",
        )


class _NoToolSearchProvider(LLMProvider):
    name = "no-tool-search"

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        del request
        return ProviderResponse(
            text="I cannot browse right now.",
            model="fake-model",
            finish_reason="stop",
        )


class _CapturingStopProvider(LLMProvider):
    name = "capturing-stop"

    def __init__(self) -> None:
        self.calls: list[ProviderRequest] = []

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        return ProviderResponse(
            text="No tool call emitted.",
            model="fake-model",
            finish_reason="stop",
        )


class _NoToolThenToolProvider(LLMProvider):
    name = "no-tool-then-tool"

    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.calls += 1
        if self.calls == 1:
            return ProviderResponse(
                text="I cannot browse right now.",
                model="fake-model",
                finish_reason="stop",
            )
        return ProviderResponse(
            text="",
            model="fake-model",
            tool_calls=[
                ProviderToolCall(
                    name="search.dispatch",
                    arguments={"query": "latest news on iran"},
                    source="native",
                )
            ],
            finish_reason="tool_calls",
        )


class _SingleToolProvider(LLMProvider):
    def __init__(self, tool_name: str, arguments: dict[str, str] | None = None) -> None:
        self.tool_name = tool_name
        self.arguments = arguments or {}

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        tool_name = self.tool_name
        if request.tools:
            first = request.tools[0]
            tool_name = getattr(first, "name", tool_name) or tool_name
        return ProviderResponse(
            text="",
            model="fake-model",
            tool_calls=[
                ProviderToolCall(
                    name=tool_name,
                    arguments=dict(self.arguments) or {"location": "tokyo"},
                    source="native",
                )
            ],
            finish_reason="tool_calls",
        )


class _ToolThenFinalProvider(LLMProvider):
    def __init__(self, arguments: dict[str, str] | None = None) -> None:
        self.arguments = arguments or {}
        self.calls: list[ProviderRequest] = []

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        if request.tools and len(self.calls) <= 2:
            tool_name = getattr(request.tools[0], "name", "") or ""
            return ProviderResponse(
                text="",
                model="fake-model",
                tool_calls=[
                    ProviderToolCall(
                        name=tool_name,
                        arguments=dict(self.arguments) or {"location": "tokyo"},
                        source="native",
                    )
                ],
                finish_reason="tool_calls",
            )
        return ProviderResponse(
            text=(
                "Fallback weather completed.\n"
                '<finalization_status>{"status":"final_answer","reasoning":"secondary tool completed the weather lookup","remaining_work":"","blocking_reason":""}</finalization_status>'
            ),
            model="fake-model",
            finish_reason="stop",
        )


class _ExecPolicyRecoveryTool(Tool):
    name = "exec.run"
    description = "Execute a shell command."
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "workdir": {"type": "string"},
        },
        "required": ["command"],
        "additionalProperties": False,
    }
    categories = ToolCategoryInfo(primary_category="system.exec")

    def execute(self, arguments, context: ToolExecutionContext) -> ToolExecutionResult:
        del context
        command = str(arguments.get("command", "")).strip()
        if command.startswith("cd "):
            return ToolExecutionResult(
                tool_name=self.name,
                ok=False,
                content="",
                error="Denied by policy: command 'cd' is not allowlisted",
                data={
                    "error_code": "POLICY_DENIED",
                    "error_details": {
                        "suggested_tool": "exec.run",
                        "suggested_fix": (
                            "Use exec.run with the actual command and pass the "
                            "target directory through workdir instead of prefixing "
                            "the command with `cd ... &&`."
                        ),
                    },
                },
                verified=False,
            )
        return ToolExecutionResult(
            tool_name=self.name,
            ok=True,
            content="Python 3.11.9",
            verified=True,
        )


class _ExecUnavailableTool(Tool):
    name = "exec.run"
    description = "Run a command in the workspace."
    parameters = _ExecPolicyRecoveryTool.parameters
    categories = ToolCategoryInfo(primary_category="system.exec")

    def execute(self, arguments, context: ToolExecutionContext) -> ToolExecutionResult:
        del context
        command = str(arguments.get("command", "")).strip()
        return ToolExecutionResult(
            tool_name=self.name,
            ok=False,
            content="command exited with code 127",
            error="command exited with code 127",
            data={
                "error_code": "EXEC_ERROR",
                "exit_code": 127,
                "stderr": "nasm: command not found",
                "command": command,
            },
            verified=False,
        )


class _DeniedThenRecoveredExecProvider(LLMProvider):
    name = "denied-then-recovered-exec"

    def __init__(self) -> None:
        self.calls: list[ProviderRequest] = []

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        history_text = "\n".join(
            str(message.content or "") for message in request.history
        )
        if "workdir instead of prefixing" in history_text:
            return ProviderResponse(
                text="",
                model="fake-model",
                tool_calls=[
                    ProviderToolCall(
                        name="exec.run",
                        arguments={
                            "command": "python3.11 --version",
                            "workdir": "repo",
                        },
                        source="native",
                    )
                ],
                finish_reason="tool_calls",
            )
        if request.tools:
            return ProviderResponse(
                text="",
                model="fake-model",
                tool_calls=[
                    ProviderToolCall(
                        name="exec.run",
                        arguments={
                            "command": "cd repo && python3 --version",
                        },
                        source="native",
                    )
                ],
                finish_reason="tool_calls",
            )
        return ProviderResponse(
            text=(
                "Python is available in the repo runtime.\n"
                '<finalization_status>{"status":"final_answer","reasoning":"The recovered exec.run result answered the version check.","remaining_work":"","blocking_reason":""}</finalization_status>'
            ),
            model="fake-model",
            finish_reason="stop",
        )


class _RepeatsUnavailableVersionProvider(LLMProvider):
    name = "repeats-unavailable-version"

    def __init__(self) -> None:
        self.calls: list[ProviderRequest] = []

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        if request.tools:
            return ProviderResponse(
                text="",
                model="fake-model",
                tool_calls=[
                    ProviderToolCall(
                        name="exec.run",
                        arguments={"command": "nasm --version"},
                        source="native",
                    )
                ],
                finish_reason="tool_calls",
            )
        return ProviderResponse(
            text="should-not-be-used",
            model="fake-model",
            finish_reason="stop",
        )


class _SearchTool(Tool):
    name = "search.dispatch"
    description = "Search current web information."
    parameters = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": False,
    }
    categories = ToolCategoryInfo(primary_category="search.news")

    def execute(self, arguments, context: ToolExecutionContext) -> ToolExecutionResult:
        del context
        query = str(arguments.get("query", "")).strip()
        return ToolExecutionResult(
            tool_name=self.name,
            ok=True,
            content=f"search ok: {query}",
            verified=True,
            data={"query": query},
            source="unit-test",
        )


class _FailPrimaryTool(Tool):
    name = "weather.primary"
    description = "Primary weather tool that fails"
    parameters = {
        "type": "object",
        "properties": {"location": {"type": "string"}},
        "required": ["location"],
    }
    categories = ToolCategoryInfo(primary_category="weather.current")

    def __init__(self, error_code: str) -> None:
        self.error_code = error_code

    def execute(self, arguments, context: ToolExecutionContext) -> ToolExecutionResult:
        del arguments, context
        return ToolExecutionResult(
            tool_name=self.name,
            ok=False,
            content="fail",
            verified=False,
            error="simulated failure",
            data={"error_code": self.error_code},
        )


class _FallbackWeatherTool(Tool):
    name = "weather.secondary"
    description = "Secondary weather tool that succeeds"
    parameters = {
        "type": "object",
        "properties": {"location": {"type": "string"}},
        "required": ["location"],
    }
    categories = ToolCategoryInfo(primary_category="weather.current")

    def execute(self, arguments, context: ToolExecutionContext) -> ToolExecutionResult:
        del context
        return ToolExecutionResult(
            tool_name=self.name,
            ok=True,
            content=f"secondary ok {arguments.get('location')}",
            verified=True,
        )


class _BrowserUnifiedTool(Tool):
    name = "browser"
    description = "Unified browser tool."
    parameters = {
        "type": "object",
        "properties": {
            "op": {"type": "string"},
            "url": {"type": "string"},
        },
        "required": ["op"],
        "additionalProperties": True,
    }
    categories = ToolCategoryInfo(primary_category="browser")

    def execute(self, arguments, context: ToolExecutionContext) -> ToolExecutionResult:
        del context
        op = str(arguments.get("op", "")).strip()
        url = str(arguments.get("url", "")).strip()
        if op in {"tab.navigate", "tab.open"} and url:
            content = f"opened {url}"
        else:
            content = op or "browser op executed"
        return ToolExecutionResult(
            tool_name=self.name,
            ok=True,
            content=content,
            verified=True,
            data={"op": op, "url": url},
        )


def test_tool_arg_retry_exhaustion_is_deterministic_and_non_crashing() -> None:
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.runtime.tool_selection.allow_runtime_direct_fallback = False
    provider = _MissingArgProvider()
    service = AgentService(
        config=config,
        plugins=PluginRegistry([]),
        provider=provider,
        logger=logging.getLogger("openminion.tests"),
        tools=ToolRegistry([_WeatherTool()]),
    )

    response = _run_async(
        service.run_turn(
            Message(
                channel="console", target="cli", body="what's weather in san francisco?"
            ),
            capability_category="weather",
        )
    )

    assert "Invalid tool arguments" in response.text
    assert len(provider.calls) == 2
    assert response.metadata.get("tool_loop_termination_reason") == "tool_arg_exhausted"
    assert response.metadata.get("tool_arg_exhausted") == "weather.openmeteo.current"
    assert "location" in response.metadata.get("tool_arg_exhausted_missing", "")


def test_required_tool_missing_args_are_repaired_from_bounded_weather_text() -> None:
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.runtime.tool_selection.allow_runtime_direct_fallback = True
    provider = _MissingArgThenTextProvider()
    service = AgentService(
        config=config,
        plugins=PluginRegistry([]),
        provider=provider,
        logger=logging.getLogger("openminion.tests"),
        tools=ToolRegistry([_WeatherTool()]),
    )

    response = _run_async(
        service.run_turn(
            Message(
                channel="console", target="cli", body="what's latest weather at sf?"
            ),
            capability_category="weather",
        )
    )

    assert response.text == "Invalid tool arguments"
    assert response.metadata.get("tool_execution_count") == "0"
    assert len(provider.calls) == 2


def test_agent_service_weather_extraction_helper_removed() -> None:
    assert not hasattr(AgentService, "_extract_weather_location")


def test_normalize_required_weather_arguments_preserves_model_arguments_verbatim() -> (
    None
):
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    service = AgentService(
        config=config,
        plugins=PluginRegistry([]),
        provider=_NoToolSearchProvider(),
        logger=logging.getLogger("openminion.tests"),
        tools=ToolRegistry([_WeatherTool()]),
    )
    normalized = service._normalize_required_tool_arguments(
        tool_name="weather.openmeteo.current",
        arguments={"location_name": "sf"},
    )
    assert normalized == {"location_name": "sf"}


def test_normalize_required_file_list_dir_arguments_preserves_freeform_alias_values() -> (
    None
):
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    service = AgentService(
        config=config,
        plugins=PluginRegistry([]),
        provider=_NoToolSearchProvider(),
        logger=logging.getLogger("openminion.tests"),
        tools=ToolRegistry([_WeatherTool()]),
    )
    normalized = service._normalize_required_tool_arguments(
        tool_name="file.list_dir",
        arguments={"path": "this folder"},
    )
    assert normalized.get("path") == "this folder"


def test_normalize_required_file_list_dir_arguments_keeps_current_dir_text_verbatim() -> (
    None
):
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    service = AgentService(
        config=config,
        plugins=PluginRegistry([]),
        provider=_NoToolSearchProvider(),
        logger=logging.getLogger("openminion.tests"),
        tools=ToolRegistry([_WeatherTool()]),
    )
    normalized = service._normalize_required_tool_arguments(
        tool_name="file.list_dir",
        arguments={"path": "current dir"},
    )
    assert normalized.get("path") == "current dir"


def test_normalize_required_browser_arguments_no_longer_rewrite_aliases() -> None:
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    service = AgentService(
        config=config,
        plugins=PluginRegistry([]),
        provider=_NoToolSearchProvider(),
        logger=logging.getLogger("openminion.tests"),
        tools=ToolRegistry([_WeatherTool()]),
    )
    normalized = service._normalize_required_tool_arguments(
        tool_name="browser.navigate",
        arguments={"url": "https://example.com"},
    )
    assert normalized == {"url": "https://example.com"}

    normalized_unified = service._normalize_required_tool_arguments(
        tool_name="browser",
        arguments={"url": "https://example.com"},
    )
    assert normalized_unified == {"url": "https://example.com"}


def test_sanitize_arguments_for_spec_drops_unknown_fields() -> None:
    spec = ProviderToolSpec(
        name="weather.openmeteo.current",
        description="",
        parameters={
            "type": "object",
            "properties": {
                "location": {"type": "string"},
            },
        },
    )
    sanitized = AgentService._sanitize_arguments_for_spec(
        arguments={"location": "san francisco", "units": "metric"},
        spec=spec,
    )
    assert sanitized == {"location": "san francisco"}


def test_forced_search_success_includes_tool_results_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_ALLOW_MODEL_EXPOSURE_PROVIDER_FALLBACK", "1")
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.runtime.tool_selection.bindings["web.search"] = "search.dispatch"

    provider = _TwoStepSearchProvider()
    service = AgentService(
        config=config,
        plugins=PluginRegistry([]),
        provider=provider,
        logger=logging.getLogger("openminion.tests"),
        tools=ToolRegistry([_SearchTool()]),
    )

    response = _run_async(
        service.run_turn(
            Message(
                channel="console", target="cli", body="what's latest news on iran?"
            ),
            capability_category="web.search",
        )
    )

    assert response.metadata.get("tool_execution_count") == "1"
    assert response.metadata.get("tool_calls_count") == "1"
    assert response.metadata.get("tool_verified") == "true"

    tool_results = json.loads(response.metadata.get("tool_results", "[]"))
    assert isinstance(tool_results, list)
    assert tool_results
    assert tool_results[0].get("tool_name") == "search.dispatch"
    assert tool_results[0].get("ok") is True


def test_forced_search_executes_direct_tool_when_provider_skips_tool_call() -> None:
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.runtime.tool_selection.bindings["web.search"] = "search.dispatch"
    config.runtime.tool_selection.allow_runtime_direct_fallback = True

    service = AgentService(
        config=config,
        plugins=PluginRegistry([]),
        provider=_NoToolSearchProvider(),
        logger=logging.getLogger("openminion.tests"),
        tools=ToolRegistry([_SearchTool()]),
    )

    response = _run_async(
        service.run_turn(
            Message(
                channel="console", target="cli", body="what's latest news on iran?"
            ),
            capability_category="web.search",
        )
    )

    assert response.text == "Required tool call missing"
    assert (
        response.metadata.get("tool_loop_termination_reason")
        == "required_tool_call_missing"
    )
    assert response.metadata.get("tool_execution_count") == "0"


def test_forced_search_requires_model_tool_call_by_default() -> None:
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.runtime.tool_selection.bindings["web.search"] = "search.dispatch"
    config.runtime.tool_selection.allow_runtime_direct_fallback = False

    service = AgentService(
        config=config,
        plugins=PluginRegistry([]),
        provider=_NoToolSearchProvider(),
        logger=logging.getLogger("openminion.tests"),
        tools=ToolRegistry([_SearchTool()]),
    )

    response = _run_async(
        service.run_turn(
            Message(
                channel="console", target="cli", body="what's latest news on iran?"
            ),
            capability_category="web.search",
        )
    )

    assert response.text == "Required tool call missing"
    assert (
        response.metadata.get("tool_loop_termination_reason")
        == "required_tool_call_missing"
    )
    assert response.metadata.get("tool_execution_count") == "0"


@pytest.mark.parametrize("forced_name", ["weather", "functions.weather"])
def test_forced_tool_provider_request_includes_visible_canonical_schema(
    forced_name: str,
) -> None:
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    provider = _CapturingStopProvider()
    service = AgentService(
        config=config,
        plugins=PluginRegistry([]),
        provider=provider,
        logger=logging.getLogger("openminion.tests"),
        tools=build_default_tool_registry(),
    )

    response = _run_async(
        service.run_turn(
            Message(channel="console", target="cli", body="check weather"),
            forced_tools=[forced_name],
        )
    )

    assert response.text == "Required tool call missing"
    assert len(provider.calls) == 2
    assert all(call.tool_choice == "required" for call in provider.calls)
    assert all([spec.name for spec in call.tools] == ["weather"] for call in provider.calls)


def test_forced_unknown_tool_exits_unavailable_before_provider_call() -> None:
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    provider = _CapturingStopProvider()
    service = AgentService(
        config=config,
        plugins=PluginRegistry([]),
        provider=provider,
        logger=logging.getLogger("openminion.tests"),
        tools=build_default_tool_registry(),
    )

    response = _run_async(
        service.run_turn(
            Message(channel="console", target="cli", body="use missing"),
            forced_tools=["missing.tool"],
        )
    )

    assert response.text == "Required tool unavailable"
    assert response.metadata.get("tool_loop_termination_reason") == (
        "forced_tool_unavailable"
    )
    assert provider.calls == []


def test_forced_hidden_tool_exits_unavailable_before_provider_call() -> None:
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    provider = _CapturingStopProvider()
    tools = build_default_tool_registry()
    service = AgentService(
        config=config,
        plugins=PluginRegistry([]),
        provider=provider,
        logger=logging.getLogger("openminion.tests"),
        tools=tools,
    )

    response = _run_async(
        service.run_turn(
            Message(channel="console", target="cli", body="cancel job"),
            forced_tools=["ops.job.cancel"],
        )
    )

    assert response.text == "Required tool unavailable"
    assert response.metadata.get("tool_loop_termination_reason") == (
        "forced_tool_unavailable"
    )
    assert provider.calls == []


def test_required_lane_retries_once_for_tool_call_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_ALLOW_MODEL_EXPOSURE_PROVIDER_FALLBACK", "1")
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.runtime.tool_selection.bindings["web.search"] = "search.dispatch"
    config.runtime.tool_selection.allow_runtime_direct_fallback = True

    provider = _NoToolThenToolProvider()
    service = AgentService(
        config=config,
        plugins=PluginRegistry([]),
        provider=provider,
        logger=logging.getLogger("openminion.tests"),
        tools=ToolRegistry([_SearchTool()]),
    )

    response = _run_async(
        service.run_turn(
            Message(
                channel="console", target="cli", body="what's latest news on iran?"
            ),
            capability_category="web.search",
        )
    )

    # Initial no-tool, repair retry, successful tool call, and typed finalization polish.
    assert provider.calls == 4
    assert response.metadata.get("tool_execution_count") == "1"
    assert response.metadata.get("capability_tool") in {"web.search", "search.dispatch"}


def test_required_lane_recovers_once_from_policy_denied_tool_with_structured_hint() -> (
    None
):
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.runtime.tool_selection.bindings["system.exec"] = "exec.run"
    config.runtime.tool_selection.allow_runtime_direct_fallback = False

    provider = _DeniedThenRecoveredExecProvider()
    service = AgentService(
        config=config,
        plugins=PluginRegistry([]),
        provider=provider,
        logger=logging.getLogger("openminion.tests"),
        tools=ToolRegistry([_ExecPolicyRecoveryTool()]),
    )

    response = _run_async(
        service.run_turn(
            Message(
                channel="console",
                target="cli",
                body="check the python version in the repo runtime",
            ),
            capability_category="system.exec",
        )
    )

    assert len(provider.calls) >= 3
    assert "Python 3.11.9" in response.text
    assert response.metadata.get("tool_execution_count") == "2"
    tool_results = json.loads(response.metadata.get("tool_results", "[]"))
    assert [result.get("ok") for result in tool_results] == [False, True]
    denied_details = (tool_results[0].get("data") or {}).get("error_details") or {}
    assert denied_details.get("suggested_tool") == "exec.run"
    assert "workdir" in str(denied_details.get("suggested_fix") or "")


def test_required_lane_finalizes_unavailable_version_probe_instead_of_loop_error() -> (
    None
):
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.runtime.tool_selection.bindings["system.exec"] = "exec.run"
    config.runtime.tool_selection.allow_runtime_direct_fallback = False

    provider = _RepeatsUnavailableVersionProvider()
    service = AgentService(
        config=config,
        plugins=PluginRegistry([]),
        provider=provider,
        logger=logging.getLogger("openminion.tests"),
        tools=ToolRegistry([_ExecUnavailableTool()]),
    )

    response = _run_async(
        service.run_turn(
            Message(
                channel="console",
                target="cli",
                body="check whether nasm exists and what version it is",
            ),
            capability_category="system.exec",
        )
    )

    assert "nasm --version" in response.text
    assert "appears unavailable" in response.text
    assert "repeated identical tool calls" not in response.text
    assert response.metadata.get("tool_loop_termination_reason") == (
        "tool_unavailable_final"
    )
    assert response.metadata.get("tool_execution_count") == "1"
    assert len(provider.calls) == 2


def _assert_invalid_tool_arguments_contract_metadata(
    response, *, expected_missing: str
) -> None:
    metadata = response.metadata
    assert metadata.get("tool_loop_termination_reason") == "tool_arg_exhausted"
    assert metadata.get("tool_error_code") == "INVALID_TOOL_ARGUMENTS"
    assert metadata.get("tool_error_reason_code") == "tool_arg_validation_failed"
    assert metadata.get("tool_execution_count") == "0"
    assert metadata.get("tool_arg_exhausted") == "weather.openmeteo.current"
    assert expected_missing in metadata.get("tool_arg_exhausted_missing", "")

    payload = json.loads(metadata.get("tool_error_payload", "{}"))
    assert payload.get("error_code") == "INVALID_TOOL_ARGUMENTS"
    assert payload.get("reason_code") == "tool_arg_validation_failed"
    assert payload.get("tool_name") == "weather.openmeteo.current"
    assert expected_missing in payload.get("missing_fields", [])

    tool_results = json.loads(metadata.get("tool_results", "[]"))
    assert tool_results and tool_results[0].get("ok") is False
    assert (tool_results[0].get("data") or {}).get(
        "contract_error_code"
    ) == "INVALID_TOOL_ARGUMENTS"
    assert (tool_results[0].get("data") or {}).get(
        "reason_code"
    ) == "tool_arg_validation_failed"
    assert expected_missing in (
        (tool_results[0].get("data") or {}).get("missing_fields") or []
    )


def test_required_lane_invalid_arguments_contract_payload_stable_for_missing_fields() -> (
    None
):
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.runtime.tool_selection.allow_runtime_direct_fallback = False
    for provider_name in ("openrouter-sim", "cortensor-sim"):
        provider = _InvalidArgProvider(provider_name=provider_name, arguments={})
        service = AgentService(
            config=config,
            plugins=PluginRegistry([]),
            provider=provider,
            logger=logging.getLogger("openminion.tests"),
            tools=ToolRegistry([_WeatherTool()]),
        )
        response = _run_async(
            service.run_turn(
                Message(
                    channel="console",
                    target="cli",
                    body="what's weather in san francisco?",
                ),
                capability_category="weather",
            )
        )
        assert "Invalid tool arguments" in response.text
        assert len(provider.calls) == 2
        _assert_invalid_tool_arguments_contract_metadata(
            response, expected_missing="location"
        )


def test_required_lane_invalid_arguments_contract_payload_stable_for_non_object_args() -> (
    None
):
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.runtime.tool_selection.allow_runtime_direct_fallback = False
    for provider_name in ("openrouter-sim", "cortensor-sim"):
        provider = _InvalidArgProvider(provider_name=provider_name, arguments="sf")
        service = AgentService(
            config=config,
            plugins=PluginRegistry([]),
            provider=provider,
            logger=logging.getLogger("openminion.tests"),
            tools=ToolRegistry([_WeatherTool()]),
        )
        response = _run_async(
            service.run_turn(
                Message(channel="console", target="cli", body="what's weather in sf?"),
                capability_category="weather",
            )
        )
        assert "Invalid tool arguments" in response.text
        assert len(provider.calls) == 2
        _assert_invalid_tool_arguments_contract_metadata(
            response, expected_missing="location"
        )


def test_weather_lane_direct_fallback_can_recover_when_enabled() -> None:
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.runtime.tool_selection.bindings["weather"] = "weather.openmeteo.current"
    config.runtime.tool_selection.allow_runtime_direct_fallback = True

    service = AgentService(
        config=config,
        plugins=PluginRegistry([]),
        provider=_NoToolSearchProvider(),
        logger=logging.getLogger("openminion.tests"),
        tools=ToolRegistry([_WeatherTool()]),
    )

    response = _run_async(
        service.run_turn(
            Message(
                channel="console", target="cli", body="what's weather at canada today?"
            ),
            capability_category="weather",
        )
    )

    assert response.text == "Required tool call missing"
    assert (
        response.metadata.get("tool_loop_termination_reason")
        == "required_tool_call_missing"
    )
    assert response.metadata.get("tool_execution_count") == "0"


def test_required_lane_repairs_empty_search_query_when_enabled() -> None:
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.runtime.tool_selection.bindings["web.search"] = "search.dispatch"
    config.runtime.tool_selection.allow_runtime_direct_fallback = True

    provider = _InvalidArgProvider(
        provider_name="openrouter-sim",
        tool_name="search.dispatch",
        arguments={},
    )
    service = AgentService(
        config=config,
        plugins=PluginRegistry([]),
        provider=provider,
        logger=logging.getLogger("openminion.tests"),
        tools=ToolRegistry([_SearchTool()]),
    )

    response = _run_async(
        service.run_turn(
            Message(
                channel="console",
                target="cli",
                body="what's latest news on iran?",
            ),
            capability_category="web.search",
        )
    )

    assert response.text == "Invalid tool arguments"
    assert response.metadata.get("tool_execution_count") == "0"


def test_required_lane_repairs_missing_weather_location_when_enabled() -> None:
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.runtime.tool_selection.bindings["weather"] = "weather.openmeteo.current"
    config.runtime.tool_selection.allow_runtime_direct_fallback = True

    provider = _InvalidArgProvider(
        provider_name="openrouter-sim",
        tool_name="weather.openmeteo.current",
        arguments={},
    )
    service = AgentService(
        config=config,
        plugins=PluginRegistry([]),
        provider=provider,
        logger=logging.getLogger("openminion.tests"),
        tools=ToolRegistry([_WeatherTool()]),
    )

    response = _run_async(
        service.run_turn(
            Message(
                channel="console",
                target="cli",
                body="what's weather in tokyo right now?",
            ),
            capability_category="weather",
        )
    )

    assert response.text == "Invalid tool arguments"
    assert response.metadata.get("tool_execution_count") == "0"


def test_weather_lane_missing_location_prompts_for_location() -> None:
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.runtime.tool_selection.bindings["weather"] = "weather.openmeteo.current"
    config.runtime.tool_selection.allow_runtime_direct_fallback = True

    service = AgentService(
        config=config,
        plugins=PluginRegistry([]),
        provider=_NoToolSearchProvider(),
        logger=logging.getLogger("openminion.tests"),
        tools=ToolRegistry([_WeatherTool()]),
    )

    response = _run_async(
        service.run_turn(
            Message(channel="console", target="cli", body="what's weather?"),
            capability_category="weather",
        )
    )

    assert response.text == "Required tool call missing"
    assert (
        response.metadata.get("tool_loop_termination_reason")
        == "required_tool_call_missing"
    )


def test_browser_lane_direct_fallback_bootstraps_pinchtab_navigation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_ALLOW_MODEL_EXPOSURE_PROVIDER_FALLBACK", "1")
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.runtime.tool_selection.bindings["browser"] = "browser"
    config.runtime.tool_selection.allow_runtime_direct_fallback = True

    service = AgentService(
        config=config,
        plugins=PluginRegistry([]),
        provider=_SingleToolProvider(
            "browser", {"op": "tab.navigate", "url": "https://google.com"}
        ),
        logger=logging.getLogger("openminion.tests"),
        tools=ToolRegistry([_BrowserUnifiedTool()]),
    )

    response = _run_async(
        service.run_turn(
            Message(
                channel="console",
                target="cli",
                body="open browser and go to google.com",
            ),
            capability_category="browser",
        )
    )

    assert "opened https://google.com" in response.text
    assert response.metadata.get("finish_reason") == "tool_calls"
    assert response.metadata.get("capability_tool") == "browser"


def test_capability_fallback_chain_executes_on_tool_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENMINION_ALLOW_MODEL_EXPOSURE_PROVIDER_FALLBACK", "1")
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.runtime.tool_selection.bindings["weather"] = "weather.primary"
    config.runtime.tool_selection.bindings_fallback["weather"] = ["weather.secondary"]

    provider = _ToolThenFinalProvider({"location": "tokyo"})

    service = AgentService(
        config=config,
        plugins=PluginRegistry([]),
        provider=provider,
        logger=logging.getLogger("openminion.tests"),
        tools=ToolRegistry(
            [_FailPrimaryTool("tool_unavailable"), _FallbackWeatherTool()]
        ),
    )

    response = _run_async(
        service.run_turn(
            Message(
                channel="console",
                target="cli",
                body="what's weather in tokyo?",
                metadata={"allow_runtime_direct": "true"},
            ),
            capability_category="weather",
        )
    )

    assert response.text.startswith("Fallback weather completed.")
    attempted = json.loads(response.metadata.get("capability_attempted_tools", "[]"))
    assert attempted == ["weather.primary", "weather.secondary"]
    assert (
        response.metadata.get("capability_fallback_trigger_reason")
        == "tool_unavailable"
    )
    assert response.metadata.get("capability_final_tool") == "weather.secondary"
    assert response.metadata.get("fallback_used") == "true"
    assert provider.calls[0].tools[0].name == "weather.primary"
    assert provider.calls[1].tools[0].name == "weather.secondary"


def test_capability_fallback_is_blocked_on_policy_denied() -> None:
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.runtime.tool_selection.bindings["weather"] = "weather.primary"
    config.runtime.tool_selection.bindings_fallback["weather"] = ["weather.secondary"]

    provider = _SingleToolProvider("weather.primary", {"location": "tokyo"})

    service = AgentService(
        config=config,
        plugins=PluginRegistry([]),
        provider=provider,
        logger=logging.getLogger("openminion.tests"),
        tools=ToolRegistry([_FailPrimaryTool("policy_denied"), _FallbackWeatherTool()]),
    )

    response = _run_async(
        service.run_turn(
            Message(channel="console", target="cli", body="what's weather in tokyo?"),
            capability_category="weather",
        )
    )

    results = json.loads(response.metadata.get("tool_results", "[]"))
    assert [item["tool_name"] for item in results] == ["weather.primary"]
    assert response.metadata.get("runtime_tool_name") == "weather.primary"
    assert response.metadata.get("tool_loop_termination_reason") in {
        "tool_no_success",
        "required_tool_call_missing",
        "fallback_exhausted",
        None,
    }
    # Fallback should not have been used
    assert response.metadata.get("fallback_used") in {"false", False, "False", None}
