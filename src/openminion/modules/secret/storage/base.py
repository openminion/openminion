from abc import ABC, abstractmethod


class SecretStore(ABC):
    """Abstract base for secret storage implementations."""

    @abstractmethod
    def upsert(
        self,
        *,
        key: str,
        namespace: str,
        value: str,
        created_at: float,
        updated_at: float,
    ) -> None: ...

    @abstractmethod
    def fetch_value(self, *, key: str, namespace: str) -> str | None: ...

    @abstractmethod
    def delete(self, *, key: str, namespace: str) -> None: ...

    @abstractmethod
    def list_keys(self, *, namespace: str) -> list[str]: ...

    @abstractmethod
    def close(self) -> None: ...
