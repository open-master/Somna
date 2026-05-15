"""LLM-driven skill selection for executor prompt context."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
from typing import Any

from app.config import get_settings
from app.llm.client import get_async_openai
from app.logging_setup import get_logger
from app.services.skills import list_enabled_skill_candidates

log = get_logger(__name__)

_MAX_ROUTER_RETRIES = 5
_MAX_SKILL_CONTEXT_CHARS = 30_000
_MAX_FILE_CHARS = 10_000


@dataclass(frozen=True)
class SelectedSkill:
    id: str
    name: str
    reason: str = ""
    load_files: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SkillRouteResult:
    selected: list[SelectedSkill]
    prompt_block: str | None


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.removeprefix("```json").removeprefix("```").strip()
        if raw.endswith("```"):
            raw = raw[:-3].strip()
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("skill router response must be a JSON object")
    return parsed


def _compact_json(value: Any, *, limit: int) -> str:
    raw = json.dumps(value, ensure_ascii=False, default=str)
    if len(raw) <= limit:
        return raw
    return raw[:limit] + "...(truncated)"


def _candidate_manifest(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in candidates:
        files = item.get("files") if isinstance(item.get("files"), dict) else {}
        out.append(
            {
                "id": item["id"],
                "name": item["name"],
                "title": item.get("title"),
                "description": item.get("description"),
                "visibility": item.get("visibility"),
                "version": item.get("version"),
                "files": sorted(str(path) for path in files.keys()),
            }
        )
    return out


def _normalize_load_files(raw: Any, files: dict[str, str]) -> list[str]:
    selected: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            path = str(item or "").strip()
            if path and path in files and path not in selected:
                selected.append(path)
    if "SKILL.md" in files and "SKILL.md" not in selected:
        selected.insert(0, "SKILL.md")
    return selected[:12]


def _skill_routing_hints(
    *,
    candidates: list[dict[str, Any]],
    user_message: str,
    task_frame: dict[str, Any] | None,
) -> list[dict[str, str]]:
    """Surface LLM task-framing conclusions as explicit hints for the router.

    The router still makes the final LLM decision. This prevents a strong
    task-frame signal from being buried inside a large JSON blob.
    """
    frame_text = json.dumps(task_frame or {}, ensure_ascii=False, default=str)
    haystack = f"{user_message}\n{frame_text}".lower()
    hints: list[dict[str, str]] = []
    for item in candidates:
        name = str(item.get("name") or "").strip()
        if not name or name.lower() not in haystack:
            continue
        hints.append(
            {
                "id": str(item["id"]),
                "name": name,
                "description": str(item.get("description") or "")[:500],
                "reason": "The current user request or Task Frame explicitly names this enabled skill.",
            }
        )
    return hints[:6]


def _render_skill_block(
    candidates_by_id: dict[str, dict[str, Any]],
    selected: list[SelectedSkill],
) -> str | None:
    blocks: list[str] = []
    total = 0
    for item in selected:
        candidate = candidates_by_id.get(item.id)
        if not candidate:
            continue
        files = candidate.get("files") if isinstance(candidate.get("files"), dict) else {}
        file_blocks: list[str] = []
        for path in item.load_files:
            content = files.get(path)
            if not isinstance(content, str):
                continue
            body = content.strip()
            if len(body) > _MAX_FILE_CHARS:
                body = body[:_MAX_FILE_CHARS] + "\n...（Skill 文件内容已截断）"
            next_block = f"#### {path}\n\n```text\n{body}\n```"
            if total + len(next_block) > _MAX_SKILL_CONTEXT_CHARS:
                break
            file_blocks.append(next_block)
            total += len(next_block)
        if not file_blocks:
            continue
        reason = f"\n选择原因：{item.reason.strip()}" if item.reason.strip() else ""
        blocks.append(
            f"### Skill: {candidate['name']} ({candidate.get('visibility')}){reason}\n\n"
            + "\n\n".join(file_blocks)
        )
        if total >= _MAX_SKILL_CONTEXT_CHARS:
            break
    if not blocks:
        return None
    return (
        "以下 Skill 由 `agent-skill` 根据当前任务语义选择。只使用这些已加载的 Skill；"
        "若其中包含脚本，必须先阅读脚本内容并在合适的沙盒工具中显式运行。\n\n"
        + "\n\n---\n\n".join(blocks)
    )


async def route_skills_for_task(
    *,
    user_id: str | None,
    user_message: str,
    task_frame: dict[str, Any] | None,
    plan: dict[str, Any] | None,
    skill_mode: str | None,
    skill_model: str | None = None,
) -> SkillRouteResult:
    if (skill_mode or "auto").strip().lower() == "off":
        return SkillRouteResult(selected=[], prompt_block=None)
    if not user_id:
        log.info("skill.router.skipped", reason="missing_user_id")
        return SkillRouteResult(selected=[], prompt_block=None)

    candidates = await list_enabled_skill_candidates(user_id=user_id)
    if not candidates:
        log.info("skill.router.skipped", reason="no_enabled_candidates", user_id=user_id)
        return SkillRouteResult(selected=[], prompt_block=None)

    candidates_by_id = {item["id"]: item for item in candidates}
    manifest = _candidate_manifest(candidates)
    routing_hints = _skill_routing_hints(
        candidates=candidates,
        user_message=user_message,
        task_frame=task_frame,
    )
    model = (skill_model or get_settings().agent_default_skill).strip()
    prompt = f"""
