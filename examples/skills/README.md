# Skill Examples

`openminion/examples/skills/` contains two intentionally different surfaces:

1. Markdown-first authoring examples such as `hello/` and the `ops-*` skills,
2. YAML-frontmatter compatibility fixtures used by skill ingest tests.

Use the Markdown-first examples to author new local skills. They match the
format emitted by `openminion scaffold skill`.

The YAML-frontmatter files preserve catalog and CLI-chat compatibility. Most
use current OpenMinion tool IDs. The three API fixtures use the external-catalog
placeholder `http_request`, which is not a built-in OpenMinion tool:

1. `api-account-create-post-share/`
2. `api-account-publish-share/`
3. `cli-chat-smoke/api-post/`

Do not copy the intentionally invalid fixtures under `cli-chat-smoke-invalid/`
as authoring examples.
