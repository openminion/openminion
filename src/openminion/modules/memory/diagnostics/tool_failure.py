"""Diagnostics for tool-failure fact poisoning."""

from dataclasses import asdict, dataclass
from typing import Any

from openminion.base.constants import STATE_KEY_SOURCE_OUTCOME
from openminion.modules.memory.storage.base import ListQueryOptions


_AMBIGUOUS_TOOL_FAILURE_TEXT_MARKERS = (
    "unknown tool:",
    "tool execution failed",
    "tool runtime unavailable",
)


@dataclass(frozen=True)
class ToolFailureFactDiagnosticRecord:
    record_id: str
    scope: str
    record_type: str
    title: str
    text_preview: str
    tags: list[str]
    meta: dict[str, Any]
    reason: str
    tombstoned: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ToolFailureFactDiagnosticReport:
    scanned_count: int
    structured_count: int
    ambiguous_text_count: int
    tombstoned_count: int
    structured: list[ToolFailureFactDiagnosticRecord]
    ambiguous_text: list[ToolFailureFactDiagnosticRecord]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanned_count": self.scanned_count,
            "structured_count": self.structured_count,
            "ambiguous_text_count": self.ambiguous_text_count,
            "tombstoned_count": self.tombstoned_count,
            "structured": [item.to_dict() for item in self.structured],
            "ambiguous_text": [item.to_dict() for item in self.ambiguous_text],
        }


def _record_text(record: Any) -> str:
    content = getattr(record, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        for key in ("text", "summary", "value", "note", "content"):
            if value := content.get(key):
                return str(value)
    return str(getattr(record, "title", "") or "")


def _record_tags(record: Any) -> set[str]:
    return {
        str(item or "").strip().lower()
        for item in (getattr(record, "tags", []) or [])
        if str(item or "").strip()
    }


def _diagnostic_record_meta(record: Any) -> dict[str, Any]:
    raw = getattr(record, "meta", {}) or {}
    return dict(raw) if isinstance(raw, dict) else {}


def is_structured_tool_failure_fact(record: Any) -> tuple[bool, str]:
    """Return whether `record` is explicitly marked as a tool-failure fact."""

    if str(getattr(record, "type", "") or "") != "fact":
        return False, ""
    tags = _record_tags(record)
    meta = _diagnostic_record_meta(record)
    if "tool_failure" in tags:
        return True, "tag:tool_failure"
    if bool(meta.get("source_negative_outcome")):
        return True, "meta:source_negative_outcome"
    source_kind = str(meta.get("source_kind") or "").strip().lower()
    outcome_status = str(meta.get(STATE_KEY_SOURCE_OUTCOME) or "").strip().lower()
    if source_kind == "tool_outcome" and outcome_status not in {
        "",
        "ok",
        "success",
        "succeeded",
    }:
        return True, "meta:tool_outcome_negative_status"
    if "tool_outcome" in tags and any(
        tag in tags
        for tag in {
            "outcome:error",
            "outcome:failed",
            "outcome:failure",
        }
    ):
        return True, "tags:tool_outcome_negative_status"
    return False, ""


def is_ambiguous_tool_failure_text_fact(record: Any) -> tuple[bool, str]:
    """Return whether text resembles tool failure without structural provenance.

    This is an operator diagnostic only. Runtime prompt assembly must not use
    this text check to suppress or reinterpret semantic facts.
    """

    if str(getattr(record, "type", "") or "") != "fact":
        return False, ""
    text = " ".join(_record_text(record).lower().split())
    if not text:
        return False, ""
    for marker in _AMBIGUOUS_TOOL_FAILURE_TEXT_MARKERS:
        if marker in text:
            return True, f"text:{marker}"
    return False, ""


def _diagnostic_record(
    record: Any,
    *,
    reason: str,
    tombstoned: bool = False,
) -> ToolFailureFactDiagnosticRecord:
    text = " ".join(_record_text(record).split()).strip()
    if len(text) > 200:
        text = text[:197].rstrip() + "..."
    return ToolFailureFactDiagnosticRecord(
        record_id=str(getattr(record, "id", "") or ""),
        scope=str(getattr(record, "scope", "") or ""),
        record_type=str(getattr(record, "type", "") or ""),
        title=str(getattr(record, "title", "") or ""),
        text_preview=text,
        tags=list(getattr(record, "tags", []) or []),
        meta=_diagnostic_record_meta(record),
        reason=reason,
        tombstoned=bool(tombstoned),
    )


def diagnose_tool_failure_fact_poisoning(
    store: Any,
    *,
    scopes: list[str] | None = None,
    tombstone_structured: bool = False,
    limit: int | None = None,
) -> ToolFailureFactDiagnosticReport:
    records = list(
        store.list(
            ListQueryOptions(
                scopes=list(scopes or []),
                types=["fact"],
                limit=limit,
            )
        )
    )
    structured: list[ToolFailureFactDiagnosticRecord] = []
    ambiguous: list[ToolFailureFactDiagnosticRecord] = []
    tombstoned_count = 0

    for record in records:
        is_structured, structured_reason = is_structured_tool_failure_fact(record)
        if is_structured:
            tombstoned = False
            if tombstone_structured:
                record_id = str(getattr(record, "id", "") or "").strip()
                if record_id:
                    store.delete(record_id)
                    tombstoned = True
                    tombstoned_count += 1
            structured.append(
                _diagnostic_record(
                    record,
                    reason=structured_reason,
                    tombstoned=tombstoned,
                )
            )
            continue
        is_ambiguous, ambiguous_reason = is_ambiguous_tool_failure_text_fact(record)
        if is_ambiguous:
            ambiguous.append(_diagnostic_record(record, reason=ambiguous_reason))

    return ToolFailureFactDiagnosticReport(
        scanned_count=len(records),
        structured_count=len(structured),
        ambiguous_text_count=len(ambiguous),
        tombstoned_count=tombstoned_count,
        structured=structured,
        ambiguous_text=ambiguous,
    )


def render_tool_failure_fact_diagnostic_report(
    report: ToolFailureFactDiagnosticReport,
) -> str:
    lines = [
        "Tool-failure fact poisoning diagnostic",
        f"  scanned facts: {report.scanned_count}",
        f"  structured operational failures: {report.structured_count}",
        f"  ambiguous text-only facts: {report.ambiguous_text_count}",
        f"  tombstoned structured failures: {report.tombstoned_count}",
    ]
    if report.structured:
        lines.append("")
        lines.append("Structured operational failure facts:")
        for item in report.structured:
            suffix = " tombstoned" if item.tombstoned else ""
            lines.append(
                f"  - {item.record_id} {item.scope} reason={item.reason}{suffix}"
            )
            if item.text_preview:
                lines.append(f"    {item.text_preview}")
    if report.ambiguous_text:
        lines.append("")
        lines.append("Ambiguous text-only facts (manual review):")
        for item in report.ambiguous_text:
            lines.append(f"  - {item.record_id} {item.scope} reason={item.reason}")
            if item.text_preview:
                lines.append(f"    {item.text_preview}")
    return "\n".join(lines)


__all__ = [
    "ToolFailureFactDiagnosticRecord",
    "ToolFailureFactDiagnosticReport",
    "diagnose_tool_failure_fact_poisoning",
    "is_ambiguous_tool_failure_text_fact",
    "is_structured_tool_failure_fact",
    "render_tool_failure_fact_diagnostic_report",
]
