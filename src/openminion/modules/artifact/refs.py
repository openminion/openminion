from collections.abc import Mapping
import re
from typing import Any

from openminion.modules.artifact.errors import ArtifactCtlError
from openminion.modules.artifact.models import parse_ref_or_sha

CANONICAL_ARTIFACT_REF_PATTERN = re.compile(
    r"\Aartifact://sha256/[0-9a-f]{64}\Z"
)
ARTIFACT_IMAGE_MIME_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/webp"}
)
MAX_ARTIFACT_IMAGE_BYTES = 10 * 1024 * 1024
MAX_ARTIFACT_IMAGES_PER_REQUEST = 4
MAX_ARTIFACT_IMAGE_BYTES_PER_REQUEST = 20 * 1024 * 1024
_ARTIFACT_IMAGE_UNAVAILABLE = "Artifact image is unavailable or unreadable"


def normalize_artifact_ref_target(value: Any) -> str | None:
    """Return a valid artifact ref target or ``None`` for non-artifact evidence."""
    candidates: list[Any] = []
    if isinstance(value, (str, bytes)):
        candidates.append(value.decode() if isinstance(value, bytes) else value)
    elif isinstance(value, Mapping):
        candidates.extend((value.get("ref"), value.get("sha256")))
    else:
        ref_value = getattr(value, "ref", None)
        sha_value = getattr(value, "sha256", None)
        if ref_value is not None or sha_value is not None:
            candidates.extend((ref_value, sha_value))
        else:
            candidates.append(value)

    for candidate in candidates:
        text = str(candidate or "").strip()
        if not text:
            continue
        try:
            return parse_ref_or_sha(text)
        except ValueError:
            continue
    return None


def normalize_artifact_ref_targets(values: Any) -> list[str]:
    """Normalize a heterogeneous artifact ref payload into unique valid targets."""
    if values is None:
        return []
    if isinstance(values, (str, bytes, Mapping)):
        raw_values = [values]
    else:
        try:
            raw_values = list(values)
        except TypeError:
            raw_values = [values]

    targets: list[str] = []
    seen: set[str] = set()
    for value in raw_values:
        target = normalize_artifact_ref_target(value)
        if target is None or target in seen:
            continue
        seen.add(target)
        targets.append(target)
    return targets


def create_default_artifactctl() -> Any:
    from openminion.modules.artifact.control import ArtifactCtl

    return ArtifactCtl({})


def is_canonical_artifact_ref(value: Any) -> bool:
    return bool(CANONICAL_ARTIFACT_REF_PATTERN.fullmatch(str(value or "")))


def inspect_artifact_image(ref: str) -> tuple[str, int]:
    artifactctl = create_default_artifactctl()
    try:
        meta = _validated_artifact_image_meta(artifactctl, ref)
        try:
            with artifactctl.open(ref):
                pass
        except ArtifactCtlError as exc:
            raise ArtifactCtlError(
                "INVALID_ARGUMENT", _ARTIFACT_IMAGE_UNAVAILABLE
            ) from exc
        return str(meta.mime).strip().lower(), int(meta.size_bytes)
    except ArtifactCtlError:
        raise
    except Exception as exc:
        raise ArtifactCtlError(
            "INVALID_ARGUMENT", _ARTIFACT_IMAGE_UNAVAILABLE
        ) from exc
    finally:
        artifactctl.close()


def read_artifact_image_bytes(ref: str) -> tuple[str, bytes]:
    artifactctl = create_default_artifactctl()
    try:
        meta = _validated_artifact_image_meta(artifactctl, ref)
        try:
            with artifactctl.open(ref) as stream:
                data = stream.read(MAX_ARTIFACT_IMAGE_BYTES + 1)
        except ArtifactCtlError as exc:
            raise ArtifactCtlError(
                "INVALID_ARGUMENT", _ARTIFACT_IMAGE_UNAVAILABLE
            ) from exc
        if len(data) > MAX_ARTIFACT_IMAGE_BYTES or len(data) != meta.size_bytes:
            raise ArtifactCtlError(
                "INVALID_ARGUMENT", "Artifact image size is invalid"
            )
        return str(meta.mime).strip().lower(), data
    except ArtifactCtlError:
        raise
    except Exception as exc:
        raise ArtifactCtlError(
            "INVALID_ARGUMENT", _ARTIFACT_IMAGE_UNAVAILABLE
        ) from exc
    finally:
        artifactctl.close()


def _validated_artifact_image_meta(artifactctl: Any, ref: str) -> Any:
    if not is_canonical_artifact_ref(ref):
        raise ArtifactCtlError(
            "INVALID_ARGUMENT", "Artifact image reference is not canonical"
        )
    try:
        meta = artifactctl.get(ref)
    except ArtifactCtlError as exc:
        raise ArtifactCtlError(
            "INVALID_ARGUMENT", _ARTIFACT_IMAGE_UNAVAILABLE
        ) from exc
    if meta.deleted_at:
        raise ArtifactCtlError("INVALID_ARGUMENT", "Artifact image was deleted")
    mime = str(meta.mime or "").strip().lower()
    if mime not in ARTIFACT_IMAGE_MIME_TYPES:
        raise ArtifactCtlError(
            "INVALID_ARGUMENT", "Artifact attachment is not a supported image"
        )
    size_bytes = int(meta.size_bytes)
    if size_bytes <= 0 or size_bytes > MAX_ARTIFACT_IMAGE_BYTES:
        raise ArtifactCtlError(
            "INVALID_ARGUMENT", "Artifact image exceeds the supported size"
        )
    return meta


def _apply_reference_edges(
    *,
    artifactctl: Any | None,
    owner_type: str,
    owner_id: str,
    ref_values: Any,
    operation_name: str,
) -> None:
    if artifactctl is None:
        return
    operation = getattr(artifactctl, operation_name)
    for target in normalize_artifact_ref_targets(ref_values):
        operation(owner_type, owner_id, target)


def add_reference_edges(
    *,
    artifactctl: Any | None,
    owner_type: str,
    owner_id: str,
    ref_values: Any,
) -> None:
    _apply_reference_edges(
        artifactctl=artifactctl,
        owner_type=owner_type,
        owner_id=owner_id,
        ref_values=ref_values,
        operation_name="ref_add",
    )


def remove_reference_edges(
    *,
    artifactctl: Any | None,
    owner_type: str,
    owner_id: str,
    ref_values: Any,
) -> None:
    _apply_reference_edges(
        artifactctl=artifactctl,
        owner_type=owner_type,
        owner_id=owner_id,
        ref_values=ref_values,
        operation_name="ref_remove",
    )
