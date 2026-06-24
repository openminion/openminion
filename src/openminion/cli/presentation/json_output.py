from __future__ import annotations

import json
import sys
from typing import Any, TextIO


def print_json_payload(
    payload: Any,
    *,
    indent: int | None = 2,
    sort_keys: bool = True,
    default: Any | None = None,
    ensure_ascii: bool = False,
    stream: TextIO | None = None,
) -> None:
    target = sys.stdout if stream is None else stream
    target.write(
        json.dumps(
            payload,
            indent=indent,
            sort_keys=sort_keys,
            default=default,
            ensure_ascii=ensure_ascii,
        )
    )
    target.write("\n")
