from typing import Final, Literal

REACTIONS_SET_TOOL = "reactions.set"
REACTIONS_LIST_TOOL = "reactions.list"
REACTION_CHANNEL_SIGNAL: Final[Literal["signal"]] = "signal"
REACTION_CHANNEL_WHATSAPP: Final[Literal["whatsapp"]] = "whatsapp"
REACTION_ACTION_ADDED: Final[Literal["added"]] = "added"
REACTION_ACTION_REMOVED_ONE: Final[Literal["removed_one"]] = "removed_one"
REACTION_ACTION_REMOVED_ALL_BOT: Final[Literal["removed_all_bot"]] = "removed_all_bot"
REACTION_ACTION_NOOP: Final[Literal["noop"]] = "noop"
REACTION_LIST_SCOPE_BOT_ONLY: Final[Literal["bot_only"]] = "bot_only"
REACTION_LIST_SCOPE_ALL: Final[Literal["all"]] = "all"
REMOVE_ALL_WITH_EMPTY_EMOJI_CHANNELS: frozenset[str] = frozenset(
    {"discord", "slack", "google_chat", "telegram", "whatsapp"}
)
REMOVE_SPECIFIC_EMOJI_CHANNELS: frozenset[str] = frozenset(
    {"discord", "slack", "google_chat", "telegram", "whatsapp", "zalo_personal"}
)
REQUIRE_NON_EMPTY_EMOJI_CHANNELS: frozenset[str] = frozenset({"zalo_personal"})

__all__ = [
    "REACTION_ACTION_ADDED",
    "REACTION_ACTION_NOOP",
    "REACTION_ACTION_REMOVED_ALL_BOT",
    "REACTION_ACTION_REMOVED_ONE",
    "REACTION_CHANNEL_SIGNAL",
    "REACTION_CHANNEL_WHATSAPP",
    "REACTION_LIST_SCOPE_ALL",
    "REACTION_LIST_SCOPE_BOT_ONLY",
    "REACTIONS_LIST_TOOL",
    "REACTIONS_SET_TOOL",
    "REMOVE_ALL_WITH_EMPTY_EMOJI_CHANNELS",
    "REMOVE_SPECIFIC_EMOJI_CHANNELS",
    "REQUIRE_NON_EMPTY_EMOJI_CHANNELS",
]
