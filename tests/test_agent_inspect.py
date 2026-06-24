import json
from unittest.mock import MagicMock


def test_agent_inspect_returns_json():
    from openminion.cli.commands.agents import agent_inspect
    from openminion.modules.storage.runtime.registry_store import (
        AgentRegistryStore,
    )

    # Create mock registry
    mock_registry = MagicMock(spec=AgentRegistryStore)
    mock_registry.get_agent.return_value = None
    mock_registry.get_heartbeat.return_value = None
    mock_registry.is_agent_stale.return_value = True

    # Capture output
    import io
    import sys

    old_stdout = sys.stdout
    sys.stdout = io.StringIO()

    result = agent_inspect(mock_registry, "test-agent", as_json=True)

    json_output = sys.stdout.getvalue()
    sys.stdout = old_stdout

    # Parse and validate
    data = json.loads(json_output)
    assert data["agent_id"] == "test-agent"
    assert "runtime" in data
    assert "skills" in data
    assert "tools" in data
    assert "identity" in data
    assert "health" in data
    assert result == 0


def test_agent_inspect_returns_human_readable():
    from openminion.cli.commands.agents import agent_inspect
    from openminion.modules.storage.runtime.registry_store import (
        AgentRegistryStore,
    )

    mock_registry = MagicMock(spec=AgentRegistryStore)
    mock_registry.get_agent.return_value = None
    mock_registry.get_heartbeat.return_value = None
    mock_registry.is_agent_stale.return_value = True

    import io
    import sys

    old_stdout = sys.stdout
    sys.stdout = io.StringIO()

    result = agent_inspect(mock_registry, "test-agent", as_json=False)

    output = sys.stdout.getvalue()
    sys.stdout = old_stdout

    assert "Agent:" in output
    assert "test-agent" in output
    assert "Runtime:" in output
    assert "Skills:" in output
    assert result == 0


def test_agent_inspect_with_running_agent():
    from openminion.cli.commands.agents import agent_inspect

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

    import io
    import sys

    old_stdout = sys.stdout
    sys.stdout = io.StringIO()

    agent_inspect(mock_registry, "test-agent", as_json=True)

    json_output = sys.stdout.getvalue()
    sys.stdout = old_stdout

    data = json.loads(json_output)
    assert data["status"] == "running"
    assert data["health"]["flags"] == ["healthy"]


def test_agent_inspect_reflects_runtime_tools():
    from openminion.cli.commands.agents import agent_inspect
    from openminion.modules.storage.runtime.registry_store import (
        AgentRegistryStore,
    )

    mock_registry = MagicMock(spec=AgentRegistryStore)
    mock_registry.get_agent.return_value = None
    mock_registry.get_heartbeat.return_value = None
    mock_registry.is_agent_stale.return_value = True

    import io
    import sys

    old_stdout = sys.stdout
    sys.stdout = io.StringIO()

    agent_inspect(mock_registry, "test-agent", as_json=True)

    json_output = sys.stdout.getvalue()
    sys.stdout = old_stdout

    data = json.loads(json_output)
    assert "tools" in data
    assert "catalog_summary" in data["tools"]
    # Runtime-backed counts may be nonzero; missing runtime still falls back to 0.
    assert "total" in data["tools"]["catalog_summary"]
    assert isinstance(data["tools"]["catalog_summary"]["total"], int)


def test_agent_inspect_tools_have_category_breakdown():
    from openminion.cli.commands.agents import agent_inspect
    from openminion.modules.storage.runtime.registry_store import (
        AgentRegistryStore,
    )

    mock_registry = MagicMock(spec=AgentRegistryStore)
    mock_registry.get_agent.return_value = None
    mock_registry.get_heartbeat.return_value = None
    mock_registry.is_agent_stale.return_value = True

    import io
    import sys

    old_stdout = sys.stdout
    sys.stdout = io.StringIO()

    agent_inspect(mock_registry, "test-agent", as_json=True)

    json_output = sys.stdout.getvalue()
    sys.stdout = old_stdout

    data = json.loads(json_output)
    # Should have category breakdown
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
    # Should have required fields
    assert hasattr(debug_output, "module")
    assert hasattr(debug_output, "status")
    assert hasattr(debug_output, "mode")
    assert hasattr(debug_output, "wiring_source")
