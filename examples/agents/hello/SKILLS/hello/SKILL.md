# Skill: Hello Greeting

## Goal

Produce `hello <name>` using `hello_tool`.

## Procedure

1. Resolve name from user message.
2. Call `hello_tool`.
3. Return tool result directly.

## Guardrails

1. Requires scope `tool.hello.read`.
2. No external network access.
3. No filesystem writes.
