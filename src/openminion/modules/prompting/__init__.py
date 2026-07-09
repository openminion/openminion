"""Shared prompt fragments and render helpers for OpenMinion runtime surfaces."""

from .context_blocks import (
    CURRENT_SESSION_SUMMARY_HEADER,
    GROUNDING_BLOCK_HEADER,
    PENDING_TURN_BLOCK_HEADER,
    PRIOR_SESSION_SUMMARY_HEADER,
    PRIOR_TURN_BLOCK_HEADER,
    PROJECT_CONTEXT_FILE_HEADER,
    THIRD_BRAIN_GRAPH_CONTEXT_HEADER,
    build_project_context_block,
)
from .continuation import (
    ACTIVE_TASK_CONTINUATION_PROMPT,
    PARTIAL_SUCCESS_CONTINUATION_PROMPT,
    TOOL_LOOP_CONTINUE_PROMPT,
    build_active_task_continuation_prompt,
    build_continuation_choice_message,
    build_feasibility_choice_prompt,
    build_goal_run_continuation_prompt,
    build_plan_checkpoint_continuation_message,
    build_successful_tool_continuation_prompt,
)
from .finalization import (
    FINALIZATION_STATUS_FOLLOW_UP_GUIDANCE,
    FINALIZATION_STATUS_RETRY_GUIDANCE,
)
from .decision import (
    BRAIN_FRESHNESS_POLICY_CONSTRAINT,
    DECIDE_STYLE_OVERRIDES,
    fixed_profile_rewrites,
)
from .identity import (
    AGENT_IDENTITY_FRAME,
    DEFAULT_SAFETY_TEXT,
    IDENTITY_DIRECTIVE,
    TOOL_RESULT_FORMAT_TEXT,
)

__all__ = [
    "ACTIVE_TASK_CONTINUATION_PROMPT",
    "AGENT_IDENTITY_FRAME",
    "BRAIN_FRESHNESS_POLICY_CONSTRAINT",
    "CURRENT_SESSION_SUMMARY_HEADER",
    "DECIDE_STYLE_OVERRIDES",
    "DEFAULT_SAFETY_TEXT",
    "FINALIZATION_STATUS_FOLLOW_UP_GUIDANCE",
    "FINALIZATION_STATUS_RETRY_GUIDANCE",
    "GROUNDING_BLOCK_HEADER",
    "IDENTITY_DIRECTIVE",
    "PARTIAL_SUCCESS_CONTINUATION_PROMPT",
    "PENDING_TURN_BLOCK_HEADER",
    "PRIOR_SESSION_SUMMARY_HEADER",
    "PRIOR_TURN_BLOCK_HEADER",
    "PROJECT_CONTEXT_FILE_HEADER",
    "THIRD_BRAIN_GRAPH_CONTEXT_HEADER",
    "TOOL_LOOP_CONTINUE_PROMPT",
    "TOOL_RESULT_FORMAT_TEXT",
    "build_active_task_continuation_prompt",
    "build_continuation_choice_message",
    "build_feasibility_choice_prompt",
    "build_goal_run_continuation_prompt",
    "build_plan_checkpoint_continuation_message",
    "build_successful_tool_continuation_prompt",
    "build_project_context_block",
    "fixed_profile_rewrites",
]
