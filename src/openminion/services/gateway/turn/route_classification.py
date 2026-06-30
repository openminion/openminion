import re
from dataclasses import dataclass
from typing import Mapping, Sequence

_FILE_HINT_RE = re.compile(
    r"(^|\s)(@?[./~]?[\w.-]+(?:/[\w.-]+)+|@?[\w.-]+\.(?:py|md|json|toml|yaml|yml|txt))\b"
)
_CODE_HINTS = frozenset({"edit", "change", "modify", "fix", "patch", "refactor"})
_LOCAL_HINTS = frozenset({"disk", "memory", "cpu", "process", "status", "ip"})
_RESEARCH_HINTS = frozenset({"research", "search", "lookup", "latest", "current"})


@dataclass(frozen=True)
class SetupCostRoute:
    label: str
    reason: str


def classify_setup_cost_route(
    *,
    message: str,
    forced_tools: Sequence[str] | None = None,
    capability_category: str | None = None,
    inbound_metadata: Mapping[str, str] | None = None,
) -> SetupCostRoute:
    text = str(message or "").strip()
    lowered = text.lower()
    words = set(re.findall(r"[a-z0-9_]+", lowered))
    forced_count = len(tuple(forced_tools or ()))
    category = str(capability_category or "").strip().lower()
    metadata = dict(inbound_metadata or {})

    if forced_count > 1:
        return SetupCostRoute("multi_tool_request", "multiple_forced_tools")
    if category in {"research", "web", "deep_research"}:
        return SetupCostRoute("research_request", "capability_category")
    if category in {"coding", "code"}:
        return SetupCostRoute("code_edit_request", "capability_category")
    if metadata.get("file_mentions") or _FILE_HINT_RE.search(text):
        if words & _CODE_HINTS:
            return SetupCostRoute("code_edit_request", "file_and_code_hint")
        return SetupCostRoute("file_context_request", "file_hint")
    if words & _RESEARCH_HINTS:
        return SetupCostRoute("research_request", "research_hint")
    if words & _LOCAL_HINTS:
        return SetupCostRoute("local_status_request", "local_status_hint")
    if forced_count == 1:
        return SetupCostRoute("tool_request", "single_forced_tool")
    if text and len(text) <= 120 and "?" not in text:
        return SetupCostRoute("no_tool_answer", "short_plain_prompt")
    return SetupCostRoute("ambiguous_request", "fallback")


__all__ = ["SetupCostRoute", "classify_setup_cost_route"]
