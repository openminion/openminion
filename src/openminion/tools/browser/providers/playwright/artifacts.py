import hashlib
from datetime import datetime, timezone
from pathlib import Path


class ArtifactWriter:
    def __init__(
        self,
        *,
        workspace_root: str,
        downloads_dir: str,
        screenshots_dir: str,
        pdf_dir: str,
        traces_dir: str,
        allowed_roots: tuple[str, ...] | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve(strict=False)
        self.downloads_dir = Path(downloads_dir).resolve(strict=False)
        self.screenshots_dir = Path(screenshots_dir).resolve(strict=False)
        self.pdf_dir = Path(pdf_dir).resolve(strict=False)
        self.traces_dir = Path(traces_dir).resolve(strict=False)
        if allowed_roots:
            self.allowed_roots = tuple(
                Path(root).resolve(strict=False) for root in allowed_roots
            )
        else:
            self.allowed_roots = (self.workspace_root,)

    def ensure_dirs(self) -> None:
        for path in (
            self.downloads_dir,
            self.screenshots_dir,
            self.pdf_dir,
            self.traces_dir,
        ):
            self._ensure_workspace_path(path)
            path.mkdir(parents=True, exist_ok=True)

    def resolve_output_path(self, path: str) -> Path:
        token = str(path).strip()
        if not token:
            raise ValueError("output path is required")
        target = Path(token)
        if not target.is_absolute():
            target = (self.workspace_root / target).resolve(strict=False)
        else:
            target = target.resolve(strict=False)
        self._ensure_workspace_path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def write_screenshot(
        self, data: bytes, *, output_path: str | None = None
    ) -> dict[str, str]:
        target = self._target_for_kind(
            kind="screenshot", output_path=output_path, ext=".png"
        )
        target.write_bytes(data)
        return self._artifact(kind="screenshot", path=target, content=data)

    def write_pdf(
        self, data: bytes, *, output_path: str | None = None
    ) -> dict[str, str]:
        target = self._target_for_kind(kind="pdf", output_path=output_path, ext=".pdf")
        target.write_bytes(data)
        return self._artifact(kind="pdf", path=target, content=data)

    def _target_for_kind(self, *, kind: str, output_path: str | None, ext: str) -> Path:
        if output_path:
            return self.resolve_output_path(output_path)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        base_dir = self.screenshots_dir if kind == "screenshot" else self.pdf_dir
        base_dir.mkdir(parents=True, exist_ok=True)
        return (base_dir / f"{kind}_{stamp}{ext}").resolve(strict=False)

    def _artifact(self, *, kind: str, path: Path, content: bytes) -> dict[str, str]:
        rel = self._to_workspace_relative(path)
        return {
            "kind": kind,
            "path": rel,
            "sha256": hashlib.sha256(content).hexdigest(),
        }

    def _to_workspace_relative(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.workspace_root))
        except ValueError:
            return str(path)

    def _ensure_workspace_path(self, path: Path) -> None:
        resolved = path.resolve(strict=False)
        for root in self.allowed_roots:
            try:
                resolved.relative_to(root)
                return
            except ValueError:
                continue
        raise PermissionError(f"path must stay inside workspace: {path}")
