from dataclasses import dataclass

from openminion.modules.identity.models import AgentProfile


@dataclass(frozen=True)
class BundleMarkdownDocument:
    relative_path: str
    content: str


@dataclass(frozen=True)
class BundleMarkdownExport:
    agent_id: str
    documents: tuple[BundleMarkdownDocument, ...]
    lossy_fields: tuple[str, ...]


def export_profile_to_markdown_bundle(profile: AgentProfile) -> BundleMarkdownExport:
    docs = (
        BundleMarkdownDocument(
            relative_path="AGENT.md",
            content=_render_agent_markdown(profile),
        ),
        BundleMarkdownDocument(
            relative_path="SOUL.md",
            content=_render_soul_markdown(profile),
        ),
    )
    return BundleMarkdownExport(
        agent_id=profile.agent_id,
        documents=docs,
        lossy_fields=_collect_lossy_fields(profile),
    )


def _render_agent_markdown(profile: AgentProfile) -> str:
    return (
        "## Mission\n"
        f"{profile.role.mission.strip()}\n\n"
        "## Responsibilities\n"
        f"{_render_bullets(profile.role.responsibilities)}\n\n"
        "## Constraints\n"
        f"{_render_bullets(profile.role.hard_constraints)}\n\n"
        "## Escalation Policy\n"
        f"{_render_bullets(profile.role.escalation_rules)}\n"
    )


def _render_soul_markdown(profile: AgentProfile) -> str:
    return (
        "## Voice\n"
        f"{_render_bullets([profile.personality.tone])}\n\n"
        "## Values\n"
        f"{_render_bullets(profile.personality.interaction_style)}\n\n"
        "## Decision Bias\n"
        f"{_render_bullets(profile.personality.formatting)}\n"
    )


def _render_bullets(items: list[str]) -> str:
    cleaned = [item.strip() for item in items if item.strip()]
    if not cleaned:
        return "- n/a"
    return "\n".join(f"- {line}" for line in cleaned)


def _collect_lossy_fields(profile: AgentProfile) -> tuple[str, ...]:
    fields: list[str] = []
    if profile.role.domain:
        fields.append("role.domain")
    if profile.personality.verbosity != "normal":
        fields.append("personality.verbosity")

    if (
        profile.risk.risk_level != "medium"
        or profile.risk.confirm_before != ["destructive_actions"]
        or profile.risk.auto_proceed_rules
    ):
        fields.append("risk.*")

    if (
        profile.tool_posture.tool_use != "allowed"
        or profile.tool_posture.sandbox_root
        or profile.tool_posture.blocked_patterns
        or profile.tool_posture.allowed_tools
    ):
        fields.append("tool_posture.*")

    if profile.inherits:
        fields.append("inherits")
    if profile.llm_policy_ref:
        fields.append("llm_policy_ref")
    if profile.allowed_capabilities:
        fields.append("allowed_capabilities")
    if profile.meta:
        fields.append("meta.*")
    return tuple(fields)
