from typing import Any

from openminion.modules.brain.loop.tools.postprocess.evidence_closeout import (
    mutating_file_evidence_can_closeout,
)


def salvage_final_answer_after_disallowed_writer(
    runner: Any,
    *,
    outcome: Any,
) -> str | None:
    loop = runner._loop_state
    if not bool(loop.scratchpad.get("coding.final_answer_reserve_used")):
        return None
    tool_name = str(getattr(outcome, "tool_name", "") or "").strip()
    if tool_name not in {"file.write", "code.patch"}:
        return None
    return salvage_reserved_closeout_from_existing_evidence(
        runner,
        tool_results=_successful_tool_results(loop),
        interruption_detail=(
            "The model kept asking for extra write calls during the reserved "
            "answer-only closeout, so this summary is derived from the existing "
            "coding evidence."
        ),
    )


def salvage_reserved_closeout_from_existing_evidence(
    runner: Any,
    *,
    tool_results: list[dict[str, Any]] | None = None,
    interruption_detail: str,
) -> str | None:
    loop = runner._loop_state
    if not bool(loop.scratchpad.get("coding.final_answer_reserve_used")):
        return None
    tool_results = (
        tool_results if tool_results is not None else _successful_tool_results(loop)
    )
    if not tool_results:
        return None
    if not mutating_file_evidence_can_closeout(loop):
        return None

    changed_paths: list[str] = []
    for item in tool_results:
        data = item.get("data")
        if not isinstance(data, dict):
            continue
        path = str(data.get("path", "") or "").strip()
        if path and path not in changed_paths:
            changed_paths.append(path)

    verifier_status = (
        "preserved from an earlier read-only verification step"
        if runner._has_verifier_candidate()
        else "not recorded after the final successful write"
    )
    requested_markers = runner._requested_final_markers()
    marker_lines: list[str] = []
    for marker in requested_markers:
        normalized = str(marker or "").strip().lower().rstrip(":")
        if not normalized:
            continue
        if normalized == "result":
            marker_lines.append(
                "result: reserved final closeout was interrupted after successful "
                f"tool writes; returning deterministic run evidence instead. "
                f"{interruption_detail}"
            )
            continue
        if normalized in {"files", "files changed"}:
            marker_lines.append(f"{normalized}: {_render_paths(changed_paths)}")
            continue
        if normalized in {"validation", "validation result"}:
            marker_lines.append(f"{normalized}: {verifier_status}")
            continue
        if normalized in {"follow-ups", "remaining follow-ups"}:
            marker_lines.append(
                f"{normalized}: no deterministic follow-up list was captured before "
                "the reserved closeout was interrupted."
            )
            continue
        marker_lines.append(
            f"{normalized}: not captured before closeout interruption; preserved "
            "written-file evidence is reported instead."
        )

    if not marker_lines:
        marker_lines = [
            f"files changed: {_render_paths(changed_paths)}",
            (
                "result: reserved final closeout was interrupted after successful "
                f"tool writes; returning deterministic run evidence instead. "
                f"{interruption_detail}"
            ),
            f"validation: {verifier_status}",
        ]
    return "\n".join(marker_lines)


def _successful_tool_results(loop: Any) -> list[dict[str, Any]]:
    return [
        item
        for item in list(loop.scratchpad.get("adaptive.tool_results", []) or [])
        if isinstance(item, dict) and bool(item.get("ok"))
    ]


def _render_paths(paths: list[str]) -> str:
    return ", ".join(paths[:8]) if paths else "none recorded"
