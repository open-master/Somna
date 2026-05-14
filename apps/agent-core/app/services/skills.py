"""Claude-compatible Skill storage, validation, and prompt formatting."""

from __future__ import annotations

from dataclasses import dataclass
import io
import json
import re
from pathlib import PurePosixPath
import uuid
import zipfile
from typing import Any

from fastapi import HTTPException

from app.api.deps import CurrentUser
from app.config import get_settings
from app.llm.client import get_async_openai
from app.logging_setup import get_logger
from app.storage.postgres import get_pool

log = get_logger(__name__)

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_MAX_SKILL_MD_BYTES = 256_000
_MAX_SKILL_FILES = 80
_MAX_SKILL_PACKAGE_BYTES = 2_000_000
_MAX_PROMPT_SKILLS = 4
_MAX_SKILL_PROMPT_CHARS = 12_000


async def ensure_skill_tables() -> None:
    """Create Skill tables for existing dev databases that already passed init scripts."""
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS skills (
                id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                owner_user_id  UUID REFERENCES users(id) ON DELETE SET NULL,
                name           TEXT NOT NULL,
                title          TEXT,
                description    TEXT NOT NULL,
                visibility     TEXT NOT NULL DEFAULT 'private'
                               CHECK (visibility IN ('private','shared','official')),
                source         TEXT NOT NULL DEFAULT 'manual'
                               CHECK (source IN ('upload','manual','generated','official')),
                status         TEXT NOT NULL DEFAULT 'active'
                               CHECK (status IN ('draft','active','archived')),
                version        INTEGER NOT NULL DEFAULT 1,
                skill_md       TEXT NOT NULL,
                files          JSONB NOT NULL DEFAULT '{}'::jsonb,
                metadata       JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE(owner_user_id, name, version)
            )
            """
        )
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_skills_owner ON skills(owner_user_id, created_at DESC)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_skills_market ON skills(visibility, status, updated_at DESC)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_skills_name ON skills(name)")
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_skills (
                user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                skill_id     UUID NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
                enabled      BOOLEAN NOT NULL DEFAULT true,
                installed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (user_id, skill_id)
            )
            """
        )
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_user_skills_enabled ON user_skills(user_id, enabled)")
        await conn.execute(
            """
            DO $$ BEGIN
                CREATE TRIGGER trg_skills_touch BEFORE UPDATE ON skills
                    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
            EXCEPTION WHEN duplicate_object THEN NULL; END $$
            """
        )
        await conn.execute(
            """
            DO $$ BEGIN
                CREATE TRIGGER trg_user_skills_touch BEFORE UPDATE ON user_skills
                    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
            EXCEPTION WHEN duplicate_object THEN NULL; END $$
            """
        )


@dataclass(frozen=True)
class SkillPackage:
    name: str
    description: str
    title: str | None
    skill_md: str
    files: dict[str, str]


def _parse_simple_frontmatter(raw: str) -> dict[str, str]:
    """Parse the small YAML subset required by Claude Skill frontmatter."""
    match = _FRONTMATTER_RE.match(raw)
    if not match:
        raise HTTPException(status_code=400, detail="SKILL.md 缺少 YAML frontmatter")
    out: dict[str, str] = {}
    for line in match.group(1).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            out[key] = value
    return out


