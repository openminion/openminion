import asyncio
import pytest
from unittest.mock import MagicMock


class TestControlplaneTimeoutSemantics:
    @pytest.fixture
    def mock_gateway(self):
        gateway = MagicMock()
        return gateway

    @pytest.fixture
    def mock_runtime(self, mock_gateway):
        runtime = MagicMock()
        runtime.runtime_manager.gateway = mock_gateway
        return runtime

    def test_timeout_seconds_default(self):
        from openminion.modules.controlplane.adapters.client import (
            OpenMinionBrainClient,
        )

        client = OpenMinionBrainClient(
            config_path=None,
            runtime_factory=lambda _: MagicMock(),
        )
        assert client.timeout_seconds == 600.0

    def test_timeout_seconds_custom(self):
        from openminion.modules.controlplane.adapters.client import (
            OpenMinionBrainClient,
        )

        client = OpenMinionBrainClient(
            config_path=None,
            timeout_seconds=300.0,
            runtime_factory=lambda _: MagicMock(),
        )
        assert client.timeout_seconds == 300.0

    def test_timeout_passed_to_metadata(self, mock_runtime):
        from openminion.modules.controlplane.adapters.client import (
            OpenMinionBrainClient,
        )

        client = OpenMinionBrainClient(
            config_path=None,
            timeout_seconds=300.0,
            runtime_factory=lambda _: mock_runtime,
        )

        # Mock the gateway.run_once to capture the call
        captured_calls = []

        async def mock_run_once(**kwargs):
            captured_calls.append(kwargs)
            result = MagicMock()
            result.body = "Test response"
            result.metadata = {}
            return result

        mock_runtime.runtime_manager.gateway.run_once = mock_run_once

        # Run the client
        client.run(
            session_id="test-session",
            agent_id="test-agent",
            user_text="Hello",
            attachment_refs=[],
            trace_id="test-trace",
        )

        # Verify timeout was passed in metadata
        assert len(captured_calls) == 1
        metadata = captured_calls[0]["inbound_metadata"]
        assert metadata["turn_timeout_seconds"] == "300"

    def test_timeout_error_response(self, mock_runtime):
        from openminion.modules.controlplane.adapters.client import (
            OpenMinionBrainClient,
        )

        client = OpenMinionBrainClient(
            config_path=None,
            timeout_seconds=0.001,  # Very short timeout
            runtime_factory=lambda _: mock_runtime,
        )

        # Mock the gateway to simulate timeout
        async def slow_run_once(**kwargs):
            await asyncio.sleep(10)  # Will definitely timeout
            return MagicMock()

        mock_runtime.runtime_manager.gateway.run_once = slow_run_once

        # Run the client
        result = client.run(
            session_id="test-session",
            agent_id="test-agent",
            user_text="Hello",
            attachment_refs=[],
            trace_id="test-trace",
        )

        # Verify timeout error response
        assert result["text"] == ""
        assert result["session_id"] == "test-session"
        assert result["agent_id"] == "test-agent"
        assert result["trace_id"] == "test-trace"
        assert result["metadata"]["error"] == "TURN_TIMEOUT"
        assert "0.001s" in result["metadata"]["error_message"]
        assert result["metadata"]["lifecycle_stage"] == "timeout"
        assert result["metadata"]["retryable"] is True


