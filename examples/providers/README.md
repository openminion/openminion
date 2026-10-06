# Provider Examples

OpenMinion keeps application code provider-neutral. Configure a provider, then
run the same SDK program with the resulting agent profile.

List the exact presets available in your installed version:

```bash
openminion setup --list-providers
```

## Hosted providers

Set the provider credential in your environment, then run setup:

```bash
export OPENAI_API_KEY='...'
openminion setup --provider openai --model gpt-4.1-mini --no-chat

export ANTHROPIC_API_KEY='...'
openminion setup --provider anthropic --model claude-sonnet-5 --no-chat

export OPENROUTER_API_KEY='...'
openminion setup --provider openrouter --model openai/gpt-4.1-mini --no-chat
```

Add `--check-provider` only when one bounded, potentially billable validation
request is acceptable.

## Local Ollama

Install Ollama, pull the model, and configure the local preset:

```bash
ollama pull llama3.1
openminion setup --provider ollama --model llama3.1 --no-chat
```

## Cortensor

Portal and direct Router setup are separate presets:

```bash
export CORTENSOR_API_KEY='...'
openminion setup --provider cortensor-portal --model oss-20b --no-chat
openminion setup --provider cortensor-router --model gpt-oss-20b --no-chat
```

Portal uses the shared OpenAI-compatible adapter. Router uses the `cortensor`
runtime adapter and its completion transport.

## Compatible providers

MiniMax, Kimi, Z.ai, DeepSeek, Qwen, Gemini, xAI, Mistral, and Together have
named setup presets. Use the preset ID shown by `--list-providers`; do not put
credentials in source or committed configuration.

## Run the same application

After setup, select the configured agent without changing the application:

```bash
python examples/sdk/provider_switching.py \
  --agent default \
  "Explain why provider-neutral application code is useful."
```

Run `openminion doctor --check-turn` before relying on a provider in a demo.
