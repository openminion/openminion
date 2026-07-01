"""A2A v1 task / message / part model.

Per the spec, a Task carries a sequence of Messages, each made of Parts (text,
file, structured data). The state machine moves through
``submitted -> working -> input-required | completed | failed | canceled``.
"""

import enum
from dataclasses import dataclass, field
from typing import Any


class TaskState(str, enum.Enum):
    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


TASK_STATES: frozenset[str] = frozenset(state.value for state in TaskState)


@dataclass
class TaskPart:
    kind: str  # "text" | "file" | "data"
    text: str | None = None
    data: dict[str, Any] | None = None
    file_url: str | None = None
    file_mime: str | None = None

    def to_jsonable(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"kind": self.kind}
        for key, value in (
            ("text", self.text),
            ("data", self.data),
            ("fileUrl", self.file_url),
            ("fileMime", self.file_mime),
        ):
            if value is not None:
                payload[key] = value
        return payload


@dataclass
class TaskMessage:
    role: str  # "user" | "agent"
    parts: list[TaskPart] = field(default_factory=list)

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "parts": [p.to_jsonable() for p in self.parts],
        }


@dataclass
class Task:
    id: str
    state: TaskState
    messages: list[TaskMessage] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "state": self.state.value,
            "messages": [m.to_jsonable() for m in self.messages],
            "metadata": dict(self.metadata),
        }
