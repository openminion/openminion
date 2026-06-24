from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from openminion.modules.artifact.errors import ArtifactCtlError
from openminion.modules.artifact.models import sha_to_ref
from openminion.modules.artifact.refs import (
    normalize_artifact_ref_targets,
    remove_reference_edges,
)

from .utils import artifact_ctl


def test_alias_set_respects_overwrite_flag(tmp_path):
    with artifact_ctl(tmp_path) as ctl:
        ref_a = ctl.ingest_bytes(b"alpha", original_name="alpha.txt")
        ref_b = ctl.ingest_bytes(b"beta", original_name="beta.txt")

        ctl.alias_set("session:s1/latest", ref_a.sha256)
        with pytest.raises(ArtifactCtlError):
            ctl.alias_set("session:s1/latest", ref_b.sha256, overwrite=False)

        ctl.alias_set("session:s1/latest", ref_b.sha256)
        resolved = ctl.alias_resolve("session:s1/latest")
        assert resolved is not None
        assert resolved.sha256 == ref_b.sha256


def test_alias_expiry_filters_list_and_resolve(tmp_path):
    with artifact_ctl(tmp_path) as ctl:
        ref = ctl.ingest_bytes(b"payload", original_name="payload.bin")
        expires = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        ctl.alias_set("alias:expired", ref.sha256, expires_at=expires)
        assert ctl.alias_resolve("alias:expired") is None
        assert all(item["alias"] != "alias:expired" for item in ctl.alias_list())


def test_reference_edge_idempotency(tmp_path):
    with artifact_ctl(tmp_path) as ctl:
        ref = ctl.ingest_bytes(b"keep", original_name="keep.txt")

        ctl.ref_add("session", "s1", ref.sha256)
        ctl.ref_add("session", "s1", ref.sha256)
        assert ref.sha256 in ctl.index.active_reference_shas()


def test_normalize_artifact_ref_targets_dedupes_mixed_shapes() -> None:
    sha = "a" * 64
    targets = normalize_artifact_ref_targets(
        [sha_to_ref(sha), {"sha256": sha.upper()}, {"ref": sha_to_ref(sha)}]
    )
    assert targets == [sha]


def test_remove_reference_edges_accepts_mixed_target_shapes(tmp_path) -> None:
    with artifact_ctl(tmp_path) as ctl:
        ref = ctl.ingest_bytes(b"keep", original_name="keep.txt")
        ctl.ref_add("session", "s1", ref.sha256)

        remove_reference_edges(
            artifactctl=ctl,
            owner_type="session",
            owner_id="s1",
            ref_values=[sha_to_ref(ref.sha256), {"sha256": ref.sha256.upper()}],
        )

        assert ref.sha256 not in ctl.index.active_reference_shas()

        ctl.ref_remove("session", "s1", ref.sha256)
        assert ref.sha256 not in ctl.index.active_reference_shas()

        ctl.ref_add("session", "s1", ref.sha256)
        assert ref.sha256 in ctl.index.active_reference_shas()
