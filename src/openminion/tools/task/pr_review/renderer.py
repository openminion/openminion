from ..constants import PR_REVIEW_ANNOUNCE_MAX_CHARS
from .schemas import ReviewOutcomePayloadV1, ReviewedPrV1

_REVIEW_STATE_GLYPH = {
    "needs_human_review": "[review]",
    "approved_lgtm": "[lgtm]",
    "needs_changes": "[changes]",
}

_SEVERITY_GLYPH = {
    "info": "info",
    "warn": "warn",
    "error": "error",
}


def render_artifact_markdown(
    *,
    routine_id: str,
    repo: str,
    checked_at: str,
    outcome: ReviewOutcomePayloadV1,
) -> str:
    lines: list[str] = []
    lines.append(f"# GitHub PR Review — {repo}")
    lines.append("")
    lines.append(f"Routine: {routine_id}")
    lines.append(f"Checked at: {checked_at}")
    lines.append(f"Reviewed: {len(outcome.reviewed_prs)}")
    lines.append(f"Skipped: {len(outcome.skipped_prs)}")
    lines.append("")

    for entry in outcome.reviewed_prs:
        lines.extend(_render_pr_section(entry))
        lines.append("")

    if outcome.skipped_prs:
        lines.append("## Skipped")
        for skipped in outcome.skipped_prs:
            reason = skipped.reason or "(no reason)"
            lines.append(f"- #{skipped.number}: {reason}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _render_pr_section(entry: ReviewedPrV1) -> list[str]:
    state = _REVIEW_STATE_GLYPH.get(entry.review_state, entry.review_state)
    out: list[str] = [
        f"## #{entry.number} {state}",
        f"Head SHA: {entry.head_sha_reviewed}",
        f"Summary: {entry.summary}".rstrip(),
    ]
    if entry.findings:
        out.append("")
        out.append("### Findings")
        for finding in entry.findings:
            sev = _SEVERITY_GLYPH.get(finding.severity, finding.severity)
            location = f"{finding.file}:{finding.line}" if finding.file else ""
            prefix = f"[{sev}]"
            if location:
                out.append(f"- {prefix} {location} — {finding.message}")
            else:
                out.append(f"- {prefix} {finding.message}")
    return out


def render_announce_summary(
    *,
    repo: str,
    outcome: ReviewOutcomePayloadV1,
) -> str:
    reviewed = len(outcome.reviewed_prs)
    findings_total = sum(len(pr.findings) for pr in outcome.reviewed_prs)
    summary = (
        f"PR review run for {repo}: reviewed {reviewed} PR(s), "
        f"{findings_total} finding(s)."
    )
    if len(summary) > PR_REVIEW_ANNOUNCE_MAX_CHARS:
        summary = summary[: PR_REVIEW_ANNOUNCE_MAX_CHARS - 1] + "…"
    return summary


__all__ = [
    "render_artifact_markdown",
    "render_announce_summary",
]
