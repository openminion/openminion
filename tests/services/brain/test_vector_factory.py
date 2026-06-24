from types import SimpleNamespace

from openminion.services.brain.factory.vector import init_vector_adapter


class DummyLogger:
    def info(self, *args, **kwargs) -> None:
        pass

    def warning(self, *args, **kwargs) -> None:
        pass


def test_vector_factory_disabled_returns_none(tmp_path) -> None:
    config = SimpleNamespace(vector=SimpleNamespace(enabled=False), extra={})
    adapter, sync = init_vector_adapter(
        config=config,
        db_dir=tmp_path,
        logger=DummyLogger(),
    )
    assert adapter is None
    assert sync is None


def test_vector_factory_local_sqlite(monkeypatch, tmp_path) -> None:
    import openminion.modules.storage.runtime.vector_index as vector_index
    import openminion.services.integration.vector_sync as vector_sync

    provider_calls: list[dict[str, object]] = []

    class DummyEmbeddingProvider:
        def __init__(self, *args, **kwargs) -> None:
            provider_calls.append({"args": args, "kwargs": kwargs})

    class DummyBackend:
        def __init__(self, *args, **kwargs) -> None:
            pass

    class DummyScheduler:
        def __init__(self, vector_adapter, batch_size: int) -> None:
            self.vector_adapter = vector_adapter
            self.batch_size = batch_size
            self.started = False

        def start(self) -> None:
            self.started = True

    def fake_create_vector_index_adapter(**kwargs):
        return "adapter"

    monkeypatch.setattr(vector_index, "LocalEmbeddingProvider", DummyEmbeddingProvider)
    monkeypatch.setattr(vector_index, "SQLiteVecBackend", DummyBackend)
    monkeypatch.setattr(
        vector_index, "create_vector_index_adapter", fake_create_vector_index_adapter
    )
    monkeypatch.setattr(vector_sync, "VectorSyncScheduler", DummyScheduler)

    vector_cfg = SimpleNamespace(
        enabled=True,
        provider="local",
        backend="sqlite",
        model="m",
        dimension=8,
        sync_batch_size=7,
    )
    config = SimpleNamespace(vector=vector_cfg, extra={})

    adapter, sync = init_vector_adapter(
        config=config,
        db_dir=tmp_path,
        logger=DummyLogger(),
    )

    assert adapter == "adapter"
    assert isinstance(sync, DummyScheduler)
    assert sync.started is True
    assert provider_calls == [{"args": (), "kwargs": {"model": "m", "dimension": 8}}]
