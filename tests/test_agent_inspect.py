import io
import json
from contextlib import redirect_stdout
from unittest.mock import MagicMock


def _missing_agent_registry() -> MagicMock:
    from openminion.modules.storage.runtime.registry_store import AgentRegistryStore

    registry = MagicMock(spec=AgentRegistryStore)
    registry.get_agent.return_value = None
    registry.get_heartbeat.return_value = None
    registry.is_agent_stale.return_value = True
    return registry


def _inspect_agent(registry: MagicMock, *, as_json: bool) -> tuple[int, str]:
    from openminion.cli.commands.agents import agent_inspect

    buf = io.StringIO()
    with redirect_stdout(buf):
        result = agent_inspect(registry, "test-agent", as_json=as_json)
    return result, buf.getvalue()


def test_agent_inspect_returns_json():
    result, json_output = _inspect_agent(_missing_agent_registry(), as_json=True)

    data = json.loads(json_output)
    assert data["agent_id"] == "test-agent"
    assert "runtime" in data
    assert "skills" in data
    assert "tools" in data
    assert "identity" in data
    assert "health" in data
    assert result == 0


def test_agent_inspect_returns_human_readable():
    result, output = _inspect_agent(_missing_agent_registry(), as_json=False)

    assert "Agent:" in output
    assert "test-agent" in output
    assert "Runtime:" in output
    assert "Skills:" in output
    assert result == 0


def test_agent_inspect_with_running_agent():
    mock_agent = MagicMock()
    mock_agent.display_name = "Test Agent"

    mock_hb = MagicMock()
    mock_hb.status = "running"
    mock_hb.pid = 12345
    mock_hb.host = "localhost"
    mock_hb.port = 8080
    mock_hb.active_run_id = "run-123"
    mock_hb.started_at = "2026-03-06T00:00:00"
    mock_hb.last_heartbeat_at = "2026-03-06T01:00:00"

    mock_registry = MagicMock()
    mock_registry.get_agent.return_value = mock_agent
    mock_registry.get_heartbeat.return_value = mock_hb
    mock_registry.is_agent_stale.return_value = False

    _, json_output = _inspect_agent(mock_registry, as_json=True)
    data = json.loads(json_output)
    assert data["status"] == "running"
    assert data["health"]["flags"] == ["healthy"]


def test_agent_inspect_reflects_runtime_tools():
    _, json_output = _inspect_agent(_missing_agent_registry(), as_json=True)
    data = json.loads(json_output)
    assert "tools" in data
    assert "catalog_summary" in data["tools"]
    # Runtime-backed counts may be nonzero; missing runtime still falls back to 0.
    assert "total" in data["tools"]["catalog_summary"]
    assert isinstance(data["tools"]["catalog_summary"]["total"], int)


def test_agent_inspect_tools_have_category_breakdown():
    _, json_output = _inspect_agent(_missing_agent_registry(), as_json=True)
    data = json.loads(json_output)
    assert "by_category" in data["tools"]["catalog_summary"]
    assert isinstance(data["tools"]["catalog_summary"]["by_category"], dict)


def test_debug_module_probes_all_core_modules():
    from openminion.services.diagnostics.debug import get_debug_registry
    from openminion.cli.commands.debug import _register_core_providers

    registry = get_debug_registry()
    _register_core_providers(registry)

    core_modules = [
        "openminion-session",
        "openminion-context",
        "openminion-memory",
        "context.compress",
        "openminion-retrieve",
    ]

    for module_name in core_modules:
        provider = registry.get_module(module_name)
        assert provider is not None, f"Module {module_name} should be registered"


def test_debug_module_returns_structured_payload():
    from openminion.services.diagnostics.debug import get_debug_registry
    from openminion.cli.commands.debug import _register_core_providers

    registry = get_debug_registry()
    _register_core_providers(registry)

    provider = registry.get_module("openminion-session")
    assert provider is not None

    debug_output = provider.get_debug()
    assert hasattr(debug_output, "module")
    assert hasattr(debug_output, "status")
    assert hasattr(debug_output, "mode")
    assert hasattr(debug_output, "wiring_source")
