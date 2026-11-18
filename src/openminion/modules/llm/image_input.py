"""Admission and provider-bound materialization of session image inputs."""

from openminion.modules.artifact.models import ImageInputContext
from openminion.modules.llm.schemas import LLMRequest, LLMResponse
from typing import Callable
from logging import Logger
from openminion.modules.storage import SessionRecord, SessionStore

import base64
import hashlib
import json
from dataclasses import asdict

from openminion.base.config import OpenMinionConfig, resolve_agent_config
from openminion.base.config.runtime.profile import build_runtime_config
from openminion.base.config.runtime.profile_overrides import RunProfileOverrides
from openminion.modules.artifact.config import ArtifactCtlConfig
from openminion.modules.artifact.constants import MAX_IMAGE_BATCH_BYTES
from openminion.modules.artifact.control import ArtifactCtl
from openminion.modules.llm.providers.contracts import ProviderError
from openminion.modules.llm.schemas import (
    ImageContentPart,
    TextContentPart,
)


def image_destination(config: OpenMinionConfig) -> str:
    profile = resolve_agent_config(config)
    provider = getattr(config.providers, profile.provider, None)
    if provider is None:
        raise ProviderError(
            "This model connection cannot receive images",
            code="IMAGE_INPUT_UNSUPPORTED",
        )
    payload = {"provider": profile.provider, "config": asdict(provider)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def image_reader_config(
    config: OpenMinionConfig, *, connection_id: str, model: str
) -> OpenMinionConfig:
    profile = resolve_agent_config(config)
    connection = profile.model_connections.get(connection_id)
    if (
        connection is None
        or connection.get("image_input", {}).get(model) != "supported"
    ):
        raise ProviderError(
            "Select a model with declared image support in image settings",
            code="IMAGE_INPUT_UNSUPPORTED",
        )
    selected = build_runtime_config(
        config, overrides=RunProfileOverrides(provider=connection_id, model=model)
    )
    provider = getattr(
        selected.providers, resolve_agent_config(selected).provider, None
    )
    if not getattr(provider, "enable_vision_input", False):
        raise ProviderError(
            "Enable image input on the selected model connection",
            code="IMAGE_INPUT_DISABLED",
        )
    return selected


def admit_images(
    config: OpenMinionConfig,
    artifacts: ArtifactCtlConfig,
    *,
    session: SessionRecord,
    image_refs: tuple[str, ...],
) -> ImageInputContext:
    profile = resolve_agent_config(config)
    policy = profile.image_input
    if policy is None:
        raise ProviderError(
            "Configure image input for this agent before sending images",
            code="IMAGE_INPUT_NOT_CONFIGURED",
        )
    if policy.mode == "separate":
        connection_id, model = policy.connection_id, policy.model
        selected = image_reader_config(config, connection_id=connection_id, model=model)
    else:
        actual = image_destination(config)
        matches = []
        for key, connection in profile.model_connections.items():
            for candidate in connection.get("models", []):
                if connection.get("image_input", {}).get(candidate) != "supported":
                    continue
                selected = build_runtime_config(
                    config, overrides=RunProfileOverrides(provider=key, model=candidate)
                )
                if image_destination(selected) == actual:
                    image_reader_config(config, connection_id=key, model=candidate)
                    matches.append((key, candidate, selected))
        if len(matches) != 1:
            raise ProviderError(
                "Declare image support for the active model connection",
                code="IMAGE_INPUT_UNSUPPORTED",
            )
        connection_id, model, selected = matches[0]
    record = ImageInputContext(
        session_id=session.id,
        session_generation=session.image_generation,
        image_refs=list(image_refs),
        mode=policy.mode,
        connection_id=connection_id,
        model=model,
        destination=image_destination(selected),
        purpose=policy.purpose or "describe",
    )
    check_image_refs(artifacts, record)
    return record


def check_image_refs(config: ArtifactCtlConfig, record: ImageInputContext) -> None:
    with ArtifactCtl(config) as artifacts:
        images = [
            artifacts.get_image(
                ref, session_id=f"{record.session_id}::{record.session_generation}"
            )
            for ref in record.image_refs
        ]
    if (
        max(
            sum(image.source_size_bytes for image in images),
            sum(image.size_bytes for image in images),
        )
        > MAX_IMAGE_BATCH_BYTES
    ):
        raise ProviderError(
            "Selected images exceed the 20 MiB request limit",
            code="IMAGE_INPUT_TOO_LARGE",
        )


def image_parts(
    config: ArtifactCtlConfig, record: ImageInputContext, *, question: str
) -> list[TextContentPart | ImageContentPart]:
    check_image_refs(config, record)
    parts = [TextContentPart(text=question)] if question else []
    with ArtifactCtl(config) as artifacts:
        for ref in record.image_refs:
            data = artifacts.read_image(
                ref, session_id=f"{record.session_id}::{record.session_generation}"
            )
            parts.append(
                ImageContentPart(
                    source="base64",
                    mime_type="image/png",
                    data_base64=base64.b64encode(data).decode("ascii"),
                    refs=[ref],
                )
            )
    return parts


def materialize_image_input(
    config: OpenMinionConfig,
    artifacts: ArtifactCtlConfig,
    record: ImageInputContext,
    *,
    question: str,
) -> list[TextContentPart | ImageContentPart]:
    check_image_refs(artifacts, record)
    if record.mode == "native":
        if image_destination(config) != record.destination:
            raise ProviderError(
                "This conversation contains images for a different model connection. Restore that connection or start a new conversation.",
                code="IMAGE_DESTINATION_CHANGED",
            )
        image_reader_config(
            config, connection_id=record.connection_id, model=record.model
        )
        return image_parts(artifacts, record, question=question)
    if record.observations is None:
        raise ProviderError(
            "Image reading has not completed", code="IMAGE_INPUT_NOT_READY"
        )
    from openminion.modules.prompting.image_input import image_evidence_prompt

    return [
        TextContentPart(text=question),
        TextContentPart(text=image_evidence_prompt(record)),
    ]


def read_images(
    config: OpenMinionConfig,
    artifacts: ArtifactCtlConfig,
    record: ImageInputContext,
    *,
    question: str,
    max_output_tokens: int,
    logger: Logger,
    sessions: SessionStore,
    on_response: Callable[[LLMResponse], None],
    before_request: Callable[[LLMRequest], LLMRequest],
) -> LLMResponse:
    from openminion.modules.llm.providers.factory import build_runtime_llm_handle
    from openminion.modules.llm.schemas import LLMRequest, Message
    from openminion.modules.prompting.image_input import image_reader_prompt
    from openminion.modules.runtime.sync import raise_if_cancelled

    selected = image_reader_config(
        config, connection_id=record.connection_id, model=record.model
    )
    if image_destination(selected) != record.destination:
        raise ProviderError(
            "Image reader settings changed. Send the images again.",
            code="IMAGE_DESTINATION_CHANGED",
        )
    request = LLMRequest(
        model=record.model,
        tools=[],
        tool_choice="none",
        max_output_tokens=max_output_tokens,
        messages=[
            Message(role="system", content=image_reader_prompt(record)),
            Message(
                role="user",
                content=question,
                image_input=record,
            ),
        ],
        metadata={"purpose": "image_reader"},
    )
    raise_if_cancelled()
    runtime = build_runtime_llm_handle(selected, logger)

    def resolve_attempt(attempt: LLMRequest) -> LLMRequest:
        bounded = before_request(attempt)
        return resolve_image_request(
            bounded,
            config=selected,
            artifacts=artifacts,
            sessions=sessions,
            provider=runtime.name,
            model=runtime.model,
            reader=True,
        )

    runtime.client.image_resolver = resolve_attempt
    runtime.client.on_attempt_response = on_response
    try:
        response = runtime.client.call_sync(request)
    finally:
        runtime.close()
    check_image_session(sessions, record)
    check_image_refs(artifacts, record)
    raise_if_cancelled()
    return response


def finish_image_reading(
    record: ImageInputContext, response: LLMResponse
) -> ImageInputContext:
    from pydantic import TypeAdapter, ValidationError
    from openminion.modules.artifact.models import ImageObservation

    if (
        not response.ok
        or response.tool_calls
        or response.finish_reason in {"length", "max_tokens"}
        or len(response.output_text.encode("utf-8")) > 65536
    ):
        raise ProviderError(
            "Image reader could not produce a complete result",
            code="IMAGE_READER_FAILED",
        )
    try:
        observations = TypeAdapter(list[ImageObservation]).validate_json(
            response.output_text, strict=True
        )
    except ValidationError as exc:
        raise ProviderError(
            "Image reader returned an invalid result", code="IMAGE_READER_FAILED"
        ) from exc
    if [item.image_ref for item in observations] != record.image_refs or (
        record.purpose == "text" and any(item.observations for item in observations)
    ):
        raise ProviderError(
            "Image reader returned mismatched image evidence",
            code="IMAGE_READER_FAILED",
        )
    return record.model_copy(update={"observations": observations})


def check_image_session(sessions: SessionStore, record: ImageInputContext) -> None:
    session = sessions.get_session(record.session_id)
    if session is None or session.image_generation != record.session_generation:
        raise ProviderError(
            "The session for these images is no longer available",
            code="IMAGE_SESSION_UNAVAILABLE",
        )


def resolve_image_request(
    request: LLMRequest,
    *,
    config: OpenMinionConfig,
    artifacts: ArtifactCtlConfig,
    sessions: SessionStore,
    provider: str,
    model: str,
    reader: bool = False,
) -> LLMRequest:
    from openminion.modules.artifact.errors import ArtifactCtlError
    from openminion.modules.llm.errors import LLMCtlError

    try:
        if request.provider != provider or request.model != model:
            raise ProviderError("Image input cannot switch model connections")
        messages = []
        for message in request.messages:
            record = message.image_input
            if record is None:
                messages.append(message)
                continue
            check_image_session(sessions, record)
            if reader:
                if image_destination(config) != record.destination:
                    raise ProviderError("Image reader destination changed")
                parts = image_parts(artifacts, record, question=message.content)
            else:
                parts = materialize_image_input(
                    config, artifacts, record, question=message.content
                )
            messages.append(message.model_copy(update={"content_parts": parts}))
        return request.model_copy(update={"messages": messages})
    except (ProviderError, ArtifactCtlError) as exc:
        raise LLMCtlError("POLICY_DENIED", str(exc)) from exc
