from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from openminion.base.time import utc_now_iso as iso_now

Purpose = Literal["plan", "act", "verify", "summarize", "decide"]
SourceType = Literal["episode", "artifact", "skill", "mem", "doc"]
ScopeType = Literal["session", "agent", "global", "project"]
RetrievalStrategy = Literal["auto", "contextual", "raptor", "longrag_doc_group"]
UnitKind = Literal["chunk", "doc_group", "document"]
HierarchyLevel = Literal["none", "root", "internal", "leaf"]
RLMRaptorLevel = Literal["none", "internal", "leaf"]
RLMSourceType = Literal["wm", "em", "sm", "skill", "session"]


class RetrievalFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time_window_hours: int | None = Field(default=None, ge=1, le=24 * 365)
    tags: list[str] = Field(default_factory=list)
    types: list[SourceType] = Field(default_factory=list)
    scope_keys: list[str] = Field(default_factory=list)
    risk_constraints: dict[str, Any] = Field(default_factory=dict)


class RetrieveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    purpose: Purpose = "act"
    scope: dict[str, Any] = Field(default_factory=dict)
    filters: RetrievalFilters = Field(default_factory=RetrievalFilters)
    k: int = Field(default=8, ge=1, le=128)
    strategy: RetrievalStrategy = "auto"


class DocUnit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_id: str
    source_ref: str
    source_type: SourceType
    text: str
    scope: ScopeType
    tags: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=iso_now)
    updated_at: str = Field(default_factory=iso_now)
    title: str = ""
    corpus_id: str | None = None


class RetrievedItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref_type: SourceType
    ref_id: str
    text_snippet: str
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    why: str = ""
    level: HierarchyLevel = "none"
    unit_kind: UnitKind = "chunk"
    meta: dict[str, Any] = Field(default_factory=dict)

    # Compatibility fields used by openminion-rlm normalization.
    source: RLMSourceType = "em"
    text: str = ""
    recency_score: float = Field(default=0.0, ge=0.0, le=1.0)
    tags: list[str] = Field(default_factory=list)
    created_at: str | None = None
    retrieval_strategy: RetrievalStrategy = "contextual"
    raptor_level: RLMRaptorLevel = "none"
    node_id: str | None = None
    doc_group_id: str | None = None
    trust_score: float = Field(default=0.6, ge=0.0, le=1.0)


class IngestResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_id: str
    source_ref: str
    source_type: SourceType
    unit_kind: UnitKind
    unit_count: int


class RaptorBuildResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_id: str
    root_node_id: str
    internal_node_count: int
    leaf_count: int


class GroupLongUnitsResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    corpus_id: str
    docs_updated: int
    groups_created: int
