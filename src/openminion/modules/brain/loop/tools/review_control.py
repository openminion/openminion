"""Review-diff loop-control tool spec and handler."""

from __future__ import annotations

from typing import Any

from openminion.modules.brain.constants import BRAIN_ACTION_STATUS_FAILED
from openminion.modules.brain.runtime.review.diff import (
    ReviewResult,
    analyze_diff,
)
from openminion.modules.brain.runtime.review.observation import REVIEW_TOOL_NAME
from openminion.modules.brain.schemas import ActionError, ActionResult, new_uuid
from openminion.modules.llm.schemas import ToolSpec


__REVIEW_TOOL_NAME = REVIEW_TOOL_NAME  # noqa: F401

REVIEW_TOOL_ATTEMPTED_SCRATCHPAD_KEY = "review_tool.attempted"
REVIEW_TOOL_USED_SCRATCHPAD_KEY = "review_tool.used"


def build_review_tool_spec() -> ToolSpec:
    """Loop-control review tool spec.

    The model calls this tool with a unified-diff payload (typically
    obtained from ``git.diff`` or ``exec.run`` of ``git diff``) on
    multi-file edit turns to get a structural second-opinion review.
    """
    return ToolSpec(
        name=REVIEW_TOOL_NAME,
        description=(
            "Run a structural review pass on a unified diff. Use this "
            "tool on multi-file edit turns to surface findings like "
            "missing test coverage, large deletions, and TODO/FIXME "
            "introductions before declaring the turn done. The tool "
            "returns a structured findings count and severity; the "
            "model should address block-severity findings before "
            "responding."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "diff": {
                    "type": "string",
                    "description": (
                        "Unified-diff text to review. Typically the "
                        "output of `git diff` or `git diff <ref>..HEAD` "
                        "for the work the model just produced."
                    ),
                },
            },
            "required": ["diff"],
            "additionalProperties": False,
        },
    )


def handle_review_tool_call(
    *,
    loop_ctx: Any,
    arguments: dict[str, Any],
) -> ActionResult:
    """Run the v1 structural diff analyzer and emit a typed ActionResult.

    Default-safe: empty/missing ``diff`` argument returns a failure
    envelope with reason ``REVIEW_MISSING_DIFF``. The model can then
    call the tool again with a real diff.
    """
    del loop_ctx  # v1 has no loop-state interaction; reserved for v2.
    diff_text = str((arguments or {}).get("diff", "") or "")
    if not diff_text.strip():
        return ActionResult(
            command_id=new_uuid(),
            status=BRAIN_ACTION_STATUS_FAILED,
            summary="review.diff requires a non-empty diff argument.",
            error=ActionError(
                code="REVIEW_MISSING_DIFF",
                message="review.diff requires a non-empty diff argument.",
                details={"argument": "diff"},
            ),
        )
    result: ReviewResult = analyze_diff(diff_text)
    outputs: dict[str, Any] = {
        "review_result": result.model_dump(mode="json"),
        "findings_count": len(result.findings),
        "severity": result.severity,
        "file_count": result.file_count,
        "lines_added": result.lines_added,
        "lines_removed": result.lines_removed,
    }
    return ActionResult(
        command_id=new_uuid(),
        status="success",
        summary=result.summary or "review.diff returned no findings.",
        outputs=outputs,
    )
