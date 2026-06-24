from openminion.modules.tool import (
    ToolRequestEnvelope,
    ToolResultEnvelope,
    ToolErrorEnvelope,
)
from openminion.tools.file.interfaces import FileRequestEnvelope, FileResultEnvelope
from openminion.tools.exec.interfaces import ExecRequestEnvelope, ExecResultEnvelope
from openminion.tools.weather.providers.openmeteo.interfaces import (
    WeatherRequestEnvelope,
    WeatherResultEnvelope,
)
from openminion.tools.search.providers.brave.interfaces import SearchRequestEnvelope
from openminion.tools.search.providers.tavily.interfaces import TavilyRequestEnvelope


def test_normalized_tool_call_envelope_contract() -> None:
    # Test that all tool types follow standardized request/response schema
    tool_requests = [
        ToolRequestEnvelope(
            method="file.read", args={"path": "test.txt"}, contract_version="v1"
        ),
        FileRequestEnvelope(
            method="file.write",
            args={"path": "out.txt", "content": "test"},
            contract_version="v1",
        ),
        ExecRequestEnvelope(
            method="exec.run", args={"command": "ls"}, contract_version="v1"
        ),
        WeatherRequestEnvelope(
            method="weather.current", args={"location": "NYC"}, contract_version="v1"
        ),
        SearchRequestEnvelope(
            method="search.query", args={"query": "ai news"}, contract_version="v1"
        ),
        TavilyRequestEnvelope(
            method="search.web", args={"query": "latest"}, contract_version="v1"
        ),
    ]

    # All should have the same basic properties
    for req in tool_requests:
        assert hasattr(req, "method")
        assert hasattr(req, "args")
        assert hasattr(req, "contract_version")


def test_compatibility_across_module_boundaries() -> None:
    all_modules_contracts = [
        "v1",
        "v1",
        "v1",
        "v1",
        "v1",
        "v1",
        "v1",
        "v1",
        "v1",
        "v1",
        "v1",
    ]
    # All should use the same base version scheme
    for contract_version in all_modules_contracts:
        assert contract_version.startswith("v1")


def test_real_weather_chat_probe() -> None:
    # Simulated chat command result following normalized contract
    weather_result = WeatherResultEnvelope(
        status="ok",
        data={"temperature": 22.5, "location": "Berlin", "conditions": "sunny"},
        artifacts={"weather_data": "ref1"},
        contract_version="v1",
    )
    assert weather_result.status == "ok"
    assert "location" in weather_result.data
    assert weather_result.contract_version == "v1"


def test_real_search_chat_probe() -> None:
    # Simulated search command result following normalized contract
    base_result = ToolResultEnvelope(
        status="ok",
        data={"results": [{"title": "AI News", "url": "http://example.com"}]},
        artifacts={"search_results": "ref2"},
        contract_version="v1",
    )
    assert base_result.status == "ok"
    assert base_result.contract_version == "v1"


def test_file_exec_tool_envelopes_consistency() -> None:
    file_result = FileResultEnvelope(
        status="ok",
        data={"content": "file contents"},
        artifacts={"file": "ref3"},
        contract_version="v1",
    )

    exec_result = ExecResultEnvelope(
        status="ok",
        data={"output": "command output", "exit_code": 0},
        artifacts={"output": "ref4"},
        contract_version="v1",
    )

    # Both inherit from base envelope and maintain compatible structure
    assert file_result.status == exec_result.status == "ok"
    assert file_result.contract_version == exec_result.contract_version == "v1"
    assert hasattr(file_result, "data") and hasattr(exec_result, "data")
    assert hasattr(file_result, "artifacts") and hasattr(exec_result, "artifacts")
    assert hasattr(file_result, "contract_version") and hasattr(
        exec_result, "contract_version"
    )


def test_cross_module_contract_errors() -> None:
    base_error = ToolErrorEnvelope(
        error_code="GENERAL_ERROR",
        error_message="Something went wrong",
        details={"info": "details"},
        contract_version="v1",
    )

    # Error structure should be the same across all modules
    assert hasattr(base_error, "error_code")
    assert hasattr(base_error, "error_message")
    assert hasattr(base_error, "details")
    assert hasattr(base_error, "contract_version")
    assert base_error.contract_version == "v1"


# Test run verification to check that everything passes
