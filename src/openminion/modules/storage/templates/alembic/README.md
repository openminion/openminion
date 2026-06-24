# Alembic Templates (OpenMinion module-per-DB)

These files are reference templates for module migration bootstrap.

## Files

1. `env.py`: reference environment file with OpenMinion identity sync (`application_id`, `user_version`, `om_meta`).
2. `versions/0001_init.py`: example initial revision.
3. `versions/0002_split.py`: example SQLite-breaking migration with batch mode (rename + split table).

Copy templates into a module package under:

- `openminion_<module>/migrations/`
- `openminion_<module>/migrations/versions/`
