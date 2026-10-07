from threading import Event
from typing import Any, assert_type

from pydantic import BaseModel

from openminion import Agent, AgentRunResult
from openminion.api import RuntimeTurnHandle, TurnChunk, TurnResponse


class Review(BaseModel):
    decision: str


structured: Agent[str, Review] = Agent(output_type=Review)


def approve(_tool: str, _arguments: dict[str, Any], _approval_id: str) -> bool:
    return True


structured_result = structured.run(
    "review",
    timeout_seconds=30,
    approval_callback=approve,
    cancel_event=Event(),
)
assert_type(structured_result, AgentRunResult[Review])
assert_type(structured_result.output, Review)
assert_type(structured_result.id, str | None)
assert_type(structured_result.metadata, dict[str, Any])
assert_type(structured_result.stats, dict[str, Any])

plain: Agent[str, str] = Agent()
assert_type(plain.run("hello").output, str)


def inspect_submitted_turn(handle: RuntimeTurnHandle) -> None:
    for chunk in handle.stream():
        assert_type(chunk, TurnChunk)
    assert_type(handle.result(), TurnResponse)
    assert_type(handle.cancel(), bool)
    assert_type(
        handle.resolve_approval(approval_id="approval-1", decision="deny"),
        bool,
    )
