from __future__ import annotations

from scripts.validate.prompt_literals import (
    collect_domain_owner_findings,
    collect_findings,
)


def test_prompt_literal_validator_catches_inline_user_message(tmp_path) -> None:
    source = tmp_path / "inline_prompt.py"
    source.write_text(
        "def build_request(ProviderRequest):\n"
        "    return ProviderRequest(\n"
        "        user_message=(\n"
        "            'Tool execution results:\\n[]\\n\\n'\n"
        "            'Do not emit any tool call markup, channel envelope, JSON tool payload.'\n"
        "        )\n"
        "    )\n",
        encoding="utf-8",
    )

    findings = collect_findings(tmp_path)

    assert len(findings) == 1
    assert findings[0].path == source
    assert findings[0].name == "user_message"


def test_prompt_literal_validator_catches_inline_return_prompt(tmp_path) -> None:
    source = tmp_path / "return_prompt.py"
    source.write_text(
        "def prompt():\n"
        "    return (\n"
        "        'You MUST call exactly one tool: file.read.\\n'\n"
        "        'Return a valid tool call now.'\n"
        "    )\n",
        encoding="utf-8",
    )

    findings = collect_findings(tmp_path)

    assert len(findings) == 1
    assert findings[0].path == source
    assert findings[0].name == "<inline-prompt>"


def test_prompt_literal_validator_catches_missing_domain_owner(tmp_path) -> None:
    findings = collect_domain_owner_findings(
        tmp_path,
        {"moved/prompts.py": "domain prompt owner"},
    )

    assert len(findings) == 1
    assert findings[0].name == "<missing-domain-owner>"


def test_prompt_literal_validator_catches_empty_domain_owner(tmp_path) -> None:
    source = tmp_path / "prompts.py"
    source.write_text("VALUE = 'not a prompt contract'\n", encoding="utf-8")

    findings = collect_domain_owner_findings(
        tmp_path,
        {"prompts.py": "domain prompt owner"},
    )

    assert len(findings) == 1
    assert findings[0].path == source
    assert findings[0].name == "<empty-domain-owner>"
