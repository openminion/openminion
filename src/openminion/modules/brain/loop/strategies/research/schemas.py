from __future__ import annotations

from typing import Literal

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


class ResearchSynthesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(..., min_length=1)
    status: Literal["complete", "incomplete", "blocked"] = "complete"
    remaining_work: str = ""


class ConvergenceCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    converged: bool
    reasoning: str
    suggested_next_query: str = ""
