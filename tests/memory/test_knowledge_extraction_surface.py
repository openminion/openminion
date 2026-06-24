from __future__ import annotations

from openminion.modules.memory.audit.trust_gate import TrustGateEvent
from openminion.modules.memory.models import MemoryRecord
from openminion.modules.memory.portability.models import MemoryBundleSnapshot
from openminion.modules.memory.storage.audit import MemoryAuditEvent
from openminion.modules.memory.storage.base import (
    CandidateListOptions,
    ListQueryOptions,
)
from openminion.modules.memory.trust.types import TrustScore


def test_openminion_wrappers_now_resolve_to_knowledge_owners() -> None:
    import sophiagraph.models.core as _sg_core

    assert MemoryRecord.__module__ == "openminion.modules.memory.models"
    assert issubclass(MemoryRecord, _sg_core.MemoryRecord)
    assert "goal_id" in MemoryRecord.__dataclass_fields__, (
        "openminion MemoryRecord wrapper must retain `goal_id`; if the "
        "field migrates upstream this test should flip to assert "
        "MemoryRecord.__module__ == 'sophiagraph.models.core'."
    )
    assert ListQueryOptions.__module__ == "sophiagraph.query.options"
    assert CandidateListOptions.__module__ == "sophiagraph.query.options"
    assert TrustScore.__module__ == "sophiagraph.trust.types"
    assert MemoryBundleSnapshot.__module__ == "sophiagraph.portability.models"
    assert MemoryAuditEvent.__module__ == "sophiagraph.audit.events"
    assert TrustGateEvent.__module__ == "sophiagraph.audit.events"
