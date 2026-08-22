import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from openminion.modules.skill.storage.store import SQLiteSkillStore


def _insert(store: SQLiteSkillStore, version: str, status: str = "draft") -> None:
    store.upsert_skill(
        skill_id="deploy",
        name="Deploy",
        status=status,
        scope="global",
        agent_id=None,
        ts="2026-01-01T00:00:00Z",
    )
    store.insert_skill_version(
        skill_id="deploy",
        version_hash=version,
        source_artifact_ref=f"artifact://{version}",
        package_json=json.dumps(
            {"skill_id": "deploy", "version_hash": version, "status": "draft"}
        ),
        content_fingerprint=f"content-{version}",
        created_at=f"2026-01-0{version[-1]}T00:00:00Z",
    )


def test_active_version_controls_implicit_reads_without_mutating_package(
    tmp_path: Path,
) -> None:
    store = SQLiteSkillStore(tmp_path / "skill.db", wal=False)
    try:
        _insert(store, "v1")
        _insert(store, "v2")

        assert store.activate_skill_version(
            skill_id="deploy",
            version_hash="v1",
            expected_active_version_hash=None,
            target_status="verified",
            authority_class="local_operator",
            reviewer_id="operator",
            reason="approve",
            decided_at="2026-01-03T00:00:00Z",
        )
        assert store.get_skill_package("deploy")["version_hash"] == "v1"
        assert store.get_skill_package("deploy")["status"] == "verified"
        assert store.get_skill_package("deploy", "v1")["status"] == "draft"
    finally:
        store.close()


def test_activation_compare_and_swap_rejects_stale_writer(tmp_path: Path) -> None:
    store = SQLiteSkillStore(tmp_path / "skill.db", wal=False)
    try:
        _insert(store, "v1")
        _insert(store, "v2")
        assert store.activate_skill_version(
            skill_id="deploy",
            version_hash="v1",
            expected_active_version_hash=None,
            target_status="verified",
            authority_class="local_operator",
            reviewer_id="operator",
            reason="approve",
            decided_at="2026-01-03T00:00:00Z",
        )
        assert not store.activate_skill_version(
            skill_id="deploy",
            version_hash="v2",
            expected_active_version_hash=None,
            target_status="verified",
            authority_class="local_operator",
            reviewer_id="operator",
            reason="stale",
            decided_at="2026-01-04T00:00:00Z",
        )
        assert store.activate_skill_version(
            skill_id="deploy",
            version_hash="v2",
            expected_active_version_hash="v1",
            target_status="blessed",
            authority_class="local_operator",
            reviewer_id="operator",
            reason="replace",
            decided_at="2026-01-04T00:00:00Z",
        )
        assert store.get_skill_package("deploy")["version_hash"] == "v2"
    finally:
        store.close()


def test_reopening_store_does_not_activate_pending_version(tmp_path: Path) -> None:
    db_path = tmp_path / "skill.db"
    store = SQLiteSkillStore(db_path, wal=False)
    try:
        _insert(store, "v1")
        store.stage_skill_version(
            skill_id="deploy",
            version_hash="v1",
            content_fingerprint="content-v1",
            authority_class="local_operator",
            created_at="2026-01-01T00:00:00Z",
        )
    finally:
        store.close()

    reopened = SQLiteSkillStore(db_path, wal=False)
    try:
        assert reopened.get_active_skill_version_hash(skill_id="deploy") is None
        admission = reopened.get_skill_admission(
            skill_id="deploy", version_hash="v1"
        )
        assert admission is not None
        assert admission["state"] == "pending"
    finally:
        reopened.close()


def test_activation_compare_and_swap_has_one_winner_across_connections(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "skill.db"
    seed = SQLiteSkillStore(db_path, wal=True)
    try:
        _insert(seed, "v1")
        _insert(seed, "v2")
        for version_hash in ("v1", "v2"):
            seed.stage_skill_version(
                skill_id="deploy",
                version_hash=version_hash,
                content_fingerprint=f"content-{version_hash}",
                authority_class="local_operator",
                created_at="2026-01-01T00:00:00Z",
            )
    finally:
        seed.close()

    def _activate(version_hash: str) -> bool:
        store = SQLiteSkillStore(db_path, wal=True)
        try:
            return store.activate_skill_version(
                skill_id="deploy",
                version_hash=version_hash,
                expected_active_version_hash=None,
                target_status="verified",
                authority_class="local_operator",
                reviewer_id="operator",
                reason="race",
                decided_at="2026-01-03T00:00:00Z",
            )
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(_activate, ("v1", "v2")))

    assert sorted(results) == [False, True]
