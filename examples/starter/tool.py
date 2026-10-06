"""Expose a normal Python function as an Agent tool."""

from openminion import tool


@tool
def hello_tool(name: str = "world") -> str:
    """Return a short greeting."""

    return f"hello {name.strip() or 'world'}"
