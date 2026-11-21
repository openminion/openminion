"""Clipboard image capture for the interactive shell."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from openminion.cli.presentation.image_paste import store_image_bytes
from openminion.cli.transport.images import read_clipboard_image
from openminion.modules.artifact.constants import MAX_TURN_IMAGES

if TYPE_CHECKING:
    from openminion.cli.interactive.runtime import OpenMinionRuntime
    from openminion.cli.interactive.terminal.composer import TerminalComposer


def paste_clipboard_image(
    runtime: OpenMinionRuntime, composer: TerminalComposer, working_dir: str
) -> str:
    """Store one clipboard image for the draft and report the selected list."""
    if len(composer.image_paths) >= MAX_TURN_IMAGES:
        raise ValueError("Select at most four images")
    data = read_clipboard_image()
    path = store_image_bytes(
        data,
        data_root=Path(runtime.api_runtime.data_root),
        session_id=runtime.session_id,
        name=f"clipboard-{uuid4().hex}",
    )
    try:
        composer.add_image(str(path), working_dir=working_dir)
    except (OSError, ValueError):
        path.unlink(missing_ok=True)
        raise
    summary: str = composer.manage_images("/image", working_dir=working_dir)
    return summary
