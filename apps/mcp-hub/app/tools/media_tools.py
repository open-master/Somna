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

        body: dict[str, Any] = {
            "model": model,
            "input": {"prompt": prompt},
            "parameters": params,
        }
        neg = (args.get("negative_prompt") or "").strip()
        if neg:
            body["input"]["negative_prompt"] = neg

        base = settings.dashscope_http_base
        timeout = httpx.Timeout(settings.wan_request_timeout_sec)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                created = await _dashscope_post(
                    client,
                    base,
                    key,
                    "/services/aigc/text2image/image-synthesis",
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
                results = fout.get("results") or []
                urls: list[str] = []
                for item in results:
                    if isinstance(item, dict) and item.get("url"):
                        urls.append(item["url"])

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


@register
class WanText2VideoTool(BaseTool):
    name = "wan_text2video"
    description = (
        "Generate a short video from a text prompt using Alibaba DashScope 万相 (text-to-video). "
        "Async task + poll; downloads MP4 into the sandbox. Requires DASHSCOPE_API_KEY."
    )
    category = "media"
    mutates = True
    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string", "description": "Video prompt (Chinese or English)."},
            "negative_prompt": {"type": "string", "description": "Optional negative prompt."},
            "model": {
                "type": "string",
                "description": "Override model id (default from MCP_WAN_T2V_MODEL).",
            },
            "size": {"type": "string", "description": "e.g. 832*480 (see model docs)."},
            "prompt_extend": {
                "type": "boolean",
                "description": "Let the model extend/rewrite the prompt (adds latency).",
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

        model = (args.get("model") or settings.wan_t2v_model).strip()
        parameters: dict[str, Any] = {}
        size = (args.get("size") or "").strip()
        if size:
            parameters["size"] = size
        if args.get("prompt_extend") is not None:
            parameters["prompt_extend"] = bool(args["prompt_extend"])

        inp: dict[str, Any] = {"prompt": prompt}
        neg = (args.get("negative_prompt") or "").strip()
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
                    f"artifacts/wan_t2v_{_safe_slug(prompt)}_{uuid.uuid4().hex[:8]}.mp4",
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
            log.exception("wan_text2video.failed")
            return ToolResult(ok=False, error=f"{type(e).__name__}: {e}")


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
