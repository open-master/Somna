"""Filesystem tool — read / write / append / list / stat / delete in a sandbox.

A single tool with a dispatched `action` keeps the LLM surface small.
"""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path
from typing import Any

import aiofiles

from app.config import get_settings
from app.sandbox.manager import PathEscapeError, get_sandbox_manager

from .base import BaseTool, ToolContext, ToolResult
from .registry import register


@register
class FilesystemTool(BaseTool):
    name = "filesystem"
    description = (
        "Read, write, append, list, stat or delete files inside the session sandbox. "
        "All paths are relative to the sandbox root; absolute paths and `..` segments "
        "are rejected."
    )
    category = "file"
    mutates = True
    input_schema = {
        "type": "object",
        "required": ["action", "path"],
        "properties": {
            "action": {
                "type": "string",
                "enum": ["read", "write", "append", "list", "stat", "delete"],
            },
            "path": {
                "type": "string",
                "description": "Sandbox-relative path. Use '.' for the sandbox root (list / stat).",
            },
            "content": {
                "type": "string",
                "description": "UTF-8 text content for write/append. Ignored for other actions.",
            },
            "binary_base64": {
                "type": "string",
                "description": "Base64 payload for write (alternative to `content`).",
            },
            "encoding": {
                "type": "string",
                "enum": ["utf-8", "base64"],
                "default": "utf-8",
                "description": "Output encoding for `read`.",
            },
            "recursive": {
                "type": "boolean",
                "default": False,
                "description": "For `list` only — walk subdirectories.",
            },
        },
        "additionalProperties": False,
    }

    async def invoke(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        settings = get_settings()
        action = args.get("action")
        raw_path = args.get("path", "")

        sm = get_sandbox_manager(settings)
        try:
            target = sm.safe_join(ctx.sandbox_id, raw_path)
        except PathEscapeError as e:
            return ToolResult(ok=False, error=str(e))

        started = time.perf_counter()
        try:
            if action == "read":
                result = await _action_read(target, args, settings)
            elif action == "write":
                result = await _action_write(target, args, settings, append=False)
            elif action == "append":
                result = await _action_write(target, args, settings, append=True)
            elif action == "list":
                result = _action_list(target, args, settings)
            elif action == "stat":
                result = _action_stat(target)
            elif action == "delete":
                result = _action_delete(target)
            else:
                return ToolResult(ok=False, error=f"unknown action: {action!r}")
        except FileNotFoundError as e:
            return ToolResult(ok=False, error=f"not found: {e}")
        except PermissionError as e:
            return ToolResult(ok=False, error=f"permission denied: {e}")
        except OSError as e:
            return ToolResult(ok=False, error=f"io error: {e}")

        result.duration_ms = int((time.perf_counter() - started) * 1000)
        return result


# ---------- per-action helpers ----------


async def _action_read(target: Path, args: dict[str, Any], settings) -> ToolResult:
    if not target.is_file():
        return ToolResult(ok=False, error=f"not a file: {target.name}")
    size = target.stat().st_size
    if size > settings.fs_max_file_bytes:
        return ToolResult(
            ok=False,
            error=f"file {target.name} is {size} bytes; exceeds limit {settings.fs_max_file_bytes}",
        )
    async with aiofiles.open(target, "rb") as f:
        data = await f.read()
    encoding = args.get("encoding", "utf-8")
    if encoding == "base64":
        body = base64.b64encode(data).decode("ascii")
        preview = f"[{size} bytes, base64]\n{body[:500]}"
    else:
        try:
            body = data.decode("utf-8")
        except UnicodeDecodeError:
            body = data.decode("utf-8", errors="replace")
        preview = body if len(body) <= 4096 else body[:4096] + f"\n... [truncated {len(body) - 4096} chars]"
    return ToolResult(
        ok=True,
        preview=preview,
        output={"size": size, "encoding": encoding, "content": body},
    )


async def _action_write(target: Path, args: dict[str, Any], settings, *, append: bool) -> ToolResult:
    target.parent.mkdir(parents=True, exist_ok=True)
    if "binary_base64" in args and args["binary_base64"] is not None:
        try:
            payload = base64.b64decode(args["binary_base64"], validate=True)
        except Exception as e:
            return ToolResult(ok=False, error=f"invalid base64: {e}")
    else:
        payload = (args.get("content") or "").encode("utf-8")

    if len(payload) > settings.fs_max_file_bytes:
        return ToolResult(
            ok=False,
            error=f"payload {len(payload)} bytes exceeds limit {settings.fs_max_file_bytes}",
        )

    mode = "ab" if append else "wb"
    async with aiofiles.open(target, mode) as f:
        await f.write(payload)
    return ToolResult(
        ok=True,
        preview=f"{'appended' if append else 'wrote'} {len(payload)} bytes → {_rel(target)}",
        output={"size": len(payload), "path": str(target)},
    )


def _action_list(target: Path, args: dict[str, Any], settings) -> ToolResult:
    if not target.exists():
        return ToolResult(ok=False, error=f"not found: {_rel(target)}")
    if not target.is_dir():
        return ToolResult(ok=False, error=f"not a directory: {_rel(target)}")

    recursive = bool(args.get("recursive"))
    entries: list[dict[str, Any]] = []
    limit = settings.fs_max_list_entries

    if recursive:
        for root, dirs, files in os.walk(target):
            rp = Path(root)
            for d in dirs:
                entries.append(_entry(rp / d, is_dir=True))
                if len(entries) >= limit:
                    break
            for fn in files:
                entries.append(_entry(rp / fn, is_dir=False))
                if len(entries) >= limit:
                    break
            if len(entries) >= limit:
                break
    else:
        for child in sorted(target.iterdir()):
            entries.append(_entry(child, is_dir=child.is_dir()))
            if len(entries) >= limit:
                break

    preview_lines = [
        f"{'d' if e['is_dir'] else 'f'}  {e['size']:>10}  {e['path']}"
        for e in entries[:40]
    ]
    preview = "\n".join(preview_lines) or "(empty)"
    if len(entries) > 40:
        preview += f"\n... {len(entries) - 40} more"
    return ToolResult(ok=True, preview=preview, output={"entries": entries, "truncated": len(entries) >= limit})


def _action_stat(target: Path) -> ToolResult:
    if not target.exists():
        return ToolResult(ok=False, error=f"not found: {_rel(target)}")
    st = target.stat()
    info = {
        "path": str(target),
        "is_dir": target.is_dir(),
        "is_file": target.is_file(),
        "size": st.st_size,
        "mtime": st.st_mtime,
        "ctime": st.st_ctime,
    }
    return ToolResult(ok=True, preview=_fmt_stat(info), output=info)


def _action_delete(target: Path) -> ToolResult:
    if not target.exists():
        return ToolResult(ok=False, error=f"not found: {_rel(target)}")
    if target.is_dir():
        # Only empty dirs are removed to avoid foot-guns; use shell for rmdir -rf.
        try:
            target.rmdir()
        except OSError as e:
            return ToolResult(ok=False, error=f"dir not empty: {e}")
        return ToolResult(ok=True, preview=f"rmdir {_rel(target)}", output={"removed": str(target)})
    target.unlink()
    return ToolResult(ok=True, preview=f"rm {_rel(target)}", output={"removed": str(target)})


# ---------- util ----------


def _entry(p: Path, *, is_dir: bool) -> dict[str, Any]:
    try:
        size = 0 if is_dir else p.stat().st_size
    except OSError:
        size = 0
    return {"path": str(p), "name": p.name, "is_dir": is_dir, "size": size}


def _rel(p: Path) -> str:
    return p.name or str(p)


def _fmt_stat(info: dict[str, Any]) -> str:
    kind = "dir" if info["is_dir"] else "file"
    return f"{kind}  size={info['size']}  mtime={info['mtime']:.0f}  {info['path']}"
