from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ResearchPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    research_query: str = Field(..., min_length=1)
    research_scope: str = ""


class ResearchFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iteration: int
    source_tool: str
    source_query: str
    content: str
    evidence_dates: list[str] = Field(default_factory=list)


class ConvergenceCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    converged: bool
    reasoning: str
    suggested_next_query: str = ""
