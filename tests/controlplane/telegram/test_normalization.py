from __future__ import annotations

import pytest

from openminion.modules.controlplane.contracts.inbound import (
    canonicalize_inbound_message,
)
from openminion.modules.controlplane.contracts.models import InboundMessage
from openminion.modules.controlplane.channels.telegram.command_aliases import (
    normalize_command_aliases,
)
from openminion.modules.controlplane.channels.telegram.normalization import (
    extract_envelope,
    to_control_event,
    to_inbound_message,
)


def test_extract_message_update_with_topic() -> None:
    raw = {
        "update_id": 11,
        "message": {
            "message_id": 22,
            "message_thread_id": 42,
            "text": "hello",
            "document": {"file_id": "doc-1"},
            "chat": {"id": -100123, "type": "supergroup"},
            "from": {"id": 7, "username": "alice", "first_name": "Alice"},
        },
    }
    env = extract_envelope(raw)
    assert env is not None
    assert env.chat_id == -100123
    assert env.topic_id == 42
    assert env.from_user.id == 7
    assert env.from_user.username == "alice"

    event = to_control_event(env)
    assert event.channel == "telegram"
    assert event.conversation_id == "-100123"
    assert event.thread_id == "42"
    assert event.metadata["raw_type"] == "message"
    assert len(event.attachments) == 1
    assert event.attachments[0]["kind"] == "document"
    assert event.attachments[0]["ref"] == "tgfile:doc-1"


def test_extract_callback_query_update() -> None:
    raw = {
        "update_id": 12,
        "callback_query": {
            "id": "cq-1",
            "data": "clicked",
            "from": {"id": 8, "username": "bob"},
            "message": {
                "message_id": 33,
                "chat": {"id": -500, "type": "group"},
            },
        },
    }
    env = extract_envelope(raw)
    assert env is not None
    assert env.raw_type == "callback_query"
    assert env.callback_query_id == "cq-1"
    assert env.text == "clicked"


def test_command_alias_normalization() -> None:
    assert normalize_command_aliases("/start", bot_username="mybot") == "/help"
    assert (
        normalize_command_aliases("/start abc123", bot_username="mybot")
        == "/start abc123"
    )
    assert normalize_command_aliases("/new", bot_username="mybot") == "/session new"
    assert normalize_command_aliases("/status", bot_username="mybot") == "/status"
    assert normalize_command_aliases("/pair", bot_username="mybot") == "/pair"
    assert normalize_command_aliases("/diag", bot_username="mybot") == "/diag"
    assert (
        normalize_command_aliases("/run status abc", bot_username="mybot") == "/job ls"
    )
    assert (
        normalize_command_aliases("/cancel abc", bot_username="mybot")
        == "/profile stop"
    )
    assert normalize_command_aliases("/stop", bot_username="mybot") == "/profile stop"
    assert (
        normalize_command_aliases("/agent researcher", bot_username="mybot")
        == "/profile use researcher"
    )
    assert (
        normalize_command_aliases("/profile use minimax-m2-5", bot_username="mybot")
        == "/profile use minimax-m2-5"
    )
    assert (
        normalize_command_aliases("/profile current", bot_username="mybot")
        == "/profile"
    )
    assert (
        normalize_command_aliases("/profile list", bot_username="mybot")
        == "/profile ls"
    )
    assert normalize_command_aliases("/help@mybot", bot_username="mybot") == "/help"
    assert (
        normalize_command_aliases("/help@otherbot", bot_username="mybot")
        == "/help@otherbot"
    )


def test_to_inbound_message_uses_user_scoped_identity_in_group_chat() -> None:
    raw = {
        "update_id": 101,
        "message": {
            "message_id": 77,
            "text": "hello",
            "chat": {"id": -100900, "type": "supergroup"},
            "from": {"id": 42, "username": "group_user"},
        },
    }
    env = extract_envelope(raw)
    assert env is not None
    event = to_control_event(env)
    inbound = to_inbound_message(env, normalized_text=env.text, control_event=event)

    assert inbound.user_key == "telegram:42"
    assert inbound.chat_key == "telegram:-100900"
    assert inbound.user_key != inbound.chat_key
    assert inbound.chat_id == "-100900"
    assert inbound.user_id == "42"
    assert inbound.thread_id is None


def test_to_inbound_message_keeps_stable_keys_in_dm() -> None:
    raw = {
        "update_id": 102,
        "message": {
            "message_id": 88,
            "text": "hello",
            "chat": {"id": 55, "type": "private"},
            "from": {"id": 55, "username": "dm_user"},
        },
    }
    env = extract_envelope(raw)
    assert env is not None
    event = to_control_event(env)
    inbound = to_inbound_message(env, normalized_text=env.text, control_event=event)

    assert inbound.user_key == "telegram:55"
    assert inbound.chat_key == "telegram:55"
    assert inbound.chat_id == "55"
    assert inbound.user_id == "55"
    assert inbound.thread_id is None


def test_to_inbound_message_sets_thread_id_for_topic_messages() -> None:
    raw = {
        "update_id": 103,
        "message": {
            "message_id": 90,
            "message_thread_id": 77,
            "text": "topic message",
            "chat": {"id": -1001, "type": "supergroup"},
            "from": {"id": 999, "username": "topic_user"},
        },
    }
    env = extract_envelope(raw)
    assert env is not None
    event = to_control_event(env)
    inbound = to_inbound_message(env, normalized_text=env.text, control_event=event)

    assert inbound.chat_id == "-1001"
    assert inbound.user_id == "999"
    assert inbound.thread_id == "77"


def test_to_inbound_message_populates_canonical_metadata_and_alias() -> None:
    raw = {
        "update_id": 104,
        "message": {
            "message_id": 91,
            "text": "hello metadata",
            "chat": {"id": 77, "type": "private"},
            "from": {"id": 22, "username": "meta_user"},
        },
    }
    env = extract_envelope(raw)
    assert env is not None
    event = to_control_event(env)
    inbound = to_inbound_message(
        env,
        normalized_text=env.text,
        control_event=event,
        extra_meta={"trace_id": "trace-xyz"},
    )

    assert inbound.metadata["trace_id"] == "trace-xyz"
    assert inbound.metadata["telegram"]["chat_id"] == 77
    assert inbound.metadata["control_event"]["message_id"] == "91"
    assert inbound.meta == inbound.metadata


def test_inbound_alias_bridge_backfills_metadata_and_thread_id() -> None:
    legacy = InboundMessage(
        user_key="telegram:22",
        chat_key="telegram:77",
        text="legacy payload",
        channel="telegram",
        thread_key="telegram-topic:88",
        meta={"trace_id": "trace-legacy"},
    )
    with pytest.warns(DeprecationWarning):
        normalized = canonicalize_inbound_message(legacy)
    assert normalized.metadata["trace_id"] == "trace-legacy"
    assert normalized.meta["trace_id"] == "trace-legacy"
    assert normalized.thread_id == "88"
    assert normalized.thread_key == "telegram-topic:88"
