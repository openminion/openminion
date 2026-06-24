import pytest
from types import SimpleNamespace
from unittest.mock import patch


def _weather_runner():
    from openminion.modules.brain.runner import BrainRunner
    from openminion.modules.tool.registry import ToolSpec
    from pydantic import BaseModel

    runner = BrainRunner.__new__(BrainRunner)
    runner.tool_api = SimpleNamespace(
        registry=SimpleNamespace(
            _tools={
                "weather": ToolSpec(
                    name="weather",
                    args_model=type("WeatherArgs", (BaseModel,), {}),
                    min_scope="READ_ONLY",
                    handler=lambda args, ctx: {},
                    capabilities=(
                        "read_only",
                        "network",
                        "weather",
                        "time_sensitive",
                    ),
                )
            }
        )
    )
    return runner


class TestToolInventoryIntent:
    @pytest.fixture
    def runner(self):
        from openminion.modules.brain.runner import BrainRunner

        runner = BrainRunner.__new__(BrainRunner)
        runner._tool_registry = {}
        runner.tool_api = None
        runner.skill_api = None
        return runner

    def test_inventory_heuristic_helper_removed(self, runner):
        assert not hasattr(runner, "_is_tool_inventory_intent")

    def test_build_tool_inventory_response_returns_bounded_list(self, runner):
        from openminion.modules.brain.schemas import WorkingState

        state = WorkingState(
            session_id="test-session",
            agent_id="test-agent",
            budgets_remaining={
                "ticks": 0,
                "tool_calls": 0,
                "a2a_calls": 0,
                "tokens": 0,
                "time_ms": 0,
            },
        )

        response = runner._build_tool_inventory_response(state=state)

        assert "Available Tools and Skills" in response
        assert "Tools (" in response
        assert "• file.write" in response or "• file.read" in response
        assert "Skills:" in response
        tool_count = response.count("• ")
        assert tool_count <= 15, f"Tool list not bounded: {tool_count} tools"


class TestWeatherNormalization:
    @pytest.fixture
    def runner(self):
        return _weather_runner()

    def test_weather_location_normalization_helper_removed(self, runner):
        assert not hasattr(runner, "_normalize_city_name")


class TestRemovedPlanOwner:
    def test_prerouting_package_no_longer_exposes_legacy_plan_owner(self):
        import importlib.util

        assert importlib.util.find_spec("openminion.modules.brain.phases") is None


class TestFreshnessPolicy:
    @pytest.fixture
    def runner(self):
        return _weather_runner()

    def test_time_sensitive_heuristic_helper_removed(self, runner):
        assert not hasattr(runner, "_is_time_sensitive_intent")

    def test_is_time_sensitive_tool_command(self, runner):
        from openminion.modules.brain.schemas import ToolCommand

        weather_cmd = ToolCommand(
            title="Tool call: weather",
            tool_name="weather",
            args={"location": "san francisco"},
        )

        non_weather_cmd = ToolCommand(
            title="Tool call: read_file",
            tool_name="read_file",
            args={"path": "/tmp/test.txt"},
        )

        assert runner._is_time_sensitive_tool_command(weather_cmd)
        assert not runner._is_time_sensitive_tool_command(non_weather_cmd)

    def test_build_time_sensitive_failure_response_includes_error_code(self, runner):
        from openminion.modules.brain.schemas import (
            WorkingState,
            ToolCommand,
            ActionResult,
            ActionError,
        )

        state = WorkingState(
            session_id="test-session",
            agent_id="test-agent",
            goal="what's the weather in sf?",
            budgets_remaining={
                "ticks": 0,
                "tool_calls": 0,
                "a2a_calls": 0,
                "tokens": 0,
                "time_ms": 0,
            },
        )

        command = ToolCommand(
            title="Tool call: weather",
            tool_name="weather",
            args={"location": "san francisco"},
        )

        action_result = ActionResult(
            command_id=command.command_id,
            status="failed",
            summary="Weather API unavailable",
            error=ActionError(code="TIMEOUT", message="Request timed out"),
        )

        response = runner._build_time_sensitive_failure_response(
            state=state,
            command=command,
            action_result=action_result,
        )

        assert "Weather Request Failed" in response or "weather" in response.lower()
        assert "[TIMEOUT]" in response
        assert "Retry options" in response or "retry" in response.lower()
        assert "cannot provide stale" in response.lower() or "stale" in response.lower()


