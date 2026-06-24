from dataclasses import dataclass
import re
from typing import Any, Iterable

from openminion.modules.identity.models import (
    AgentProfile,
    IdentitySnippet,
    SnippetBudget,
)


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4) if text else 0


_CANONICAL_PURPOSES = frozenset(
    ("decide", "plan", "act", "reflect", "summarize", "judge")
)

_PURPOSE_ALIASES = {
    "decision": "decide",
    "planning": "plan",
    "reflection": "reflect",
    "summary": "summarize",
    "summarization": "summarize",
    "validate": "judge",
    "verify": "judge",
    "validation": "judge",
    "chat": "act",
    "respond_followup": "act",
    "follow_up": "act",
    "followup": "act",
    "reply": "act",
    "response": "act",
}


def normalize_purpose(purpose: str) -> str:
    normalized = str(purpose or "").strip().lower().replace("-", "_")
    normalized = _PURPOSE_ALIASES.get(normalized, normalized)
    return normalized if normalized in _CANONICAL_PURPOSES else "act"


@dataclass(frozen=True)
class PurposeSpec:
    constraints: int
    confirm_rules: int
    style_rules: int
    include_escalation: bool
    include_responsibilities: bool
    emphasize_tool_posture: bool


_PURPOSES: dict[str, PurposeSpec] = {
    "decide": PurposeSpec(
        constraints=3,
        confirm_rules=2,
        style_rules=2,
        include_escalation=False,
        include_responsibilities=False,
        emphasize_tool_posture=False,
    ),
    "plan": PurposeSpec(
        constraints=5,
        confirm_rules=5,
        style_rules=4,
        include_escalation=True,
        include_responsibilities=True,
        emphasize_tool_posture=False,
    ),
    "act": PurposeSpec(
        constraints=5,
        confirm_rules=4,
        style_rules=1,
        include_escalation=False,
        include_responsibilities=False,
        emphasize_tool_posture=True,
    ),
    "reflect": PurposeSpec(
        constraints=5,
        confirm_rules=4,
        style_rules=3,
        include_escalation=True,
        include_responsibilities=True,
        emphasize_tool_posture=False,
    ),
    "summarize": PurposeSpec(
        constraints=3,
        confirm_rules=2,
        style_rules=2,
        include_escalation=False,
        include_responsibilities=False,
        emphasize_tool_posture=False,
    ),
    "judge": PurposeSpec(
        constraints=4,
        confirm_rules=3,
        style_rules=1,
        include_escalation=False,
        include_responsibilities=False,
        emphasize_tool_posture=True,
    ),
}


@dataclass
class LineItem:
    text: str
    field: str
    category: str


@dataclass
class Section:
    name: str
    header: str
    items: list[LineItem]


_DROP_ORDER = [
    "skill_query",
    "skill_always",
    "style",
    "escalation",
    "constraint",
    "tool",
    "responsibility",
]


_SKILL_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9]+")


def _skill_id_tokens(skill_id: str) -> list[str]:
    text = str(skill_id or "").strip().lower().replace("_", "-")
    parts = [part for part in _SKILL_TOKEN_SPLIT_RE.split(text) if len(part) >= 3]
    return parts


def _query_activates_skill(*, query_text: str, skill_id: str) -> bool:
    normalized_query = str(query_text or "").strip().lower()
    if not normalized_query:
        return False
    tokens = _skill_id_tokens(skill_id)
    if not tokens:
        return False
    return any(token in normalized_query for token in tokens)


