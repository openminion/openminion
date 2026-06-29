from openminion.modules.tool.base import ToolCategoryInfo
from openminion.modules.tool.contracts import normalize_raw_model_tool_name
from openminion.modules.tool.contracts.model_ids import (
    MODEL_EXEC_KILL,
    MODEL_EXEC_LIST,
    MODEL_EXEC_POLL,
    MODEL_EXEC_RUN,
    MODEL_FILE_FIND,
    MODEL_FILE_LIST_DIR,
    MODEL_FILE_READ,
    MODEL_FILE_WRITE,
    MODEL_HOST_METRICS,
    MODEL_IP_LOCAL,
    MODEL_IP_PUBLIC,
    MODEL_LOCATION,
    MODEL_TIME,
    MODEL_WEATHER,
    MODEL_WEB_FETCH,
    MODEL_WEB_SEARCH,
)

# Canonical category mapping keyed by model-facing tool IDs and a minimal set of
# non-model runtime tool names that remain intentionally exposed.
DEFAULT_TOOL_CATEGORY_MAP: dict[str, ToolCategoryInfo] = {
    # Model-facing canonical IDs
    MODEL_FILE_LIST_DIR: ToolCategoryInfo(
        primary_category="file.list", secondary_categories=("file.read",)
    ),
    MODEL_FILE_READ: ToolCategoryInfo(
        primary_category="file.read", secondary_categories=("file.list",)
    ),
    MODEL_FILE_WRITE: ToolCategoryInfo(
        primary_category="file.write", secondary_categories=("file.read",)
    ),
    MODEL_FILE_FIND: ToolCategoryInfo(
        primary_category="file.search", secondary_categories=("file.list", "file.read")
    ),
    MODEL_EXEC_RUN: ToolCategoryInfo(
        primary_category="exec.run", secondary_categories=("process_control",)
    ),
    MODEL_EXEC_POLL: ToolCategoryInfo(primary_category="process_control"),
    MODEL_EXEC_KILL: ToolCategoryInfo(primary_category="process_control"),
    MODEL_EXEC_LIST: ToolCategoryInfo(primary_category="process_control"),
    MODEL_WEB_SEARCH: ToolCategoryInfo(
        primary_category="web.search",
        secondary_categories=("search.news", "search.web"),
    ),
    MODEL_WEB_FETCH: ToolCategoryInfo(primary_category="web.fetch"),
    MODEL_WEATHER: ToolCategoryInfo(primary_category="weather"),
    MODEL_TIME: ToolCategoryInfo(
        primary_category="time", secondary_categories=("time_util",)
    ),
    MODEL_LOCATION: ToolCategoryInfo(primary_category="location"),
    MODEL_HOST_METRICS: ToolCategoryInfo(
        primary_category="host.metrics", secondary_categories=("system", "resources")
    ),
    MODEL_IP_PUBLIC: ToolCategoryInfo(
        primary_category="network.public_ip", secondary_categories=("network",)
    ),
    MODEL_IP_LOCAL: ToolCategoryInfo(
        primary_category="network.local_ip", secondary_categories=("network",)
    ),
    "browser.screenshot": ToolCategoryInfo(primary_category="browser"),
    "browser.snapshot": ToolCategoryInfo(primary_category="browser"),
    "browser.text": ToolCategoryInfo(primary_category="browser"),
    "browser.pdf": ToolCategoryInfo(primary_category="browser"),
    "browser.tab.open": ToolCategoryInfo(primary_category="browser"),
    "browser.tab.list": ToolCategoryInfo(primary_category="browser"),
    "browser.tab.close": ToolCategoryInfo(primary_category="browser"),
    "browser.instance.start": ToolCategoryInfo(primary_category="browser"),
    "browser.instance.stop": ToolCategoryInfo(primary_category="browser"),
    "browser.health": ToolCategoryInfo(primary_category="browser"),
    # Explicit non-model tool surfaces
    "time.in_zone": ToolCategoryInfo(
        primary_category="time", secondary_categories=("time_util",)
    ),
    "time.convert": ToolCategoryInfo(
        primary_category="time_convert", secondary_categories=("time_util",)
    ),
    "time.parse_iso": ToolCategoryInfo(
        primary_category="time_parse", secondary_categories=("time_util",)
    ),
    "time.diff": ToolCategoryInfo(
        primary_category="time_diff", secondary_categories=("time_util",)
    ),
    "time.format": ToolCategoryInfo(
        primary_category="time_format", secondary_categories=("time_util",)
    ),
    "time.start_of_day": ToolCategoryInfo(
        primary_category="time_boundary", secondary_categories=("time_util",)
    ),
    "time.end_of_day": ToolCategoryInfo(
        primary_category="time_boundary", secondary_categories=("time_util",)
    ),
    "time.next_cron": ToolCategoryInfo(
        primary_category="time_schedule",
        secondary_categories=("cron_calc", "time_util"),
    ),
    "location.set_default": ToolCategoryInfo(primary_category="location_write"),
}


