"""Prompt template loader.

Loads versioned markdown templates from `packages/prompts/<role>/<version>.md`
and performs `{{var}}` substitution. Keep the substitution deliberately
simple — no Jinja — so prompts stay portable (tested in isolation, diffed as
pure text).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from app.config import get_settings
from app.logging_setup import get_logger
from app.tools.client import ToolManifest

log = get_logger(__name__)

_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


@lru_cache(maxsize=32)
def load_template(role: str, version: str = "v1") -> str:
    """Return the raw markdown contents of a versioned template."""
    base = Path(get_settings().prompts_dir)
    path = base / role / f"{version}.md"
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        log.warning("prompts.missing", role=role, version=version, path=str(path))
        return ""


def render(template: str, **vars: Any) -> str:
    def _sub(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in vars and vars[key] is not None:
            return str(vars[key])
        return match.group(0)  # leave placeholder intact

    return _VAR_RE.sub(_sub, template)


def _format_tools_list(manifests: Iterable[ToolManifest]) -> str:
    lines = []
    for m in manifests:
        lines.append(f"- `{m.name}` ({m.category}): {m.description.splitlines()[0] if m.description else ''}")
    return "\n".join(lines) or "(暂无可用工具)"


def build_system_prompt(
    *,
    session_id: str,
    user_id: str | None,
    manifests: Iterable[ToolManifest] = (),
    extra_context: str | None = None,
) -> str:
    """Compose the system prompt shown to the executor each turn."""
    template = load_template("system", "v1")
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tools_block = _format_tools_list(manifests)

    rendered = render(
        template,
        session_id=session_id,
        user_id=user_id or "(匿名)",
        now_iso=now_iso,
        tools_list=f"\n{tools_block}",
    )
    if extra_context:
        rendered = f"{rendered}\n\n## 补充上下文\n{extra_context.strip()}\n"
    return rendered or _fallback_prompt(session_id, manifests)


def _fallback_prompt(session_id: str, manifests: Iterable[ToolManifest]) -> str:
    """Used if the template file is missing; keeps us from ever sending an empty system prompt."""
    return (
        "你是 Somna，一个通用 AI Agent。遵守如下纪律：\n"
        "- 任务优先、承认不确定、小步输出、失败不盲目重试\n"
        "- 文件和命令都在当前会话的沙盒中执行\n"
        f"- session_id = {session_id}\n"
        f"- 可用工具:\n{_format_tools_list(manifests)}\n"
    )
