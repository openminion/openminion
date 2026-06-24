"""Reaction tool schemas."""

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CHANNEL_ALIASES: dict[str, str] = {
    "discord": "discord",
    "slack": "slack",
    "telegram": "telegram",
    "whatsapp": "whatsapp",
    "google_chat": "google_chat",
    "googlechat": "google_chat",
    "gchat": "google_chat",
    "signal": "signal",
    "zalo_personal": "zalo_personal",
    "zalouser": "zalo_personal",
    "zalo": "zalo_personal",
}


def normalize_channel_name(value: Any) -> str:
    token = str(value or "").strip().lower().replace("-", "_")
    if not token:
        return ""
    return CHANNEL_ALIASES.get(token, token)


class MessageRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: str = Field(..., min_length=1)
    conversation_id: str = Field(..., min_length=1)
    message_id: str = Field(..., min_length=1)
    account_id: Optional[str] = None

    @field_validator("channel", mode="before")
    @classmethod
    def _normalize_channel(cls, value: Any) -> str:
        normalized = normalize_channel_name(value)
        if not normalized:
            raise ValueError("channel is required")
        return normalized

    @field_validator("conversation_id", "message_id", mode="before")
    @classmethod
    def _normalize_required_str(cls, value: Any) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("value is required")
        return normalized

    @field_validator("account_id", mode="before")
    @classmethod
    def _normalize_optional_str(cls, value: Any) -> Optional[str]:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None


class ReactionsSetArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: MessageRef
    emoji: str = Field(
        ...,
        description="Emoji to add. Empty string removes bot reactions where supported.",
    )
    remove: bool = Field(
        default=False,
        description="Remove the specified emoji where supported (requires non-empty emoji).",
    )
    reason: Optional[str] = Field(default=None, description="Optional audit note.")

    @field_validator("emoji", mode="before")
    @classmethod
    def _normalize_emoji(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @field_validator("reason", mode="before")
    @classmethod
    def _normalize_reason(cls, value: Any) -> Optional[str]:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    @model_validator(mode="after")
    def _validate_remove_requires_emoji(self) -> "ReactionsSetArgs":
        if self.remove and not self.emoji:
            raise ValueError("remove=true requires a non-empty emoji")
        return self


class ReactionsListArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: MessageRef
    scope: Literal["bot_only", "all"] = "bot_only"


AppliedAction = Literal["added", "removed_one", "removed_all_bot", "noop"]


class ReactionsSetApplied(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: AppliedAction
    emoji: str = ""


class ReactionsSetResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    applied: ReactionsSetApplied
    message: MessageRef
    warnings: list[str] = Field(default_factory=list)
    error: Optional[str] = None


class ReactionsListRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    emoji: str
    count: int
    reacted_by_bot: bool


class ReactionsListResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    reactions: list[ReactionsListRow] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


REACTIONS_SET_INPUT_SCHEMA: dict[str, Any] = ReactionsSetArgs.model_json_schema()
REACTIONS_SET_OUTPUT_SCHEMA: dict[str, Any] = ReactionsSetResult.model_json_schema()
REACTIONS_LIST_INPUT_SCHEMA: dict[str, Any] = ReactionsListArgs.model_json_schema()
REACTIONS_LIST_OUTPUT_SCHEMA: dict[str, Any] = ReactionsListResult.model_json_schema()
