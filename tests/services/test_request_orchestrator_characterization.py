from __future__ import annotations
from tests._csc_fixtures import _csc_install_default_agent


from dataclasses import dataclass
from types import SimpleNamespace

from openminion.base.config import OpenMinionConfig, resolve_agent_config
from openminion.services.lifecycle.request_orchestrator import run_turn


def test_lifecycle_orchestrator_surface_is_canonical_ingress_owner() -> None:
    from openminion.services.lifecycle.request_orchestrator import (
        run_turn as compatibility,
    )
    from openminion.services.runtime.ingress.orchestrator import run_turn as canonical

    assert compatibility is canonical


@dataclass
class _Spec:
    name: str


class _ToolRegistryStub:
    def __init__(self, names: list[str] | None = None) -> None:
        self._names = list(names or ["web.search"])

    def model_provider_specs(self):
        return [_Spec(name) for name in self._names]

    def provider_specs(self):
        return [_Spec(name) for name in self._names]


class _SessionStoreStub:
    def __init__(self) -> None:
        self.resolved: list[dict[str, str]] = []
        self.events: list[dict[str, object]] = []

    def resolve_session(
        self,
        *,
        agent_id: str,
        channel: str,
        target: str,
        session_id: str,
    ) -> SimpleNamespace:
        self.resolved.append(
            {
                "agent_id": agent_id,
                "channel": channel,
                "target": target,
                "session_id": session_id,
            }
        )
        return SimpleNamespace(id=session_id)

    def append_event(
        self,
        *,
        session_id: str,
        event_type: str,
        payload: dict[str, object],
    ) -> None:
        self.events.append(
            {
                "session_id": session_id,
                "event_type": event_type,
                "payload": dict(payload),
            }
        )


class _GatewayStub:
    def __init__(self, responses: list[SimpleNamespace]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, object]] = []

    async def run_once(self, **kwargs):
        self.calls.append(dict(kwargs))
        index = min(len(self.calls) - 1, len(self._responses) - 1)
        return self._responses[index]


class _RuntimeStub:
    def __init__(
        self,
        *,
        config: OpenMinionConfig,
        gateway: _GatewayStub,
        tools: _ToolRegistryStub | None = None,
    ) -> None:
        self.config = config
        self.gateway = gateway
        self.tools = tools or _ToolRegistryStub()
        self.sessions = _SessionStoreStub()
        self.closed = False
        self.tool_workspace_root = None

    def resolve_agent_profile(self, agent_id=None):  # noqa: ANN001
        return resolve_agent_config(self.config, agent_id)

    def resolve_gateway(self, _agent_id=None, overrides=None):  # noqa: ANN001
        del overrides
        return self.gateway

    def close(self) -> None:
        self.closed = True


def _default_config() -> OpenMinionConfig:
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.runtime.log_level = "ERROR"
    _csc_install_default_agent(config, name="main", provider="echo")
    return config


def test_memory_policy_intent_route() -> None:
    gateway = _GatewayStub(
        [
            SimpleNamespace(
                id="turn-memory-policy",
                channel="console",
                target="api-user",
                body="handled-by-normal-gateway-route",
                metadata={
                    "session_id": "memory-policy-session",
                    "run_id": "run-memory",
                    "run_state": "completed",
                },
            )
        ]
    )
    runtime = _RuntimeStub(
        config=_default_config(),
        gateway=gateway,
    )

    result = run_turn(
        config_path=None,
        payload={
            "message": "what is your memory retention and refresh policy?",
            "session_id": "memory-policy-session",
            "channel": "console",
            "target": "api-user",
        },
        runtime=runtime,
        request_id="req-memory-policy",
    )

    assert result["id"] == "turn-memory-policy"
    assert result["session_id"] == "memory-policy-session"
    assert result["body"] == "handled-by-normal-gateway-route"
    assert len(runtime.gateway.calls) == 1


