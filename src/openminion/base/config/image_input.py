"""Explicit image-input policy for one agent's configured model connections."""

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .base import ConfigError


class ImageInputConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    mode: Literal["native", "separate"]
    connection_id: str | None = Field(default=None, min_length=1)
    model: str | None = Field(default=None, min_length=1)
    purpose: Literal["describe", "text"] | None = None

    @model_validator(mode="after")
    def validate_route(self) -> Self:
        if self.mode == "native":
            if (
                self.connection_id is not None
                or self.model is not None
                or self.purpose is not None
            ):
                raise ValueError(
                    "native image input uses the chat model; reader fields are invalid"
                )
        elif not self.connection_id or not self.model:
            raise ValueError("separate image input requires connection_id and model")
        elif self.purpose is None:
            self.purpose = "describe"
        return self


def validate_image_connections(
    policy: ImageInputConfig | None, connections: dict[str, dict[str, Any]]
) -> None:
    for connection_id, connection in connections.items():
        capabilities = connection.get("image_input", {})
        if not isinstance(capabilities, dict):
            raise ConfigError(
                f"model_connections.{connection_id}.image_input must be a model map"
            )
        for model, capability in capabilities.items():
            if model not in connection.get("models", []):
                raise ConfigError(
                    f"image capability model {model!r} is not configured on {connection_id!r}"
                )
            if capability not in ("supported", "unsupported", "unknown"):
                raise ConfigError(
                    "image capability must be supported, unsupported or unknown"
                )
    if policy is not None and policy.mode == "separate":
        reader_connection = connections.get(policy.connection_id or "")
        if reader_connection is None or policy.model not in reader_connection.get(
            "models", []
        ):
            raise ConfigError(
                "image reader must reference a configured connection and model"
            )
