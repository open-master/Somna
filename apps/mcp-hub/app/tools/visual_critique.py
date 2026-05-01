"""Visual / aesthetic critique — multimodal LLM (DashScope OpenAI-compatible).

Thin tool I/O: sandbox image path + optional natural-language context.
Judgment lives in the model (Less Structure, More Intelligence).
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import httpx

from app.config import get_settings
from app.sandbox.manager import PathEscapeError, get_sandbox_manager

from .base import BaseTool, ToolContext, ToolResult
from .registry import register

log = logging.getLogger(__name__)

_EXT_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}

_JSON_BLOCK = re.compile(r"\{[\s\S]*\}")


def _guess_mime(path: Path) -> str | None:
    return _EXT_MIME.get(path.suffix.lower())


@register
class VisualCritiqueTool(BaseTool):
    name = "visual_critique"
    description = (
        "对沙盒内的**图片/截图**做版式与视觉审美层面的评审（信息层次、留白、对比度、"
        "字体与对齐、整体统一感等）。传入相对路径与可选说明；返回结构化 JSON 结论与改进建议。"
        "适用于海报、网页截图、幻灯片导出图等。**不**用于替代功能测试或 OCR 验收。"
    )
    category = "media"
    mutates = False
    input_schema = {
        "type": "object",
        "required": ["path"],
        "properties": {
            "path": {
                "type": "string",
                "description": "沙盒内相对路径，指向 png/jpg/webp/gif/bmp 等位图文件。",
            },
            "context": {
                "type": "string",
                "description": "可选。用途与受众的自然语言说明（例如：产品介绍页头图、路演 PPT 第 3 页）。",
            },
            "model": {
                "type": "string",
                "description": "可选。覆盖默认多模态模型 id（默认 qwen3-vl-plus）。",
            },
        },
        "additionalProperties": False,
    }

    async def invoke(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        settings = get_settings()
        raw_path = str(args.get("path") or "").strip()
        context = str(args.get("context") or "").strip()
        model_id = str(args.get("model") or "").strip() or settings.visual_critique_model

        if not settings.dashscope_api_key:
            return ToolResult(
                ok=False,
                error="DASHSCOPE_API_KEY is not set; visual_critique requires DashScope.",
            )

        sm = get_sandbox_manager(settings)
        try:
            target = sm.safe_join(ctx.sandbox_id, raw_path)
        except PathEscapeError as e:
            return ToolResult(ok=False, error=str(e))

        path = Path(target)
        if not path.is_file():
            return ToolResult(ok=False, error=f"not a file: {raw_path!r}")

        mime = _guess_mime(path)
        if not mime:
            return ToolResult(
                ok=False,
                error=f"unsupported image type (suffix {path.suffix!r}); use png/jpg/webp/gif/bmp.",
            )

        size = path.stat().st_size
        if size > settings.visual_critique_max_image_bytes:
            return ToolResult(
                ok=False,
                error=f"image too large ({size} bytes); max {settings.visual_critique_max_image_bytes}.",
            )

        started = time.perf_counter()
        data = path.read_bytes()
        b64 = base64.standard_b64encode(data).decode("ascii")
        data_url = f"data:{mime};base64,{b64}"

        system = (
            "你是资深视觉与版式顾问，只做**审美与可读性**层面的评价，不评价业务事实对错。"
            "根据用户给出的图片与说明，输出**仅一个 JSON 对象**（不要 markdown 代码围栏），"
            "键名固定为："
            '`verdict`（字符串，取 pass | needs_work | uncertain）、'
            '`summary`（一到两句中文总评）、'
            '`strengths`（字符串数组，优点）、'
            '`issues`（对象数组，每项含 `area` 与 `detail` 字符串）、'
            '`suggestions`（字符串数组，可执行的改进方向）。'
            "主观判断用 uncertain；明显违和、拥挤、层次不清等用 needs_work。"
        )
        user_text = (
            (f"背景与用途：{context}\n\n" if context else "")
            + "请评审这张图片的视觉效果与版式质量，并只输出上述 JSON。"
        )

        body: dict[str, Any] = {
            "model": model_id,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text", "text": user_text},
                    ],
                },
            ],
            "response_format": {"type": "json_object"},
        }

        url = f"{settings.visual_critique_openai_base.rstrip('/')}/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=settings.visual_critique_timeout_sec) as client:
                r = await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {settings.dashscope_api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
        except httpx.RequestError as e:
            return ToolResult(ok=False, error=f"dashscope request failed: {e}")

        duration_ms = int((time.perf_counter() - started) * 1000)
        try:
            payload = r.json()
        except json.JSONDecodeError:
            return ToolResult(
                ok=False,
                error=f"dashscope non-json response HTTP {r.status_code}: {r.text[:300]}",
                duration_ms=duration_ms,
            )

        if r.status_code >= 400:
            err = payload.get("error")
            if isinstance(err, dict):
                err_msg = err.get("message") or str(err)
            else:
                err_msg = payload.get("message") or str(err) or r.text[:400]
            return ToolResult(
                ok=False,
                error=f"DashScope HTTP {r.status_code}: {err_msg}",
                duration_ms=duration_ms,
            )

        choices = payload.get("choices") or []
        if not choices:
            return ToolResult(
                ok=False,
                error=f"no choices in response: {json.dumps(payload, ensure_ascii=False)[:400]}",
                duration_ms=duration_ms,
            )

        raw_content = (choices[0].get("message") or {}).get("content") or ""
        if not isinstance(raw_content, str):
            raw_content = json.dumps(raw_content, ensure_ascii=False)

        parsed: dict[str, Any] | None = None
        try:
            parsed = json.loads(raw_content)
        except json.JSONDecodeError:
            m = _JSON_BLOCK.search(raw_content)
            if m:
                try:
                    parsed = json.loads(m.group(0))
                except json.JSONDecodeError:
                    parsed = None

        if not isinstance(parsed, dict):
            log.warning("visual_critique.parse_failed", model=model_id, snippet=raw_content[:200])
            return ToolResult(
                ok=True,
                preview=raw_content[:1500],
                output={
                    "model": model_id,
                    "path": raw_path,
                    "parse_error": True,
                    "raw": raw_content[:8000],
                },
                duration_ms=duration_ms,
            )

        verdict = str(parsed.get("verdict") or "").strip() or "uncertain"
        summary = str(parsed.get("summary") or "").strip()
        preview = f"[{verdict}] {summary}"[:2000] if summary else f"[{verdict}] (见 output)"

        return ToolResult(
            ok=True,
            preview=preview,
            output={
                "model": model_id,
                "path": raw_path,
                "critique": parsed,
            },
            duration_ms=duration_ms,
        )
