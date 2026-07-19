from __future__ import annotations

import pytest

from openminion.modules.session.security import (
    FernetSessionKeyRing,
    SessionContentSearchDisabledError,
    SessionEncryptionIdentityError,
    SessionEncryptionKeyError,
    assert_content_search_allowed,
    build_migration_checkpoint,
    decrypt_session_payload,
    encrypt_session_payload,
    referenced_key_ids,
)
from openminion.modules.secret.interfaces import SECRET_KEY_RING_INTERFACE_VERSION


def test_session_encryption_round_trip_restart_and_rotation() -> None:
    ring = FernetSessionKeyRing(active_key_id="k1")
    envelope = encrypt_session_payload(
        ring,
        payload={"text": "hello"},
        purpose="session.turn.content",
        record_identity={"session_id": "s1", "record_id": "r1"},
    )
    ring.rotate(key_id="k2")
    new_envelope = encrypt_session_payload(
        ring,
        payload={"text": "new"},
        purpose="session.turn.content",
        record_identity={"session_id": "s1", "record_id": "r2"},
    )

    assert decrypt_session_payload(ring, envelope, expected_identity={"session_id": "s1", "record_id": "r1"}) == {"text": "hello"}
    assert decrypt_session_payload(ring, new_envelope, expected_identity={"session_id": "s1", "record_id": "r2"}) == {"text": "new"}
    assert referenced_key_ids([envelope, new_envelope]) == {"k1", "k2"}
    assert ring.can_remove_key("k1", referenced_key_ids([envelope])) is False


def test_session_encryption_wrong_key_and_transplant_fail_closed() -> None:
    ring = FernetSessionKeyRing(active_key_id="k1")
    envelope = encrypt_session_payload(
        ring,
        payload={"text": "hello"},
        purpose="session.turn.content",
        record_identity={"session_id": "s1", "record_id": "r1"},
    )
    wrong_ring = FernetSessionKeyRing(active_key_id="k1")

    with pytest.raises(SessionEncryptionKeyError):
        decrypt_session_payload(wrong_ring, envelope, expected_identity={"session_id": "s1", "record_id": "r1"})
    with pytest.raises(SessionEncryptionIdentityError):
        decrypt_session_payload(ring, envelope, expected_identity={"session_id": "s2", "record_id": "r1"})


def test_content_search_rejection_and_migration_checkpoint_schema() -> None:
    with pytest.raises(SessionContentSearchDisabledError):
        assert_content_search_allowed(encryption_enabled=True)
    checkpoint = build_migration_checkpoint(
        checkpoint_id="cp1",
        migrated_count=4,
        last_record_id="r4",
        complete=False,
    )
    assert checkpoint.to_dict()["schema_version"] == "session_encryption_migration.v1"
    assert SECRET_KEY_RING_INTERFACE_VERSION == "secret_key_ring.v1"
