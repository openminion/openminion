import shutil
import subprocess
from typing import Optional

from .contracts import SandboxExecResult


class PyodideSandboxAdapter:
    name = "pyodide"

    def __init__(self, *, deno_path: Optional[str] = None) -> None:
        self.deno_path = deno_path or shutil.which("deno")

    def _ensure_deno(self) -> str:
        if not self.deno_path:
            raise RuntimeError(
                "PyodideSandboxAdapter requires the 'deno' binary on PATH. "
                "Install: https://deno.land/#installation."
            )
        return self.deno_path

    def exec(
        self,
        command: list[str],
        *,
        cwd: Optional[str] = None,
        env: Optional[dict[str, str]] = None,
        timeout_seconds: Optional[int] = None,
    ) -> SandboxExecResult:
        deno = self._ensure_deno()
        if not (
            len(command) >= 3
            and command[0] in ("python", "python3", "python3.11")
            and command[1] == "-c"
        ):
            raise ValueError(
                "PyodideSandboxAdapter only accepts ['python', '-c', '<source>'] "
                "invocations. For shell commands, use a different sandbox."
            )
        source = command[2]
        # Wrap the user source so it runs inside Pyodide via Deno.
        deno_source = _PYODIDE_DENO_TEMPLATE.format(source_json=_json_quote(source))
        completed = subprocess.run(
            [deno, "run", "--allow-all", "-"],
            input=deno_source,
            text=True,
            capture_output=True,
            cwd=cwd,
            env=env,
            timeout=timeout_seconds,
        )
        return SandboxExecResult(
            exit_code=int(completed.returncode),
            stdout=completed.stdout,
            stderr=completed.stderr,
            meta={"runtime": "deno-pyodide"},
        )

    def close(self) -> None:
        # Pyodide+Deno is stateless per-invocation — nothing to release.
        return


def _json_quote(text: str) -> str:
    import json

    return json.dumps(text)


_PYODIDE_DENO_TEMPLATE = """\
import {{ loadPyodide }} from "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/pyodide.mjs";
const pyodide = await loadPyodide();
const src = {source_json};
try {{
    const result = await pyodide.runPythonAsync(src);
    if (result !== undefined) {{
        console.log(result);
    }}
}} catch (err) {{
    console.error(String(err));
    Deno.exit(1);
}}
"""
