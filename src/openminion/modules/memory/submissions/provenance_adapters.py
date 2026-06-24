"""Typed provenance adapters for submission callers."""

from typing import Any, Mapping

from openminion.modules.memory.submissions.envelope import SubmissionProvenance


def _provenance(
    *,
    source_owner: str,
    extra: Mapping[str, Any] | None = None,
    **fields: str | None,
) -> SubmissionProvenance:
    return SubmissionProvenance(
        source_owner=source_owner,
        extra=dict(extra or {}),
        **fields,
    )


def provenance_from_turn(
    *,
    turn_id: str,
    source_owner: str,
    extra: Mapping[str, Any] | None = None,
) -> SubmissionProvenance:
    return _provenance(source_owner=source_owner, turn_id=turn_id, extra=extra)


def provenance_from_tool_call(
    *,
    turn_id: str,
    tool_call_id: str,
    source_owner: str,
    extra: Mapping[str, Any] | None = None,
) -> SubmissionProvenance:
    return _provenance(
        source_owner=source_owner,
        turn_id=turn_id,
        tool_call_id=tool_call_id,
        extra=extra,
    )


def provenance_from_file(
    *,
    file_path: str,
    source_owner: str,
    turn_id: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> SubmissionProvenance:
    return _provenance(
        source_owner=source_owner,
        turn_id=turn_id,
        file_path=file_path,
        extra=extra,
    )


def provenance_from_artifact(
    *,
    artifact_id: str,
    source_owner: str,
    turn_id: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> SubmissionProvenance:
    return _provenance(
        source_owner=source_owner,
        turn_id=turn_id,
        artifact_id=artifact_id,
        extra=extra,
    )


def provenance_from_validation(
    *,
    validation_command: str,
    source_owner: str,
    turn_id: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> SubmissionProvenance:
    return _provenance(
        source_owner=source_owner,
        turn_id=turn_id,
        validation_command=validation_command,
        extra=extra,
    )


def provenance_from_user_correction(
    *,
    user_correction_id: str,
    source_owner: str,
    turn_id: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> SubmissionProvenance:
    return _provenance(
        source_owner=source_owner,
        turn_id=turn_id,
        user_correction_id=user_correction_id,
        extra=extra,
    )


__all__ = (
    "provenance_from_artifact",
    "provenance_from_file",
    "provenance_from_tool_call",
    "provenance_from_turn",
    "provenance_from_user_correction",
    "provenance_from_validation",
)
