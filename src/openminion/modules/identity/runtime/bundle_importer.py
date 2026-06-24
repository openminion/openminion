from dataclasses import dataclass
import re
from typing import Iterable

from openminion.modules.identity.runtime.defaults import (
    default_mission,
    normalize_identity_text,
)
from openminion.modules.identity.models import (
    AgentProfile,
    PersonalitySpec,
    RiskSpec,
    RoleSpec,
    ToolPostureSpec,
)


@dataclass(frozen=True)
class BundleTextDocument:
    relative_path: str
    content: str


@dataclass(frozen=True)
class ParsedBundleContent:
    mission: str
    responsibilities: tuple[str, ...]
    constraints: tuple[str, ...]
    escalation_rules: tuple[str, ...]
    voice: tuple[str, ...]
    values: tuple[str, ...]
    decision_bias: tuple[str, ...]
    skills: tuple[str, ...]


_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
DEFAULT_TONE = "professional"
DEFAULT_RISK_LEVEL = "medium"
DEFAULT_CONFIRM_BEFORE = ("destructive_actions",)
DEFAULT_TOOL_USE = "allowed"


def parse_bundle_documents(
    documents: Iterable[BundleTextDocument],
) -> ParsedBundleContent:
    """Parse markdown bundle text into normalized sections (pure, no file I/O)."""
    agent_md = ""
    soul_md = ""
    skills: list[str] = []

    for document in documents:
        normalized = _normalize_path(document.relative_path)
        content = str(document.content or "")
        if normalized == "agent.md":
            agent_md = content
            continue
        if normalized == "soul.md":
            soul_md = content
            continue
        if normalized.startswith("skills/") and normalized.endswith("/skill.md"):
            skill_name = _skill_name_from_path(normalized)
            summary = summarize_markdown(content)
            if skill_name and summary:
                skills.append(
                    _skill_responsibility(skill_name=skill_name, summary=summary)
                )

    agent_sections = split_markdown_sections(agent_md)
    soul_sections = split_markdown_sections(soul_md)
    return ParsedBundleContent(
        mission=normalize_text(agent_sections.get("mission", "")),
        responsibilities=parse_bullets(agent_sections.get("responsibilities", "")),
        constraints=parse_bullets(agent_sections.get("constraints", "")),
        escalation_rules=parse_bullets(agent_sections.get("escalation policy", "")),
        voice=parse_bullets(soul_sections.get("voice", "")),
        values=parse_bullets(soul_sections.get("values", "")),
        decision_bias=parse_bullets(soul_sections.get("decision bias", "")),
        skills=tuple(skills),
    )


def split_markdown_sections(markdown: str) -> dict[str, str]:
    """Split markdown by H2 headings into a lowercase-section map."""
    text = str(markdown or "")
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return {}
    sections: dict[str, str] = {}
    for idx, match in enumerate(matches):
        title = normalize_text(match.group(1)).lower()
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        sections[title] = text[start:end].strip()
    return sections


def parse_bullets(value: str) -> tuple[str, ...]:
    """Extract markdown bullet lines; fallback to normalized paragraph if no bullets."""
    text = str(value or "").strip()
    if not text:
        return ()
    bullets: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith(("-", "*")):
            continue
        bullet = normalize_text(line[1:])
        if bullet:
            bullets.append(bullet)
    if bullets:
        return tuple(bullets)
    normalized = normalize_text(text)
    return (normalized,) if normalized else ()


def summarize_markdown(markdown: str, *, max_chars: int = 220) -> str:
    """Build a compact single-line summary from markdown text."""
    text = normalize_text(markdown)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def normalize_text(value: str) -> str:
    return normalize_identity_text(value)


def build_profile_from_bundle_documents(
    *,
    agent_id: str,
    documents: Iterable[BundleTextDocument],
    profile_revision: int = 1,
    display_name: str | None = None,
    system_prompt: str = "",
) -> AgentProfile:
    parsed = parse_bundle_documents(documents)
    return build_profile_from_parsed_bundle(
        agent_id=agent_id,
        parsed=parsed,
        profile_revision=profile_revision,
        display_name=display_name,
        system_prompt=system_prompt,
    )


def build_profile_from_parsed_bundle(
    *,
    agent_id: str,
    parsed: ParsedBundleContent,
    profile_revision: int = 1,
    display_name: str | None = None,
    system_prompt: str = "",
) -> AgentProfile:
    mission = parsed.mission or default_mission(
        agent_id=agent_id, system_prompt=system_prompt
    )
    tone = _compose_tone(parsed.voice) if parsed.voice else DEFAULT_TONE
    return AgentProfile(
        agent_id=normalize_text(agent_id),
        display_name=normalize_text(display_name or agent_id),
        profile_revision=max(1, int(profile_revision)),
        role=RoleSpec(
            mission=mission,
            responsibilities=list(parsed.responsibilities + parsed.skills),
            hard_constraints=list(parsed.constraints),
            escalation_rules=list(parsed.escalation_rules),
        ),
        personality=PersonalitySpec(
            tone=tone,
            verbosity="normal",
            formatting=list(parsed.decision_bias),
            interaction_style=list(parsed.values),
        ),
        risk=RiskSpec(
            risk_level=DEFAULT_RISK_LEVEL,
            confirm_before=list(DEFAULT_CONFIRM_BEFORE),
        ),
        tool_posture=ToolPostureSpec(
            tool_use=DEFAULT_TOOL_USE,
        ),
    )


def _normalize_path(path: str) -> str:
    return str(path or "").strip().replace("\\", "/").lower().lstrip("./")


def _skill_name_from_path(normalized_path: str) -> str:
    # Input is normalized lowercase path; expected form: skills/<name>/skill.md
    parts = normalized_path.split("/")
    if len(parts) < 3:
        return ""
    raw_name = parts[-2].strip().replace("-", " ")
    return normalize_text(raw_name)


def _skill_responsibility(*, skill_name: str, summary: str) -> str:
    return normalize_text(f"Use {skill_name} skill: {summary}")


def _compose_tone(voice: tuple[str, ...]) -> str:
    if not voice:
        return DEFAULT_TONE
    normalized = [
        normalize_text(item).rstrip(".") for item in voice if normalize_text(item)
    ]
    if not normalized:
        return DEFAULT_TONE
    return ". ".join(normalized) + "."
