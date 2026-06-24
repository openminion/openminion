These fixtures are small adapted interoperability samples derived from the
public OpenAI skills and Anthropic skills repositories.

They are intentionally minimized for regression coverage only:

1. they preserve representative names, bundle layout, and compact metadata
   shapes,
2. they do not copy full upstream packages or large instruction bodies,
3. they exist to test OpenMinion ingest, catalog summary, deterministic
   matching, and lint behavior.

Upstream source context:

1. OpenAI skills repository: representative bundle structure with `SKILL.md`
   plus `agents/openai.yaml`
2. Anthropic skills repository: representative markdown-only `SKILL.md` skills

Fixture families (SOCE-01):

1. OpenAI Figma dense family (5 skills): `figma`, `figma_generate_design`,
   `figma_create_design_system_rules`, `figma_code_connect_components`,
   `figma_create_new_file` — tests dense-sibling summary-query disambiguation
2. Anthropic descriptor-scarce family (5 skills): `mcp_builder`,
   `skill_creator`, `slack_gif_creator`, `theme_factory`,
   `web_artifacts_builder` — tests summary-query ranking without compact
   descriptors
3. Suspicious-tool samples: `claude-api`, `figma_create_design_system_rules`,
   `linear` — contain embedded filenames, code symbols, and prose tokens that
   trigger `_TOOL_RE` false positives for tool-extraction normalization testing

License context:

1. upstream repositories retain their own licenses and terms,
2. these fixtures are reduced/adapted test samples rather than canonical
   upstream copies,
3. review the upstream repositories before re-expanding or redistributing large
   verbatim content.
