"""Memory retention scenarios shared by the SQLite and PostgreSQL tests.

The runtime session lifecycle hands the retention helpers its
``AuditedMemoryStore``, so each helper scenario runs on a store or on an audited
wrapper around it.
"""

from __future__ import annotations

import collections
import dataclasses
import datetime
import json
import logging
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner, Result

from openminion import Agent, APIRuntime
from openminion.base.config import ConfigManager, OpenMinionConfig, save_config
from openminion.modules.memory import cli as memory_cli
from openminion.modules.memory.config import from_base_config
from openminion.modules.memory.models import MemoryRecord
from openminion.modules.memory.runtime import session_lifecycle
from openminion.modules.memory.runtime.gc import (
    apply_confidence_decay,
    compress_old_summaries,
    enforce_scope_capacity,
    evict_stale_insights,
)
from openminion.modules.memory.runtime.purge import purge_soft_deleted
from openminion.modules.memory.storage.audit import (
    AuditedMemoryStore,
    InMemoryMemoryAuditSink,
)
from openminion.services.runtime import memory as runtime_memory
from tests._csc_fixtures import _csc_install_default_agent

GC_TARGETS = ("direct", "audited")
LIFECYCLE_HELPERS = (
    "apply_confidence_decay",
    "compress_old_summaries_gc",
    "enforce_scope_capacity",
    "evict_stale_insights",
)


def gc_target(kind: str, store: Any) -> tuple[Any, InMemoryMemoryAuditSink]:
    sink = InMemoryMemoryAuditSink()
    if kind == "direct":
        return store, sink
    return AuditedMemoryStore(store, sink=sink), sink


def _iso_days_ago(days: int) -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    ).isoformat()


def _record(
    record_id: str,
    *,
    scope: str = "agent:retention",
    record_type: str = "fact",
    confidence: float = 0.8,
    age_days: int = 1,
    content: str | dict[str, Any] = "content",
    last_hit_days_ago: int | None = None,
) -> MemoryRecord:
    when = _iso_days_ago(age_days)
    return MemoryRecord(
        id=record_id,
        scope=scope,
        type=record_type,  # type: ignore[arg-type]
        title=record_id,
        content=content,
        created_at=when,
        updated_at=when,
        confidence=confidence,
        last_hit_at=(
            _iso_days_ago(last_hit_days_ago) if last_hit_days_ago is not None else None
        ),
    )


def _summary(
    record_id: str,
    *,
    age_days: int,
    summary_text: str,
    scope: str = "agent:retention",
) -> MemoryRecord:
    return _record(
        record_id,
        scope=scope,
        record_type="session_summary",
        confidence=1.0,
        age_days=age_days,
        content={
            "decisions": ["decided to use pytest"],
            "open_questions": [],
            "corrections": [],
            "topic_keywords": ["pytest"],
            "turn_count": 4,
            "summary_text": summary_text,
        },
    )


def _check(actual: Any, expected: Any) -> None:
    # Pytest does not rewrite asserts in this shared module: show both sides.
    assert actual == expected, f"\nactual:   {actual!r}\nexpected: {expected!r}"


def _deleted_reason(store: Any, record_id: str) -> str | None:
    record = store.get(record_id)
    assert record is not None, record_id
    return record.deleted_reason if record.is_deleted else None


def _deleted_reasons(store: Any, *record_ids: str) -> dict[str, str | None]:
    return {record_id: _deleted_reason(store, record_id) for record_id in record_ids}


