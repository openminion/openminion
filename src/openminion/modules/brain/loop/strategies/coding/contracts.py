from typing import Any, Protocol, runtime_checkable

from openminion.modules.brain.loop.tools import (
    ADAPTIVE_TERM_BUDGET_EXHAUSTED,
    ADAPTIVE_TERM_CONFIDENT_COMPLETE,
    ADAPTIVE_TERM_DISALLOWED_TOOL,
    ADAPTIVE_TERM_FINAL_TEXT,
    ADAPTIVE_TERM_ITERATION_CAP,
    ADAPTIVE_TERM_JOB_PENDING,
    ADAPTIVE_TERM_LLM_ERROR,
    ADAPTIVE_TERM_NEEDS_USER,
    AdaptiveToolLoopLLMRuntime,
)
from openminion.modules.brain.loop.strategies.coding.constants import (
    CODING_TERM_VERIFY_CAP_EXCEEDED as CODING_TERM_VERIFY_CAP_EXCEEDED,
)
from openminion.modules.llm.schemas import LLMResponse, Message, ToolSpec
from openminion.modules.tool.contracts.model_ids import (
    MODEL_CODE_GREP,
    MODEL_CODE_PATCH,
    MODEL_CODE_REPO_INDEX,
    MODEL_CODE_REPO_MAP,
    MODEL_CODE_SYMBOL_FIND,
    MODEL_EXEC_KILL,
    MODEL_EXEC_LIST,
    MODEL_EXEC_POLL,
    MODEL_EXEC_RUN,
    MODEL_FILE_FIND,
    MODEL_FILE_LIST_DIR,
    MODEL_FILE_READ,
    MODEL_FILE_READ_RANGE,
    MODEL_FILE_WRITE,
    MODEL_WEB_FETCH,
)

CODING_ALLOWED_TOOLS: frozenset[str] = frozenset(
    (
        MODEL_FILE_LIST_DIR,
        MODEL_FILE_READ,
        MODEL_FILE_READ_RANGE,
        MODEL_FILE_FIND,
        MODEL_FILE_WRITE,
        MODEL_CODE_PATCH,
        MODEL_CODE_GREP,
        MODEL_CODE_REPO_MAP,
        MODEL_CODE_REPO_INDEX,
        MODEL_CODE_SYMBOL_FIND,
        MODEL_WEB_FETCH,
        MODEL_EXEC_RUN,
        MODEL_EXEC_POLL,
        MODEL_EXEC_LIST,
        MODEL_EXEC_KILL,
    )
)

CODING_V1_ALLOWED_TOOLS = CODING_ALLOWED_TOOLS

CODING_TERM_FINAL_TEXT = ADAPTIVE_TERM_FINAL_TEXT
CODING_TERM_CONFIDENT_COMPLETE = ADAPTIVE_TERM_CONFIDENT_COMPLETE
CODING_TERM_APPROVAL_NEEDED = "approval_needed"
CODING_TERM_NEEDS_USER = ADAPTIVE_TERM_NEEDS_USER
CODING_TERM_JOB_PENDING = ADAPTIVE_TERM_JOB_PENDING
CODING_TERM_BUDGET_EXHAUSTED = ADAPTIVE_TERM_BUDGET_EXHAUSTED
CODING_TERM_DISALLOWED_TOOL = ADAPTIVE_TERM_DISALLOWED_TOOL
CODING_TERM_LLM_ERROR = ADAPTIVE_TERM_LLM_ERROR
CODING_TERM_TOOL_FAILURE = "tool_failure"
CODING_TERM_ITERATION_CAP = ADAPTIVE_TERM_ITERATION_CAP


class CodingModeError(Exception):
    """Base error for coding-mode failures."""


class CodingRuntimeUnavailableError(CodingModeError):
    """Raised when the raw LLM runtime cannot be obtained."""


class CodingDisallowedToolError(CodingModeError):
    """Raised when the model requests a tool outside the coding allowlist."""

    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        super().__init__(f"Disallowed coding tool: {tool_name!r}")


@runtime_checkable
class CodingLLMRuntime(AdaptiveToolLoopLLMRuntime, Protocol):
    """Mode-local raw LLM protocol."""

    def complete(
        self,
        *,
        messages: list[Message],
        tools: list[ToolSpec],
        model: str,
        tool_choice: str | dict[str, Any] = "auto",
        max_output_tokens: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LLMResponse: ...
