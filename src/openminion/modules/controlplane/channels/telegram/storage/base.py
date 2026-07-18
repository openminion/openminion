from abc import ABC, abstractmethod

from ..models import PairConsumeResult, PairTokenIssue


class TelegramPollStateStoreBase(ABC):
    """Abstract base for Telegram poll-state storage implementations."""

    @abstractmethod
    def get_last_update_id(self, account_id: str) -> int: ...

    @abstractmethod
    def set_last_update_id(self, account_id: str, update_id: int) -> None: ...

    @abstractmethod
    def issue_pair_token(
        self,
        *,
        token: str | None,
        token_ttl_seconds: int,
        scopes: list[str],
        expected_user_id: int | None,
        expected_chat_id: int | None,
        hash_pepper: str | None,
    ) -> PairTokenIssue: ...

    @abstractmethod
    def consume_pair_token(
        self,
        *,
        token: str,
        user_id: int,
        chat_id: int,
        topic_id: int | None,
        hash_pepper: str | None,
    ) -> PairConsumeResult: ...

    @abstractmethod
    def record_pair_attempt(
        self,
        *,
        token: str,
        user_id: int,
        chat_id: int,
        outcome: str,
        hash_pepper: str | None,
    ) -> None: ...

    @abstractmethod
    def count_recent_attempts_for_user(
        self, *, user_id: int, window_seconds: int
    ) -> int: ...

    @abstractmethod
    def count_recent_attempts_for_chat(
        self, *, chat_id: int, window_seconds: int
    ) -> int: ...

    def iter_pair_tokens(self) -> list[dict[str, object]]: ...

    def iter_pair_attempts(self) -> list[dict[str, object]]: ...

    @abstractmethod
    def close(self) -> None: ...
