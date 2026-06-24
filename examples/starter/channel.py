from __future__ import annotations

from datetime import timezone

from openminion.base.channel import Channel
from openminion.base.types import Message


class HelloChannel(Channel):
    name = "hello"

    def send(self, message: Message) -> None:
        timestamp = message.timestamp.astimezone(timezone.utc).isoformat()
        print(
            f"[{timestamp}] [hello-channel] target={message.target} id={message.id} body={message.body}"
        )