class TestControlplaneLifecyclePropagation:
    @pytest.fixture
    def mock_runtime_with_lifecycle(self):
        runtime = MagicMock()

        result = MagicMock()
        result.body = "Weather: sunny"
        result.metadata = {
            "lifecycle_events": [
                {"stage": "submitted", "timestamp": 1000.0, "details": {}},
                {"stage": "pending", "timestamp": 1001.0, "details": {"attempt": 1}},
                {
                    "stage": "resolved",
                    "timestamp": 1005.0,
                    "details": {"poll_duration": 4.0},
                },
                {"stage": "parsed", "timestamp": 1006.0, "details": {"has_text": True}},
            ]
        }

        async def mock_run_once(**kwargs):
            return result

        runtime.runtime_manager.gateway.run_once = mock_run_once
        return runtime

    def test_lifecycle_events_extracted(self, mock_runtime_with_lifecycle):
        from openminion.modules.controlplane.adapters.client import (
            OpenMinionBrainClient,
        )

        client = OpenMinionBrainClient(
            config_path=None,
            runtime_factory=lambda _: mock_runtime_with_lifecycle,
        )

        result = client.run(
            session_id="test-session",
            agent_id="test-agent",
            user_text="what's weather at sf?",
            attachment_refs=[],
            trace_id="test-trace",
        )

        # Verify lifecycle events in metadata
        assert "lifecycle_events" in result["metadata"]
        events = result["metadata"]["lifecycle_events"]
        assert len(events) == 4
        assert events[0]["stage"] == "submitted"
        assert events[1]["stage"] == "pending"
        assert events[2]["stage"] == "resolved"
        assert events[3]["stage"] == "parsed"

        # Verify last stage summary
        assert result["metadata"]["lifecycle_stage"] == "parsed"
        assert result["metadata"]["lifecycle_timestamp"] == 1006.0

    def test_lifecycle_from_tool_results(self):
        from openminion.modules.controlplane.adapters.client import (
            OpenMinionBrainClient,
        )
        import json

        runtime = MagicMock()

        result = MagicMock()
        result.body = "Response text"
        result.metadata = {
            "tool_results": json.dumps(
                [
                    {"tool_name": "other_tool", "result": {}},
                    {
                        "tool_name": "cortensor_complete",
                        "result": {
                            "lifecycle_events": [
                                {"stage": "submitted", "timestamp": 1000.0},
                                {"stage": "delivered", "timestamp": 1010.0},
                            ]
                        },
                    },
                ]
            )
        }

        async def mock_run_once(**kwargs):
            return result

        runtime.runtime_manager.gateway.run_once = mock_run_once

        client = OpenMinionBrainClient(
            config_path=None,
            runtime_factory=lambda _: runtime,
        )

        result = client.run(
            session_id="test-session",
            agent_id="test-agent",
            user_text="Hello",
            attachment_refs=[],
            trace_id="test-trace",
        )

        # Verify lifecycle events extracted from tool results
        assert "lifecycle_events" in result["metadata"]
        events = result["metadata"]["lifecycle_events"]
        assert len(events) == 2
        assert events[0]["stage"] == "submitted"
        assert events[1]["stage"] == "delivered"

    def test_no_lifecycle_events_when_not_present(self):
        from openminion.modules.controlplane.adapters.client import (
            OpenMinionBrainClient,
        )

        runtime = MagicMock()

        result = MagicMock()
        result.body = "Simple response"
        result.metadata = {}

        async def mock_run_once(**kwargs):
            return result

        runtime.runtime_manager.gateway.run_once = mock_run_once

        client = OpenMinionBrainClient(
            config_path=None,
            runtime_factory=lambda _: runtime,
        )

        result = client.run(
            session_id="test-session",
            agent_id="test-agent",
            user_text="Hello",
            attachment_refs=[],
            trace_id="test-trace",
        )

        # Verify no lifecycle events added
        assert "lifecycle_events" not in result["metadata"]


class TestControlplaneWeatherPromptPath:
    def test_weather_prompt_returns_response(self):
        from openminion.modules.controlplane.adapters.client import (
            OpenMinionBrainClient,
        )

        runtime = MagicMock()

        result = MagicMock()
        result.body = "The weather in San Francisco is sunny today."
        result.metadata = {
            "tool_calls": "cortensor_complete",
            "usage_prompt_tokens": 100,
            "usage_completion_tokens": 50,
        }

        async def mock_run_once(**kwargs):
            # Verify it's a weather prompt
            assert "weather" in kwargs["message"].lower()
            assert kwargs["session_id"].startswith("weather-test")
            return result

        runtime.runtime_manager.gateway.run_once = mock_run_once

        client = OpenMinionBrainClient(
            config_path=None,
            runtime_factory=lambda _: runtime,
        )

        result = client.run(
            session_id="weather-test-123",
            agent_id="cortensor35",
            user_text="what's weather at sf?",
            attachment_refs=[],
            trace_id="test-trace-456",
        )

        assert "sunny" in result["text"].lower() or "weather" in result["text"].lower()
        assert result["session_id"] == "weather-test-123"

    def test_weather_prompt_with_error(self):
        from openminion.modules.controlplane.adapters.client import (
            OpenMinionBrainClient,
        )

        runtime = MagicMock()

        async def mock_run_once(**kwargs):
            raise RuntimeError("Provider error")

        runtime.runtime_manager.gateway.run_once = mock_run_once

        client = OpenMinionBrainClient(
            config_path=None,
            runtime_factory=lambda _: runtime,
        )

        # Should not raise, should return error response
        with pytest.raises(RuntimeError):
            client.run(
                session_id="weather-test-123",
                agent_id="cortensor35",
                user_text="what's weather at sf?",
                attachment_refs=[],
                trace_id="test-trace-456",
            )


class TestNegativePaths:
    def test_timeout_with_zero_duration(self):
        from openminion.modules.controlplane.adapters.client import (
            OpenMinionBrainClient,
        )

        runtime = MagicMock()

        async def slow_run(**kwargs):
            await asyncio.sleep(0.1)
            return MagicMock()

        runtime.runtime_manager.gateway.run_once = slow_run

        client = OpenMinionBrainClient(
            config_path=None,
            timeout_seconds=0.0,
            runtime_factory=lambda _: runtime,
        )

        result = client.run(
            session_id="test",
            agent_id="agent",
            user_text="Hello",
            attachment_refs=[],
            trace_id="trace",
        )

        assert result["metadata"]["error"] == "TURN_TIMEOUT"

    def test_empty_response_handling(self):
        from openminion.modules.controlplane.adapters.client import (
            OpenMinionBrainClient,
        )

        runtime = MagicMock()

        result = MagicMock()
        result.body = ""
        result.metadata = {}

        async def mock_run_once(**kwargs):
            return result

        runtime.runtime_manager.gateway.run_once = mock_run_once

        client = OpenMinionBrainClient(
            config_path=None,
            runtime_factory=lambda _: runtime,
        )

        result = client.run(
            session_id="test",
            agent_id="agent",
            user_text="Hello",
            attachment_refs=[],
            trace_id="trace",
        )

        assert result["text"] == ""
