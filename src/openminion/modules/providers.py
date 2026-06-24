from __future__ import annotations

import re
from typing import Generic, TypeVar

T = TypeVar("T")


def _provider_id(value: str) -> str:
    provider_id = str(value).strip()
    if not provider_id:
        raise ValueError("Provider ID cannot be empty")
    return provider_id


class ModuleNotFoundError(Exception):
    pass


class ProviderNotFoundError(Exception):
    pass


class ContractVersionError(Exception):
    pass


class DuplicateProviderError(Exception):
    pass


def normalize_contract_version(version: str) -> str:
    version = str(version).strip().lower()
    if version.startswith("v"):
        version = version[1:]
    match = re.match(r"(\d+)", version)
    if not match:
        raise ContractVersionError(f"Invalid contract version format: {version}")
    return f"v{match.group(1)}"


def check_contract_version_compatibility(
    provided: str,
    expected: str,
    allow_higher: bool = False,
) -> bool:
    provided_norm = normalize_contract_version(provided)
    expected_norm = normalize_contract_version(expected)
    provided_major = int(provided_norm[1:])
    expected_major = int(expected_norm[1:])
    if provided_major == expected_major or (
        allow_higher and provided_major > expected_major
    ):
        return True

    raise ContractVersionError(
        f"Contract version mismatch: provided={provided} ({provided_norm}), "
        f"expected={expected} ({expected_norm})"
    )


class ModuleRegistry(Generic[T]):
    """Registry that rejects duplicate or unknown providers explicitly."""

    def __init__(self, expected_contract_version: str = "v1") -> None:
        self._providers: dict[str, T] = {}
        self._contract_versions: dict[str, str] = {}
        self._expected_version = expected_contract_version

    def register(
        self,
        provider_id: str,
        provider: T,
        contract_version: str = "v1",
    ) -> None:
        provider_id = _provider_id(provider_id)
        if provider_id in self._providers:
            raise DuplicateProviderError(
                f"Provider '{provider_id}' is already registered"
            )
        try:
            check_contract_version_compatibility(
                contract_version,
                self._expected_version,
                allow_higher=False,
            )
        except ContractVersionError as e:
            raise ContractVersionError(
                f"Cannot register provider '{provider_id}': {e}"
            ) from e

        self._providers[provider_id] = provider
        self._contract_versions[provider_id] = normalize_contract_version(
            contract_version
        )

    def get(self, provider_id: str) -> T:
        provider_id = _provider_id(provider_id)
        if provider_id not in self._providers:
            available = list(self._providers.keys())
            raise ProviderNotFoundError(
                f"Provider '{provider_id}' not found. "
                f"Available providers: {available if available else 'none'}"
            )

        return self._providers[provider_id]

    def is_registered(self, provider_id: str) -> bool:
        return str(provider_id).strip() in self._providers

    def list_providers(self) -> list[str]:
        return list(self._providers.keys())

    def get_contract_version(self, provider_id: str) -> str:
        provider_id = _provider_id(provider_id)
        if provider_id not in self._contract_versions:
            raise ProviderNotFoundError(f"Provider '{provider_id}' not found")
        return self._contract_versions[provider_id]
