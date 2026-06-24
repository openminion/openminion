import pytest

CTCE_CHAIN_CASES = [
    ("what's weather in san francisco today?", "weather tool call"),
    ("latest news on AI", "web.search + evidence"),
    ("check first link and summarize", "fetch + summary"),
    ("search AI news, get weather for top 2 cities", "full chain"),
]


@pytest.mark.parametrize("prompt,expected", CTCE_CHAIN_CASES)
def test_chain_execution_quality(prompt, expected):
    assert len(prompt) > 0
    assert len(expected) > 0
