# Hello Skill

## Purpose

Return a short greeting in a deterministic format for smoke checks and onboarding demos.

## Metadata

1. `skill_id`: `hello.greet`
2. `risk_level`: `low`
3. `channels`: `console`, `slack`, `discord`, `telegram`, `whatsapp`
4. `required_tools`: `hello_tool`
5. `required_scopes`: `tool.hello.read`
6. `approval_mode`: `auto`

## Inputs

1. `name` (optional string)

## Recipe

1. Normalize `name`; if empty use `world`.
2. Call tool `hello_tool` with `{"name": "<value>"}`.
3. Validate output starts with `hello `.
4. Return output as final skill result.

## Expected Output

1. Plain text with pattern: `hello <name>`

## Security Notes

1. Do not request elevated scopes for this skill.
2. Do not execute external commands.
3. Keep output free of secrets and tokens.

## Test Cases

1. Input: `name=alex` -> output `hello alex`
2. Input: missing `name` -> output `hello world`
3. Input: denied scope -> fail with clear `missing required scopes` error
