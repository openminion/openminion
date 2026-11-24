from __future__ import annotations

import json
from pathlib import Path

import pytest

from openminion.modules.storage.cli import main as storage_main
from openminion.modules.storage.migrations.module_ids import get_module_application_id
from openminion.modules.storage.migrations import export_omx, import_omx


def _create_sample_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    import sqlite3

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        conn.execute("INSERT INTO sample (name) VALUES ('alpha'), ('beta')")
        conn.commit()


class _RaisingReporter:
    def on_start(self, **_kwargs) -> None:
        raise RuntimeError("reporter start failed")

    def on_progress(self, **_kwargs) -> None:
        raise RuntimeError("reporter progress failed")

    def on_end(self, **_kwargs) -> None:
        raise RuntimeError("reporter end failed")


def test_omx_roundtrip_via_helpers(tmp_path: Path) -> None:
    db_path = tmp_path / "sample.db"
    _create_sample_db(db_path)

    omx_dir = tmp_path / "omx"
    manifest = export_omx(
        db_path=db_path,
        module_id="task",
        module_application_id=get_module_application_id("task"),
        export_dir=omx_dir,
        export_notes="test",
    )
    assert (omx_dir / "manifest.json").exists()
    assert manifest.module_id == "task"

    restored_path = tmp_path / "restored.db"
    report = import_omx(
        omx_dir=omx_dir, target_db_path=restored_path, verify_checksums=True
    )
    assert report.success is True


def test_omx_reporter_failures_are_best_effort(tmp_path: Path) -> None:
    db_path = tmp_path / "sample.db"
    _create_sample_db(db_path)

    omx_dir = tmp_path / "omx"
    export_omx(
        db_path=db_path,
        module_id="task",
        module_application_id=get_module_application_id("task"),
        export_dir=omx_dir,
        reporter=_RaisingReporter(),
    )

    restored_path = tmp_path / "restored.db"
    report = import_omx(
        omx_dir=omx_dir,
        target_db_path=restored_path,
        verify_checksums=True,
        reporter=_RaisingReporter(),
    )

    assert report.success is True


def test_omx_roundtrip_via_cli(tmp_path: Path, capsys) -> None:
    db_path = tmp_path / "sample.db"
    _create_sample_db(db_path)

    omx_dir = tmp_path / "omx-cli"
    storage_main(
        [
            "export",
            "--namespace",
            "task",
            "--sqlite",
            str(db_path),
            "--out",
            str(omx_dir),
        ]
    )
    export_out = capsys.readouterr().out.strip()
    assert export_out, "export should emit JSON"
    export_payload = json.loads(export_out)
    assert export_payload["ok"] is True

    restored_path = tmp_path / "restored.db"
    storage_main(
        [
            "import",
            "--namespace",
            "task",
            "--sqlite",
            str(restored_path),
            "--input",
            str(omx_dir),
        ]
    )
    import_out = capsys.readouterr().out.strip()
    assert import_out, "import should emit JSON"
    import_payload = json.loads(import_out)
    assert import_payload["ok"] is True
    assert restored_path.exists()


def test_omx_roundtrip_preserves_keyword_columns_and_sqlite_pragmas(
    tmp_path: Path,
) -> None:
    import sqlite3

    task_app_id = get_module_application_id("task")
    source_path = tmp_path / "keyword.db"
    with sqlite3.connect(str(source_path)) as conn:
        conn.execute(f"PRAGMA application_id={task_app_id}")
        conn.execute("PRAGMA user_version=7")
        conn.execute(
            'CREATE TABLE queue_items ("id" INTEGER PRIMARY KEY, '
            '"order" INTEGER NOT NULL, "group" TEXT NOT NULL)'
        )
        conn.executemany(
            'INSERT INTO queue_items ("id", "order", "group") VALUES (?, ?, ?)',
            [(1, 10, "alpha"), (2, 20, "beta")],
        )
        conn.commit()

    omx_dir = tmp_path / "omx-keyword"
    export_omx(
        db_path=source_path,
        module_id="task",
        module_application_id=task_app_id,
        export_dir=omx_dir,
    )
    manifest_path = omx_dir / "manifest.json"
    manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_payload["tables"][0]["path"] = "tables\\queue_items.jsonl"
    manifest_path.write_text(json.dumps(manifest_payload), encoding="utf-8")

    restored_path = tmp_path / "restored-keyword.db"
    report = import_omx(omx_dir=omx_dir, target_db_path=restored_path)
    assert report.success is True, report.error

    with sqlite3.connect(str(restored_path)) as conn:
        rows = conn.execute(
            'SELECT "id", "order", "group" FROM queue_items ORDER BY "id"'
        ).fetchall()
        app_id = conn.execute("PRAGMA application_id").fetchone()[0]
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]

    assert rows == [(1, 10, "alpha"), (2, 20, "beta")]
    assert app_id == task_app_id
    assert user_version == 7


