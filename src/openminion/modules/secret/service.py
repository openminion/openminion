import asyncio
import time
from pathlib import Path
from collections.abc import Mapping

from cryptography.fernet import Fernet, InvalidToken

from .interfaces import SECRET_INTERFACE_VERSION
from .constants import (
    DEFAULT_SQLITE_FILENAME,
    OPENMINION_SECRET_KEY_ENV,
)
from .schemas import (
    SecretKeyError,
    SecretNotFoundError,
    SecretEncryptionError,
)
from openminion.base.config import (
    resolve_module_storage_path,
    resolve_home_root,
    resolve_data_root,
)
from openminion.base.config.env import EnvironmentConfig, resolve_environment_config
from openminion.modules.storage.record_store import RecordStore
from .storage.store import PostgresSecretStore, SQLiteSecretStore


class SecretService:
    def __init__(
        self,
        db_path: str | None = None,
        master_key: str | None = None,
        env: EnvironmentConfig | Mapping[str, object] | None = None,
        record_store: RecordStore | None = None,
    ) -> None:
        env_config = resolve_environment_config(env=env)
        if master_key is None:
            master_key = env_config.get(OPENMINION_SECRET_KEY_ENV, "")
            if not master_key:
                raise SecretKeyError(
                    f"{OPENMINION_SECRET_KEY_ENV} environment variable is required. "
                    'Generate a 32-byte key with: python -c "import base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"'
                )

        try:
            self._fernet = Fernet(master_key.encode())
        except Exception as exc:
            raise SecretKeyError(f"Invalid secret key: {exc}")

        home_root = resolve_home_root()
        data_root = resolve_data_root(home_root)
        if record_store is not None:
            self._db_path = str(
                getattr(record_store, "sqlite_path", "<external-record-store>")
            )
            self._store = PostgresSecretStore(record_store=record_store)
            return

        if db_path is None:
            db_path = str(
                resolve_module_storage_path(
                    home_root,
                    "secret",
                    data_root=str(data_root),
                    filename=DEFAULT_SQLITE_FILENAME,
                )
            )
        else:
            if str(db_path).strip() == ":memory:":
                self._db_path = ":memory:"
                self._store = SQLiteSecretStore(self._db_path)
                return
            candidate = Path(db_path).expanduser()
            if not candidate.is_absolute():
                candidate = data_root / candidate
            db_path = str(candidate.resolve(strict=False))
        self._db_path = db_path
        self._store = SQLiteSecretStore(self._db_path)

    @property
    def contract_version(self) -> str:
        return SECRET_INTERFACE_VERSION

    async def close(self) -> None:
        await asyncio.to_thread(self._store.close)

    def _encrypt(self, value: str) -> str:
        try:
            return self._fernet.encrypt(value.encode()).decode()
        except Exception as exc:
            raise SecretEncryptionError(f"Failed to encrypt: {exc}")

    def _decrypt(self, encrypted: str) -> str:
        try:
            return self._fernet.decrypt(encrypted.encode()).decode()
        except InvalidToken:
            raise SecretNotFoundError("Secret not found or corrupted")
        except Exception as exc:
            raise SecretEncryptionError(f"Failed to decrypt: {exc}")

    async def set_secret(
        self, key: str, value: str, *, namespace: str = "default"
    ) -> None:
        encrypted = self._encrypt(value)
        now = time.time()
        await asyncio.to_thread(
            self._store.upsert,
            key=key,
            namespace=namespace,
            value=encrypted,
            created_at=now,
            updated_at=now,
        )

    async def get_secret(self, key: str, *, namespace: str = "default") -> str:
        encrypted = await asyncio.to_thread(
            self._store.fetch_value, key=key, namespace=namespace
        )
        if encrypted is None:
            raise SecretNotFoundError(
                f"Secret '{key}' not found in namespace '{namespace}'"
            )
        return self._decrypt(encrypted)

    async def delete_secret(self, key: str, *, namespace: str = "default") -> None:
        await asyncio.to_thread(self._store.delete, key=key, namespace=namespace)

    async def list_keys(self, namespace: str = "default") -> list[str]:
        return await asyncio.to_thread(self._store.list_keys, namespace=namespace)
