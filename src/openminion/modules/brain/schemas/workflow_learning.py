from typing import Literal

from pydantic import BaseModel, ConfigDict


class WorkflowLearningSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_category: Literal[
        "analyze",
        "create",
        "modify",
        "verify",
        "operate",
        "research",
        "communicate",
    ]
    capability_category: Literal[
        "code",
        "files",
        "shell",
        "web",
        "browser",
        "data",
        "system",
        "collaboration",
    ]