def _render_skill_items(
    *,
    profile: AgentProfile,
    skillctl: Any | None,
    purpose: str,
    query_text: str | None,
    bullet_prefix: str,
) -> tuple[list[LineItem], list[str], list[str]]:
    posture = profile.skill_posture
    if posture is None or skillctl is None:
        return [], [], []

    excluded = set(posture.excluded)
    omitted_fields = [f"skill:{skill_id}" for skill_id in sorted(excluded)]
    ordered_ids: list[tuple[str, str]] = []
    seen: set[str] = set()
    for skill_id in posture.always_active:
        if skill_id in excluded or skill_id in seen:
            continue
        ordered_ids.append((skill_id, "skill_always"))
        seen.add(skill_id)
    for skill_id in posture.query_activated:
        if skill_id in excluded or skill_id in seen:
            continue
        if not _query_activates_skill(
            query_text=str(query_text or ""),
            skill_id=skill_id,
        ):
            continue
        ordered_ids.append((skill_id, "skill_query"))
        seen.add(skill_id)

    if not ordered_ids:
        return [], omitted_fields, []

    warnings: list[str] = []
    items: list[LineItem] = []
    remaining_budget = max(1, int(posture.max_skill_tokens))

    for index, (skill_id, category) in enumerate(ordered_ids):
        remaining_candidates = max(1, len(ordered_ids) - index)
        request_tokens = max(1, remaining_budget // remaining_candidates)
        try:
            snippet_text, version_hash = skillctl.render_snippet(
                skill_id=skill_id,
                version_hash=None,
                purpose=purpose,
                max_tokens=request_tokens,
            )
        except Exception:
            warnings.append(f"skill_render_failed:{skill_id}")
            omitted_fields.append(f"skill:{skill_id}")
            continue
        normalized_text = str(snippet_text or "").strip()
        normalized_hash = str(version_hash or "").strip()
        if not normalized_text or not normalized_hash:
            warnings.append(f"skill_render_empty:{skill_id}")
            omitted_fields.append(f"skill:{skill_id}")
            continue
        hash_prefix = normalized_hash[:12]
        items.append(
            LineItem(
                text=f"{bullet_prefix}{skill_id}@{hash_prefix}\n{normalized_text}",
                field=f"skill:{skill_id}@{hash_prefix}",
                category=category,
            )
        )
        remaining_budget = max(1, remaining_budget - estimate_tokens(normalized_text))

    return items, omitted_fields, warnings


def _take(
    values: list[str], limit: int, field_prefix: str
) -> tuple[list[tuple[str, str]], list[str]]:
    if limit <= 0:
        return [], [f"{field_prefix}[{idx}]" for idx, _ in enumerate(values)]
    selected = values[:limit]
    omitted = [f"{field_prefix}[{idx}]" for idx in range(limit, len(values))]
    pairs = [(selected[idx], f"{field_prefix}[{idx}]") for idx in range(len(selected))]
    return pairs, omitted


def _render_sections(
    sections: list[Section],
    *,
    section_headers: bool,
) -> str:
    chunks: list[str] = []
    for section in sections:
        if not section.items:
            continue
        lines = [item.text for item in section.items]
        if section_headers:
            chunks.append(f"[{section.header}]\n" + "\n".join(lines))
        else:
            chunks.append("\n".join(lines))
    return "\n\n".join(chunk.strip() for chunk in chunks if chunk.strip()).strip()


def _section_text_map(
    sections: list[Section],
    *,
    section_headers: bool,
) -> dict[str, str]:
    rendered: dict[str, str] = {}
    for section in sections:
        if not section.items:
            continue
        text = "\n".join(item.text for item in section.items)
        if section_headers:
            text = f"[{section.header}]\n{text}"
        text = text.strip()
        if text:
            rendered[section.name] = text
    return rendered


def _iter_items(sections: Iterable[Section]) -> Iterable[LineItem]:
    for section in sections:
        for item in section.items:
            yield item


def render_identity_snippet(
    profile: AgentProfile,
    *,
    purpose: str,
    max_tokens: int,
    max_chars: int | None,
    render_version: str,
    profile_version: str,
    bullet_prefix: str,
    section_headers: bool,
    skillctl: Any | None = None,
    query_text: str | None = None,
) -> IdentitySnippet:
    resolved_purpose = normalize_purpose(purpose)
    spec = _PURPOSES.get(resolved_purpose, _PURPOSES["act"])
    effective_max_chars = max(1, int(max_chars or (max_tokens * 4)))
    omitted_fields: list[str] = []
    warnings: list[str] = []
    sections = _initial_sections(
        profile=profile,
        spec=spec,
        omitted_fields=omitted_fields,
        bullet_prefix=bullet_prefix,
        skillctl=skillctl,
        purpose=resolved_purpose,
        query_text=query_text,
        warnings=warnings,
    )
    text, truncated = _truncate_sections_to_budget(
        sections=sections,
        section_headers=section_headers,
        max_tokens=max_tokens,
        max_chars=effective_max_chars,
        omitted_fields=omitted_fields,
        warnings=warnings,
    )
    mission_text = " ".join(profile.role.mission.splitlines()).strip()
    if not text:
        text = f"Mission: {mission_text}"
        warnings.append("fallback mission-only snippet applied")
    used_chars = len(text)
    used_tokens = estimate_tokens(text)
    included_fields = sorted({item.field for item in _iter_items(sections)})
    deduped_omitted = sorted(set(omitted_fields) - set(included_fields))
    if truncated and used_tokens <= max_tokens and used_chars <= effective_max_chars:
        warnings.append("non-critical fields omitted to fit budget")
    return IdentitySnippet(
        agent_id=profile.agent_id,
        purpose=resolved_purpose,
        text=text,
        profile_version=profile_version,
        render_version=render_version,
        budget=SnippetBudget(
            max_tokens=max_tokens,
            used_tokens=used_tokens,
            max_chars=effective_max_chars,
            used_chars=used_chars,
        ),
        sections=_section_text_map(sections, section_headers=section_headers),
        included_fields=included_fields,
        omitted_fields=deduped_omitted,
        warnings=warnings,
    )


def _initial_sections(
    *,
    profile: AgentProfile,
    spec: PurposeSpec,
    omitted_fields: list[str],
    bullet_prefix: str,
    skillctl: Any | None,
    purpose: str,
    query_text: str | None,
    warnings: list[str],
) -> list[Section]:
    mission_text = " ".join(profile.role.mission.splitlines()).strip()
    constraints, omitted = _take(
        list(profile.role.hard_constraints), spec.constraints, "role.hard_constraints"
    )
    omitted_fields.extend(omitted)
    confirms, omitted = _take(
        profile.risk.confirm_before, spec.confirm_rules, "risk.confirm_before"
    )
    omitted_fields.extend(omitted)
    style_candidates = (
        profile.personality.formatting + profile.personality.interaction_style
    )
    styles, omitted = _take(
        style_candidates, spec.style_rules, "personality.style_rules"
    )
    omitted_fields.extend(omitted)
    skill_items, omitted_skill_fields, skill_warnings = _render_skill_items(
        profile=profile,
        skillctl=skillctl,
        purpose=purpose,
        query_text=query_text,
        bullet_prefix=bullet_prefix,
    )
    omitted_fields.extend(omitted_skill_fields)
    warnings.extend(skill_warnings)
    sections = [
        Section(
            "mission",
            "ROLE MISSION",
            _mission_items(profile, mission_text, spec, bullet_prefix),
        ),
        Section(
            "constraints",
            "HARD CONSTRAINTS",
            [
                LineItem(f"{bullet_prefix}{value}", field, "constraint")
                for value, field in constraints
            ],
        ),
        Section("risk", "RISK POSTURE", _risk_items(profile, confirms, bullet_prefix)),
        Section("tool", "TOOL POSTURE", _tool_items(profile, spec, bullet_prefix)),
        Section("style", "STYLE", _style_items(profile, styles, bullet_prefix)),
    ]
    if skill_items:
        sections.append(Section("skills", "SKILLS", skill_items))
    if spec.include_escalation:
        sections.append(
            Section(
                "escalation", "ESCALATION", _escalation_items(profile, bullet_prefix)
            )
        )
    return sections


def _mission_items(
    profile: AgentProfile, mission_text: str, spec: PurposeSpec, bullet_prefix: str
) -> list[LineItem]:
    items = [LineItem(f"Mission: {mission_text}", "role.mission", "mission")]
    if spec.include_responsibilities:
        items.extend(
            LineItem(
                f"{bullet_prefix}responsibility: {value}",
                f"role.responsibilities[{idx}]",
                "responsibility",
            )
            for idx, value in enumerate(profile.role.responsibilities)
        )
    return items


def _risk_items(
    profile: AgentProfile, confirms: list[tuple[str, str]], bullet_prefix: str
) -> list[LineItem]:
    items = [
        LineItem(f"Risk level: {profile.risk.risk_level}", "risk.risk_level", "risk")
    ]
    items.extend(
        LineItem(f"{bullet_prefix}confirm_before: {value}", field, "confirm")
        for value, field in confirms
    )
    return items


def _tool_items(
    profile: AgentProfile, spec: PurposeSpec, bullet_prefix: str
) -> list[LineItem]:
    summary = f"Tool posture: {profile.tool_posture.tool_use}"
    if profile.tool_posture.sandbox_root:
        summary += f"; sandbox={profile.tool_posture.sandbox_root}"
    items = [LineItem(summary, "tool_posture.tool_use", "tool")]
    if spec.emphasize_tool_posture:
        allowed_tools = profile.tool_posture.allowed_tools[:3]
        if allowed_tools:
            items.append(
                LineItem(
                    f"{bullet_prefix}allowed_tools: {', '.join(allowed_tools)}",
                    "tool_posture.allowed_tools",
                    "tool",
                )
            )
        blocked_patterns = profile.tool_posture.blocked_patterns[:2]
        if blocked_patterns:
            items.append(
                LineItem(
                    f"{bullet_prefix}blocked_patterns: {', '.join(blocked_patterns)}",
                    "tool_posture.blocked_patterns",
                    "tool",
                )
            )
    return items


def _style_items(
    profile: AgentProfile, styles: list[tuple[str, str]], bullet_prefix: str
) -> list[LineItem]:
    items = [
        LineItem(
            f"Style: tone={profile.personality.tone}; verbosity={profile.personality.verbosity}",
            "personality.tone",
            "style",
        )
    ]
    items.extend(
        LineItem(f"{bullet_prefix}{value}", field, "style") for value, field in styles
    )
    return items


def _escalation_items(profile: AgentProfile, bullet_prefix: str) -> list[LineItem]:
    return [
        LineItem(
            f"{bullet_prefix}{value}", f"role.escalation_rules[{idx}]", "escalation"
        )
        for idx, value in enumerate(profile.role.escalation_rules)
    ]


def _truncate_sections_to_budget(
    *,
    sections: list[Section],
    section_headers: bool,
    max_tokens: int,
    max_chars: int,
    omitted_fields: list[str],
    warnings: list[str],
) -> tuple[str, bool]:
    def current_text() -> str:
        return _render_sections(sections, section_headers=section_headers)

    def over_budget(text: str) -> bool:
        return estimate_tokens(text) > max_tokens or len(text) > max_chars

    text = current_text()
    truncated = over_budget(text)
    if truncated:
        warnings.append("snippet exceeds budget; truncation applied")
    while over_budget(text):
        removed = _drop_one_budget_item(sections)
        if removed is None:
            warnings.append("snippet still over budget after truncation")
            break
        omitted_fields.append(removed.field)
        text = current_text()
    return text, truncated


def _drop_one_budget_item(sections: list[Section]) -> LineItem | None:
    for category in _DROP_ORDER:
        for section in sections:
            for idx in range(len(section.items) - 1, -1, -1):
                if section.items[idx].category == category:
                    return section.items.pop(idx)
    return None
