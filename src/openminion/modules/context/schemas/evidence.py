# ruff: noqa: F403,F405
from .common import *

class EvidenceItem(BaseModel):
    """A candidate evidence item produced by a ContextRetriever."""

    ref: str
    content: str
    score: float = 0.0
    source: str = ""  # retriever name that produced this
    metadata: Dict[str, Any] = Field(default_factory=dict)
