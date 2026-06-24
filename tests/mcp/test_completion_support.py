from __future__ import annotations

from openminion.base.config.mcp import MCPServerConfig
from openminion.tools.mcp.manager import MCPFleetManager, MCPServerSession


class _CompletionTransport:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.requests: list[tuple[str, dict]] = []

    def is_running(self) -> bool:
        return True

    def start(self) -> None:
        return None

    def notify(self, method: str, params: dict | None = None) -> None:
        self.requests.append((method, dict(params or {})))

    def close(self) -> None:
        return None

    def request(
        self,
        *,
        method: str,
        params: dict | None = None,
        timeout_seconds: float,
        server_request_handler=None,
    ) -> dict:
        del timeout_seconds, server_request_handler
        payload = dict(params or {})
        self.requests.append((method, payload))
        if self.fail:
            raise RuntimeError("completion failed")
        if method == "initialize":
            return {
                "protocolVersion": "2025-03-26",
                "capabilities": {"completion": {}},
            }
        if method == "completion/complete":
            argument = dict(payload.get("argument", {}) or {})
            value = str(argument.get("value", "") or "")
            return {
                "completion": {
                    "values": [f"{value}alpha", f"{value}beta"],
                    "total": 2,
                    "hasMore": False,
                }
            }
        return {}


def _session() -> MCPServerSession:
    return MCPServerSession(
        MCPServerConfig(name="Fixture", command=["python", "-m", "fixture"])
    )


def test_mcp_completion_supports_prompt_argument_completion() -> None:
    session = _session()
    transport = _CompletionTransport()
    session._transport = transport  # noqa: SLF001

    result = session.complete(
        ref_type="ref/prompt",
        ref_name="greet-user",
        argument_name="user_name",
        argument_value="ta",
    )

    assert result.values == ("taalpha", "tabeta")
    assert result.total == 2
    assert result.has_more is False
    method, payload = transport.requests[-1]
    assert method == "completion/complete"
    assert payload["ref"] == {"type": "ref/prompt", "name": "greet-user"}
    assert payload["argument"] == {"name": "user_name", "value": "ta"}
    assert payload["context"] == {"arguments": {}}


def test_mcp_completion_supports_resource_template_arguments_through_fleet() -> None:
    session = _session()
    session._transport = _CompletionTransport()  # noqa: SLF001
    manager = MCPFleetManager(servers=[])
    manager._sessions = {"fixture": session}  # noqa: SLF001

    result = manager.complete(
        server_name="fixture",
        ref_type="ref/resource",
        ref_name="file://fixture/{slug}.md",
        argument_name="slug",
        argument_value="d",
        context_arguments={"kind": "doc"},
    )

    assert result.values == ("dalpha", "dbeta")


def test_mcp_completion_failure_is_isolated_to_completion_call() -> None:
    session = _session()
    session._transport = _CompletionTransport(fail=True)  # noqa: SLF001
    session._initialized = True  # noqa: SLF001
    manager = MCPFleetManager(servers=[])
    manager._sessions = {"fixture": session}  # noqa: SLF001

    try:
        manager.complete(
            server_name="fixture",
            ref_type="ref/prompt",
            ref_name="greet-user",
            argument_name="user_name",
        )
    except RuntimeError as exc:
        assert str(exc) == "completion failed"


def test_mcp_completion_tui_runtime_query_path_returns_values() -> None:
    session = _session()
    session._transport = _CompletionTransport()  # noqa: SLF001
    manager = MCPFleetManager(servers=[])
    manager._sessions = {"fixture": session}  # noqa: SLF001
    tools = type("Tools", (), {"mcp_manager": manager})()
    runtime = type("Runtime", (), {"tools": tools})()

    from openminion.cli.tui.providers.runtime import OpenMinionRuntime

    provider = object.__new__(OpenMinionRuntime)
    provider._rt = runtime  # noqa: SLF001

    values = provider.mcp_complete(
        server_name="fixture",
        ref_type="ref/prompt",
        ref_name="greet-user",
        argument_name="user_name",
        argument_value="z",
    )

    assert values == ["zalpha", "zbeta"]