def _safe_package_path(path: str) -> str | None:
    p = PurePosixPath(path.replace("\\", "/"))
    parts = [part for part in p.parts if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        return None
    if "__MACOSX" in parts:
        return None
    return str(PurePosixPath(*parts))


def _decode_text(data: bytes, *, filename: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"{filename} 必须是 UTF-8 文本") from exc


def validate_skill_package(files: dict[str, str]) -> SkillPackage:
    normalized: dict[str, str] = {}
    for raw_path, content in files.items():
        safe = _safe_package_path(raw_path)
        if safe is None:
            continue
        if PurePosixPath(safe).name.startswith("."):
            continue
        normalized[safe] = content
    if "SKILL.md" not in normalized:
        candidates = [p for p in normalized if p.endswith("/SKILL.md")]
        if len(candidates) == 1:
            prefix = candidates[0][: -len("SKILL.md")]
            normalized = {
                path[len(prefix) :] if path.startswith(prefix) else path: content
                for path, content in normalized.items()
            }
    if "SKILL.md" not in normalized:
        raise HTTPException(status_code=400, detail="Claude Skill 包必须包含 SKILL.md")
    if len(normalized) > _MAX_SKILL_FILES:
        raise HTTPException(status_code=400, detail="Skill 包文件过多")

    skill_md = normalized["SKILL.md"]
    if len(skill_md.encode("utf-8")) > _MAX_SKILL_MD_BYTES:
        raise HTTPException(status_code=400, detail="SKILL.md 超过大小限制")
    meta = _parse_simple_frontmatter(skill_md)
    name = (meta.get("name") or "").strip()
    description = (meta.get("description") or "").strip()
    if not _NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="Skill name 必须为 1-64 位小写字母/数字/连字符")
    if not description or len(description) > 1024:
        raise HTTPException(status_code=400, detail="Skill description 必须非空且不超过 1024 字符")
    title = None
    for line in skill_md.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()[:200] or None
            break
    return SkillPackage(name=name, description=description, title=title, skill_md=skill_md, files=normalized)


def package_from_markdown(skill_md: str) -> SkillPackage:
    return validate_skill_package({"SKILL.md": skill_md})


