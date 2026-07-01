import re
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel, Field


class IdentitySubsectionUsage(BaseModel):
    """Identity subsection budget usage and truncation status."""

    cap_tokens: int = Field(ge=0)
    used_tokens: int = Field(ge=0)
    truncated: bool = False
    omitted_reason: str | None = None


DEFAULT_IDENTITY_SECTION_ORDER = [
    "constraints",
    "tool_posture",
    "mission",
    "responsibilities",
    "voice",
    "notes",
]
DEFAULT_IDENTITY_SECTION_CAP_RATIOS: dict[str, float] = {
    "constraints": 0.40,
    "tool_posture": 0.30,
    "mission": 0.40,
    "responsibilities": 0.30,
    "voice": 0.20,
    "notes": 0.10,
}


@dataclass(frozen=True)
class ResolvedIdentityBudgetConfig:
    total_tokens: int
    section_order: list[str]
    section_priority: dict[str, int]
    section_caps: dict[str, int]
    truncate_strategy: str


@dataclass(frozen=True)
class IdentityBudgetResult:
    text: str
    cap_tokens: int
    used_tokens: int
    truncated: bool
    subsections: dict[str, IdentitySubsectionUsage]
    ordering_applied: list[str]
    unknown_sections: list[str]


def normalize_identity_section_name(raw: Any) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(raw or "").strip().lower()).strip("_")


def derive_identity_section_caps(
    total_tokens: int, section_order: list[str]
) -> dict[str, int]:
    return {
        section: max(
            1,
            int(total_tokens * DEFAULT_IDENTITY_SECTION_CAP_RATIOS.get(section, 0.25)),
        )
        for section in section_order
    }


def fit_identity_section_text(
    text: str,
    *,
    cap_tokens: int,
    strategy: str,
    fit_to_budget: Callable[[str, int], tuple[str, bool]],
) -> tuple[str, bool]:
    cap = max(0, int(cap_tokens))
    if cap == 0:
        return "", bool(text.strip())

    compact = str(text or "").strip()
    if not compact:
        return "", False

    max_chars = cap * 4
    if len(compact) <= max_chars:
        return compact, False

    if strategy == "bullets":
        lines = [line.strip() for line in compact.splitlines() if line.strip()]
        if lines:
            kept: list[str] = []
            for line in lines:
                candidate = "\n".join([*kept, line]).strip()
                if len(candidate) > max_chars:
                    break
                kept.append(line)
            if kept:
                return "\n".join(kept).strip(), True
    else:
        collapsed = re.sub(r"\s+", " ", compact).strip()
        chunks = [
            item.strip()
            for item in re.split(r"(?<=[.!?])\s+", collapsed)
            if item.strip()
        ]
        if chunks:
            kept_sentence = ""
            for chunk in chunks:
                candidate = (
                    f"{kept_sentence} {chunk}".strip() if kept_sentence else chunk
                )
                if len(candidate) > max_chars:
                    break
                kept_sentence = candidate
            if kept_sentence:
                return kept_sentence, True

    return fit_to_budget(compact, cap)


def resolve_identity_budget_config(
    payload: Any | None,
) -> ResolvedIdentityBudgetConfig | None:
    if payload is None:
        return None

    getter = (
        payload.get
        if isinstance(payload, dict)
        else lambda key, default=None: getattr(payload, key, default)
    )

    try:
        total_tokens = max(1, int(getter("total_tokens", 200) or 200))
    except (TypeError, ValueError):
        total_tokens = 200

    section_order_raw = getter("section_order", DEFAULT_IDENTITY_SECTION_ORDER)
    section_order: list[str] = []
    seen: set[str] = set()
    if isinstance(section_order_raw, list):
        for item in section_order_raw:
            name = normalize_identity_section_name(item)
            if not name or name in seen:
                continue
            seen.add(name)
            section_order.append(name)
    if not section_order:
        section_order = list(DEFAULT_IDENTITY_SECTION_ORDER)

    section_priority_raw = getter("section_priority", {}) or {}
    section_priority: dict[str, int] = {}
    if isinstance(section_priority_raw, dict):
        for raw_key, raw_value in section_priority_raw.items():
            key = normalize_identity_section_name(raw_key)
            if not key:
                continue
            try:
                section_priority[key] = int(raw_value)
            except (TypeError, ValueError):
                continue

    section_caps = derive_identity_section_caps(total_tokens, section_order)
    section_caps_raw = getter("section_caps", {}) or {}
    if isinstance(section_caps_raw, dict):
        for raw_key, raw_value in section_caps_raw.items():
            key = normalize_identity_section_name(raw_key)
            if not key:
                continue
            try:
                parsed = int(raw_value)
            except (TypeError, ValueError):
                continue
            section_caps[key] = min(max(0, parsed), total_tokens)

    raw_strategy = str(getter("truncate_strategy", "sentences") or "").strip().lower()
    truncate_strategy = (
        "bullets" if raw_strategy in {"bullet", "bullets"} else "sentences"
    )

    return ResolvedIdentityBudgetConfig(
        total_tokens=total_tokens,
        section_order=section_order,
        section_priority=section_priority,
        section_caps=section_caps,
        truncate_strategy=truncate_strategy,
    )


