# Export a Stored Skill Bundle

Export one exact admitted skill version to a new local directory with the
module CLI. Previously admitted versions remain eligible even when another
version is active. Pending versions cannot be exported.

```bash
python -m openminion.modules.skill.cli --config skill.yaml \
  export my-skill --version FULL_VERSION_HASH --destination ./shared-skill
```

The destination parent must already exist. The destination must not exist,
and neither it nor its ancestors may be symlinks. Use a trusted parent that is
not being modified concurrently. There is no overwrite or retry mode.

## What Is Preserved

- Retained Markdown bytes become `SKILL.md`. Original pre-ingest newline
  conventions are not recoverable.
- Captured companion resources are copied as binary bytes after size and digest
  checks. Files omitted during ingestion are not recovered from the source tree.
- Stored OpenAI companion metadata becomes `agents/openai.yaml`. Its mapping is
  preserved semantically, not its original formatting or comments. The result
  includes `export.openai_yaml_semantic_only` when this file is exported.

The JSON result contains the selected skill/version, destination, output paths,
and warnings. Export does not change the active version or execute scripts.
It never rereads original source directories. Missing source blobs and `review:`
sources, including learned packages without retained Markdown, are unsupported;
export does not reconstruct Markdown from package sections.

## Review Before Sharing

Export does not collect credentials, environment variables, catalog admission,
review receipts, or measurement records. However, retained authored Markdown,
YAML, and resources can themselves contain private text or secrets. Export
does not redact or certify that content. Review every output before sharing.

This is retained-content export, not a portability or execution guarantee.
Check structural portability separately:

```bash
openminion skill test ./shared-skill --require-portable
```

Imported invocation restrictions are not permission grants. Their preservation
does not imply enforcement; do not admit a skill if it requires unsupported
manual-only behavior.

## Import and Admission

```bash
python -m openminion.modules.skill.cli --config destination-skill.yaml \
  ingest --name my-skill --file ./shared-skill/SKILL.md
```

In a fresh destination catalog the imported version remains pending until
local review and admission. An identical re-import into an existing catalog
preserves its existing admission. Runtime version hashes may differ between
installations. No exporter approval or authority is transferred.

## Failures

- `INVALID_ARGUMENT`: missing version or unsafe/colliding output paths,
  existing destination, symlink ancestry, or missing destination parent.
- `NOT_FOUND`: the exact requested skill version does not exist.
- `SKILL_EXPORT_NOT_ADMITTED`: the version has not been admitted.
- `SKILL_EXPORT_SOURCE_UNAVAILABLE`: retained bytes are missing/unreadable or
  the source is unsupported.
- `SKILL_EXPORT_INTEGRITY_ERROR`: stored content fails digest, size, or count
  checks.
- `SKILL_EXPORT_WRITE_FAILED`: destination creation/writing failed. A failed
  write removes only the newly created export directory; if cleanup also
  fails, the error identifies the remaining destination for operator inspection.

Preflight failures create no destination. Export neither admits content nor
changes admission rules. It is available through the local module CLI, not a
remote API, model tool, or Focus action.