You are Somna's Skill Router. Decide whether the executor should use any enabled Claude Skills for the current task.

Rules:
- Use semantic fit only. Do not select a skill just because a keyword overlaps.
- If Task Framing already identifies an enabled skill as matching the task, treat that as a strong prior and select it unless it is clearly inconsistent with the actual user request.
- When the user asks to create an artifact, transform content, run a workflow, or perform a deliverable that directly matches a skill description, select that skill.
- Return an empty selected array only when no enabled skill would materially improve the task.
- Select `skill-creator` only for tasks about creating, editing, validating, packaging, or explaining Claude/Somna Skills.
- Prefer one highly relevant skill. Use multiple skills only when their scopes are clearly complementary.
- Choose which files to load for each selected skill. Always include SKILL.md plus any references/scripts that are genuinely needed.

Return ONLY valid JSON:
{{
  "selected": [
    {{
      "id": "skill id from the manifest",
      "reason": "brief reason",
      "load_files": ["SKILL.md", "references/workflow.md"]
    }}
  ]
}}

Enabled skill manifest:
{_compact_json(manifest, limit=20_000)}

Strong routing hints from Task Framing:
{_compact_json(routing_hints, limit=4000)}

Current user request:
{user_message[:4000]}

Task frame:
{_compact_json(task_frame or {}, limit=4000)}

Plan:
{_compact_json(plan or {}, limit=4000)}
""".strip()

    client = get_async_openai()
    last_error: Exception | None = None
    payload: dict[str, Any] | None = None
    for attempt in range(1, _MAX_ROUTER_RETRIES + 1):
        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": "You route enabled Claude Skills for an agent executor. Return strict JSON only.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            candidate_payload = _extract_json_object(resp.choices[0].message.content or "")
            if not isinstance(candidate_payload.get("selected"), list):
                raise ValueError("skill router response missing selected array")
            payload = candidate_payload
            break
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            log.warning(
                "skill.router.llm_failed",
                model=model,
                attempt=attempt,
                max_attempts=_MAX_ROUTER_RETRIES,
                error=str(exc),
            )
            if attempt < _MAX_ROUTER_RETRIES:
                await asyncio.sleep(min(0.5 * attempt, 2.0))

    if payload is None:
        log.error("skill.router.unavailable", model=model, error=str(last_error) if last_error else "")
        return SkillRouteResult(selected=[], prompt_block=None)

    selected_raw = payload.get("selected")
    selected: list[SelectedSkill] = []
    for raw in selected_raw[:6]:
        if not isinstance(raw, dict):
            continue
        sid = str(raw.get("id") or "").strip()
        candidate = candidates_by_id.get(sid)
        if not candidate:
            continue
        files = candidate.get("files") if isinstance(candidate.get("files"), dict) else {}
        selected.append(
            SelectedSkill(
                id=sid,
                name=str(candidate["name"]),
                reason=str(raw.get("reason") or "").strip()[:500],
                load_files=_normalize_load_files(raw.get("load_files"), files),
            )
        )

    log.info(
        "skill.router.selected",
        model=model,
        user_id=user_id,
        routing_hints=routing_hints,
        selected_skills=[{"id": item.id, "name": item.name, "files": item.load_files} for item in selected],
    )
    return SkillRouteResult(selected=selected, prompt_block=_render_skill_block(candidates_by_id, selected))
