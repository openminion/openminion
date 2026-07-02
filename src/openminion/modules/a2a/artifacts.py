import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import ArtifactRef

_MIME_SUFFIXES = {
    "application/json": ".json",
    "text/plain": ".txt",
}


class LocalArtifactStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser().resolve(strict=False)
        self.root.mkdir(parents=True, exist_ok=True)

    def put_bytes(
        self, data: bytes, *, mime: str, label: str | None = None
    ) -> ArtifactRef:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        rel_dir = Path(day)
        full_dir = self.root / rel_dir
        full_dir.mkdir(parents=True, exist_ok=True)
        suffix = _suffix_from_mime(mime)
        name = f"{uuid.uuid4().hex}{suffix}"
        full_path = full_dir / name
        full_path.write_bytes(data)
        sha = hashlib.sha256(data).hexdigest()
        return ArtifactRef(
            ref=full_path.resolve().as_uri(),
            mime=mime,
            sha256=sha,
            size_bytes=len(data),
            label=label,
        )

    def put_json(
        self, payload: dict[str, Any], *, label: str | None = None
    ) -> ArtifactRef:
        encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode(
            "utf-8"
        )
        return self.put_bytes(encoded, mime="application/json", label=label)


def _suffix_from_mime(mime: str) -> str:
    return _MIME_SUFFIXES.get(mime, ".bin")
