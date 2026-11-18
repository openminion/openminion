from pathlib import Path

VALID_OWNER_TYPES: frozenset[str] = frozenset(
    {"session", "memory", "alias", "collection", "a2a"}
)

DEFAULT_CONFIG_FILENAME = "artifact.yaml"
DEFAULT_INDEX_FILENAME = "index.db"
DEFAULT_STANDALONE_ROOT_SUBPATH = Path(".artifactctl")
DEFAULT_INTEGRATED_ROOT_SUBPATH = Path("artifact")

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_BATCH_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_TURN_IMAGES = 4
