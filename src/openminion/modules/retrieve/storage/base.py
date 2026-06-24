from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod
from typing import Any, Mapping, Sequence


class RetrieveStore(ABC):
    """Abstract base for retrieve storage implementations."""

    @abstractmethod
    def execute(
        self, sql: str, params: Sequence[Any] | tuple[Any, ...] = ()
    ) -> sqlite3.Cursor: ...

    @abstractmethod
    def fetchone(
        self, sql: str, params: Sequence[Any] | tuple[Any, ...] = ()
    ) -> sqlite3.Row | None: ...

    @abstractmethod
    def fetchall(
        self, sql: str, params: Sequence[Any] | tuple[Any, ...] = ()
    ) -> list[sqlite3.Row]: ...

    @abstractmethod
    def commit(self) -> None: ...

    @abstractmethod
    def ensure_schema(self) -> bool: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def get_feedback_state(
        self, unit_ids: Sequence[str]
    ) -> dict[str, dict[str, Any]]: ...

    @abstractmethod
    def record_hits(self, unit_ids: Sequence[str], *, observed_at: str) -> int: ...

    @abstractmethod
    def set_feedback_scores(self, scores_by_unit: Mapping[str, float]) -> int: ...

    @abstractmethod
    def apply_feedback_decay(
        self,
        *,
        halflife_days: int,
        min_feedback_score: float,
    ) -> int: ...