def package_from_zip(data: bytes) -> SkillPackage:
    if len(data) > _MAX_SKILL_PACKAGE_BYTES:
        raise HTTPException(status_code=413, detail="Skill 包超过大小限制")
    files: dict[str, str] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                if info.file_size > _MAX_SKILL_MD_BYTES:
                    raise HTTPException(status_code=400, detail=f"{info.filename} 超过大小限制")
                safe = _safe_package_path(info.filename)
                if safe is None:
                    continue
                suffix = PurePosixPath(safe).suffix.lower()
                if suffix not in ("", ".md", ".txt", ".json", ".yaml", ".yml", ".py", ".sh", ".js", ".ts"):
                    continue
                files[safe] = _decode_text(zf.read(info), filename=safe)
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="无法读取 zip Skill 包") from exc
    return validate_skill_package(files)


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw).strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(raw[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("LLM skill payload must be a JSON object")
    return parsed


def _fallback_generated_package(raw_text: str) -> SkillPackage:
    base = re.sub(r"[^a-z0-9]+", "-", raw_text.lower())[:40].strip("-") or "somna-workflow"
    name = base if _NAME_RE.match(base) else f"skill-{uuid.uuid4().hex[:8]}"
    description = (
        "Reusable workflow distilled from a Somna task. Use when the user asks for a similar task, "
        f"workflow, format, or operating procedure: {raw_text[:180] or 'repeat a previous Somna task pattern'}"
    )[:1024]
    skill_md = f"""---
name: {name}
description: {description}
---

# {name}

## Overview

Use this skill when the user asks for work similar to:

> {raw_text[:500] or "a completed Somna task"}

## Workflow

1. Restate the goal, deliverables, and constraints before starting.
2. Break the task into small verifiable steps.
3. Use tools to create real files, code, or structured outputs when the task requires artifacts.
4. Verify the result before finalizing.
5. Summarize what was completed, what was verified, and what still needs user confirmation.

## Additional Resources

- For the reusable workflow outline, see [workflow.md](references/workflow.md).
- For recommended final response patterns, see [output-patterns.md](references/output-patterns.md).
"""
    files = {
        "SKILL.md": skill_md,
        "LICENSE.txt": "Generated by Somna for the current user. Review before sharing.\n",
        "references/workflow.md": f"# Workflow\n\nSource task:\n\n> {raw_text[:1000] or 'N/A'}\n\nUse the checklist in SKILL.md and adapt it to the user's current constraints.\n",
        "references/output-patterns.md": "# Output Patterns\n\n- Keep the final response concise.\n- Mention produced artifacts and verification steps.\n- Ask for confirmation only when a real decision remains.\n",
    }
    return validate_skill_package(files)


async def _llm_generated_package(raw_text: str, assistant_text: str) -> SkillPackage:
    settings = get_settings()
    client = get_async_openai()
    prompt = f"""
You create Claude-compatible Skill packages for Somna.

Return ONLY valid JSON in this exact shape:
{{
  "files": {{
    "SKILL.md": "...",
    "LICENSE.txt": "...",
    "references/workflow.md": "...",
    "references/output-patterns.md": "..."
  }}
}}

Requirements:
- SKILL.md must begin with YAML frontmatter containing:
  name: lower-case English slug, max 64 chars, letters/numbers/hyphens only
  description: third-person, specific, <=1024 chars, includes when to use it
- Give the skill a meaningful English name based on the task, not a random id or email.
- SKILL.md body should be concise and under 500 lines.
- Use progressive disclosure: put detailed workflow in references/workflow.md and final answer templates in references/output-patterns.md.
- Do not include executable scripts unless the source task clearly requires a stable script.
- LICENSE.txt should state this is user-generated Somna skill content and should be reviewed before sharing.
- Content may be Chinese when useful, but name must be English slug.

Source user task:
{raw_text[:4000]}

Assistant result summary:
{assistant_text[:3000]}
""".strip()
    resp = await client.chat.completions.create(
        model=settings.agent_default_coder,
        messages=[
            {
                "role": "system",
                "content": "You author production-quality Claude Skill packages. Return strict JSON only.",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    text = resp.choices[0].message.content or ""
    payload = _extract_json_object(text)
    files = payload.get("files")
    if not isinstance(files, dict):
        raise ValueError("LLM skill payload missing files")
    normalized = {str(k): str(v) for k, v in files.items() if v is not None}
    return validate_skill_package(normalized)


def _visibility_for_user(user: CurrentUser, requested: str | None) -> str:
    if user.role == "admin":
        return "official"
    v = requested or "private"
    return v if v in ("private", "shared") else "private"


def _source_for_user(user: CurrentUser, requested: str) -> str:
    if user.role == "admin":
        return "official"
    return requested if requested in ("upload", "manual", "generated") else "manual"


def _row_to_dict(row: Any, *, installed: bool | None = None, enabled: bool | None = None) -> dict[str, Any]:
    keys = set(row.keys()) if hasattr(row, "keys") else set()
    out = {
        "id": str(row["id"]),
        "owner_user_id": str(row["owner_user_id"]) if row["owner_user_id"] else None,
        "owner_email": row["owner_email"] if "owner_email" in keys else None,
        "name": row["name"],
        "title": row["title"],
        "description": row["description"],
        "visibility": row["visibility"],
        "source": row["source"],
        "status": row["status"],
        "version": row["version"],
        "metadata": row["metadata"] or {},
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
    }
    if installed is not None:
        out["installed"] = installed
    if enabled is not None:
        out["enabled"] = enabled
    return out


async def create_skill(
    *,
    user: CurrentUser,
    package: SkillPackage,
    visibility: str | None,
    source: str,
    status: str = "active",
) -> dict[str, Any]:
    pool = get_pool()
    vis = _visibility_for_user(user, visibility)
    src = _source_for_user(user, source)
    async with pool.acquire() as conn:
        next_version = await conn.fetchval(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM skills WHERE owner_user_id = $1 AND name = $2",
            user.id,
            package.name,
        )
        row = await conn.fetchrow(
            """
            INSERT INTO skills (owner_user_id, name, title, description, visibility, source, status, version, skill_md, files)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb)
            RETURNING id, owner_user_id, name, title, description, visibility, source, status, version, metadata, created_at, updated_at
            """,
            user.id,
            package.name,
            package.title,
            package.description,
            vis,
            src,
            status if status in ("draft", "active") else "active",
            int(next_version or 1),
            package.skill_md,
            json.dumps(package.files, ensure_ascii=False),
        )
        await conn.execute(
            """
            INSERT INTO user_skills (user_id, skill_id, enabled)
            VALUES ($1, $2, true)
            ON CONFLICT (user_id, skill_id) DO UPDATE SET enabled = true, updated_at = now()
            """,
            user.id,
            row["id"],
        )
    log.info("skill.created", skill_id=str(row["id"]), user_id=str(user.id), visibility=vis, source=src)
    return _row_to_dict(row, installed=True, enabled=True)


async def list_my_skills(user: CurrentUser) -> list[dict[str, Any]]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT s.id, s.owner_user_id, u.email AS owner_email, s.name, s.title, s.description,
                   s.visibility, s.source, s.status, s.version, s.metadata, s.created_at, s.updated_at,
                   COALESCE(us.enabled, false) AS enabled
            FROM skills s
            LEFT JOIN users u ON u.id = s.owner_user_id
            LEFT JOIN user_skills us ON us.skill_id = s.id AND us.user_id = $1
            WHERE s.owner_user_id = $1 OR us.user_id = $1
            ORDER BY s.updated_at DESC
            """,
            user.id,
        )
    return [_row_to_dict(row, installed=True, enabled=bool(row["enabled"])) for row in rows]


async def list_market_skills(user: CurrentUser) -> list[dict[str, Any]]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT s.id, s.owner_user_id, u.email AS owner_email, s.name, s.title, s.description,
                   s.visibility, s.source, s.status, s.version, s.metadata, s.created_at, s.updated_at,
                   (us.user_id IS NOT NULL) AS installed,
                   COALESCE(us.enabled, false) AS enabled
            FROM skills s
            LEFT JOIN users u ON u.id = s.owner_user_id
            LEFT JOIN user_skills us ON us.skill_id = s.id AND us.user_id = $1
            WHERE s.status = 'active'
              AND (s.visibility IN ('shared','official') OR s.owner_user_id = $1)
            ORDER BY
              CASE WHEN s.visibility = 'official' THEN 0 WHEN s.owner_user_id = $1 THEN 1 ELSE 2 END,
              s.updated_at DESC
            """,
            user.id,
        )
    return [
        _row_to_dict(row, installed=bool(row["installed"]), enabled=bool(row["enabled"]))
        for row in rows
    ]


async def get_skill_detail(skill_id: uuid.UUID, user: CurrentUser) -> dict[str, Any]:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT s.*, u.email AS owner_email,
                   (us.user_id IS NOT NULL) AS installed,
                   COALESCE(us.enabled, false) AS enabled
            FROM skills s
            LEFT JOIN users u ON u.id = s.owner_user_id
            LEFT JOIN user_skills us ON us.skill_id = s.id AND us.user_id = $2
            WHERE s.id = $1
              AND (s.owner_user_id = $2 OR s.visibility IN ('shared','official') OR us.user_id = $2)
            """,
            skill_id,
            user.id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="skill not found")
    out = _row_to_dict(row, installed=bool(row["installed"]), enabled=bool(row["enabled"]))
    out["skill_md"] = row["skill_md"]
    out["files"] = row["files"] or {}
    return out


async def set_skill_enabled(skill_id: uuid.UUID, user: CurrentUser, enabled: bool) -> dict[str, Any]:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id FROM skills
            WHERE id = $1 AND status = 'active'
              AND (owner_user_id = $2 OR visibility IN ('shared','official'))
            """,
            skill_id,
            user.id,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="skill not found")
        await conn.execute(
            """
            INSERT INTO user_skills (user_id, skill_id, enabled)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id, skill_id) DO UPDATE SET enabled = $3, updated_at = now()
            """,
            user.id,
            skill_id,
            enabled,
        )
    return await get_skill_detail(skill_id, user)


async def update_skill_visibility(skill_id: uuid.UUID, user: CurrentUser, visibility: str) -> dict[str, Any]:
    vis = _visibility_for_user(user, visibility)
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE skills
            SET visibility = $3, source = CASE WHEN $4 THEN 'official' ELSE source END
            WHERE id = $1 AND owner_user_id = $2
            RETURNING id
            """,
            skill_id,
            user.id,
            vis,
            user.role == "admin",
        )
    if row is None:
        raise HTTPException(status_code=404, detail="skill not found")
    return await get_skill_detail(skill_id, user)


async def delete_skill(skill_id: uuid.UUID, user: CurrentUser) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT owner_user_id FROM skills WHERE id = $1", skill_id)
        if row is None:
            raise HTTPException(status_code=404, detail="skill not found")
        if row["owner_user_id"] != user.id and user.role != "admin":
            raise HTTPException(status_code=403, detail="not skill owner")
        await conn.execute("DELETE FROM skills WHERE id = $1", skill_id)


def _score_skill(query: str, row: Any) -> int:
    text = f"{row['name']} {row['title'] or ''} {row['description']}".lower()
    score = 0
    for token in {t for t in re.split(r"[\s,，。；;:：/\\]+", query.lower()) if len(t) >= 2}:
        if token in text:
            score += 2
    if row["visibility"] == "official":
        score += 1
    return score


async def format_enabled_skills_for_prompt(
    *,
    user_id: str | None,
    query: str,
    limit: int = _MAX_PROMPT_SKILLS,
) -> str | None:
    if not user_id:
        return None
    try:
        uid = uuid.UUID(str(user_id))
    except ValueError:
        return None
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT s.name, s.title, s.description, s.visibility, s.skill_md, s.files
            FROM user_skills us
            JOIN skills s ON s.id = us.skill_id
            WHERE us.user_id = $1 AND us.enabled = true AND s.status = 'active'
            ORDER BY s.updated_at DESC
            LIMIT 40
            """,
            uid,
        )
    ranked = sorted(rows, key=lambda row: _score_skill(query, row), reverse=True)
    selected = [row for row in ranked if _score_skill(query, row) > 0][:limit] or ranked[: min(2, len(ranked))]
    blocks: list[str] = []
    total = 0
    for row in selected:
        body = str(row["skill_md"]).strip()
        if not body:
            continue
        remaining = _MAX_SKILL_PROMPT_CHARS - total
        if remaining <= 0:
            break
        if len(body) > remaining:
            body = body[:remaining] + "\n...（Skill 内容已截断）"
        blocks.append(
            f"### Skill: {row['name']} ({row['visibility']})\n"
            f"描述：{row['description']}\n\n"
            f"{body}"
        )
        total += len(body)
    if not blocks:
        return None
    return (
        "以下是用户启用的 Claude 标准 Skill。遇到匹配任务时，应遵循相应 SKILL.md 的说明；"
        "不要执行 Skill 包中的脚本，除非用户明确授权并通过沙盒工具执行。\n\n"
        + "\n\n---\n\n".join(blocks)
    )


async def generate_skill_from_session(session_id: uuid.UUID, user: CurrentUser) -> SkillPackage:
    pool = get_pool()
    async with pool.acquire() as conn:
        owner = await conn.fetchval("SELECT user_id FROM sessions WHERE id = $1", session_id)
        if owner != user.id:
            raise HTTPException(status_code=404, detail="session not found")
        user_msg = await conn.fetchrow(
            """
            SELECT content FROM messages
            WHERE session_id = $1 AND role = 'user'
            ORDER BY created_at DESC LIMIT 1
            """,
            session_id,
        )
        assistant_events = await conn.fetch(
            """
            SELECT payload
            FROM events
            WHERE session_id = $1 AND type = 'message.delta'
            ORDER BY id DESC
            LIMIT 200
            """,
            session_id,
        )
    raw_text = ""
    if user_msg:
        content = user_msg["content"]
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except json.JSONDecodeError:
                content = {}
        if isinstance(content, dict):
            raw_text = str(content.get("text") or "")
    deltas: list[str] = []
    for row in reversed(assistant_events):
        payload = row["payload"]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = {}
        if isinstance(payload, dict) and isinstance(payload.get("text"), str):
            deltas.append(payload["text"])
    assistant_text = "".join(deltas).strip()
    try:
        return await _llm_generated_package(raw_text, assistant_text)
    except Exception as exc:  # noqa: BLE001
        log.warning("skill.generate.llm_failed", session_id=str(session_id), error=str(exc))
        return _fallback_generated_package(raw_text)
