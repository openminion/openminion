"""Protect the typed recovery-pipeline contract from interpretive drift."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_IMPORT_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_IMPORT_ROOT))

from scripts.common.terminal_output import emit_json_report  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[2]
RECOVERY_ROOT = (
    REPO_ROOT / "src" / "openminion" / "modules" / "brain" / "runtime" / "recovery"
)
SCHEMAS_PATH = RECOVERY_ROOT / "schemas.py"
PIPELINE_PATH = RECOVERY_ROOT / "pipeline.py"
FORBIDDEN_PROSE_PARAM_NAMES = frozenset({"assistant_body", "model_output", "body_text"})
SUSPICIOUS_FUNCTION_PREFIXES = (
    "fuzzy_",
    "lenient_",
    "interpret_",
    "extract_intent_",
    "rescue_",
)
FORBIDDEN_LLM_ATTRIBUTE_NAMES = frozenset(
    {
        "call_llm",
        "invoke_llm",
        "complete",
        "completions",
        "responses",
        "chat",
        "generate",
    }
)
FORBIDDEN_LLM_OBJECT_TOKENS = ("llm", "openai", "anthropic", "minimax")


def _parse_file(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"))


def _literal_str(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _enum_members(tree: ast.AST) -> set[str]:
    members: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != "RepairType":
            continue
        for stmt in node.body:
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        members.add(target.id)
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                members.add(stmt.target.id)
    return members


def _function_map(tree: ast.AST) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions[node.name] = node
    return functions


def _registered_repairs(
    tree: ast.AST,
) -> list[tuple[str, str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    registered: list[tuple[str, str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    for node in tree.body if isinstance(tree, ast.Module) else []:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            if not isinstance(decorator.func, ast.Name):
                continue
            if decorator.func.id != "register_repair" or len(decorator.args) != 1:
                continue
            arg = decorator.args[0]
            if not isinstance(arg, ast.Attribute) or not isinstance(
                arg.value, ast.Name
            ):
                continue
            if arg.value.id != "RepairType":
                continue
            registered.append((node.name, arg.attr, node))
    return registered


def _attribute_chain(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _attribute_chain(node.value)
        if prefix:
            return f"{prefix}.{node.attr}"
        return node.attr
    return ""


def _has_annotation(
    annotation: ast.AST | None, *, expected_token: str | None = None
) -> bool:
    if annotation is None:
        return False
    if expected_token is None:
        return True
    return expected_token in ast.unparse(annotation)


def _scan_suspicious_function_names(tree: ast.AST, path: Path) -> list[str]:
    findings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith(SUSPICIOUS_FUNCTION_PREFIXES):
                findings.append(
                    f"{path}:{node.lineno}: suspicious recovery helper name `{node.name}` is forbidden"
                )
    return findings


def _scan_registered_repairs(
    tree: ast.AST, path: Path, allowed_members: set[str]
) -> list[str]:
    findings: list[str] = []
    registered = _registered_repairs(tree)
    seen_members: set[str] = set()
    for func_name, member_name, func in registered:
        seen_members.add(member_name)
        if member_name not in allowed_members:
            findings.append(
                f"{path}:{func.lineno}: registered repair type `{member_name}` is outside the closed RepairType enum"
            )
        if not func.args.args:
            findings.append(
                f"{path}:{func.lineno}: registered repair `{func_name}` must accept a typed payload parameter"
            )
            continue
        payload_arg = func.args.args[0]
        if not _has_annotation(payload_arg.annotation, expected_token="RepairPayload"):
            findings.append(
                f"{path}:{payload_arg.lineno}: registered repair `{func_name}` payload parameter must be annotated as RepairPayload"
            )
        if not _has_annotation(func.returns):
            findings.append(
                f"{path}:{func.lineno}: registered repair `{func_name}` must declare a typed return annotation"
            )
        arg_names = {arg.arg for arg in func.args.args}
        for forbidden_name in sorted(FORBIDDEN_PROSE_PARAM_NAMES):
            if forbidden_name in arg_names:
                findings.append(
                    f"{path}:{func.lineno}: registered repair `{func_name}` must not accept prose-shaped parameter `{forbidden_name}`"
                )
    missing_members = allowed_members - seen_members
    if missing_members:
        findings.append(
            f"{path}:1: missing registered repairs for closed enum members {sorted(missing_members)}"
        )
    return findings


def _scan_validation_message_function(
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef], path: Path
) -> list[str]:
    findings: list[str] = []
    target = functions.get("_deterministic_validation_message")
    if target is None:
        return [f"{path}:1: missing _deterministic_validation_message helper"]
    if not target.args.args:
        return [
            f"{path}:{target.lineno}: _deterministic_validation_message must take typed error input"
        ]
    first_arg = target.args.args[0]
    if not _has_annotation(first_arg.annotation, expected_token="TCRPValidationError"):
        findings.append(
            f"{path}:{first_arg.lineno}: _deterministic_validation_message must take TCRPValidationError input"
        )
    for node in ast.walk(target):
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_PROSE_PARAM_NAMES:
            findings.append(
                f"{path}:{node.lineno}: deterministic validation message must not read prose-shaped variable `{node.id}`"
            )
    return findings


def _scan_event_payload_fields(
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef], path: Path
) -> list[str]:
    findings: list[str] = []
    target = functions.get("_base_event")
    if target is None:
        return [f"{path}:1: missing _base_event helper"]
    forbidden_keys = {
        "assistant_body",
        "model_output",
        "body_text",
        "summary",
        "message",
    }
    for node in ast.walk(target):
        if isinstance(node, ast.Dict):
            for key in node.keys:
                value = _literal_str(key)
                if value in forbidden_keys:
                    findings.append(
                        f"{path}:{getattr(key, 'lineno', target.lineno)}: typed events must not carry free-form field `{value}`"
                    )
    return findings


def _scan_llm_calls(tree: ast.AST, path: Path) -> list[str]:
    findings: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            callee = node.func.id
        else:
            callee = _attribute_chain(node.func)
        lowered = callee.lower()
        if not lowered:
            continue
        if any(token in lowered for token in FORBIDDEN_LLM_OBJECT_TOKENS) and any(
            lowered.endswith(f".{name}") or lowered == name
            for name in FORBIDDEN_LLM_ATTRIBUTE_NAMES
        ):
            findings.append(
                f"{path}:{node.lineno}: recovery pipeline must not call LLM surface `{callee}`"
            )
    return findings


def validate(root: Path = RECOVERY_ROOT) -> list[str]:
    findings: list[str] = []
    if not root.exists():
        return [f"{root}:1: recovery root not found"]
    schemas_path = root / "schemas.py"
    pipeline_path = root / "pipeline.py"
    try:
        schema_tree = _parse_file(schemas_path)
        pipeline_tree = _parse_file(pipeline_path)
    except (OSError, SyntaxError, UnicodeDecodeError) as exc:
        return [f"{root}:1: unable to parse recovery pipeline: {exc}"]

    repair_members = _enum_members(schema_tree)
    pipeline_functions = _function_map(pipeline_tree)
    findings.extend(_scan_suspicious_function_names(pipeline_tree, pipeline_path))
    findings.extend(
        _scan_registered_repairs(pipeline_tree, pipeline_path, repair_members)
    )
    findings.extend(
        _scan_validation_message_function(pipeline_functions, pipeline_path)
    )
    findings.extend(_scan_event_payload_fields(pipeline_functions, pipeline_path))
    findings.extend(_scan_llm_calls(pipeline_tree, pipeline_path))
    findings.extend(_scan_llm_calls(schema_tree, schemas_path))
    return findings


def main() -> int:
    findings = validate()
    payload = {
        "validator": "validate_recovery_pipeline_contract",
        "ok": not findings,
        "findings": findings,
    }
    emit_json_report(
        "validate_recovery_pipeline_contract",
        payload,
        summary=(("recovery root", RECOVERY_ROOT), ("findings", len(findings))),
        findings=findings,
        ok_message="typed recovery-pipeline contract is clean.",
        report_stream=sys.stderr,
        json_stream=sys.stdout,
    )
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
