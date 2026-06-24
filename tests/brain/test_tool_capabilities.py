from __future__ import annotations

from openminion.modules.brain.schemas import ToolCommand
from openminion.modules.brain.tools.capabilities import (
    browser_capabilities_for_op,
    command_capabilities,
    commands_cover_sub_intents,
    coverage_missing_sub_intents,
    exec_capabilities_for_tool,
    file_capabilities_for_tool,
    location_capabilities_for_tool,
    search_capabilities_for_tool,
    time_capabilities_for_tool,
    weather_capabilities_for_tool,
)


def test_browser_registry_maps_known_ops() -> None:
    assert browser_capabilities_for_op("instance.start") == ("start_browser",)
    assert browser_capabilities_for_op("tab.navigate") == (
        "start_browser",
        "navigate_to_url",
    )
    assert browser_capabilities_for_op("unknown.op") == ()


def test_browser_command_capabilities_from_browser_tool_op() -> None:
    command = ToolCommand(
        title="navigate browser",
        tool_name="browser",
        args={"op": "tab.navigate", "url": "https://example.com"},
        success_criteria={"status": "success"},
    )
    assert command_capabilities(command) == {"start_browser", "navigate_to_url"}


def test_browser_command_capabilities_do_not_bridge_playwright_aliases() -> None:
    command = ToolCommand(
        title="playwright navigate",
        tool_name="browser.playwright.navigate",
        args={"url": "https://example.com"},
        success_criteria={"status": "success"},
    )
    assert command_capabilities(command) == set()


def test_file_and_exec_capability_maps_are_available() -> None:
    assert set(file_capabilities_for_tool("file.write")) == {
        "write_file",
        "create_file",
    }
    assert set(exec_capabilities_for_tool("exec.run")) == {"start_shell", "run_command"}


def test_weather_time_search_capability_maps_are_available() -> None:
    assert set(weather_capabilities_for_tool("weather")) >= {
        "check_weather",
        "get_weather",
    }
    assert set(time_capabilities_for_tool("time")) >= {"check_time", "get_time"}
    assert set(search_capabilities_for_tool("web.search")) >= {
        "search_web",
        "web_search",
    }
    assert set(location_capabilities_for_tool("location")) >= {
        "get_location",
        "get_current_location",
    }


def test_runtime_aliases_no_longer_report_canonical_capabilities() -> None:
    assert weather_capabilities_for_tool("weather.openmeteo.current") == ()
    assert time_capabilities_for_tool("time.now") == ()
    assert search_capabilities_for_tool("search.tavily.search") == ()
    assert location_capabilities_for_tool("location.get") == ()


def test_weather_command_covers_weather_sub_intents() -> None:
    command = ToolCommand(
        title="get weather sf",
        tool_name="weather",
        args={"location": "San Francisco"},
        success_criteria={"status": "success"},
    )
    assert commands_cover_sub_intents(
        sub_intents=["get_weather"],
        commands=[command],
    )
    assert (
        coverage_missing_sub_intents(
            sub_intents=["get_weather", "check_weather"],
            commands=[command],
        )
        == set()
    )


def test_location_command_covers_location_sub_intents() -> None:
    command = ToolCommand(
        title="get current location",
        tool_name="location",
        args={"max_privacy": "city"},
        success_criteria={"status": "success"},
    )
    assert commands_cover_sub_intents(
        sub_intents=["get_current_location"],
        commands=[command],
    )
    assert (
        coverage_missing_sub_intents(
            sub_intents=["get_location", "get_current_location"],
            commands=[command],
        )
        == set()
    )


def test_sub_intent_coverage_detects_missing_capabilities() -> None:
    start_only = ToolCommand(
        title="start browser",
        tool_name="browser",
        args={"op": "instance.start"},
        success_criteria={"status": "success"},
    )
    navigate = ToolCommand(
        title="navigate browser",
        tool_name="browser",
        args={"op": "tab.navigate", "url": "https://example.com"},
        success_criteria={"status": "success"},
    )

    assert not commands_cover_sub_intents(
        sub_intents=["start_browser", "navigate_to_url"],
        commands=[start_only],
    )
    assert coverage_missing_sub_intents(
        sub_intents=["start_browser", "navigate_to_url"],
        commands=[start_only],
    ) == {"navigate_to_url"}
    assert commands_cover_sub_intents(
        sub_intents=["start_browser", "navigate_to_url"],
        commands=[navigate],
    )
