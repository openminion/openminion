from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openminion.cli.interactive.runtime import OpenMinionRuntime

from pathlib import Path

from openminion.api.runtime import APIRuntime
from openminion.cli.presentation.image_paste import detect_image_bytes
from openminion.modules.artifact.config import from_base_config
from openminion.modules.artifact.constants import (
    MAX_IMAGE_BYTES,
    MAX_IMAGE_BATCH_BYTES,
    MAX_TURN_IMAGES,
)
from openminion.modules.artifact.control import ArtifactCtl
from openminion.modules.artifact.errors import ArtifactCtlError


def read_selected_images(paths: list[str]) -> list[tuple[bytes, str]]:
    if len(paths) > MAX_TURN_IMAGES:
        raise ValueError("Select at most four images")
    images = []
    total = 0
    for path in paths:
        with Path(path).expanduser().open("rb") as stream:
            data = stream.read(MAX_IMAGE_BYTES + 1)
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise ValueError("Each image must be between 1 byte and 10 MiB")
        image_format = detect_image_bytes(data)
        if image_format not in {"png", "jpeg", "webp"}:
            raise ValueError("Select a PNG, JPEG or WebP image")
        total += len(data)
        if total > MAX_IMAGE_BATCH_BYTES:
            raise ValueError("Selected images exceed the 20 MiB request limit")
        images.append((data, f"image/{image_format}"))
    return images


def ingest_selected_images(
    runtime: APIRuntime,
    images: list[tuple[bytes, str]],
    *,
    session_id: str,
    agent_id: str,
    channel: str,
    target: str,
) -> list[str]:
    session = runtime.sessions.resolve_session(
        agent_id=agent_id,
        channel=channel,
        target=target,
        session_id=session_id,
    )
    config = from_base_config(
        base_config=runtime.config,
        home_root=runtime.home_root,
        data_root=runtime.data_root,
    )
    with ArtifactCtl(config) as artifacts:
        refs = []
        try:
            for data, mime in images:
                refs.append(
                    artifacts.ingest_image(
                        data, mime=mime, session_id=session.image_scope
                    ).ref
                )
        except (ArtifactCtlError, OSError):
            for ref in refs:
                artifacts.revoke_image(ref, session_id=session.image_scope)
            raise
        return refs


def configure_images(runtime: OpenMinionRuntime, command: str) -> str:
    import shlex

    parts = shlex.split(command)
    if parts == ["off"]:
        runtime._rt.configure_image_input(agent_id=runtime.agent_id, mode="off")
    elif (len(parts) == 3 and parts[0] == "native") or (
        len(parts) in {3, 4} and parts[0] == "separate"
    ):
        runtime._rt.configure_image_input(
            agent_id=runtime.agent_id,
            mode=parts[0],
            connection_id=parts[1],
            model=parts[2],
            purpose=parts[3] if len(parts) == 4 else "describe",
        )
    else:
        raise ValueError(
            "Use /image mode off, /image mode native CONNECTION MODEL, or /image mode separate CONNECTION MODEL [describe|text]"
        )
    runtime._rebind_model_gateway()
    return "Image settings saved. Image support is declared, not live verified."


def prepare_image_refs(
    runtime: OpenMinionRuntime, image_paths: list[str]
) -> tuple[str, ...]:
    if not image_paths:
        return ()
    runtime._ensure_agent_resolved()
    if not runtime.is_bound:
        raise RuntimeError("Select a conversation before attaching images")
    return tuple(
        ingest_selected_images(
            runtime._rt,
            read_selected_images(image_paths),
            session_id=runtime.session_id,
            agent_id=runtime.agent_id,
            channel=runtime._channel,
            target=runtime._target,
        )
    )
