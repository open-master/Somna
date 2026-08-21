"""Shell tool — run bash commands in the sandbox workdir.

M2: host subprocess. Safe because the process runs as the non-root image user
and inside a named directory; NOT a strong isolation boundary.
M3: delegate to a per-session container, same request shape.
"""

from __future__ import annotations

import asyncio
import shlex
import time
import venv
from pathlib import Path
from typing import Any

from app.config import get_settings

from .base import BaseTool, ToolContext, ToolResult
from .registry import register

# Names that look like credentials must never reach the sandbox subprocess.
_SECRET_ENV_MARKERS = (
    "KEY",
    "SECRET",
    "TOKEN",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "DATABASE_URL",
    "POSTGRES",
    "MYSQL",
    "REDIS",
    "MONGO",
    "SMTP",
    "AWS_",
    "S3_",
    "JWT",
    "PRIVATE",
)

_BASE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def _is_denied_env_name(name: str) -> bool:
    upper = name.upper()
    return any(marker in upper for marker in _SECRET_ENV_MARKERS)


def sandbox_shell_environ(
    *,
    home: str,
    venv_dir: Path,
    extra: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Minimal env for sandbox bash. Do not copy the Hub process environment."""
    env = {
        "HOME": home,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TERM": "dumb",
        "PS1": "$ ",
        "PYTHONUNBUFFERED": "1",
        "VIRTUAL_ENV": str(venv_dir),
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PATH": f"{venv_dir / 'bin'}:{_BASE_PATH}",
    }
    for key, value in (extra or {}).items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        if not key or _is_denied_env_name(key):
            continue
        env[key] = value
    return env


@register
class ShellTool(BaseTool):
    name = "shell"
    description = (
        "Execute a bash command inside the session sandbox. Captures combined "
        "stdout/stderr and returns exit code. Use for file ops, running scripts, "
        "pip/npm installs, etc."
    )
    category = "os"
    mutates = True
    input_schema = {
        "type": "object",
        "required": ["cmd"],
        "properties": {
            "cmd": {
                "type": "string",
                "description": "Bash command line to execute (single string, shell will parse).",
            },
            "cwd": {
                "type": "string",
                "description": "Relative path inside the sandbox to use as working dir. Defaults to sandbox root.",
            },
            "timeout_sec": {
                "type": "integer",
                "minimum": 1,
                "description": "Hard timeout in seconds (capped by MCP_SHELL_MAX_TIMEOUT_SEC).",
            },
            "env": {
                "type": "object",
                "description": "Extra environment variables.",
                "additionalProperties": {"type": "string"},
            },
        },
        "additionalProperties": False,
    }

    async def invoke(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        settings = get_settings()
        cmd = args.get("cmd", "").strip()
        if not cmd:
            return ToolResult(ok=False, error="`cmd` is required")

        cap = settings.shell_max_timeout_sec
        timeout = int(args.get("timeout_sec") or settings.shell_default_timeout_sec)
        timeout = max(1, min(timeout, cap))

        cwd_arg = args.get("cwd") or "."
        # Use sandbox.safe_join via the caller, fall back to relative under workdir
        from app.sandbox.manager import PathEscapeError, get_sandbox_manager

        try:
            sm = get_sandbox_manager(settings)
            workdir = sm.safe_join(ctx.sandbox_id, cwd_arg)
        except PathEscapeError as e:
            return ToolResult(ok=False, error=str(e))

        workdir.mkdir(parents=True, exist_ok=True)
        sandbox_root = Path(sm.get_or_create(ctx.sandbox_id).workdir)
        try:
            venv_dir = await _ensure_python_venv(sandbox_root)
        except OSError as e:
            return ToolResult(ok=False, error=f"failed to initialize python venv: {e}")

        env = sandbox_shell_environ(
            home=ctx.workdir,
            venv_dir=venv_dir,
            extra=args.get("env") if isinstance(args.get("env"), dict) else None,
        )

        started = time.perf_counter()
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                cwd=str(workdir),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                stdout_bytes, _ = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                duration = int((time.perf_counter() - started) * 1000)
                return ToolResult(
                    ok=False,
                    error=f"timeout after {timeout}s",
                    preview=f"(timeout) $ {cmd}\n",
                    duration_ms=duration,
                    output={"exit_code": None, "timed_out": True, "cmd": cmd},
                )
        except (OSError, ValueError) as e:
            return ToolResult(ok=False, error=f"spawn failed: {e}")

        duration = int((time.perf_counter() - started) * 1000)
        rc = proc.returncode
        raw = stdout_bytes.decode("utf-8", errors="replace")

        preview_limit = settings.shell_output_preview_bytes
        truncated = len(raw) > preview_limit
        preview_body = raw[:preview_limit] + (
            f"\n... [truncated {len(raw) - preview_limit} more bytes]\n" if truncated else ""
        )
        preview = f"$ {_quote(cmd)}\n{preview_body}" if preview_body else f"$ {cmd}\n(no output)"

        return ToolResult(
            ok=rc == 0,
            error=None if rc == 0 else f"exit code {rc}",
            preview=preview,
            duration_ms=duration,
            output={
                "exit_code": rc,
                "stdout": raw,
                "truncated": truncated,
                "cmd": cmd,
                "cwd": str(workdir),
                "venv": str(venv_dir),
            },
        )


async def _ensure_python_venv(sandbox_root: Path) -> Path:
    """Create a per-sandbox virtualenv on first shell use.

    This gives each session an isolated Python package space so the agent can
    `pip install` missing dependencies (pandas, plotly, etc.) without polluting
    the global mcp-hub image.
    """
    venv_dir = sandbox_root / ".venv"
    python_bin = venv_dir / "bin" / "python"
    if python_bin.exists():
        return venv_dir

    def _create() -> None:
        builder = venv.EnvBuilder(with_pip=True, clear=False, symlinks=True)
        builder.create(venv_dir)

    await asyncio.to_thread(_create)
    return venv_dir


def _quote(cmd: str) -> str:
    # For preview readability; not security.
    return cmd if len(cmd) <= 200 else shlex.quote(cmd[:200]) + "..."
