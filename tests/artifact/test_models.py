from __future__ import annotations

import pytest

from openminion.modules.artifact.models import (
    ARTIFACT_REF_PREFIX,
    VALID_OWNER_TYPES,
    parse_ref_or_sha,
    sha_to_ref,
)


def test_parse_ref_accepts_prefixed_reference() -> None:
    sha = "a" * 64
    ref = sha_to_ref(sha)
    assert ref.startswith(ARTIFACT_REF_PREFIX)
    assert parse_ref_or_sha(ref) == sha


def test_parse_ref_accepts_raw_sha() -> None:
    sha = "b" * 64
    assert parse_ref_or_sha(sha) == sha


def test_parse_ref_normalizes_uppercase_sha() -> None:
    sha = "A" * 64
    assert parse_ref_or_sha(sha) == sha.lower()


def test_parse_ref_rejects_invalid_length() -> None:
    with pytest.raises(ValueError):
        parse_ref_or_sha("1234")


def test_parse_ref_rejects_non_hex() -> None:
    bad = "g" * 64
    with pytest.raises(ValueError):
        parse_ref_or_sha(bad)


def test_owner_type_contract_is_stable() -> None:
    assert VALID_OWNER_TYPES == {"session", "memory", "alias", "collection", "a2a"}
