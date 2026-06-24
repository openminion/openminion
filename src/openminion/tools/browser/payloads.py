"""Browser payload and selector normalization."""

from pathlib import Path
from typing import Any, Mapping

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


def extract_tabs(payload: Any, *, to_tab_info) -> list[TabInfo]:  # type: ignore[no-untyped-def]
    rows: list[Any] = []
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, Mapping):
        for key in ("tabs", "items", "data", "result"):
            value = payload.get(key)
            if isinstance(value, list):
                rows = value
                break
            if isinstance(value, Mapping):
                nested = value.get("items")
                if isinstance(nested, list):
                    rows = nested
                    break
    return [
        to_tab_info(row)
        for row in rows
        if isinstance(row, Mapping)
        and str(row.get("id") or row.get("tabId") or row.get("tab_id") or "").strip()
    ]


def extract_instances(payload: Any) -> list[InstanceInfo]:
    rows: list[Any] = []
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, Mapping):
        for key in ("instances", "items", "data", "result"):
            value = payload.get(key)
            if isinstance(value, list):
                rows = value
                break
            if isinstance(value, Mapping):
                nested = value.get("items")
                if isinstance(nested, list):
                    rows = nested
                    break
    out: list[InstanceInfo] = []
    for row in rows:
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


def extract_instance_id(payload: Mapping[str, Any]) -> str:
    instance = payload.get("instance")
    if isinstance(instance, Mapping):
        value = (
            instance.get("id")
            or instance.get("instance_id")
            or instance.get("instanceId")
        )
        if value is not None:
            return str(value).strip()
    for key in ("instance_id", "instanceId", "id"):
        value = payload.get(key)
        if value is None:
            continue
        if isinstance(value, Mapping):
            nested = (
                value.get("id") or value.get("instance_id") or value.get("instanceId")
            )
            if nested is not None:
                return str(nested).strip()
            continue
        return str(value).strip()
    return ""


def extract_tab_id(payload: Mapping[str, Any]) -> str:
    tab = payload.get("tab")
    if isinstance(tab, Mapping):
        value = tab.get("id") or tab.get("tab_id") or tab.get("tabId")
        if value is not None:
            return str(value).strip()
    for key in ("tab_id", "tabId", "id"):
        value = payload.get(key)
        if value is None:
            continue
        if isinstance(value, Mapping):
            nested = value.get("id") or value.get("tab_id") or value.get("tabId")
            if nested is not None:
                return str(nested).strip()
            continue
        return str(value).strip()
    return ""


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
