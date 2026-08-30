import json
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from openminion.modules.tool.registry import ToolRegistry, ToolSpec
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.tools.config import resolve_tool_workspace_root

from .interfaces import TOOL_HOST_INVENTORY_REPORT, TOOL_HOST_METRICS
from .schemas import HostInventoryReportArgs, HostMetricsArgs

_BYTE_UNITS = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
_INVENTORY_SCHEMA_VERSION = "openminion.local-system-inventory.v1"


def _format_bytes(value: int | None) -> str:
    if value is None:
        return "unknown"
    amount = float(max(0, value))
    unit = _BYTE_UNITS[0]
    for unit in _BYTE_UNITS:
        if amount < 1024.0 or unit == _BYTE_UNITS[-1]:
            break
        amount /= 1024.0
    if unit == "B":
        return f"{int(amount)} {unit}"
    return f"{amount:.1f} {unit}"


def _percent(used: int | None, total: int | None) -> float | None:
    if used is None or not total:
        return None
    return round((used / total) * 100.0, 1)


def _workspace_path(ctx: Any) -> Path:
    return Path(ctx.workspace)


def _disk_paths(requested: Path) -> list[Path]:
    paths: list[Path] = []
    devices: set[int] = set()
    current = requested
    while not current.exists() and current != current.parent:
        current = current.parent
    if current.exists():
        paths.append(current)
        try:
            devices.add(current.stat().st_dev)
        except OSError:
            pass
    root = Path(current.anchor or "/")
    if root.exists() and root not in paths:
        try:
            root_device = root.stat().st_dev
        except OSError:
            root_device = -1
        if root_device in devices:
            return paths
        paths.append(root)
    return paths


def _disk_usage(path: Path) -> dict[str, Any]:
    usage = shutil.disk_usage(path)
    used = usage.total - usage.free
    return {
        "path": str(path),
        "total_bytes": usage.total,
        "used_bytes": used,
        "free_bytes": usage.free,
        "used_percent": _percent(used, usage.total),
    }


def _parse_linux_meminfo() -> dict[str, int] | None:
    meminfo = Path("/proc/meminfo")
    if not meminfo.exists():
        return None
    values: dict[str, int] = {}
    try:
        for line in meminfo.read_text(encoding="utf-8", errors="replace").splitlines():
            key, _, rest = line.partition(":")
            if not key or not rest:
                continue
            amount = rest.strip().split(maxsplit=1)[0]
            values[key] = int(amount) * 1024
    except (OSError, ValueError):
        return None
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if total is None:
        return None
    return {
        "total_bytes": total,
        "available_bytes": available if available is not None else -1,
    }


def _sysconf_memory_total() -> int | None:
    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        page_count = int(os.sysconf("SC_PHYS_PAGES"))
    except (AttributeError, OSError, ValueError):
        return None
    if page_size <= 0 or page_count <= 0:
        return None
    return page_size * page_count


