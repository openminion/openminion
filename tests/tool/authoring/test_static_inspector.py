from __future__ import annotations

import pytest

from openminion.modules.tool.authoring.runtime.static import (
    POWER_USER,
    READ_SAFE,
    WRITE_SAFE,
    inspect_source,
    rollup_risk_level,
)


PATTERN_CASES = [
    (
        "STATIC-DYN-001",
        "critical",
        POWER_USER,
        "def tool(x):\n    exec('print(x)')\n",
        set(),
    ),
    (
        "STATIC-DYN-002",
        "critical",
        POWER_USER,
        "def tool(x):\n    return getattr(builtins, 'eval')('1+1')\n",
        set(),
    ),
    (
        "STATIC-NATIVE-001",
        "critical",
        POWER_USER,
        "import ctypes\n\ndef tool(x):\n    return x\n",
        set(),
    ),
    (
        "STATIC-NATIVE-002",
        "critical",
        POWER_USER,
        "import socket\n\ndef tool(x):\n    return x\n",
        set(),
    ),
    (
        "STATIC-SHELL-001",
        "critical",
        POWER_USER,
        "import os\n\ndef tool(x):\n    return os.system('echo hi')\n",
        set(),
    ),
    (
        "STATIC-FS-WRITE",
        "high",
        READ_SAFE,
        "def tool(x):\n    with open('x.txt', 'w') as fh:\n        fh.write(x)\n",
        set(),
    ),
    (
        "STATIC-NET-EGRESS",
        "high",
        READ_SAFE,
        "import requests\n\ndef tool(x):\n    return x\n",
        set(),
    ),
    (
        "STATIC-SUBPROC",
        "high",
        READ_SAFE,
        "import subprocess\n\ndef tool(x):\n    return subprocess.run(['echo', x])\n",
        set(),
    ),
    (
        "STATIC-FS-ESCAPE",
        "high",
        WRITE_SAFE,
        "def tool(x):\n    return '/etc/passwd'\n",
        set(),
    ),
    (
        "STATIC-NET-RAW",
        "high",
        WRITE_SAFE,
        "import requests\n\ndef tool(x):\n    return x\n",
        set(),
    ),
    (
        "STATIC-SUBPROC-WS",
        "high",
        WRITE_SAFE,
        "import subprocess\n\ndef tool(x):\n    return subprocess.Popen(['echo', x])\n",
        set(),
    ),
    (
        "STATIC-DEP-DENY",
        "high",
        POWER_USER,
        "import pandas\n\ndef tool(x):\n    return x\n",
        set(),
    ),
    (
        "STATIC-NET-ANTHROPIC",
        "medium",
        POWER_USER,
        "URL = 'https://api.anthropic.com/v1/messages'\n\ndef tool(x):\n    return x\n",
        set(),
    ),
    (
        "STATIC-SECRET-001",
        "critical",
        POWER_USER,
        "TOKEN = 'sk-abcdef1234567890'\n\ndef tool(x):\n    return x\n",
        set(),
    ),
    (
        "STATIC-SECRET-002",
        "critical",
        POWER_USER,
        "KEY = '-----BEGIN RSA PRIVATE KEY-----'\n\ndef tool(x):\n    return x\n",
        set(),
    ),
]


@pytest.mark.parametrize(
    ("code", "expected_risk", "tier", "source", "allowed_deps"),
    PATTERN_CASES,
)
def test_static_inspector_detects_each_pattern(
    code: str,
    expected_risk: str,
    tier: str,
    source: str,
    allowed_deps: set[str],
) -> None:
    risk_level, findings = inspect_source(
        source,
        target_scope_tier=tier,
        allowed_deps=allowed_deps,
    )

    assert code in {item.code for item in findings}
    assert risk_level == expected_risk


@pytest.mark.parametrize(
    ("code", "tier"), [(case[0], case[2]) for case in PATTERN_CASES]
)
def test_static_inspector_clean_reference_avoids_false_positive(
    code: str,
    tier: str,
) -> None:
    source = "import json\n\ndef tool(x):\n    return json.dumps({'x': x})\n"

    _risk_level, findings = inspect_source(
        source,
        target_scope_tier=tier,
        allowed_deps={"json"},
    )

    assert code not in {item.code for item in findings}


def test_rollup_risk_level_prefers_highest_severity() -> None:
    _risk_level, findings = inspect_source(
        "import requests\nTOKEN = 'sk-abcdef1234567890'\n",
        target_scope_tier=READ_SAFE,
        allowed_deps=set(),
    )

    assert rollup_risk_level(findings) == "critical"


def test_static_inspector_is_ast_only_for_comment_vs_string_literal() -> None:
    comment_only = (
        "# sk-abcdef1234567890 lives in a comment only\n\ndef tool(x):\n    return x\n"
    )
    literal_value = "TOKEN = 'sk-abcdef1234567890'\n\ndef tool(x):\n    return x\n"

    comment_risk, comment_findings = inspect_source(comment_only)
    literal_risk, literal_findings = inspect_source(literal_value)

    assert "STATIC-SECRET-001" not in {item.code for item in comment_findings}
    assert comment_risk == "low"
    assert "STATIC-SECRET-001" in {item.code for item in literal_findings}
    assert literal_risk == "critical"
