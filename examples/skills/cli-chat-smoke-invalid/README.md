# CLI Chat Smoke Test - Invalid Fixtures Index

**Purpose**: Negative test fixtures for CLI chat smoke testing.
**Location**: `openminion/examples/skills/cli-chat-smoke-invalid/`

## Negative Fixtures

| Fixture ID | Issue | Path | Expected Error |
|------------|-------|------|----------------|
| missing-sections | Missing required sections | `missing-sections/SKILL.md` | Schema validation error |
| malformed-headings | Malformed YAML frontmatter | `malformed-headings/SKILL.md` | YAML parse error |
| invalid-tools | Invalid tool references | `invalid-tools/SKILL.md` | Tool not found error |

## Expected Behavior

These fixtures should trigger deterministic ingest errors without runtime crashes:

1. **missing-sections**: Should fail schema validation due to incomplete Procedure section and missing Failure & Recovery section.

2. **malformed-headings**: Should fail YAML parsing due to malformed frontmatter (missing colons, improper syntax).

3. **invalid-tools**: Should fail tool validation due to references to non-existent tools.

## Usage

Test negative path:
```
/skill ingest openminion/examples/skills/cli-chat-smoke-invalid/missing-sections/SKILL.md
```

Expected: Error message (not crash) indicating validation failure.

## Testing

Run the negative-path tests:
```bash
PYTHONPATH=src .venv/bin/python3.11 -m pytest tests/test_skill_fixtures_negative.py -v
```
