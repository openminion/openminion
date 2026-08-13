"""Browser payload and selector normalization."""

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from openminion.modules.tool.runtime.resource_selectors import ResourceSelectors

from .constants import BLANK_BROWSER_URLS, NEW_TAB_URL_PREFIXES
from .models import InstanceInfo, TabInfo


def merge_unique_tuples(
    left: tuple[Any, ...], right: tuple[Any, ...]
) -> tuple[Any, ...]:
    out: list[Any] = []
    seen_hashable: set[Any] = set()
    seen_unhashable: list[Any] = []
    for item in left + right:
        try:
            if item in seen_hashable:
                continue
            seen_hashable.add(item)
            out.append(item)
        except TypeError:
            if any(existing == item for existing in seen_unhashable):
                continue
            seen_unhashable.append(item)
            out.append(item)
    return tuple(out)


def merge_resource_selectors(
    primary: ResourceSelectors, secondary: ResourceSelectors
) -> ResourceSelectors:
    return ResourceSelectors(
        paths_read=merge_unique_tuples(primary.paths_read, secondary.paths_read),
        paths_write=merge_unique_tuples(primary.paths_write, secondary.paths_write),
        paths_delete=merge_unique_tuples(primary.paths_delete, secondary.paths_delete),
        command=secondary.command or primary.command,
        args=merge_unique_tuples(primary.args, secondary.args),
        cwd=secondary.cwd or primary.cwd,
        env_keys_requested=merge_unique_tuples(
            primary.env_keys_requested, secondary.env_keys_requested
        ),
        domains=merge_unique_tuples(primary.domains, secondary.domains),
        hosts=merge_unique_tuples(primary.hosts, secondary.hosts),
        ports=merge_unique_tuples(primary.ports, secondary.ports),
        protocols=merge_unique_tuples(primary.protocols, secondary.protocols),
    )


def normalize_path(path: str, base: str) -> str:
    base_root = Path(str(base)).expanduser().resolve(strict=False)
    candidate = Path(str(path)).expanduser()
    if not candidate.is_absolute():
        candidate = base_root / candidate
    resolved = candidate.resolve(strict=False)
    resolved.relative_to(base_root)
    return str(resolved)


def is_meaningful_url(url: str) -> bool:
    token = str(url or "").strip().lower()
    if not token:
        return False
    if token in BLANK_BROWSER_URLS:
        return False
    if any(token.startswith(prefix) for prefix in NEW_TAB_URL_PREFIXES):
        return False
    return True


def to_payload(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    return {"result": value}


def _extract_rows(payload: Any, keys: tuple[str, ...]) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, Mapping):
        return []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return value
        nested = value.get("items") if isinstance(value, Mapping) else None
        if isinstance(nested, list):
            return nested
    return []


def extract_tabs(
    payload: Any, *, to_tab_info: Callable[[Mapping[str, Any]], TabInfo]
) -> list[TabInfo]:
    return [
        to_tab_info(row)
        for row in _extract_rows(payload, ("tabs", "items", "data", "result"))
        if isinstance(row, Mapping)
        and str(row.get("id") or row.get("tabId") or row.get("tab_id") or "").strip()
    ]


def extract_instances(payload: Any) -> list[InstanceInfo]:
    out: list[InstanceInfo] = []
    for row in _extract_rows(payload, ("instances", "items", "data", "result")):
        if not isinstance(row, Mapping):
            continue
        instance_id = str(
            row.get("id") or row.get("instance_id") or row.get("instanceId") or ""
        ).strip()
        if not instance_id:
            continue
        out.append(
            InstanceInfo(
                id=instance_id,
                profile=str(row.get("profile"))
                if row.get("profile") is not None
                else None,
                mode=str(row.get("mode")) if row.get("mode") is not None else None,
            )
        )
    return out


def _extract_id(
    payload: Mapping[str, Any], group: str, aliases: tuple[str, str, str]
) -> str:
    values = (
        payload.get(group),
        payload.get(aliases[1]),
        payload.get(aliases[2]),
        payload.get(aliases[0]),
    )
    for value in values:
        if value is None:
            continue
        if isinstance(value, Mapping):
            nested = (
                value.get(aliases[0]) or value.get(aliases[1]) or value.get(aliases[2])
            )
            if nested is not None:
                return str(nested).strip()
            continue
        return str(value).strip()
    return ""


def extract_instance_id(payload: Mapping[str, Any]) -> str:
    return _extract_id(payload, "instance", ("id", "instance_id", "instanceId"))


def extract_tab_id(payload: Mapping[str, Any]) -> str:
    return _extract_id(payload, "tab", ("id", "tab_id", "tabId"))


def is_stale_recoverable_error(exc: Exception) -> bool:
    if isinstance(exc, KeyError):
        token = str(exc).lower()
        return "tab not found" in token or "instance not found" in token

    status = getattr(exc, "status", None)
    body = str(getattr(exc, "body", "")).strip().lower()
    message = str(exc).strip().lower()
    token = " ".join(part for part in (message, body) if part).strip()

    stale_markers = (
        "tab not found",
        "instance not found",
        "not running",
        "status: starting",
        "stale",
        "target closed",
        "context closed",
        "no such tab",
        "no such window",
        "session deleted",
    )
    if any(marker in token for marker in stale_markers):
        return True
    if isinstance(status, int) and status in {404, 409, 410, 503}:
        return True
    return False


__all__ = [
    "extract_instance_id",
    "extract_instances",
    "extract_tab_id",
    "extract_tabs",
    "is_meaningful_url",
    "is_stale_recoverable_error",
    "merge_resource_selectors",
    "merge_unique_tuples",
    "normalize_path",
    "to_payload",
]