def _darwin_available_memory() -> int | None:
    vm_stat = Path("/usr/bin/vm_stat")
    if not vm_stat.exists():
        return None
    try:
        result = subprocess.run(
            [str(vm_stat)],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    text = result.stdout or ""
    page_match = re.search(r"page size of (\d+) bytes", text)
    page_size = int(page_match.group(1)) if page_match else 4096
    wanted = {"Pages free", "Pages inactive", "Pages speculative"}
    pages = 0
    for line in text.splitlines():
        key, _, raw_value = line.partition(":")
        if key not in wanted:
            continue
        token = raw_value.strip().rstrip(".").replace(",", "")
        try:
            pages += int(token)
        except ValueError:
            continue
    return pages * page_size if pages else None


def _memory_metrics() -> dict[str, Any]:
    source = "sysconf"
    linux = _parse_linux_meminfo()
    if linux is not None:
        source = "proc.meminfo"
        total = linux["total_bytes"]
        available = linux["available_bytes"]
        if available < 0:
            available = None
    else:
        total = _sysconf_memory_total()
        available = None

    if platform.system().lower() == "darwin":
        darwin_available = _darwin_available_memory()
        if darwin_available is not None:
            available = darwin_available
            source = "darwin.vm_stat"

    used = total - available if total is not None and available is not None else None
    return {
        "total_bytes": total,
        "available_bytes": available,
        "used_bytes": used,
        "used_percent": _percent(used, total),
        "source": source,
    }


def _platform_metrics() -> dict[str, str]:
    uname = platform.uname()
    return {
        "system": uname.system,
        "release": uname.release,
        "version": uname.version,
        "machine": uname.machine,
        "processor": uname.processor,
        "python": platform.python_version(),
    }


def _content(data: dict[str, Any]) -> str:
    lines: list[str] = []
    platform_data = data["platform"]
    system = platform_data.get("system") or "unknown"
    release = (platform_data.get("release") or "").strip()
    machine = (platform_data.get("machine") or "").strip()
    suffix = " ".join(item for item in (release, machine) if item)
    lines.append(f"Host: {system}{f' {suffix}' if suffix else ''}")

    disks = data.get("disk")
    if disks:
        lines.append("Disk:")
        for item in disks:
            total = _format_bytes(item.get("total_bytes"))
            used = _format_bytes(item.get("used_bytes"))
            free = _format_bytes(item.get("free_bytes"))
            percent = item.get("used_percent")
            percent_text = f", {percent}% used" if percent is not None else ""
            lines.append(
                f"- {item.get('path')}: {used} used / {total} total "
                f"({free} free{percent_text})"
            )

    memory = data.get("memory")
    if memory is not None:
        total = _format_bytes(memory.get("total_bytes"))
        used = _format_bytes(memory.get("used_bytes"))
        available = _format_bytes(memory.get("available_bytes"))
        percent = memory.get("used_percent")
        percent_text = f", {percent}% used" if percent is not None else ""
        lines.append(
            f"Memory: {used} used / {total} total ({available} available{percent_text})"
        )
    return "\n".join(lines) if lines else "Host metrics unavailable."


def collect_host_metrics(
    workspace: Path,
    *,
    path: str | None = None,
    include_disk: bool = True,
    include_memory: bool = True,
) -> tuple[dict[str, Any], list[str]]:
    data: dict[str, Any] = {
        "source": "openminion-tool-host",
        "method": TOOL_HOST_METRICS,
        "platform": _platform_metrics(),
    }
    warnings: list[str] = []

    if include_disk:
        requested = Path(path).expanduser() if path else workspace
        if not requested.is_absolute():
            requested = workspace / requested
        disks: list[dict[str, Any]] = []
        for path in _disk_paths(requested):
            try:
                disks.append(_disk_usage(path))
            except OSError as exc:
                warnings.append(f"disk usage unavailable for {path}: {exc}")
        data["disk"] = disks

    if include_memory:
        data["memory"] = _memory_metrics()

    return data, warnings


def _h_metrics(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    parsed = HostMetricsArgs.model_validate(args)
    data, warnings = collect_host_metrics(
        _workspace_path(ctx),
        path=parsed.path,
        include_disk=parsed.include_disk,
        include_memory=parsed.include_memory,
    )

    return {
        "ok": True,
        "content": _content(data),
        "data": data,
        "warnings": warnings,
        "verified": True,
    }


def _inventory_markdown(report: dict[str, Any]) -> str:
    def display(value: Any) -> str:
        return "unknown" if value is None or value == "" else str(value)

    lines = ["# Local System Inventory", "", "## Platform"]
    for key, value in report["platform"].items():
        lines.append(f"- {key}: {display(value)}")

    lines.extend(["", "## Memory"])
    memory = report["memory"] or {}
    if memory:
        for key, value in memory.items():
            lines.append(f"- {key}: {display(value)}")
    else:
        lines.append("- unknown")

    lines.extend(["", "## Disk"])
    disks = report["disk"]
    if disks:
        for index, disk in enumerate(disks, start=1):
            lines.append(f"### Disk {index}")
            for key, value in disk.items():
                lines.append(f"- {key}: {display(value)}")
    else:
        lines.append("- unknown")

    lines.extend(["", "## Warnings"])
    warnings = report["warnings"]
    lines.extend(f"- {warning}" for warning in warnings)
    if not warnings:
        lines.append("- None")
    return "\n".join(lines) + "\n"


def _inventory_output_dir(ctx: Any, raw_output_dir: str) -> tuple[Path, Path]:
    workspace = resolve_tool_workspace_root(context=ctx, fallback=_workspace_path(ctx))
    candidate = Path(raw_output_dir).expanduser()
    if not candidate.is_absolute():
        candidate = workspace / candidate
    output_dir = candidate.resolve(strict=False)
    try:
        output_dir.relative_to(workspace)
    except ValueError as exc:
        raise ToolRuntimeError(
            "POLICY_DENIED",
            f"output directory escapes workspace root: {raw_output_dir}",
        ) from exc
    ctx.policy.ensure_path_allowed(
        str(output_dir),
        workspace=workspace,
        operation="write",
    )
    return workspace, output_dir


def _h_inventory_report(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    parsed = HostInventoryReportArgs.model_validate(args)
    workspace, output_dir = _inventory_output_dir(ctx, parsed.output_dir)
    json_path = output_dir / "system-inventory.json"
    markdown_path = output_dir / "system-inventory.md"
    if not parsed.overwrite and (json_path.exists() or markdown_path.exists()):
        raise ToolRuntimeError(
            "ALREADY_EXISTS",
            "inventory report already exists; set overwrite=true to replace it",
        )

    data, warnings = collect_host_metrics(_workspace_path(ctx))
    disk = []
    for item in data.get("disk", []):
        report_item = dict(item)
        measured_path = Path(str(report_item.get("path", ""))).resolve(strict=False)
        report_item["path"] = (
            "workspace" if measured_path == workspace else measured_path.anchor
        )
        disk.append(report_item)

    report = {
        "schema_version": _INVENTORY_SCHEMA_VERSION,
        "source": data["source"],
        "platform": data["platform"],
        "memory": data.get("memory"),
        "disk": disk,
        "warnings": warnings,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(_inventory_markdown(report), encoding="utf-8")
    return {
        "ok": True,
        "content": f"Wrote and verified local inventory reports in {parsed.output_dir}.",
        "data": {
            "method": TOOL_HOST_INVENTORY_REPORT,
            "schema_version": _INVENTORY_SCHEMA_VERSION,
            "json_path": str(json_path.relative_to(workspace)),
            "markdown_path": str(markdown_path.relative_to(workspace)),
            "platform_system": data["platform"].get("system"),
        },
        "warnings": warnings,
        "verified": True,
    }


def register(registry: ToolRegistry) -> None:
    registry.add(
        ToolSpec(
            name=TOOL_HOST_METRICS,
            args_model=HostMetricsArgs,
            min_scope="READ_ONLY",
            handler=_h_metrics,
            dangerous=False,
            idempotent=True,
            tags=("plugin", "host"),
            capabilities=("read_only", "host", "metrics", "system", "resources"),
        )
    )
    registry.add(
        ToolSpec(
            name=TOOL_HOST_INVENTORY_REPORT,
            args_model=HostInventoryReportArgs,
            min_scope="WRITE_SAFE",
            handler=_h_inventory_report,
            dangerous=True,
            idempotent=False,
            block_under_readonly=True,
            tags=("plugin", "host"),
            capabilities=("host", "metrics", "system", "resources", "file.write"),
        )
    )


__all__ = ["collect_host_metrics", "register"]
