from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Tuple

from openminion.modules.tool.contracts import ProviderToolSpec
from openminion.modules.tool.runtime.delegation import A2ADelegateApi
from openminion.modules.tool.runtime.memory import MemoryToolRuntimeService


@dataclass
class ToolExecutionContext:
    channel: str
    target: str
    session_id: str = ""
    metadata: Dict[str, str] = field(default_factory=dict)
    memory_service: MemoryToolRuntimeService | None = None
    sandbox_runner: Any | None = None
    authored_tools_api: Any | None = None
    # optional A2A delegation seam threaded to RuntimeContext so the
    # task.delegate handler can perform a real sub-agent delegation.
    a2a_delegate_api: A2ADelegateApi | None = None
    blast_radius_adapter: Any | None = None
    telemetryctl: Any | None = None


@dataclass
class ToolExecutionResult:
    tool_name: str
    ok: bool
    content: str
    verified: bool = False
    error: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    call_id: str = ""
    source: str = ""
    started_at: float | None = None
    ended_at: float | None = None
    duration_ms: int | None = None
    state: str = "ok"
    fallback_index: int = 0


@dataclass(frozen=True)
class ToolExecutionPolicy:
    required_scopes_all: tuple[str, ...] = ("tool.execute",)
    risk: str = "medium"
    budget_cost: int = 1


@dataclass(frozen=True)
class ToolCategoryInfo:
    primary_category: str = "general_assistance"
    secondary_categories: Tuple[str, ...] = ()


class Tool(ABC):
    name = "tool"
    description = "Tool"
    parameters: Dict[str, Any] = {}
    policy: ToolExecutionPolicy = ToolExecutionPolicy()
    categories: ToolCategoryInfo = ToolCategoryInfo()

    def provider_spec(self) -> ProviderToolSpec:
        return ProviderToolSpec(
            name=str(self.name).strip(),
            description=str(self.description).strip() or str(self.name).strip(),
            parameters=dict(self.parameters),
        )

    def execution_policy(self) -> ToolExecutionPolicy:
        profile = getattr(self, "policy", ToolExecutionPolicy())
        if isinstance(profile, ToolExecutionPolicy):
            scopes = _normalize_scope_tuple(profile.required_scopes_all)
            risk = str(profile.risk).strip().lower() or "medium"
            try:
                budget_cost = max(1, int(profile.budget_cost))
            except (TypeError, ValueError):
                budget_cost = 1
            return ToolExecutionPolicy(
                required_scopes_all=scopes or ("tool.execute",),
                risk=risk,
                budget_cost=budget_cost,
            )
        scopes = _normalize_scope_tuple(
            getattr(self, "required_scopes_all", ("tool.execute",))
        )
        risk = str(getattr(self, "risk", "medium")).strip().lower() or "medium"
        budget_cost = getattr(self, "budget_cost", 1)
        try:
            normalized_budget = max(1, int(budget_cost))
        except (TypeError, ValueError):
            normalized_budget = 1
        return ToolExecutionPolicy(
            required_scopes_all=scopes or ("tool.execute",),
            risk=risk,
            budget_cost=normalized_budget,
        )

    def category_info(self) -> ToolCategoryInfo:
        info = getattr(self, "categories", None)
        if isinstance(info, ToolCategoryInfo):
            return info
        primary = str(
            getattr(self, "primary_category", "general_assistance")
            or "general_assistance"
        ).strip()
        if not primary:
            primary = "general_assistance"
        secondary_raw = getattr(self, "secondary_categories", None)
        secondary: Tuple[str, ...] = ()
        if isinstance(secondary_raw, (list, tuple)):
            secondary = tuple(str(s).strip() for s in secondary_raw if str(s).strip())
        return ToolCategoryInfo(
            primary_category=primary,
            secondary_categories=secondary,
        )

    @abstractmethod
    def execute(
        self,
        arguments: Mapping[str, Any],
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        """Execute tool action for a single call."""


def _normalize_scope_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
    else:
        items = []
    normalized: list[str] = []
    for item in items:
        token = str(item or "").strip().lower()
        if token:
            normalized.append(token)
    return tuple(sorted(set(normalized)))


BaseTool = Tool
