from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class LoopTemplate:
    match_tags: tuple[str, ...]
    tool_sequence: tuple[str, ...]
    avg_iterations: float
    success: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "match_tags": list(self.match_tags),
            "tool_sequence": list(self.tool_sequence),
            "avg_iterations": self.avg_iterations,
            "success": self.success,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LoopTemplate:
        return cls(
            match_tags=_normalize_template_tags(data.get("match_tags", [])),
            tool_sequence=tuple(data.get("tool_sequence", [])),
            avg_iterations=data.get("avg_iterations", 0.0),
            success=data.get("success", False),
        )


def _normalize_template_tags(values: Any) -> tuple[str, ...]:
    if isinstance(values, str):
        raw_values = [values]
    elif isinstance(values, tuple | list | set | frozenset):
        raw_values = list(values)
    else:
        raw_values = []
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        tag = str(raw or "").strip()
        if not tag:
            continue
        lowered = tag.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(tag)
    return tuple(normalized)


def match_templates(
    templates: list[LoopTemplate],
    match_tags: tuple[str, ...],
    top_n: int = 3,
) -> list[LoopTemplate]:
    """Find top-N matching templates by exact overlap on typed match tags."""
    normalized_tags = _normalize_template_tags(match_tags)
    if not templates or not normalized_tags:
        return []
    tag_set = {tag.lower() for tag in normalized_tags}
    scored: list[tuple[int, LoopTemplate]] = []
    for template in templates:
        overlap = len(tag_set & {tag.lower() for tag in template.match_tags})
        if overlap > 0:
            scored.append((overlap, template))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [template for _, template in scored[:top_n]]


def build_template_hint(templates: list[LoopTemplate]) -> str:
    """Build a system message hint from matching templates."""
    if not templates:
        return ""
    lines = ["For similar tasks, successful approaches used:"]
    for t in templates:
        lines.append(
            f"  - Tools: {' → '.join(t.tool_sequence)}"
            f" ({t.avg_iterations:.1f} iterations avg)"
        )
    return "\n".join(lines)
