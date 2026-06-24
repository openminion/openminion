import base64
import fnmatch
import platform
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable

import psutil

from ..errors import ToolRuntimeError
from . import RuntimeContext, redact_text


def _resolve_path(ctx: RuntimeContext, raw_path: str, operation: str) -> Path:
    return ctx.policy.ensure_path_allowed(
        raw_path, workspace=ctx.workspace, operation=operation
    )


def _truncate_text(value: str, max_bytes: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return value, False
    return encoded[:max_bytes].decode("utf-8", errors="replace"), True


def h_fs_list_dir(args: Dict[str, Any], ctx: RuntimeContext) -> Dict[str, Any]:
    root = _resolve_path(ctx, args["path"], "read")
    recursive = bool(args.get("recursive", False))
    include_hidden = bool(args.get("include_hidden", False))
    pattern = args.get("pattern")
    max_entries = int(
        args.get("max_entries") or ctx.policy.limit_int("fs_list_max_entries", 500)
    )

    if not root.exists():
        raise ToolRuntimeError("NOT_FOUND", f"Path not found: {root}")
    if not root.is_dir():
        raise ToolRuntimeError("INVALID_ARGUMENT", f"Path is not a directory: {root}")

    entries: list[Dict[str, Any]] = []
    queue: list[Path] = [root]
    truncated = False

    while queue and len(entries) < max_entries:
        current = queue.pop(0)
        try:
            children = sorted(current.iterdir(), key=lambda p: p.name.lower())
        except OSError as exc:
            raise ToolRuntimeError(
                "EXEC_ERROR",
                f"Unable to list directory: {current}",
                {"error": str(exc)},
            ) from exc

        for child in children:
            if len(entries) >= max_entries:
                truncated = True
                break
            if not include_hidden and child.name.startswith("."):
                continue

            # Re-authorize each discovered child to enforce deny roots inside
            # a generally allowed subtree and avoid symlink escapes.
            try:
                _resolve_path(ctx, str(child), "read")
            except ToolRuntimeError as exc:
                if exc.code == "POLICY_DENIED":
                    continue
                raise

            rel_from_root = str(child.relative_to(root))
            if pattern and not fnmatch.fnmatch(rel_from_root, pattern):
                if recursive and child.is_dir() and not child.is_symlink():
                    queue.append(child)
                continue
            stat = child.lstat()
            is_symlink = child.is_symlink()
            is_dir = (not is_symlink) and child.is_dir()
            is_file = (not is_symlink) and child.is_file()
            kind = (
                "symlink"
                if is_symlink
                else "dir"
                if is_dir
                else "file"
                if is_file
                else "other"
            )
            entries.append(
                {
                    "name": child.name,
                    "path": str(child),
                    "kind": kind,
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                }
            )
            if recursive and is_dir:
                queue.append(child)

    return {"entries": entries, "truncated": truncated}


def h_fs_read_file(args: Dict[str, Any], ctx: RuntimeContext) -> Dict[str, Any]:
    path = _resolve_path(ctx, args["path"], "read")
    if not path.exists():
        raise ToolRuntimeError("NOT_FOUND", f"File not found: {path}")
    if not path.is_file():
        raise ToolRuntimeError("INVALID_ARGUMENT", f"Path is not a file: {path}")

    max_bytes = int(
        args.get("max_bytes") or ctx.policy.limit_int("file_max_read_bytes", 200000)
    )
    binary = bool(args.get("binary", False))
    encoding = str(args.get("encoding", "utf-8"))

    raw = path.read_bytes()
    truncated = len(raw) > max_bytes
    visible = raw[:max_bytes]

    if binary:
        return {
            "path": str(path),
            "base64": base64.b64encode(visible).decode("ascii"),
            "truncated": truncated,
            "bytes_read": len(visible),
            "total_bytes": len(raw),
        }

    text = visible.decode(encoding, errors="replace")
    text = redact_text(text, ctx.policy.redaction_mode())
    return {
        "path": str(path),
        "content": text,
        "truncated": truncated,
        "bytes_read": len(visible),
        "total_bytes": len(raw),
    }


def h_fs_write_file(args: Dict[str, Any], ctx: RuntimeContext) -> Dict[str, Any]:
    path = _resolve_path(ctx, args["path"], "write")
    content = args.get("content")
    base64_content = args.get("base64")
    mode = str(args.get("mode", "overwrite"))
    mkdirs = bool(args.get("mkdirs", True))
    atomic = bool(args.get("atomic", True))

    if content is None and base64_content is None:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT", "Either 'content' or 'base64' must be provided"
        )
    if content is not None and base64_content is not None:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT", "Provide only one of 'content' or 'base64'"
        )

    if mkdirs:
        path.parent.mkdir(parents=True, exist_ok=True)

    if base64_content is not None:
        try:
            payload = base64.b64decode(base64_content)
        except Exception as exc:
            raise ToolRuntimeError(
                "INVALID_ARGUMENT", "Invalid base64 payload"
            ) from exc
    else:
        payload = str(content).encode("utf-8")

    if mode == "create_only" and path.exists():
        raise ToolRuntimeError("EXEC_ERROR", f"File already exists: {path}")

    if mode == "append":
        with path.open("ab") as f:
            f.write(payload)
    else:
        if atomic and mode == "overwrite":
            with tempfile.NamedTemporaryFile(delete=False, dir=str(path.parent)) as tmp:
                tmp.write(payload)
                tmp_path = Path(tmp.name)
            tmp_path.replace(path)
        else:
            path.write_bytes(payload)

    return {"bytes_written": len(payload), "final_path": str(path)}


