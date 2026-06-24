from pathlib import Path

VALID_OWNER_TYPES: frozenset[str] = frozenset(
    {"session", "memory", "alias", "collection", "a2a"}
)

DEFAULT_CONFIG_FILENAME = "artifact.yaml"
DEFAULT_INDEX_FILENAME = "index.db"
DEFAULT_STANDALONE_ROOT_SUBPATH = Path(".artifactctl")
DEFAULT_INTEGRATED_ROOT_SUBPATH = Path("artifact")
