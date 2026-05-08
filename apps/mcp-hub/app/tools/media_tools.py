"""Generative media tools: DashScope 万相文生图/文生视频、MiniMax TTS.

All credentials and endpoints come from Settings / .env (see app/config.py).
"""

from __future__ import annotations

import asyncio
import binascii
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from app.config import get_settings

from .base import BaseTool, ToolContext, ToolResult
from .registry import register

log = logging.getLogger(__name__)

_SAFE_NAME = re.compile(r"[^a-zA-Z0-9._-]+")


def _safe_slug(s: str, max_len: int = 48) -> str:
    t = _SAFE_NAME.sub("_", (s or "").strip())[:max_len].strip("_")
    return t or "out"


async def _dashscope_post(
    client: httpx.AsyncClient,
    base: str,
    api_key: str,
    path: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    url = f"{base.rstrip('/')}/{path.lstrip('/')}"
    r = await client.post(
        url,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "X-DashScope-Async": "enable",
        },
        json=body,
    )
    data = r.json() if r.content else {}
    if r.status_code >= 400:
        msg = data.get("message") or data.get("Message") or r.text[:500]
        raise RuntimeError(f"DashScope HTTP {r.status_code}: {msg}")
    if data.get("code"):
        raise RuntimeError(f"DashScope error {data.get('code')}: {data.get('message', data)}")
    return data


async def _dashscope_get_task(
    client: httpx.AsyncClient,
    base: str,
    api_key: str,
    task_id: str,
) -> dict[str, Any]:
    url = f"{base.rstrip('/')}/tasks/{task_id}"
    r = await client.get(
        url,
        headers={"Authorization": f"Bearer {api_key}"},
    )
    data = r.json() if r.content else {}
    if r.status_code >= 400:
        msg = data.get("message") or r.text[:500]
        raise RuntimeError(f"DashScope task HTTP {r.status_code}: {msg}")
    return data


