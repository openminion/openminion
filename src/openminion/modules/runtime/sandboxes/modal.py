"""Modal sandbox adapter."""

from typing import Any, Optional

from .contracts import SandboxExecResult


class ModalSandboxAdapter:
    name = "modal"

    def __init__(
        self,
        *,
        image: Optional[str] = None,
        app_name: str = "openminion-sandbox",
    ) -> None:
        self.image = image
        self.app_name = app_name
        self._sandbox: Any | None = None

    def _ensure_sandbox(self) -> Any:
        if self._sandbox is not None:
            return self._sandbox
        try:
            import modal
        except ImportError as exc:
            raise RuntimeError(
                "ModalSandboxAdapter requires the 'modal' package (pip install modal)."
            ) from exc
        try:
            app = modal.App.lookup(self.app_name, create_if_missing=True)
            image = (
                modal.Image.from_registry(self.image)
                if self.image
                else modal.Image.debian_slim()
            )
            self._sandbox = modal.Sandbox.create(app=app, image=image)
        except Exception as exc:  # noqa: BLE001 — surface as helpful runtime error
            raise RuntimeError(
                f"Modal sandbox creation failed ({exc!r}). Confirm 'modal token' "
                "is configured."
            ) from exc
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
        process = sandbox.exec(*command, workdir=cwd, env=env)
        try:
            stdout = process.stdout.read()
            stderr = process.stderr.read()
            exit_code = process.wait()
        finally:
            terminate = getattr(process, "terminate", None)
            if callable(terminate):
                terminate()
        return SandboxExecResult(
            exit_code=int(exit_code or 0),
            stdout=str(stdout or ""),
            stderr=str(stderr or ""),
            meta={"app_name": self.app_name},
        )

    def close(self) -> None:
        if self._sandbox is not None:
            terminate = getattr(self._sandbox, "terminate", None)
            if callable(terminate):
                terminate()
            self._sandbox = None
