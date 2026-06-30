from abc import ABC, abstractmethod

from openminion.base.types import Message


class Channel(ABC):
    name = "channel"

    @abstractmethod
    def send(self, message: Message) -> None:
        """Deliver a message to the channel backend."""
