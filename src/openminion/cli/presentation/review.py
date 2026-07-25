from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shlex
from typing import Any

from openminion.cli.presentation.git.diff import render_git_diff
from openminion.modules.brain.loop.tools.review_control import handle_review_tool_call
from openminion.modules.brain.schemas import ActionResult

__all__ = ["ReviewWorkflowResult", "run_review_workflow"]


@dataclass(frozen=True)
class ReviewWorkflowResult:
    action_result: ActionResult | None
    body: str
    diff_source: str


def run_review_workflow(working_dir: str | Path, args: str = "") -> ReviewWorkflowResult:
    diff_text, source, no_target_message = _resolve_review_diff(working_dir, args)
    if no_target_message is not None:
        return ReviewWorkflowResult(
            action_result=None,
            body=f"/review: no review target ({no_target_message})",
            diff_source=source,
        )

    action_result = handle_review_tool_call(loop_ctx=None, arguments={"diff": diff_text})
    return ReviewWorkflowResult(
        action_result=action_result,
        body=_format_review_result(action_result, diff_source=source),
        diff_source=source,
    )


def _resolve_review_diff(
    working_dir: str | Path,
    args: str,
) -> tuple[str, str, str | None]:
    raw = str(args or "").strip()
    if raw.startswith("--diff"):
        payload = raw.removeprefix("--diff").strip()
        return payload, "supplied-diff", None if payload else "empty supplied diff"
    if raw.startswith("diff --git"):
        return raw, "supplied-diff", None
    if raw.startswith("--file"):
        return _read_workspace_diff_file(working_dir, raw)

    try:
        result = render_git_diff(working_dir, raw)
    except ValueError as exc:
        return "", "git-diff", str(exc)
    if result.has_diff:
        return result.output, "git-diff", None
    return "", "git-diff", result.message.strip("()") or "no pending changes detected"


def _read_workspace_diff_file(
    working_dir: str | Path,
    raw: str,
) -> tuple[str, str, str | None]:
    try:
        parts = shlex.split(raw)
    except ValueError as exc:
        return "", "file", f"invalid --file argument: {exc}"
    if len(parts) != 2 or parts[0] != "--file":
        return "", "file", "usage: /review [--file <workspace-diff>]"

    root = Path(str(working_dir or ".")).expanduser().resolve(strict=False)
    candidate = (root / parts[1]).expanduser().resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError:
        return "", "file", "diff file must stay inside the workspace"
    if not candidate.is_file():
        return "", "file", f"diff file not found: {parts[1]}"
    try:
        payload = candidate.read_text(encoding="utf-8")
    except OSError as exc:
        return "", "file", f"diff file unreadable: {exc}"
    return payload, "file", None if payload.strip() else "diff file is empty"


def _format_review_result(action_result: ActionResult, *, diff_source: str) -> str:
    if action_result.status != "success":
        error = action_result.error
        message = error.message if error is not None else action_result.summary
        return f"/review failed ({diff_source}): {message}"

    outputs: dict[str, Any] = dict(action_result.outputs or {})
    review_result = dict(outputs.get("review_result") or {})
    findings = list(review_result.get("findings") or [])
    severity = str(outputs.get("severity") or review_result.get("severity") or "ok")
    lines = [
        f"Review result ({diff_source}): severity={severity}; "
        f"findings={outputs.get('findings_count', len(findings))}; "
        f"files={outputs.get('file_count', 0)}; "
        f"+{outputs.get('lines_added', 0)}/-{outputs.get('lines_removed', 0)}",
        str(action_result.summary or "review.diff returned no findings."),
    ]
    if findings:
        lines.append("Findings:")
        lines.extend(_format_finding(finding) for finding in findings[:8])
    return "\n".join(lines)


def _format_finding(finding: Any) -> str:
    if not isinstance(finding, dict):
        return f"- {finding}"
    severity = str(finding.get("severity") or "warn")
    kind = str(finding.get("kind") or "finding")
    path = str(finding.get("file") or "")
    message = str(finding.get("message") or "")
    location = f" {path}" if path else ""
    return f"- [{severity}] {kind}{location}: {message}".rstrip()
