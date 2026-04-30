"""Shell tool — run bash commands in the sandbox workdir.

M2: host subprocess. Safe because the process runs as the non-root image user
and inside a named directory; NOT a strong isolation boundary.
M3: delegate to a per-session container, same request shape.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import shlex
import time
from typing import Any
import venv

from app.config import get_settings

from .base import BaseTool, ToolContext, ToolResult
from .registry import register


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
        from app.sandbox.manager import get_sandbox_manager, PathEscapeError

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

        env = os.environ.copy()
        env.update(
            {
                "PS1": "$ ",
                "PYTHONUNBUFFERED": "1",
                "HOME": ctx.workdir,
                "VIRTUAL_ENV": str(venv_dir),
                "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            }
        )
        env["PATH"] = f"{venv_dir / 'bin'}:{env.get('PATH', '')}"
        for k, v in (args.get("env") or {}).items():
            if isinstance(k, str) and isinstance(v, str):
                env[k] = v

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
