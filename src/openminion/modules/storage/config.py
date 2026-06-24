from dataclasses import dataclass

VECTOR_INDEX_CHAR_NGRAM_MIN = 3
VECTOR_INDEX_CHAR_NGRAM_MAX = 5

POOL_HEALTH_EMIT_INTERVAL_SECONDS_DEFAULT = 60.0


@dataclass(frozen=True)
class StorageConfig:
    pass


def load_config(*_args: object, **_kwargs: object) -> StorageConfig:
    return StorageConfig()
