from __future__ import annotations

from collections.abc import Callable, Mapping

from .contracts import OperationRequest, TargetPlatform

ProfileBuilder = Callable[
    [Mapping[str, str | int | bool], TargetPlatform], tuple[str, ...]
]


def _powershell(script: str, *parameters: str) -> tuple[str, ...]:
    return (
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        script,
        *parameters,
    )


def _required(parameters: Mapping[str, str | int | bool], name: str) -> str:
    value = str(parameters.get(name, "")).strip()
    if not value:
        raise ValueError(f"operation profile requires parameter: {name}")
    return value


def _host_snapshot(
    _: Mapping[str, str | int | bool], target_platform: TargetPlatform
) -> tuple[str, ...]:
    if target_platform == "windows":
        return _powershell(
            "Get-CimInstance Win32_OperatingSystem | "
            "Select-Object Caption,Version,LastBootUpTime"
        )
    return ("uname", "-a")


def _service_inspect_argv(
    parameters: Mapping[str, str | int | bool], target_platform: TargetPlatform
) -> tuple[str, ...]:
    service = _required(parameters, "service")
    if target_platform == "windows":
        return _powershell("Get-Service -Name $args[0]", service)
    if target_platform == "darwin":
        return ("launchctl", "print", service)
    return ("systemctl", "show", service, "--no-pager")


def _logs(
    parameters: Mapping[str, str | int | bool], target_platform: TargetPlatform
) -> tuple[str, ...]:
    service = _required(parameters, "service")
    limit = min(max(int(parameters.get("limit", 100)), 1), 500)
    if target_platform == "windows":
        return _powershell(
            "Get-WinEvent -LogName Application -MaxEvents $args[1] | "
            "Where-Object ProviderName -EQ $args[0]",
            service,
            str(limit),
        )
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
    if target_platform == "windows":
        return _powershell("Get-NetTCPConnection")
    if target_platform == "darwin":
        return ("netstat", "-an")
    return ("ss", "-tunlp")


def _disk(
    _: Mapping[str, str | int | bool], target_platform: TargetPlatform
) -> tuple[str, ...]:
    if target_platform == "windows":
        return _powershell(
            "Get-CimInstance Win32_LogicalDisk | Select-Object DeviceID,Size,FreeSpace"
        )
    return ("df", "-h")


def _memory(
    _: Mapping[str, str | int | bool], target_platform: TargetPlatform
) -> tuple[str, ...]:
    if target_platform == "windows":
        return _powershell(
            "Get-CimInstance Win32_OperatingSystem | "
            "Select-Object TotalVisibleMemorySize,FreePhysicalMemory"
        )
    if target_platform == "darwin":
        return ("vm_stat",)
    return ("free", "-h")


def _processes(
    _: Mapping[str, str | int | bool], target_platform: TargetPlatform
) -> tuple[str, ...]:
    if target_platform == "windows":
        return _powershell("Get-Process | Select-Object Id,Name,CPU,WorkingSet")
    return ("ps", "-axo", "pid,ppid,user,%cpu,%mem,command")


def _bounded_int(
    parameters: Mapping[str, str | int | bool], name: str, maximum: int
) -> int:
    value = parameters.get(name)
    if isinstance(value, bool):
        raise ValueError(f"operation profile requires integer parameter: {name}")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"operation profile requires integer parameter: {name}"
        ) from exc
    if not 1 <= parsed <= maximum:
        raise ValueError(f"operation profile parameter out of range: {name}")
    return parsed


def _process_inspect_argv(
    parameters: Mapping[str, str | int | bool], target_platform: TargetPlatform
) -> tuple[str, ...]:
    pid = _bounded_int(parameters, "pid", 4_194_304)
    if target_platform == "windows":
        return _powershell("Get-Process -Id $args[0]", str(pid))
    return ("ps", "-p", str(pid), "-o", "pid=,ppid=,user=,%cpu=,%mem=,etime=,comm=")


def _port_owner(
    parameters: Mapping[str, str | int | bool], target_platform: TargetPlatform
) -> tuple[str, ...]:
    port = _bounded_int(parameters, "port", 65_535)
    protocol = _required(parameters, "protocol").lower()
    if protocol not in {"tcp", "udp"}:
        raise ValueError("operation profile protocol must be tcp or udp")
    if target_platform == "windows":
        command = (
            "Get-NetTCPConnection -LocalPort $args[0]"
            if protocol == "tcp"
            else "Get-NetUDPEndpoint -LocalPort $args[0]"
        )
        return _powershell(command, str(port))
    if target_platform == "darwin":
        selector = f"-i{protocol.upper()}:{port}"
        return (
            ("lsof", "-nP", selector, "-sTCP:LISTEN")
            if protocol == "tcp"
            else ("lsof", "-nP", selector)
        )
    mode = "-ltnp" if protocol == "tcp" else "-lunp"
    return ("ss", mode, "sport", "=", f":{port}")


PROFILE_BUILDERS: dict[str, ProfileBuilder] = {
    "host.snapshot": _host_snapshot,
    "service.inspect": _service_inspect_argv,
    "logs.query": _logs,
    "network.inspect": _network,
    "disk.usage": _disk,
    "memory.usage": _memory,
    "process.list": _processes,
    "process.inspect": _process_inspect_argv,
    "network.port_owner": _port_owner,
}

PROFILE_PARAMETERS: dict[str, frozenset[str]] = {
    "host.snapshot": frozenset(),
    "service.inspect": frozenset({"service"}),
    "logs.query": frozenset({"service", "limit"}),
    "network.inspect": frozenset(),
    "disk.usage": frozenset(),
    "memory.usage": frozenset(),
    "process.list": frozenset(),
    "process.inspect": frozenset({"pid"}),
    "network.port_owner": frozenset({"port", "protocol"}),
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
