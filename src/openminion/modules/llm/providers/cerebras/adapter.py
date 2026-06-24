from ..openai.adapter import OpenAIProvider


class CerebrasProvider(OpenAIProvider):
    name = "cerebras"
    default_base_url = "https://api.cerebras.ai/v1"


def cerebras_provider() -> CerebrasProvider:
    return CerebrasProvider()
