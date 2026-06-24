from __future__ import annotations

import inspect

import pytest

from tests.storage.conftest import BackendCase

pytestmark = pytest.mark.postgres


def _create_widgets_table(case: BackendCase) -> None:
    case.store.execute_count(
        'CREATE TABLE "widgets" ('
        '"id" INTEGER PRIMARY KEY, '
        '"name" TEXT NOT NULL, '
        '"weight" INTEGER NOT NULL'
        ")"
    )


def test_insert_many_inserts_all_rows(record_store_case: BackendCase) -> None:
    _create_widgets_table(record_store_case)
    rows = [
        {"id": 1, "name": "alpha", "weight": 10},
        {"id": 2, "name": "beta", "weight": 20},
        {"id": 3, "name": "gamma", "weight": 30},
    ]
    inserted = record_store_case.store.insert_many("widgets", rows)
    assert inserted == 3
    fetched = record_store_case.store.query_dicts(
        'SELECT "id", "name", "weight" FROM "widgets" ORDER BY "id"'
    )
    assert fetched == rows


def test_insert_many_empty_returns_zero(record_store_case: BackendCase) -> None:
    _create_widgets_table(record_store_case)
    assert record_store_case.store.insert_many("widgets", []) == 0
    assert record_store_case.store.query_dicts('SELECT * FROM "widgets"') == []


def test_insert_many_rejects_mismatched_columns(
    record_store_case: BackendCase,
) -> None:
    _create_widgets_table(record_store_case)
    with pytest.raises(ValueError):
        record_store_case.store.insert_many(
            "widgets",
            [
                {"id": 1, "name": "alpha", "weight": 10},
                {"id": 2, "name": "beta"},  # missing column
            ],
        )


def test_insert_many_handles_large_batch(record_store_case: BackendCase) -> None:
    _create_widgets_table(record_store_case)
    rows = [{"id": i, "name": f"item-{i}", "weight": i * 2} for i in range(1, 1001)]
    inserted = record_store_case.store.insert_many("widgets", rows)
    assert inserted == 1000
    count = record_store_case.store.query_dicts('SELECT COUNT(*) AS n FROM "widgets"')
    assert int(count[0]["n"]) == 1000


def test_stream_dicts_yields_iterator(record_store_case: BackendCase) -> None:
    _create_widgets_table(record_store_case)
    rows = [{"id": i, "name": f"row-{i}", "weight": i} for i in range(1, 101)]
    record_store_case.store.insert_many("widgets", rows)

    streamed = record_store_case.store.stream_dicts(
        'SELECT "id", "name", "weight" FROM "widgets" ORDER BY "id"',
        batch_size=10,
    )
    # The result must be a generator/iterator, not a list.
    assert inspect.isgenerator(streamed) or hasattr(streamed, "__next__")

    collected = list(streamed)
    assert collected == rows


def test_stream_rows_yields_iterator(record_store_case: BackendCase) -> None:
    _create_widgets_table(record_store_case)
    rows = [
        {"id": i, "name": "even" if i % 2 == 0 else "odd", "weight": i}
        for i in range(1, 21)
    ]
    record_store_case.store.insert_many("widgets", rows)

    streamed = record_store_case.store.stream_rows(
        "widgets", where={"name": "even"}, order='"id" ASC', batch_size=5
    )
    collected = list(streamed)
    assert all(r["name"] == "even" for r in collected)
    assert [r["id"] for r in collected] == list(range(2, 21, 2))


def test_stream_dicts_handles_large_result_lazily(
    record_store_case: BackendCase,
) -> None:
    _create_widgets_table(record_store_case)
    rows = [{"id": i, "name": f"row-{i}", "weight": i} for i in range(1, 2001)]
    record_store_case.store.insert_many("widgets", rows)

    iterator = record_store_case.store.stream_dicts(
        'SELECT "id" FROM "widgets" ORDER BY "id"',
        batch_size=100,
    )
    # Pull just the first 5 rows; iterator should not have materialised the rest.
    first_five = []
    for row in iterator:
        first_five.append(row["id"])
        if len(first_five) >= 5:
            break
    assert first_five == [1, 2, 3, 4, 5]
