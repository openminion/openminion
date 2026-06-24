REACTIONS_SET_TOOL = "reactions.set"
REACTIONS_LIST_TOOL = "reactions.list"
REMOVE_ALL_WITH_EMPTY_EMOJI_CHANNELS: frozenset[str] = frozenset(
    {"discord", "slack", "google_chat", "telegram", "whatsapp"}
)
REMOVE_SPECIFIC_EMOJI_CHANNELS: frozenset[str] = frozenset(
    {"discord", "slack", "google_chat", "telegram", "whatsapp", "zalo_personal"}
)
REQUIRE_NON_EMPTY_EMOJI_CHANNELS: frozenset[str] = frozenset({"zalo_personal"})

__all__ = [
    "REACTIONS_LIST_TOOL",
    "REACTIONS_SET_TOOL",
    "REMOVE_ALL_WITH_EMPTY_EMOJI_CHANNELS",
    "REMOVE_SPECIFIC_EMOJI_CHANNELS",
    "REQUIRE_NON_EMPTY_EMOJI_CHANNELS",
]
