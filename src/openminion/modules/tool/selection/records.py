from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from openminion.modules.tool.contracts.model_ids import (
    MODEL_BROWSER,
    MODEL_EXEC_LIST,
    MODEL_EXEC_POLL,
    MODEL_EXEC_RUN,
    MODEL_FILE_EDIT,
    MODEL_FILE_FIND,
    MODEL_FILE_LIST_DIR,
    MODEL_FILE_READ,
    MODEL_FILE_SEARCH,
    MODEL_FILE_WRITE,
    MODEL_GITHUB_COMMIT_FILES,
    MODEL_GITHUB_FETCH_CHECKS,
    MODEL_GITHUB_FETCH_COMMENTS,
    MODEL_GITHUB_FETCH_DIFF,
    MODEL_GITHUB_FETCH_PR,
    MODEL_GITHUB_LIST_PRS,
    MODEL_GITHUB_OPEN_PR,
    MODEL_GITHUB_POST_PR_COMMENT,
    MODEL_GITHUB_POST_PR_REVIEW,
    MODEL_HOST_METRICS,
    MODEL_LOCATION,
    MODEL_TASK_CANCEL,
    MODEL_TASK_CONSOLIDATE_MEMORY,
    MODEL_TASK_LIST,
    MODEL_TASK_PAUSE,
    MODEL_TASK_RESUME,
    MODEL_TASK_SCHEDULE,
    MODEL_TASK_SHOW,
    MODEL_TASK_WATCH,
    MODEL_TIME,
    MODEL_TOOL_LIST,
    MODEL_WEATHER,
    MODEL_WEB_FETCH,
    MODEL_WEB_SEARCH,
)
from openminion.modules.tool.contracts.provider_types import ProviderToolSpec

READ_ONLY_BLOCKED_CATEGORIES: set[str] = {
    MODEL_FILE_WRITE,
    MODEL_EXEC_RUN,
    MODEL_GITHUB_COMMIT_FILES,
    MODEL_GITHUB_OPEN_PR,
    MODEL_GITHUB_POST_PR_REVIEW,
    MODEL_GITHUB_POST_PR_COMMENT,
}

CANONICAL_CATEGORY_COMPAT_IDS: frozenset[str] = frozenset({MODEL_WEB_SEARCH})

PREFERRED_MODEL_TOOLS_BY_CATEGORY: dict[str, list[str]] = {
    "browser": [
        MODEL_BROWSER,
    ],
    "web.search": [
        MODEL_WEB_SEARCH,
    ],
    "weather": [
        MODEL_WEATHER,
    ],
    "file.list_dir": [
        MODEL_FILE_LIST_DIR,
    ],
    "file.read": [
        MODEL_FILE_READ,
    ],
    "file.write": [
        MODEL_FILE_WRITE,
    ],
    "file.find": [
        MODEL_FILE_FIND,
    ],
    "file.search": [
        MODEL_FILE_SEARCH,
    ],
    "file.edit": [
        MODEL_FILE_EDIT,
    ],
    "tool.list": [
        MODEL_TOOL_LIST,
    ],
    "tool.search": [
        MODEL_TOOL_LIST,
    ],
    "exec.run": [
        MODEL_EXEC_RUN,
    ],
    "process_control": [
        MODEL_EXEC_LIST,
        MODEL_EXEC_POLL,
    ],
    "web.fetch": [
        MODEL_WEB_FETCH,
    ],
    "time": [
        MODEL_TIME,
    ],
    "location": [
        MODEL_LOCATION,
    ],
    "host.metrics": [
        MODEL_HOST_METRICS,
    ],
    "resources": [
        MODEL_HOST_METRICS,
    ],
    "system": [
        MODEL_HOST_METRICS,
    ],
    "task.schedule": [
        MODEL_TASK_SCHEDULE,
    ],
    "task.list": [
        MODEL_TASK_LIST,
    ],
    "task.cancel": [
        MODEL_TASK_CANCEL,
    ],
    "task.pause": [
        MODEL_TASK_PAUSE,
    ],
    "task.resume": [
        MODEL_TASK_RESUME,
    ],
    "task.show": [
        MODEL_TASK_SHOW,
    ],
    "task.watch": [
        MODEL_TASK_WATCH,
    ],
    "task.consolidate_memory": [
        MODEL_TASK_CONSOLIDATE_MEMORY,
    ],
    "github.list_prs": [
        MODEL_GITHUB_LIST_PRS,
    ],
    "github.fetch_pr": [
        MODEL_GITHUB_FETCH_PR,
    ],
    "github.fetch_diff": [
        MODEL_GITHUB_FETCH_DIFF,
    ],
    "github.fetch_comments": [
        MODEL_GITHUB_FETCH_COMMENTS,
    ],
    "github.fetch_checks": [
        MODEL_GITHUB_FETCH_CHECKS,
    ],
    "github.commit_files": [
        MODEL_GITHUB_COMMIT_FILES,
    ],
    "github.open_pr": [
        MODEL_GITHUB_OPEN_PR,
    ],
    "github.post_pr_review": [
        MODEL_GITHUB_POST_PR_REVIEW,
    ],
    "github.post_pr_comment": [
        MODEL_GITHUB_POST_PR_COMMENT,
    ],
}


