"""E2B sandbox adapter."""

from collections.abc import Mapping
from typing import Any, Optional

from openminion.base.config.env import EnvironmentConfig, resolve_environment_config
from .contracts import SandboxExecResult


class E2BSandboxAdapter:
    name = "e2b"

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        env: EnvironmentConfig | Mapping[str, object] | None = None,
        template: str = "base",
    ) -> None:
        env_owner = resolve_environment_config(env=env)
        self.api_key = (api_key or env_owner.get("E2B_API_KEY", "") or "").strip()
        self.template = template
        self._sandbox: Any | None = None

    def _ensure_sandbox(self) -> Any:
        if self._sandbox is not None:
            return self._sandbox
        if not self.api_key:
            raise RuntimeError(
                "E2BSandboxAdapter requires E2B_API_KEY in the environment. "
                "See https://e2b.dev for credential setup."
            )
        try:
            from e2b_code_interpreter import Sandbox
        except ImportError as exc:
            raise RuntimeError(
                "E2BSandboxAdapter requires the 'e2b_code_interpreter' "
                "package (pip install e2b-code-interpreter)."
            ) from exc
        self._sandbox = Sandbox(template=self.template, api_key=self.api_key)
        return self._sandbox

    def exec(
        self,
        command: list[str],
        *,
        cwd: Optional[str] = None,
        env: Optional[dict[str, str]] = None,
        timeout_seconds: Optional[int] = None,
    ) -> SandboxExecResult:
        sandbox = self._ensure_sandbox()
        # E2B's API accepts a command string; quote args naively. Real
        # production use would shlex-quote.
        cmd_string = " ".join(command)
        result = sandbox.commands.run(
            cmd_string,
            cwd=cwd,
            envs=env,
            timeout=timeout_seconds,
        )
        return SandboxExecResult(
            exit_code=int(getattr(result, "exit_code", 0)),
            stdout=str(getattr(result, "stdout", "") or ""),
            stderr=str(getattr(result, "stderr", "") or ""),
            meta={"sandbox_id": getattr(sandbox, "sandbox_id", None)},
        )

    def close(self) -> None:
        if self._sandbox is not None:
            close = getattr(self._sandbox, "kill", None) or getattr(
                self._sandbox, "close", None
            )
            if callable(close):
                close()
            self._sandbox = None