class TestDebugUsageFields:
    def test_module_usage_debug_info_returns_expected_fields(self):
        from openminion.cli.chat.commands import _get_module_usage_debug_info

        class MockConfig:
            class storage:
                path = "/tmp/test"

        result = _get_module_usage_debug_info(
            config=MockConfig(),
            session_id="test-session",
        )

        assert "modules" in result
        assert "note" in result

        modules = result["modules"]
        assert "openminion-memory" in modules
        assert "openminion-retrieve" in modules
        assert "openminion-identity" in modules

        for module_name, module_info in modules.items():
            assert "importable" in module_info
            assert "available" in module_info
            assert "last_used_at" in module_info
            assert "recent_calls" in module_info
            assert "degraded_reason" in module_info

    def test_module_usage_debug_info_queries_module_usage_without_weather_alias(
        self, tmp_path
    ):
        from openminion.cli.chat.commands import _get_module_usage_debug_info

        db_path = tmp_path / "records.db"
        db_path.write_text("", encoding="utf-8")
        captured_params: list[tuple[object, ...]] = []

        class FakeCursor:
            def fetchall(self):
                return [{"call_count": 0, "last_used": None, "success_count": 0}]

        class FakeConnection:
            def execute(self, _sql, params):
                captured_params.append(tuple(params))
                return FakeCursor()

        class FakeStore:
            def __init__(self, _path, wal=True):
                self.connection = FakeConnection()

            def close(self):
                return None

        class MockConfig:
            class storage:
                path = str(db_path)

        with patch(
            "openminion.modules.storage.record_store.RecordStoreSQLite", FakeStore
        ):
            result = _get_module_usage_debug_info(
                config=MockConfig(),
                session_id="test-session",
            )

        assert "modules" in result
        assert captured_params
        assert all(len(params) == 2 for params in captured_params)
        assert all(
            "weather.openmeteo.current" not in str(params) for params in captured_params
        )

    def test_module_usage_debug_info_handles_missing_storage(self):
        from openminion.cli.chat.commands import _get_module_usage_debug_info

        class MockConfig:
            class storage:
                path = ""

        result = _get_module_usage_debug_info(
            config=MockConfig(),
            session_id="test-session",
        )

        assert "modules" in result
        for module_info in result["modules"].values():
            assert not module_info["available"]
            assert module_info["degraded_reason"] is not None


class TestNegativePaths:
    def test_inventory_intent_does_not_trigger_run_command(self):
        from openminion.modules.brain.runner import BrainRunner

        runner = BrainRunner.__new__(BrainRunner)

        assert not hasattr(runner, "_is_tool_inventory_intent")

    def test_legacy_plan_owner_stays_absent(self):
        import importlib.util

        assert importlib.util.find_spec("openminion.modules.brain.phases") is None

    def test_freshness_policy_blocks_stale_fabrication(self):
        from openminion.modules.brain.schemas import (
            WorkingState,
            ToolCommand,
            ActionResult,
            ActionError,
        )

        runner = _weather_runner()

        state = WorkingState(
            session_id="test",
            agent_id="test",
            goal="what's the weather in sf?",
            budgets_remaining={
                "ticks": 0,
                "tool_calls": 0,
                "a2a_calls": 0,
                "tokens": 0,
                "time_ms": 0,
            },
        )
        command = ToolCommand(
            title="Tool call: weather",
            tool_name="weather",
            args={"location": "san francisco"},
        )
        action_result = ActionResult(
            command_id=command.command_id,
            status="failed",
            summary="Weather API unavailable",
            error=ActionError(code="TIMEOUT", message="Request timed out"),
        )

        assert runner._is_time_sensitive_tool_command(command)

        response = runner._build_time_sensitive_failure_response(
            state=state,
            command=command,
            action_result=action_result,
        )

        assert "stale" in response.lower() or "cannot provide" in response.lower()