def _resolve_category_key(tool_name: str) -> str:
    token = str(tool_name or "").strip()
    if not token:
        return ""

    if token in DEFAULT_TOOL_CATEGORY_MAP:
        return token

    canonical = normalize_raw_model_tool_name(token)
    if canonical and canonical in DEFAULT_TOOL_CATEGORY_MAP:
        return canonical

    lowered = token.lower()
    if lowered in DEFAULT_TOOL_CATEGORY_MAP:
        return lowered

    canonical_lower = normalize_raw_model_tool_name(lowered)
    if canonical_lower and canonical_lower in DEFAULT_TOOL_CATEGORY_MAP:
        return canonical_lower

    return ""


def mapped_category_for_tool_name(tool_name: str) -> ToolCategoryInfo | None:
    key = _resolve_category_key(tool_name)
    if key:
        return DEFAULT_TOOL_CATEGORY_MAP.get(key)

    lowered = str(tool_name or "").strip().lower()
    if not lowered:
        return None

    if lowered == "browser" or lowered.startswith("browser."):
        return ToolCategoryInfo(primary_category="browser")
    return None


def heuristic_category_for_tool_name(tool_name: str) -> ToolCategoryInfo:
    lowered = str(tool_name or "").strip().lower()
    if not lowered:
        return ToolCategoryInfo(primary_category="general_assistance")

    if lowered.startswith("browser.") or lowered == "browser":
        return ToolCategoryInfo(primary_category="browser")
    if "weather" in lowered:
        return ToolCategoryInfo(primary_category="weather")
    if "location" in lowered:
        return ToolCategoryInfo(primary_category="location")
    if lowered.startswith("ip."):
        return ToolCategoryInfo(primary_category="network")
    if "search" in lowered or "tavily" in lowered:
        return ToolCategoryInfo(primary_category="web.search")
    if (
        lowered.startswith("web.")
        or lowered.startswith("http_")
        or lowered.startswith("http.")
    ):
        return ToolCategoryInfo(primary_category="web.fetch")
    if lowered.startswith("shell") or lowered.startswith("exec"):
        return ToolCategoryInfo(primary_category="exec.run")
    if "process" in lowered:
        return ToolCategoryInfo(primary_category="process_control")
    if lowered.startswith("read_") or ".read" in lowered:
        return ToolCategoryInfo(primary_category="file.read")
    if lowered.startswith("write_") or ".write" in lowered:
        return ToolCategoryInfo(primary_category="file.write")
    if "list" in lowered and "file" in lowered:
        return ToolCategoryInfo(
            primary_category="file.list", secondary_categories=("file.read",)
        )
    if "find" in lowered and "file" in lowered:
        return ToolCategoryInfo(
            primary_category="file.search",
            secondary_categories=("file.list", "file.read"),
        )

    return ToolCategoryInfo(primary_category="general_assistance")


def normalize_category_info(tool_name: str, info: ToolCategoryInfo) -> ToolCategoryInfo:
    primary = str(getattr(info, "primary_category", "") or "").strip()
    secondary_raw = list(getattr(info, "secondary_categories", ()) or ())
    secondary: list[str] = []
    for item in secondary_raw:
        token = str(item or "").strip()
        if token:
            secondary.append(token)

    normalized_primary = primary.lower()
    if normalized_primary == "uncategorized":
        primary = ""
        normalized_primary = ""

    mapped = mapped_category_for_tool_name(tool_name)
    if mapped is not None and (
        not primary or normalized_primary == "general_assistance"
    ):
        primary = str(mapped.primary_category or "").strip() or primary
        secondary.extend(
            str(item).strip()
            for item in mapped.secondary_categories
            if str(item).strip()
        )

    if not primary:
        fallback = heuristic_category_for_tool_name(tool_name)
        primary = str(fallback.primary_category or "").strip() or "general_assistance"
        secondary.extend(
            str(item).strip()
            for item in fallback.secondary_categories
            if str(item).strip()
        )

    cleaned_secondary: list[str] = []
    seen: set[str] = set()
    for item in secondary:
        token = str(item or "").strip()
        if not token or token == primary or token in seen:
            continue
        seen.add(token)
        cleaned_secondary.append(token)

    return ToolCategoryInfo(
        primary_category=primary or "general_assistance",
        secondary_categories=tuple(cleaned_secondary),
    )
