from openminion.base.types import Message
from openminion.services.context.budget import (
    ContextBudgetConfig,
    assemble_budgeted_context,
)


def _message(role: str, body: str) -> Message:
    return Message(
        channel="console",
        target="focus",
        body=body,
        metadata={"role": role},
    )


def _total_chars(messages: list[Message]) -> int:
    return sum(
        len(str(message.body or "")) + len(str(message.metadata or ""))
        for message in messages
    )


def test_recent_large_assistant_artifact_is_compacted_not_dropped() -> None:
    large_code = (
        "def get_weather(city: str) -> str:\n"
        "    return f'weather for {city}'\n"
        + ("# implementation detail\n" * 220)
        + "if __name__ == '__main__':\n"
        "    print(get_weather('Tokyo'))\n"
    )
    history = [
        _message("inbound", "Please write a Python weather helper."),
        _message("outbound", large_code),
        _message("inbound", "Can you write files?"),
        _message("outbound", "Yes, I can write files."),
    ]

    budgeted = assemble_budgeted_context(
        system_messages=[],
        history_messages=history,
        budget=ContextBudgetConfig(max_tokens=320, chars_per_token=1.0),
    )

    assert _total_chars(budgeted.messages) <= 320
    assistant_messages = [
        message
        for message in budgeted.messages
        if message.metadata.get("role") == "outbound"
    ]
    assert len(assistant_messages) == 2
    compacted = assistant_messages[0].body
    assert "[context budget compacted message:" in compacted
    assert "def get_weather" in compacted
    assert "print(get_weather('Tokyo'))" in compacted
    assert "Yes, I can write files." in assistant_messages[1].body
    assert budgeted.telemetry.overflow is False


def test_budgeter_prefers_newest_history_when_system_context_uses_budget() -> None:
    system = [_message("system", "S" * 180)]
    history = [
        _message("inbound", "old question " + ("x" * 300)),
        _message("outbound", "old answer " + ("y" * 300)),
        _message("inbound", "new question"),
        _message("outbound", "new answer"),
    ]

    budgeted = assemble_budgeted_context(
        system_messages=system,
        history_messages=history,
        budget=ContextBudgetConfig(max_tokens=260, chars_per_token=1.0),
    )

    bodies = [message.body for message in budgeted.messages]
    assert bodies[0] == system[0].body
    assert "new question" in bodies
    assert "new answer" in bodies
    assert all("old question" not in body for body in bodies)
    assert _total_chars(budgeted.messages) <= 260
