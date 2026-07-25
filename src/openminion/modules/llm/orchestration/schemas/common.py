# ruff: noqa: F401
import uuid
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...schemas import Message, ResponseError

EnsembleMode = Literal["second_opinion", "panel_judge", "self_consistency", "vote"]
SelectionPolicyName = Literal[
    "pick_primary_if_ok",
    "pick_highest_score",
    "majority_vote",
    "first_success",
    "ask_user_on_disagreement",
]
CandidateStatus = Literal["success", "failed", "timeout"]
FallbackMode = Literal["single", "ensemble"]
ProviderCapabilityName = Literal[
    "json",
    "tools",
    "vision",
    "streaming",
    "prompt_caching",
    "cost",
    "auth",
]