def check_confidence_decay(
    target: Any, store: Any, sink: InMemoryMemoryAuditSink
) -> None:
    store.put(_record("decays", confidence=0.8, age_days=28))
    store.put(_record("evicted", confidence=0.35, age_days=28))
    store.put(_record("recent", confidence=0.8, age_days=1))
    store.put(_record("pinned", record_type="pin", confidence=0.8, age_days=28))
    store.put(_record("in-session", scope="session:s1", confidence=0.8, age_days=28))

    result = apply_confidence_decay(
        target, interval_days=7, decay_rate=0.05, min_confidence=0.3
    )

    record_ids = ("decays", "evicted", "recent", "pinned", "in-session")
    _check(
        (
            result,
            {
                record_id: (
                    round(store.get(record_id).confidence, 3),
                    _deleted_reason(store, record_id),
                )
                for record_id in record_ids
            },
            sink.events,
        ),
        (
            (2, 1),
            {
                # 28 days at 0.05 per 7 days.
                "decays": (0.6, None),
                "evicted": (0.15, "confidence_below_threshold"),
                "recent": (0.8, None),
                "pinned": (0.8, None),
                "in-session": (0.8, None),
            },
            [],
        ),
    )


def check_summary_compression(
    target: Any, store: Any, sink: InMemoryMemoryAuditSink
) -> None:
    store.put(
        _summary(
            "summary-old",
            age_days=20,
            summary_text="Decided to use pytest. Actually, wrong fixture scope.",
        )
    )
    store.put(_summary("summary-expired", age_days=100, summary_text="Old session."))
    store.put(
        _summary(
            "summary-recent",
            age_days=2,
            summary_text="Recent session. Keep every sentence.",
        )
    )

    result = compress_old_summaries(
        target, max_age_days=14, delete_age_days=90, max_summary_chars=100
    )

    old = store.get("summary-old").content
    _check(
        (
            result,
            old["summary_text"],
            old["decisions"],
            store.get("summary-recent").content["summary_text"],
            _deleted_reasons(store, "summary-old", "summary-expired", "summary-recent"),
            sink.events,
        ),
        (
            (1, 1),
            "Decided to use pytest",
            ["decided to use pytest"],
            "Recent session. Keep every sentence.",
            {
                "summary-old": None,
                "summary-expired": "summary_retention",
                "summary-recent": None,
            },
            [],
        ),
    )


def check_scope_capacity(
    target: Any, store: Any, sink: InMemoryMemoryAuditSink
) -> None:
    for index, confidence in enumerate((0.4, 0.5, 0.6, 0.7, 0.8)):
        store.put(
            _record(f"fact-{index}", scope="agent:capacity", confidence=confidence)
        )
    store.put(_record("pin", scope="agent:capacity", record_type="pin", confidence=0.1))
    store.put(_record("other-1", scope="agent:other", confidence=0.1))
    store.put(_record("other-2", scope="agent:other", confidence=0.1))

    result = enforce_scope_capacity(target, max_records=4)

    record_ids = ("fact-0", "fact-1", "fact-2", "fact-3", "fact-4", "pin")
    _check(
        (
            result,
            _deleted_reasons(store, *record_ids, "other-1", "other-2"),
            sink.events,
        ),
        (
            {"agent:capacity": 2},
            {
                "fact-0": "scope_capacity",
                "fact-1": "scope_capacity",
                "fact-2": None,
                "fact-3": None,
                "fact-4": None,
                "pin": None,
                "other-1": None,
                "other-2": None,
            },
            [],
        ),
    )


def check_stale_insight_eviction(
    target: Any, store: Any, sink: InMemoryMemoryAuditSink
) -> None:
    store.put(_record("stale", record_type="meta_insight", age_days=100))
    store.put(
        _record(
            "old-but-used",
            record_type="meta_insight",
            age_days=100,
            last_hit_days_ago=5,
        )
    )
    store.put(_record("fresh", record_type="meta_insight", age_days=2))
    store.put(_record("old-fact", record_type="fact", age_days=100))

    result = evict_stale_insights(target, staleness_days=60)

    _check(
        (
            result,
            _deleted_reasons(store, "stale", "old-but-used", "fresh", "old-fact"),
            sink.events,
        ),
        (
            1,
            {
                "stale": "stale_insight",
                "old-but-used": None,
                "fresh": None,
                "old-fact": None,
            },
            [],
        ),
    )


