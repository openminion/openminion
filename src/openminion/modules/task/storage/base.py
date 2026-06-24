from abc import ABC

from openminion.modules.storage.record_store import RecordStore


class TaskStore(ABC):
    """Abstract base for task storage implementations."""

    record_store: RecordStore
