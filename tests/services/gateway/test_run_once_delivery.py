from __future__ import annotations

import asyncio

from tests.services.gateway._gateway_service_support import GatewayServiceTestCase

CALLER_HANDLES_DELIVERY = "caller_handles_delivery"
# `openminion run` always sends an explicit session id; lock that contract too.
EXPLICIT_SESSION_ID = "run-delivery-session"


class RunOnceDeliveryAcknowledgementTestCase(GatewayServiceTestCase):
    """A ``deliver=False`` reply stays pending until the caller acknowledges delivery."""

    def test_deliver_false_without_caller_delivery_replays_pending_response(
        self,
    ) -> None:
        first = asyncio.run(
            self.gateway.run_once(
                channel="console",
                target="local-user",
                message="first",
                session_id=EXPLICIT_SESSION_ID,
                deliver=False,
            )
        )
        second = asyncio.run(
            self.gateway.run_once(
                channel="console",
                target="local-user",
                message="second",
                session_id=EXPLICIT_SESSION_ID,
                deliver=False,
            )
        )

        # No acknowledgement -> the pending outbound is replayed, no new turn.
        self.assertEqual(len(self.provider.requests), 1)
        self.assertEqual(second.body, first.body)
        self.assertEqual(second.metadata.get("session_id"), EXPLICIT_SESSION_ID)
        self.assertEqual(len(self.channel.sent), 0)

    def test_deliver_false_with_caller_delivery_acknowledges_and_runs_new_turn(
        self,
    ) -> None:
        meta = {CALLER_HANDLES_DELIVERY: "true"}
        first = asyncio.run(
            self.gateway.run_once(
                channel="console",
                target="local-user",
                message="first",
                session_id=EXPLICIT_SESSION_ID,
                deliver=False,
                inbound_metadata=meta,
            )
        )
        second = asyncio.run(
            self.gateway.run_once(
                channel="console",
                target="local-user",
                message="second",
                session_id=EXPLICIT_SESSION_ID,
                deliver=False,
                inbound_metadata=meta,
            )
        )

        # Acknowledged -> the second call runs a fresh turn carrying history.
        self.assertEqual(len(self.provider.requests), 2)
        self.assertNotEqual(second.body, first.body)
        self.assertEqual(len(self.provider.requests[1].history), 2)
        self.assertEqual(second.metadata.get("session_id"), EXPLICIT_SESSION_ID)
        self.assertEqual(len(self.channel.sent), 0)
