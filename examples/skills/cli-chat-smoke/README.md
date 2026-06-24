# CLI Chat Smoke Test Fixtures Index

**Purpose**: Canonical skill example fixtures for CLI chat smoke testing.
**Location**: `openminion/examples/skills/cli-chat-smoke/`

## Valid Fixtures

| Skill ID | Domain | Path | Purpose |
|----------|--------|------|---------|
| cli-chat-smoke-plan | Planning | `plan/SKILL.md` | Simple checkpointed planning skill |
| cli-chat-smoke-debug | Debugging | `debug/SKILL.md` | Systematic error triage skill |
| cli-chat-smoke-web-research | Research | `web-research/SKILL.md` | Web search and source summary skill |
| cli-chat-smoke-api-post | API Workflow | `api-post/SKILL.md` | HTTP POST workflow skill |

## Usage

Ingest a skill:
```
/skill ingest openminion/examples/skills/cli-chat-smoke/plan/SKILL.md
```

Use the skill:
```
plan my release schedule
```

List ingested skills:
```
/skill list
```

## Schema

All fixtures follow the SKILL.md schema:
- YAML frontmatter with `id`, `name`, `version`, `description`, `tags`, `tools`, `scopes_required`, `risk`, `when_to_use`, `inputs`, `outputs`
- `# Skill Card` section with Goal, Triggers, Tools, Hard rules
- `# Procedure` section with numbered steps
- `# Checks` section with validation criteria
- `# Failure & Recovery` section with error handling

## Testing

Run the fixture tests:
```bash
PYTHONPATH=src .venv/bin/python3.11 -m pytest tests/test_skill_fixtures.py -v
```
