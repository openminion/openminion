from typing import Iterable

from openminion.modules.tool.contracts.model_ids import (
    MODEL_BROWSER,
    MODEL_LOCATION,
    MODEL_TIME,
    MODEL_WEATHER,
    MODEL_WEB_SEARCH,
)

from ..schemas import Command

# LTC-06/LTC-07: Static capability registry.
# Keep this registry runtime-owned and independent from LLM-declared metadata.
BROWSER_OP_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "instance.start": ("start_browser",),
    "instance.list": ("inspect_browser_instance",),
    "instance.stop": ("stop_browser",),
    "instance.kill": ("stop_browser",),
    "tab.new": ("start_browser", "open_tab"),
    "tab.list": ("inspect_tabs",),
    "tab.select": ("select_tab",),
    "tab.close": ("close_tab",),
    "tab.navigate": ("start_browser", "navigate_to_url"),
    "tab.snapshot": ("inspect_page",),
    "tab.text": ("inspect_page",),
    "tab.action": ("interact_with_page",),
    "tab.actions": ("interact_with_page",),
    "tab.screenshot": ("capture_screenshot",),
    "tab.pdf": ("capture_pdf",),
    "tab.lock": ("lock_tab",),
    "tab.unlock": ("unlock_tab",),
}

# Added in LTC-07.
FILE_TOOL_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "file.write": ("write_file", "create_file"),
    "file.read": ("read_file", "verify_file"),
    "file.list_dir": ("list_files", "verify_file"),
    "file.find": ("find_files",),
}

# Added in LTC-07.
EXEC_TOOL_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "exec.run": ("start_shell", "run_command"),
    "exec.poll": ("inspect_command_status",),
    "exec.list": ("inspect_command_status",),
    "exec.kill": ("stop_command",),
}

WEATHER_TOOL_CAPABILITIES: dict[str, tuple[str, ...]] = {
    MODEL_WEATHER: ("check_weather", "get_weather", "weather_lookup", "weather_query"),
}

TIME_TOOL_CAPABILITIES: dict[str, tuple[str, ...]] = {
    MODEL_TIME: ("check_time", "get_time", "time_lookup", "time_query"),
}

SEARCH_TOOL_CAPABILITIES: dict[str, tuple[str, ...]] = {
    MODEL_WEB_SEARCH: ("search_web", "web_search", "search_query", "research"),
}

LOCATION_TOOL_CAPABILITIES: dict[str, tuple[str, ...]] = {
    MODEL_LOCATION: (
        "get_location",
        "get_current_location",
        "location_query",
        "where_am_i",
    ),
}

KNOWN_CAPABILITY_SIGNALS: frozenset[str] = frozenset(
    signal
    for mapping in (
        BROWSER_OP_CAPABILITIES,
        FILE_TOOL_CAPABILITIES,
        EXEC_TOOL_CAPABILITIES,
        WEATHER_TOOL_CAPABILITIES,
        TIME_TOOL_CAPABILITIES,
        SEARCH_TOOL_CAPABILITIES,
        LOCATION_TOOL_CAPABILITIES,
    )
    for values in mapping.values()
    for signal in values
)


def _capabilities_for_tool(
    mapping: dict[str, tuple[str, ...]],
    tool_name: str | None,
) -> tuple[str, ...]:
    name = str(tool_name or "").strip().lower()
    if not name:
        return ()
    return mapping.get(name, ())


def browser_capabilities_for_op(op: str | None) -> tuple[str, ...]:
    op_name = str(op or "").strip().lower()
    if not op_name:
        return ()
    return BROWSER_OP_CAPABILITIES.get(op_name, ())


def file_capabilities_for_tool(tool_name: str | None) -> tuple[str, ...]:
    return _capabilities_for_tool(FILE_TOOL_CAPABILITIES, tool_name)


def exec_capabilities_for_tool(tool_name: str | None) -> tuple[str, ...]:
    return _capabilities_for_tool(EXEC_TOOL_CAPABILITIES, tool_name)


def weather_capabilities_for_tool(tool_name: str | None) -> tuple[str, ...]:
    return _capabilities_for_tool(WEATHER_TOOL_CAPABILITIES, tool_name)


def time_capabilities_for_tool(tool_name: str | None) -> tuple[str, ...]:
    return _capabilities_for_tool(TIME_TOOL_CAPABILITIES, tool_name)


def search_capabilities_for_tool(tool_name: str | None) -> tuple[str, ...]:
    return _capabilities_for_tool(SEARCH_TOOL_CAPABILITIES, tool_name)


def location_capabilities_for_tool(tool_name: str | None) -> tuple[str, ...]:
    return _capabilities_for_tool(LOCATION_TOOL_CAPABILITIES, tool_name)


def is_known_capability_signal(value: str | None) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return text in KNOWN_CAPABILITY_SIGNALS


def _normalize_browser_op_from_command(command: Command) -> str | None:
    if command.kind != "tool":
        return None
    tool_name = str(command.tool_name or "").strip().lower()
    if tool_name in {MODEL_BROWSER, "browser"}:
        raw_args = command.args if isinstance(command.args, dict) else {}
        if not raw_args and isinstance(command.inputs, dict):
            raw_args = command.inputs
        raw_op = raw_args.get("op")
        op = str(raw_op or "").strip().lower()
        return op or None
    return None


def command_capabilities(command: Command) -> set[str]:
    if command.kind != "tool":
        return set()

    caps = set(browser_capabilities_for_op(_normalize_browser_op_from_command(command)))
    if caps:
        return caps

    caps.update(weather_capabilities_for_tool(command.tool_name))
    caps.update(time_capabilities_for_tool(command.tool_name))
    caps.update(search_capabilities_for_tool(command.tool_name))
    caps.update(location_capabilities_for_tool(command.tool_name))
    caps.update(file_capabilities_for_tool(command.tool_name))
    caps.update(exec_capabilities_for_tool(command.tool_name))
    return caps


def covered_sub_intent_signals(commands: Iterable[Command]) -> set[str]:
    covered: set[str] = set()
    for command in commands:
        covered.update(
            {
                str(item or "").strip()
                for item in (getattr(command, "sub_intent_ids", []) or [])
                if str(item or "").strip()
            }
        )
        covered.update(command_capabilities(command))
    return covered


def coverage_missing_sub_intents(
    *,
    sub_intents: Iterable[str],
    commands: Iterable[Command],
) -> set[str]:
    expected = {
        str(intent or "").strip() for intent in sub_intents if str(intent or "").strip()
    }
    if not expected:
        return set()

    covered = covered_sub_intent_signals(commands)
    return expected - covered


def commands_cover_sub_intents(
    *,
    sub_intents: Iterable[str],
    commands: Iterable[Command],
) -> bool:
    return not coverage_missing_sub_intents(sub_intents=sub_intents, commands=commands)