async def _poll_dashscope_task(
    client: httpx.AsyncClient,
    *,
    base: str,
    api_key: str,
    task_id: str,
    poll_interval_sec: float,
    timeout_sec: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_sec
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = await _dashscope_get_task(client, base, api_key, task_id)
        out = last.get("output") or {}
        status = (out.get("task_status") or "").upper()
        if status == "SUCCEEDED":
            return last
        if status == "FAILED":
            msg = out.get("message") or out.get("code") or last.get("message") or "task failed"
            raise RuntimeError(f"DashScope task failed: {msg}")
        if status == "UNKNOWN":
            raise RuntimeError("DashScope task status UNKNOWN")
        await asyncio.sleep(poll_interval_sec)
    raise TimeoutError(f"DashScope task {task_id!r} timed out after {timeout_sec:.0f}s")


async def _download_bytes(client: httpx.AsyncClient, url: str, timeout_sec: float) -> bytes:
    r = await client.get(url, follow_redirects=True, timeout=timeout_sec)
    r.raise_for_status()
    return r.content


def _write_sandbox_file(workdir: str, rel_path: str, data: bytes) -> str:
    rel = rel_path.replace("\\", "/").lstrip("/")
    path = Path(workdir) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return rel


def _t2i_uses_wan26_style_api(model: str) -> bool:
    """万相 2.6+ 文生图：HTTP 异步 `image-generation/generation`，body 为 messages 形态。"""
    m = (model or "").strip().lower()
    return m.startswith("wan2.6-t2i") or m.startswith("wan2.7")


def _video_is_happyhorse(model: str) -> bool:
    return "happyhorse" in (model or "").lower()


def _video_is_wan27_family(model: str) -> bool:
    return (model or "").strip().lower().startswith("wan2.7")


def _model_kind_error(model: str, kind: str) -> str | None:
    """若 model 与工具类型不匹配则返回中文错误句。"""
    m = (model or "").strip().lower()
    if kind == "t2v":
        if "-i2v" in m:
            return "文生视频工具请选用 t2v 类 model（不要选图生 i2v）"
        if "-r2v" in m:
            return "文生视频工具请选用 t2v 类 model（不要选参考生 r2v）"
        if "videoedit" in m or "video-edit" in m:
            return "文生视频工具请选用 t2v 类 model（不要选视频编辑）"
        if "t2v" not in m:
            return "`model` 须为文生视频 id（如 wan2.2-t2v-plus、wan2.7-t2v-2026-04-25、happyhorse-1.0-t2v）"
        return None
    if kind == "i2v":
        if "-i2v" not in m:
            return "图生视频工具请选用含 -i2v 的 model（如 happyhorse-1.0-i2v、wan2.7-i2v-2026-04-25）"
        return None
    if kind == "r2v":
        if "-i2v" in m:
            return "参考生请使用 r2v 类 model，不要用 i2v"
        if "-r2v" not in m:
            return "参考生视频请选用含 -r2v 的 model（如 happyhorse-1.0-r2v、wan2.7-r2v）"
        return None
    if kind == "edit":
        if "video-edit" not in m and "videoedit" not in m:
            return "视频编辑请选用编辑类 model（happyhorse-1.0-video-edit 或 wan2.7-videoedit）"
        return None
    return None


def _clamp_duration(
    value: object,
    *,
    low: int,
    high: int,
    default: int,
) -> int:
    try:
        d = int(value) if value is not None else default
    except (TypeError, ValueError):
        d = default
    return max(low, min(high, d))


def _extract_image_urls_from_task_output(fout: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for item in fout.get("results") or []:
        if isinstance(item, dict) and item.get("url"):
            urls.append(str(item["url"]))
    return urls


@register
class WanText2ImageTool(BaseTool):
    name = "wan_text2image"
    description = (
        "Generate image(s) from a text prompt using Alibaba DashScope 万相 (text-to-image). "
        "Downloads result files into the sandbox under artifacts/. Requires DASHSCOPE_API_KEY."
    )
    category = "media"
    mutates = True
    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string", "description": "Positive prompt (Chinese or English)."},
            "negative_prompt": {"type": "string", "description": "Optional negative prompt."},
            "model": {
                "type": "string",
                "description": "Override model id (default from MCP_WAN_T2I_MODEL).",
            },
            "size": {
                "type": "string",
                "description": "e.g. 1024*1024, 1280*720 (see model docs).",
            },
            "n": {
                "type": "integer",
                "minimum": 1,
                "maximum": 4,
                "description": "Number of images (model-dependent).",
            },
        },
        "additionalProperties": False,
    }

    async def invoke(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        settings = get_settings()
        started = time.perf_counter()
        key = (settings.dashscope_api_key or "").strip()
        if not key:
            return ToolResult(
                ok=False,
                error="DASHSCOPE_API_KEY is not set. Add it in .env for 万相 / Qwen.",
            )
        prompt = (args.get("prompt") or "").strip()
        if not prompt:
            return ToolResult(ok=False, error="`prompt` is required")

        model = (args.get("model") or settings.wan_t2i_model).strip()
        params: dict[str, Any] = {}
        size = (args.get("size") or "").strip()
        if size:
            params["size"] = size
        if args.get("n") is not None:
            params["n"] = int(args["n"])

        neg = (args.get("negative_prompt") or "").strip()

        base = settings.dashscope_http_base
        timeout = httpx.Timeout(settings.wan_request_timeout_sec)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                if _t2i_uses_wan26_style_api(model):
                    size_eff = size or "1280*1280"
                    body = {
                        "model": model,
                        "input": {
                            "messages": [
                                {
                                    "role": "user",
                                    "content": [{"text": prompt}],
                                }
                            ]
                        },
                        "parameters": {
                            "prompt_extend": True,
                            "watermark": False,
                            "n": int(args["n"]) if args.get("n") is not None else 1,
                            "negative_prompt": neg,
                            "size": size_eff,
                        },
                    }
                    t2i_path = "/services/aigc/image-generation/generation"
                else:
                    body = {
                        "model": model,
                        "input": {"prompt": prompt},
                        "parameters": params,
                    }
                    if neg:
                        body["input"]["negative_prompt"] = neg
                    t2i_path = "/services/aigc/text2image/image-synthesis"

                created = await _dashscope_post(
                    client,
                    base,
                    key,
                    t2i_path,
                    body,
                )
                out0 = created.get("output") or {}
                task_id = out0.get("task_id")
                if not task_id:
                    return ToolResult(ok=False, error=f"no task_id from DashScope: {created!r}")

                final = await _poll_dashscope_task(
                    client,
                    base=base,
                    api_key=key,
                    task_id=task_id,
                    poll_interval_sec=settings.wan_poll_interval_sec,
                    timeout_sec=settings.wan_poll_timeout_sec,
                )
                fout = final.get("output") or {}
                urls = _extract_image_urls_from_task_output(fout)

                if not urls:
                    return ToolResult(
                        ok=False,
                        error=f"no image URLs in result: {fout!r}",
                        output={"task_id": task_id, "raw_output": fout},
                    )

                prefix = f"artifacts/wan_t2i_{_safe_slug(prompt)}_{uuid.uuid4().hex[:8]}"
                paths: list[str] = []
                for i, u in enumerate(urls):
                    ext = ".png"
                    if ".jpg" in u.lower() or "jpeg" in u.lower():
                        ext = ".jpg"
                    elif ".webp" in u.lower():
                        ext = ".webp"
                    raw = await _download_bytes(client, u, settings.wan_request_timeout_sec)
                    rel = _write_sandbox_file(ctx.workdir, f"{prefix}_{i}{ext}", raw)
                    paths.append(rel)

                ms = int((time.perf_counter() - started) * 1000)
                preview = f"{len(paths)} image(s) → " + ", ".join(paths[:3])
                if len(paths) > 3:
                    preview += " ..."
                return ToolResult(
                    ok=True,
                    preview=preview,
                    output={
                        "paths": paths,
                        "task_id": task_id,
                        "model": model,
                    },
                    duration_ms=ms,
                )
        except httpx.HTTPError as e:
            return ToolResult(ok=False, error=f"http error: {e}")
        except TimeoutError as e:
            return ToolResult(ok=False, error=str(e))
        except RuntimeError as e:
            return ToolResult(ok=False, error=str(e))
        except Exception as e:  # noqa: BLE001
            log.exception("wan_text2image.failed")
            return ToolResult(ok=False, error=f"{type(e).__name__}: {e}")


async def _wan_video_synthesis_invoke(
    ctx: ToolContext,
    args: dict[str, Any],
    *,
    kind: str,
    default_model: str,
    artifact_prefix: str,
    log_key: str,
) -> ToolResult:
    settings = get_settings()
    started = time.perf_counter()
    key = (settings.dashscope_api_key or "").strip()
    if not key:
        return ToolResult(
            ok=False,
            error="DASHSCOPE_API_KEY is not set. Add it in .env for 万相 / Qwen.",
        )
    prompt = (args.get("prompt") or "").strip()
    if not prompt:
        return ToolResult(ok=False, error="`prompt` is required")

    model = (args.get("model") or default_model).strip()
    kind_err = _model_kind_error(model, kind)
    if kind_err:
        return ToolResult(ok=False, error=kind_err)

    parameters: dict[str, Any] = {}
    inp: dict[str, Any] = {"prompt": prompt}

    raw_media = args.get("media")
    if kind in {"i2v", "r2v", "edit"}:
        if not isinstance(raw_media, list) or len(raw_media) == 0:
            return ToolResult(
                ok=False,
                error="请传入非空 `media`（公网 URL；结构见 DashScope：first_frame / reference_image / video 等）",
            )
        inp["media"] = raw_media
    elif raw_media is not None:
        if not isinstance(raw_media, list):
            return ToolResult(ok=False, error="`media` must be a JSON array")
        inp["media"] = raw_media

    audio_u = (args.get("audio_url") or "").strip()
    if audio_u:
        inp["audio_url"] = audio_u

    mlow = model.lower()
    neg = (args.get("negative_prompt") or "").strip()

    if _video_is_happyhorse(model):
        if "video-edit" in mlow:
            parameters["resolution"] = (args.get("resolution") or "1080P").strip() or "1080P"
            if args.get("watermark") is not None:
                parameters["watermark"] = bool(args["watermark"])
        elif "-i2v" in mlow:
            parameters["resolution"] = (args.get("resolution") or "1080P").strip() or "1080P"
            parameters["duration"] = _clamp_duration(args.get("duration"), low=3, high=15, default=5)
            if args.get("watermark") is not None:
                parameters["watermark"] = bool(args["watermark"])
        else:
            parameters["resolution"] = (args.get("resolution") or "720P").strip() or "720P"
            parameters["ratio"] = (args.get("ratio") or "16:9").strip() or "16:9"
            parameters["duration"] = _clamp_duration(args.get("duration"), low=3, high=15, default=5)
            if args.get("watermark") is not None:
                parameters["watermark"] = bool(args["watermark"])
    elif _video_is_wan27_family(model):
        if "videoedit" in mlow:
            parameters["resolution"] = (args.get("resolution") or "720P").strip() or "720P"
            if args.get("prompt_extend") is not None:
                parameters["prompt_extend"] = bool(args["prompt_extend"])
            else:
                parameters["prompt_extend"] = True
            if args.get("watermark") is not None:
                parameters["watermark"] = bool(args["watermark"])
            else:
                parameters["watermark"] = True
        else:
            parameters["resolution"] = (args.get("resolution") or "720P").strip() or "720P"
            parameters["ratio"] = (args.get("ratio") or "16:9").strip() or "16:9"
            parameters["duration"] = _clamp_duration(args.get("duration"), low=2, high=15, default=5)
            if args.get("prompt_extend") is not None:
                parameters["prompt_extend"] = bool(args["prompt_extend"])
            else:
                parameters["prompt_extend"] = True
            if args.get("watermark") is not None:
                parameters["watermark"] = bool(args["watermark"])
            if neg:
                inp["negative_prompt"] = neg
    else:
        size = (args.get("size") or "").strip()
        if size:
            parameters["size"] = size
        if args.get("prompt_extend") is not None:
            parameters["prompt_extend"] = bool(args["prompt_extend"])
        if neg:
            inp["negative_prompt"] = neg

    body = {"model": model, "input": inp, "parameters": parameters}
    base = settings.dashscope_http_base
    timeout = httpx.Timeout(settings.wan_request_timeout_sec)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            created = await _dashscope_post(
                client,
                base,
                key,
                "/services/aigc/video-generation/video-synthesis",
                body,
            )
            out0 = created.get("output") or {}
            task_id = out0.get("task_id")
            if not task_id:
                return ToolResult(ok=False, error=f"no task_id from DashScope: {created!r}")

            final = await _poll_dashscope_task(
                client,
                base=base,
                api_key=key,
                task_id=task_id,
                poll_interval_sec=settings.wan_poll_interval_sec,
                timeout_sec=settings.wan_poll_timeout_sec,
            )
            fout = final.get("output") or {}
            video_url = fout.get("video_url")
            if not video_url:
                return ToolResult(
                    ok=False,
                    error=f"no video_url in result: {fout!r}",
                    output={"task_id": task_id, "raw_output": fout},
                )

            raw = await _download_bytes(client, video_url, settings.wan_request_timeout_sec)
            rel = _write_sandbox_file(
                ctx.workdir,
                f"artifacts/{artifact_prefix}_{_safe_slug(prompt)}_{uuid.uuid4().hex[:8]}.mp4",
                raw,
            )
            ms = int((time.perf_counter() - started) * 1000)
            return ToolResult(
                ok=True,
                preview=f"video → {rel}",
                output={"path": rel, "task_id": task_id, "model": model},
                duration_ms=ms,
            )
    except httpx.HTTPError as e:
        return ToolResult(ok=False, error=f"http error: {e}")
    except TimeoutError as e:
        return ToolResult(ok=False, error=str(e))
    except RuntimeError as e:
        return ToolResult(ok=False, error=str(e))
    except Exception as e:  # noqa: BLE001
        log.exception("%s.failed", log_key)
        return ToolResult(ok=False, error=f"{type(e).__name__}: {e}")


@register
class WanT2vTool(BaseTool):
    name = "wan_t2v"
    description = (
        "阿里云 DashScope 文生视频（text-to-video）：仅用文本描述生成短视频。"
        "选用 t2v 类 model（如 wan2.2-t2v-plus、wan2.7-t2v-2026-04-25、happyhorse-1.0-t2v）；"
        "可选 audio_url（万相 2.7）。Requires DASHSCOPE_API_KEY。"
    )
    category = "media"
    mutates = True
    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string", "description": "视频内容描述。"},
            "negative_prompt": {"type": "string", "description": "反向提示（万相 2.x 文生视频）。"},
            "model": {"type": "string", "description": "覆盖默认 model（默认 MCP_WAN_T2V_MODEL）。"},
            "size": {"type": "string", "description": "万相 2.2/2.6：如 832*480。"},
            "prompt_extend": {"type": "boolean", "description": "扩写提示词。"},
            "resolution": {"type": "string", "description": "720P / 1080P 等。"},
            "ratio": {"type": "string", "description": "如 16:9。"},
            "duration": {"type": "integer", "description": "时长（秒）。"},
            "audio_url": {"type": "string", "description": "自定义音频 URL（万相 2.7）。"},
            "watermark": {"type": "boolean"},
        },
        "additionalProperties": False,
    }

    async def invoke(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        s = get_settings()
        return await _wan_video_synthesis_invoke(
            ctx,
            args,
            kind="t2v",
            default_model=s.wan_t2v_model,
            artifact_prefix="wan_t2v",
            log_key="wan_t2v",
        )


@register
class WanI2vTool(BaseTool):
    name = "wan_i2v"
    description = (
        "图生视频（image-to-video）：基于首帧等图像 + 文本生成视频。"
        "须使用含 -i2v 的 model；`media` 必填。"
        " Requires DASHSCOPE_API_KEY。"
    )
    category = "media"
    mutates = True
    input_schema = {
        "type": "object",
        "required": ["prompt", "media"],
        "properties": {
            "prompt": {"type": "string", "description": "对动态内容的描述。"},
            "media": {
                "type": "array",
                "description": "首帧等素材，如 [{\"type\":\"first_frame\",\"url\":\"https://...\"}]",
                "items": {"type": "object"},
            },
            "model": {"type": "string", "description": "默认 MCP_WAN_I2V_MODEL。"},
            "resolution": {"type": "string"},
            "duration": {"type": "integer"},
            "watermark": {"type": "boolean"},
        },
        "additionalProperties": False,
    }

    async def invoke(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        s = get_settings()
        return await _wan_video_synthesis_invoke(
            ctx,
            args,
            kind="i2v",
            default_model=s.wan_i2v_model,
            artifact_prefix="wan_i2v",
            log_key="wan_i2v",
        )


@register
class WanR2vTool(BaseTool):
    name = "wan_r2v"
    description = (
        "参考生视频（reference-to-video）：多参考图 + 文本，保持角色/风格一致性。"
        "须使用 r2v 类 model；`media` 必填。"
        " Requires DASHSCOPE_API_KEY。"
    )
    category = "media"
    mutates = True
    input_schema = {
        "type": "object",
        "required": ["prompt", "media"],
        "properties": {
            "prompt": {"type": "string", "description": "融合参考素材的叙述（可用 [Image 1] 指代）。"},
            "media": {
                "type": "array",
                "description": "多张 reference_image 等，见官方文档。",
                "items": {"type": "object"},
            },
            "model": {"type": "string", "description": "默认 MCP_WAN_R2V_MODEL。"},
            "resolution": {"type": "string"},
            "ratio": {"type": "string"},
            "duration": {"type": "integer"},
            "watermark": {"type": "boolean"},
        },
        "additionalProperties": False,
    }

    async def invoke(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        s = get_settings()
        return await _wan_video_synthesis_invoke(
            ctx,
            args,
            kind="r2v",
            default_model=s.wan_r2v_model,
            artifact_prefix="wan_r2v",
            log_key="wan_r2v",
        )


@register
class WanVideoEditTool(BaseTool):
    name = "wan_video_edit"
    description = (
        "视频编辑：待编辑视频（+ 可选参考图）+ 指令，完成风格迁移、替换等。"
        "须使用 video-edit / videoedit 类 model；`media` 须含 type=video。"
        " Requires DASHSCOPE_API_KEY。"
    )
    category = "media"
    mutates = True
    input_schema = {
        "type": "object",
        "required": ["prompt", "media"],
        "properties": {
            "prompt": {"type": "string", "description": "编辑指令。"},
            "media": {
                "type": "array",
                "description": "含一条 video，及可选 reference_image。",
                "items": {"type": "object"},
            },
            "model": {"type": "string", "description": "默认 MCP_WAN_VIDEO_EDIT_MODEL。"},
            "resolution": {"type": "string"},
            "prompt_extend": {"type": "boolean"},
            "watermark": {"type": "boolean"},
        },
        "additionalProperties": False,
    }

    async def invoke(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        s = get_settings()
        return await _wan_video_synthesis_invoke(
            ctx,
            args,
            kind="edit",
            default_model=s.wan_video_edit_model,
            artifact_prefix="wan_video_edit",
            log_key="wan_video_edit",
        )


@register
class MinimaxTtsTool(BaseTool):
    name = "minimax_tts"
    description = (
        "Text-to-speech via MiniMax HTTP API (t2a_v2). Saves an MP3 under artifacts/. "
        "Set MINIMAX_API_KEY and MCP_MINIMAX_BASE_URL (e.g. https://api.minimaxi.com for China)."
    )
    category = "media"
    mutates = True
    input_schema = {
        "type": "object",
        "required": ["text"],
        "properties": {
            "text": {"type": "string", "description": "Text to synthesize."},
            "model": {"type": "string", "description": "Override TTS model (default MCP_MINIMAX_TTS_MODEL)."},
            "voice_id": {"type": "string", "description": "Override voice (default MCP_MINIMAX_TTS_VOICE_ID)."},
            "speed": {"type": "number", "description": "Speech speed ~0.5–2.0."},
            "language_boost": {"type": "string", "description": "e.g. auto, zh (MiniMax doc)."},
        },
        "additionalProperties": False,
    }

    async def invoke(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        settings = get_settings()
        started = time.perf_counter()
        key = (settings.minimax_api_key or "").strip()
        if not key:
            return ToolResult(
                ok=False,
                error="MINIMAX_API_KEY is not set. Add it in .env.",
            )
        text = (args.get("text") or "").strip()
        if not text:
            return ToolResult(ok=False, error="`text` is required")

        model = (args.get("model") or settings.minimax_tts_model).strip()
        voice_id = (args.get("voice_id") or settings.minimax_tts_voice_id).strip()
        speed = args.get("speed")
        if speed is None:
            speed = 1.0
        lang = (args.get("language_boost") or "auto").strip() or "auto"

        body: dict[str, Any] = {
            "model": model,
            "text": text,
            "stream": False,
            "language_boost": lang,
            "output_format": "hex",
            "voice_setting": {
                "voice_id": voice_id,
                "speed": float(speed),
                "vol": 1.0,
                "pitch": 0,
            },
            "audio_setting": {
                "sample_rate": 32000,
                "bitrate": 128000,
                "format": "mp3",
                "channel": 1,
            },
        }

        url = f"{settings.minimax_http_base.rstrip('/')}/v1/t2a_v2"
        timeout = httpx.Timeout(settings.minimax_request_timeout_sec)

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(
                    url,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {key}",
                    },
                    json=body,
                )
                data = r.json() if r.content else {}
                if r.status_code >= 400:
                    return ToolResult(
                        ok=False,
                        error=f"MiniMax HTTP {r.status_code}: {(data or r.text)[:500]}",
                    )
                br = data.get("base_resp") or {}
                scode = br.get("status_code")
                if scode not in (0, "0", None):
                    return ToolResult(
                        ok=False,
                        error=f"MiniMax error {scode}: {br.get('status_msg', data)}",
                    )
                audio_hex = (data.get("data") or {}).get("audio")
                if not audio_hex or not isinstance(audio_hex, str):
                    return ToolResult(ok=False, error=f"no hex audio in response keys={list(data.keys())}")
                try:
                    raw = binascii.unhexlify(audio_hex.strip())
                except binascii.Error as e:
                    return ToolResult(ok=False, error=f"invalid audio hex: {e}")

                rel = _write_sandbox_file(
                    ctx.workdir,
                    f"artifacts/minimax_tts_{_safe_slug(text[:32])}_{uuid.uuid4().hex[:8]}.mp3",
                    raw,
                )
                ms = int((time.perf_counter() - started) * 1000)
                return ToolResult(
                    ok=True,
                    preview=f"audio → {rel}",
                    output={"path": rel, "model": model, "voice_id": voice_id},
                    duration_ms=ms,
                )
        except httpx.HTTPError as e:
            return ToolResult(ok=False, error=f"http error: {e}")
        except Exception as e:  # noqa: BLE001
            log.exception("minimax_tts.failed")
            return ToolResult(ok=False, error=f"{type(e).__name__}: {e}")
