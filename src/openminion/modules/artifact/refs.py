from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from openminion.modules.artifact.models import parse_ref_or_sha


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
