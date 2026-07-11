from __future__ import annotations
from tests._csc_fixtures import _csc_install_default_agent


import asyncio
import hmac
import logging
import os
import tempfile
import time
import unittest
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

from openminion.base.channel import Channel, ChannelRegistry
from openminion.base.config import ChannelAuthenticityConfig, OpenMinionConfig
from openminion.base.types import Message
from openminion.services.runtime.plugins import PluginRegistry
from openminion.modules.llm.providers.base import (
    LLMProvider,
    ProviderRequest,
    ProviderResponse,
    ProviderToolCall,
)
from openminion.modules.brain.adapters.factory import create_memory_adapter
from openminion.services.agent import AgentService
from openminion.services.agent.memory import MemoryPatchResult
from openminion.services.channel.authenticity import build_channel_authenticity_policy
from openminion.services.gateway import GatewayService
from openminion.services.runtime.run_status import (
    RUN_STATE_RUNNING,
    append_run_state_event,
)
from openminion.services.security.policy import SecurityPolicyEngine, SecurityPolicyRule
from openminion.modules.memory.errors import ConstraintViolationError, StoreWriteError
from openminion.modules.storage.runtime.idempotency_store import IdempotencyStore
from openminion.modules.storage.runtime.migrations import migrate_database
from openminion.modules.storage.runtime.session_store import SessionStore
from openminion.modules.storage.runtime.sqlite import connect_database
from openminion.modules.tool.base import (
    Tool,
    ToolExecutionContext,
    ToolExecutionResult,
)
from openminion.modules.tool.registry import ToolRegistry


class _CaptureProvider(LLMProvider):
    name = "capture"

    def __init__(self) -> None:
        self.requests: list[ProviderRequest] = []

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        return ProviderResponse(
            text=f"history={len(request.history)}::{request.user_message}",
            model="capture-model",
        )


class _SlowCaptureProvider(LLMProvider):
    name = "slow-capture"

    def __init__(self) -> None:
        self.requests: list[ProviderRequest] = []

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        await asyncio.sleep(0.05)
        return ProviderResponse(
            text=f"slow::{request.user_message}", model="slow-capture-model"
        )


class _SinkChannel(Channel):
    def __init__(self, *, name: str = "console") -> None:
        self.name = name
        self.sent: list[Message] = []

    def send(self, message: Message) -> None:
        self.sent.append(message)


class _ToolCallProvider(LLMProvider):
    name = "tool-call-provider"

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        del request
        return ProviderResponse(
            text="",
            model="tool-call-model",
            tool_calls=[
                ProviderToolCall(
                    id="call-1",
                    name="weather.openmeteo.current",
                    arguments={"city": "Tokyo"},
                    source="fallback",
                )
            ],
            finish_reason="tool_calls",
        )


class _StubWeatherTool(Tool):
    name = "weather.openmeteo.current"
    description = "Lookup weather by city."
    parameters = {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    }

    def execute(self, arguments, context: ToolExecutionContext) -> ToolExecutionResult:
        del context
        city = str(arguments.get("city", "unknown")).strip() or "unknown"
        return ToolExecutionResult(
            tool_name=self.name,
            ok=True,
            content=f"{city} weather now: 30C.",
            verified=True,
            data={"city": city},
            source="test-stub",
        )


class _FailingSearchTool(Tool):
    name = "tavily.web.search"
    description = "Search tool that fails."
    parameters = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }

    def execute(self, arguments, context: ToolExecutionContext) -> ToolExecutionResult:
        del arguments, context
        return ToolExecutionResult(
            tool_name=self.name,
            ok=False,
            content="",
            verified=False,
            error="missing required argument 'query'",
            source="test-stub",
        )


class _StaticSecurityEventAgent:
    def __init__(self, metadata: dict[str, str]) -> None:
        self._metadata = metadata

    async def run_turn(self, message: Message, history=None, **_kwargs):  # noqa: ANN001
        del history, _kwargs
        return type(
            "_Response",
            (),
            {
                "text": f"reply:{message.body}",
                "channel": message.channel,
                "target": message.target,
                "metadata": dict(self._metadata),
            },
        )()


