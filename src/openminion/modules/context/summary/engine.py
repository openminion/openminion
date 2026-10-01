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
            content = _edge_excerpt(turn.text, max_chars=180)
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
        return self.fit_summary_edges(merged, max_chars=max_chars)

    def fit_summary_edges(self, value: str, *, max_chars: int) -> str:
        summary = _dedupe_summary_lines(value)
        if len(summary) <= max_chars:
            return summary
        return _bounded_edges(summary, max_chars=max_chars)

    def merge_enrichment(
        self,
        *,
        deterministic_summary: str,
        enriched_summary: str,
        max_chars: int,
    ) -> str:
        base = _dedupe_summary_lines(deterministic_summary)
        enriched = _dedupe_summary_lines(enriched_summary)
        if not enriched or enriched == base:
            return self.fit_summary_edges(base, max_chars=max_chars)

        separator = "\n...\n"
        available = max_chars - (2 * len(separator))
        if available <= 0:
            return self.fit_summary_edges(base, max_chars=max_chars)

        enrichment_budget = min(len(enriched), max(1, available // 3))
        base_budget = available - enrichment_budget
        if len(base) < base_budget:
            enrichment_budget = min(
                len(enriched), enrichment_budget + base_budget - len(base)
            )
            base_budget = available - enrichment_budget

        head, tail = _split_edges(base, max_chars=base_budget)
        middle = _edge_excerpt(enriched, max_chars=enrichment_budget)
        return f"{head}{separator}{middle}{separator}{tail}"[:max_chars]

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
            f"{turn.role}: {_edge_excerpt(turn.text, max_chars=max_chars_per_turn)}"
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


def _edge_excerpt(value: str, *, max_chars: int) -> str:
    compact = " ".join(str(value or "").strip().split())
    if len(compact) <= max_chars:
        return compact
    if max_chars <= 3:
        return compact[:max_chars]
    separator = "..."
    available = max_chars - len(separator)
    head_chars = available // 2
    tail_chars = available - head_chars
    return f"{compact[:head_chars].rstrip()}{separator}{compact[-tail_chars:].lstrip()}"


def _bounded_edges(value: str, *, max_chars: int) -> str:
    separator = "\n...\n"
    available = max_chars - len(separator)
    if available <= 0:
        return value[:max_chars]
    head, tail = _split_edges(value, max_chars=available)
    return f"{head}{separator}{tail}"


def _split_edges(value: str, *, max_chars: int) -> tuple[str, str]:
    if len(value) <= max_chars:
        split_at = (len(value) + 1) // 2
        return value[:split_at].rstrip(), value[split_at:].lstrip()
    head_chars = (max_chars + 1) // 2
    tail_chars = max_chars - head_chars
    return (
        _leading_summary_edge(value, max_chars=head_chars),
        _trailing_summary_edge(value, max_chars=tail_chars),
    )


def _leading_summary_edge(value: str, *, max_chars: int) -> str:
    first_line = value.splitlines()[0]
    if len(first_line) >= max_chars:
        return _edge_excerpt(first_line, max_chars=max_chars)
    return value[:max_chars].rstrip()


def _trailing_summary_edge(value: str, *, max_chars: int) -> str:
    last_line = value.splitlines()[-1]
    if len(last_line) >= max_chars:
        return _edge_excerpt(last_line, max_chars=max_chars)
    prefix = value[: -len(last_line)].rstrip()
    prefix_chars = max_chars - len(last_line) - 1
    if prefix_chars <= 0 or not prefix:
        return last_line
    return f"{prefix[-prefix_chars:].lstrip()}\n{last_line}"


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
