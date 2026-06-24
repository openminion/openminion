from openminion.modules.storage.migrations.alembic import run_module_env

from openminion.modules.session.storage.migrations import (
    MODULE_APPLICATION_ID,
    MODULE_ID,
    TARGET_USER_VERSION,
)


run_module_env(
    module_id=MODULE_ID,
    module_application_id=MODULE_APPLICATION_ID,
    target_user_version=TARGET_USER_VERSION,
)