class _FlakyProvider(LLMProvider):
    name = "flaky-provider"

    def __init__(self) -> None:
        self.calls = 0
        self.requests: list[ProviderRequest] = []

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.calls += 1
        self.requests.append(request)
        if self.calls == 1:
            raise RuntimeError("simulated provider failure")
        return ProviderResponse(text=f"ok::{request.user_message}", model="flaky-model")


class _SequenceTextProvider(LLMProvider):
    name = "sequence-text-provider"

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.requests: list[ProviderRequest] = []

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        if not self._responses:
            text = ""
        else:
            index = min(len(self.requests) - 1, len(self._responses) - 1)
            text = self._responses[index]
        return ProviderResponse(text=text, model="sequence-text-model")


class _SuccessfulSearchTool(Tool):
    name = "tavily.web.search"
    description = "Search tool that succeeds."
    parameters = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }

    def execute(self, arguments, context: ToolExecutionContext) -> ToolExecutionResult:
        del context
        query = str(arguments.get("query", "")).strip() or "unknown"
        return ToolExecutionResult(
            tool_name=self.name,
            ok=True,
            content=f"Search results for {query}",
            verified=True,
            data={"query": query, "result_count": 1},
            source="test-stub",
        )


class _EphemeralSmokeMemoryAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def build_context(self, *, session_id: str, user_message: str) -> str:
        self.calls.append(("build_context", session_id))
        del user_message
        return (
            "Agent canonical memory (cross-session):\n\n"
            "Relevant facts:\n"
            "- ephemeral-memory-smoke is active"
        )

    def build_retrieval_context(self, *, session_id: str, user_message: str) -> str:
        self.calls.append(("build_retrieval_context", session_id))
        del user_message
        return (
            "Agent memory (dynamic retrieval):\n\n"
            "Relevant facts:\n"
            "- retrieval channel from ephemeral-memory-smoke"
        )

    def record_turn(
        self,
        *,
        session_id: str,
        run_id: str,
        request_id: str,
        channel: str,
        target: str,
        user_message: str,
        assistant_message: str,
    ) -> MemoryPatchResult:
        self.calls.append(("record_turn", session_id))
        del run_id, request_id, channel, target, user_message, assistant_message
        return MemoryPatchResult(facts_added=0, todos_added=0, todos_completed=0)


class _FailingMemoryAdapter(_EphemeralSmokeMemoryAdapter):
    def record_turn(
        self,
        *,
        session_id: str,
        run_id: str,
        request_id: str,
        channel: str,
        target: str,
        user_message: str,
        assistant_message: str,
    ) -> MemoryPatchResult:
        del (
            session_id,
            run_id,
            request_id,
            channel,
            target,
            user_message,
            assistant_message,
        )
        raise RuntimeError("simulated memory write failure")


class _QuotaThenRecoverMemoryAdapter(_EphemeralSmokeMemoryAdapter):
    def __init__(self) -> None:
        super().__init__()
        self._failed = False

    def build_context(self, *, session_id: str, user_message: str) -> str:
        self.calls.append(("build_context", session_id))
        del user_message
        if not self._failed:
            self._failed = True
            raise ConstraintViolationError(
                "memory quota exceeded",
                details={"reason_code": "memory_quota_exceeded"},
            )
        return (
            "Agent canonical memory (cross-session):\n\n"
            "Relevant facts:\n"
            "- ephemeral-memory-smoke recovered"
        )


class _WriteFailOnceMemoryAdapter(_EphemeralSmokeMemoryAdapter):
    def __init__(self) -> None:
        super().__init__()
        self._failed = False

    def record_turn(
        self,
        *,
        session_id: str,
        run_id: str,
        request_id: str,
        channel: str,
        target: str,
        user_message: str,
        assistant_message: str,
    ) -> MemoryPatchResult:
        self.calls.append(("record_turn", session_id))
        del run_id, request_id, channel, target, user_message, assistant_message
        if not self._failed:
            self._failed = True
            raise StoreWriteError(
                "memory store unavailable",
                details={"reason_code": "memory_store_unavailable"},
            )
        return MemoryPatchResult(facts_added=1, todos_added=0, todos_completed=0)


class GatewayServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.database_path = Path(self._tmp.name) / "state" / "openminion.db"
        migrate_database(self.database_path)
        self.connection = connect_database(self.database_path)
        self.sessions = SessionStore(self.connection)
        self.idempotency = IdempotencyStore(self.connection)

        provider = _CaptureProvider()
        gateway, sink = self._build_gateway(
            provider=provider,
            logger_name="openminion.tests.gateway",
            agent_logger_name="openminion.tests.gateway.agent",
        )

        self.provider = provider
        self.channel = sink
        self.gateway = gateway

    def tearDown(self) -> None:
        self.connection.close()
        self._tmp.cleanup()

    def _build_gateway(
        self,
        *,
        provider: LLMProvider | None = None,
        agent: object | None = None,
        logger_name: str,
        agent_logger_name: str,
        security_policy: SecurityPolicyEngine | None = None,
        tools: ToolRegistry | None = None,
        sink_channel_name: str = "console",
        authenticity_policy=None,
        history_limit: int = 20,
        session_context: object | None = None,
        agent_memory: object | None = None,
        knowledge_graphs: object | None = None,
        auto_resume: bool = True,
    ) -> tuple[GatewayService, _SinkChannel]:
        config = OpenMinionConfig()
        _csc_install_default_agent(config, name="main")
        active_agent = agent
        if active_agent is None:
            if provider is None:
                raise AssertionError("provider or agent is required")
            active_agent = AgentService(
                config=config,
                plugins=PluginRegistry([]),
                provider=provider,
                logger=logging.getLogger(agent_logger_name),
                tools=tools,
                security_policy=security_policy,
            )
        sink = _SinkChannel(name=sink_channel_name)
        gateway = GatewayService(
            agent=active_agent,  # type: ignore[arg-type]
            channels=ChannelRegistry([sink]),
            logger=logging.getLogger(logger_name),
            sessions=self.sessions,
            idempotency=self.idempotency,
            agent_id=config.agents[next(iter(config.agents.keys()))].name,
            security_policy=security_policy,
            channel_authenticity_policy=authenticity_policy,
            history_limit=history_limit,
            session_context=session_context,  # type: ignore[arg-type]
            agent_memory=agent_memory,
            knowledge_graphs=knowledge_graphs,
        )
        if auto_resume:
            original_run_once = gateway.run_once

            async def _run_once_with_resume(**kwargs):  # type: ignore[no-untyped-def]
                meta = dict(kwargs.get("inbound_metadata") or {})
                meta.setdefault("resume", "true")
                kwargs["inbound_metadata"] = meta
                return await original_run_once(**kwargs)

            gateway.run_once = _run_once_with_resume  # type: ignore[assignment]
        return gateway, sink


__all__ = [
    "AgentService",
    "Channel",
    "ChannelAuthenticityConfig",
    "ChannelRegistry",
    "GatewayService",
    "GatewayServiceTestCase",
    "IdempotencyStore",
    "LLMProvider",
    "MemoryPatchResult",
    "Message",
    "OpenMinionConfig",
    "Path",
    "PluginRegistry",
    "ProviderRequest",
    "ProviderResponse",
    "ProviderToolCall",
    "RUN_STATE_RUNNING",
    "SecurityPolicyEngine",
    "SecurityPolicyRule",
    "SessionStore",
    "Tool",
    "ToolExecutionContext",
    "ToolExecutionResult",
    "ToolRegistry",
    "_CaptureProvider",
    "_FailingMemoryAdapter",
    "_FailingSearchTool",
    "_FlakyProvider",
    "_EphemeralSmokeMemoryAdapter",
    "_QuotaThenRecoverMemoryAdapter",
    "_SequenceTextProvider",
    "_SinkChannel",
    "_SlowCaptureProvider",
    "_StaticSecurityEventAgent",
    "_StubWeatherTool",
    "_SuccessfulSearchTool",
    "_ToolCallProvider",
    "_WriteFailOnceMemoryAdapter",
    "append_run_state_event",
    "asyncio",
    "build_channel_authenticity_policy",
    "connect_database",
    "create_memory_adapter",
    "hmac",
    "logging",
    "migrate_database",
    "os",
    "patch",
    "sha256",
    "tempfile",
    "time",
    "unittest",
]
