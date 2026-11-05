"""Merge generated task definitions without changing runtime-tool references."""

from typing import Any, cast


def merge_task_definitions(
    schema: dict[str, Any], update: dict[str, Any]
) -> dict[str, Any]:
    definitions = dict(schema.get("$defs") or {})
    generated = update.pop("$defs", {})
    occupied = set(definitions) | set(generated)
    renamed = {}
    for name in generated:
        if name not in definitions:
            continue
        candidate = f"task_update_{name}"
        suffix = 0
        while candidate in occupied:
            suffix += 1
            candidate = f"task_update_{name}_{suffix}"
        renamed[name] = candidate
        occupied.add(candidate)
    refs = {f"#/$defs/{name}": f"#/$defs/{new}" for name, new in renamed.items()}
    for name, value in generated.items():
        definitions[renamed.get(name, name)] = _rewrite_refs(value, refs)
    if definitions:
        schema["$defs"] = definitions
    return cast(dict[str, Any], _rewrite_refs(update, refs))


def _rewrite_refs(value: Any, refs: dict[str, str]) -> Any:
    if isinstance(value, list):
        return [_rewrite_refs(item, refs) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _rewrite_refs(child, refs) for key, child in value.items()}
    ref = result.get("$ref")
    if isinstance(ref, str):
        for original, replacement in refs.items():
            if ref == original or ref.startswith(original + "/"):
                result["$ref"] = replacement + ref[len(original) :]
                break
    return result