@pytest.mark.parametrize(
    "bad_path",
    [
        "../outside.jsonl",
        "..\\outside.jsonl",
        "tables/../../outside.jsonl",
        "",
        ".",
    ],
)
def test_import_omx_rejects_out_of_bundle_paths(tmp_path: Path, bad_path: str) -> None:
    db_path = tmp_path / "sample.db"
    _create_sample_db(db_path)
    omx_dir = tmp_path / "omx"
    export_omx(
        db_path=db_path,
        module_id="task",
        module_application_id=get_module_application_id("task"),
        export_dir=omx_dir,
    )
    outside = tmp_path / "outside.jsonl"
    outside.write_text('{"id": 99, "name": "outside"}\n', encoding="utf-8")

    manifest_path = omx_dir / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["tables"][0]["path"] = bad_path
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    report = import_omx(
        omx_dir=omx_dir,
        target_db_path=tmp_path / "restored.db",
        verify_checksums=False,
    )
    assert report.success is False
    assert "escapes bundle directory" in str(report.error)


def test_import_omx_rejects_absolute_and_symlink_escaped_table_paths(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "sample.db"
    _create_sample_db(db_path)
    omx_dir = tmp_path / "omx"
    export_omx(
        db_path=db_path,
        module_id="task",
        module_application_id=get_module_application_id("task"),
        export_dir=omx_dir,
    )
    outside = tmp_path / "outside.jsonl"
    outside.write_text('{"id": 99, "name": "outside"}\n', encoding="utf-8")
    manifest_path = omx_dir / "manifest.json"

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["tables"][0]["path"] = str(outside)
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    abs_report = import_omx(
        omx_dir=omx_dir,
        target_db_path=tmp_path / "abs.db",
        verify_checksums=False,
    )
    assert abs_report.success is False
    assert "escapes bundle directory" in str(abs_report.error)

    link_path = omx_dir / "tables" / "escaped.jsonl"
    link_path.symlink_to(outside)
    payload["tables"][0]["path"] = "tables/escaped.jsonl"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    sym_report = import_omx(
        omx_dir=omx_dir,
        target_db_path=tmp_path / "sym.db",
        verify_checksums=False,
    )
    assert sym_report.success is False
    assert "escapes bundle directory" in str(sym_report.error)


def test_import_omx_rejects_heterogeneous_rows_before_deleting_seeded_targets(
    tmp_path: Path,
) -> None:
    import hashlib
    import sqlite3
    from openminion.modules.storage.record_store import RecordStoreSQLite

    nullable_db = tmp_path / "nullable.db"
    with sqlite3.connect(str(nullable_db)) as conn:
        conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
        conn.commit()
    omx_dir = tmp_path / "omx-nullable"
    export_omx(
        db_path=nullable_db,
        module_id="task",
        module_application_id=get_module_application_id("task"),
        export_dir=omx_dir,
    )
    notes_file = omx_dir / "tables" / "notes.jsonl"
    manifest_path = omx_dir / "manifest.json"

    notes_file.write_text('{"id": 1}\n{"id": 2, "body": "kept"}\n', encoding="utf-8")
    seeded_sqlite = tmp_path / "seeded-sqlite.db"
    with sqlite3.connect(str(seeded_sqlite)) as conn:
        conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
        conn.execute("INSERT INTO notes (id, body) VALUES (7, 'seeded-sqlite')")
        conn.commit()
    het_sqlite = import_omx(
        omx_dir=omx_dir,
        target_db_path=seeded_sqlite,
        verify_checksums=False,
    )
    assert het_sqlite.success is False
    assert "same columns" in str(het_sqlite.error)
    with sqlite3.connect(str(seeded_sqlite)) as conn:
        assert conn.execute("SELECT id, body FROM notes").fetchall() == [
            (7, "seeded-sqlite")
        ]

    seeded_store_path = tmp_path / "seeded-store.db"
    store = RecordStoreSQLite(seeded_store_path)
    try:
        store.execute_count("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
        store.insert("notes", {"id": 9, "body": "seeded-store"})
        het_store = import_omx(
            omx_dir=omx_dir,
            target_record_store=store,
            verify_checksums=False,
        )
        assert het_store.success is False
        assert het_store.imported_rows == 0
        assert "same columns" in str(het_store.error)
        assert store.query_dicts("SELECT id, body FROM notes") == [
            {"id": 9, "body": "seeded-store"}
        ]

        notes_file.write_text('{"id": 1, "bad col": "x"}\n', encoding="utf-8")
        bad_col_store = import_omx(
            omx_dir=omx_dir,
            target_record_store=store,
            verify_checksums=False,
        )
        assert bad_col_store.success is False
        assert "Invalid SQL identifier" in str(bad_col_store.error)
        assert store.query_dicts("SELECT id, body FROM notes") == [
            {"id": 9, "body": "seeded-store"}
        ]
    finally:
        store.close()

    multi_src = tmp_path / "multi-src.db"
    with sqlite3.connect(str(multi_src)) as conn:
        conn.execute("CREATE TABLE a_items (id INTEGER PRIMARY KEY, body TEXT)")
        conn.execute(
            "CREATE TABLE z_items (id INTEGER PRIMARY KEY, body TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO a_items VALUES (1, 'bundle-a')")
        conn.execute("INSERT INTO z_items VALUES (1, 'bundle-z')")
        conn.commit()
    multi_omx = tmp_path / "multi-omx"
    export_omx(
        db_path=multi_src,
        module_id="task",
        module_application_id=get_module_application_id("task"),
        export_dir=multi_omx,
    )
    (multi_omx / "tables" / "z_items.jsonl").write_text(
        '{"id": 1, "body": null}\n', encoding="utf-8"
    )
    tx_store = RecordStoreSQLite(tmp_path / "tx-store.db")
    try:
        tx_store.execute_count(
            "CREATE TABLE a_items (id INTEGER PRIMARY KEY, body TEXT)"
        )
        tx_store.execute_count(
            "CREATE TABLE z_items (id INTEGER PRIMARY KEY, body TEXT NOT NULL)"
        )
        tx_store.insert("a_items", {"id": 5, "body": "keep-a"})
        tx_store.insert("z_items", {"id": 6, "body": "keep-z"})
        tx_report = import_omx(
            omx_dir=multi_omx,
            target_record_store=tx_store,
            verify_checksums=False,
        )
        assert tx_report.success is False
        assert tx_report.imported_rows == 0
        assert tx_store.query_dicts("SELECT id, body FROM a_items") == [
            {"id": 5, "body": "keep-a"}
        ]
        assert tx_store.query_dicts("SELECT id, body FROM z_items") == [
            {"id": 6, "body": "keep-z"}
        ]
    finally:
        tx_store.close()

    notes_file.write_text('[1, "alpha"]\n', encoding="utf-8")
    list_report = import_omx(
        omx_dir=omx_dir,
        target_db_path=tmp_path / "list.db",
        verify_checksums=False,
    )
    assert list_report.success is False
    assert "JSON object" in str(list_report.error)

    notes_file.write_text('{"id": 1, "body": "only-one"}\n', encoding="utf-8")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["tables"][0]["sha256"] = hashlib.sha256(notes_file.read_bytes()).hexdigest()
    payload["tables"][0]["row_count"] = 5
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    count_report = import_omx(
        omx_dir=omx_dir,
        target_db_path=tmp_path / "count.db",
        verify_checksums=True,
    )
    assert count_report.success is False
    assert "Row count mismatch" in str(count_report.error)

    unverified_report = import_omx(
        omx_dir=omx_dir,
        target_db_path=tmp_path / "count-unverified.db",
        verify_checksums=False,
    )
    assert unverified_report.success is True

    payload["tables"][0]["name"] = "notes; DROP TABLE notes"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    bad_sqlite = import_omx(
        omx_dir=omx_dir,
        target_db_path=tmp_path / "bad-ident.db",
        verify_checksums=False,
    )
    assert bad_sqlite.success is False
    assert "Invalid SQL identifier" in str(bad_sqlite.error)

    bad_store = RecordStoreSQLite(tmp_path / "bad-store.db")
    try:
        bad_store_report = import_omx(
            omx_dir=omx_dir,
            target_record_store=bad_store,
            verify_checksums=False,
        )
    finally:
        bad_store.close()
    assert bad_store_report.success is False
    assert "Invalid SQL identifier" in str(bad_store_report.error)


def test_import_omx_enforces_owned_and_unowned_application_ids_and_verification(
    tmp_path: Path, monkeypatch
) -> None:
    import sqlite3
    from openminion.modules.storage.migrations import MigrationRunner
    from openminion.modules.storage.migrations.errors import RehydrateError
    from openminion.modules.storage.migrations.models import (
        Finding,
        VerificationReport,
    )
    from openminion.modules.storage.migrations import transfer
    from openminion.modules.storage.record_store import RecordStoreSQLite

    db_path = tmp_path / "sample.db"
    _create_sample_db(db_path)
    omx_dir = tmp_path / "omx"
    task_app_id = get_module_application_id("task")
    secret_app_id = get_module_application_id("secret")
    export_omx(
        db_path=db_path,
        module_id="task",
        module_application_id=task_app_id,
        export_dir=omx_dir,
    )

    unowned_omx = tmp_path / "omx-unowned"
    export_omx(
        db_path=db_path,
        module_id="task",
        module_application_id=0,
        export_dir=unowned_omx,
    )

    foreign_db = tmp_path / "foreign.db"
    with sqlite3.connect(str(foreign_db)) as conn:
        conn.execute(f"PRAGMA application_id={secret_app_id}")
        conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        conn.execute("INSERT INTO sample (id, name) VALUES (1, 'secret-owned')")
        conn.commit()

    for candidate_omx in (omx_dir, unowned_omx):
        mismatch_report = import_omx(omx_dir=candidate_omx, target_db_path=foreign_db)
        assert mismatch_report.success is False
        assert "application_id mismatch" in str(mismatch_report.error)
        with sqlite3.connect(str(foreign_db)) as conn:
            assert conn.execute("SELECT id, name FROM sample").fetchall() == [
                (1, "secret-owned")
            ]
            assert conn.execute("PRAGMA application_id").fetchone()[0] == secret_app_id

    foreign_store = RecordStoreSQLite(foreign_db)
    try:
        zero_into_store = import_omx(
            omx_dir=unowned_omx, target_record_store=foreign_store
        )
        assert zero_into_store.success is False
        assert "application_id mismatch" in str(zero_into_store.error)
        assert foreign_store.query_dicts("SELECT id, name FROM sample") == [
            {"id": 1, "name": "secret-owned"}
        ]
    finally:
        foreign_store.close()

    fresh_unowned = tmp_path / "fresh-unowned.db"
    unowned_ok = import_omx(omx_dir=unowned_omx, target_db_path=fresh_unowned)
    assert unowned_ok.success is True, unowned_ok.error
    with sqlite3.connect(str(fresh_unowned)) as conn:
        assert conn.execute("PRAGMA application_id").fetchone()[0] == 0
        assert conn.execute("SELECT name FROM sample ORDER BY id").fetchall() == [
            ("alpha",),
            ("beta",),
        ]

    matching_owned = tmp_path / "matching-owned.db"
    with sqlite3.connect(str(matching_owned)) as conn:
        conn.execute(f"PRAGMA application_id={task_app_id}")
        conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        conn.execute("INSERT INTO sample (id, name) VALUES (99, 'stale')")
        conn.commit()
    matching_ok = import_omx(omx_dir=omx_dir, target_db_path=matching_owned)
    assert matching_ok.success is True, matching_ok.error
    with sqlite3.connect(str(matching_owned)) as conn:
        assert conn.execute("PRAGMA application_id").fetchone()[0] == task_app_id
        assert conn.execute("SELECT name FROM sample ORDER BY id").fetchall() == [
            ("alpha",),
            ("beta",),
        ]

    fatal_verification = VerificationReport(
        module_id="task",
        db_path=str(tmp_path / "failed-verify.db"),
        level="quick",
        quick_check="corrupt",
        integrity_check=None,
        findings=[
            Finding(
                severity="fatal",
                code="quick_check_failed",
                message="PRAGMA quick_check returned non-ok result.",
            )
        ],
        ok=False,
    )
    monkeypatch.setattr(
        transfer, "run_verification", lambda **_kwargs: fatal_verification
    )
    verify_report = import_omx(
        omx_dir=omx_dir, target_db_path=tmp_path / "failed-verify.db"
    )
    assert verify_report.success is False
    assert verify_report.imported_rows == 2
    assert verify_report.verification is fatal_verification
    assert "quick_check" in str(verify_report.error)

    runner = MigrationRunner(
        module_id="task",
        db_path=tmp_path / "rehydrate.db",
        module_application_id=task_app_id,
    )
    with pytest.raises(RehydrateError, match="quick_check"):
        runner.fallback_rehydrate(
            source_db_path=db_path,
            target_db_path=tmp_path / "rehydrate.db",
            omx_dir=omx_dir,
        )


def test_import_omx_refuses_caller_owned_transaction_and_rolls_back_owned_failure(
    tmp_path: Path,
) -> None:
    import hashlib
    import json
    import sqlite3
    from openminion.modules.storage.record_store import RecordStoreSQLite

    src_db = tmp_path / "src-notes.db"
    with sqlite3.connect(str(src_db)) as conn:
        conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT NOT NULL)")
        conn.execute("INSERT INTO notes (id, body) VALUES (1, 'alpha'), (2, 'beta')")
        conn.commit()

    valid_omx = tmp_path / "valid-omx"
    export_omx(
        db_path=src_db,
        module_id="task",
        module_application_id=get_module_application_id("task"),
        export_dir=valid_omx,
    )

    dup_omx = tmp_path / "dup-omx"
    export_omx(
        db_path=src_db,
        module_id="task",
        module_application_id=get_module_application_id("task"),
        export_dir=dup_omx,
    )
    dup_notes = dup_omx / "tables" / "notes.jsonl"
    dup_notes.write_text(
        '{"id": 1, "body": "first"}\n{"id": 1, "body": "duplicate"}\n',
        encoding="utf-8",
    )
    dup_manifest_path = dup_omx / "manifest.json"
    dup_manifest = json.loads(dup_manifest_path.read_text(encoding="utf-8"))
    dup_manifest["tables"][0]["sha256"] = hashlib.sha256(
        dup_notes.read_bytes()
    ).hexdigest()
    dup_manifest["tables"][0]["row_count"] = 2
    dup_manifest_path.write_text(json.dumps(dup_manifest), encoding="utf-8")

    store = RecordStoreSQLite(tmp_path / "caller-tx.db")
    try:
        store.execute_count(
            "CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT NOT NULL)"
        )
        store.execute_count(
            "CREATE TABLE caller_audit (id INTEGER PRIMARY KEY, note TEXT NOT NULL)"
        )
        store.insert("notes", {"id": 99, "body": "seeded"})
        store.insert("caller_audit", {"id": 1, "note": "prior-committed"})

        # Caller-owned store.transaction()
        with store.transaction():
            store.insert("caller_audit", {"id": 2, "note": "caller-owned-tx"})
            nested_dup = import_omx(omx_dir=dup_omx, target_record_store=store)
            nested_valid = import_omx(omx_dir=valid_omx, target_record_store=store)
            assert nested_dup.success is False
            assert nested_dup.imported_rows == 0
            assert "active transaction" in str(nested_dup.error)
            assert nested_valid.success is False
            assert nested_valid.imported_rows == 0
            assert "active transaction" in str(nested_valid.error)

        assert store.in_transaction is False
        assert store.query_dicts("SELECT id, body FROM notes ORDER BY id") == [
            {"id": 99, "body": "seeded"}
        ]
        assert store.query_dicts("SELECT id, note FROM caller_audit ORDER BY id") == [
            {"id": 1, "note": "prior-committed"},
            {"id": 2, "note": "caller-owned-tx"},
        ]

        # Raw sqlite3 connection transaction outside store._in_tx
        store.connection.execute("BEGIN")
        store.connection.execute(
            "INSERT INTO caller_audit (id, note) VALUES (3, 'raw-conn-tx')"
        )
        raw_nested = import_omx(omx_dir=dup_omx, target_record_store=store)
        assert raw_nested.success is False
        assert raw_nested.imported_rows == 0
        assert "active transaction" in str(raw_nested.error)
        store.connection.commit()
        assert store.query_dicts("SELECT id, body FROM notes ORDER BY id") == [
            {"id": 99, "body": "seeded"}
        ]
        assert store.query_dicts("SELECT id, note FROM caller_audit ORDER BY id") == [
            {"id": 1, "note": "prior-committed"},
            {"id": 2, "note": "caller-owned-tx"},
            {"id": 3, "note": "raw-conn-tx"},
        ]

        # Import-owned transaction rollback and success
        owned_fail = import_omx(omx_dir=dup_omx, target_record_store=store)
        assert owned_fail.success is False
        assert owned_fail.imported_rows == 0
        assert store.in_transaction is False
        assert store.query_dicts("SELECT id, body FROM notes ORDER BY id") == [
            {"id": 99, "body": "seeded"}
        ]
        assert len(store.query_dicts("SELECT id FROM caller_audit")) == 3

        owned_ok = import_omx(omx_dir=valid_omx, target_record_store=store)
        assert owned_ok.success is True, owned_ok.error
        assert owned_ok.imported_rows == 2
        assert store.query_dicts("SELECT id, body FROM notes ORDER BY id") == [
            {"id": 1, "body": "alpha"},
            {"id": 2, "body": "beta"},
        ]
        assert len(store.query_dicts("SELECT id FROM caller_audit")) == 3
    finally:
        store.close()