def check_purge_soft_deleted(
    target: Any, store: Any, sink: InMemoryMemoryAuditSink
) -> None:
    # memctl gc purges through the AuditedMemoryStore it builds (#401).
    store.put(_record("kept"))
    store.put(_record("purged"))
    store.delete("purged")

    result = purge_soft_deleted(target)

    kept = store.get("kept")
    _check(
        (
            (result.deleted_records, result.deleted_candidates),
            kept is not None and not kept.is_deleted,
            store.get("purged"),
            sink.events,
        ),
        ((1, 0), True, None, []),
    )


RETENTION_SCENARIOS = (
    pytest.param(check_confidence_decay, id="decay"),
    pytest.param(check_summary_compression, id="summaries"),
    pytest.param(check_scope_capacity, id="capacity"),
    pytest.param(check_stale_insight_eviction, id="insights"),
    pytest.param(check_purge_soft_deleted, id="purge"),
)


def seed_memctl_gc_records(store: Any) -> None:
    """A fact that decays from 0.8 to 0.6 and a soft-deleted one to purge."""
    store.put(_record("old", scope="agent:a", confidence=0.8, age_days=28))
    store.put(_record("deleted", scope="agent:a", confidence=0.8, age_days=28))
    store.delete("deleted")


MEMCTL_SECTIONS: dict[str, Any] = {
    "defaults": {
        "confidence": {
            "user_said": 0.7,
            "tool_output": 0.8,
            "agent_inferred": 0.4,
            "validated": 0.95,
            "imported": 0.9,
        }
    },
    "promotion": {
        "require_approval_for_global": True,
        "auto_promote_agent_procedures": True,
        "allowlisted_auto_rules": [],
    },
    "retrieval": {"max_results": 25, "min_confidence_default": 0.55, "pin_first": True},
    "retention": {"enable_soft_delete": True, "gc_enabled": True, "gc_batch_size": 500},
}


def run_memctl_gc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, store_config: dict[str, Any]
) -> Result:
    """Run the real `memctl gc` with a typed memctl config file for its store.

    No `--db`: memctl's `_get_service` builds the store from the config, wraps
    it in its `AuditedMemoryStore`, and `gc` runs the retention pass too.
    """
    config_path = tmp_path / "memory.yaml"
    config = {"version": 1, "memctl": {**MEMCTL_SECTIONS, "store": store_config}}
    config_path.write_text(json.dumps(config), encoding="utf-8")  # JSON is YAML
    monkeypatch.setenv("MEMCTL_CONFIG", str(config_path))
    return CliRunner().invoke(memory_cli._build_app(), ["gc"])


def memctl_gc_rows(store: Any) -> dict[str, Any]:
    old = store.get("old")
    return {
        "old": (round(old.confidence, 3), old.is_deleted),
        "deleted": store.get("deleted"),
    }


def _lifecycle_runtime(root: Path, memory_url: str | None) -> APIRuntime:
    config = OpenMinionConfig()
    _csc_install_default_agent(config, provider="echo")
    config.runtime.process_mode = "single-process"
    config.runtime.daemon_auto_start = False
    config.storage.path = str(root / ".openminion" / "state.db")
    config_path = root / "agents.json"
    save_config(config, str(config_path))
    manager = ConfigManager.load(
        str(config_path), home_root=root, data_root=root / ".openminion"
    )

    def memory_config(**kwargs):
        memory = from_base_config(**kwargs)
        retention = dataclasses.replace(memory.retention, max_records_per_scope=3)
        memory = dataclasses.replace(memory, retention=retention)
        if memory_url is None:
            return memory
        store = dataclasses.replace(
            memory.store, backend="postgres", postgres_url=memory_url
        )
        return dataclasses.replace(memory, store=store)

    manager.register("memory", memory_config)
    return APIRuntime.from_manager(manager)


def _insight(record_id: str) -> MemoryRecord:
    # Old enough to be stale (60 days) but not to decay below 0.3 first.
    return _record(
        record_id,
        record_type="meta_insight",
        scope="agent:insights",
        confidence=1.0,
        age_days=70,
    )


