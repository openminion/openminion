class StorageMigrationError(RuntimeError):
    """Base migration/runtime error for the OpenMinion storage module."""


class DbIdentityError(StorageMigrationError):
    """Raised when DB identity metadata does not match expectations."""


class DbVersionError(StorageMigrationError):
    """Raised when DB version is outside supported migration window."""


class BackupError(StorageMigrationError):
    """Raised when pre-migration backup creation fails."""


class MigrationApplyError(StorageMigrationError):
    """Raised when Alembic upgrade application fails."""


class VerificationError(StorageMigrationError):
    """Raised when post-migration verification fails with fatal findings."""


class RehydrateError(StorageMigrationError):
    """Raised when OMX export/import rehydrate path fails."""


class RecoveryError(StorageMigrationError):
    """Raised when diagnostics recovery orchestration fails."""
