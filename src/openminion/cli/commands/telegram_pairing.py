from __future__ import annotations

import shlex
from collections.abc import Sequence
from typing import Protocol


class PairTokenOutputLike(Protocol):
    token: str
    token_hint: str
    token_hash_prefix: str
    expires_at_iso: str
    scopes: Sequence[str]
    deep_link: str | None


class TelegramCandidateLike(Protocol):
    user_id: int
    chat_id: int
    chat_type: str
    username: str
    display_name: str


def telegram_command(action: str, config_path: str | None) -> str:
    command = f"openminion channel telegram {action}"
    if config_path:
        command += " --config " + shlex.quote(str(config_path))
    return command


def print_pair_token_output(
    output: PairTokenOutputLike, *, config_path: str | None = None
) -> None:
    print("Pairing token created.")
    print("1. Make sure the Telegram listener is running:")
    print(telegram_command("run", config_path))
    if output.deep_link:
        print("2. Open this link in Telegram:")
        print(output.deep_link)
        print("If Telegram does not open, send this message to the bot:")
    else:
        print("2. Send this message to the bot:")
    print(f"/start {output.token}")
    print("3. Wait for the Paired confirmation, then send /status or a message.")
    print(
        "Security note: this chat can use OpenMinion control commands on this "
        "computer. Fine-grained ACL is not available yet."
    )
    print("Automation details:")
    print(f"PAIR_TOKEN={output.token}")
    print(f"PAIR_TOKEN_HINT={output.token_hint}")
    print(f"PAIR_TOKEN_HASH_PREFIX={output.token_hash_prefix}")
    print(f"PAIR_EXPIRES_AT={output.expires_at_iso}")
    print(f"PAIR_SCOPES={','.join(output.scopes)}")
    if output.deep_link:
        print(f"PAIR_DEEP_LINK={output.deep_link}")


def print_candidate(
    candidate: TelegramCandidateLike, *, config_path: str | None = None
) -> None:
    print("Telegram candidate found:")
    print(f"  user_id: {candidate.user_id}")
    print(f"  chat_id: {candidate.chat_id}")
    print(f"  chat_type: {candidate.chat_type}")
    if candidate.username:
        print(f"  username: @{candidate.username}")
    if candidate.display_name:
        print(f"  display_name: {candidate.display_name}")
    if config_path is not None:
        print("Copy and run:")
        print(
            telegram_pair_command(
                config_path=config_path,
                user_id=candidate.user_id,
                chat_id=candidate.chat_id,
            )
        )


def print_pair_missing_ids_hint(config_path: str | None) -> None:
    print("Pairing needs Telegram IDs first.")
    print("Recommended: " + telegram_command("pair", config_path) + " --wait")
    print("Advanced: " + telegram_command("identify", config_path))


def telegram_pair_command(
    *, config_path: str | None, user_id: int | str, chat_id: int | str
) -> str:
    return (
        telegram_command("pair", config_path)
        + f" --user-id {user_id} --chat-id {chat_id}"
    )