def _seed_lifecycle_records(store: Any) -> None:
    store.put(_record("decays", scope="agent:decay", confidence=0.8, age_days=28))
    store.put(
        _record("decay-evicted", scope="agent:decay", confidence=0.35, age_days=28)
    )
    summary_text = "Decided to use pytest. Actually, wrong fixture scope."
    store.put(
        _summary(
            "summary-old",
            scope="agent:summaries",
            age_days=20,
            summary_text=summary_text,
        )
    )
    # Past summary deletion (90 days) but not decayed below 0.3 first.
    store.put(
        _summary(
            "summary-expired",
            scope="agent:summaries",
            age_days=95,
            summary_text=summary_text,
        )
    )
    for index, confidence in enumerate((0.4, 0.5, 0.6, 0.7, 0.8)):
        store.put(
            _record(f"cap-{index}", scope="agent:capacity", confidence=confidence)
        )
    store.put(_insight("stale-insight"))


def check_session_lifecycle_runs_once(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    memory_url: str | None,
) -> None:
    stores: list[Any] = []
    resolve_backend = runtime_memory.resolve_memory_backend

    def seeded_backend(**kwargs):
        resolved = resolve_backend(**kwargs)
        _seed_lifecycle_records(resolved.store)
        stores.append(resolved.store)
        return resolved

    monkeypatch.setattr(runtime_memory, "resolve_memory_backend", seeded_backend)
    calls: list[tuple[str, str, str]] = []

    def recording(name: str, helper: Any) -> Any:
        def record_call(store: Any, **kwargs: Any) -> Any:
            try:
                result = helper(store, **kwargs)
            except Exception as exc:
                calls.append((name, type(store).__name__, f"raised {exc!r}"))
                raise
            calls.append((name, type(store).__name__, "ok"))
            return result

        return record_call

    for name in LIFECYCLE_HELPERS:
        monkeypatch.setattr(
            session_lifecycle, name, recording(name, getattr(session_lifecycle, name))
        )

    runtime = _lifecycle_runtime(root, memory_url)
    try:
        with caplog.at_level(logging.WARNING):
            with Agent(runtime=runtime) as agent:
                assert agent.run("hello").run_state == "completed"
                [store] = stores
                calls_after_first_turn = list(calls)
                store.put(_insight("stale-insight-2"))
                assert agent.run("hello again").run_state == "completed"
        [assembly] = list(runtime._memory_assemblies.values())
        _check(type(assembly.service._store), AuditedMemoryStore)
        _check(assembly.service._store._store is store, True)

        lifecycle_warnings = [
            record.getMessage()
            for record in caplog.records
            if record.getMessage().startswith("memory.session_lifecycle")
        ]
        _check(lifecycle_warnings, [])
        # Every call returned, on the runtime's audited store. The first turn
        # compresses summaries for its preamble and runs the session lifecycle;
        # the second turn of the same session runs neither.
        _check(
            collections.Counter(calls),
            {
                ("apply_confidence_decay", "AuditedMemoryStore", "ok"): 1,
                ("compress_old_summaries_gc", "AuditedMemoryStore", "ok"): 2,
                ("enforce_scope_capacity", "AuditedMemoryStore", "ok"): 1,
                ("evict_stale_insights", "AuditedMemoryStore", "ok"): 1,
            },
        )
        _check(calls, calls_after_first_turn)

        _check(
            (
                round(store.get("decays").confidence, 3),
                store.get("summary-old").content["summary_text"],
                _deleted_reasons(
                    store,
                    "decays",
                    "decay-evicted",
                    "summary-old",
                    "summary-expired",
                    *(f"cap-{index}" for index in range(5)),
                    "stale-insight",
                    "stale-insight-2",
                ),
            ),
            (
                0.6,
                "Decided to use pytest",
                {
                    "decays": None,
                    "decay-evicted": "confidence_below_threshold",
                    "summary-old": None,
                    "summary-expired": "summary_retention",
                    "cap-0": "scope_capacity",
                    "cap-1": "scope_capacity",
                    "cap-2": None,
                    "cap-3": None,
                    "cap-4": None,
                    "stale-insight": "stale_insight",
                    # Seeded after the first turn: the lifecycle does not run again.
                    "stale-insight-2": None,
                },
            ),
        )
    finally:
        runtime.close()
