# ruff: noqa: F403,F405
from .common import *


class IdentitySnippet(BaseModel):
    agent_id: str
    purpose: str = ""
    profile_version: str
    render_version: str
    text: str
    budget: Optional[dict] = None
    sections: Optional[Dict[str, str]] = None
    included_fields: List[str] = Field(default_factory=list)
    omitted_fields: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class FactRecord(BaseModel):
    record_id: str
    text: str
    score: float = 0.0
    confidence: float = 0.0
    ttl_valid: bool = True
    record_type: str = "fact"
    source: str = ""
    tags: List[str] = Field(default_factory=list)
    meta: Dict[str, Any] = Field(default_factory=dict)


class MemoryCard(BaseModel):
    record_id: str
    record_type: str
    text: str
    score: float = 0.0
    pinned: bool = False
    source: str = ""
    tags: List[str] = Field(default_factory=list)
    meta: Dict[str, Any] = Field(default_factory=dict)


class RecentSessionArtifactRef(BaseModel):
    record_id: str
    artifact_type: str
    artifact_path: str
    artifact_digest: str = ""
    session_id: str
    turn_index: int = 0
    tool_name: str = ""


class ProcedureSnippet(BaseModel):
    procedure_id: str
    title: str
    preflight: List[str] = Field(default_factory=list)
    steps: List[str] = Field(default_factory=list)
    rollback_hint: str = ""


class ArtifactDigest(BaseModel):
    ref: str
    view_id: Optional[str] = None
    digest_hash: str = ""
    bullets: List[str] = Field(default_factory=list)
    excerpt: Optional[str] = None
    score: float = 0.0
