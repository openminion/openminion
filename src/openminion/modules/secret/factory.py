from pathlib import Path
from typing import Any

from .constants import DEFAULT_SQLITE_FILENAME, OPENMINION_SECRET_KEY_ENV
from .service import SecretService


def build_secret_service(*, data_root: Path, env: Any) -> SecretService | None:
    if not env.has(OPENMINION_SECRET_KEY_ENV):
        return None
    secret_dir = data_root / "secret"
    secret_dir.mkdir(parents=True, exist_ok=True)
    return SecretService(
        db_path=str(secret_dir / DEFAULT_SQLITE_FILENAME),
        env=env,
    )