def _remove_existing(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def h_fs_copy(args: Dict[str, Any], ctx: RuntimeContext) -> Dict[str, Any]:
    src = _resolve_path(ctx, args["src"], "read")
    dst = _resolve_path(ctx, args["dst"], "write")
    overwrite = bool(args.get("overwrite", False))
    recursive = bool(args.get("recursive", True))
    preserve_metadata = bool(args.get("preserve_metadata", False))
    if not src.exists():
        raise ToolRuntimeError("NOT_FOUND", f"Source not found: {src}")
    if dst.exists() and not overwrite:
        raise ToolRuntimeError(
            "EXEC_ERROR", f"Destination exists and overwrite=false: {dst}"
        )

    if src.is_dir():
        if not recursive:
            raise ToolRuntimeError(
                "INVALID_ARGUMENT", "Source is a directory but recursive=false"
            )
        copy_fn = shutil.copy2 if preserve_metadata else shutil.copy
        if dst.exists() and overwrite:
            _remove_existing(dst)
        # Preserve symlinks as symlinks so copy does not dereference and read
        # targets that may be outside authorized roots.
        shutil.copytree(src, dst, copy_function=copy_fn, symlinks=True)
        return {"copied": True, "kind": "dir", "src": str(src), "dst": str(dst)}

    if dst.exists() and overwrite:
        _remove_existing(dst)
    copy_file_fn = shutil.copy2 if preserve_metadata else shutil.copy
    copy_file_fn(src, dst)
    return {"copied": True, "kind": "file", "src": str(src), "dst": str(dst)}


def h_fs_move(args: Dict[str, Any], ctx: RuntimeContext) -> Dict[str, Any]:
    src = _resolve_path(ctx, args["src"], "write")
    dst = _resolve_path(ctx, args["dst"], "write")
    overwrite = bool(args.get("overwrite", False))
    if not src.exists():
        raise ToolRuntimeError("NOT_FOUND", f"Source not found: {src}")
    if dst.exists() and not overwrite:
        raise ToolRuntimeError(
            "EXEC_ERROR", f"Destination exists and overwrite=false: {dst}"
        )
    if dst.exists() and overwrite:
        _remove_existing(dst)
    shutil.move(str(src), str(dst))
    return {"moved": True, "src": str(src), "dst": str(dst)}


def h_fs_delete(args: Dict[str, Any], ctx: RuntimeContext) -> Dict[str, Any]:
    path = _resolve_path(ctx, args["path"], "write")
    recursive = bool(args.get("recursive", False))
    if not path.exists():
        return {"deleted": False, "path": str(path)}
    if path.is_dir():
        if not recursive:
            raise ToolRuntimeError(
                "POLICY_DENIED", "Refusing to delete directory without recursive=true"
            )
        shutil.rmtree(path)
    else:
        path.unlink()
    return {"deleted": True, "path": str(path)}


def _iter_search_files(root: Path, file_glob: str) -> Iterable[Path]:
    pattern = "*" if file_glob == "**/*" else file_glob
    yield from root.rglob(pattern)


def h_fs_search(args: Dict[str, Any], ctx: RuntimeContext) -> Dict[str, Any]:
    root = _resolve_path(ctx, args["root"], "read")
    if not root.exists():
        raise ToolRuntimeError("NOT_FOUND", f"Path not found: {root}")
    if not root.is_dir():
        raise ToolRuntimeError("INVALID_ARGUMENT", f"Path is not a directory: {root}")
    query = str(args["query"])
    regex = bool(args.get("regex", False))
    file_glob = str(args.get("file_glob", "**/*"))
    max_matches = int(args.get("max_matches", 200))
    max_read_bytes = ctx.policy.limit_int("file_max_read_bytes", 200000)
    try:
        matcher = re.compile(query) if regex else None
    except re.error as exc:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            "Invalid regex pattern for file.search",
            {"query": query},
        ) from exc

    matches: list[Dict[str, Any]] = []
    scanned = 0
    for path in _iter_search_files(root, file_glob):
        if len(matches) >= max_matches:
            break
        if not path.is_file():
            continue
        if path.is_symlink():
            continue
        try:
            _resolve_path(ctx, str(path), "read")
        except ToolRuntimeError as exc:
            if exc.code == "POLICY_DENIED":
                continue
            raise
        scanned += 1
        try:
            payload = path.read_bytes()
        except OSError:
            continue
        if len(payload) > max_read_bytes:
            continue
        if b"\x00" in payload:
            continue
        text = payload.decode("utf-8", errors="replace")
        for line_no, line in enumerate(text.splitlines(), start=1):
            ok = bool(matcher.search(line)) if matcher else (query in line)
            if not ok:
                continue
            matches.append(
                {
                    "path": str(path),
                    "line": line_no,
                    "snippet": line[:240],
                }
            )
            if len(matches) >= max_matches:
                break
    return {"matches": matches, "count": len(matches), "scanned_files": scanned}


