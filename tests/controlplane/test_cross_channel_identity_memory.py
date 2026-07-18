from __future__ import annotations

from openminion.modules.controlplane.runtime.identity import (
    StoreBackedIdentityAPI,
    bind_channel_identity_memory,
    resolve_channel_identity_memory_provenance,
    unpair_channel_identity_memory,
)


class _IdentityStore:
    def __init__(self) -> None:
        self.records: dict[tuple[str, str], dict[str, object]] = {}

    def bind_principal_subject(self, **kwargs: object) -> None:
        key = (str(kwargs["channel"]), str(kwargs["subject_id"]))
        self.records[key] = {
            "principal_id": kwargs["principal_id"],
            "channel": kwargs["channel"],
            "subject_id": kwargs["subject_id"],
            "status": kwargs.get("status", "active"),
            "scopes": list(kwargs.get("scopes") or []),
            "note": kwargs.get("note"),
            "meta": dict(kwargs.get("meta") or {}),
        }

    def resolve_principal(self, *, channel: str, subject_id: str) -> str | None:
        record = self.records.get((channel, subject_id))
        return str(record["principal_id"]) if record else None

    def get_channel_subject(self, *, channel: str, subject_id: str) -> dict[str, object] | None:
        return self.records.get((channel, subject_id))


def test_paired_channels_share_namespace_with_typed_provenance() -> None:
    api = StoreBackedIdentityAPI(_IdentityStore())
    cli = bind_channel_identity_memory(
        api,
        pairing_id="principal-1",
        channel="cli",
        channel_account_id="alice",
        channel_thread_id="local",
        participant_kind="human",
    )
    slack = bind_channel_identity_memory(
        api,
        pairing_id="principal-1",
        channel="slack",
        channel_account_id="U123",
        channel_thread_id="C999",
        participant_kind="human",
    )

    assert cli.namespace_id == slack.namespace_id
    assert cli.to_dict()["pairing_id"] == "principal-1"
    assert slack.to_dict()["channel"] == "slack"
    assert slack.to_dict()["participant_kind"] == "human"


def test_unpaired_and_same_name_human_agent_identities_remain_isolated() -> None:
    api = StoreBackedIdentityAPI(_IdentityStore())
    human = resolve_channel_identity_memory_provenance(
        api,
        channel="slack",
        channel_account_id="alex",
        channel_thread_id="room",
        participant_kind="human",
    )
    agent = resolve_channel_identity_memory_provenance(
        api,
        channel="slack",
        channel_account_id="alex",
        channel_thread_id="room",
        participant_kind="agent",
    )

    assert human.paired is False
    assert agent.paired is False
    assert human.namespace_id != agent.namespace_id


def test_unpair_rolls_back_to_channel_isolation() -> None:
    api = StoreBackedIdentityAPI(_IdentityStore())
    paired = bind_channel_identity_memory(
        api,
        pairing_id="principal-1",
        channel="telegram",
        channel_account_id="42",
        channel_thread_id="dm",
        participant_kind="human",
    )
    unpair_channel_identity_memory(
        api,
        channel="telegram",
        channel_account_id="42",
        channel_thread_id="dm",
        participant_kind="human",
    )
    isolated = resolve_channel_identity_memory_provenance(
        api,
        channel="telegram",
        channel_account_id="42",
        channel_thread_id="dm",
        participant_kind="human",
    )

    assert paired.paired is True
    assert isolated.paired is False
    assert isolated.namespace_id != paired.namespace_id
