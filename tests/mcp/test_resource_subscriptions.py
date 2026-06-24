from __future__ import annotations

import sys

from openminion.api.queries.runtime_reports import _build_mcp_section
from openminion.base.config.mcp import MCPServerConfig
from openminion.tools.mcp.manager import MCPFleetManager, MCPServerSession


class _ResourceSubscriptionTransport:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict]] = []

    def start(self) -> None:
        return None

    def is_running(self) -> bool:
        return True

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
        self.requests.append((method, dict(params or {})))
        if method == "initialize":
            return {
                "protocolVersion": "2025-03-26",
                "capabilities": {"resources": {"subscribe": True}},
            }
        return {}


def _session() -> MCPServerSession:
    return MCPServerSession(
        MCPServerConfig(name="Fixture", command=[sys.executable, "-m", "fixture"])
    )


def test_mcp_resource_subscription_lifecycle_and_update_report() -> None:
    session = _session()
    transport = _ResourceSubscriptionTransport()
    session._transport = transport  # noqa: SLF001

    session.subscribe_resource(resource_uri="file://fixture/readme.md")
    session.unsubscribe_resource(resource_uri="file://fixture/readme.md")
    session._handle_server_notification(  # noqa: SLF001
        method="notifications/resources/updated",
        params={"uri": "file://fixture/readme.md", "title": "README changed"},
    )

    methods = [method for method, _payload in transport.requests]
    assert methods == [
        "initialize",
        "notifications/initialized",
        "resources/subscribe",
        "resources/unsubscribe",
    ]
    assert transport.requests[2][1]["uri"] == "file://fixture/readme.md"
    assert transport.requests[3][1]["uri"] == "file://fixture/readme.md"

    updates = session.recent_resource_updates(limit=1)
    assert updates[0].server_name == "fixture"
    assert updates[0].uri == "file://fixture/readme.md"
    assert updates[0].title == "README changed"

    manager = MCPFleetManager(servers=[])
    manager._sessions = {"fixture": session}  # noqa: SLF001
    tools = type("Tools", (), {"mcp_manager": manager})()
    report = _build_mcp_section(type("Runtime", (), {"tools": tools})())

    assert report["resource_updates"]["fixture"][0]["uri"] == (
        "file://fixture/readme.md"
    )
    assert "content" not in report["resource_updates"]["fixture"][0]
