from __future__ import annotations

import os

from openminion.modules.controlplane.channels.telegram.config import load_config


def test_load_config_from_channels_root_with_env_token(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc123")
    cfg = load_config(
        {
            "channels": {
                "telegram": {
                    "enabled": True,
                    "botToken": "${TELEGRAM_BOT_TOKEN}",
                    "mode": "polling",
                    "allowedUpdates": ["message", "callback_query"],
                    "polling": {
                        "timeoutSeconds": 25,
                        "limit": 99,
                        "backoff_s": [1, 3, 5],
                        "persistOffset": True,
                        "dropPendingOnStart": True,
                    },
                    "access": {
                        "dmPolicy": "allowlist",
                        "allowFromUserIds": [123],
                        "groupPolicy": "allowlist",
                        "allowGroupChatIds": [-1001],
                        "mentionOnlyInGroups": False,
                        "allowedTopicIdsByChat": {"-1001": [42]},
                    },
                    "pairing": {
                        "mode": "required",
                        "code_len": 8,
                        "code_ttl_s": 420,
                        "pending_cap_per_channel": 4,
                        "attemptWindowSeconds": 30,
                        "maxAttemptsPerUser": 4,
                        "maxAttemptsPerChat": 9,
                        "hashPepper": "${TELEGRAM_BOT_TOKEN}",
                        "allowInGroups": False,
                        "defaultScopes": ["chat.interact", "tool.weather.read"],
                    },
                    "reply": {"mode": "reply_to_thread"},
                    "delivery": {
                        "parseMode": "MarkdownV2",
                        "linkPreview": False,
                        "chunkLimit": 3000,
                        "retry": {"maxAttempts": 5, "backoffMs": [100, 200]},
                    },
                }
            }
        }
    ).telegram

    assert cfg.enabled is True
    assert cfg.bot_token == "abc123"
    assert cfg.polling.timeout_seconds == 25
    assert cfg.polling.limit == 99
    assert cfg.polling.backoff_seconds == [1, 3, 5]
    assert cfg.polling.drop_pending_on_start is True
    assert cfg.access.dm_policy == "allowlist"
    assert cfg.access.allow_from_user_ids == [123]
    assert cfg.access.allow_group_chat_ids == [-1001]
    assert cfg.access.allowed_topic_ids_by_chat["-1001"] == [42]
    assert cfg.pairing.mode == "required"
    assert cfg.pairing.code_length == 8
    assert cfg.pairing.pending_cap_per_channel == 4
    assert cfg.pairing.token_ttl_seconds == 420
    assert cfg.pairing.attempt_window_seconds == 30
    assert cfg.pairing.max_attempts_per_user == 4
    assert cfg.pairing.max_attempts_per_chat == 9
    assert cfg.pairing.hash_pepper == "abc123"
    assert cfg.pairing.default_scopes == ["chat.interact", "tool.weather.read"]
    assert cfg.reply.mode == "reply_to_thread"
    assert cfg.delivery.parse_mode == "MarkdownV2"
    assert cfg.delivery.link_preview is False
    assert cfg.delivery.chunk_limit == 3000
    assert cfg.delivery.retry.max_attempts == 5
    assert cfg.delivery.retry.backoff_ms == [100, 200]


def test_load_config_defaults_without_source() -> None:
    cfg = load_config().telegram
    assert cfg.enabled is False
    assert cfg.mode == "polling"
    assert cfg.allowed_updates == ["message", "edited_message", "callback_query"]
    assert cfg.polling.backoff_seconds == [1, 2, 4, 8, 16]
    assert cfg.access.dm_policy == "allowlist"
    assert cfg.access.group_policy == "deny"
    assert cfg.pairing.enabled is True
    assert cfg.pairing.mode == "required"
    assert cfg.pairing.token_ttl_seconds == 600


def test_unset_env_secret_resolves_to_empty(monkeypatch) -> None:
    monkeypatch.delenv("MISSING_TOKEN", raising=False)
    cfg = load_config(
        {"channels": {"telegram": {"botToken": "${MISSING_TOKEN}"}}}
    ).telegram
    assert cfg.bot_token == ""
    assert os.getenv("MISSING_TOKEN") is None


def test_load_config_supports_snake_case_polling_schema() -> None:
    cfg = load_config(
        {
            "channels": {
                "telegram": {
                    "enabled": True,
                    "bot_token": "token",
                    "polling": {
                        "timeout_s": 12,
                        "allowed_updates": ["message", "callback_query"],
                        "backoff_s": [2, 4, 8],
                    },
                    "groups": {"enabled": False, "require_mention": True},
                }
            }
        }
    ).telegram

    assert cfg.bot_token == "token"
    assert cfg.polling.timeout_seconds == 12
    assert cfg.allowed_updates == ["message", "callback_query"]
    assert cfg.polling.backoff_seconds == [2, 4, 8]
    assert cfg.access.group_policy == "deny"
    assert cfg.access.mention_only_in_groups is True


def test_load_config_includes_clarify_settings() -> None:
    cfg = load_config(
        {
            "channels": {
                "telegram": {
                    "enabled": True,
                    "bot_token": "token",
                    "clarify": {
                        "enabled": True,
                        "mode": "reply",
                        "maxQuestionsPerMessage": 3,
                        "answerPrefix": "/clarify",
                    },
                }
            }
        }
    ).telegram

    assert cfg.clarify.enabled is True
    assert cfg.clarify.mode == "reply"
    assert cfg.clarify.max_questions_per_message == 3
    assert cfg.clarify.answer_prefix == "/clarify"
