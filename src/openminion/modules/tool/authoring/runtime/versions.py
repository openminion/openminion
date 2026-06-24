import hashlib


def compute_version_hash(*, source_code: str, unit_tests_source: str) -> str:
    payload = f"{source_code}\n---\n{unit_tests_source}".encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def build_tool_name(*, local_name: str, version_number: int) -> str:
    return f"authored.{local_name}@v{version_number}"


__all__ = ["build_tool_name", "compute_version_hash"]