def h_cmd_which(args: Dict[str, Any], ctx: RuntimeContext) -> Dict[str, Any]:
    del ctx
    return {"path": shutil.which(args["name"])}


def h_cmd_run(args: Dict[str, Any], ctx: RuntimeContext) -> Dict[str, Any]:
    argv = [str(item) for item in args["argv"]]
    cwd_path = _resolve_path(ctx, str(args.get("cwd", ".")), "read")
    if not cwd_path.exists() or not cwd_path.is_dir():
        raise ToolRuntimeError(
            "NOT_FOUND", f"cwd not found or not a directory: {cwd_path}"
        )
    ctx.policy.ensure_command_allowed(argv)

    timeout = int(
        args.get("timeout_sec") or ctx.policy.limit_int("cmd_timeout_sec", 45)
    )
    max_output_bytes = int(
        args.get("max_output_bytes")
        or ctx.policy.limit_int("cmd_max_output_bytes", 200000)
    )
    allowed_exit_codes = set(args.get("allowed_exit_codes", [0]))
    capture = bool(args.get("capture", True))
    stdin = args.get("stdin")

    env = ctx.policy.filter_env(args.get("env", {}))
    ctx.add_log(
        "info",
        "Running command",
        {"argv": argv, "cwd": str(cwd_path), "timeout": timeout},
    )

    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd_path),
            env=env,
            input=stdin,
            text=True,
            capture_output=capture,
            shell=False,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ToolRuntimeError("NOT_FOUND", f"Executable not found: {argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        stdout = (exc.stdout or "").encode("utf-8", errors="replace")
        stderr = (exc.stderr or "").encode("utf-8", errors="replace")
        if stdout:
            ctx.write_artifact("artifacts/stdout_timeout.txt", stdout, "text/plain")
        if stderr:
            ctx.write_artifact("artifacts/stderr_timeout.txt", stderr, "text/plain")
        raise ToolRuntimeError(
            "TIMEOUT", "Command timed out", {"timeout_sec": timeout}
        ) from exc

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""

    stdout_bytes = stdout.encode("utf-8", errors="replace")
    stderr_bytes = stderr.encode("utf-8", errors="replace")
    if len(stdout_bytes) > max_output_bytes:
        ctx.write_artifact("artifacts/stdout_full.txt", stdout_bytes, "text/plain")
    if len(stderr_bytes) > max_output_bytes:
        ctx.write_artifact("artifacts/stderr_full.txt", stderr_bytes, "text/plain")

    trunc_stdout, stdout_trunc = _truncate_text(stdout, max_output_bytes)
    trunc_stderr, stderr_trunc = _truncate_text(stderr, max_output_bytes)
    redaction_mode = ctx.policy.redaction_mode()
    trunc_stdout = redact_text(trunc_stdout, redaction_mode)
    trunc_stderr = redact_text(trunc_stderr, redaction_mode)

    result = {
        "exit_code": proc.returncode,
        "stdout": trunc_stdout,
        "stderr": trunc_stderr,
        "truncated": stdout_trunc or stderr_trunc,
        "exec_path": shutil.which(argv[0]) or argv[0],
    }

    if proc.returncode not in allowed_exit_codes:
        raise ToolRuntimeError(
            "EXEC_ERROR",
            f"Command exited with code {proc.returncode}",
            {
                "exit_code": proc.returncode,
                "allowed_exit_codes": sorted(allowed_exit_codes),
                **result,
            },
        )

    return result


def h_proc_list(args: Dict[str, Any], ctx: RuntimeContext) -> Dict[str, Any]:
    contains = args.get("contains")
    user_only = bool(args.get("user_only", True))
    limit = int(args.get("limit", 200))
    sort_by = str(args.get("sort_by", "cpu"))
    me = psutil.Process().username()

    items: list[Dict[str, Any]] = []
    for proc in psutil.process_iter(
        attrs=["pid", "name", "username", "cmdline", "cpu_percent", "memory_percent"]
    ):
        info = proc.info
        if user_only and info.get("username") != me:
            continue
        name = info.get("name") or ""
        cmdline = " ".join(info.get("cmdline") or [])
        if contains and contains not in name and contains not in cmdline:
            continue
        items.append(
            {
                "pid": info.get("pid"),
                "name": name,
                "user": info.get("username"),
                "cpu": info.get("cpu_percent") or 0.0,
                "mem": info.get("memory_percent") or 0.0,
                "cmdline": redact_text(cmdline, ctx.policy.redaction_mode()),
            }
        )

    reverse = sort_by in ("cpu", "mem")
    items.sort(key=lambda item: item.get(sort_by, 0), reverse=reverse)
    return {"processes": items[:limit], "count": len(items)}


def h_proc_details(args: Dict[str, Any], ctx: RuntimeContext) -> Dict[str, Any]:
    pid = int(args["pid"])
    include_open_files = bool(args.get("include_open_files", False))
    include_connections = bool(args.get("include_connections", False))
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess as exc:
        raise ToolRuntimeError("NOT_FOUND", f"Process not found: {pid}") from exc

    try:
        info = proc.as_dict(
            attrs=["pid", "name", "username", "status", "create_time", "cmdline"]
        )
    except psutil.Error as exc:
        raise ToolRuntimeError(
            "EXEC_ERROR", f"Unable to inspect process: {pid}", {"error": str(exc)}
        ) from exc

    data: Dict[str, Any] = {
        "pid": info.get("pid"),
        "name": info.get("name"),
        "user": info.get("username"),
        "status": info.get("status"),
        "start_time": info.get("create_time"),
        "cmdline": redact_text(
            " ".join(info.get("cmdline") or []), ctx.policy.redaction_mode()
        ),
        "parent_pid": proc.ppid(),
        "children": [child.pid for child in proc.children(recursive=False)],
    }

    if include_open_files:
        try:
            data["open_files"] = [
                {"path": f.path, "fd": f.fd} for f in proc.open_files()
            ]
        except psutil.Error:
            data["open_files"] = []

    if include_connections:
        try:
            conns = []
            for conn in proc.connections(kind="inet"):
                conns.append(
                    {
                        "fd": conn.fd,
                        "family": int(conn.family),
                        "type": int(conn.type),
                        "laddr": str(conn.laddr),
                        "raddr": str(conn.raddr),
                        "status": conn.status,
                    }
                )
            data["connections"] = conns
        except psutil.Error:
            data["connections"] = []

    return data


def h_proc_kill(args: Dict[str, Any], ctx: RuntimeContext) -> Dict[str, Any]:
    pid = int(args["pid"])
    signal_name = str(args.get("signal", "TERM")).upper()
    if not ctx.confirm:
        raise ToolRuntimeError(
            "POLICY_DENIED", "proc.kill requires explicit confirmation"
        )
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess as exc:
        raise ToolRuntimeError("NOT_FOUND", f"Process not found: {pid}") from exc

    if signal_name == "KILL":
        proc.kill()
    else:
        proc.terminate()
    return {"pid": pid, "signal": signal_name, "signaled": True}


def h_sys_info(args: Dict[str, Any], ctx: RuntimeContext) -> Dict[str, Any]:
    include_disks = bool(args.get("include_disks", True))
    include_net_ifaces = bool(args.get("include_net_ifaces", False))
    vm = psutil.virtual_memory()
    data: Dict[str, Any] = {
        "os": platform.platform(),
        "python": platform.python_version(),
        "cpu_count": psutil.cpu_count(logical=True),
        "ram_total": vm.total,
        "ram_available": vm.available,
    }
    if include_disks:
        disk = psutil.disk_usage(str(ctx.workspace))
        data["workspace_disk"] = {
            "total": disk.total,
            "used": disk.used,
            "free": disk.free,
        }
    if include_net_ifaces:
        data["net_ifaces"] = list(psutil.net_if_addrs().keys())
    return data
