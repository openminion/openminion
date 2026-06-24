import re
from typing import Any

from openminion.modules.secret.service import SecretService


SECRET_PATTERN = re.compile(r"^\$SECRET:([a-zA-Z0-9_-]+)$")


class SecretConfigLoader:
    """Config loader that resolves $SECRET:key references."""

    def __init__(self, secret_service: SecretService) -> None:
        self._secret_service = secret_service

    async def resolve_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """Recursively resolve $SECRET: references in config."""
        return await self._resolve_mapping(config)

    async def _resolve_mapping(self, config: dict[str, Any]) -> dict[str, Any]:
        return {key: await self._resolve_value(value) for key, value in config.items()}

    async def _resolve_value(self, value: Any) -> Any:
        if isinstance(value, str):
            match = SECRET_PATTERN.match(value)
            if match:
                return await self._secret_service.get_secret(match.group(1))
            return value
        if isinstance(value, dict):
            return await self._resolve_mapping(value)
        if isinstance(value, list):
            return [await self._resolve_value(item) for item in value]
        return value

    @staticmethod
    def is_secret_ref(value: str) -> bool:
        """Check if a string is a secret reference."""
        return SECRET_PATTERN.match(value) is not None

    @staticmethod
    def extract_secret_key(value: str) -> str | None:
        """Extract the key from a secret reference."""
        match = SECRET_PATTERN.match(value)
        return match.group(1) if match else None