def apply_identity_budget(
    *,
    identity: Any,
    cap_tokens: int,
    cfg: ResolvedIdentityBudgetConfig | None,
    fit_to_budget: Callable[[str, int], tuple[str, bool]],
    estimate_tokens: Callable[[str], int],
) -> IdentityBudgetResult:
    sections_attr = getattr(identity, "sections", None)
    sections_raw = sections_attr if isinstance(sections_attr, dict) else {}
    normalized_sections: dict[str, str] = {}
    for raw_key, raw_value in sections_raw.items():
        name = normalize_identity_section_name(raw_key)
        body = str(raw_value or "").strip()
        if not name or not body:
            continue
        normalized_sections[name] = body

    if not normalized_sections:
        fitted, truncated = fit_to_budget(identity.text, cap_tokens)
        used_tokens = estimate_tokens(fitted) if fitted.strip() else 0
        return IdentityBudgetResult(
            text=fitted,
            cap_tokens=cap_tokens,
            used_tokens=used_tokens,
            truncated=truncated,
            subsections={},
            ordering_applied=[],
            unknown_sections=[],
        )

    if cfg is None:
        cfg = ResolvedIdentityBudgetConfig(
            total_tokens=cap_tokens,
            section_order=list(DEFAULT_IDENTITY_SECTION_ORDER),
            section_priority={},
            section_caps=derive_identity_section_caps(
                cap_tokens, DEFAULT_IDENTITY_SECTION_ORDER
            ),
            truncate_strategy="sentences",
        )

    total_cap = min(cap_tokens, max(1, int(cfg.total_tokens)))
    section_order = list(cfg.section_order)
    section_caps = dict(cfg.section_caps)
    section_priority = dict(cfg.section_priority)
    order_index = {name: idx for idx, name in enumerate(section_order)}

    known_sections = [name for name in section_order if name in normalized_sections]
    unknown_sections = sorted(
        name for name in normalized_sections if name not in order_index
    )
    candidates = [*known_sections, *unknown_sections]

    if section_priority:

        def _implicit_priority(section_name: str) -> int:
            idx = order_index.get(section_name)
            if idx is None:
                return -1
            return max(0, len(section_order) - idx)

        candidates = sorted(
            candidates,
            key=lambda section_name: (
                -int(
                    section_priority.get(section_name, _implicit_priority(section_name))
                ),
                order_index.get(section_name, len(section_order) + 1000),
                section_name,
            ),
        )

    fallback_caps = derive_identity_section_caps(total_cap, section_order)
    default_cap = max(1, total_cap // max(1, len(candidates)))
    remaining = total_cap
    combined_sections: list[str] = []
    subsections: dict[str, IdentitySubsectionUsage] = {}
    truncated_any = False

    for section_name in candidates:
        raw_text = normalized_sections[section_name]
        section_cap = section_caps.get(
            section_name, fallback_caps.get(section_name, default_cap)
        )
        section_cap = min(max(0, int(section_cap)), total_cap)
        if remaining <= 0:
            subsections[section_name] = IdentitySubsectionUsage(
                cap_tokens=0,
                used_tokens=0,
                truncated=False,
                omitted_reason="global_cap_exhausted",
            )
            continue

        effective_cap = min(section_cap, remaining)
        fitted, truncated = fit_identity_section_text(
            raw_text,
            cap_tokens=effective_cap,
            strategy=cfg.truncate_strategy,
            fit_to_budget=fit_to_budget,
        )
        used_tokens = estimate_tokens(fitted) if fitted.strip() else 0
        if used_tokens > effective_cap and effective_cap > 0:
            fitted, truncated = fit_to_budget(fitted, effective_cap)
            used_tokens = estimate_tokens(fitted) if fitted.strip() else 0

        omitted_reason = None
        if not fitted.strip() and raw_text.strip():
            omitted_reason = "section_cap_exhausted"
        subsections[section_name] = IdentitySubsectionUsage(
            cap_tokens=effective_cap,
            used_tokens=used_tokens,
            truncated=truncated,
            omitted_reason=omitted_reason,
        )
        if fitted.strip():
            combined_sections.append(f"[{section_name.upper()}]\n{fitted.strip()}")
        remaining = max(0, remaining - used_tokens)
        truncated_any = truncated_any or truncated

    final_text = "\n\n".join(combined_sections).strip()
    if not final_text:
        final_text, truncated_fallback = fit_to_budget(identity.text, total_cap)
        truncated_any = truncated_any or truncated_fallback
    used_total = estimate_tokens(final_text) if final_text.strip() else 0

    return IdentityBudgetResult(
        text=final_text,
        cap_tokens=total_cap,
        used_tokens=min(used_total, total_cap),
        truncated=truncated_any,
        subsections=subsections,
        ordering_applied=list(candidates),
        unknown_sections=unknown_sections,
    )
