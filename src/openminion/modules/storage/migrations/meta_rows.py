from collections.abc import Iterable


def rows_to_meta(rows: Iterable[tuple[object, object]]) -> dict[str, str]:
    return {str(key): "" if value is None else str(value) for key, value in rows}


__all__ = ["rows_to_meta"]
