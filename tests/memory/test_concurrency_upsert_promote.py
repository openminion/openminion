import threading
from openminion.modules.memory.models import MemoryCandidate
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore


def _run_threads(threads) -> None:
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


def _active_fact_count(store: SQLiteMemoryStore, *, scope: str, key: str) -> int:
    with store._connect() as conn:
        cursor = conn.execute(
            "SELECT COUNT(*) as cnt FROM memory_records WHERE scope=? "
            "AND type='fact' AND key=? AND is_deleted=0",
            (scope, key),
        )
        return cursor.fetchone()["cnt"]


def test_concurrent_upserts_same_key(tmp_path) -> None:
    store = SQLiteMemoryStore(tmp_path / "test.db")
    errors = []

    def do_upsert(i: int) -> None:
        try:
            store.upsert(
                scope="session:s1",
                type="fact",
                key="shared-key",
                record_patch={
                    "content": f"version {i}",
                    "confidence": 0.5 + i * 0.01,
                },
            )
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=do_upsert, args=(i,)) for i in range(10)]
    _run_threads(threads)
    count = _active_fact_count(store, scope="session:s1", key="shared-key")

    assert count == 1, f"Expected 1 active row, got {count}. Errors: {errors}"


def test_concurrent_candidate_promotions_same_key(tmp_path) -> None:
    store = SQLiteMemoryStore(tmp_path / "test.db")
    for i in range(5):
        store.candidate_put(
            MemoryCandidate(
                candidate_id=f"c{i}",
                session_id="s1",
                proposed_scope="session:s1",
                type="fact",
                content=f"fact {i}",
                status="approved",
                key="shared-rec",
            )
        )

    errors = []

    def do_promote(candidate_id: str) -> None:
        try:
            store.promote_candidate(candidate_id, "global:all")
        except ValueError:
            pass
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=do_promote, args=(f"c{i}",)) for i in range(5)]
    _run_threads(threads)

    assert errors == [], f"Unexpected errors: {errors}"
    count = _active_fact_count(store, scope="global:all", key="shared-rec")

    assert count == 1, f"Expected 1 active promoted row, got {count}"
