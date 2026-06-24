from __future__ import annotations

from openminion.tools.mcp import (
    MCPAuthorizationError,
    MCPCallError,
    MCPCompletionResult,
    MCPFleetHandle,
    MCPFleetManager,
    MCPListedPrompt,
    MCPListedResource,
    MCPListedResourceTemplate,
    MCPListedTool,
    MCPLogMessage,
    MCPResourceUpdate,
    MCPToolPosture,
    MCPManagerError,
    MCPProtocolError,
    MCPRemoteTransportError,
    MCPServerUnavailableError,
    MCPTimeoutError,
    REGISTRAR,
)
from openminion.tools.mcp.registrar import MCPRegistrar


def test_tools_mcp_package_exports_runtime_interfaces() -> None:
    assert MCPCompletionResult.__name__ == "MCPCompletionResult"
    assert MCPFleetHandle.__name__ == "MCPFleetHandle"
    assert MCPFleetManager.__name__ == "MCPFleetManager"
    assert MCPListedTool.__name__ == "MCPListedTool"
    assert MCPToolPosture.__name__ == "MCPToolPosture"
    assert MCPListedPrompt.__name__ == "MCPListedPrompt"
    assert MCPListedResource.__name__ == "MCPListedResource"
    assert MCPListedResourceTemplate.__name__ == "MCPListedResourceTemplate"
    assert MCPLogMessage.__name__ == "MCPLogMessage"
    assert MCPResourceUpdate.__name__ == "MCPResourceUpdate"


def test_tools_mcp_package_exports_runtime_errors() -> None:
    assert MCPAuthorizationError.__name__ == "MCPAuthorizationError"
    assert MCPCallError.__name__ == "MCPCallError"
    assert MCPManagerError.__name__ == "MCPManagerError"
    assert MCPProtocolError.__name__ == "MCPProtocolError"
    assert MCPRemoteTransportError.__name__ == "MCPRemoteTransportError"
    assert MCPServerUnavailableError.__name__ == "MCPServerUnavailableError"
    assert MCPTimeoutError.__name__ == "MCPTimeoutError"


def test_tools_mcp_package_root_exports_registrar() -> None:
    assert isinstance(REGISTRAR, MCPRegistrar)
    assert REGISTRAR.module_id == "mcp"
    assert REGISTRAR.is_provider_only is False
