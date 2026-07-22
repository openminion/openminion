"""Typed submission envelope for the OpenMinion to Sophiagraph pipeline."""

from dataclasses import dataclass, field
from typing import Any, Final, Literal
from collections.abc import Mapping


SUBMISSION_ENVELOPE_SCHEMA_VERSION: Final[str] = "openminion_sophiagraph_submission.v1"


SubmissionPayloadKind = Literal[
    "memory_candidate",
    "episode",
    "outcome",
    "artifact",
    "retrieval_feedback",
    "approved_block",
    "document",
    "explicit_link",
    "tag",
    "property",
    "fact",
    "retrieval_event",
    "entity_candidate",
    "entity_alias_candidate",
    "fact_candidate",
    "contradiction_decision",
    "entity_summary",
    "episode_event",
    "episode_step",
    "decision",
    "procedure",
]


PAYLOAD_KINDS: Final[frozenset[str]] = frozenset(
    {
        "memory_candidate",
        "episode",
        "outcome",
        "artifact",
        "retrieval_feedback",
        "approved_block",
        "document",
        "explicit_link",
        "tag",
        "property",
        "fact",
        "retrieval_event",
        "entity_candidate",
        "entity_alias_candidate",
        "fact_candidate",
        "contradiction_decision",
        "entity_summary",
        "episode_event",
        "episode_step",
        "decision",
        "procedure",
    }
)


SubmissionTrustMode = Literal["direct", "candidate"]
TRUST_MODES: Final[frozenset[str]] = frozenset({"direct", "candidate"})


class SubmissionEnvelopeError(ValueError):
    """Raised when an envelope fails structural validation."""

    def __init__(self, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


@dataclass(frozen=True)
class SubmissionNamespace:
    """Typed namespace identifier attached to every submission.

    Mirrors the ``sophiagraph.models.MemoryNamespace`` dimensions the package
    already accepts, but stays as a pure-OpenMinion dataclass so this module
    has no ``sophiagraph`` import dependency. The SDK path converts to the
    package type at the boundary.
    """

    tenant_id: str | None = None
    org_id: str | None = None
    user_id: str | None = None
    agent_id: str | None = None
    session_id: str | None = None
    conversation_id: str | None = None
    project_id: str | None = None
    graph_id: str | None = None

    def __post_init__(self) -> None:
        if not self.as_dict():
            raise SubmissionEnvelopeError(
                "at least one namespace dimension is required",
                field="namespace",
            )

    def as_dict(self) -> dict[str, str]:
        return {
            name: value
            for name, value in (
                ("tenant_id", self.tenant_id),
                ("org_id", self.org_id),
                ("user_id", self.user_id),
                ("agent_id", self.agent_id),
                ("session_id", self.session_id),
                ("conversation_id", self.conversation_id),
                ("project_id", self.project_id),
                ("graph_id", self.graph_id),
            )
            if value
        }


@dataclass(frozen=True)
class SubmissionProvenance:
    """Structural provenance attached to a submission.

    Every field is structural / caller-supplied. The pipeline does not infer
    provenance from prose; OMSS-04 provides typed adapter helpers for the
    common runtime origin points (turn, tool call, file, artifact, validation
    command, user correction).
    """

    source_owner: str
    turn_id: str | None = None
    tool_call_id: str | None = None
    file_path: str | None = None
    artifact_id: str | None = None
    validation_command: str | None = None
    user_correction_id: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.source_owner:
            raise SubmissionEnvelopeError(
                "source_owner is required",
                field="provenance.source_owner",
            )
        if not isinstance(self.extra, Mapping):
            raise SubmissionEnvelopeError(
                "extra must be a Mapping[str, Any]",
                field="provenance.extra",
            )


@dataclass(frozen=True)
class OntologyBinding:
    """SOCC-04: caller-configured project ontology attached to a submission.

    Both fields are caller-supplied identifiers. SophiaGraph never derives
    them from prose; OpenMinion projects pick the ``(ontology_id, version)``
    pair through explicit configuration.
    """

    ontology_id: str
    version: str

    def __post_init__(self) -> None:
        if not self.ontology_id:
            raise SubmissionEnvelopeError(
                "ontology_id is required",
                field="ontology_binding.ontology_id",
            )
        if not self.version:
            raise SubmissionEnvelopeError(
                "ontology version is required",
                field="ontology_binding.version",
            )


@dataclass(frozen=True)
class SubmissionEnvelope:
    """Canonical envelope wrapping every OpenMinion → Sophiagraph submission."""

    namespace: SubmissionNamespace
    payload_kind: str
    payload: Mapping[str, Any]
    provenance: SubmissionProvenance
    idempotency_key: str
    trust_mode: SubmissionTrustMode = "direct"
    schema_version: str = SUBMISSION_ENVELOPE_SCHEMA_VERSION
    ontology_binding: OntologyBinding | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.namespace, SubmissionNamespace):
            raise SubmissionEnvelopeError(
                "namespace must be SubmissionNamespace",
                field="namespace",
            )
        if not isinstance(self.provenance, SubmissionProvenance):
            raise SubmissionEnvelopeError(
                "provenance must be SubmissionProvenance",
                field="provenance",
            )
        if self.payload_kind not in PAYLOAD_KINDS:
            raise SubmissionEnvelopeError(
                f"unknown payload_kind {self.payload_kind!r}; "
                f"allowed: {sorted(PAYLOAD_KINDS)}",
                field="payload_kind",
            )
        if not isinstance(self.payload, Mapping):
            raise SubmissionEnvelopeError(
                "payload must be a Mapping[str, Any]",
                field="payload",
            )
        if not self.idempotency_key:
            raise SubmissionEnvelopeError(
                "idempotency_key is required",
                field="idempotency_key",
            )
        if self.trust_mode not in TRUST_MODES:
            raise SubmissionEnvelopeError(
                f"unknown trust_mode {self.trust_mode!r}; "
                f"allowed: {sorted(TRUST_MODES)}",
                field="trust_mode",
            )
        if self.schema_version != SUBMISSION_ENVELOPE_SCHEMA_VERSION:
            raise SubmissionEnvelopeError(
                (
                    "schema_version drift: envelope carries "
                    f"{self.schema_version!r} but module pins "
                    f"{SUBMISSION_ENVELOPE_SCHEMA_VERSION!r}"
                ),
                field="schema_version",
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "namespace": self.namespace.as_dict(),
            "payload_kind": self.payload_kind,
            "payload": dict(self.payload),
            "provenance": {
                "source_owner": self.provenance.source_owner,
                "turn_id": self.provenance.turn_id,
                "tool_call_id": self.provenance.tool_call_id,
                "file_path": self.provenance.file_path,
                "artifact_id": self.provenance.artifact_id,
                "validation_command": self.provenance.validation_command,
                "user_correction_id": self.provenance.user_correction_id,
                "extra": dict(self.provenance.extra),
            },
            "idempotency_key": self.idempotency_key,
            "trust_mode": self.trust_mode,
            "ontology_binding": (
                {
                    "ontology_id": self.ontology_binding.ontology_id,
                    "version": self.ontology_binding.version,
                }
                if self.ontology_binding is not None
                else None
            ),
        }


__all__ = (
    "OntologyBinding",
    "PAYLOAD_KINDS",
    "SUBMISSION_ENVELOPE_SCHEMA_VERSION",
    "SubmissionEnvelope",
    "SubmissionEnvelopeError",
    "SubmissionNamespace",
    "SubmissionPayloadKind",
    "SubmissionProvenance",
    "SubmissionTrustMode",
    "TRUST_MODES",
)
