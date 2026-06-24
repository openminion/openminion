"""Protocol surface for the sample module example."""

from __future__ import annotations

from typing import Any, Protocol

SAMPLE_INTERFACE_VERSION = "v1"


class SampleService(Protocol):
    contract_version: str

    def healthcheck(self) -> dict[str, Any]: ...

    def process(self, data: str) -> dict[str, Any]: ...

    def close(self) -> None: ...
