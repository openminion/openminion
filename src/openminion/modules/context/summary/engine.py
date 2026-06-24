from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class SummaryTurn:
    role: str
    text: str


@dataclass(frozen=True)
class SummaryChunkResult:
    summary_text: str
    source_turn_count: int
    output_line_count: int


class SessionSummaryEngine:
    """Deterministic summary policy owned by the context module."""

    def summarize_compaction_chunk(
        self, turns: Sequence[SummaryTurn]
    ) -> SummaryChunkResult:
        lines: list[str] = []
        for turn in turns:
            role = _normalize_role(turn.role)
            content = _truncate(turn.text, max_chars=180)
            if not content:
                continue
            lines.append(f"- {role}: {content}")
        summary_text = "\n".join(lines).strip()
        return SummaryChunkResult(
            summary_text=summary_text,
            source_turn_count=len(turns),
            output_line_count=len(lines),
        )

    def merge_summary(self, *, current: str, delta: str, max_chars: int) -> str:
        current_trimmed = str(current or "").strip()
        delta_trimmed = str(delta or "").strip()
        if not delta_trimmed:
            return _dedupe_summary_lines(current_trimmed)
        if not current_trimmed:
            merged = delta_trimmed
        else:
            merged = current_trimmed + "\n" + delta_trimmed
        merged = _dedupe_summary_lines(merged)
        if len(merged) <= max_chars:
            return merged

        trimmed = merged[-max_chars:]
        newline_index = trimmed.find("\n")
        if newline_index > 0:
            trimmed = trimmed[newline_index + 1 :]
        return _dedupe_summary_lines(trimmed)

    def render_summary_short(
        self,
        turns: Sequence[SummaryTurn],
        *,
        recent_limit: int = 3,
        max_chars_per_turn: int = 50,
    ) -> str:
        normalized = _normalize_turns(turns)
        if not normalized:
            return ""
        selected = (
            normalized[-recent_limit:]
            if int(recent_limit) > 0 and len(normalized) > int(recent_limit)
            else normalized
        )
        return " / ".join(
            f"{turn.role}: {_truncate(turn.text, max_chars=max_chars_per_turn)}"
            for turn in selected
        ).strip()

    def render_summary_long(
        self, turns: Sequence[SummaryTurn], *, recent_limit: int = 3
    ) -> str:
        normalized = _normalize_turns(turns)
        if not normalized:
            return ""
        selected = (
            normalized[-recent_limit:]
            if int(recent_limit) > 0 and len(normalized) > int(recent_limit)
            else normalized
        )
        return "\n".join(f"{turn.role}: {turn.text}" for turn in selected).strip()


def _normalize_turns(turns: Sequence[SummaryTurn]) -> list[SummaryTurn]:
    normalized: list[SummaryTurn] = []
    for turn in turns:
        role = _normalize_role(turn.role)
        text = " ".join(str(turn.text or "").strip().split())
        if not text:
            continue
        normalized.append(SummaryTurn(role=role, text=text))
    return normalized


def _normalize_role(raw_role: str) -> str:
    role = str(raw_role or "").strip().lower()
    if role in {"inbound", "user"}:
        return "user"
    if role in {"outbound", "assistant"}:
        return "assistant"
    if role == "system":
        return "system"
    return role or "user"


def _truncate(value: str, *, max_chars: int) -> str:
    compact = " ".join(str(value or "").strip().split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def _dedupe_summary_lines(value: str) -> str:
    lines = [line.rstrip() for line in str(value or "").splitlines() if line.strip()]
    if not lines:
        return ""
    deduped_reversed: list[str] = []
    seen: set[str] = set()
    for line in reversed(lines):
        key = line.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        deduped_reversed.append(line.strip())
    return "\n".join(reversed(deduped_reversed)).strip()


DEFAULT_SESSION_SUMMARY_ENGINE = SessionSummaryEngine()


__all__ = [
    "DEFAULT_SESSION_SUMMARY_ENGINE",
    "SessionSummaryEngine",
    "SummaryChunkResult",
    "SummaryTurn",
]
