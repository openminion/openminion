from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import os
from unittest.mock import patch

from openminion.modules.context.memory_client import ContextMemoryClientAdapter
from openminion.modules.memory.models import MemoryRecord
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter

from tests.services.gateway._gateway_service_support import (
    GatewayServiceTestCase,
    _CaptureProvider,
)


class ContextCtlMemorySelectionCreditTests(GatewayServiceTestCase):
    def test_real_gateway_credits_each_returned_pack_once(self) -> None:
        store = SQLiteMemoryStore(self.database_path.parent / "selection-memory.db")
        memory_service = MemoryService(store=store)
        now = datetime.now(timezone.utc).isoformat()
        candidate_ids = []
        for index in range(8):
            candidate_ids.append(
                store.put(
                    MemoryRecord(
                        id=f"cobalt-memory-{index}",
                        scope="agent:main",
                        type="fact",
                        title=f"Cobalt marker {index}",
                        content={"text": f"Cobalt {index} " + ("x" * 500)},
                        confidence=0.9,
                        meta={"outcome_success_count": 2, "feedback_score": 0.25},
                        created_at=now,
                        updated_at=now,
                    )
                )
            )
        unselected_id = store.put(
            MemoryRecord(
                id="other-agent-memory",
                scope="agent:other",
                type="fact",
                title="Other marker",
                content={"text": "The amber marker is enabled."},
                confidence=0.8,
                created_at=now,
                updated_at=now,
            )
        )
        gateway_memory = MemoryServiceGatewayAdapter(memory_service, agent_id="main")
        retrieved_ids = {
            fact.record_id
            for fact in ContextMemoryClientAdapter(gateway_memory).query_facts(
                session_id="selection-user",
                agent_id="main",
                query="cobalt",
                limit=20,
            )
        }
        assert retrieved_ids == set(candidate_ids)
        before = {record_id: store.get(record_id) for record_id in candidate_ids}
        assert all(record is not None for record in before.values())
        provider = _CaptureProvider()

        with patch.dict(
            os.environ,
            {
                "CONTEXTCTL_GATEWAY_ENABLED": "true",
                "OPENMINION_IDENTITY_ROOT": str(self.database_path.parent / "identity"),
            },
            clear=False,
        ):
            gateway, _sink = self._build_gateway(
                provider=provider,
                logger_name="openminion.tests.context-selection-credit",
                agent_logger_name="openminion.tests.context-selection-credit.agent",
                agent_memory=gateway_memory,
                record_context_selection=gateway_memory.record_context_selection,
            )
            try:
                for _ in range(2):
                    asyncio.run(
                        gateway.run_once(
                            channel="console",
                            target="selection-user",
                            message="cobalt",
                            inbound_metadata={"attach_id": "selection-credit"},
                        )
                    )
            finally:
                gateway.close()

        unselected = store.get(unselected_id)
        assert unselected is not None
        after = {record_id: store.get(record_id) for record_id in candidate_ids}
        assert all(record is not None for record in after.values())
        credited_ids = {
            record_id
            for record_id, record in after.items()
            if record is not None and record.access_count == 2
        }
        dropped_ids = {
            record_id
            for record_id, record in after.items()
            if record is not None and record.access_count == 0
        }
        assert credited_ids
        assert dropped_ids
        assert credited_ids | dropped_ids == retrieved_ids
        for record_id in candidate_ids:
            previous = before[record_id]
            current = after[record_id]
            assert previous is not None
            assert current is not None
            assert current.confidence == previous.confidence
            assert current.meta == previous.meta
            assert current.tier == previous.tier
            if record_id in credited_ids:
                assert current.last_hit_at
            else:
                assert current.last_hit_at == previous.last_hit_at
        assert unselected.access_count == 0
        memory_service.close()
