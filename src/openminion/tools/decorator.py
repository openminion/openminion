import inspect
from typing import Any, Optional, overload
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, create_model


def _build_args_model(func: Callable[..., Any], model_name: str) -> type[BaseModel]:
    """Generate a Pydantic model that mirrors ``func``'s signature."""

    signature = inspect.signature(func)
    fields: dict[str, tuple[Any, Any]] = {}
    for name, param in signature.parameters.items():
        if name in ("self", "cls"):
            continue
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            # *args / **kwargs are not surfaced to the model; runtime callers
            # must pass declared params only.
            continue
        annotation = param.annotation
        if annotation is inspect.Parameter.empty:
            annotation = Any
        default = param.default if param.default is not inspect.Parameter.empty else ...
        fields[name] = (annotation, default)
    return create_model(
        model_name,
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )


def _infer_description(func: Callable[..., Any]) -> str:
    raw_name = str(getattr(func, "__name__", "") or "").strip("_")
    words = [part for part in raw_name.replace("-", "_").split("_") if part]
    if not words:
        return ""

    subject = words[0].capitalize()
    parameters = [
        param
        for name, param in inspect.signature(func).parameters.items()
        if name not in {"self", "cls"}
        and param.kind
        not in {
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        }
    ]
    if not parameters:
        return f"{subject}."

    annotations = [param.annotation for param in parameters]

    def _annotation_name(annotation: Any) -> str:
        if annotation is inspect.Parameter.empty:
            return ""
        if isinstance(annotation, str):
            return annotation.strip().lower()
        return str(getattr(annotation, "__name__", annotation)).strip().lower()

    annotation_names = [_annotation_name(annotation) for annotation in annotations]
    if all(name == "int" for name in annotation_names):
        quantity = {
            1: "an integer",
            2: "two integers",
        }.get(len(parameters), f"{len(parameters)} integers")
        return f"{subject} {quantity}."

    if all(name == "str" for name in annotation_names):
        quantity = {
            1: "a string",
            2: "two strings",
        }.get(len(parameters), f"{len(parameters)} strings")
        return f"{subject} {quantity}."

    return f"{subject}."


def _decorate(
    func: Callable[..., Any],
    *,
    name: Optional[str],
    description: Optional[str],
    min_scope: Optional[str],
    dangerous: bool,
    idempotent: bool,
    tags: tuple[str, ...],
    capabilities: tuple[str, ...],
) -> Callable[..., Any]:
    # Lazy framework import — see module-level note on circular import.
    from openminion.modules.tool.framework import ToolDecl, ToolFamilySpec

    resolved_name = (name or func.__name__).strip()
    resolved_description = (
        description or (func.__doc__ or "").strip() or _infer_description(func)
    ).strip()
    args_model = _build_args_model(func, f"{resolved_name.replace('.', '_')}Args")

    def _handler(args: BaseModel) -> Any:
        # The runtime invokes the handler with the validated args model;
        # unpack it into kwargs for the user's function so they don't have to
        # know about the model layer.
        return func(**args.model_dump())

    decl = ToolDecl(
        name=resolved_name,
        args_model=args_model,
        handler=_handler,
        description=resolved_description,
        min_scope=min_scope,
        dangerous=dangerous,
        idempotent=idempotent,
        tags=tags,
        capabilities=capabilities,
    )

    def _family_spec(
        module_id: Optional[str] = None,
        *,
        min_scope_default: str = "WRITE_SAFE",
    ) -> ToolFamilySpec:
        return ToolFamilySpec(
            module_id=module_id or f"openminion.tools.user.{resolved_name}",
            tools=(decl,),
            min_scope_default=min_scope_default,
        )

    func.tool_decl = decl  # type: ignore[attr-defined]
    func.tool_family_spec = _family_spec  # type: ignore[attr-defined]
    func.tool_args_model = args_model  # type: ignore[attr-defined]
    return func


@overload
def tool(func: Callable[..., Any]) -> Callable[..., Any]: ...


@overload
def tool(
    *,
    name: Optional[str] = ...,
    description: Optional[str] = ...,
    min_scope: Optional[str] = ...,
    dangerous: bool = ...,
    idempotent: bool = ...,
    tags: tuple[str, ...] = ...,
    capabilities: tuple[str, ...] = ...,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]: ...


def tool(
    func: Callable[..., Any] | None = None,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    min_scope: Optional[str] = None,
    dangerous: bool = False,
    idempotent: bool = False,
    tags: tuple[str, ...] = (),
    capabilities: tuple[str, ...] = (),
) -> Any:
    """Decorate a Python function as an openminion tool.

    Both bare and parameterized forms are supported. See module docstring
    for the design contract.
    """

    if func is not None and callable(func) and name is None and description is None:
        return _decorate(
            func,
            name=None,
            description=None,
            min_scope=None,
            dangerous=False,
            idempotent=False,
            tags=(),
            capabilities=(),
        )

    def _wrap(actual: Callable[..., Any]) -> Callable[..., Any]:
        return _decorate(
            actual,
            name=name,
            description=description,
            min_scope=min_scope,
            dangerous=dangerous,
            idempotent=idempotent,
            tags=tags,
            capabilities=capabilities,
        )

    return _wrap
