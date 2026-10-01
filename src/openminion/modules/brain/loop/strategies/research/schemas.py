from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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

    @model_validator(mode="after")
    def require_remaining_work_for_nonterminal_status(self) -> "ResearchSynthesis":
        if self.status != "complete" and not self.remaining_work.strip():
            raise ValueError("remaining_work is required when research is not complete")
        return self


class ConvergenceCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    converged: bool
    reasoning: str
    suggested_next_query: str = ""