class SelectionMode(str, Enum):
    DETERMINISTIC = "deterministic"
    TYPED = "typed"


class SchemaExposure(str, Enum):
    STUB_FIRST = "stub_first"
    FULL = "full"


@dataclass
class ToolStub:
    name: str
    description_short: str
    required_args: list[str]
    example_minimal: dict[str, Any]


@dataclass
class ShortlistPlan:
    query: str
    mode: str
    selected_categories: list[str]
    selected_tools: list[str]
    token_budget: int
    estimated_tokens: int
    reason_codes: list[str]
    fallback_chain: list[str] = field(default_factory=list)


@dataclass
class ValidationError:
    code: str
    tool_name: str
    missing_required: list[str]
    wrong_type: list[str]
    retry_mode: str


@dataclass
class SelectionResult:
    mode: str
    shortlist: list[str]
    stubs: list[ToolStub]
    full_schema_tools: list[str]
    category: str | None
    binding_source: str | None
    fallback_used: bool
    token_estimate: int
    reason_codes: list[str]


@dataclass
class FilterOutcome:
    specs: list[ProviderToolSpec]
    unresolved_category_count: int = 0


def first_available_tool(
    *,
    fallback_chain: list[str],
    available_model_tools: set[str],
) -> str:
    if not fallback_chain:
        return ""
    if not available_model_tools:
        return fallback_chain[0]
    return next((tool for tool in fallback_chain if tool in available_model_tools), "")


def create_validation_error(
    tool_name: str,
    missing_required: list[str],
    wrong_type: list[str],
) -> ValidationError:
    return ValidationError(
        code="TOOL_ARG_VALIDATION_FAILED",
        tool_name=tool_name,
        missing_required=missing_required,
        wrong_type=wrong_type,
        retry_mode="full_schema_once",
    )


def stub_to_provider_spec(stub: ToolStub) -> ProviderToolSpec:
    return ProviderToolSpec(
        name=stub.name,
        description=stub.description_short,
        parameters={
            "type": "object",
            "properties": {
                arg: {"type": "string", "description": f"{arg} parameter"}
                for arg in stub.required_args
            },
            "required": stub.required_args,
        },
    )


def selection_result_to_provider_specs(
    result: SelectionResult,
    service: Any,
) -> list[ProviderToolSpec]:
    if result.stubs:
        return [stub_to_provider_spec(stub) for stub in result.stubs]

    specs: list[ProviderToolSpec] = []
    for tool_name in result.shortlist:
        spec = service.get_full_schema(tool_name)
        if spec:
            specs.append(spec)
    return specs
