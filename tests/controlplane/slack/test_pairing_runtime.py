from types import SimpleNamespace

from openminion.modules.controlplane.channels.slack.config import SlackChannelConfig
from openminion.modules.controlplane.channels.slack.pairing_adapter import (
    SlackPairingAdapter,
)
from openminion.modules.controlplane.channels.slack.runtime.helpers import (
    process_slash_command,
)
from openminion.modules.controlplane.channels.slack.slash_commands import (
    parse_slash_payload,
)
from openminion.modules.controlplane.pairing import (
    ControlPlanePairingService,
    ControlPlanePairingStore,
    PairingPolicy,
)
from openminion.modules.controlplane.runtime.store import InMemoryControlPlaneStore


class _Delivery:
    def __init__(self) -> None:
        self.calls: list[tuple[dict, object]] = []

    def deliver(self, payload, target):
        self.calls.append((payload, target))
        return SimpleNamespace(ok=True)


class _Runtime:
    def handle_inbound(self, _inbound):
        raise AssertionError("pairing attempt should not reach normal dispatch")


def test_slack_slash_pair_redeems_shared_token_before_dispatch() -> None:
    store = InMemoryControlPlaneStore()
    pairing = ControlPlanePairingService(
        policy=PairingPolicy(
            token_ttl_seconds=60,
            default_scopes=["cp.message.read", "cp.message.write"],
        ),
        store=ControlPlanePairingStore(store),
        adapter=SlackPairingAdapter(),
        bridge_store=store,
    )
    issued = pairing.issue_token(
        expected_account_id="slack:T1:user:U1",
        expected_chat_key="slack:T1:channel:C1",
        token_ttl_seconds=60,
        scopes=["cp.message.read", "cp.message.write"],
        token="tok123",
    )
    delivery = _Delivery()
    runner = SimpleNamespace(
        _audit_logger=None,
        _config=SlackChannelConfig(),
        _delivery=delivery,
        _pairing=pairing,
        _runtime=_Runtime(),
        _state_store=None,
        _store=store,
    )
    envelope = parse_slash_payload(
        {
            "team_id": "T1",
            "channel_id": "C1",
            "channel_name": "general",
            "user_id": "U1",
            "command": "/openminion",
            "text": f"pair {issued.token}",
        }
    )

    result = process_slash_command(runner, envelope)

    assert result == {"ok": True, "status": "pairing_handled"}
    assert delivery.calls[0][0]["text"] == "Paired."
    pairing_row = store.get_pairing(channel="slack", chat_id="C1")
    assert pairing_row is not None
    assert pairing_row["scopes"] == ["cp.message.read", "cp.message.write"]
