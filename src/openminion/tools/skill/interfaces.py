from typing import TypedDict

SKILL_PLUGIN_INTERFACE_VERSION = "v1"


class SkillIngestResult(TypedDict, total=False):
    ok: bool
    skill_id: str
    version_hash: str
    snippet: str
    snippet_hash: str
    warnings: list
    risk_level: str
    safe: bool
    issues: list
    safety_enforced: bool


class SkillInspectIssue(TypedDict, total=False):
    code: str
    message: str
    risk: str


class SkillInspectResult(TypedDict, total=False):
    ok: bool
    risk_level: str
    safe: bool
    issues: list[SkillInspectIssue]


class SkillListResult(TypedDict, total=False):
    ok: bool
    skills: list[dict]
    total: int


class SkillGetResult(TypedDict, total=False):
    ok: bool
    skill: dict


class SkillRemoveResult(TypedDict, total=False):
    ok: bool
    skill_id: str
    deleted: int


__all__ = [
    "SKILL_PLUGIN_INTERFACE_VERSION",
    "SkillIngestResult",
    "SkillInspectIssue",
    "SkillInspectResult",
    "SkillListResult",
    "SkillGetResult",
    "SkillRemoveResult",
]
