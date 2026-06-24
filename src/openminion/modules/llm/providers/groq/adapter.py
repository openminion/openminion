from ..openai.adapter import OpenAIProvider


class GroqProvider(OpenAIProvider):
    name = "groq"
    default_base_url = "https://api.groq.com/openai/v1"


def groq_provider() -> GroqProvider:
    return GroqProvider()
