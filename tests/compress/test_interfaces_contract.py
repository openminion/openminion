from __future__ import annotations

import pytest

from openminion.modules.context.compress.compaction import CompactionService
from openminion.modules.context.compress.interfaces import (
    COMPRESS_INTERFACE_VERSION,
    ensure_compress_component_compatibility,
)
from openminion.modules.context.compress.service import CompressionService


def test_compress_services_satisfy_contracts() -> None:
    svc = CompressionService()
    compaction = CompactionService()

    assert svc.contract_version == COMPRESS_INTERFACE_VERSION
    assert compaction.contract_version == COMPRESS_INTERFACE_VERSION

    ensure_compress_component_compatibility(svc, component_type="compression_service")
    ensure_compress_component_compatibility(
        compaction, component_type="compaction_service"
    )


def test_validator_rejects_incompatible_component() -> None:
    class _Broken:
        contract_version = "v1"

    with pytest.raises(TypeError):
        ensure_compress_component_compatibility(
            _Broken(), component_type="compression_service"
        )
