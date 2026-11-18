"""Bounded binary upload for explicitly selected session images."""

from dataclasses import asdict
from http import HTTPStatus
from typing import Any
from urllib.parse import parse_qs, unquote

from openminion.api.responses.serialization import error_response
from openminion.base.config import ConfigError, resolve_agent_identity
from openminion.modules.artifact.config import from_base_config
from openminion.modules.artifact.constants import MAX_IMAGE_BYTES
from openminion.modules.artifact.control import ArtifactCtl
from openminion.modules.artifact.errors import ArtifactCtlError


def handle_image_upload(
    handler: Any,
    *,
    session_id: str,
    query: str,
    image_id: str | None = None,
    method: str = "POST",
) -> None:
    runtime = handler.runtime
    if runtime is None:
        handler._write_json(
            *error_response(
                HTTPStatus.SERVICE_UNAVAILABLE,
                code="runtime_unavailable",
                message="OpenMinion is not ready",
                retryable=True,
            )
        )
        return
    try:
        args = parse_qs(query)
        identity = resolve_agent_identity(
            runtime.config, args.get("agent_id", [None])[0]
        )
        channel = args.get("channel", [identity.profile.default_channel])[0]
        target = args.get("target", ["api-user"])[0]
        session = runtime.sessions.resolve_session(
            agent_id=identity.public_agent_id,
            channel=channel,
            target=target,
            session_id=unquote(session_id),
            resume_existing=False,
        )
        config = from_base_config(
            base_config=runtime.config,
            home_root=runtime.home_root,
            data_root=runtime.data_root,
        )
        if image_id is not None:
            ref = f"image://{image_id}"
            with ArtifactCtl(config) as artifacts:
                image = artifacts.get_image(ref, session_id=session.image_scope)
                if method == "DELETE":
                    artifacts.revoke_image(ref, session_id=session.image_scope)
                    handler._write_json(HTTPStatus.OK, {"ok": True})
                else:
                    data = artifacts.read_image(ref, session_id=session.image_scope)
                    handler.send_response(HTTPStatus.OK)
                    handler.send_header("Content-Type", image.mime)
                    handler.send_header("Content-Length", str(len(data)))
                    handler.send_header("Cache-Control", "no-store")
                    handler.end_headers()
                    handler.wfile.write(data)
            return
        length = int(handler.headers.get("Content-Length", "0"))
        if (
            handler.headers.get("Transfer-Encoding")
            or not 0 < length <= MAX_IMAGE_BYTES
        ):
            raise ValueError(
                "Image requires a Content-Length between 1 byte and 10 MiB"
            )
        mime = handler.headers.get("Content-Type", "").split(";", 1)[0].strip()
        if mime not in {"image/png", "image/jpeg", "image/webp"}:
            raise ValueError("Select a PNG, JPEG or WebP image")
        previous_timeout = handler.connection.gettimeout()
        handler.connection.settimeout(runtime.config.gateway.api_turn_timeout_seconds)
        try:
            data = handler.rfile.read(length)
        finally:
            handler.connection.settimeout(previous_timeout)
        if len(data) != length:
            raise ValueError("Image upload was incomplete")
        with ArtifactCtl(config) as artifacts:
            image = artifacts.ingest_image(
                data, mime=mime, session_id=session.image_scope
            )
        handler._write_json(
            HTTPStatus.CREATED,
            {"ok": True, "session_id": session.id, "image": asdict(image)},
        )
    except (ValueError, ConfigError, ArtifactCtlError, TimeoutError) as exc:
        handler.close_connection = True
        handler._write_json(
            *error_response(
                HTTPStatus.BAD_REQUEST,
                code="invalid_image",
                message=str(exc),
                retryable=False,
            )
        )
