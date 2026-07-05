import re
from dataclasses import dataclass
from typing import Mapping, Sequence

_FILE_HINT_RE = re.compile(
    r"(^|\s)(@?[./~]?[\w.-]+(?:/[\w.-]+)+|@?[\w.-]+\.(?:py|md|json|toml|yaml|yml|txt))\b"
)
_RESEARCH_CATEGORIES = frozenset(
    {
        "deep_research",
        "live",
        "live_information",
        "research",
        "search",
        "web",
        "web.search",
    }
)
_CODE_EDIT_CATEGORIES = frozenset(
    {
        "artifact_change",
        "capability:cleanup",
        "cleanup",
        "code",
        "coding",
        "dev",
    }
)
_FILE_CONTEXT_CATEGORIES = frozenset(
    {
        "file",
        "file.find",
        "file.list_dir",
        "file.read",
        "file.read_range",
        "file_list",
    }
)
_LOCAL_STATUS_CATEGORIES = frozenset({"host.metrics", "ops", "system.exec"})


@dataclass(frozen=True)
class SetupCostRoute:
    label: str
    reason: str


def _route_for_capability_category(category: str) -> SetupCostRoute | None:
    if not category:
        return None
    if category in _RESEARCH_CATEGORIES:
        return SetupCostRoute("research_request", "capability_category")
    if category in _CODE_EDIT_CATEGORIES:
        return SetupCostRoute("code_edit_request", "capability_category")
    if category in _FILE_CONTEXT_CATEGORIES:
        return SetupCostRoute("file_context_request", "capability_category")
    if category in _LOCAL_STATUS_CATEGORIES:
        return SetupCostRoute("local_status_request", "capability_category")
    return None


def classify_setup_cost_route(
    *,
    message: str,
    forced_tools: Sequence[str] | None = None,
    capability_category: str | None = None,
    inbound_metadata: Mapping[str, str] | None = None,
) -> SetupCostRoute:
    text = str(message or "").strip()
    words = set(re.findall(r"[a-z0-9_]+", text.lower()))
    forced_count = len(forced_tools or ())
    category = str(capability_category or "").strip().lower()
    metadata = inbound_metadata or {}

    if forced_count > 1:
        return SetupCostRoute("multi_tool_request", "multiple_forced_tools")
    category_route = _route_for_capability_category(category)
    if category_route is not None:
        return category_route
    if metadata.get("file_mentions") or _FILE_HINT_RE.search(text):
        return SetupCostRoute("file_context_request", "file_hint")
    if forced_count == 1:
        return SetupCostRoute("tool_request", "single_forced_tool")
    if text and len(text) <= 120 and len(words) <= 8 and "?" not in text:
        return SetupCostRoute("no_tool_answer", "short_plain_prompt")
    return SetupCostRoute("ambiguous_request", "fallback")


__all__ = ["SetupCostRoute", "classify_setup_cost_route"]
