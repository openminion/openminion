from collections.abc import Mapping as ABCMapping
from collections.abc import Sequence as ABCSequence
from typing import Any, Mapping, Sequence


def effective_command_argv(argv: Sequence[str]) -> tuple[str, ...]:
    effective = tuple(str(arg) for arg in argv)
    while effective and _is_posix_env_assignment(effective[0]):
        effective = effective[1:]
    return effective


DISCOVERY_KNOWN_TOOLS = (
    "as",
    "clang",
    "gcc",
    "ld",
    "make",
    "nasm",
    "objdump",
    "otool",
    "python",
    "python3",
    "python3.11",
)

COMMAND_ALLOW_PATTERNS = (
    {
        "id": "tool.discovery.command_v",
        "argv": ["command", "-v", "{known_tool}"],
        "action_class": "discovery",
    },
    {
        "id": "tool.discovery.which",
        "argv": ["which", "{known_tool}"],
        "action_class": "discovery",
    },
    {
        "id": "tool.discovery.version",
        "argv": ["{known_tool}", "--version"],
        "action_class": "discovery",
    },
    {
        "id": "platform.uname_machine",
        "argv": ["uname", "-m"],
        "action_class": "discovery",
    },
    {
        "id": "platform.uname_system",
        "argv": ["uname", "-s"],
        "action_class": "discovery",
    },
    {
        "id": "platform.macos_version",
        "argv": ["sw_vers"],
        "action_class": "discovery",
    },
    {
        "id": "platform.macos_machine",
        "argv": ["sysctl", "-n", "hw.machine"],
        "action_class": "discovery",
    },
)


def command_action_class(argv: Sequence[str]) -> str:
    argv = effective_command_argv(argv)
    if not argv:
        return "unknown"
    exec_name = str(argv[0] or "").strip().lower()
    if exec_name in {"rm", "dd", "mkfs", "shutdown", "reboot", "poweroff", "halt"}:
        return "destructive"
    if exec_name in {"sudo", "su"}:
        return "privileged"
    if exec_name in {"command", "which"}:
        return "discovery"
    if exec_name in {"pip", "pip3", "npm", "yarn"} and any(
        str(arg).strip().lower() == "install" for arg in argv[1:]
    ):
        return "install"
    if exec_name in {"as", "clang", "gcc", "ld", "make", "nasm"}:
        return "compile"
    if exec_name.startswith("./") or "/" in exec_name:
        return "run"
    return "unknown"


def matching_allow_pattern(
    argv: Sequence[str],
    commands: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    argv = effective_command_argv(argv)
    known_tools = _known_command_tools(commands)
    patterns = commands.get("allow_patterns", COMMAND_ALLOW_PATTERNS)
    if not isinstance(patterns, ABCSequence) or isinstance(patterns, (str, bytes)):
        return None

    for pattern in patterns:
        if not isinstance(pattern, ABCMapping):
            continue
        pattern_argv = pattern.get("argv", ())
        if not isinstance(pattern_argv, ABCSequence) or isinstance(
            pattern_argv, (str, bytes)
        ):
            continue
        if len(pattern_argv) != len(argv):
            continue
        if all(
            _match_pattern_token(str(expected), str(actual), known_tools)
            for expected, actual in zip(pattern_argv, argv)
        ):
            return pattern
    return None


def _known_command_tools(commands: Mapping[str, Any]) -> set[str]:
    configured = commands.get("known_tools", DISCOVERY_KNOWN_TOOLS)
    if not isinstance(configured, ABCSequence) or isinstance(configured, (str, bytes)):
        return set(DISCOVERY_KNOWN_TOOLS)
    return {str(tool).strip() for tool in configured if str(tool).strip()}


def _match_pattern_token(token: str, value: str, known_tools: set[str]) -> bool:
    if token == "{known_tool}":
        return value in known_tools
    return token == value


def _is_posix_env_assignment(token: str) -> bool:
    name, separator, _value = token.partition("=")
    if not separator or not name:
        return False
    if not (name[0].isalpha() or name[0] == "_"):
        return False
    return all(ch.isalnum() or ch == "_" for ch in name)
