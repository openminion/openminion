"""Official Daytona SDK transport."""

from __future__ import annotations

import math
import shlex
from collections.abc import Mapping
from typing import Any

from .client import DaytonaTransportError
from .config import DaytonaConfig


class DaytonaSdkTransport:
    def __init__(self) -> None:
        self._sdk: Any = None
        self._workspaces: dict[str, Any] = {}
        self._config: DaytonaConfig | None = None

    def open(self, config: DaytonaConfig, *, api_key: str) -> None:
        if not api_key:
            raise DaytonaTransportError(
                code="UNAVAILABLE", message="Daytona API key is not configured"
            )
        try:
            from daytona import Daytona, DaytonaConfig as SdkConfig
        except ModuleNotFoundError as exc:
            raise DaytonaTransportError(
                code="UNAVAILABLE",
                message="Install openminion[sandbox-daytona] to use Daytona",
            ) from exc
        self._sdk = Daytona(SdkConfig(api_key=api_key, api_url=config.endpoint))
        self._config = config

    def close(self) -> None:
        self._sdk = None
        self._workspaces.clear()

    def create_workspace(
        self,
        *,
        name: str,
        image: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        if self._sdk is None or self._config is None:
            raise DaytonaTransportError(code="UNAVAILABLE", message="Daytona is closed")
        from daytona import CreateSandboxFromImageParams, Image

        runtime = dict(metadata or {})
        sandbox = self._sdk.create(
            CreateSandboxFromImageParams(
                name=name,
                image=Image.base(image),
                network_block_all=runtime.get("net_mode") == "deny",
                domain_allow_list=",".join(runtime.get("allowed_domains") or [])
                or None,
            ),
            timeout=self._config.connect_timeout_s,
        )
        self._workspaces[sandbox.id] = sandbox
        return {
            "workspace_id": sandbox.id,
            "name": name,
            "image": image,
            "metadata": {"root_dir": sandbox.get_user_root_dir()},
        }

    def destroy_workspace(self, workspace_id: str) -> None:
        sandbox = self._workspaces.pop(workspace_id)
        sandbox.delete(wait=True)

    def execute_command(
        self,
        *,
        workspace_id: str,
        command: list[str],
        cwd: str | None,
        env: Mapping[str, str],
        timeout_s: float,
        max_output_bytes: int,
    ) -> Mapping[str, Any]:
        del max_output_bytes
        response = self._workspaces[workspace_id].process.exec(
            shlex.join(command),
            cwd=cwd,
            env=dict(env),
            timeout=max(1, math.ceil(timeout_s)),
        )
        return {
            "returncode": response.exit_code,
            "stdout": response.result,
            "stderr": "",
            "truncated": False,
            "timed_out": False,
        }

    def start_session(self, **_kwargs: Any) -> Mapping[str, Any]:
        raise DaytonaTransportError(
            code="UNAVAILABLE", message="Daytona session transport is not configured"
        )

    def poll_session(self, **_kwargs: Any) -> Mapping[str, Any]:
        raise DaytonaTransportError(
            code="UNAVAILABLE", message="Daytona session transport is not configured"
        )

    def send_session_input(self, **_kwargs: Any) -> Mapping[str, Any] | None:
        raise DaytonaTransportError(
            code="UNAVAILABLE", message="Daytona session transport is not configured"
        )

    def terminate_session(self, **_kwargs: Any) -> Mapping[str, Any] | None:
        raise DaytonaTransportError(
            code="UNAVAILABLE", message="Daytona session transport is not configured"
        )


__all__ = ["DaytonaSdkTransport"]
