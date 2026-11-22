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


def _validated_image_mime(data: bytes) -> str:
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Each image must be between 1 byte and 10 MiB")
    image_format = detect_image_bytes(data)
    if image_format not in {"png", "jpeg", "webp"}:
        raise ValueError("Select a PNG, JPEG or WebP image")
    return f"image/{image_format}"


def read_selected_images(
    paths: list[str], *, include_clipboard: bool = False
) -> list[tuple[bytes, str]]:
    if len(paths) > MAX_TURN_IMAGES:
        raise ValueError("Select at most four images")
    images = []
    total = 0
    for path in paths:
        with Path(path).expanduser().open("rb") as stream:
            data = stream.read(MAX_IMAGE_BYTES + 1)
        mime = _validated_image_mime(data)
        total += len(data)
        if total > MAX_IMAGE_BATCH_BYTES:
            raise ValueError("Selected images exceed the 20 MiB request limit")
        images.append((data, mime))
    if include_clipboard:
        data = read_clipboard_image()
        mime = _validated_image_mime(data)
        if len(images) >= MAX_TURN_IMAGES:
            raise ValueError("Select at most four images")
        if total + len(data) > MAX_IMAGE_BATCH_BYTES:
            raise ValueError("Selected images exceed the 20 MiB request limit")
        images.append((data, mime))
    return images


_PNGPASTE = ("pngpaste", "-")
_WL_PASTE = ("wl-paste", "--type", "image/png", "--no-newline")
_XCLIP = ("xclip", "-selection", "clipboard", "-t", "image/png", "-o")
_CLIPBOARD_IMAGE_COMMANDS = (_WL_PASTE, _XCLIP, _PNGPASTE)
_CLIPBOARD_TIMEOUT_SECONDS = 5
_CLIPBOARD_OK = "ok"
_CLIPBOARD_MISSING = "missing"
_CLIPBOARD_FAILED = "failed"


def _clipboard_command_output(command: tuple[str, ...]) -> tuple[bytes | None, str]:
    """Bounded stdout from one clipboard command, and how it ended: ok, missing or failed."""
    import subprocess
    import threading

    try:
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
    except FileNotFoundError:
        return None, _CLIPBOARD_MISSING
    watchdog = threading.Timer(_CLIPBOARD_TIMEOUT_SECONDS, process.kill)
    watchdog.start()
    data = b""
    try:
        stdout = process.stdout
        data = stdout.read(MAX_IMAGE_BYTES + 1) if stdout is not None else b""
        if len(data) > MAX_IMAGE_BYTES:
            process.kill()
            process.wait()
            return data, _CLIPBOARD_FAILED
        try:
            process.wait(timeout=_CLIPBOARD_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    finally:
        watchdog.cancel()
        if process.poll() is None:
            process.kill()
        process.wait()
        if process.stdout is not None:
            process.stdout.close()
    if process.returncode != 0 or not data:
        return None, _CLIPBOARD_FAILED
    return data, _CLIPBOARD_OK


def read_clipboard_image() -> bytes:
    """One image from the system clipboard, or a ValueError naming what went wrong."""
    failed: list[str] = []
    for command in _CLIPBOARD_IMAGE_COMMANDS:
        data, status = _clipboard_command_output(command)
        if data is not None and len(data) > MAX_IMAGE_BYTES:
            raise ValueError("The clipboard image exceeds 10 MiB")
        if status != _CLIPBOARD_OK or data is None:
            if status != _CLIPBOARD_MISSING:
                failed.append(command[0])
            continue
        return data
    if failed:
        raise ValueError(
            f"{', '.join(failed)} did not return an image (failed or timed out). "
            "Copy a PNG, JPEG or WebP first."
        )
    raise ValueError(
        "No clipboard image tool found. Install pngpaste on macOS, or "
        "wl-clipboard or xclip on Linux."
    )


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