def test_latest_news_request_flows_through_without_runtime_freshness_blocking() -> None:
    gateway = _GatewayStub(
        [
            SimpleNamespace(
                id="turn-news",
                channel="console",
                target="api-user",
                body="normal-route-ok",
                metadata={"session_id": "session-news"},
            )
        ]
    )
    runtime = _RuntimeStub(config=_default_config(), gateway=gateway)

    result = run_turn(
        config_path=None,
        payload={
            "message": "what is the latest news about inflation?",
            "session_id": "session-news",
            "channel": "console",
            "target": "api-user",
        },
        runtime=runtime,
        request_id="req-news",
    )

    assert result["id"] == "turn-news"
    assert len(runtime.gateway.calls) == 1
    assert (
        runtime.gateway.calls[0]["message"]
        == "what is the latest news about inflation?"
    )
    assert runtime.gateway.calls[0]["forced_tools"] == []
    assert "freshness_enforcement" not in result["metadata"]


def test_normal_turn_completes() -> None:
    gateway = _GatewayStub(
        [
            SimpleNamespace(
                id="turn-1",
                channel="console",
                target="api-user",
                body="normal-route-ok",
                metadata={
                    "session_id": "session-normal",
                    "run_id": "run-1",
                    "run_state": "completed",
                },
            )
        ]
    )
    runtime = _RuntimeStub(config=_default_config(), gateway=gateway)

    result = run_turn(
        config_path="/tmp/config.json",
        payload={
            "message": "hello there",
            "session_id": "session-normal",
            "agent_id": "main",
            "channel": "console",
            "target": "api-user",
        },
        runtime=None,
        runtime_factory=lambda _config_path: runtime,
        request_id="req-normal",
    )

    assert runtime.closed is True
    assert len(gateway.calls) == 1
    assert gateway.calls[0]["message"] == "hello there"
    assert result["id"] == "turn-1"
    assert result["body"] == "normal-route-ok"
    assert result["agent_id"] == "main"
    assert result["run_id"] == "run-1"
    assert result["run_state"] == "completed"


def test_progress_callback_is_forwarded_to_gateway() -> None:
    gateway = _GatewayStub(
        [
            SimpleNamespace(
                id="turn-progress",
                channel="console",
                target="api-user",
                body="normal-route-ok",
                metadata={
                    "session_id": "session-progress",
                    "run_id": "run-progress",
                    "run_state": "completed",
                },
            )
        ]
    )
    runtime = _RuntimeStub(config=_default_config(), gateway=gateway)
    captured = []
    progress_callback = captured.append

    run_turn(
        config_path=None,
        payload={
            "message": "hello there",
            "session_id": "session-progress",
            "agent_id": "main",
            "channel": "console",
            "target": "api-user",
        },
        runtime=runtime,
        request_id="req-progress",
        progress_callback=progress_callback,
    )

    assert len(gateway.calls) == 1
    assert gateway.calls[0]["progress_callback"] is progress_callback


def test_latest_news_turn_does_not_runtime_retry_or_inject_search_metadata() -> None:
    runtime = _RuntimeStub(
        config=_default_config(),
        gateway=_GatewayStub(
            [
                SimpleNamespace(
                    id="turn-search-1",
                    channel="console",
                    target="api-user",
                    body="initial response without search evidence",
                    metadata={"tool_calls": []},
                ),
                SimpleNamespace(
                    id="turn-search-2",
                    channel="console",
                    target="api-user",
                    body="grounded response",
                    metadata={
                        "tool_calls": [{"tool_name": "web.search"}],
                        "tool_results": [{"tool_name": "web.search", "ok": True}],
                    },
                ),
            ]
        ),
        tools=_ToolRegistryStub(["web.search"]),
    )

    result = run_turn(
        config_path=None,
        payload={
            "message": "what is the latest news on ai regulation?",
            "session_id": "freshness-retry-session",
            "channel": "console",
            "target": "api-user",
            "agent_id": "main",
        },
        runtime=runtime,
        request_id="req-freshness-retry",
    )

    assert len(runtime.gateway.calls) == 1
    assert runtime.gateway.calls[0]["forced_tools"] == []
    assert result["body"] == "initial response without search evidence"
    assert "freshness_post_run" not in result["metadata"]
    assert "freshness_enforcement" not in result["metadata"]
