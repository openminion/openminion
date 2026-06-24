from __future__ import annotations

import pytest

from openminion.modules.context.knowledge.errors import (
    DisabledProviderError,
    DuplicateProviderError,
    HybridDurableMemoryError,
    InvalidCapabilityError,
    InvalidLayerError,
    InvalidProviderTagError,
    KnowledgeGraphError,
    MissingRequiredCapabilityError,
    MultiActiveSecondBrainError,
    UnknownProviderError,
    UnsupportedCapabilityError,
)


@pytest.mark.parametrize(
    "exc_cls,expected_code",
    [
        (KnowledgeGraphError, "KNOWLEDGE_GRAPH_ERROR"),
        (InvalidLayerError, "INVALID_LAYER"),
        (InvalidProviderTagError, "INVALID_PROVIDER_TAG"),
        (InvalidCapabilityError, "INVALID_CAPABILITY"),
        (UnsupportedCapabilityError, "UNSUPPORTED_CAPABILITY"),
        (UnknownProviderError, "UNKNOWN_PROVIDER"),
        (DuplicateProviderError, "DUPLICATE_PROVIDER"),
        (MissingRequiredCapabilityError, "MISSING_REQUIRED_CAPABILITY"),
        (DisabledProviderError, "DISABLED_PROVIDER"),
        (MultiActiveSecondBrainError, "MULTI_ACTIVE_SECOND_BRAIN_REJECTED"),
        (HybridDurableMemoryError, "HYBRID_DURABLE_MEMORY_REJECTED"),
    ],
)
def test_error_codes_are_stable(exc_cls, expected_code):
    assert exc_cls.code == expected_code


def test_error_to_dict_round_trip():
    err = UnsupportedCapabilityError(
        "provider X does not advertise refresh",
        details={"provider": "graphify", "capability": "refresh"},
    )
    payload = err.to_dict()
    assert payload == {
        "code": "UNSUPPORTED_CAPABILITY",
        "message": "provider X does not advertise refresh",
        "details": {"provider": "graphify", "capability": "refresh"},
    }


def test_error_to_dict_defensive_details_copy():
    err = InvalidLayerError("bad layer", details={"value": "fourth_brain"})
    payload = err.to_dict()
    payload["details"]["mutated"] = True
    # Mutation on the dict returned by to_dict() must not leak back into the
    # frozen error instance.
    assert "mutated" not in err.details


def test_all_typed_errors_subclass_base():
    for cls in (
        InvalidLayerError,
        InvalidProviderTagError,
        InvalidCapabilityError,
        UnsupportedCapabilityError,
        UnknownProviderError,
        DuplicateProviderError,
        MissingRequiredCapabilityError,
        DisabledProviderError,
        MultiActiveSecondBrainError,
        HybridDurableMemoryError,
    ):
        assert issubclass(cls, KnowledgeGraphError)
