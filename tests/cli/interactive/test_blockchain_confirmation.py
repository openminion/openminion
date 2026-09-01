from openminion.modules.brain.loop.tools.confirmation import (
    confirmation_required_user_message,
)
from openminion.modules.brain.schemas import ToolCommand


def test_focus_and_terminal_shared_renderer_has_no_blockchain_session_choice() -> None:
    command = ToolCommand(
        kind="tool",
        title="Send transaction",
        tool_name="blockchain.send_transaction",
        args={"preparation_digest": "sha256:" + "1" * 64},
        inputs={},
    )

    rendered = confirmation_required_user_message(command)

    assert rendered.splitlines()[-1] == (
        "Reply exactly yes to allow once, or no to cancel."
    )
