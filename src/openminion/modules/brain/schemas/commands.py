from typing import Any, Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .base import ArtifactRef, BaseCommand, new_uuid


class ToolCommand(BaseCommand):
    kind: Literal["tool"] = "tool"
    tool_name: str = Field(..., min_length=1)
    args: dict[str, Any] = Field(default_factory=dict)
    cwd: str | None = None
    env: dict[str, Any] | None = None
    artifacts_expected: bool = False
    idempotency_key: str = Field(default_factory=new_uuid, min_length=1)

    @model_validator(mode="after")
    def _normalize_args_from_inputs(self) -> "ToolCommand":
        if not self.args and isinstance(self.inputs, dict) and self.inputs:
            self.args = dict(self.inputs)
        return self


class AgentCommand(BaseCommand):
    kind: Literal["agent"] = "agent"
    target_agent_id: str = Field(..., min_length=1)
    method: str = Field(..., min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)
    expect_async: bool = False
    idempotency_key: str = Field(default_factory=new_uuid, min_length=1)


class AskUserCommand(BaseCommand):
    kind: Literal["ask_user"] = "ask_user"
    question: str = Field(..., min_length=1)
    options: list[str] | None = None
    idempotency_key: str = Field(default="")


class FinishCommand(BaseCommand):
    kind: Literal["finish"] = "finish"
    final_message: str | None = None
    final_artifact_refs: list[ArtifactRef] | None = None
    idempotency_key: str = Field(default="")
    timeout_ms: int | None = Field(default=None, ge=1)


class ThinkCommand(BaseCommand):
    kind: Literal["think"] = "think"
    prompt: str = Field(
        ...,
        min_length=1,
        description=(
            "The reasoning task to perform. Be specific: include relevant "
            "context, the goal, and the desired output format."
        ),
    )
    output_key: str = Field(
        default="",
        description="Optional label for this step's output (for traceability).",
    )
    model: str = Field(
        default="",
        description="Optional model override. Omit to use the agent's plan_model.",
    )
    idempotency_key: str = Field(default_factory=new_uuid, min_length=1)


class ThinkResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    response: str = Field(..., min_length=1, description="The reasoning output.")


Command = Annotated[
    Union[ToolCommand, AgentCommand, AskUserCommand, FinishCommand, ThinkCommand],
    Field(discriminator="kind"),
]


def refresh_command_identity(
    command: Command,
    *,
    update: dict[str, Any] | None = None,
) -> Command:
    refreshed_update = dict(update or {})
    refreshed_update.setdefault("command_id", new_uuid())
    if str(getattr(command, "idempotency_key", "") or "").strip():
        refreshed_update.setdefault("idempotency_key", new_uuid())
    return command.model_copy(update=refreshed_update, deep=True)
