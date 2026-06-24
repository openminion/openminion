from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from openminion.base.time import utc_now_iso as _utc_now_iso


class Project(BaseModel):
    """Project entity used to bind sessions and scheduled work."""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    master_instruction: str = Field(default="")
    skill_set: list[str] = Field(default_factory=list)
    scheduled_triggers: list[str] = Field(
        default_factory=list,
        description="CronEntryRef ids; see services/cron/.",
    )
    created_at: str = Field(default_factory=_utc_now_iso)

    @field_validator("project_id", "name", "master_instruction", mode="before")
    @classmethod
    def _strip_text(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("skill_set", "scheduled_triggers", mode="before")
    @classmethod
    def _dedupe_string_list(cls, value: Any) -> list[str]:
        if value is None:
            return []
        seen: set[str] = set()
        ordered: list[str] = []
        for raw in list(value):
            token = str(raw or "").strip()
            if token and token not in seen:
                seen.add(token)
                ordered.append(token)
        return ordered

    def memory_scope_key(self) -> str:
        """Return the memory scope key for this project."""
        return f"project:{self.project_id}"


class ProjectSessionBinding(BaseModel):
    """Binding of one session to one project."""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    bound_at: str = Field(default_factory=_utc_now_iso)

    @field_validator("project_id", "session_id", mode="before")
    @classmethod
    def _strip_required(cls, value: Any) -> str:
        return str(value or "").strip()


__all__ = ["Project", "ProjectSessionBinding"]
