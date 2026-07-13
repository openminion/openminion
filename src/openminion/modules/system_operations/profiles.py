from __future__ import annotations

from collections.abc import Callable, Mapping

from .schemas import OperationRequest, TargetPlatform

ProfileBuilder = Callable[
    [Mapping[str, str | int | bool], TargetPlatform], tuple[str, ...]
]


def _required(parameters: Mapping[str, str | int | bool], name: str) -> str:
    value = str(parameters.get(name, "")).strip()
    if not value:
        raise ValueError(f"operation profile requires parameter: {name}")
    return value


def _host_snapshot(
    _: Mapping[str, str | int | bool], __: TargetPlatform
) -> tuple[str, ...]:
    return ("uname", "-a")


def _service(
    parameters: Mapping[str, str | int | bool], target_platform: TargetPlatform
) -> tuple[str, ...]:
    service = _required(parameters, "service")
    if target_platform == "darwin":
        return ("launchctl", "print", service)
    return ("systemctl", "show", service, "--no-pager")


def _logs(
    parameters: Mapping[str, str | int | bool], target_platform: TargetPlatform
) -> tuple[str, ...]:
    service = _required(parameters, "service")
    limit = min(max(int(parameters.get("limit", 100)), 1), 500)
    if target_platform == "darwin":
        return (
            "log",
            "show",
            "--style",
            "compact",
            "--last",
            "1h",
            "--predicate",
            f'process == "{service}"',
        )
    return ("journalctl", "-u", service, "-n", str(limit), "--no-pager")


def _network(
    _: Mapping[str, str | int | bool], target_platform: TargetPlatform
) -> tuple[str, ...]:
    if target_platform == "darwin":
        return ("netstat", "-an")
    return ("ss", "-tunlp")


def _disk(_: Mapping[str, str | int | bool], __: TargetPlatform) -> tuple[str, ...]:
    return ("df", "-h")


def _memory(
    _: Mapping[str, str | int | bool], target_platform: TargetPlatform
) -> tuple[str, ...]:
    if target_platform == "darwin":
        return ("vm_stat",)
    return ("free", "-h")


def _processes(
    _: Mapping[str, str | int | bool], __: TargetPlatform
) -> tuple[str, ...]:
    return ("ps", "-axo", "pid,ppid,user,%cpu,%mem,command")


PROFILE_BUILDERS: dict[str, ProfileBuilder] = {
    "host.snapshot": _host_snapshot,
    "service.inspect": _service,
    "logs.query": _logs,
    "network.inspect": _network,
    "disk.usage": _disk,
    "memory.usage": _memory,
    "process.list": _processes,
}

PROFILE_PARAMETERS: dict[str, frozenset[str]] = {
    "host.snapshot": frozenset(),
    "service.inspect": frozenset({"service"}),
    "logs.query": frozenset({"service", "limit"}),
    "network.inspect": frozenset(),
    "disk.usage": frozenset(),
    "memory.usage": frozenset(),
    "process.list": frozenset(),
}


def build_argv(
    request: OperationRequest, *, target_platform: TargetPlatform
) -> tuple[str, ...]:
    try:
        builder = PROFILE_BUILDERS[request.profile_id]
    except KeyError as exc:
        raise ValueError(f"unknown operation profile: {request.profile_id}") from exc
    unknown = set(request.parameters) - PROFILE_PARAMETERS[request.profile_id]
    if unknown:
        raise ValueError(
            f"operation profile received unknown parameters: {sorted(unknown)!r}"
        )
    return builder(request.parameters, target_platform)
